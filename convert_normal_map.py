#!/usr/bin/env python3
"""
Legacy compatibility script for convert_normal_map.py.

This script provides compatibility for users still using the old
convert_normal_map.py script. It redirects to the new CLI interface.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

try:
    from wog_dump.utils.normal_map import cli_main
    from wog_dump.utils.logging import get_logger
except ImportError:
    print("Error: WOG Dump v2.0 is not properly installed.")
    print("Please run: pip install -e .")
    sys.exit(1)


def main():
    """Legacy main function with migration notice."""
    # Show deprecation warning
    warnings.warn(
        "convert_normal_map.py is deprecated. Use 'wog-convert-normals' command instead.",
        DeprecationWarning,
        stacklevel=2
    )
    
    logger = get_logger()
    
    logger.console.print("\n" + "="*60)
    logger.console.print("🔄 [yellow]Normal Map Converter Migration Notice[/yellow]")
    logger.console.print("="*60)
    logger.console.print(
        "You're using the legacy [red]convert_normal_map.py[/red] script.\n"
        "WOG Dump v2.0 now provides enhanced normal map conversion!\n"
    )
    logger.console.print("[green]New CLI command:[/green]")
    logger.console.print("  • [cyan]wog-convert-normals <path>[/cyan]     - Convert normal maps")
    logger.console.print("  • [cyan]wog-convert-normals --help[/cyan]     - Show all options")
    
    logger.console.print("\n[green]Enhanced features:[/green]")
    logger.console.print("  • Batch processing with progress bars")
    logger.console.print("  • Backup creation options")
    logger.console.print("  • Validation and analysis")
    logger.console.print("  • Advanced conversion options")
    logger.console.print("  • Recursive directory processing")
    
    logger.console.print("\n[yellow]Redirecting to new interface...[/yellow]")
    logger.console.print("="*60 + "\n")
    
    # Redirect to new CLI with same arguments
    try:
        cli_main()
    except SystemExit:
        pass  # Expected from Click
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.console.print("\nPlease use: [cyan]wog-convert-normals --help[/cyan]")
        sys.exit(1)


if __name__ == "__main__":
    main()
