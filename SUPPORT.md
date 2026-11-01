# Support

We want to help you get the most out of WOG Dump! This document provides guidance on how to get support for any issues you might encounter.

## Before You Ask for Help

Please try these steps first:

1. **Check the README** - the [`README.md`](README.md) contains comprehensive installation and usage instructions
2. **Read the FAQ and Troubleshooting sections** - common issues and solutions are documented in the README
3. **Search Existing Issues** - check if your question or issue has already been reported in our [GitHub Issues](https://github.com/InzGIBA/ReWOG/issues)
4. **Read the docs** - [configuration](docs/configuration.md), [architecture](docs/architecture.md), [database](docs/database.md) and [MITM capture](docs/mitm-capture.md)

## Reporting Issues

When reporting a bug or issue, please include:

### System Information
- **Operating System**: (e.g., Windows 10, Ubuntu 22.04, macOS 13.0)
- **Python Version**: Run `python --version` or `python3 --version`
- **WOG Dump Version**: Run `wog-dump --version`
- **Installation Method**: (pip, uv, git clone)

### Detailed Description
- **What you were trying to do**: clear description of your goal
- **What you expected to happen**: expected behavior
- **What actually happened**: actual behavior with error messages
- **Steps to reproduce**: exact commands and actions that led to the issue

### Error Information
- **Full error message**: copy the complete error output
- **Log file**: attach `wog.log` (run `wog-dump info` to see its location; it always captures INFO and above, including full tracebacks)
- **Screenshots**: if applicable, especially for CLI output

### Example Issue Template
```markdown
**Environment:**
- OS: Ubuntu 22.04
- Python: 3.12.1
- WOG Dump: 2.5.0

**Description:**
When running `wog-dump download-assets`, the tool crashes with a connection error.

**Expected Behavior:**
Assets should download successfully.

**Actual Behavior:**
```
Error: Connection timeout after 30 seconds
Traceback (most recent call last):
  ...
```

**Steps to Reproduce:**
1. Run `wog-dump download-weapons`
2. Run `wog-dump download-assets`
3. Error occurs

**Additional Context:**
- Internet connection is stable
- First time using the tool
```

## Getting Help

### GitHub Issues (Recommended)
- **Bug Reports**: [create a new issue](https://github.com/InzGIBA/ReWOG/issues/new)
- **Feature Requests**: [create a new issue](https://github.com/InzGIBA/ReWOG/issues/new) with the "enhancement" label
- **Questions**: [browse existing issues](https://github.com/InzGIBA/ReWOG/issues) or create a new one

### Community Support
- **Search First**: use GitHub's search to find similar issues
- **Be Specific**: provide detailed information to help others help you
- **Be Patient**: this is an open-source project maintained by volunteers
- **Keep it Public**: prefer issues and discussions over private messages, so answers stay useful to everyone. Private email is reserved for security reports and [Code of Conduct](CODE_OF_CONDUCT.md) violations.

## Frequently Asked Questions

### Installation Issues

**Q: I get "command not found" when running `wog-dump`**
A: Make sure you've installed the package and it's in your PATH. Try:
- `pip install -e .` or `uv pip install -e .`
- Check if `~/.local/bin` is in your PATH (Linux/macOS)

**Q: Import errors about missing modules**
A: Reinstall the package with all its dependencies (there is no `requirements.txt`; dependencies are declared in `pyproject.toml`):
```bash
uv pip install -e .
```

### Runtime Issues

**Q: Downloads are very slow or timing out**
A: Try these solutions:
- Use fewer threads: `wog-dump --max-threads 2 download-assets`
- Check your internet connection
- Try downloading specific weapons instead of all at once
- Note that downloads are rate-limited by design; see `WOG_REQUEST_DELAY` in [docs/configuration.md](docs/configuration.md)

**Q: Decryption fails with "No decryption keys found"**
A: Fetch the keys for the weapons you need (authentication must be configured first):
```bash
wog-dump download-assets --update-keys
```

**Q: Key fetching fails with "Server error" or `result=1000`**
A: Usually an authentication problem. Run `wog-dump login` with your game account, or capture a session and put `WOG_AUTH_ID`/`WOG_AUTH_SESSION` into `.env`. Details: [docs/mitm-capture.md](docs/mitm-capture.md) and the Troubleshooting section of the README.

**Q: UnityPy errors during unpacking**
A: Make sure you have the latest version:
```bash
pip install --upgrade UnityPy
```

### Development Questions

**Q: How can I contribute to the project?**
A: See our [`CONTRIBUTING.md`](CONTRIBUTING.md) guide for detailed instructions.

**Q: Can I use this in my own project?**
A: Yes! WOG Dump is MIT licensed. See [`LICENSE`](LICENSE) for details.

## Self-Help Resources

### Diagnostic Commands
```bash
# Check configuration and system status
wog-dump info --verbose

# Inspect the local database: counts, account, session age
wog-dump cache

# Test with debug logging
wog-dump --debug info

# Verify installation
python -c "import wog_dump; print('Installation OK')"
```

### Log Files

WOG Dump writes a single rotating log file, `wog.log` (5 MB x 3 backups), located next to the per-user database in the OS user data directory (for example `~/.local/share/wog_dump/logs/wog.log` on Linux). The file always captures INFO and above, including full tracebacks of unexpected errors. Override the directory with `WOG_LOG_DIR` and include debug output with `WOG_LOG_LEVEL=DEBUG` (see [docs/configuration.md](docs/configuration.md)). `wog-dump info` shows the exact path in use.

### Common Solutions

**Reset the cached data (weapon list and keys; account is kept):**
```bash
wog-dump cache --clear
```

**Remove stored credentials and session:**
```bash
wog-dump logout
```

**Force a fresh download of a stuck asset:**
```bash
rm runtime/assets/<weapon>.unity3d.tmp   # delete the partial file, then re-run the command
```

**Reinstall dependencies:**
```bash
pip uninstall wog-dump
uv pip install -e .
```

## Contact Information

- **Project Maintainer**: InzGIBA
- **Email**: [inzgiba@gmail.com](mailto:inzgiba@gmail.com)
- **GitHub**: [@inzgiba](https://github.com/inzgiba)

## Response Expectations

- **Bug Reports**: we aim to respond within 48 hours
- **Feature Requests**: we'll evaluate and respond within a week
- **Security Issues**: please email directly for a faster response

## How to Help Others

If you're an experienced user, you can help the community by:
- Answering questions in GitHub Issues
- Improving documentation
- Contributing code fixes
- Testing new features

Remember, every contribution helps make WOG Dump better for everyone!

---

**Note**: For security-related issues, please email [inzgiba@gmail.com](mailto:inzgiba@gmail.com) directly instead of creating a public issue. The same address receives [Code of Conduct](CODE_OF_CONDUCT.md) reports.
