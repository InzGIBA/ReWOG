# 🔫 WOG Dump v2.0 - Modern Python Edition

![World of Guns](https://cdn.cloudflare.steamstatic.com/steam/apps/262410/library_hero.jpg)

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-black.svg)](https://github.com/psf/black)

## 📙 Description

WOG Dump is a modern, completely refactored Python tool for extracting 3D gun models from [World of Guns: Gun Disassembly](https://store.steampowered.com/app/262410/World_of_Guns_Gun_Disassembly/). This v2.0 release features a complete rewrite with modern Python 3.12+ features, improved error handling, comprehensive testing, and a beautiful CLI interface.

### ✨ What's New in v2.0

- 🐍 **Modern Python 3.12+** - Fully refactored with latest Python features
- 🏗️ **Professional Structure** - Proper package layout with `src/` structure
- 🎨 **Beautiful CLI** - Rich console interface with progress bars and colors
- ⚡ **Async Support** - Faster downloads with improved parallel processing
- 🔧 **Type Safety** - Complete type hints and Pydantic configuration
- 🧪 **Comprehensive Tests** - Full test suite with pytest
- 📦 **Modern Packaging** - pyproject.toml and pip-installable
- 🛡️ **Better Error Handling** - Graceful error recovery and detailed logging
- 🔄 **Modular Design** - Clean separation of concerns

## 🔗 Requirements

- **Python 3.12+** (Required)
- [AssetStudio](https://github.com/Perfare/AssetStudio) - for final asset unpacking
- C compiler - for compiling XOR decrypter (optional, Python fallback available)

## 🚀 Installation

### Quick Install with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is the modern, fast Python package installer and resolver.

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh
# or with pip: pip install uv

# Clone the repository
git clone https://github.com/hampta/WOG-dump
cd WOG-dump

# Install in development mode with uv
uv pip install -e .
```

### Alternative Installation Methods

**Traditional pip:**
```bash
# Clone repository
git clone https://github.com/hampta/WOG-dump
cd WOG-dump

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

### Command Line Interface

WOG Dump v2.0 provides a modern CLI with multiple commands:

```bash
# Show help
wog-dump --help

# View configuration and status
wog-dump info

# Run complete pipeline
wog-dump full-pipeline

# Individual steps
wog-dump download-weapons
wog-dump download-assets --update-keys
wog-dump decrypt-assets
wog-dump unpack-assets
wog-dump convert-normals ./unpacked --recursive
```

### Quick Start

```bash
# Complete extraction pipeline
wog-dump full-pipeline --update-keys --convert-normals

# This will:
# 1. Download weapon list
# 2. Update decryption keys  
# 3. Download all assets
# 4. Decrypt downloaded files
# 5. Unpack Unity assets
# 6. Convert normal maps
```

### Advanced Usage

```bash
# Custom configuration
wog-dump --verbose --max-threads 8 full-pipeline

# Process specific weapons only
wog-dump download-assets --weapons "ak74,m4a1,glock17"
wog-dump decrypt-assets --weapons "ak74,m4a1,glock17"

# Check for updates without downloading
wog-dump download-assets --check-only

# Convert normal maps only
wog-dump convert-normals ./extracted_textures --recursive --backup
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
WOG-dump/
├── src/wog_dump/           # Main package
│   ├── core/               # Core functionality
│   │   ├── config.py       # Configuration management
│   │   ├── download.py     # Asset downloading
│   │   ├── decrypt.py      # Decryption operations
│   │   └── unpack.py       # Asset unpacking
│   ├── utils/              # Utility modules
│   │   ├── logging.py      # Structured logging
│   │   └── normal_map.py   # Normal map conversion
│   └── cli/                # Command-line interface
│       └── main.py         # CLI commands
├── tests/                  # Test suite
│   ├── unit/               # Unit tests
│   ├── integration/        # Integration tests
│   └── fixtures/           # Test fixtures
├── bin/                    # Compiled binaries
├── docs/                   # Documentation
├── pyproject.toml          # Modern Python packaging
└── README.md               # This file
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

## ➕ Additional Tools

### XOR Decrypter Binary
```bash
# Compile XOR decrypter (optional, for faster decryption)
gcc xor.c -o bin/linux/64bit/xor          # Linux
gcc xor.c -o bin/windows/64bit/xor.exe    # Windows

# Usage
./bin/linux/64bit/xor <encrypted_file> <key> <output_file>
```

### Normal Map Converter
```bash
# Convert Unity normal maps to standard format
wog-convert-normals <path> --recursive --backup

# Validate normal maps
wog-convert-normals <path> --validate
```

## 📊 Performance

- **Parallel Processing**: Multi-threaded downloads and processing
- **Memory Efficient**: Streaming downloads and processing
- **Fast Decryption**: Optional compiled XOR decrypter
- **Progress Tracking**: Real-time progress bars and status

## 🐛 Troubleshooting

### Common Issues

1. **Python Version**: Ensure you're using Python 3.12+
2. **Missing Dependencies**: Run `pip install -e .` to install all dependencies
3. **Network Issues**: Use `--verbose` flag to see detailed error messages
4. **Permission Errors**: Ensure write permissions in the working directory

### Debug Mode
```bash
# Enable debug logging
wog-dump --debug full-pipeline

# Check configuration
wog-dump info
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add tests for new functionality
5. Run the test suite (`pytest`)
6. Format code (`black src/ tests/`)
7. Commit changes (`git commit -m 'Add amazing feature'`)
8. Push to branch (`git push origin feature/amazing-feature`)
9. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🫂 Special Thanks

- [DeadZoneGarry](https://github.com/DeadZoneLuna) - Original decryption research
- [Noble Empire Corp.](https://noble-empire.com/news.php) - Game and assets
- Contributors to the original WOG Dump project

## ⚠️ Disclaimer

This tool is for educational and research purposes only. Please respect the game's Terms of Service and copyright. The authors are not responsible for any misuse of this software.
