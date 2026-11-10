"""Unit tests for the auth module."""

from __future__ import annotations

import hashlib
from unittest.mock import Mock

import pytest

from wog_dump.core.api import WOGApiClient, form_encode, parse_response_fields
from wog_dump.core.auth import AuthError, AuthManager, Identity, build_sys_info
from wog_dump.core.config import WOGConfig
from wog_dump.core.database import SETTINGS_API_BASE_URL, WOGDatabase

# Dummy credentials only - never real account data
TEST_EMAIL = "player@example.com"
TEST_PASSWORD = "hunter2 test password"
TEST_NAME = "Player"


def success_login_response(auth_id: str = "123456", session: str = "1") -> str:
    """A query=1 answer shaped like the real query2026 API (acc_id)."""
    return (
        f"00013076&query=1&time=1700000014&offline_ts=&"
        f"_1=16:55:179 Email+login..+Client%3A+&"
        f"_2=16:55:181 ..account+found+%21&"
        f"&acc_id={auth_id}&acc_name=Player&acc_email=player%40example.com"
        f"&acc_can_login_email=1&session={session}&result=0&ccode=KZ&sign=abc123"
    )


def success_register_response() -> str:
    """A query=39 (registration) answer: result=0, no session."""
    return (
        "00000682&query=39&time=1700000090&offline_ts=&"
        "_3=16:32:594 Email+to+player%40example.com+sent+ok+%21&"
        "_4=16:32:594 action+saved+on+server+0.+id%3D123456&"
        "result=0&sign=def456"
    )


def shard_redirect_response(server: str = "us4.ultimate-disassembly.com") -> str:
    """A query=1 result=777 answer, shaped like the live capture (Nov 2026).

    The server cannot host the account itself and names the home shard
    under server=, together with an echo of the login fields.
    """
    return (
        "00001277&query=1&time=1700000030&offline_ts=&"
        "_1=22:20:521 Email+login..+Client%3A+&"
        "_2=22:20:522 ..acoount+not+found.+Looking+for+shard..&"
        "_3=22:20:522 calling+v_shards_query%28web%2C5ea23dd5b330e0304a3624204e7ca626%29&"
        f"_13=22:20:541 redirect+to+{form_encode(server)}&"
        f"result=777&server={form_encode(server)}&client=&user=&auth=&username=&"
        "email=player%40example.com&pass=2ab5e6a3393c1e6b9f3626b1d47ba2d2&"
        "_14=22:20:541 ..shard+found+%21&sign=x"
    )


def query_fields(params: str) -> dict[str, str]:
    """Split a captured query string into fields (first value wins)."""
    fields: dict[str, str] = {}
    for segment in params.split("&"):
        if "=" in segment:
            key, _, value = segment.partition("=")
            fields.setdefault(key, value)
    return fields


class TestResponseParsing:
    def test_parse_fields_skips_debug(self) -> None:
        fields = parse_response_fields(success_login_response())
        assert fields["result"] == "0"
        assert fields["acc_id"] == "123456"
        assert fields["session"] == "1"
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


class TestSysInfo:
    def test_client_shape(self) -> None:
        info = build_sys_info()
        lines = info.splitlines()
        assert len(lines) == 5
        assert lines[0].startswith("OS:")
        assert lines[1].startswith("CPU:")
        assert lines[2].startswith("Memory:")
        assert lines[3].startswith("GPU:")
        assert lines[4] == "Rating:0"


