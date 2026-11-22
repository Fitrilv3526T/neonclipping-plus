"""Data-folder housekeeping: usage numbers, a cleanup button and auto-delete of old sources.

Everything the app stores lives in the data folder: finished clips (.mp4 + .json + .srt/.ass),
downloaded/uploaded sources (big), preview frames, transcripts, vision caches, bundles and
queues. The endpoints here let the page show what is used and delete what is not needed:

  GET  /data/usage    - sizes per category and the oldest source age
  POST /data/cleanup  - {"sources": true, "previews": true, "temp": true, "bundles": true,
                         "failed": true, "days": 0}  (days>0 keeps sources younger than that)

A background thread also deletes source videos older than NEONCLIPPING_SOURCE_TTL_DAYS
(default 14; 0 turns it off) once per hour. Finished clips and their records are never touched.
"""
import json
import os
import threading
import time
from pathlib import Path

from fastapi import HTTPException

SWEEP_EVERY = 3600
KEEP_SUFFIX = ('.mp4', '.srt', '.ass', '.vtt', '.json', '.words.json')


def source_ids(data: Path):
    """Ids of downloaded/uploaded source videos that still exist (clip records point at these)."""
    return {p.stem for p in data.glob('*.request.json')} | {p.stem for p in data.glob('*.mp4') if _is_source(data, p.stem)}


def _is_source(data: Path, stem: str):
    """A .mp4 is a source when a .request.json (a download) sits next to it, or when no clip
    record (.json with a result) claims it."""
    if (data / (stem + '.request.json')).exists():
        return True
    record = data / (stem + '.json')
    if not record.exists():
        return True
    try:
        return json.loads(record.read_text(encoding='utf-8')).get('result', {}).get('status') != 'done'
    except (OSError, ValueError):
        return False


def usage(data: Path) -> dict:
    """Bytes per category, for the Settings page."""
    def size(folder: Path):
        return sum(f.stat().st_size for f in folder.rglob('*') if f.is_file()) if folder.exists() else 0
    def glob_size(pattern: str):
        total = 0
        for p in data.glob(pattern):
            total += size(p) if p.is_dir() else p.stat().st_size
        return total
    sources = _source_files(data)
    clips = [p for p in data.glob('*.mp4') if not _is_source(data, p.stem)]
    other_mp4 = [p for p in data.glob('*.mp4') if p not in clips and p not in sources]
    return {
        'sources': {'files': len(sources), 'bytes': sum(p.stat().st_size for p in sources)},
        'clips': {'files': len(clips), 'bytes': sum(p.stat().st_size for p in clips)},
        'previews': {'bytes': glob_size('*-preview')},
        'transcripts': {'bytes': size(data / 'transcripts')},
        'vision': {'bytes': size(data / 'vision')},
        'bundles': {'bytes': size(data / 'bundles')},
        'assets': {'bytes': size(data / 'assets')},
        'other_temp': {'bytes': glob_size('live_*') + sum(p.stat().st_size for p in other_mp4)},
        'total': {'bytes': sum(p.stat().st_size for p in data.rglob('*') if p.is_file())},
    }


def _source_files(data: Path):
    return [p for p in data.glob('*.mp4') if _is_source(data, p.stem)]


def cleanup(data: Path, body: dict, busy) -> dict:
    """Delete what the request asks for. Never touches finished clips. Returns what was freed."""
    if busy():
        raise HTTPException(409, 'Wait for the running jobs to finish before cleaning up.')
    freed = {'sources': 0, 'previews': 0, 'temp': 0, 'bundles': 0, 'failed': 0, 'bytes': 0}

    def remove(path, bucket):
        try:
            size = path.stat().st_size if path.is_file() else 0
            if path.is_dir():
                import shutil
                shutil.rmtree(path, ignore_errors=True)
                freed[bucket] += 1
            else:
                path.unlink(missing_ok=True)
                freed[bucket] += 1
            freed['bytes'] += size
        except OSError:
            pass

    if body.get('sources'):
        days = float(body.get('days') or 0)
        now = time.time()
        for p in _source_files(data):
            if days > 0 and now - p.stat().st_mtime < days * 86400:
                continue                      # keep-recent mode: only older files go
            sid = p.stem
            remove(p, 'sources')
            for extra in ('.request.json', '.words.json', '.cover.jpg'):
                remove(data / (sid + extra), 'sources')
            d = data / sid
            if d.is_dir():
                remove(d, 'sources')
    if body.get('previews'):
        for d in data.glob('*-preview'):
            remove(d, 'previews')
    if body.get('temp'):
        for p in list(data.glob('*.tmp')) + list(data.glob('*.part')) + list(data.glob('*.ytdl')):
            remove(p, 'temp')
        for p in data.glob('lr_*'):
            remove(p, 'temp')
        for p in data.glob('*.audio.*'):
            remove(p, 'temp')
    if body.get('bundles'):
        remove(data / 'bundles', 'bundles')
    if body.get('failed'):
        for f in data.glob('*.state.json'):
            try:
                row = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if row.get('status') == 'error':
                remove(f, 'failed')
    freed['bytes'] = round(freed['bytes'])
    return freed


def ttl_days():
    try:
        return max(0, float(os.environ.get('NEONCLIPPING_SOURCE_TTL_DAYS', '14')))
    except ValueError:
        return 14.0


def sweep_once(data: Path, days: float, busy=lambda: False):
    """Delete source videos not touched for `days` days (0 = off). Returns the number removed."""
    if days <= 0 or busy():
        return 0
    now = time.time()
    removed = 0
    for p in _source_files(data):
        try:
            if now - p.stat().st_mtime > days * 86400:
                p.unlink(missing_ok=True)
                (data / (p.stem + '.request.json')).unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def start_sweeper(data: Path, busy):
    """Hourly background sweep of old sources."""
    def loop():
        while True:
            time.sleep(SWEEP_EVERY)
            try:
                sweep_once(Path(data), ttl_days(), busy)
            except Exception:                  # noqa: BLE001 - housekeeping must never crash the app
                pass
    threading.Thread(target=loop, daemon=True, name='ttl-sweeper').start()


def attach(app, data, state):
    data = Path(data)
    busy = lambda: any(row.get('status') in ('running', 'queued', 'starting', 'recording') for row in list(state.values()))  # noqa: E731

    @app.get('/data/usage')
    def data_usage():
        return {'usage': usage(data), 'ttl_days': ttl_days(),
                'free_bytes': _free(data), 'note': 'Cleanup never deletes finished clips.'}

    @app.post('/data/cleanup')
    def data_cleanup(b: dict):
        if not isinstance(b, dict):
            raise HTTPException(400, 'Send cleanup options.')
        days = b.get('days')
        if days not in (None, ''):
            try:
                b = {**b, 'days': max(0.0, min(3650.0, float(days)))}
            except (TypeError, ValueError):
                raise HTTPException(400, 'days must be a number.')
        return cleanup(data, b, busy)

    start_sweeper(data, busy)


def _free(data: Path):
    try:
        import shutil
        return shutil.disk_usage(data).free
    except OSError:
        return 0
