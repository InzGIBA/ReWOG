"""WOG Dump - Enhanced tool for extracting 3D models from World of Guns: Gun Disassembly.

This package provides a complete pipeline for downloading, decrypting, and unpacking
3D gun models from World of Guns: Gun Disassembly with modern Python features,
comprehensive error handling, and performance monitoring.
"""

from __future__ import annotations

__version__ = "2.3.1"
__author__ = "hampta, inzgiba"
__email__ = "inzgiba@gmail.com"
__license__ = "MIT"
__description__ = "Enhanced tool for extracting 3D models from World of Guns: Gun Disassembly"

# Version info tuple for programmatic access
VERSION_INFO = tuple(int(x) for x in __version__.split("."))

__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "__description__",
    "VERSION_INFO",
]
