"""Rewrite README.md into a PyPI-safe long description.

PyPI sanitizes the long description: <picture> and <source> tags are
stripped, SVG images are blocked, and relative paths do not resolve
outside GitHub. This script produces a description that renders there:

- the hero <picture> block becomes one <img> pointing at the committed
  social-preview PNG (absolute raw.githubusercontent URL);
- the feature-card <picture> blocks are dropped, the bullet list below
  carries the content;
- relative links (docs/, blender-addon/, assets/, LICENSE, .env.example,
  CHANGELOG.md) become absolute github.com blob URLs; in-page anchors
  stay, PyPI generates its own heading slugs.

Usage: python3 scripts/build_pypi_readme.py [IN] [OUT] (default in and
out: README.md). Idempotent: a second run on transformed content is a
no-op. The release workflow runs the script before the build, so the
wheel metadata carries the PyPI-safe text while the repository README
keeps the GitHub-only rendering.
"""

import re
import sys

REPO = "https://github.com/InzGIBA/ReWOG"
RAW = "https://raw.githubusercontent.com/InzGIBA/ReWOG/main"

HERO_IMG = (
    '<img src="' + RAW + '/assets/social-preview.png" '
    'alt="WOG Dump: World of Guns asset extraction toolkit" width="100%">'
)

_REL_LINKS = (
    "docs/",
    "blender-addon/",
    "examples/",
    "scripts/",
    "assets/",
    ".env.example",
    "CHANGELOG.md",
    "LICENSE",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SUPPORT.md",
)


def transform(text):
    def picture(match):
        if "hero-dark.svg" in match.group(0):
            return HERO_IMG
        return ""  # feature cards: the bullet list below carries the content

    text = re.sub(r"<picture>.*?</picture>", picture, text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)

    for target in _REL_LINKS:
        pattern = r"\]\((" + re.escape(target) + r"[^)]*)\)"
        text = re.sub(
            pattern, lambda m: "](" + REPO + "/blob/main/" + m.group(1) + ")", text
        )
    return text


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "README.md"
    dst = sys.argv[2] if len(sys.argv) > 2 else src
    result = transform(open(src, encoding="utf-8").read())
    open(dst, "w", encoding="utf-8").write(result)
    leftovers = [
        m
        for m in re.findall(r"\]\(([^)]+)\)", result)
        if not m.startswith(("http", "mailto", "#", "/"))
    ]
    if "<picture>" in result or leftovers:
        print("PyPI-unsafe fragments remain:", sorted(set(leftovers)) or ["<picture>"])
        return 1
    print("wrote", dst, len(result), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
