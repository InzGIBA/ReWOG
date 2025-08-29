# 🛠️ Support

We want to help you get the most out of WOG Dump! This document provides guidance on how to get support for any issues you might encounter.

## 📋 Before You Ask for Help

Please try these steps first:

1. **Check the README** - The [`README.md`](README.md) contains comprehensive installation and usage instructions
2. **Search Existing Issues** - Check if your question or issue has already been reported in our [GitHub Issues](https://github.com/InzGIBA/ReWOG/issues)
3. **Read the Documentation** - Browse through our [project documentation](https://github.com/InzGIBA/ReWOG/blob/main/README.md)
4. **Check the Troubleshooting Section** - Common issues and solutions are documented in the README

## 🐛 Reporting Issues

When reporting a bug or issue, please include:

### System Information
- **Operating System**: (e.g., Windows 10, Ubuntu 22.04, macOS 13.0)
- **Python Version**: Run `python --version` or `python3 --version`
- **WOG Dump Version**: Run `wog-dump --version`
- **Installation Method**: (pip, uv, git clone)

### Detailed Description
- **What you were trying to do**: Clear description of your goal
- **What you expected to happen**: Expected behavior
- **What actually happened**: Actual behavior with error messages
- **Steps to reproduce**: Exact commands and actions that led to the issue

### Error Information
- **Full error message**: Copy the complete error output
- **Log files**: Include relevant log files from the `logs/` directory
- **Screenshots**: If applicable, especially for CLI output

### Example Issue Template
```markdown
**Environment:**
- OS: Ubuntu 22.04
- Python: 3.12.1
- WOG Dump: 2.3.2

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

## 💬 Getting Help

### GitHub Issues (Recommended)
- **Bug Reports**: [Create a new issue](https://github.com/InzGIBA/ReWOG/issues/new)
- **Feature Requests**: [Create a new issue](https://github.com/InzGIBA/ReWOG/issues/new) with the "enhancement" label
- **Questions**: [Browse existing discussions](https://github.com/InzGIBA/ReWOG/issues) or create a new issue

### Community Support
- **Search First**: Use GitHub's search to find similar issues
- **Be Specific**: Provide detailed information to help others help you
- **Be Patient**: This is an open-source project maintained by volunteers

## ❓ Frequently Asked Questions

### Installation Issues

**Q: I get "command not found" when running `wog-dump`**
A: Make sure you've installed the package and it's in your PATH. Try:
- `pip install -e .` or `uv pip install -e .`
- Check if `~/.local/bin` is in your PATH (Linux/macOS)

**Q: Import errors about missing modules**
A: Install all dependencies:
```bash
pip install -r requirements.txt  # or uv pip install -e .
```

### Runtime Issues

**Q: Downloads are very slow or timing out**
A: Try these solutions:
- Use fewer threads: `wog-dump --max-threads 2 download-assets`
- Check your internet connection
- Try downloading specific weapons instead of all at once

**Q: Decryption fails with "Invalid key" error**
A: Update your keys:
```bash
wog-dump download-assets --update-keys
```

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

## 🔧 Self-Help Resources

### Diagnostic Commands
```bash
# Check configuration and system status
wog-dump info --verbose

# Test with debug logging
wog-dump --debug info

# Verify installation
python -c "import wog_dump; print('Installation OK')"
```

### Log Files
WOG Dump creates log files in the `logs/` directory:
- `wog_dump.log` - General application logs
- `debug.log` - Detailed debug information (when using `--debug`)

### Common Solutions

**Clear cache and restart:**
```bash
rm -rf runtime/cache/  # Linux/macOS
rmdir /s runtime\cache  # Windows
```

**Reinstall dependencies:**
```bash
pip uninstall wog-dump
pip install -e .
```

**Reset configuration:**
```bash
rm -f config.json  # Will regenerate with defaults
```

## 📞 Contact Information

- **Project Maintainer**: InzGIBA
- **Email**: [inzgiba@gmail.com](mailto:inzgiba@gmail.com)
- **GitHub**: [@inzgiba](https://github.com/inzgiba)

## 🎯 Response Expectations

- **Bug Reports**: We aim to respond within 48 hours
- **Feature Requests**: We'll evaluate and respond within a week
- **Security Issues**: Please email directly for faster response

## 🤝 How to Help Others

If you're an experienced user, you can help the community by:
- Answering questions in GitHub Issues
- Improving documentation
- Contributing code fixes
- Testing new features

Remember, every contribution helps make WOG Dump better for everyone!

---

**Note**: For security-related issues, please email [inzgiba@gmail.com](mailto:inzgiba@gmail.com) directly instead of creating a public issue.
