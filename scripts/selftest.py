#!/usr/bin/env python3
"""Prints PASS/FAIL for everything NeonClipping needs. No downloads, no side effects.

    python scripts/selftest.py

Checks: python version, ffmpeg, ffprobe, yt-dlp import, faster-whisper import (not the model
download), Ollama reachability, GPU presence, NVENC usability, caption fonts, fontconfig.
Exit code 0 when every REQUIRED check passes; optional ones only warn.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []


def check(name, ok, detail='', required=True, fix=''):
    RESULTS.append((name, bool(ok), detail, required, fix))
    mark = 'PASS' if ok else ('FAIL' if required else 'warn')
    line = f'[{mark}] {name}' + (f' - {detail}' if detail else '')
    print(line)
    if not ok and fix:
        print(f'       fix: {fix}')
    return ok


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '').strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ''


def main():
    print('NeonClipping self-test\n' + '-' * 60)

    # Python
    v = sys.version_info
    check('Python 3.10-3.13', (3, 10) <= (v.major, v.minor) <= (3, 13),
          f'{v.major}.{v.minor}.{v.micro}', True,
          'Use Python 3.10-3.13 (Colab and the pinned requirements match those).')

    # FFmpeg / ffprobe
    code, out = run(['ffmpeg', '-version'])
    ver = out.splitlines()[0].split()[2] if out else ''
    check('ffmpeg', code == 0, ver, True, 'Install it: winget install Gyan.FFmpeg / brew install ffmpeg / sudo apt install ffmpeg')
    code, out = run(['ffprobe', '-version'])
    check('ffprobe', code == 0, out.splitlines()[0].split()[2] if out else '', True, 'Comes with every normal FFmpeg install.')

    # yt-dlp
    try:
        os.environ.setdefault('NEONCLIPPING_NO_AUTOUPDATE', '1')
        import yt_dlp
        check('yt-dlp', True, yt_dlp.version.__version__, True, 'pip install -U yt-dlp')
    except Exception as error:
        check('yt-dlp', False, str(error)[:80], True, 'pip install -U yt-dlp')

    # faster-whisper (import only; no model download here)
    try:
        import faster_whisper  # noqa: F401
        check('faster-whisper', True, 'import ok (model downloads on first use)', True,
              'pip install faster-whisper')
    except Exception as error:
        check('faster-whisper', False, str(error)[:80], True, 'pip install faster-whisper')

    # OpenCV (motion analysis + face tracking)
    try:
        import cv2  # noqa: F401
        check('OpenCV', True, cv2.__version__, True, 'pip install opencv-python-headless')
    except Exception as error:
        check('OpenCV', False, str(error)[:80], True, 'pip install opencv-python-headless')

    # YuNet face model file (bundled with the repo)
    model = ROOT / 'models' / 'face_detection_yunet_2023mar.onnx'
    check('face model (YuNet)', model.exists() and model.stat().st_size > 100_000,
          f'{model.stat().st_size // 1024} KB' if model.exists() else 'missing', False,
          'git checkout the models/ folder (LFS or direct file).')

    # Ollama (optional)
    try:
        import urllib.request
        import json as _json
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
            tags = _json.load(r).get('models') or []
        names = ', '.join(m['name'] for m in tags[:4]) or 'running, no models pulled'
        check('Ollama', True, names, False, 'Not required. For smarter titles: install Ollama, then: ollama pull llama3.2:3b')
    except Exception:
        check('Ollama', False, 'not reachable', False,
              'Optional. Install from https://ollama.com then run: ollama pull llama3.2:3b')

    # GPU + NVENC
    code, out = run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'])
    if code == 0 and out:
        check('GPU (NVIDIA)', True, out.splitlines()[0], False)
        code2, out2 = run(['ffmpeg', '-hide_banner', '-encoders'])
        listed = 'h264_nvenc' in out2
        if listed:
            t = Path(ROOT / 'data') / 'selftest_nvenc.mp4'
            t.parent.mkdir(exist_ok=True)
            c3, _ = run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.2',
                         '-frames:v', '3', '-c:v', 'h264_nvenc', '-f', 'null', '-'])
            t.unlink(missing_ok=True)
            check('NVENC', c3 == 0, 'renders with the GPU' if c3 == 0 else 'listed but failed a test encode', False,
                  'GPU rendering is optional; the app falls back to the CPU automatically.')
        else:
            check('NVENC', False, 'not in this ffmpeg build', False, 'CPU rendering still works.')
    else:
        check('GPU (NVIDIA)', False, 'none found', False, 'Everything runs on CPU, just slower captions.')
        check('NVENC', False, 'no GPU', False, '')

    # Fonts
    try:
        sys.path.insert(0, str(ROOT))
        import fonts
        have = fonts.available()
        check('bundled caption fonts', True, ', '.join(have) or 'none bundled (system fonts used)', False,
              'Optional: python fonts.py --download  (Anton/Montserrat, OFL)')
    except Exception as error:
        check('bundled caption fonts', False, str(error)[:60], False, '')

    code, out = run(['fc-match', 'sans-serif'])
    check('fontconfig', code == 0 and bool(out), out.split(':')[0] if out else 'not found', False,
          'Linux only: sudo apt install fontconfig')

    # Remote access mode
    print('-' * 60)
    remote = os.environ.get('NEONCLIPPING_REMOTE') == '1'
    print(f"remote access: {'ON (token protected)' if remote else 'off (this computer only)'}")
    print(f"data folder:   {os.environ.get('NEONCLIPPING_DATA_DIR', str(ROOT / 'data'))}")

    failed_required = [r for r in RESULTS if not r[1] and r[3]]
    print('-' * 60)
    if failed_required:
        print(f'{len(failed_required)} REQUIRED check(s) FAILED: ' + ', '.join(r[0] for r in failed_required))
        sys.exit(1)
    warnings = [r for r in RESULTS if not r[1] and not r[3]]
    if warnings:
        print(f'All required checks passed. {len(warnings)} optional warning(s).')
    else:
        print('All checks passed.')
    sys.exit(0)


if __name__ == '__main__':
    main()
