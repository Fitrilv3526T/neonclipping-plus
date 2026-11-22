"""Caption fonts that look the same on every machine, including a bare Google Colab.

libass (which burns the captions) can only draw fonts fontconfig knows. A fresh Colab or a
minimal Linux box has none of the display fonts Windows and macOS ship, so captions fall back
to whatever is installed and look wrong. This module:

  1. looks for bundled OFL fonts in <repo>/fonts/ (Anton and Montserrat, both SIL OFL, free
     for any use - download once with `python fonts.py --download`),
  2. registers the folder with libass through `ass=...:fontsdir=...` so the fonts work even
     when fontconfig knows nothing about them,
  3. reports the family name to write into the ASS file.

Nothing is downloaded at runtime unless the user runs the downloader; the app works fine with
system fonts (Arial on Windows, DejaVu on Linux) when the folder is empty.
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / 'fonts'

# (family name for the ASS file, file name, source URL in the google/fonts GitHub repository)
BUNDLED = (
    ('Anton', 'Anton-Regular.ttf', 'https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf'),
    ('Montserrat', 'Montserrat-Bold.ttf', 'https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf'),
)
# A heavy display face works best for the neon/impact caption styles.
STYLE_FONT = {'gta': 'Anton', 'bold': 'Anton', 'hormozi': 'Montserrat', 'mrbeast': 'Montserrat', 'clean': 'Montserrat'}


def available():
    """{family: path} of the bundled font files that actually exist."""
    out = {}
    if not FONT_DIR.exists():
        return out
    for family, name, _ in BUNDLED:
        p = FONT_DIR / name
        if p.exists() and p.stat().st_size > 10_000:
            out[family] = p
    return out


def fontsdir():
    """The folder to pass to libass, or None when no bundled font is present."""
    return str(FONT_DIR) if available() else None


def family_for(style, custom=None):
    """The font family name to write into the ASS file. A user choice always wins; bundled
    fonts next; otherwise '' (libass picks any installed default)."""
    if custom:
        return custom
    have = available()
    want = STYLE_FONT.get(style)
    if want and want in have:
        return want
    return ''


def ensure_styles(ass_lines, style, custom_font=None):
    """Replace the font family in the 'Style: Main,...' ASS header line in place. Returns
    the same list, so callers can use it inline."""
    family = family_for(style, custom_font)
    if family:
        for i, line in enumerate(ass_lines):
            if line.startswith('Style: Main,'):
                ass_lines[i] = line.replace('Style: Main,', f'Style: Main,{family},', 1)
                break
    return ass_lines


def download():
    """Fetch the bundled OFL fonts once (also writes the licence note). Called by
    `python fonts.py --download`, the Colab setup and the self-test - never silently."""
    FONT_DIR.mkdir(exist_ok=True)
    (FONT_DIR / 'LICENCE.md').write_text(
        '# Bundled fonts\n\nAnton and Montserrat, (c) Google LLC, SIL Open Font License 1.1.\n'
        'Free for personal and commercial use; the licence text is at\n'
        'https://openfontlicense.org/open-font-license-official-text/\n', encoding='utf-8')
    ok = 0
    for family, name, url in BUNDLED:
        target = FONT_DIR / name
        if target.exists() and target.stat().st_size > 10_000:
            print(f'{family}: already bundled')
            ok += 1
            continue
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read(20_000_000)
            if len(data) < 10_000:
                raise ValueError('download too small')
            target.write_bytes(data)
            print(f'{family}: downloaded {len(data)//1024} KB')
            ok += 1
        except Exception as error:                      # noqa: BLE001 - offline is fine, report it
            print(f'{family}: NOT downloaded ({error}); captions use system fonts')
    return ok == len(BUNDLED)


def fontconfig_ok():
    """True when fontconfig can resolve at least one family (a Colab self-test check)."""
    try:
        r = subprocess.run(['fc-match', 'sans-serif'], capture_output=True, text=True, timeout=10)
        return bool((r.stdout or '').strip())
    except (OSError, subprocess.SubprocessError):
        return False


if __name__ == '__main__':
    if '--download' in sys.argv:
        sys.exit(0 if download() else 1)
    have = available()
    print('Bundled fonts:', ', '.join(have) or 'none (run: python fonts.py --download)')
