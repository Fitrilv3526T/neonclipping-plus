"""Smoke tests: the full local pipeline on a synthetic video, with NO GPU and NO internet.

A 10-second synthetic video (FFmpeg testsrc2 + a tone) is pushed through:
  - the highlight finder (highlights.rank, optical flow + audio),
  - scene + silence detection (scenes.py),
  - the caption writer (all styles + the karaoke animation),
  - the layout graph (every layout, including auto-follow),
  - a real /upload -> /export render through the FastAPI app,
  - a real /make run (speech model faked) with cancellation.

Run:  python -m pytest tests/test_smoke.py -v
"""
import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(os.environ['NEONCLIPPING_DATA_DIR'])
DATA.mkdir(parents=True, exist_ok=True)
SRC = Path(__file__).resolve().parent / 'out' / 'synthetic.mp4'


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope='session', autouse=True)
def synthetic_video():
    """A 10 s 1280x720 video with moving content and a 440 Hz tone."""
    if not SRC.exists() or SRC.stat().st_size < 10_000:
        SRC.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error',
             '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=10',
             '-f', 'lavfi', '-i', 'sine=frequency=440:duration=10',
             '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-shortest', str(SRC)],
            capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-500:]
    return SRC


@pytest.fixture(scope='session')
def app_module():
    """The app module, reloaded against the smoke data folder. Older unit tests point
    NEONCLIPPING_DATA_DIR at their own temp folders; this session needs its own."""
    import importlib
    os.environ['NEONCLIPPING_DATA_DIR'] = str(DATA)
    import app as A
    return importlib.reload(A)


@pytest.fixture(scope='session')
def fake_transcribe(app_module):
    """Replace the speech model with a stub: {'words': [], ...}. Keeps the tests offline."""
    def fake(source, start, end, data, report=lambda m: None, language=None, model=None):
        return {'words': [], 'language': 'en', 'duration': max(0.1, end - start), 'model': 'stub'}

    import smart
    import transcripts
    app_module.transcribe = fake
    smart.transcribe = fake
    transcripts.transcribe = fake
    return fake


@pytest.fixture(scope='session')
def client(app_module):
    from fastapi.testclient import TestClient
    # TestClient's default client address is "testclient", not 127.0.0.1; the access gate
    # (correctly) treats anything that is not this machine as a stranger.
    with TestClient(app_module.app, client=('127.0.0.1', 50000)) as c:
        yield c


# ------------------------------------------------------------------ pure-Python parts
def test_highlight_finder(synthetic_video):
    import highlights
    clips = highlights.rank(synthetic_video, 10, count=3, length=5, motion_on=True)
    assert clips, 'the finder must always return at least one candidate'
    for c in clips:
        assert 0 <= c['start'] < c['end'] <= 10.5
        assert 'features' in c


def test_scene_and_silence_detection(synthetic_video):
    import scenes
    cuts = scenes.cut_times(synthetic_video)
    gaps = scenes.silence_gaps(synthetic_video)
    assert isinstance(cuts, list) and isinstance(gaps, list)
    plan = scenes.plan_for(synthetic_video, DATA, key='smoke')
    cached = scenes.plan_for(synthetic_video, DATA, key='smoke')
    assert plan == cached, 'the scene plan must be cached on disk'


def test_scene_snapping():
    import scenes
    candidate = {'start': 4.1, 'end': 12.0}
    scenes.snap(candidate, [4.0, 12.5], [(11.8, 12.6)], min_len=5, max_len=20)
    assert candidate['start'] == 4.0 and candidate['end'] == 12.5


WORDS = [{'start': 0.2 + i * 0.5, 'end': 0.6 + i * 0.5, 'text': w}
         for i, w in enumerate('this is a synthetic caption line for the smoke test'.split())]


@pytest.mark.parametrize('style', ['gta', 'bold', 'clean', 'hormozi', 'mrbeast'])
def test_caption_writer(style, tmp_path):
    import captions
    ass, srt = captions.write(WORDS, style, tmp_path, 'smoke', hook='Wait, this clip is synthetic?!')
    assert (tmp_path / ass).exists() and (tmp_path / srt).exists()
    text = (tmp_path / ass).read_text(encoding='utf-8')
    assert '[Events]' in text and 'Dialogue:' in text
    assert 'SYNTHETIC' in text.upper()          # the words are really in the file
    srt_text = (tmp_path / srt).read_text(encoding='utf-8')
    assert srt_text.count('-->') >= 2 and 'synthetic' in srt_text


def test_karaoke_animation(tmp_path):
    import captions
    ass, _ = captions.write(WORDS, 'hormozi', tmp_path, 'kar', anim='karaoke')
    text = (tmp_path / ass).read_text(encoding='utf-8')
    assert '\\k' in text, 'karaoke mode writes \\k timing tags'


def test_caption_emoji(tmp_path):
    import captions
    words = [dict(w, text=w['text']) for w in WORDS]
    words[0]['text'] = 'police'
    ass, _ = captions.write(words, 'gta', tmp_path, 'emoji', appearance={'caption_emoji': True})
    text = (tmp_path / ass).read_text(encoding='utf-8')
    assert any(ord(ch) > 0x1F000 for ch in text), 'the rule-based emoji must appear'


