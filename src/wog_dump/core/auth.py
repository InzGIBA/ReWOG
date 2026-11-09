"""Game-account authentication for WOG Dump.

The key API only serves requests from authenticated clients. Instead of
shipping captured credentials, the tool registers and logs into the
user's own game account (the same email/password flow the standalone
game client offers) and generates a random device id per installation.
A fresh session is obtained automatically whenever the stored one is
missing or rejected.

The request shapes mirror the standalone client capture (Nov 2026):
login is ``query=1`` with the password sent as its MD5 hex digest under
``pass=``; the server answers with ``acc_id``/``session``. The session
value is a per-account counter the server bumps over time.

Accounts live on one regional server ("shard": eu1/eu2/us3/us4). A login
sent to the wrong shard is answered with ``result=777`` plus a
``server=`` field naming the home host; the standalone client re-sends
the login there, and so does :meth:`AuthManager.login` (once, and only
to a host under the game's own domain).
"""

from __future__ import annotations

import hashlib
import os
import platform as _platform
import re
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlparse

from ..utils.logging import get_logger
from .api import (
    ApiError,
    WOGApiClient,
    form_encode,
    parse_response_fields,
    sign_query,
)
from .config import WOGConfig, get_config
from .database import (
    SETTINGS_API_BASE_URL,
    SETTINGS_DEVICE_ID,
    SETTINGS_EMAIL,
    SETTINGS_PASSWORD,
    WOGDatabase,
)

# Shard hosts the game's own servers redirect to (server= field of a
# result=777 login answer); anything else in that field is refused
SHARD_DOMAIN = "ultimate-disassembly.com"


class AuthError(Exception):
    """Raised when authentication with the game API fails."""


class _ShardRedirect(Exception):
    """Internal: a result=777 answer named the account's home shard."""

    def __init__(self, target_url: str) -> None:
        super().__init__(target_url)
        self.target_url = target_url


@dataclass(frozen=True)
class Identity:
    """Client identity used for key requests."""

    auth_id: str
    session: str
    device_id: str


def build_sys_info() -> str:
    """Machine report sent with the login query (``sys_info=`` field).

    The game client reports OS/CPU/RAM/GPU for its own statistics; the
    server does not validate the content. Best effort from the stdlib,
    in the client's five-line format.
    """
    cpu = _platform.processor() or _platform.machine()
    try:
        cores = str(os.cpu_count() or 1)
    except Exception:
        cores = "1"
    return (
        f"OS:{_platform.platform()}|wog-dump\n"
        f"CPU:{cpu}|{cores}|0\n"
        "Memory:0|0\n"
        "GPU:||0\n"
        "Rating:0"
    )


