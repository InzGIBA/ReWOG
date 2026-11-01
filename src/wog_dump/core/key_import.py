"""Import decryption keys and identity from captured game-client traffic.

Keys can be harvested from your own game traffic captured with a
TLS-intercepting proxy (see docs/mitm-capture.md). This module parses
those captures and extracts:

- the ``model=NAME`` / ``sync=KEY`` pairs exchanged with the key API
- the ``id``/``session``/``dev`` identity the client used, so a capture
  can seed the local database without a manual login

The game client connects to the API servers by raw IP address (the Host
header carries the domain), so traffic is matched by request shape
(query endpoint path + decodable BZ2 body), not by hostname.
"""

from __future__ import annotations

import bz2
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..utils.logging import get_logger
from .auth import AuthManager
from .config import WOGConfig, get_config
from .database import WOGDatabase

GAME_API_HOST = "ultimate-disassembly.com"

# The client reaches the API at /v/query<year>.php over raw IPs
QUERY_PATH_RE = re.compile(r"/v/query\d+\.php")

MODEL_RE = re.compile(r"model=([\w.\-]+)")
SYNC_RE = re.compile(r"sync=([0-9a-fA-F]{32})")


class KeyImportError(Exception):
    """Raised when a capture cannot be parsed."""


@dataclass(frozen=True)
class CapturedKey:
    """A model name paired with the sync key captured for it."""

    model: str
    key: str
    source: str


@dataclass(frozen=True)
class CapturedIdentity:
    """Client identity (id/session/dev) recovered from a capture."""

    auth_id: str
    session: str
    device_id: str | None
    source: str


@dataclass(frozen=True)
class MergeResult:
    """Outcome of merging captured keys into the key store."""

    total: int
    added: int
    updated: int


