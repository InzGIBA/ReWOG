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
        else:
            logger.warning(
                "Password given on the command line is visible in the "
                "process list (e.g. /proc/<pid>/cmdline on shared hosts); "
                "prefer the interactive prompt"
            )

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


@click.command()
@click.option(
    "--name",
    type=str,
    default=None,
    help="Account display name (prompted when omitted)",
)
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
def register(
    ctx: click.Context, name: str | None, email: str | None, password: str | None
) -> None:
    """Create a new World of Guns game account, then log into it.

    The same email/password registration the standalone game client
    offers (the server sends a confirmation email). After registering,
    the account is logged in right away so key fetching works without
    further steps.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Register"):
        if name is None:
            name = click.prompt("Account name")
        if email is None:
            email = click.prompt("Game account email")
        if password is None:
            password = click.prompt(
                "Game account password", hide_input=True, confirmation_prompt=True
            )
        else:
            logger.warning(
                "Password given on the command line is visible in the "
                "process list (e.g. /proc/<pid>/cmdline on shared hosts); "
                "prefer the interactive prompt"
            )

        auth = AuthManager(config)
        auth.register(name, email, password)
        identity = auth.login(email, password)

        logger.print_status(
            f"Registered and logged in as {email} (id={identity.auth_id})", "success"
        )
        logger.console.print(
            "Credentials and session are stored in the local database:\n"
            f"  {config.db_path}"
        )
