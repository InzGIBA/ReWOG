"""Serve command tests (moved from core CLI coverage)."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import Mock

from click.testing import CliRunner
from wog_plugin_web.cli import open_browser_quietly, serve


class TestServeCommand:
    def test_serve_without_deps(self, monkeypatch: Any) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "uvicorn":
                raise ImportError("no uvicorn")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        result = CliRunner().invoke(serve, ["--no-browser"])
        assert result.exit_code == 1
        assert "wog-plugin-web" in result.output

    def test_serve_starts_uvicorn(self, monkeypatch: Any) -> None:
        fake_uvicorn = Mock()
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        result = CliRunner().invoke(serve, ["--no-browser", "--port", "9001"])
        assert result.exit_code == 0, result.output
        fake_uvicorn.run.assert_called_once()
        call_args = fake_uvicorn.run.call_args
        assert call_args.kwargs["port"] == 9001
        assert call_args.kwargs["host"] == "127.0.0.1"


class TestOpenBrowserQuietly:
    def test_returns_bool_and_keeps_stdio_intact(self, capsys: Any) -> None:
        result = open_browser_quietly("http://127.0.0.1:1/nowhere")
        assert isinstance(result, bool)
        # The fd juggling must not leak: a later print still lands on stdout
        print("still-here")
        assert "still-here" in capsys.readouterr().out

    def test_no_desktop_helper_noise_on_stdout(self, capsys: Any) -> None:
        open_browser_quietly("http://127.0.0.1:1/nowhere")
        captured = capsys.readouterr()
        assert "Operation not supported" not in captured.out + captured.err
