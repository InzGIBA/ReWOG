"""Unit tests for the BZ2-over-HTTP API client."""

from __future__ import annotations

import hashlib
from unittest.mock import Mock, patch

import pytest
import requests

from wog_dump.core.api import (
    ApiNetworkError,
    WOGApiClient,
    form_encode,
    parse_response_fields,
    sign_query,
)
from wog_dump.core.config import WOGConfig


class TestParseResponseFields:
    def test_basic_fields(self) -> None:
        fields = parse_response_fields("result=0&sync=abc123&model=ak74")

        assert fields == {"result": "0", "sync": "abc123", "model": "ak74"}

    def test_debug_entries_skipped(self) -> None:
        fields = parse_response_fields("result=0&_1=debug noise&sync=key")

        assert fields == {"result": "0", "sync": "key"}

    def test_segments_without_value_skipped(self) -> None:
        fields = parse_response_fields("result=0&garbage&sync=key")

        assert "garbage" not in fields
        assert fields["sync"] == "key"

    def test_empty_values_kept(self) -> None:
        fields = parse_response_fields("result=&sync=key")

        assert fields["result"] == ""


class TestFormEncode:
    def test_mimics_unity_escape_url(self) -> None:
        # Captured client bodies escape ':' as %3a (lowercase) and
        # spaces as '+'
        assert form_encode("OS:Windows 11") == "OS%3aWindows+11"

    def test_escapes_query_structure(self) -> None:
        # & and = inside a value must never rewrite the query's fields
        assert form_encode("a&b=c") == "a%26b%3dc"

    def test_passthrough_safe_characters(self) -> None:
        # -._ and alphanumerics stay literal (like Unity's EscapeURL),
        # everything else - including @ and * - is escaped
        assert form_encode("abc123.-_") == "abc123.-_"
        assert form_encode("a@b*c") == "a%40b%2ac"


class TestSignQuery:
    def test_appends_well_formed_hash(self) -> None:
        signed = sign_query("query=1&email=x")
        assert signed.startswith("query=1&email=x&hash=")
        digest = signed.rsplit("=", 1)[1]
        assert digest == hashlib.md5(b"query=1&email=x").hexdigest()

    def test_hash_is_plain_hex_digest(self) -> None:
        # The server does not verify the signature; it just has to be a
        # 32-hex value so the request shape matches the game client
        assert len(sign_query("q=1").rsplit("=", 1)[1]) == 32


class TestWOGApiClient:
    def test_compress_roundtrip(self, test_config: WOGConfig) -> None:
        client = WOGApiClient(test_config)

        payload = client.compress_request("query=3&model=ak74")
        assert isinstance(payload, bytes)

        decompressed = client.decompress_response(payload)
        assert decompressed == "query=3&model=ak74"

    def test_decompress_short_content_rejected(self, test_config: WOGConfig) -> None:
        client = WOGApiClient(test_config)

        with pytest.raises(ApiNetworkError, match="Invalid response length"):
            client.decompress_response(b"\x01\x02")

    def test_decompress_garbage_rejected(self, test_config: WOGConfig) -> None:
        client = WOGApiClient(test_config)

        with pytest.raises(ApiNetworkError, match="decompress"):
            client.decompress_response(b"\x00\x00\x00\x10" + b"not-bzip2-data")

    def test_build_common_params(self, test_config: WOGConfig) -> None:
        client = WOGApiClient(test_config)
        params = client.build_common_params()

        assert f"ver={test_config.game_version}" in params
        assert f"uver={test_config.unity_version}" in params
        assert "time=" in params

    @patch("wog_dump.core.api.requests.Session.put")
    def test_call_success(self, mock_put: Mock, test_config: WOGConfig) -> None:
        client = WOGApiClient(test_config)

        response_body = client.compress_request("result=0&sync=abc")
        mock_response = Mock()
        mock_response.content = response_body
        mock_response.raise_for_status.return_value = None
        mock_put.return_value = mock_response

        result = client.call("query=3")

        assert result == "result=0&sync=abc"
        args, kwargs = mock_put.call_args
        # The standalone client PUTs to the bare script URL - no
        # ?soc=steam marker (it breaks email/password queries)
        assert args[0] == test_config.api_base_url
        assert kwargs["data"] == client.compress_request("query=3")

    @patch("wog_dump.core.api.requests.Session.put")
    def test_call_network_error(self, mock_put: Mock, test_config: WOGConfig) -> None:
        mock_put.side_effect = requests.RequestException("timeout")
        client = WOGApiClient(test_config)

        with pytest.raises(ApiNetworkError, match="Network error"):
            client.call("query=3")

    def test_rate_limiter_applied(self, test_config: WOGConfig) -> None:
        """Every API call goes through the rate limiter."""
        client = WOGApiClient(test_config)
        limiter = Mock()
        client._rate_limiter = limiter

        with patch("wog_dump.core.api.requests.Session.put") as mock_put:
            mock_response = Mock()
            mock_response.content = client.compress_request("result=0")
            mock_response.raise_for_status.return_value = None
            mock_put.return_value = mock_response

            client.call("query=3")

        limiter.wait.assert_called_once()
