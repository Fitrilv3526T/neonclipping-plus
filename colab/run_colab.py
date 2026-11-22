#!/usr/bin/env python3
"""One-click setup + launcher for Google Colab (free tier), also usable on any Linux box.

Every step is idempotent: safe to re-run, and it skips work that is already done.

    python colab/run_colab.py            # installs what is missing, starts the app, opens a tunnel
    python colab/run_colab.py --no-tunnel
    python colab/run_colab.py --drive    # also mount Google Drive (Colab only)
    python colab/run_colab.py --save-drive   # copy finished clips to Drive

What it does
  1. apt install ffmpeg + zstd (skip when present)
  2. pip install -r requirements.txt (+ a fresh yt-dlp)
  3. install the Deno JS runtime (yt-dlp needs it for every YouTube quality)
  4. install + start Ollama and pull the small local model (llama3.2:3b by default)
  5. download the free caption fonts (Anton / Montserrat, OFL)
  6. start the app with NEONCLIPPING_REMOTE=1 (token-protected remote access)
  7. start a free Cloudflare quick tunnel and print the public ?token=... URL
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent       # the repository root
PORT = os.environ.get('NEONCLIPPING_PORT', '8770')
MODEL = os.environ.get('NEONCLIPPING_OLLAMA_MODEL', 'llama3.2:3b')
DENO_BIN = Path.home() / '.deno' / 'bin' / 'deno'
CLOUDFLARED = Path('/usr/local/bin/cloudflared')
LOG = ROOT / 'colab' / 'server.log'
TUNNEL_LOG = ROOT / 'colab' / 'tunnel.log'


def say(msg):
    print(f'\n=== {msg} ===', flush=True)


def sh(cmd, shell=False, **kw):
    """Run a command, printing it first. Returns the CompletedProcess."""
    display = ' '.join(cmd) if isinstance(cmd, list) else cmd
    print(f'$ {display}', flush=True)
    return subprocess.run(cmd, shell=shell, **kw)


def which(binary):
    try:
        return bool(subprocess.run(['which', binary], capture_output=True).stdout.strip())
    except OSError:
        return False


def http_ok(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# ---------------------------------------------------------------- steps
def step_apt():
    say('1/7 FFmpeg + zstd (system packages)')
    if which('ffmpeg') and which('zstd'):
        print('already installed, skipping')
        return
    sh(['apt-get', '-qq', 'update'])
    sh(['apt-get', '-qq', 'install', '-y', 'ffmpeg', 'zstd'])


def step_pip():
    say('2/7 Python packages')
    r = sh([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(ROOT / 'requirements.txt')])
    if r.returncode:
        print('pip reported an error; continuing (the app will say what is missing).')
    # keep yt-dlp fresh: Colab images often carry a months-old version
    sh([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp'])


def step_deno():
    say('3/7 Deno (JS runtime for yt-dlp / YouTube)')
    if which('deno') or DENO_BIN.exists():
        print('Deno already installed, skipping')
        return
    r = sh('curl -fsSL https://deno.land/install.sh | sh -s -- -y', shell=True)
    if r.returncode == 0 and DENO_BIN.exists():
        print(f'Deno installed at {DENO_BIN}')
    else:
        print('Deno install failed; YouTube downloads still work but may miss some qualities.')


def step_ollama():
    say(f'4/7 Ollama + local model ({MODEL})')
    if not which('ollama'):
        r = sh('curl -fsSL https://ollama.com/install.sh | sh', shell=True)
        if r.returncode:
            print('Ollama install failed. The app works without it (simpler titles and moment picking).')
            return
    if not http_ok('http://127.0.0.1:11434/api/tags'):
        print('Starting the Ollama service...')
        subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        for _ in range(20):
            if http_ok('http://127.0.0.1:11434/api/tags'):
                break
            time.sleep(1)
    if http_ok('http://127.0.0.1:11434/api/tags'):
        have = False
        try:
            tags = json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5))['models']
            have = any(m['name'].split(':')[0] == MODEL.split(':')[0] for m in tags)
        except Exception:
            pass
        if not have:
            print(f'Pulling {MODEL} (about 2 GB, one time only)...')
            sh(['ollama', 'pull', MODEL])
        else:
            print(f'{MODEL} already present, skipping')


def step_fonts():
    say('5/7 Caption fonts (Anton / Montserrat, SIL OFL licence)')
    r = sh([sys.executable, str(ROOT / 'fonts.py')], capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    print(out.strip() or 'fonts.py said nothing')
    if 'none' in out:
        print('Downloading fonts...')
        sh([sys.executable, str(ROOT / 'fonts.py'), '--download'])


def step_server():
    say('6/7 Starting NeonClipping (remote access ON, token protected)')
    if http_ok(f'http://127.0.0.1:{PORT}/healthz'):
        print('The app is already running, keeping it.')
        return
    env = {**os.environ, 'NEONCLIPPING_REMOTE': '1', 'NEONCLIPPING_PORT': PORT, 'PYTHONUNBUFFERED': '1'}
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open('ab') as out:
        subprocess.Popen([sys.executable, 'app.py'], cwd=str(ROOT), env=env,
                         stdout=out, stderr=out, start_new_session=True)
    for _ in range(45):
        if http_ok(f'http://127.0.0.1:{PORT}/healthz'):
            print('The app is up.')
            return
        time.sleep(1)
    print('The app did not answer the health check; the last log lines are:')
    print(LOG.read_text(errors='replace')[-1500:] if LOG.exists() else '(no log)')


def token_from_log():
    try:
        text = LOG.read_text(errors='replace')
    except OSError:
        return ''
    found = re.findall(r'token=([A-Za-z0-9_\-]+)', text)
    return found[-1] if found else ''      # the log is appended to, so the LAST token is the current one


def step_tunnel():
    say('7/7 Free Cloudflare quick tunnel (public URL)')
    if not CLOUDFLARED.exists():
        r = sh(f'curl -fsSL -o {CLOUDFLARED} https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x {CLOUDFLARED}', shell=True)
        if r.returncode:
            print('cloudflared could not be downloaded. Use the app through the Colab proxy instead.')
            return
    TUNNEL_LOG.parent.mkdir(exist_ok=True)
    running = subprocess.run(['pgrep', '-f', 'cloudflared tunnel'], capture_output=True).stdout.strip()
    if running:
        print('A tunnel is already running.')
    else:
        with TUNNEL_LOG.open('ab') as out:
            subprocess.Popen([str(CLOUDFLARED), 'tunnel', '--url', f'http://127.0.0.1:{PORT}',
                              '--no-autoupdate'], stdout=out, stderr=out, start_new_session=True)
    url = ''
    for _ in range(40):
        try:
            text = TUNNEL_LOG.read_text(errors='replace')
        except OSError:
            text = ''
        found = re.findall(r'(https://[a-z0-9-]+\.trycloudflare\.com)', text)
        if found:
            url = found[-1]
            break
        time.sleep(1)
    token = token_from_log()
    if url:
        link = f'{url}/?token={token}' if token else url
        print('\n' + '=' * 64)
        print('OPEN THIS LINK IN ANY BROWSER (keep it private, it is your access key):')
        print(f'  {link}')
        print('=' * 64 + '\n')
    else:
        print('The tunnel did not come up in 40 s. Check colab/tunnel.log and re-run.')


def step_drive():
    say('Mounting Google Drive')
    try:
        from google.colab import drive     # type: ignore
    except ImportError:
        print('Not running inside Colab; skipping Drive.')
        return
    drive.mount('/content/drive')
    target = Path('/content/drive/MyDrive/NeonClipping')
    target.mkdir(parents=True, exist_ok=True)
    print(f'Drive ready. Finished clips can be copied there with:  python colab/run_colab.py --save-drive')


def save_to_drive():
    """Copy every finished clip (mp4 + srt + json) from the data folder to Google Drive."""
    data = Path(os.environ.get('NEONCLIPPING_DATA_DIR', ROOT / 'data'))
    target = Path('/content/drive/MyDrive/NeonClipping')
    if not target.exists():
        print('Drive is not mounted. Run:  python colab/run_colab.py --drive')
        return
    count = 0
    for f in data.glob('*.json'):
        if f.name.endswith(('.state.json', '.words.json', '.request.json')):
            continue
        try:
            record = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if record.get('result', {}).get('status') != 'done' or record.get('superseded_by'):
            continue
        for ext in ('.mp4', '.srt', '.json'):
            src = data / (f.stem + ext)
            if src.exists():
                (target / src.name).write_bytes(src.read_bytes())
                count += 1
    print(f'Copied {count} files to {target}')


def main():
    ap = argparse.ArgumentParser(description='NeonClipping one-click Colab launcher')
    ap.add_argument('--no-tunnel', action='store_true', help='do not start the Cloudflare tunnel')
    ap.add_argument('--drive', action='store_true', help='mount Google Drive (Colab only)')
    ap.add_argument('--save-drive', action='store_true', help='copy finished clips to Drive')
    args = ap.parse_args()
    if args.save_drive:
        save_to_drive()
        return
    if args.drive:
        step_drive()
    os.chdir(str(ROOT))
    os.environ['PATH'] = str(DENO_BIN.parent) + os.pathsep + os.environ.get('PATH', '')
    step_apt()
    step_pip()
    step_deno()
    step_ollama()
    step_fonts()
    step_server()
    if not args.no_tunnel:
        step_tunnel()
    print('\nDone. The Colab session must stay open; closing the tab stops the app.')


if __name__ == '__main__':
    main()
