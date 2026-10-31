"""Unit tests for the BZ2-over-HTTP API client."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from wog_dump.core.api import (
    ApiNetworkError,
    WOGApiClient,
    parse_response_fields,
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
        _, kwargs = mock_put.call_args
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