class TestLogin:
    def test_login_stores_identity_and_credentials(
        self, test_config: WOGConfig
    ) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = success_login_response()

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        identity = auth.login(TEST_EMAIL, TEST_PASSWORD)

        assert identity == Identity(
            auth_id="123456", session="1", device_id=identity.device_id
        )
        assert auth.db.get_identity() == ("123456", "1")
        assert auth.get_account_email() == TEST_EMAIL

    def test_login_request_matches_standalone_protocol(
        self, test_config: WOGConfig
    ) -> None:
        """The query replicates the captured standalone client exactly.

        query=1&email=..&pass=<md5>&lang=..&sys_info=..&ver=..&uver=..
        &dev=..&session=0&id=0&time=..&platform=&hash=..
        """
        client = Mock(spec=WOGApiClient)
        client.call.return_value = success_login_response()

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        identity = auth.login(TEST_EMAIL, TEST_PASSWORD)

        sent = client.call.call_args[0][0]
        fields = query_fields(sent)

        # Password never travels in plain text: pass= is its MD5 digest
        assert "password=" not in sent
        assert fields["query"] == "1"
        assert fields["pass"] == hashlib.md5(TEST_PASSWORD.encode()).hexdigest()
        assert TEST_PASSWORD not in sent

        # Account fields are form-encoded (&/= inside values must never
        # rewrite the query's own fields)
        assert fields["email"] == "player%40example.com"

        # Pre-session markers and the standalone platform marker
        assert fields["session"] == "0"
        assert fields["id"] == "0"
        assert fields["platform"] == ""
        assert fields["lang"] == test_config.api_lang
        assert fields["sys_info"].startswith("OS%3a")

        # Client identity fields
        assert fields["ver"] == test_config.game_version
        assert fields["uver"] == test_config.unity_version
        assert fields["dev"] == identity.device_id
        assert fields["time"].isdigit()

        # Every client query carries a 32-hex hash signature
        assert len(fields["hash"]) == 32
        int(fields["hash"], 16)

        # Field order matches the captured client
        order = [segment.partition("=")[0] for segment in sent.split("&")]
        assert order.index("pass") < order.index("lang") < order.index("sys_info")
        assert order.index("time") < order.index("platform") < order.index("hash")

    def test_login_failure_raises(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = (
            "00001109&query=1&result=100&_3=52:00:000 user+not+found&sign=x"
        )

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="result=100"):
            auth.login(TEST_EMAIL, "wrong")

    def test_login_response_without_session_raises(
        self, test_config: WOGConfig
    ) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = "00001109&query=1&result=0&sign=x"

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="acc_id/session"):
            auth.login(TEST_EMAIL, TEST_PASSWORD)

    def test_login_error_message_from_debug_output(self) -> None:
        response = (
            "00001109&query=1&time=1&offline_ts=&_1=56:16:739 Email+login.."
            "&_14=56:17:089 user+not+found&result=100&sign=x"
        )
        assert AuthManager._login_error(response) == "user not found"

    def test_login_error_message_is_url_decoded(self) -> None:
        """Percent hex and + reach the user as real characters (#2)."""
        response = (
            "00001277&query=1&time=1&offline_ts=&"
            "_14=22:20:541 ..shard+found+%21&result=777&sign=x"
        )
        assert AuthManager._login_error(response) == "..shard found !"

    def test_login_error_prefers_decoded_errmsg_field(self) -> None:
        assert AuthManager._login_error("result=100", "wrong+password%21") == (
            "wrong password!"
        )


