"""Unit tests for the auth module."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from wog_dump.core.api import WOGApiClient, parse_response_fields
from wog_dump.core.auth import AuthError, AuthManager, Identity
from wog_dump.core.config import WOGConfig
from wog_dump.core.database import WOGDatabase


def success_login_response(auth_id: str = "123456", session: str = "789") -> str:
    return (
        f"00001109&query=1&time=1793472977&id={auth_id}"
        f"&session={session}&_1=52:00:000 Email+login..&result=0&sign=abc123"
    )


class TestResponseParsing:
    def test_parse_fields_skips_debug(self) -> None:
        fields = parse_response_fields(success_login_response())
        assert fields["result"] == "0"
        assert fields["id"] == "123456"
        assert fields["session"] == "789"
        assert not any(key.startswith("_") for key in fields)


class TestDeviceId:
    def test_generated_once_and_stable(self, test_config: WOGConfig) -> None:
        auth = AuthManager(test_config)

        first = auth.get_device_id()
        assert len(first) == 40
        int(first, 16)  # hex

        # A new manager over the same database keeps the same device id
        assert AuthManager(test_config).get_device_id() == first

    def test_random_per_installation(self) -> None:
        assert AuthManager.generate_device_id() != AuthManager.generate_device_id()


class TestLogin:
    def test_login_stores_identity_and_credentials(
        self, test_config: WOGConfig
    ) -> None:
        client = Mock(spec=WOGApiClient)
        client.build_common_params.return_value = "ver=1&time=2"
        client.call.return_value = success_login_response()

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        identity = auth.login("user@example.com", "secret")

        assert identity == Identity(
            auth_id="123456", session="789", device_id=identity.device_id
        )
        assert auth.db.get_identity() == ("123456", "789")
        assert auth.get_account_email() == "user@example.com"

        # The request carried the generated device id and the credentials
        # (URL-encoded: &/= inside a value must never rewrite the query)
        sent_params = client.call.call_args[0][0]
        assert "email=user%40example.com" in sent_params
        assert "password=secret" in sent_params
        assert f"dev={identity.device_id}" in sent_params

    def test_login_failure_raises(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.build_common_params.return_value = "ver=1&time=2"
        client.call.return_value = (
            "00001109&query=1&result=100&_3=52:00:000 user+not+found&sign=x"
        )

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="result=100"):
            auth.login("user@example.com", "wrong")

    def test_login_response_without_session_raises(
        self, test_config: WOGConfig
    ) -> None:
        client = Mock(spec=WOGApiClient)
        client.build_common_params.return_value = "ver=1&time=2"
        client.call.return_value = "00001109&query=1&result=0&sign=x"

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="id/session"):
            auth.login("user@example.com", "secret")

    def test_login_error_message_from_debug_output(self) -> None:
        response = (
            "00001109&query=1&time=1&offline_ts=&_1=56:16:739 Email+login.."
            "&_14=56:17:089 user+not+found&result=100&sign=x"
        )
        assert AuthManager._login_error(response) == "user not found"


class TestEnsureSession:
    def test_without_account_raises_actionable_error(
        self, test_config: WOGConfig
    ) -> None:
        auth = AuthManager(test_config, db=WOGDatabase(test_config))
        with pytest.raises(AuthError, match="wog-dump login"):
            auth.ensure_session()

    def test_uses_stored_session(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.set_setting("device_id", "d" * 40)
        db.save_identity("42", "77")
        client = Mock(spec=WOGApiClient)

        auth = AuthManager(test_config, db=db, client=client)
        identity = auth.ensure_session()

        assert (identity.auth_id, identity.session) == ("42", "77")
        client.call.assert_not_called()

    def test_identity_from_env(
        self, test_config: WOGConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WOG_AUTH_ID", "111")
        monkeypatch.setenv("WOG_AUTH_SESSION", "222")
        monkeypatch.setenv("WOG_DEVICE_ID", "e" * 40)

        client = Mock(spec=WOGApiClient)
        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)

        identity = auth.ensure_session()

        assert (identity.auth_id, identity.session, identity.device_id) == (
            "111",
            "222",
            "e" * 40,
        )
        client.call.assert_not_called()
        # Imported into the database for later runs
        assert auth.db.get_identity() == ("111", "222")

    def test_force_relogin_logs_in_again(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.set_setting("device_id", "d" * 40)
        db.save_identity("old", "old")
        db.set_setting("account_email", "user@example.com")
        db.set_setting("account_password", "secret")

        client = Mock(spec=WOGApiClient)
        client.build_common_params.return_value = "ver=1&time=2"
        client.call.return_value = success_login_response("999", "555")

        auth = AuthManager(test_config, db=db, client=client)
        identity = auth.ensure_session(force_relogin=True)

        assert (identity.auth_id, identity.session) == ("999", "555")
        assert db.get_identity() == ("999", "555")


class TestLogout:
    def test_logout_removes_credentials(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.set_setting("account_email", "user@example.com")
        db.set_setting("account_password", "secret")
        db.save_identity("1", "2")

        AuthManager(test_config, db=db).logout()

        assert db.get_setting("account_email") is None
        assert db.get_setting("account_password") is None
        assert db.get_identity() is None
