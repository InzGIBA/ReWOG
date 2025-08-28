"""WOG Dump - A modern tool to extract 3D models from World of Guns: Gun Disassembly.

This package provides functionality to:
- Download weapon assets from the game servers
- Decrypt encrypted asset files
- Unpack Unity3D assets
- Convert normal maps for external use
"""

from __future__ import annotations

__version__ = "2.1.0"
__author__ = "hampta"
__email__ = "hampta@example.com"
__description__ = "A modern tool to download, decrypt and unpack 3D gun models from World of Guns: Gun Disassembly"

# Version info tuple for programmatic access
VERSION_INFO = tuple(int(x) for x in __version__.split("."))

__all__ = [
    "__version__",
    "__author__", 
    "__email__",
    "__description__",
    "VERSION_INFO",
]