def write_keys_file(path: Path, keys: dict[str, str]) -> None:
    """Write keys as a shareable ``model key`` text file."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write("# WOG Dump Decryption Keys\n")
        f.write("# Format: weapon_name decryption_key\n\n")

        for model, key in sorted(keys.items()):
            f.write(f"{model} {key}\n")


def decode_api_body(data: bytes) -> str | None:
    """Decode a WOG API message body.

    API bodies are BZ2-compressed with a 4-byte little-endian length
    prefix; plain-text dumps are accepted as well. Returns None when the
    data is neither.
    """
    if not data:
        return None

    # Length-prefixed BZ2 first, then raw BZ2, then plain text
    for candidate in (data[4:], data):
        try:
            return bz2.decompress(candidate).decode("utf-8", errors="replace")
        except (OSError, EOFError, ValueError):
            continue

    try:
        return data.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return None


def _split_http_body(raw: bytes) -> bytes:
    """Return the body of a raw HTTP message (everything after the headers)."""
    for separator in (b"\r\n\r\n", b"\n\n"):
        index = raw.find(separator)
        if index != -1:
            return raw[index + len(separator) :]
    return raw


def _find_body_start(raw: bytes, pos: int = 0) -> int | None:
    """Index just past the first HTTP header block at/after pos, or None."""
    for separator in (b"\r\n\r\n", b"\n\n"):
        index = raw.find(separator, pos)
        if index != -1:
            return index + len(separator)
    return None


def _prefix_length(data: bytes) -> int | None:
    """Compressed length from a 4-byte little-endian prefix, if plausible."""
    if len(data) >= 4:
        length = int.from_bytes(data[:4], "little")
        if 0 < length <= len(data) - 4:
            return length
    return None


def _iter_message_texts(raw: bytes) -> list[str]:
    """Decode the payload of every message contained in a dump.

    Handles plain-text dumps and concatenated HTTP messages whose bodies
    are length-prefixed BZ2 chunks.
    """
    texts: list[str] = []

    whole = decode_api_body(raw)
    if whole is not None:
        texts.append(whole)

    pos = 0
    while pos < len(raw):
        body_start = _find_body_start(raw, pos)
        if body_start is None:
            break
        data = raw[body_start:]

        length = _prefix_length(data)
        if length is not None:
            try:
                texts.append(
                    bz2.decompress(data[4 : 4 + length]).decode(
                        "utf-8", errors="replace"
                    )
                )
                pos = body_start + 4 + length
                continue
            except (OSError, EOFError, ValueError):
                pass

        text = decode_api_body(data)
        if text is not None:
            texts.append(text)
        break  # cannot reliably locate the next message

    return texts


def _parse_request_response(
    request_body: bytes, response_body: bytes, source: str
) -> CapturedKey | None:
    """Pair a model name from an API request with the sync key from its response."""
    request_text = decode_api_body(request_body) or ""
    response_text = decode_api_body(response_body) or ""

    model_match = MODEL_RE.search(request_text)
    sync_match = SYNC_RE.search(response_text)
    if not (model_match and sync_match):
        return None

    return CapturedKey(
        model=model_match.group(1),
        key=sync_match.group(1).lower(),
        source=source,
    )


def _is_game_host(host: str) -> bool:
    """Whether a host is exactly a game domain (no substring matches)."""
    return host == GAME_API_HOST or host.endswith("." + GAME_API_HOST)


def _is_api_request(path: str, host: str, request_body: bytes) -> bool:
    """Match a captured request against the WOG query API.

    The client connects by raw IP (Host header carries the domain), so a
    request counts when its path looks like the query endpoint, its host
    is a known game domain, or its body decodes to a query payload.
    """
    if QUERY_PATH_RE.search(path):
        return True
    if GAME_API_HOST in host:
        return True
    text = decode_api_body(request_body) or ""
    return "query=" in text and "&ver=" in text


def extract_keys_from_flows(path: Path) -> list[CapturedKey]:
    """Extract keys from a mitmproxy flow dump (``*.flows``)."""
    try:
        from mitmproxy import http as mitm_http
        from mitmproxy import io as mitm_io
    except ImportError as e:
        raise KeyImportError(
            "Reading mitmproxy captures requires the mitmproxy package. "
            "Install it with: uv sync --extra mitm (or: pip install mitmproxy)"
        ) from e

    keys: list[CapturedKey] = []
    with open(path, "rb") as f:
        for flow in mitm_io.FlowReader(f).stream():
            if not isinstance(flow, mitm_http.HTTPFlow) or flow.response is None:
                continue
            request_body = flow.request.content or b""
            if not _is_api_request(flow.request.path, flow.request.host, request_body):
                continue

            captured = _parse_request_response(
                request_body,
                flow.response.content or b"",
                source=flow.request.pretty_url,
            )
            if captured:
                keys.append(captured)

    return keys


def extract_identity_from_flows(path: Path) -> CapturedIdentity:
    """Recover the client identity (id/session/dev) from a mitmproxy dump.

    Identity-bearing requests on a genuine game host always win over
    shape-only matches regardless of order in the file - a capture is
    file-authored content, so ``evil-ultimate-disassembly.com`` or a
    query-shaped request on any host must never define the live
    identity. Among equals the first request carrying all three fields
    wins; Steam-login requests (query=2, no id yet) are skipped.
    """
    try:
        from mitmproxy import http as mitm_http
        from mitmproxy import io as mitm_io
    except ImportError as e:
        raise KeyImportError(
            "Reading mitmproxy captures requires the mitmproxy package. "
            "Install it with: uv sync --extra mitm (or: pip install mitmproxy)"
        ) from e

    fallback: CapturedIdentity | None = None

    with open(path, "rb") as f:
        for flow in mitm_io.FlowReader(f).stream():
            if not isinstance(flow, mitm_http.HTTPFlow):
                continue
            request_body = flow.request.content or b""
            if not _is_api_request(flow.request.path, flow.request.host, request_body):
                continue

            text = decode_api_body(request_body) or ""
            fields = {}
            for segment in text.split("&"):
                if "=" in segment:
                    key, _, value = segment.partition("=")
                    fields[key] = value

            auth_id = fields.get("id")
            session = fields.get("session")
            if not auth_id or not session or auth_id == "0" or session == "0":
                continue

            identity = CapturedIdentity(
                auth_id=auth_id,
                session=session,
                device_id=fields.get("dev"),
                source=flow.request.pretty_url,
            )
            if _is_game_host(flow.request.host):
                return identity
            if fallback is None:
                fallback = identity

    if fallback is not None:
        return fallback
    raise KeyImportError(f"No authenticated API request (id/session) found in {path}")


def extract_keys_from_saz(path: Path) -> list[CapturedKey]:
    """Extract keys from a Fiddler SAZ archive (``*.saz``)."""
    keys: list[CapturedKey] = []

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            request_names = sorted(
                n for n in names if re.search(r"raw/.*_c\.(txt|dat)$", n, re.IGNORECASE)
            )
            for request_name in request_names:
                prefix = request_name[: request_name.rindex("_c.")]
                response_name = next(
                    (
                        n
                        for n in names
                        if n.startswith(prefix)
                        and re.search(r"_s\.(txt|dat)$", n, re.IGNORECASE)
                    ),
                    None,
                )
                if response_name is None:
                    continue

                request_raw = archive.read(request_name)
                request_body = _split_http_body(request_raw)
                # The request line (e.g. "PUT /v/query2026.php?soc=steam HTTP/1.1")
                request_line = request_raw.split(b"\r\n", 1)[0].decode(
                    "utf-8", errors="replace"
                )
                host = ""
                for header_line in request_raw.split(b"\r\n\r\n", 1)[0].split(b"\r\n"):
                    if header_line.lower().startswith(b"host:"):
                        host = header_line.split(b":", 1)[1].strip().decode()
                if not _is_api_request(request_line, host, request_body):
                    continue

                captured = _parse_request_response(
                    request_body,
                    _split_http_body(archive.read(response_name)),
                    source=prefix,
                )
                if captured:
                    keys.append(captured)
    except zipfile.BadZipFile as e:
        raise KeyImportError(f"Not a valid SAZ (zip) archive: {path}") from e

    return keys


def _extract_from_flat_file(path: Path) -> list[CapturedKey]:
    """Extract a key from a single dump file.

    Works when the model name and the sync key both appear in the file -
    a request/response pair copied into one text file, or a dump of
    concatenated HTTP messages.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise KeyImportError(f"Failed to read {path}: {e}") from e

    texts = _iter_message_texts(raw)
    for text in texts:
        # Only trust an unambiguous message: exactly one model and one sync key
        models = MODEL_RE.findall(text)
        syncs = SYNC_RE.findall(text)
        if len(models) == 1 and len(syncs) == 1:
            return [CapturedKey(models[0], syncs[0].lower(), source=str(path))]

    # No self-contained message: pair across the dump when exactly one
    # model and one sync key appear in total (e.g. a request body
    # followed by the response body in one file)
    all_models = [m for text in texts for m in MODEL_RE.findall(text)]
    all_syncs = [s for text in texts for s in SYNC_RE.findall(text)]
    if len(all_models) == 1 and len(all_syncs) == 1:
        return [CapturedKey(all_models[0], all_syncs[0].lower(), source=str(path))]

    return []


