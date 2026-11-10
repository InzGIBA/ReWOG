"""Protocol fixture tests: parser shapes and the secret guard (task 13)."""

from __future__ import annotations

import base64
import bz2
import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "protocol"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestFixturesPresent:
    def test_fixture_set_complete(self) -> None:
        names = {p.name for p in FIXTURES.glob("*.json")}
        assert {
            "anonymous_query.json",
            "weapon_list_head.json",
            "login_shape_synthetic.json",
        } <= names


class TestParserShapes:
    def test_anonymous_response_decompresses(self) -> None:
        from wog_dump.core.api import WOGApiClient

        fixture = load_fixture("anonymous_query.json")
        raw = base64.b64decode(fixture["data_b64"])
        if fixture["kind"] == "bz2":
            body = bz2.decompress(raw)
            # BZ2 payload parses as key=value text
            assert b"=" in body
        else:
            assert fixture["status"] in (200, 400, 403, 405)

    def test_login_shape_carries_acc_id(self) -> None:
        """The server answers login with acc_id= (the v2.7.0 discovery)."""
        fixture = load_fixture("login_shape_synthetic.json")
        raw = base64.b64decode(fixture["data_b64"])
        fields = dict(piece.split("=", 1) for piece in raw.decode("utf-8").split("&"))
        assert "acc_id" in fields
        assert "session" in fields

    def test_weapon_list_head_shape(self) -> None:
        fixture = load_fixture("weapon_list_head.json")
        assert fixture["kind"] == "head"
        assert fixture["status"] == 200
        assert fixture["content_length"] > 0


class TestSecretGuard:
    """No fixture may carry credential material, ever."""

    SECRET_RE = re.compile(
        rb"(pass|hash|session|acc_id|auth_id|device)=[^&\s\x00]{4,}", re.IGNORECASE
    )

    def test_no_secrets_in_any_fixture(self) -> None:
        violations = []
        for path in sorted(FIXTURES.glob("*.json")):
            data = path.read_bytes()
            for match in self.SECRET_RE.finditer(data):
                fragment = match.group(0)
                if b"SCRUBBED" in fragment:
                    continue
                violations.append((path.name, fragment[:40]))
        assert violations == [], f"credential-shaped data in fixtures: {violations}"

    def test_synthetic_fixture_is_marked(self) -> None:
        fixture = load_fixture("login_shape_synthetic.json")
        assert "SYNTHETIC" in fixture["note"]


class TestProbeVerdicts:
    def test_verdicts_cascade(self) -> None:
        from wog_dump.core.probe import ProbeResult, probe_verdict

        assert probe_verdict([ProbeResult("a", "ok")]) == "ok"
        assert (
            probe_verdict([ProbeResult("a", "ok"), ProbeResult("b", "warn")]) == "warn"
        )
        assert (
            probe_verdict([ProbeResult("a", "ok"), ProbeResult("b", "fail")]) == "fail"
        )

    def test_cascade_stops_on_unreachable_hosts(self) -> None:
        from wog_dump.core.probe import ProbeResult, ProtocolProbe

        probe = ProtocolProbe.__new__(ProtocolProbe)

        def fail_api(self: Any) -> ProbeResult:
            return ProbeResult("API endpoint", "fail", "down")

        def ok_data(self: Any) -> ProbeResult:
            return ProbeResult("Data endpoint", "ok", "up")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ProtocolProbe, "check_api_reachable", fail_api)
            mp.setattr(ProtocolProbe, "check_data_reachable", ok_data)
            results = ProtocolProbe.run(probe)
        assert results[-1].verdict == "skip"