class TestShardRedirect:
    """result=777 handling: the account lives on another shard (#2)."""

    def test_login_follows_redirect_to_home_shard(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.side_effect = [
            shard_redirect_response("us4.ultimate-disassembly.com"),
            success_login_response(),
        ]

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        identity = auth.login(TEST_EMAIL, TEST_PASSWORD)

        assert (identity.auth_id, identity.session) == ("123456", "1")
        assert client.call.call_count == 2

        # The client and the store both move to the home shard, so the
        # key queries of this run and later logins go there directly
        target = "https://us4.ultimate-disassembly.com/v/query2026.php"
        assert auth.config.api_base_url == target
        assert auth.db.get_setting(SETTINGS_API_BASE_URL) == target

    def test_redirect_to_the_same_shard_refused(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = shard_redirect_response(
            "eu1.ultimate-disassembly.com"  # the default endpoint itself
        )

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="same server"):
            auth.login(TEST_EMAIL, TEST_PASSWORD)

        assert client.call.call_count == 1

    def test_second_redirect_refused(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.side_effect = [
            shard_redirect_response("us4.ultimate-disassembly.com"),
            shard_redirect_response("us3.ultimate-disassembly.com"),
        ]

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="more than one shard redirect"):
            auth.login(TEST_EMAIL, TEST_PASSWORD)

        assert client.call.call_count == 2
        # No shard remembered from a failed redirect chain
        assert auth.db.get_setting(SETTINGS_API_BASE_URL) is None

    @pytest.mark.parametrize(
        "server",
        [
            "",  # server= missing entirely
            "evil.example.com",  # outside the game's domain
            "eu1.ultimate-disassembly.com.evil.com",  # suffix look-alike
            "eu1.ultimate-disassembly.com:8080",  # port injection
            "https://eu1.ultimate-disassembly.com",  # scheme injection
        ],
    )
    def test_redirect_to_untrusted_host_refused(
        self, test_config: WOGConfig, server: str
    ) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = shard_redirect_response(server)

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="no valid home"):
            auth.login(TEST_EMAIL, TEST_PASSWORD)

        assert client.call.call_count == 1
        assert auth.config.api_base_url == test_config.api_base_url

    def test_remembered_shard_used_by_later_managers(
        self, test_config: WOGConfig
    ) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.side_effect = [
            shard_redirect_response("us4.ultimate-disassembly.com"),
            success_login_response(),
        ]
        db = WOGDatabase(test_config)
        AuthManager(test_config, db=db, client=client).login(TEST_EMAIL, TEST_PASSWORD)

        # A fresh process: default eu1 config, same database
        fresh_config = WOGConfig(
            base_dir=test_config.base_dir, db_path=test_config.db_path
        )
        later_auth = AuthManager(fresh_config, db=db)

        assert fresh_config.api_base_url == (
            "https://us4.ultimate-disassembly.com/v/query2026.php"
        )

    def test_remembered_shard_beats_env_endpoint(
        self, test_config: WOGConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The server-confirmed shard is a fact about the account; env is
        only the probe endpoint for the first login (#2)."""
        db = WOGDatabase(test_config)
        db.set_setting(
            SETTINGS_API_BASE_URL,
            "https://us4.ultimate-disassembly.com/v/query2026.php",
        )
        monkeypatch.setenv(
            "WOG_API_BASE_URL", "https://us3.ultimate-disassembly.com/v/query2026.php"
        )
        test_config.api_base_url = (
            "https://us3.ultimate-disassembly.com/v/query2026.php"
        )

        AuthManager(test_config, db=db)

        assert test_config.api_base_url == (
            "https://us4.ultimate-disassembly.com/v/query2026.php"
        )

    def test_env_conflicting_with_remembered_shard_warns(
        self,
        test_config: WOGConfig,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        db = WOGDatabase(test_config)
        db.set_setting(
            SETTINGS_API_BASE_URL,
            "https://us4.ultimate-disassembly.com/v/query2026.php",
        )
        monkeypatch.setenv(
            "WOG_API_BASE_URL", "https://us3.ultimate-disassembly.com/v/query2026.php"
        )
        test_config.api_base_url = (
            "https://us3.ultimate-disassembly.com/v/query2026.php"
        )

        with caplog.at_level("WARNING", logger="wog_dump"):
            AuthManager(test_config, db=db)

        assert "lives on shard us4" in caplog.text
        assert "'wog-dump logout' resets it" in caplog.text

    def test_env_endpoint_used_when_no_shard_remembered(
        self, test_config: WOGConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First login ever (or after logout): the env probe endpoint
        stays in charge until the server confirms a home shard."""
        monkeypatch.setenv(
            "WOG_API_BASE_URL", "https://us3.ultimate-disassembly.com/v/query2026.php"
        )
        test_config.api_base_url = (
            "https://us3.ultimate-disassembly.com/v/query2026.php"
        )

        AuthManager(test_config, db=WOGDatabase(test_config))

        assert test_config.api_base_url == (
            "https://us3.ultimate-disassembly.com/v/query2026.php"
        )

    def test_logout_clears_remembered_shard(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.set_setting(
            SETTINGS_API_BASE_URL,
            "https://us4.ultimate-disassembly.com/v/query2026.php",
        )

        AuthManager(test_config, db=db).logout()

        assert db.get_setting(SETTINGS_API_BASE_URL) is None

    def test_login_accepts_legacy_id_field(self, test_config: WOGConfig) -> None:
        """Older servers may answer with id= instead of acc_id=."""
        client = Mock(spec=WOGApiClient)
        client.call.return_value = "00001109&query=1&id=42&session=7&result=0&sign=abc"

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        identity = auth.login(TEST_EMAIL, TEST_PASSWORD)

        assert (identity.auth_id, identity.session) == ("42", "7")


class TestRegister:
    def test_register_request_matches_standalone_protocol(
        self, test_config: WOGConfig
    ) -> None:
        """query=39&reg_name=..&reg_email=..&reg_pass=..&reg_subs=0&..."""
        client = Mock(spec=WOGApiClient)
        client.call.return_value = success_register_response()

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        auth.register(TEST_NAME, TEST_EMAIL, TEST_PASSWORD)

        sent = client.call.call_args[0][0]
        fields = query_fields(sent)

        # Registration carries the password in plain text (captured
        # behavior - the MD5 digest is login-only); spaces travel as +
        assert fields["query"] == "39"
        assert fields["reg_name"] == TEST_NAME
        assert fields["reg_email"] == "player%40example.com"
        assert fields["reg_pass"] == form_encode(TEST_PASSWORD)
        assert fields["reg_subs"] == "0"
        assert fields["session"] == "0"
        assert fields["id"] == "0"
        assert fields["ver"] == test_config.game_version
        assert fields["dev"] == auth.get_device_id()
        assert len(fields["hash"]) == 32

    def test_register_stores_no_identity(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = success_register_response()

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        auth.register(TEST_NAME, TEST_EMAIL, TEST_PASSWORD)

        assert auth.db.get_identity() is None

    def test_register_failure_raises(self, test_config: WOGConfig) -> None:
        client = Mock(spec=WOGApiClient)
        client.call.return_value = (
            "00000682&query=39&result=100&_2=16:32:591 email+already+exists&sign=x"
        )

        auth = AuthManager(test_config, db=WOGDatabase(test_config), client=client)
        with pytest.raises(AuthError, match="already exists"):
            auth.register(TEST_NAME, TEST_EMAIL, TEST_PASSWORD)


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
        db.set_setting("account_email", TEST_EMAIL)
        db.set_setting("account_password", TEST_PASSWORD)

        client = Mock(spec=WOGApiClient)
        client.call.return_value = success_login_response("999", "555")

        auth = AuthManager(test_config, db=db, client=client)
        identity = auth.ensure_session(force_relogin=True)

        assert (identity.auth_id, identity.session) == ("999", "555")
        assert db.get_identity() == ("999", "555")


class TestLogout:
    def test_logout_removes_credentials(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.set_setting("account_email", TEST_EMAIL)
        db.set_setting("account_password", TEST_PASSWORD)
        db.save_identity("1", "2")

        AuthManager(test_config, db=db).logout()

        assert db.get_setting("account_email") is None
        assert db.get_setting("account_password") is None
        assert db.get_identity() is None
