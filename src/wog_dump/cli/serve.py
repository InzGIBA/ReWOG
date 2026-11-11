"""Serve command: local web UI over the core (task 16)."""

from __future__ import annotations

import sys
import webbrowser

import click

from .errors import error_handler, print_banner_if_standalone


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
            webbrowser.open(entry)

        uvicorn.run(app, host=host, port=port, log_level="warning")
