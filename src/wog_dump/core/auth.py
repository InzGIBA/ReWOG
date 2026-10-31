"""Game-account authentication for WOG Dump.

The key API only serves requests from authenticated clients. Instead of
shipping captured credentials, the tool logs into the user's own game
account (the same email/password login the game client offers) and
generates a random device id per installation. A fresh session is
obtained automatically whenever the stored one is missing or rejected.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from dataclasses import dataclass

from ..utils.logging import get_logger
from .api import ApiError, WOGApiClient, parse_response_fields
from .config import WOGConfig, get_config
from .database import SETTINGS_DEVICE_ID, SETTINGS_EMAIL, SETTINGS_PASSWORD, WOGDatabase


class AuthError(Exception):
    """Raised when authentication with the game API fails."""


@dataclass(frozen=True)
class Identity:
    """Client identity used for key requests."""

    auth_id: str
    session: str
    device_id: str


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

    def login(self, email: str, password: str) -> Identity:
        """Log into the game account and store the fresh session.

        The credentials are kept in the local database so future runs can
        re-login automatically when the session expires.
        """
        device_id = self.get_device_id()

        params = (
            "query=1"
            f"&email={email}"
            f"&password={password}"
            f"&dev={device_id}"
            f"&{self.client.build_common_params()}"
        )

        try:
            response_text = self.client.call(params)
        except Exception as e:
            raise AuthError(f"Login request failed: {e}") from e

        fields = parse_response_fields(response_text)
        result = fields.get("result", "")

        if result != "0":
            message = fields.get("errmsg", "") or self._login_error(response_text)
            raise AuthError(f"Login failed (result={result}): {message}")

        auth_id = fields.get("id")
        session = fields.get("session")
        if not auth_id or not session:
            raise AuthError(
                "Login response did not contain id/session: "
                + response_text.replace("\n", " ")[:200]
            )

        with self._login_lock:
            self.db.set_setting(SETTINGS_EMAIL, email)
            self.db.set_setting(SETTINGS_PASSWORD, password)
            self.db.save_identity(auth_id, session)

        identity = Identity(auth_id=auth_id, session=session, device_id=device_id)
        self.logger.info(f"Logged in as {email} (id={auth_id})")
        return identity

    @staticmethod
    def _login_error(response_text: str) -> str:
        """Best-effort error message from errmsg or the API debug output."""
        match = re.search(r"errmsg=([^&\s]+)", response_text)
        if match:
            return match.group(1).replace("+", " ")

        # The API narrates failures in _N= debug entries; use the last one
        debug_entries = re.findall(r"_\d+=[\d:.]+ ([^&]+)", response_text)
        if debug_entries:
            return str(debug_entries[-1]).replace("+", " ")

        return response_text.replace("\n", " ")[:200]

    def get_stored_identity(self) -> Identity | None:
        """The stored identity, if a login happened before."""
        stored = self.db.get_identity()
        if stored is None:
            return None
        device_id = self.db.get_setting(SETTINGS_DEVICE_ID)
        if device_id is None:
            return None
        return Identity(auth_id=stored[0], session=stored[1], device_id=device_id)

    def ensure_session(self, force_relogin: bool = False) -> Identity:
        """Return a usable identity, logging in when necessary.

        Uses the stored session; re-logins with the saved account
        credentials when there is none, or when force_relogin is set
        (the previous session was rejected).
        """
        if not force_relogin:
            identity = self.get_stored_identity()
            if identity is not None:
                return identity

        email = self.db.get_setting(SETTINGS_EMAIL)
        password = self.db.get_setting(SETTINGS_PASSWORD)
        if not email or not password:
            raise AuthError(
                "No game account configured. Run 'wog-dump login' and enter "
                "your World of Guns account email and password."
            )

        with self._login_lock:
            # Another thread may have refreshed the session while we waited
            if not force_relogin:
                identity = self.get_stored_identity()
                if identity is not None:
                    return identity
            return self.login(email, password)

    def logout(self) -> None:
        """Remove the stored account credentials and session."""
        self.db.delete_setting(SETTINGS_EMAIL)
        self.db.delete_setting(SETTINGS_PASSWORD)
        self.db.clear_identity()
        self.logger.info("Logged out: removed stored account credentials")

    def get_account_email(self) -> str | None:
        return self.db.get_setting(SETTINGS_EMAIL)


__all__ = ["AuthError", "AuthManager", "Identity", "ApiError"]
