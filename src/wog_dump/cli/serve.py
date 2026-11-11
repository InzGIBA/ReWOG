"""Serve command: local web UI over the core (task 16)."""

from __future__ import annotations

import os
import sys
import webbrowser

import click

from .errors import error_handler, print_banner_if_standalone


def open_browser_quietly(url: str) -> bool:
    """Open the URL without letting desktop helpers print into the serve log.

    On headless Linux (WSL, servers) ``webbrowser`` falls back to
    ``gio open``/``xdg-open``; those desktop helpers fail with a printed
    "Operation not supported" because their subprocesses inherit our
    stdio. Park both file descriptors at devnull for the duration of
    the call, then report honestly whether anything launched.
    """
    saved_out, saved_err = os.dup(1), os.dup(2)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            return bool(webbrowser.open(url))
    except Exception:
        return False
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)


@click.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address")
@click.option("--port", default=8765, show_default=True, type=int)
@click.option(
    "--no-browser", is_flag=True, default=False, help="Do not open the browser"
)
@click.pass_context
def serve(ctx: click.Context, host: str, port: int, no_browser: bool) -> None:
    """Run the local web UI (pip install rewog[web]).

    Binds 127.0.0.1 by default and guards every route with a per-start
    token URL. Stages run as wog-dump subprocesses; their output
    streams into the page.

    \b
    Example:
        wog-dump serve --port 9000
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Web UI"):
        print_banner_if_standalone(ctx)
        try:
            import uvicorn
        except ImportError:
            logger.error("The web UI needs the web extra: pip install rewog[web]")
            sys.exit(1)

        from ..core.webapp.app import create_app

        app = create_app(config)
        entry = app.state.entry_url.replace("8765", str(port)).replace(
            "127.0.0.1", host
        )
        logger.print_status(f"Web UI: {entry}", "success")
        if host != "127.0.0.1":
            logger.print_status(
                f"Bound to {host}: the token URL is the only access control",
                "warning",
            )
        if not no_browser:
            if not open_browser_quietly(entry):
                logger.print_status(
                    "No browser found - open the URL above manually", "info"
                )

        uvicorn.run(app, host=host, port=port, log_level="warning")
