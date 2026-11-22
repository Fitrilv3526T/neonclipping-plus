"""Scene-cut and silence detection, so clips never start or end mid-cut or mid-word.

Both signals are computed locally with FFmpeg only (no model, no internet):
  cuts    - hard scene changes, via FFmpeg's scene score (frame difference), sampled at low
            resolution. Returns the times where a new scene begins.
  silence - gaps >= 0.35 s where the audio is near-quiet. Clip boundaries snap into these
            gaps so a sentence is never cut in half.

Used by smart.find() to snap candidate boundaries, and available as /scenes/{id}.
"""
import json
import subprocess
from pathlib import Path

W, H, FPS = 160, 90, 5          # tiny frames: only the change between them matters
CUT_SCORE = 0.30                 # FFmpeg scene score above this = a cut
SILENCE_DB = -32.0               # below this RMS = quiet
SILENCE_MIN = 0.35               # seconds of quiet that counts as a real gap


def cut_times(path, threshold=CUT_SCORE):
    """Times (seconds) where a hard scene change happens, from FFmpeg's scene detection."""
    cmd = ['ffmpeg', '-v', 'info', '-i', str(path), '-an',
           '-vf', f'select=gt(scene\\,{threshold}),metadata=print:file=-', '-f', 'null', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return []
    out = []
    text = (r.stdout or '') + (r.stderr or '')
    for line in text.splitlines():
        if 'pts_time:' not in line:
            continue
        try:
            out.append(float(line.split('pts_time:')[1].strip().split()[0]))
        except (ValueError, IndexError):
            continue
    return out


def silence_gaps(path, min_len=SILENCE_MIN):
    """[(start, end)] of quiet stretches of at least `min_len` seconds."""
    cmd = ['ffmpeg', '-v', 'info', '-i', str(path), '-vn',
           '-af', f'silencedetect=noise={SILENCE_DB}dB:d={min_len}', '-f', 'null', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return []
    text = (r.stderr or '') + (r.stdout or '')
    starts, gaps = [], []
    for line in text.splitlines():
        if 'silence_start:' in line:
            try:
                starts.append(float(line.split('silence_start:')[1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
        elif 'silence_end:' in line and starts:
            try:
                end = float(line.split('silence_end:')[1].strip().split()[0])
            except (ValueError, IndexError):
                continue
            gaps.append((max(0.0, starts.pop(0)), end))
    return gaps


def _nearest(times, target, before=True, max_shift=1.2):
    """The time from `times` closest to `target` (and not after it when before=True), within max_shift."""
    if not times:
        return None
    if before:
        eligible = [t for t in times if t <= target + 0.05]
        best = max(eligible) if eligible else None
    else:
        eligible = [t for t in times if t >= target - 0.05]
        best = min(eligible) if eligible else None
    if best is None or abs(best - target) > max_shift:
        return None
    return best


def snap(candidate, cuts, silences, min_len, max_len):
    """Move a candidate's start to the nearest scene cut / silence start and its end to the
    nearest sentence-friendly boundary, keeping the length inside [min_len, max_len].
    Mutates and returns the candidate dict."""
    if not cuts and not silences:
        return candidate
    edges = []
    for s, e in silences:
        edges += [s, e]
    all_times = sorted(set(cuts) | set(edges))
    start, end = float(candidate['start']), float(candidate['end'])
    new_start = _nearest(all_times, start, before=True) or start
    if new_start < start and end - new_start > max_len:      # never grow past max_len
        new_start = max(start, end - max_len)
    new_end = _nearest(all_times, end, before=False) or end
    if new_end > end and new_end - new_start > max_len:
        new_end = min(end, new_start + max_len)
    if new_end - new_start >= min(min_len, 3.0) and new_end > new_start:
        candidate['start'], candidate['end'] = round(new_start, 2), round(new_end, 2)
    return candidate


def plan_for(path, cache_dir=None, key=''):
    """{'cuts': [...], 'silences': [...]} with a small disk cache keyed by the source."""
    if cache_dir is not None:
        cache = Path(cache_dir) / f'scenes-{key}.json'
        if cache.exists():
            try:
                return json.loads(cache.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
    result = {'cuts': [round(t, 2) for t in cut_times(path)],
              'silences': [[round(a, 2), round(b, 2)] for a, b in silence_gaps(path)]}
    if cache_dir is not None:
        try:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            cache = Path(cache_dir) / f'scenes-{key}.json'
            cache.write_text(json.dumps(result), encoding='utf-8')
        except OSError:
            pass
    return result