class AuthManager:
    """Manages the generated device id and game-account sessions."""

    def __init__(
        self,
        config: WOGConfig | None = None,
        db: WOGDatabase | None = None,
        client: WOGApiClient | None = None,
    ) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.db = db or WOGDatabase(self.config)
        self.client = client or WOGApiClient(self.config)
        # Key fetching is parallel: never run two logins at once.
        # RLock: login() may be called while ensure_session() holds the lock.
        self._login_lock = threading.RLock()
        self._apply_remembered_shard()

    @staticmethod
    def generate_device_id() -> str:
        """Random 40-hex device id (same shape the game client uses)."""
        return hashlib.sha1(secrets.token_bytes(32)).hexdigest()

    def get_device_id(self) -> str:
        """The per-installation device id, generated on first use."""
        device_id = self.db.get_setting(SETTINGS_DEVICE_ID)
        if device_id is None:
            device_id = self.generate_device_id()
            self.db.set_setting(SETTINGS_DEVICE_ID, device_id)
            self.logger.debug("Generated new device id")
        return device_id

    def _common_query_fields(self) -> list[tuple[str, str]]:
        """Fields every client query carries, in captured order."""
        return [
            ("ver", self.config.game_version),
            ("uver", self.config.unity_version),
            ("dev", self.get_device_id()),
            ("session", "0"),
            ("id", "0"),
            ("time", str(int(time.time()))),
        ]

    def login(self, email: str, password: str) -> Identity:
        """Log into the game account and store the fresh session.

        Mirrors the standalone client's email login (``query=1``): the
        password travels as its MD5 hex digest under ``pass=``, never in
        plain text. The credentials are kept in the local database so
        future runs can re-login automatically when the session expires.

        A ``result=777`` answer means the account lives on another
        shard; the request is then re-sent to the home server named in
        the response (once), exactly like the standalone client, and the
        home shard is remembered in the local database.
        """
        try:
            return self._login_attempt(email, password)
        except _ShardRedirect as redirect:
            return self._follow_shard_redirect(email, password, redirect.target_url)

    def _login_attempt(self, email: str, password: str) -> Identity:
        """One login request against the currently configured API URL."""
        # Standalone login shape (captured Nov 2026, ver 2.2.3t6):
        # query=1&email=..&pass=<md5>&lang=..&sys_info=..&ver=..&uver=..
        #   &dev=..&session=0&id=0&time=..&platform=&hash=..
        device_id = self.get_device_id()
        pairs: list[tuple[str, str]] = [
            ("query", "1"),
            ("email", email),
            ("pass", hashlib.md5(password.encode("utf-8")).hexdigest()),
            ("lang", self.config.api_lang),
            ("sys_info", build_sys_info()),
        ]
        pairs += self._common_query_fields()
        pairs.append(("platform", ""))

        params = sign_query(
            "&".join(f"{key}={form_encode(value)}" for key, value in pairs)
        )

        try:
            response_text = self.client.call(params)
        except Exception as e:
            raise AuthError(f"Login request failed: {e}") from e

        fields = parse_response_fields(response_text)
        result = fields.get("result", "")

        if result == "777":
            # The account exists but lives on another shard. Verified
            # live (Nov 2026): the answer carries
            # server=<home host> plus the narration "redirect to <host>".
            target = self._shard_base_url(fields.get("server", ""))
            if target is None:
                raise AuthError(
                    "Login failed (result=777): the account lives on another "
                    "shard, but the server named no valid home "
                    f"(server={fields.get('server', '')!r})"
                )
            raise _ShardRedirect(target)

        if result != "0":
            message = self._login_error(response_text, fields.get("errmsg", ""))
            raise AuthError(f"Login failed (result={result}): {message}")

        # The query2026 API answers with acc_id; accept the plain id
        # spelling too in case older servers are ever addressed
        auth_id = fields.get("acc_id") or fields.get("id")
        session = fields.get("session")
        if not auth_id or not session:
            raise AuthError(
                "Login response did not contain acc_id/session: "
                + response_text.replace("\n", " ")[:200]
            )

        with self._login_lock:
            self.db.set_setting(SETTINGS_EMAIL, email)
            self.db.set_setting(SETTINGS_PASSWORD, password)
            self.db.save_identity(auth_id, session)

        identity = Identity(auth_id=auth_id, session=session, device_id=device_id)
        self.logger.info(f"Logged in as {email} (id={auth_id})")
        return identity

    def _follow_shard_redirect(
        self, email: str, password: str, target_url: str
    ) -> Identity:
        """Re-send a 777-redirected login to the account's home shard.

        One hop only: a second redirect is refused (the home shard hosts
        the account, so another 777 there means something is wrong). The
        remembered shard keeps later runs, and the key queries of the
        current run, on the right server without the extra round trip.
        """
        current_host = urlparse(self.config.api_base_url).hostname or ""
        target_host = urlparse(target_url).hostname or ""
        if target_host == current_host:
            raise AuthError(
                f"Login failed (result=777): {current_host} says the account "
                "is hosted on another shard but named this same server; "
                "nothing to redirect to"
            )

        self.config.api_base_url = target_url
        self.logger.info(
            f"Account {email} is hosted on shard {target_host}: following "
            f"the server's redirect from {current_host}"
        )
        try:
            identity = self._login_attempt(email, password)
        except _ShardRedirect as again:
            raise AuthError(
                "Login failed (result=777): shard "
                f"{urlparse(again.target_url).hostname} redirected again; "
                "refusing to follow more than one shard redirect"
            ) from again

        self.db.set_setting(SETTINGS_API_BASE_URL, target_url)
        self.logger.info(
            f"{target_host} is this account's home shard (remembered in "
            f"{self.config.db_path})"
        )
        return identity

    @staticmethod
    def _shard_base_url(server_field: str) -> str | None:
        """Canonical API URL for a ``server=`` shard host, or None.

        The value comes from a server response and ends up in a request
        URL, so accept only a bare hostname under the game's own domain:
        no scheme, port, path, credentials or punctuation beyond shard
        label dashes.
        """
        host = server_field.strip().lower().rstrip(".")
        suffix = "." + SHARD_DOMAIN
        if not host.endswith(suffix):
            return None
        label = host[: -len(suffix)]
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", label):
            return None
        return f"https://{host}/v/query2026.php"

    def _apply_remembered_shard(self) -> None:
        """Point the client at the remembered home shard of the account.

        The home shard is a property of the account, confirmed by the
        server on the last successful login (result=777 redirect), so it
        wins over WOG_API_BASE_URL: the variable is the probe endpoint
        for the first login and after logout, not a persistent override.
        A conflict is reported; ``wog-dump logout`` resets the shard
        with the account.
        """
        stored = self.db.get_setting(SETTINGS_API_BASE_URL)
        if not stored:
            return
        target = self._shard_base_url(urlparse(stored).hostname or "")
        if target is None or target == self.config.api_base_url:
            return

        env_url = os.environ.get("WOG_API_BASE_URL", "").strip()
        if env_url:
            env_host = urlparse(env_url).hostname or env_url
            self.logger.warning(
                f"WOG_API_BASE_URL points at {env_host}, but the stored "
                f"account lives on shard {urlparse(target).hostname} "
                "(confirmed by the server on the last login); using the "
                "remembered shard - 'wog-dump logout' resets it"
            )

        self.config.api_base_url = target
        self.logger.debug(
            f"Using remembered home shard "
            f"{urlparse(target).hostname} for the stored account"
        )

    def register(self, name: str, email: str, password: str) -> None:
        """Create a new game account (the standalone client's ``query=39``).

        The registration password travels in plain text (exactly like the
        captured client - the MD5 hashing is login-only) and the server
        sends a confirmation email. No session is returned; call
        :meth:`login` afterwards to obtain one.
        """
        # Captured standalone shape:
        # query=39&reg_name=..&reg_email=..&reg_pass=..&reg_subs=0&ver=..
        #   &uver=..&dev=..&session=0&id=0&time=..&hash=..
        pairs: list[tuple[str, str]] = [
            ("query", "39"),
            ("reg_name", name),
            ("reg_email", email),
            ("reg_pass", password),
            ("reg_subs", "0"),
        ]
        pairs += self._common_query_fields()

        params = sign_query(
            "&".join(f"{key}={form_encode(value)}" for key, value in pairs)
        )

        try:
            response_text = self.client.call(params)
        except Exception as e:
            raise AuthError(f"Registration request failed: {e}") from e

        fields = parse_response_fields(response_text)
        result = fields.get("result", "")

        if result != "0":
            message = self._login_error(response_text, fields.get("errmsg", ""))
            raise AuthError(f"Registration failed (result={result}): {message}")

        self.logger.info(f"Registered game account for {email}")

    @staticmethod
    def _login_error(response_text: str, errmsg: str = "") -> str:
        """Best-effort error message from errmsg or the API debug output.

        Values arrive form-encoded (spaces as ``+``, punctuation as
        percent hex) and are decoded for display: ``..shard+found+%21``
        reads "..shard found !", not "..shard found %21".
        """
        if errmsg:
            return unquote_plus(errmsg)

        match = re.search(r"errmsg=([^&\s]+)", response_text)
        if match:
            return unquote_plus(match.group(1))

        # The API narrates failures in _N= debug entries; use the last one
        debug_entries = re.findall(r"_\d+=[\d:.]+ ([^&]+)", response_text)
        if debug_entries:
            return unquote_plus(str(debug_entries[-1]))

        return response_text.replace("\n", " ")[:200]

    def get_stored_identity(self) -> Identity | None:
        """The stored identity, if a login or import happened before."""
        stored = self.db.get_identity()
        if stored is None:
            return None
        device_id = self.db.get_setting(SETTINGS_DEVICE_ID)
        if device_id is None:
            return None
        return Identity(auth_id=stored[0], session=stored[1], device_id=device_id)

    @staticmethod
    def _env_session() -> tuple[str, str] | None:
        """Session pair provided via environment / .env.

        WOG_AUTH_ID + WOG_AUTH_SESSION from your own capture work; the
        device id (WOG_DEVICE_ID) is optional - the API accepts any
        value with a valid session.
        """
        auth_id = os.environ.get("WOG_AUTH_ID", "").strip()
        session = os.environ.get("WOG_AUTH_SESSION", "").strip()
        if not auth_id or not session:
            return None
        return auth_id, session

    def _env_device_id(self) -> str:
        """Optional WOG_DEVICE_ID from the environment, or the stored/generated one."""
        env_device = os.environ.get("WOG_DEVICE_ID", "").strip()
        if env_device:
            self.db.set_setting(SETTINGS_DEVICE_ID, env_device)
            return env_device
        return self.get_device_id()

    def ensure_session(self, force_relogin: bool = False) -> Identity:
        """Return a usable identity, logging in when necessary.

        Resolution order: stored session, then WOG_AUTH_ID/WOG_AUTH_SESSION
        from the environment (imported into the database), then a fresh
        email login with the configured account.
        """
        if not force_relogin:
            identity = self.get_stored_identity()
            if identity is not None:
                return identity

            env_session = self._env_session()
            if env_session is not None:
                auth_id, session = env_session
                device_id = self._env_device_id()
                self.db.save_identity(auth_id, session)
                self.logger.info(f"Using identity from environment (id={auth_id})")
                return Identity(auth_id=auth_id, session=session, device_id=device_id)

        email = (
            self.db.get_setting(SETTINGS_EMAIL)
            or os.environ.get("WOG_ACCOUNT_EMAIL", "").strip()
            or None
        )
        password = (
            self.db.get_setting(SETTINGS_PASSWORD)
            or os.environ.get("WOG_ACCOUNT_PASSWORD", "").strip()
            or None
        )
        if not email or not password:
            raise AuthError(
                "No game account configured. Either log in ('wog-dump login'), "
                "set WOG_AUTH_ID/WOG_AUTH_SESSION from your own capture in .env "
                "(see docs/mitm-capture.md), or set WOG_ACCOUNT_EMAIL/"
                "WOG_ACCOUNT_PASSWORD."
            )

        with self._login_lock:
            # Another thread may have refreshed the session while we waited
            if not force_relogin:
                identity = self.get_stored_identity()
                if identity is not None:
                    return identity
            return self.login(email, password)

    def logout(self) -> None:
        """Remove the stored account credentials, session and device id.

        Clearing the device id too keeps logout terminal: otherwise a
        WOG_AUTH_ID/WOG_AUTH_SESSION pair still in the environment would
        silently re-import an identity on the next run. The remembered
        home shard belongs to the account and goes with it.
        """
        self.db.delete_setting(SETTINGS_EMAIL)
        self.db.delete_setting(SETTINGS_PASSWORD)
        self.db.delete_setting(SETTINGS_DEVICE_ID)
        self.db.delete_setting(SETTINGS_API_BASE_URL)
        self.db.clear_identity()
        self.logger.info("Logged out: removed stored account credentials")

    def get_account_email(self) -> str | None:
        return self.db.get_setting(SETTINGS_EMAIL)


__all__ = ["AuthError", "AuthManager", "Identity", "ApiError", "build_sys_info"]
