# Contributing to WOG Dump

Thank you for your interest in contributing to WOG Dump. Every contribution counts: a typo fix, a clearer docstring or a bug report helps the next user as much as a new feature. This guide explains how to contribute, what to expect, and where to start.

Before participating, please read our [Code of Conduct](CODE_OF_CONDUCT.md). The code applies to every community space of this project.

## Ways to Contribute

You do not have to write code to help:

- **Bug reports**: reproduce the problem and report the details (see [Bug Reports](#bug-reports)). This is how most issues get fixed.
- **Documentation**: improvements to the README, this guide, or the `docs/` pages. Documentation-only pull requests are welcome.
- **Testing**: new test cases, or running the live tests (`pytest -m live`) against the current game servers and reporting what changed.
- **Feature requests**: describe the problem you are trying to solve (see [Feature Requests](#feature-requests)).
- **Code**: fixes and features in `src/` (see the [Development Workflow](#development-workflow) below).

Look for issues labeled `good first issue` in the [issue tracker](https://github.com/InzGIBA/ReWOG/issues). Maintainers pick these issues as safe entry points for newcomers.

## Project Scope

To keep the project focused, contributions fall into two groups.

In scope:

- The download, decrypt, unpack and scene-export pipeline for World of Guns assets
- Keeping the tool compatible with the current game protocol
- Documentation, tests and packaging for the above

Out of scope (such contributions will be declined):

- Anything which helps redistribute the game's assets or violates their terms of use
- Support for other games or generic Unity extractors (use [AssetRipper](https://github.com/AssetRipper/AssetRipper) or [AssetStudio](https://github.com/Perfare/AssetStudio) for those)
- Automating gameplay or interacting with the game beyond the documented APIs

If you are unsure where an idea fits, open an issue and ask before writing code.

## Getting Started

### Development Setup

1. **Fork and Clone**
   ```bash
   git clone https://github.com/your-username/ReWOG.git
   cd ReWOG
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

## Development Workflow

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

   # Live tests against the real servers (auto-skipped without credentials)
   pytest -m live
   ```

4. **Format and Check Code**
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

## Testing Guidelines

### Test Structure

- **Unit Tests**: `tests/unit/` for individual components
- **Integration Tests**: `tests/integration/` for component interactions and the CLI end to end
- **Live Tests**: `tests/integration/test_live.py` (`pytest -m live`) exercise the real game servers. The tests skip automatically when no credentials are configured.

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

- Aim for >90% test coverage (the enforced floor is 75, see `[tool.coverage.report]` in `pyproject.toml`)
- All new features must include tests
- Bug fixes should include regression tests
- Mock at the `requests.Session` / `UnityPy.load` boundary. Never hit the network in unit tests

## Documentation

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
- Document configuration options in `docs/configuration.md`
- Add a CHANGELOG.md entry under `[Unreleased]`

## Bug Reports

When reporting bugs, please include:

1. **Environment Information**
   - Python version
   - Operating system
   - WOG Dump version (`wog-dump --version`)

2. **Steps to Reproduce**
   - Exact commands run
   - Expected behavior
   - Actual behavior

3. **Error Messages**
   - Full error traceback
   - Log file (`wog-dump info` shows the location)

4. **Additional Context**
   - Screenshots if applicable
   - Related issues

## Feature Requests

For new features:

1. **Check Existing Issues**: avoid duplicates
2. **Describe the Problem**: what need does the request address?
3. **Propose a Solution**: how should the feature work?
4. **Consider Alternatives**: what other approaches exist?

## Code Architecture

### Project Structure

```
src/wog_dump/
├── core/               # Domain logic, no Click imports
│   ├── config.py       # Pydantic configuration management
│   ├── http.py         # Shared HTTP session factory, rate limiting
│   ├── api.py          # BZ2-over-HTTP query API client
│   ├── auth.py         # Game-account login and identity
│   ├── database.py     # Local SQLite storage
│   ├── keys.py         # Key fetching and caching
│   ├── paths.py        # Name validation and path containment
│   ├── download.py     # Resumable parallel asset downloads
│   ├── decrypt.py      # XOR decryption
│   ├── unpack.py       # Unity asset extraction (extractor registry)
│   ├── scene.py        # Blender scene export
│   ├── glb.py          # glTF 2.0 / GLB writer
│   └── key_import.py   # MITM capture parsing
├── resources/
│   └── blender_setup.py  # Template for the generated Blender script
├── utils/              # Utilities
│   ├── logging.py      # Rich-formatted logging
│   └── normal_map.py   # Normal map conversion
└── cli/                # One module per command group
    ├── main.py         # wog-dump group, global options
    └── ...             # download, decrypt, unpack, convert, scene, pipeline, auth, cache, info
```

Dependency direction is strictly downwards: `cli` imports from `core`, `core` never imports from `cli`. See [docs/architecture.md](docs/architecture.md) for the full module map.

### Design Principles

1. **Separation of Concerns**: each module has a single responsibility
2. **Dependency Injection**: pass dependencies explicitly
3. **Error Handling**: graceful error recovery with domain exception hierarchies
4. **Type Safety**: type hints throughout, `mypy` strict-clean
5. **Testability**: design for easy testing
6. **No Secrets in Code**: credentials and keys live only in the per-user database

### Adding New Features

1. **Core Logic**: add business logic to the appropriate `core/` module
2. **CLI Interface**: add the command to the matching `cli/` module (create a new one for a new command group and register the module in `cli/main.py`)
3. **Configuration**: add config options to `core/config.py` with an environment-variable mapping
4. **Tests**: add comprehensive tests
5. **Documentation**: update docs and examples

### Code Quality Standards

**Complexity Guidelines:**
- Functions should be < 50 lines
- Classes should be < 300 lines
- Files should be < 500 lines
- Cyclomatic complexity < 10

**Performance Guidelines:**
- Use the shared rate limiter for every network request
- Use the thread pool for parallel I/O-bound work
- Use generators for large data processing

**Security Guidelines:**
- Validate all user inputs
- Use secure defaults for configuration
- Sanitize file paths and names
- Never log or commit credentials and keys

## Advanced Development

### Editor Setup

**VS Code (`.vscode/settings.json`):**
```json
{
    "python.defaultInterpreterPath": "./.venv/bin/python",
    "python.formatting.provider": "black",
    "python.testing.pytestEnabled": true,
    "python.testing.pytestArgs": ["tests"]
}
```

### Profiling

```bash
# CPU profile of a full run
python -m cProfile -o profile.stats -m wog_dump.cli.main full-pipeline
```

## Pull Request Process

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

## What to Expect

So nobody has to guess, here is how contributions are handled:

- **Response time**: bug reports get a first response within about 48 hours, feature requests within about a week. This is a volunteer-maintained project, and complex reviews take longer.
- **Review criteria**: PRs are reviewed for tests (new features need them), type safety (`mypy`), style (black/isort) and fit with the project scope above.
- **Issues before big PRs**: for changes beyond a small fix, open an issue first so the approach is agreed on before you spend time implementing.
- **Declined contributions**: if a PR does not fit the scope, we will explain why and link the relevant documentation. We still appreciate the interest.

## Release Process

For maintainers:

1. **Version Bump**
   - Update `__version__` in `src/wog_dump/__init__.py` (the single version source). `pyproject.toml` reads the value dynamically via hatchling.

2. **Update Changelog**
   - Move the `[Unreleased]` entries into a new version section with the date

3. **Create Release**
   - Tag release: `git tag v2.5.0`
   - Push tags: `git push --tags`
   - Create GitHub release

## Getting Help

- **Discussions**: use GitHub Discussions for questions
- **Issues**: create issues for bugs and feature requests
- **Email**: contact maintainers for sensitive matters, including Code of Conduct reports (see [SUPPORT.md](SUPPORT.md) for all channels)

## License

By contributing, you agree your contributions will be licensed under the MIT License.

Thank you for contributing to WOG Dump!