def test_layout_graph_every_layout():
    import layouts
    rects = [(0, 0, 320, 216), (320, 0, 960, 720)]
    for key, name, _ in layouts.CATALOG:
        graph = layouts.render_graph(key, rects, .32, 1080, 1920)
        assert '[base]' in graph, key
    assert 'auto' in {k for k, _, _ in layouts.CATALOG}


def test_layout_graph_auto_follows_or_falls_back(synthetic_video):
    import layouts
    rects = [(0, 0, 320, 216), (0, 0, 1280, 720)]
    # a synthetic test pattern has no face: must fall back to the fill-style graph, not crash
    graph = layouts.render_graph('auto', rects, .32, 1080, 1920, source=synthetic_video, src_size=(1280, 720))
    assert '[base]' in graph


def test_autofollow_expression_from_points(synthetic_video, monkeypatch):
    import autofollow
    points = [(0.0, 0.3, 0.4), (2.0, 0.3, 0.4), (2.5, 0.7, 0.4), (6.0, 0.7, 0.4), (9.0, 0.5, 0.5)]
    monkeypatch.setattr(autofollow, 'follow_path', lambda path: points)
    # a 9:16 window inside the 1280x720 synthetic source is 405x720
    expr = autofollow.crop_expression(synthetic_video, 405, 720, src_w=1280, src_h=720)
    assert expr, 'a moving face must produce crop expressions'
    x, y = expr
    assert x.startswith('if(lt(t,') and 't-' in x, 'the expression must switch over time'
    monkeypatch.setattr(autofollow, 'follow_path', lambda path: [(0.0, 0.5, 0.5)])
    one = autofollow.crop_expression(synthetic_video, 405, 720, src_w=1280, src_h=720)
    assert one == ('438', '0'), 'a single still point gives a constant crop'
    monkeypatch.setattr(autofollow, 'follow_path', lambda path: None)
    assert autofollow.plan(synthetic_video, 405, 720, 1280, 720) is None, 'no face -> no plan (centre crop fallback)'
    monkeypatch.setattr(autofollow, 'follow_path', lambda path: points)
    assert autofollow.plan(synthetic_video, 608, 1080, 1280, 720) is None, 'a window bigger than the source gives no plan'


def test_emoji_rules():
    import emojis
    assert emojis.for_caption('the police arrived') == '🚓'
    assert emojis.for_caption('https://police.example.com/x') == ''
    assert '🔥' in emojis.for_hook('this explosion was insane')


def test_friendly_youtube_errors():
    import ytdl
    assert ytdl.fail_kind(Exception('Please sign in to confirm you are not a bot')) == 'bot check'
    assert ytdl.fail_kind(Exception('This video is private')) == 'private'
    assert ytdl.fail_kind(Exception('Video unavailable')) == 'unavailable'
    assert 'cookies.txt' in ytdl.human_error(Exception('HTTP Error 429: Too Many Requests'))
    assert ytdl.fail_kind(Exception('some unrelated failure')) is None


def test_curation_diversity():
    import curation
    clips = [{'start': s, 'end': s + 30, 'rank': 1.0 - i * 0.01, 'transcript': f'unique words {i}',
              'features': {}, 'title': str(i)} for i, s in enumerate(range(0, 300, 30))]
    picked = curation.select(clips, 3, 'balanced')
    centres = [(c['start'] + c['end']) / 2 for c in picked]
    for a in centres:
        for b in centres:
            if a is not b:
                assert abs(a - b) >= 90 or abs(a - b) == 0, 'picks must come from different parts of the video'


# ------------------------------------------------------------------ end-to-end through the API
def test_upload_and_export(synthetic_video, client):
    with SRC.open('rb') as f:
        r = client.post('/upload', files={'file': ('synthetic.mp4', f, 'video/mp4')})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info['duration'] >= 9.5 and info['w'] == 1280

    body = {
        'id': info['id'], 'start': 1.0, 'end': 7.0, 'layout': 'blur', 'top': .32,
        'captions': True, 'caption_style': 'gta', 'caption_anim': 'highlight', 'caption_y': 1450,
        'cam': {'x': 0, 'y': 0, 'w': .25, 'h': .3}, 'game': {'x': .25, 'y': 0, 'w': .75, 'h': 1},
        'hook': 'Wait, this is synthetic?!', 'title': 'Smoke clip', 'words': WORDS,
    }
    r = client.post('/export', json=body)
    assert r.status_code == 200, r.text
    job = r.json()['job']
    final = _poll(client, f'/status/{job}')
    assert final['status'] == 'done', final.get('error')
    out = DATA / (final['file'] + '.mp4')
    assert out.exists() and out.stat().st_size > 10_000
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height',
                            '-of', 'json', str(out)], capture_output=True, text=True)
    streams = json.loads(probe.stdout)['streams']
    assert any(s.get('height') == 1920 for s in streams), 'the export must be vertical 1080x1920'
    assert (DATA / (final['file'] + '.srt')).exists()