def extract_keys(path: Path) -> list[CapturedKey]:
    """Extract model->key pairs from a capture.

    ``path`` may be a mitmproxy ``.flows`` dump, a Fiddler ``.saz``
    archive, a single dumped request/response file, or a directory
    containing any of these.
    """
    path = Path(path)
    if not path.exists():
        raise KeyImportError(f"Capture not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".flows":
        return extract_keys_from_flows(path)
    if suffix == ".saz":
        return extract_keys_from_saz(path)
    if path.is_dir():
        keys: list[CapturedKey] = []
        for file_path in sorted(path.rglob("*")):
            if not file_path.is_file():
                continue
            file_suffix = file_path.suffix.lower()
            if file_suffix == ".flows":
                keys.extend(extract_keys_from_flows(file_path))
            elif file_suffix == ".saz":
                keys.extend(extract_keys_from_saz(file_path))
            else:
                keys.extend(_extract_from_flat_file(file_path))
        return keys
    if path.is_file():
        return _extract_from_flat_file(path)

    raise KeyImportError(f"Capture not found: {path}")


def dedupe_keys(captured: list[CapturedKey]) -> dict[str, str]:
    """Reduce captured keys to a model->key mapping (last capture wins)."""
    logger = get_logger()
    result: dict[str, str] = {}
    for item in captured:
        previous = result.get(item.model)
        if previous is not None and previous != item.key:
            logger.warning(
                f"Different keys captured for {item.model}: "
                f"{previous} -> {item.key} (keeping the latest)"
            )
        result[item.model] = item.key
    return result


def merge_keys(
    new_keys: dict[str, str], config: WOGConfig | None = None
) -> MergeResult:
    """Merge captured keys into the local database.

    Existing keys are never dropped; captured values win on conflict.
    """
    config = config or get_config()
    db = WOGDatabase(config)
    added, updated = db.merge_keys(new_keys, source="mitm_import")
    total = len(db.get_keys())
    return MergeResult(total=total, added=added, updated=updated)


def import_identity(
    identity: CapturedIdentity, config: WOGConfig | None = None, force: bool = False
) -> None:
    """Store a captured client identity (id/session/dev) in the database.

    A capture is file-authored content: an existing live identity is
    never silently replaced - the import refuses (``force=True``
    overrides) unless the captured identity is byte-identical. The
    source URL of the winning request is announced so the user can see
    which host the credentials came from.
    """
    config = config or get_config()
    db = WOGDatabase(config)
    logger = get_logger()

    existing = db.get_identity()
    if (
        existing is not None
        and existing != (identity.auth_id, identity.session)
        and not force
    ):
        raise KeyImportError(
            f"Refusing to overwrite the existing stored identity "
            f"(id={existing[0]}) with the captured one (id={identity.auth_id}); "
            f"re-run with --force to replace it"
        )

    device_id = identity.device_id
    if device_id:
        db.set_setting("device_id", device_id)
    elif db.get_setting("device_id") is None:
        db.set_setting("device_id", AuthManager.generate_device_id())

    db.save_identity(identity.auth_id, identity.session)
    logger.info(
        f"Imported captured identity (id={identity.auth_id}) from {identity.source}"
    )
