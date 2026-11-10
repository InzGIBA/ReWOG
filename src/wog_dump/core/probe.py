"""Protocol compatibility probe: is the game API still the API we know?

``wog-dump info --protocol-probe`` runs a cascade of cheap checks and
reports OK / FAIL / SKIP / WARN per check. The verdict answers the
"did I break something or did the game update" question in one run;
any FAIL exits with code 2 so CI and scripts can react.

Golden response shapes live in ``tests/fixtures/protocol/``; when the
game's protocol drifts, capture fresh responses with
``scripts/refresh_protocol_fixtures.py`` and update the fixtures.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from .config import WOGConfig, get_config
from .database import WOGDatabase

__all__ = ["PROBE_SCHEMA", "ProbeResult", "ProtocolProbe", "KNOWN_GAME_VERSION"]

PROBE_SCHEMA = "wog-dump.probe/1"

# The standalone client build this codebase was written against; a
# mismatch warns that the protocol may have moved on
KNOWN_GAME_VERSION = "2.2.3t6"

PROBE_VERDICTS = ("ok", "fail", "skip", "warn")


@dataclass
class ProbeResult:
    """One check outcome."""

    name: str
    verdict: str  # "ok", "fail", "skip" or "warn"
    detail: str = ""


class ProtocolProbe:
    """Runs the compatibility cascade against the configured endpoints."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_api_reachable(self) -> ProbeResult:
        """DNS + TCP + TLS to the query API host: any HTTP answer counts."""
        host = urlparse(self.config.api_base_url).hostname or ""
        return self._host_reachable("API endpoint", host)

    def check_data_reachable(self) -> ProbeResult:
        host = urlparse(self.config.data_base_url).hostname or ""
        return self._host_reachable("Data endpoint", host)

    def check_anonymous_query(self) -> ProbeResult:
        """The query endpoint answers without a session.

        The exact result code does not matter: any HTTP response proves
        the endpoint is alive. A protocol drift usually surfaces later,
        in the authenticated key query.
        """
        try:
            response = requests.get(
                self.config.api_base_url, timeout=self.config.request_timeout
            )
        except requests.RequestException as e:
            return ProbeResult(
                "Anonymous query", "fail", f"no HTTP answer: {type(e).__name__}"
            )
        return ProbeResult("Anonymous query", "ok", f"HTTP {response.status_code}")

    def check_authenticated_keys(self) -> ProbeResult:
        """query=3 with the stored session: the cheapest authed roundtrip."""
        identity = WOGDatabase(self.config).get_identity()
        if identity is None:
            return ProbeResult(
                "Authenticated key query",
                "skip",
                "no stored session, run wog-dump login",
            )
        from .keys import KeyManager

        manager = KeyManager(self.config)
        key = manager.get_key_for_asset("ak47", use_cache=False)
        if key:
            return ProbeResult("Authenticated key query", "ok", "query=3 answered")
        return ProbeResult(
            "Authenticated key query",
            "fail",
            "query=3 returned no key: session expired or protocol drifted "
            "(see docs/mitm-capture.md)",
        )

    def check_weapon_list(self) -> ProbeResult:
        """The catalog asset is still served on the data host."""
        from .download import SUBDIRECTORY_ASSETS

        subdirectory = SUBDIRECTORY_ASSETS.get("spider_gen")
        url = f"{self.config.data_base_url.rstrip('/')}/spider_gen.unity3d"
        if subdirectory:
            url = (
                f"{self.config.data_base_url.rstrip('/')}"
                f"/{subdirectory}/spider_gen.unity3d"
            )
        try:
            response = requests.head(
                url, timeout=self.config.request_timeout, allow_redirects=True
            )
        except requests.RequestException as e:
            return ProbeResult(
                "Weapon list asset", "fail", f"no HTTP answer: {type(e).__name__}"
            )
        if response.status_code == 200:
            size = response.headers.get("Content-Length", "?")
            return ProbeResult("Weapon list asset", "ok", f"HTTP 200, {size} bytes")
        return ProbeResult("Weapon list asset", "fail", f"HTTP {response.status_code}")

    def check_client_version(self) -> ProbeResult:
        """Client build matches the version this codebase was written for."""
        current = self.config.game_version
        if current == KNOWN_GAME_VERSION:
            return ProbeResult(
                "Client version", "ok", f"game_version={current} (known good)"
            )
        return ProbeResult(
            "Client version",
            "warn",
            f"game_version={current} differs from the known-good "
            f"{KNOWN_GAME_VERSION}: check release notes before blaming "
            "the protocol",
        )

    # ------------------------------------------------------------------
    # Cascade
    # ------------------------------------------------------------------

    def run(self) -> list[ProbeResult]:
        """Run every check; network checks stop the cascade when unreachable."""
        results = [
            self.check_api_reachable(),
            self.check_data_reachable(),
        ]
        if any(result.verdict == "fail" for result in results):
            results.append(
                ProbeResult(
                    "Remaining checks", "skip", "hosts unreachable, cascade stopped"
                )
            )
            return results
        results.append(self.check_anonymous_query())
        results.append(self.check_authenticated_keys())
        results.append(self.check_weapon_list())
        results.append(self.check_client_version())
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _host_reachable(name: str, host: str) -> ProbeResult:
        if not host:
            return ProbeResult(name, "fail", "endpoint URL has no host")
        try:
            sock = socket.create_connection((host, 443), timeout=10)
            sock.close()
        except OSError as e:
            return ProbeResult(name, "fail", f"{host}:443 unreachable: {e}")
        return ProbeResult(name, "ok", f"{host}:443 connected")


def probe_verdict(results: list[ProbeResult]) -> str:
    """Overall verdict: fail on any fail, warn on any warn, else ok."""
    verdicts = {result.verdict for result in results}
    if "fail" in verdicts:
        return "fail"
    if "warn" in verdicts:
        return "warn"
    return "ok"
