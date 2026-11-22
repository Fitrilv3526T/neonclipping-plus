"""One place for every YouTube / yt-dlp call, so a "Sign in to confirm you're not a bot" block does not stop people.

What it does
1. Keeps yt-dlp fresh (YouTube changes often; an old yt-dlp is the most common reason downloads fail). Checked at most once a day.
2. Wraps yt-dlp in YDL, a drop-in for yt_dlp.YoutubeDL. When YouTube answers with the bot check (or a 403/429), it retries with
   other settings instead of failing: your own cookies (only if you set them up), then other YouTube player clients.
3. If everything is blocked it raises ONE short, readable message that says what to do (upload the MP4, use your browser login).

Optional cookies (never read unless you ask for them):
  NEONCLIPPING_COOKIES_BROWSER=chrome|edge|firefox|brave   use the login of that browser on this computer
  NEONCLIPPING_COOKIES_FILE=path\\to\\cookies.txt          a Netscape cookies.txt exported from your browser
  data/youtube_cookies.txt                                 same file, picked up automatically if it exists
  data/ytdl.json                                           {"cookies_browser": "chrome"} instead of environment variables
Set NEONCLIPPING_NO_AUTOUPDATE=1 to turn off the daily yt-dlp update.
"""
from pathlib import Path
import json, os, subprocess, sys, time

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('NEONCLIPPING_DATA_DIR', str(ROOT / 'data')))
UPDATE_EVERY = 24 * 3600

def update_ytdlp(force=False):
    """pip-upgrade yt-dlp (with its YouTube helper extras) at most once a day. Silent and offline-safe."""
    if os.environ.get('NEONCLIPPING_NO_AUTOUPDATE') == '1' and not force:
        return False
    stamp = DATA / '.ytdlp_checked'
    try:
        if not force and time.time() - stamp.stat().st_mtime < UPDATE_EVERY:
            return False
    except OSError:
        pass
    ok = False
    try:
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-U', 'yt-dlp[default]', '--quiet',
                            '--disable-pip-version-check', '--retries', '0', '--timeout', '8'],
                           capture_output=True, timeout=120)
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        stamp.write_text(time.strftime('%Y-%m-%d %H:%M:%S') + (' ok' if ok else ' failed'), encoding='utf-8')
    except OSError:
        pass
    return ok

update_ytdlp()          # must run before yt_dlp is imported so the new version is the one that loads
import yt_dlp

BLOCK_MARKERS = ('not a bot', 'sign in to confirm', 'cookies-from-browser', 'http error 403', 'http error 429', 'too many requests')

# (marker text, kind, plain-language explanation). Checked in order; the first hit wins.
FAIL_KINDS = (
    ('not a bot', 'bot check', 'YouTube showed its "confirm you are not a bot" check to this server.'),
    ('cookies-from-browser', 'bot check', 'YouTube showed its bot check to this server.'),
    ('http error 403', 'bot check', 'YouTube refused this server (HTTP 403, usually its bot check).'),
    ('http error 429', 'rate limited', 'This server asked YouTube too often and was paused (HTTP 429).'),
    ('too many requests', 'rate limited', 'This server asked YouTube too often and was paused.'),
    ('private', 'private', 'This video is private or members-only; only the owner (or members) can open it.'),
    ('sign in to confirm your age', 'age-gated', 'This video is age-restricted and needs a signed-in adult account.'),
    ('age-restricted', 'age-gated', 'This video is age-restricted and needs a signed-in adult account.'),
    ('inappropriate for some users', 'age-gated', 'This video is age-restricted and needs a signed-in adult account.'),
    ('is live', 'live', 'This stream is live right now; replays work once it has ended. Use the Live clipping panel while it is on.'),
    ('live event', 'live', 'This stream is live right now. Use the Live clipping panel while it is on.'),
    ('premieres', 'live', 'This is a premiere that has not started yet. Try again after it ends.'),
    ('not available in your country', 'geo-blocked', 'The uploader blocked this video in this server\'s country.'),
    ('video unavailable', 'unavailable', 'This video is unavailable (removed or region-locked).'),
    ('has been removed', 'removed', 'This video was removed.'),
    ('no longer available', 'removed', 'This video is no longer available.'),
    ('nsfw', 'age-gated', 'This video is age-restricted.'),
    ('requester', 'bot check', 'YouTube blocked this server.'),
)


def fail_kind(error):
    """A short kind label for a yt-dlp error ('bot check', 'private', ...), or None."""
    text = str(error).lower()
    for marker, kind, _ in FAIL_KINDS:
        if marker in text:
            return kind
    if any(m in text for m in BLOCK_MARKERS):
        return 'bot check'
    return None


