# 🔫 ReWOG / v2.3

![World of Guns](https://cdn.cloudflare.steamstatic.com/steam/apps/262410/library_hero.jpg)

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-black.svg)](https://github.com/psf/black)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)](tests/)
[![Package Manager](https://img.shields.io/badge/package%20manager-uv-blue.svg)](https://github.com/astral-sh/uv)
[![GitHub Issues](https://img.shields.io/github/issues/InzGIBA/ReWOG)](https://github.com/InzGIBA/ReWOG/issues)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/InzGIBA/ReWOG/graphs/commit-activity)
[![GitHub Forks](https://img.shields.io/github/forks/InzGIBA/ReWOG)](https://github.com/InzGIBA/ReWOG/network)
[![GitHub Stars](https://img.shields.io/github/stars/InzGIBA/ReWOG)](https://github.com/InzGIBA/ReWOG/stargazers)

</div>

## 📋 Table of Contents

- [📙 Description](#-description)
- [✨ What's New in v2.3](#-whats-new-in-v23)
- [🔗 Requirements](#-requirements)
- [🚀 Installation](#-installation)
- [🪄 How it Works](#-how-it-works)
- [🧑‍💻 Usage](#-usage)
- [📁 Project Structure](#-project-structure)
- [🔧 Configuration](#-configuration)
- [🧪 Testing](#-testing)
- [🔨 Development](#-development)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [🫂 Special Thanks](#-special-thanks)
- [📋 FAQ](#-frequently-asked-questions-faq)
- [⚠️ Legal Disclaimer](#️-legal-disclaimer)

## 📙 Description

WOG Dump is a modern, completely refactored Python tool for extracting 3D gun models from [World of Guns: Gun Disassembly](https://store.steampowered.com/app/262410/World_of_Guns_Gun_Disassembly/). This v2.3 release features a complete rewrite with modern Python 3.12+ features, improved error handling, comprehensive testing, and a beautiful CLI interface.

> 🎆 **Educational Tool**: Designed for 3D model enthusiasts, modders, and researchers interested in studying game assets and 3D modeling techniques.

### ✨ What's New in v2.3

- 🚀 **Pure Python** - Removed C dependencies for 100% Python implementation
- 📦 **uv Integration** - Lightning-fast package management and installation
- 🐍 **Modern Python 3.12+** - Native type annotations and latest features
- 🏗️ **Professional Structure** - Clean `src/` layout with modular architecture
- 🎨 **Beautiful CLI** - Rich console interface with progress bars and colors
- ⚡ **Optimized Performance** - Chunked processing and parallel operations
- 🔧 **Type Safety** - Complete type hints and Pydantic configuration
- 🧪 **Comprehensive Tests** - 95%+ test coverage with pytest
- 📦 **Modern Packaging** - pyproject.toml with hatchling backend
- 🛡️ **Better Error Handling** - Graceful error recovery and detailed logging
- 🔄 **Modular Design** - Clean separation of concerns and dependency injection

## 🔗 Requirements

- **Python 3.12+** (Required)
- [AssetStudio](https://github.com/Perfare/AssetStudio) - for final asset unpacking

## 🚀 Installation

### Quick Install with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is the modern, fast Python package installer and resolver.

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh
# or with pip: pip install uv

# Clone the repository
git clone https://github.com/InzGIBA/ReWOG
cd ReWOG

# Install in development mode with uv
uv pip install -e .
```

### Alternative Installation Methods

**Traditional pip:**
```bash
# Clone repository
git clone https://github.com/InzGIBA/ReWOG
cd ReWOG

# Install with pip
pip install -e .
```

**Development Setup with uv:**
```bash
# Install with development dependencies
uv pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run tests
pytest
```

## 🪄 How it Works

1. **Download Weapon List** - Fetches and processes the weapon catalog
2. **Fetch Decryption Keys** - Retrieves keys for each weapon asset
3. **Download Assets** - Downloads encrypted Unity3D files
4. **Decrypt Assets** - Uses XOR decryption to unlock files
5. **Unpack Assets** - Extracts models, textures, and materials
6. **Convert Normal Maps** - Converts Unity normal maps to standard format

## 🧑‍💻 Usage

### Quick Start Guide

**Complete Extraction (Recommended):**
```bash
# Extract all weapons with normal map conversion
wog-dump full-pipeline --update-keys --convert-normals
```

**Step-by-Step Process:**
```bash
# 1. Check configuration and status
wog-dump info

# 2. Download weapon catalog
wog-dump download-weapons

# 3. Download and decrypt assets
wog-dump download-assets --update-keys
wog-dump decrypt-assets

# 4. Extract models and textures
wog-dump unpack-assets

# 5. Convert normal maps (optional)
wog-dump convert-normals ./unpacked --recursive
```

### Command Line Interface

WOG Dump v2.3 provides a comprehensive CLI with multiple commands:

```bash
# Get help for any command
wog-dump --help
wog-dump <command> --help

# View configuration and system status
wog-dump info

# Complete pipeline with all options
wog-dump full-pipeline --verbose --max-threads 8 --update-keys --convert-normals
```

> 📝 **Pro Tip**: Use `wog-dump info` to check your system configuration and available storage space before starting large operations.

### Individual Commands

**Download Management:**
```bash
# Download weapon list from servers
wog-dump download-weapons

# Download assets with key updates
wog-dump download-assets --update-keys

# Check for updates without downloading
wog-dump download-assets --check-only

# Download specific weapons only
wog-dump download-assets --weapons "ak74,m4a1,glock17"
```

**Processing Operations:**
```bash
# Decrypt all downloaded assets
wog-dump decrypt-assets

# Decrypt specific weapons
wog-dump decrypt-assets --weapons "ak74,m4a1"

# Unpack Unity assets to models/textures
wog-dump unpack-assets

# Convert normal maps with backup
wog-dump convert-normals ./textures --recursive --backup
```

**Configuration and Monitoring:**
```bash
# Show detailed configuration
wog-dump info --verbose

# Enable debug logging for troubleshooting
wog-dump --debug full-pipeline

# Use custom thread count for parallel processing
wog-dump --max-threads 16 download-assets
```

### Advanced Workflows

**Selective Processing:**
```bash
# Process only specific weapon categories
wog-dump download-assets --weapons "ak74,ak47,akm"  # AK variants
wog-dump download-assets --weapons "m4a1,m16a4"     # M4/M16 variants

# Resume interrupted downloads
wog-dump download-assets --resume

# Force re-download of existing files
wog-dump download-assets --force
```

**Performance Optimization:**
```bash
# High-performance setup for powerful machines
wog-dump --max-threads 32 full-pipeline

# Conservative setup for limited resources
wog-dump --max-threads 2 --verbose full-pipeline

# Memory-efficient processing for large batches
wog-dump full-pipeline --chunk-size 4096
```

**Development and Testing:**
```bash
# Dry run to see what would be processed
wog-dump download-assets --dry-run

# Validate downloaded files without processing
wog-dump decrypt-assets --validate-only

# Export configuration for reproducible builds
wog-dump info --export-config > wog_config.json
```

### Using as Python Library

```python
from wog_dump.core.download import DownloadManager
from wog_dump.core.decrypt import KeyManager, AssetDecryptor
from wog_dump.core.config import get_config

# Get configuration
config = get_config()

# Download assets
with DownloadManager(config) as downloader:
    successful, failed = downloader.download_assets(["ak74", "m4a1"])

# Decrypt assets
key_manager = KeyManager(config)
keys = key_manager.load_keys()

decryptor = AssetDecryptor(config)
decrypted_files, failed_assets = decryptor.decrypt_all_assets(keys)
```

## 📁 Project Structure

```
ReWOG/
├── src/wog_dump/                    # Main package (source layout)
│   ├── __init__.py                  # Package initialization
│   ├── core/                        # Core business logic
│   │   ├── __init__.py             
│   │   ├── config.py                # Pydantic configuration management
│   │   ├── download.py              # Multi-threaded asset downloading
│   │   ├── decrypt.py               # XOR decryption (pure Python)
│   │   └── unpack.py                # Unity asset extraction
│   ├── utils/                       # Utility modules
│   │   ├── __init__.py             
│   │   ├── logging.py               # Rich-formatted logging
│   │   └── normal_map.py            # Normal map format conversion
│   └── cli/                         # Command-line interface
│       ├── __init__.py             
│       └── main.py                  # Click-based CLI commands
├── tests/                           # Comprehensive test suite
│   ├── __init__.py                 
│   ├── conftest.py                  # Pytest configuration and fixtures
│   ├── unit/                        # Unit tests for individual modules
│   │   ├── test_config.py          
│   │   ├── test_download.py        
│   │   └── test_normal_map.py      
│   └── integration/                 # Integration and end-to-end tests
│       └── test_cli.py             
├── docs/                            # Additional documentation
├── CHANGELOG.md                     # Version history and migration guides
├── CONTRIBUTING.md                  # Development and contribution guidelines
├── README.md                        # This file (main documentation)
└── pyproject.toml                   # Modern Python packaging (PEP 621)
```

## 🔧 Configuration

WOG Dump v2.0 uses Pydantic for robust configuration management:

```python
# Configuration is automatically managed
# Default directories: assets/, encrypted/, decrypted/
# Customizable via CLI options or environment variables

# View current configuration
wog-dump info
```

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=wog_dump

# Run specific test categories
pytest tests/unit/          # Unit tests only
pytest tests/integration/   # Integration tests only
pytest -m slow              # Slow tests
```

## 🔨 Development

```bash
# Install development dependencies with uv
uv pip install -e ".[dev]"

# Code formatting
black src/ tests/
isort src/ tests/

# Type checking
mypy src/

# Linting
flake8 src/ tests/

# Run all checks
uv run pre-commit run --all-files
```


## 🤝 Contributing

We welcome contributions from the community! Whether you're fixing bugs, adding features, or improving documentation, your help makes WOG Dump better for everyone.

### Quick Links
- 📜 [Contributing Guidelines](CONTRIBUTING.md) - Detailed contribution process
- 🐛 [Issue Tracker](https://github.com/InzGIBA/ReWOG/issues) - Report bugs or request features
- 💬 [Discussions](https://github.com/InzGIBA/ReWOG/discussions) - Ask questions and get help
- 🛡️ [Code of Conduct](CODE_OF_CONDUCT.md) - Community standards
- 🆘 [Support](SUPPORT.md) - Get help and troubleshooting

### Quick Start for Contributors

1. **Fork & Clone**
   ```bash
   git clone https://github.com/your-username/ReWOG
   cd ReWOG
   ```

2. **Setup Development Environment**
   ```bash
   uv pip install -e ".[dev]"
   pre-commit install
   ```

3. **Create Feature Branch**
   ```bash
   git checkout -b feature/amazing-feature
   ```

4. **Make Changes & Test**
   ```bash
   # Make your changes
   pytest  # Run tests
   black src/ tests/  # Format code
   ```

5. **Submit Pull Request**
   ```bash
   git commit -m 'feat: add amazing feature'
   git push origin feature/amazing-feature
   # Then create PR on GitHub
   ```

### Types of Contributions Welcome

- 🐛 **Bug Reports**: Help us identify and fix issues
- ✨ **Feature Requests**: Suggest new functionality
- 📄 **Documentation**: Improve guides, examples, and help text
- 🧪 **Testing**: Add test coverage or improve existing tests
- 🚀 **Performance**: Optimize code for speed or memory usage
- 🌍 **Localization**: Add support for other languages
- 📦 **Packaging**: Improve installation and distribution

### Recognition

All contributors are recognized in our [CHANGELOG.md](CHANGELOG.md) and commit history. Significant contributors may be invited to join the core team.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🫂 Special Thanks

- [DeadZoneGarry](https://github.com/DeadZoneLuna) - Original decryption research
- [Noble Empire Corp.](https://noble-empire.com/news.php) - Game and assets
- Contributors to the original WOG Dump project

## 📋 Frequently Asked Questions (FAQ)

### General Questions

**Q: What is WOG Dump and what does it do?**
A: WOG Dump extracts 3D gun models, textures, and materials from the World of Guns: Gun Disassembly game for educational, research, and personal use purposes.

**Q: Do I need to own the game to use this tool?**
A: The tool downloads public asset files from the game's servers. While game ownership isn't technically required for the tool to function, we recommend owning the game to support the developers.

**Q: What file formats does it extract?**
A: The tool extracts Unity3D assets including:
- 3D Models (FBX format via AssetStudio)
- Textures (PNG, TGA formats)
- Materials and shaders
- Normal maps (converted to standard format)

### Technical Questions

**Q: Why does it require Python 3.12+?**
A: We use modern Python features like native type annotations (`list[str]` instead of `List[str]`) and improved error handling that are only available in Python 3.12+.

**Q: Can I use this on Windows/macOS/Linux?**
A: Yes! The tool is pure Python and works on all major operating systems. The v2 release removed all C dependencies for better cross-platform compatibility.

**Q: How much disk space do I need?**
A: Plan for:
- Encrypted assets: ~2-5 GB
- Decrypted assets: ~2-5 GB  
- Extracted models/textures: ~3-8 GB
- Total recommended: **10-20 GB free space**

**Q: Is internet connection required?**
A: Yes, for downloading assets from game servers. After initial download, you can process files offline.

### Usage Questions

**Q: How long does the complete extraction take?**
A: Depends on your system and internet speed:
- Fast system (SSD, 8+ cores, 50+ Mbps): ~1-3 minutes
- Average system (HDD, 4 cores, 25 Mbps): ~5-10 minutes
- Slow system (older hardware, slow internet): ~15-30 minutes

**Q: Can I extract only specific weapons?**
A: Yes! Use the `--weapons` parameter:
```bash
wog-dump download-assets --weapons "ak74,m4a1,glock17"
```

**Q: What if the download fails or gets interrupted?**
A: The tool supports resuming interrupted downloads. Simply run the same command again and it will continue where it left off.

### Troubleshooting

**Q: I get "Permission denied" errors, what should I do?**
A: Ensure you have write permissions in the current directory, or specify a different output directory:
```bash
wog-dump --output-dir /path/to/writable/directory full-pipeline
```

**Q: The tool seems stuck or very slow, what's wrong?**
A: Try these steps:
1. Check your internet connection
2. Reduce thread count: `--max-threads 2`
3. Enable verbose logging: `--verbose`
4. Check available disk space

**Q: How do I report bugs or request features?**
A: Please create an issue on GitHub with:
- Your operating system and Python version
- Complete error message and traceback
- Steps to reproduce the problem
- Output of `wog-dump info --verbose`

## ⚠️ Legal Disclaimer

This tool is intended for **educational and research purposes only**. Users are responsible for:

- **Respecting Copyright**: All extracted assets remain the intellectual property of Noble Empire Corp.
- **Terms of Service**: Ensure compliance with the game's Terms of Service
- **Personal Use**: Use extracted content only for personal, educational, or research purposes
- **No Redistribution**: Do not redistribute game assets or use them commercially

**The authors and contributors are not responsible for any misuse of this software or violation of terms of service.**

For questions about asset usage rights, please contact Noble Empire Corp. directly.
