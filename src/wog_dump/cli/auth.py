"""Login/logout commands for the WOG Dump CLI."""

from __future__ import annotations

import click

from ..core.auth import AuthManager
from .errors import error_handler


@click.command()
@click.option(
    "--email", type=str, default=None, help="Game account email (prompted when omitted)"
)
@click.option(
    "--password",
    type=str,
    default=None,
    help="Game account password (prompted when omitted)",
)
@click.pass_context
def login(ctx: click.Context, email: str | None, password: str | None) -> None:
    """Log into your World of Guns game account.

    The key API only serves authenticated clients. Enter the email and
    password of your game account (create one in-game first if you don't
    have it yet). A fresh session is obtained automatically on every run,
    so no sensitive data ever ships with the repository.

    Alternatively, skip the account entirely and put a captured session
    (WOG_AUTH_ID / WOG_AUTH_SESSION) into .env - see docs/mitm-capture.md.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Login"):
        if email is None:
            email = click.prompt("Game account email")
        if password is None:
            password = click.prompt("Game account password", hide_input=True)

        auth = AuthManager(config)
        identity = auth.login(email, password)

        logger.print_status(f"Logged in as {email} (id={identity.auth_id})", "success")
        logger.console.print(
            "Credentials and session are stored in the local database:\n"
            f"  {config.db_path}"
        )


@click.command()
@click.pass_context
def logout(ctx: click.Context) -> None:
    """Remove the stored game account credentials."""
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Logout"):
        AuthManager(config).logout()
        logger.print_status(
            f"Removed account credentials from {config.db_path}", "success"
        )
