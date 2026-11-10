"""Unit tests for KeyManager fetch flows (coverage follow-up)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.decrypt import AuthenticationError, NetworkError
from wog_dump.core.keys import KeyManager


@pytest.fixture
def manager(test_config: WOGConfig) -> KeyManager:
    manager = KeyManager(test_config)
    manager.auth = Mock()
    from wog_dump.core.auth import Identity

    manager.auth.ensure_session.return_value = Identity(
        auth_id="42", session="sess", device_id="dev"
    )
    manager.client = Mock()
    return manager


def ok(text: str) -> str:
    return text


class TestParseApiResponse:
    def test_ok_with_key(self, manager: KeyManager) -> None:
        assert manager._parse_api_response("result=0&sync=abc", "ak47") == "abc"

    def test_ok_without_key_warns(self, manager: KeyManager) -> None:
        assert manager._parse_api_response("result=0", "ak47") is None

    def test_auth_codes_raise(self, manager: KeyManager) -> None:
        for code in ("100", "1000"):
            with pytest.raises(AuthenticationError):
                manager._parse_api_response(f"result={code}", "ak47")

    def test_server_error_codes_raise_network(self, manager: KeyManager) -> None:
        for code in ("429", "500", "502", "503", "504"):
            with pytest.raises(NetworkError):
                manager._parse_api_response(f"result={code}", "ak47")

    def test_unknown_code_warns_and_returns_none(self, manager: KeyManager) -> None:
        assert manager._parse_api_response("result=999", "ak47") is None

    def test_errmsg_used_when_unknown(self, manager: KeyManager) -> None:
        assert manager._parse_api_response("result=555&errmsg=oops", "ak47") is None


class TestGetKeyForAsset:
    def test_cache_hit_skips_network(self, manager: KeyManager) -> None:
        manager._key_cache["ak47"] = "cached"
        assert manager.get_key_for_asset("ak47") == "cached"
        manager.client.call.assert_not_called()

    def test_success_roundtrip(self, manager: KeyManager) -> None:
        manager.client.call.side_effect = ["result=0&sync=k1"]
        assert manager.get_key_for_asset("ak47") == "k1"
        # second call hits the cache, the network is not touched again
        assert manager.get_key_for_asset("ak47") == "k1"
        assert manager.client.call.call_count == 1

    def test_network_error_wraps(self, manager: KeyManager) -> None:
        from wog_dump.core.api import ApiNetworkError

        manager.client.call.side_effect = ApiNetworkError(0, "down")
        with pytest.raises(NetworkError, match="ak47"):
            manager.get_key_for_asset("ak47", use_cache=False)

    def test_auth_retry_once_then_success(self, manager: KeyManager) -> None:
        manager.client.call.side_effect = [
            "result=100",
            "result=0&sync=k2",
        ]  # list: one element per call
        assert manager.get_key_for_asset("ak47", use_cache=False) == "k2"
        assert (
            manager.auth.ensure_session.call_args_list[-1].kwargs.get("force_relogin")
            or manager.auth.ensure_session.call_count == 2
        )

    def test_auth_retry_exhausted_raises(self, manager: KeyManager) -> None:
        manager.client.call.side_effect = lambda params: "result=100"
        with pytest.raises(AuthenticationError):
            manager.get_key_for_asset("ak47", use_cache=False)
        assert manager.auth.ensure_session.call_count == 2

    def test_unexpected_error_wraps(self, manager: KeyManager) -> None:
        manager.client.call.side_effect = RuntimeError("boom")
        from wog_dump.core.decrypt import DecryptionError

        with pytest.raises(DecryptionError, match="boom"):
            manager.get_key_for_asset("ak47", use_cache=False)

    def test_auth_ensure_failure(self, manager: KeyManager) -> None:
        from wog_dump.core.auth import AuthError

        manager.auth.ensure_session.side_effect = AuthError("no account")
        with pytest.raises(AuthenticationError, match="no account"):
            manager.get_key_for_asset("ak47", use_cache=False)


class TestRequestBuilding:
    def test_query3_shape_and_signature(self, manager: KeyManager) -> None:
        from wog_dump.core.auth import Identity

        params = manager._build_api_request_data(
            "ak47", Identity(auth_id="42", session="sess", device_id="dev")
        )
        assert "query=3" in params
        assert "model=ak47" in params
        assert "id=42" in params
        assert "hash=" in params


class TestSaveLoadKeys:
    def test_save_and_load_roundtrip(self, manager: KeyManager, tmp_path: Any) -> None:
        manager.save_keys({"ak47": "k1"})
        keys = manager.load_keys()
        assert keys.get("ak47") == "k1"

    def test_clear_cache(self, manager: KeyManager) -> None:
        manager._key_cache["ak47"] = "k"
        manager.clear_cache()
        assert "ak47" not in manager._key_cache
