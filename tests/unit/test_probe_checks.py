"""Unit tests for the protocol probe checks (task 13 coverage follow-up)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.probe import (
    KNOWN_GAME_VERSION,
    ProbeResult,
    ProtocolProbe,
    probe_verdict,
)


@pytest.fixture
def probe(test_config: WOGConfig) -> ProtocolProbe:
    return ProtocolProbe(test_config)


class TestHostReachability:
    def test_connected(self, probe: ProtocolProbe) -> None:
        with patch("wog_dump.core.probe.socket.create_connection") as conn:
            result = probe.check_api_reachable()
        conn.assert_called_once()
        assert result.verdict == "ok"
        assert "connected" in result.detail

    def test_unreachable(self, probe: ProtocolProbe) -> None:
        import socket

        with patch(
            "wog_dump.core.probe.socket.create_connection",
            side_effect=socket.gaierror("no dns"),
        ):
            result = probe.check_api_reachable()
        assert result.verdict == "fail"
        assert "unreachable" in result.detail

    def test_empty_host(self) -> None:
        result = ProtocolProbe._host_reachable("API endpoint", "")
        assert result.verdict == "fail"
        assert "no host" in result.detail


class TestAnonymousQuery:
    def test_http_answer_is_ok(self, probe: ProtocolProbe) -> None:
        with patch("wog_dump.core.probe.requests.get") as get:
            get.return_value = Mock(status_code=403)
            result = probe.check_anonymous_query()
        assert result.verdict == "ok"
        assert "403" in result.detail

    def test_network_error_fails(self, probe: ProtocolProbe) -> None:
        import requests as requests_module

        with patch(
            "wog_dump.core.probe.requests.get",
            side_effect=requests_module.ConnectTimeout("timeout"),
        ):
            result = probe.check_anonymous_query()
        assert result.verdict == "fail"


class TestAuthenticatedKeys:
    def test_skip_without_session(self, test_config: WOGConfig) -> None:
        from wog_dump.core.database import WOGDatabase

        # no identity in the database
        assert WOGDatabase(test_config).get_identity() is None
        result = ProtocolProbe(test_config).check_authenticated_keys()
        assert result.verdict == "skip"
        assert "wog-dump login" in result.detail

    def test_ok_with_key(self, test_config: WOGConfig) -> None:
        from wog_dump.core.database import WOGDatabase

        WOGDatabase(test_config).save_identity("42", "sess")
        with patch("wog_dump.core.keys.KeyManager.get_key_for_asset") as fetch:
            fetch.return_value = "key123"
            result = ProtocolProbe(test_config).check_authenticated_keys()
        assert result.verdict == "ok"

    def test_fail_without_key(self, test_config: WOGConfig) -> None:
        from wog_dump.core.database import WOGDatabase

        WOGDatabase(test_config).save_identity("42", "sess")
        with patch("wog_dump.core.keys.KeyManager.get_key_for_asset") as fetch:
            fetch.return_value = None
            result = ProtocolProbe(test_config).check_authenticated_keys()
        assert result.verdict == "fail"
        assert "mitm-capture" in result.detail


class TestWeaponListCheck:
    def test_ok_uses_subdirectory(self, probe: ProtocolProbe) -> None:
        with patch("wog_dump.core.probe.requests.head") as head:
            head.return_value = Mock(status_code=200, headers={"Content-Length": "100"})
            result = probe.check_weapon_list()
        assert result.verdict == "ok"
        called_url = head.call_args[0][0]
        assert "/spider/spider_gen.unity3d" in called_url

    def test_http_error(self, probe: ProtocolProbe) -> None:
        with patch("wog_dump.core.probe.requests.head") as head:
            head.return_value = Mock(status_code=404, headers={})
            result = probe.check_weapon_list()
        assert result.verdict == "fail"

    def test_network_error(self, probe: ProtocolProbe) -> None:
        import requests as requests_module

        with patch(
            "wog_dump.core.probe.requests.head",
            side_effect=requests_module.ConnectionError("down"),
        ):
            result = probe.check_weapon_list()
        assert result.verdict == "fail"


class TestClientVersion:
    def test_known_version_ok(self, probe: ProtocolProbe) -> None:
        assert probe.check_client_version().verdict == "ok"
        assert KNOWN_GAME_VERSION == "2.2.3t6"

    def test_unknown_version_warns(self, test_config: WOGConfig) -> None:
        config = WOGConfig(game_version="9.9.9t9")
        result = ProtocolProbe(config).check_client_version()
        assert result.verdict == "warn"
        assert "known-good" in result.detail


class TestCascade:
    def test_stops_on_unreachable(self, probe: ProtocolProbe) -> None:
        with (
            patch.object(
                probe,
                "check_api_reachable",
                return_value=ProbeResult("API", "fail", "x"),
            ),
            patch.object(
                probe,
                "check_data_reachable",
                return_value=ProbeResult("Data", "ok", ""),
            ),
        ):
            results = probe.run()
        assert results[-1].name == "Remaining checks"
        assert results[-1].verdict == "skip"
        assert len(results) == 3

    def test_full_cascade_runs_everything(self, probe: ProtocolProbe) -> None:
        ok = ProbeResult("x", "ok")
        with (
            patch.object(probe, "check_api_reachable", return_value=ok),
            patch.object(probe, "check_data_reachable", return_value=ok),
            patch.object(probe, "check_anonymous_query", return_value=ok),
            patch.object(probe, "check_authenticated_keys", return_value=ok),
            patch.object(probe, "check_weapon_list", return_value=ok),
            patch.object(probe, "check_client_version", return_value=ok),
        ):
            results = probe.run()
        assert len(results) == 6
        assert probe_verdict(results) == "ok"
