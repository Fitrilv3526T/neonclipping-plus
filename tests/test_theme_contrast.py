"""Both themes must meet WCAG AA contrast (4.5:1 body text, 3:1 UI) - measured, not guessed.

The palette lives in style.css as CSS custom properties; the values here mirror it. If you
change the palette, change both places and re-run this test.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip('#')
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def palette_of(theme: str) -> dict:
    """Parse the tokens of a :root / [data-theme=...] block out of style.css."""
    css = (ROOT / 'style.css').read_text(encoding='utf-8')
    block = re.search((r'\[data-theme="dark"\]\s*{(.*?)}') if theme == 'dark' else (r':root\s*{(.*?)}'), css, re.S)
    tokens = dict(re.findall(r'(--[\w-]+):\s*(#[0-9a-fA-F]{6})', block.group(1)))
    if theme == 'light':                      # dark-only tokens fall back to :root values
        dark_block = re.search(r'\[data-theme="dark"\]\s*{(.*?)}', css, re.S)
        tokens.update({k: v for k, v in re.findall(r'(--[\w-]+):\s*(#[0-9a-fA-F]{6})', dark_block.group(1)) if k not in tokens})
    return tokens


LIGHT = palette_of('light')
DARK = palette_of('dark')

CASES = [
    ('body text', '--text', '--surface', 4.5),
    ('body text on page', '--text', '--bg', 4.5),
    ('soft text', '--text-soft', '--surface', 4.5),
    ('muted text', '--text-muted', '--surface', 4.5),
    ('muted text on tinted surface', '--text-muted', '--surface-2', 4.5),
    ('faint hints', '--text-faint', '--surface', 4.5),
    ('accent text', '--accent', '--surface', 4.5),
    ('primary button label', '--on-accent', '--accent', 4.5),
    ('focus ring', '--focus', '--surface', 3.0),
    ('danger text', '--danger', '--surface', 4.5),
    ('success text', '--success', '--surface', 4.5),
    ('selected tab text', '--accent', '--accent-soft', 4.5),
]


class Contrast(unittest.TestCase):
    def test_light_theme_meets_aa(self):
        for name, fg, bg, need in CASES:
            with self.subTest(name):
                self.assertGreaterEqual(contrast(LIGHT[fg], LIGHT[bg]), need,
                                        f'light: {name} {LIGHT[fg]} on {LIGHT[bg]}')

    def test_dark_theme_meets_aa(self):
        for name, fg, bg, need in CASES:
            with self.subTest(name):
                self.assertGreaterEqual(contrast(DARK[fg], DARK[bg]), need,
                                        f'dark: {name} {DARK[fg]} on {DARK[bg]}')

    def test_palettes_are_actually_different(self):
        self.assertNotEqual(LIGHT['--bg'], DARK['--bg'])
        self.assertNotEqual(LIGHT['--accent'], DARK['--accent'])


if __name__ == '__main__':
    unittest.main()