def test_make_clips_end_to_end(synthetic_video, client, fake_transcribe):
    with SRC.open('rb') as f:
        up = client.post('/upload', files={'file': ('synthetic.mp4', f, 'video/mp4')})
    assert up.status_code == 200
    sid = up.json()['id']
    body = {'id': sid, 'count': 2, 'min_len': 4, 'max_len': 8, 'mode': 'fast', 'layout': 'fill',
            'caption_style': 'clean', 'captions': True}
    r = client.post('/make', json=body)
    assert r.status_code == 200, r.text
    job = r.json()['job']
    final = _poll(client, f'/make/{job}', timeout=240)
    assert final['status'] == 'done', final.get('error')
    assert final.get('clips'), 'Make clips must produce clips'
    for clip in final['clips']:
        assert clip.get('file') and clip.get('score') is not None
        assert (DATA / (clip['file'] + '.mp4')).exists()


def test_cancel_endpoint(synthetic_video, client, fake_transcribe):
    with SRC.open('rb') as f:
        up = client.post('/upload', files={'file': ('synthetic.mp4', f, 'video/mp4')})
    sid = up.json()['id']
    r = client.post('/make', json={'id': sid, 'count': 3, 'min_len': 4, 'max_len': 8, 'layout': 'blur'})
    job = r.json()['job']
    r = client.post(f'/make/{job}/cancel')
    assert r.status_code == 200 and r.json()['ok']
    final = _poll(client, f'/make/{job}', timeout=240)
    assert final['status'] in ('canceled', 'done'), final      # fast machines may finish first
    assert 'clips' in final


def test_library_and_bundles(client):
    r = client.get('/library')
    assert r.status_code == 200
    records = r.json()
    assert any(rec.get('id') for rec in records), 'the clips made above must be in the library'
    r = client.post('/bundles/all')
    assert r.status_code == 200, r.text
    assert r.json()['clips'] >= 1
    token = r.json()['bundle']
    r = client.get(f'/bundles/{token}')
    assert r.status_code == 200 and r.headers['content-type'] == 'application/zip'


def test_cookies_upload_validation(client):
    # not a cookies file
    r = client.post('/ytdl/cookies', files={'file': ('x.txt', b'hello world', 'text/plain')})
    assert r.status_code == 400
    good = b'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES\n'
    r = client.post('/ytdl/cookies', files={'file': ('cookies.txt', good, 'text/plain')})
    assert r.status_code == 200, r.text
    assert (DATA / 'youtube_cookies.txt').exists()
    r = client.post('/ytdl/cookies/clear')
    assert r.status_code == 200
    assert not (DATA / 'youtube_cookies.txt').exists()


def test_data_usage_and_cleanup(client):
    r = client.get('/data/usage')
    assert r.status_code == 200
    usage = r.json()['usage']
    assert usage['clips']['files'] >= 1
    r = client.post('/data/cleanup', json={'previews': True, 'temp': True})
    assert r.status_code == 200 and r.json()['bytes'] >= 0


def test_remote_token_gate(tmp_path):
    """NEONCLIPPING_REMOTE=1: nothing opens without the token; the link exchanges it for a cookie."""
    os.environ['NEONCLIPPING_DATA_DIR'] = str(tmp_path)
    os.environ['NEONCLIPPING_REMOTE'] = '1'
    try:
        import importlib
        import phone
        importlib.reload(phone)
        from fastapi import FastAPI
        from fastapi.responses import PlainTextResponse
        from fastapi.testclient import TestClient
        app = FastAPI()

        @app.get('/')
        def home():
            return PlainTextResponse('studio')

        @app.get('/library')
        def library():
            return PlainTextResponse('[]')

        welcome = phone.attach(app, tmp_path, ROOT)
        assert callable(welcome)
        assert 'token=' in welcome()
        pc = TestClient(app, client=('127.0.0.1', 40001))       # a tunnel also appears as localhost
        away = TestClient(app, client=('203.0.113.9', 40002))   # an internet stranger
        assert pc.get('/').status_code == 401                   # no token: nothing opens
        assert away.get('/library').status_code == 401
        assert pc.get('/healthz').status_code == 404            # open route (404 = reached the app)
        token = re.search(r'token=([A-Za-z0-9_\-]+)', welcome()).group(1)
        r = pc.get('/', params={'token': token}, follow_redirects=False)
        assert r.status_code == 303, 'the token must turn into a cookie and leave the URL'
        assert 'nc_remote' in r.headers.get('set-cookie', '')
        r = pc.get('/')
        assert r.status_code == 200 and r.text == 'studio'
        # a wrong token is rejected and rate-limited
        assert pc.post('/remote/login', json={'token': 'nope'}).status_code == 401
    finally:
        os.environ.pop('NEONCLIPPING_REMOTE', None)
        os.environ['NEONCLIPPING_DATA_DIR'] = str(DATA)
        import importlib
        import phone
        importlib.reload(phone)


def _poll(client, path, timeout=120):
    """Poll a status endpoint until the job leaves 'running'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(path).json()
        if row.get('status') not in ('running', 'queued', None):
            return row
        time.sleep(0.5)
    raise AssertionError(f'{path} did not finish within {timeout}s')
