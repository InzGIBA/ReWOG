# Contributing to WOG Dump

Thank you for your interest in contributing to WOG Dump! This document provides guidelines for contributing to the project.

## 🚀 Getting Started

### Development Setup

1. **Fork and Clone**
   ```bash
   git clone https://github.com/your-username/WOG-dump.git
   cd WOG-dump
   ```

2. **Set up Development Environment**
   ```bash
   # Install uv if you haven't already
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Create virtual environment with uv
   uv venv
   source .venv/bin/activate  # Linux/macOS
   # or
   .venv\Scripts\activate     # Windows

   # Install in development mode
   uv pip install -e ".[dev]"
   ```

3. **Install Pre-commit Hooks**
   ```bash
   pre-commit install
   ```

## 🏗️ Development Workflow

### Making Changes

1. **Create a Feature Branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make Your Changes**
   - Follow the existing code style
   - Add tests for new functionality
   - Update documentation if needed

3. **Run Tests**
   ```bash
   # Run all tests
   pytest

   # Run with coverage
   pytest --cov=wog_dump

   # Run specific test categories
   pytest tests/unit/
   pytest tests/integration/
   ```

4. **Format Code**
   ```bash
   # Format with black
   black src/ tests/

   # Sort imports
   isort src/ tests/

   # Check typing
   mypy src/

   # Lint code
   flake8 src/ tests/
   ```

### Code Style

- **Python Version**: Python 3.12+
- **Formatter**: Black (line length 88)
- **Import Sorting**: isort
- **Type Hints**: Required for all public functions
- **Docstrings**: Google style docstrings

### Commit Messages

Follow conventional commit format:
```
type(scope): description

Examples:
feat(cli): add new command for batch processing
fix(decrypt): handle empty key files gracefully
docs(readme): update installation instructions
test(download): add tests for parallel downloads
```

## 🧪 Testing Guidelines

### Test Structure

- **Unit Tests**: `tests/unit/` - Test individual components
- **Integration Tests**: `tests/integration/` - Test component interactions
- **Fixtures**: `tests/fixtures/` - Shared test data

### Writing Tests

1. **Test File Naming**: `test_module_name.py`
2. **Test Function Naming**: `test_function_purpose`
3. **Test Class Naming**: `TestClassName`

Example:
```python
class TestDownloadManager:
    def test_download_success(self, test_config):
        """Test successful asset download."""
        # Test implementation
        pass
```

### Test Coverage

- Aim for >90% test coverage
- All new features must include tests
- Bug fixes should include regression tests

## 📝 Documentation

### Code Documentation

- All public functions need docstrings
- Use Google style docstrings
- Include type hints for all parameters and returns

Example:
```python
def download_asset(self, asset_name: str, force: bool = False) -> bool:
    """Download a single asset from the server.
    
    Args:
        asset_name: Name of the asset to download
        force: Force download even if file exists
        
    Returns:
        True if download was successful, False otherwise
        
    Raises:
        DownloadError: If download fails due to network issues
    """
```

### User Documentation

- Update README.md for user-facing changes
- Add examples for new CLI commands
- Document configuration options

## 🐛 Bug Reports

When reporting bugs, please include:

1. **Environment Information**
   - Python version
   - Operating system
   - WOG Dump version

2. **Steps to Reproduce**
   - Exact commands run
   - Expected behavior
   - Actual behavior

3. **Error Messages**
   - Full error traceback
   - Log files if available

4. **Additional Context**
   - Screenshots if applicable
   - Related issues

## 💡 Feature Requests

For new features:

1. **Check Existing Issues** - Avoid duplicates
2. **Describe the Problem** - What need does this address?
3. **Propose a Solution** - How should it work?
4. **Consider Alternatives** - What other approaches exist?

## 🔧 Code Architecture

### Project Structure

```
src/wog_dump/
├── core/           # Core business logic
│   ├── config.py   # Configuration management
│   ├── download.py # Asset downloading
│   ├── decrypt.py  # Decryption operations
│   └── unpack.py   # Asset unpacking
├── utils/          # Utility functions
│   ├── logging.py  # Structured logging
│   └── normal_map.py # Normal map conversion
└── cli/            # Command-line interface
    └── main.py     # CLI commands
```

### Design Principles

1. **Separation of Concerns** - Each module has a single responsibility
2. **Dependency Injection** - Pass dependencies explicitly
3. **Error Handling** - Graceful error recovery with detailed messages
4. **Type Safety** - Use type hints throughout
5. **Testability** - Design for easy testing

### Adding New Features

1. **Core Logic** - Add business logic to appropriate `core/` module
2. **CLI Interface** - Add CLI commands in `cli/main.py`
3. **Configuration** - Add config options to `core/config.py`
4. **Tests** - Add comprehensive tests
5. **Documentation** - Update docs and examples

## 📋 Pull Request Process

1. **Ensure CI Passes**
   - All tests pass
   - Code style checks pass
   - Type checking passes

2. **Update Documentation**
   - README if user-facing changes
   - Docstrings for new functions
   - CHANGELOG.md entry

3. **Review Checklist**
   - Code follows project conventions
   - Tests cover new functionality
   - No breaking changes (or properly documented)
   - Performance considerations addressed

4. **Get Reviews**
   - Request review from maintainers
   - Address feedback promptly
   - Be open to suggestions

## 🏷️ Release Process

For maintainers:

1. **Version Bump**
   - Update version in `pyproject.toml`
   - Update version in `src/wog_dump/__init__.py`

2. **Update Changelog**
   - Add release notes
   - List all changes since last release

3. **Create Release**
   - Tag release: `git tag v2.1.0`
   - Push tags: `git push --tags`
   - Create GitHub release

## 📞 Getting Help

- **Discussions**: Use GitHub Discussions for questions
- **Issues**: Create issues for bugs and feature requests
- **Email**: Contact maintainers for sensitive matters

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

Thank you for contributing to WOG Dump! 🎉