def human_error(error):
    """A friendly one-line explanation with what to do next, for the most common failures."""
    text = str(error).lower()
    for marker, kind, message in FAIL_KINDS:
        if marker in text:
            if kind in ('bot check', 'rate limited'):
                return (message + ' Fix: upload a cookies.txt file (Settings - YouTube cookies), wait a few minutes, '
                        'try another network, or simply upload the MP4 file instead - that always works.')
            if kind in ('private', 'age-gated', 'removed', 'unavailable'):
                return message + ' Sign in to YouTube in your browser, export cookies.txt and upload it in Settings, or upload the MP4 file.'
            if kind == 'geo-blocked':
                return message + ' A VPN in an allowed country or the MP4 file are the way around it.'
            if kind == 'live':
                return message
    if any(m in text for m in BLOCK_MARKERS):
        return ('YouTube blocked this server (its bot check). Upload a cookies.txt file in Settings, wait, '
                'try another network, or upload the MP4 file instead - that always works.')
    return ''


def is_blocked(error):
    text = str(error).lower()
    return any(m in text for m in BLOCK_MARKERS)

def _config():
    saved = {}
    try:
        saved = json.loads((DATA / 'ytdl.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    browser = (os.environ.get('NEONCLIPPING_COOKIES_BROWSER') or saved.get('cookies_browser') or '').strip().lower()
    cookie_file = os.environ.get('NEONCLIPPING_COOKIES_FILE') or saved.get('cookies_file') or str(DATA / 'youtube_cookies.txt')
    return browser, cookie_file

def js_runtimes():
    """YouTube needs a JavaScript runtime to unlock every video quality. yt-dlp only looks for Deno by default; also allow Node."""
    import shutil
    found = {}
    for name in ('deno', 'node'):
        if shutil.which(name):
            found[name] = {}
    return found


def variants(base):
    """Option sets to try, best first. Each entry is (options, is_default)."""
    runtimes = js_runtimes()
    if runtimes and 'js_runtimes' not in base:
        base = {**base, 'js_runtimes': runtimes}
    browser, cookie_file = _config()
    out = []
    if Path(cookie_file).is_file():
        out.append(({**base, 'cookiefile': cookie_file}, False))
    if browser:
        out.append(({**base, 'cookiesfrombrowser': (browser,)}, False))
    out.append((dict(base), True))
    # Several player clients as fallback: Colab/datacenter IPs often get blocked on one
    # client but not another. tv + web_safari first (they carry the least bot checking).
    for clients in (['tv', 'web_safari'], ['ios'], ['mweb'], ['android'], ['web_embedded']):
        out.append(({**base, 'extractor_args': {'youtube': {'player_client': clients}}}, False))
    return out

GUIDE = ("YouTube blocked this download (its bot check). This happens on shared, mobile-data or VPN internet - and often on "
         "Google Colab, whose server IPs are blocked by YouTube. Fix, easiest first: "
         "1) Click Upload video and upload the MP4 instead (always works). "
         "2) Upload a cookies.txt exported from a browser signed in to YouTube (Settings - YouTube cookies) and retry. "
         "3) Set NEONCLIPPING_COOKIES_BROWSER=chrome and restart, or try another network. "
         "The tool already updated its downloader and tried several other YouTube modes.")

class BlockedError(RuntimeError):
    """Raised when every variant was blocked by YouTube. Carries a friendly, actionable message."""

class YDL:
    """Drop-in for yt_dlp.YoutubeDL: supports `with YDL(opts) as y: y.extract_info(...)` and `y.download([...])`."""
    def __init__(self, opts=None):
        self.opts = dict(opts or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _run(self, fn):
        blocked = False
        first = None
        for options, is_default in variants(self.opts):
            try:
                with yt_dlp.YoutubeDL(options) as y:
                    return fn(y)
            except Exception as e:      # noqa: BLE001 - yt-dlp raises several error types
                first = first or e
                if is_blocked(e):
                    blocked = True
                    continue
                if not is_default:      # cookies unreadable, client unsupported, ...: just try the next way
                    continue
                raise
        if blocked:
            raise BlockedError(GUIDE) from first
        if first is not None and fail_kind(first) and human_error(first):
            raise RuntimeError(human_error(first)) from first
        raise first

    def extract_info(self, url, download=True, **kw):
        return self._run(lambda y: y.extract_info(url, download=download, **kw))

    def download(self, urls):
        return self._run(lambda y: y.download(urls))
