"""Auto-follow speaker: a 9:16 crop that tracks the speaker's face, without jitter.

How it works (all local, CPU-friendly, no download):
  1. Sample frames of the source at ~2 fps at a small size.
  2. Find the face in each frame with the bundled YuNet detector (the same MIT-licensed
     230 KB model facecam_detect.py uses). No face in a frame -> keep the previous position.
  3. Smooth the face centre with an exponential moving average (EMA) and then step-limit the
     movement, so the crop glides instead of twitching.
  4. Simplify the path into a few constant/linear segments and render them as an FFmpeg crop
     expression with per-segment x/y. The layout module turns this into the crop filter.

When no face is found in the whole video, plan() returns None and the caller falls back to a
plain centre crop (layout 'fill').
"""
import subprocess
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:                                # opencv is in requirements; guard anyway
    cv2 = None

MODEL = Path(__file__).resolve().parent / 'models' / 'face_detection_yunet_2023mar.onnx'
SAMPLE_FPS = 2.0
SMALL_W, SMALL_H = 480, 270
EMA = 0.12                    # smoothing: higher = more lag, less jitter
DEAD_ZONE = 0.06              # ignore drift smaller than this fraction of the frame
MAX_STEP = 0.35               # maximum fraction of the frame the crop may move per second
MIN_FACE = 0.05               # faces smaller than 5% of frame width are ignored


def _probe_size(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height:format=duration', '-of', 'csv=p=0:s=,', str(path)],
                       capture_output=True, text=True, timeout=30)
    values = [x for x in (r.stdout or '').replace('\n', ',').split(',') if x.strip()]
    width, height = int(float(values[0])), int(float(values[1]))
    duration = float(values[2]) if len(values) > 2 and values[2].strip() else 0.0
    return width, height, duration


def _frames(path, duration):
    """Yield (time_seconds, BGR small frame)."""
    cmd = ['ffmpeg', '-v', 'error', '-i', str(path), '-an',
           '-vf', f'fps={SAMPLE_FPS},scale={SMALL_W}:{SMALL_H}', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = SMALL_W * SMALL_H * 3
    t, out = 0.0, []
    try:
        while True:
            raw = p.stdout.read(size)
            if not raw or len(raw) < size:
                break
            out.append((t, np.frombuffer(raw, np.uint8).reshape(SMALL_H, SMALL_W, 3)))
            t += 1.0 / SAMPLE_FPS
        p.wait(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        p.stdout.close()
    return out


class _Faces:
    def __init__(self):
        self.det = cv2.FaceDetectorYN.create(str(MODEL), '', (320, 320), 0.5, 0.3, 500)

    def centre(self, img):
        """(cx, cy) of the biggest face in frame-normalised 0..1, or None."""
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(img)
        if found is None:
            return None
        best = None
        for row in found:
            x, y, fw, fh, score = float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[14])
            if score < 0.6 or fw < MIN_FACE * w:
                continue
            if best is None or fw * fh > best[0]:
                best = (fw * fh, x + fw / 2, y + fh / 2)
        return None if best is None else (best[1] / w, best[2] / h)


def follow_path(path):
    """[(t, cx, cy)] smoothed face centres in 0..1 fractions, or None when no face at all."""
    if cv2 is None or not MODEL.exists():
        return None
    width, height, duration = _probe_size(path)
    if duration <= 0 or width <= 0:
        return None
    frames = _frames(path, duration)
    if len(frames) < 4:
        return None
    faces = _Faces()
    raw = []
    for t, img in frames:
        centre = faces.centre(img)
        raw.append((t, centre) if centre else (t, None))
    seen = [c for _, c in raw if c]
    if not seen:
        return None
    # start from the median of the first detections for a stable opening
    cx = float(np.median([c[0] for c in seen[:5]]))
    cy = float(np.median([c[1] for c in seen[:5]]))
    path_out, last = [], (cx, cy)
    for i, (t, centre) in enumerate(raw):
        nxt_t = raw[i + 1][0] if i + 1 < len(raw) else t + 1.0 / SAMPLE_FPS
        dt = max(1e-3, nxt_t - t)
        if centre is not None:
            tx, ty = centre
            # EMA smoothing toward the measured face
            cx += EMA * (tx - cx)
            cy += EMA * (ty - cy)
        # dead zone: tiny drift does not move the crop at all
        if abs(cx - last[0]) < DEAD_ZONE:
            cx = last[0]
        if abs(cy - last[1]) < DEAD_ZONE:
            cy = last[1]
        # step limit: never jump
        max_move = MAX_STEP * dt
        cx = last[0] + max(-max_move, min(max_move, cx - last[0]))
        cy = last[1] + max(-max_move, min(max_move, cy - last[1]))
        last = (cx, cy)
        path_out.append((t, round(cx, 4), round(cy, 4)))
    return path_out


def _simplify(points):
    """Merge consecutive points whose position barely changes into constant segments."""
    if not points:
        return []
    out = [points[0]]
    for t, x, y in points[1:]:
        pt, px, py = out[-1]
        if abs(x - px) < 0.015 and abs(y - py) < 0.015:
            out[-1] = (pt, x, y)          # hold position: extend the constant segment
        else:
            out.append((t, x, y))
    return out


def crop_expression(path, out_w, out_h, src_w=None, src_h=None, max_points=200):
    """FFmpeg crop x/y expressions that follow the speaker, or None for a centre crop.

    out_w/out_h: the crop window in source pixels (for example 608x1080 for 9:16 of 1080p).
    Built as if(lt(t,T1),seg0, if(lt(t,T2),seg1, ... pos_last)) so each frame picks its own
    segment; libavfilter evaluates the expressions once per frame."""
    points = follow_path(path)
    if not points:
        return None
    if src_w is None or src_h is None:
        src_w, src_h, _ = _probe_size(path)
    if src_w < out_w or src_h < out_h:      # a window exactly as tall as the source is fine
        return None
    pts = _simplify(points)
    if len(pts) > max_points:                     # very long clips: keep the expression sane
        step = len(pts) / max_points
        pts = [pts[int(i * step)] for i in range(max_points)] + [pts[-1]]
    limit_x, limit_y = src_w - out_w, src_h - out_h

    def clamp_x(fx):
        return max(0, min(limit_x, round(fx * src_w - out_w / 2)))

    def clamp_y(fy):
        return max(0, min(limit_y, round(fy * src_h - out_h / 2)))

    if len(pts) == 1:
        return str(clamp_x(pts[0][1])), str(clamp_y(pts[0][2]))
    # positions for every point, then build the expression from the last segment inward
    pos = [(round(t, 2), clamp_x(x), clamp_y(y)) for t, x, y in pts]
    expr_x, expr_y = str(pos[-1][1]), str(pos[-1][2])          # t >= last change: hold the end
    for i in range(len(pos) - 2, -1, -1):
        t0, px, py = pos[i]
        t1, nx, ny = pos[i + 1]
        if px == nx and py == ny:
            seg_x, seg_y = str(px), str(py)
        else:
            rise = min(0.5, max(0.05, t1 - t0))
            seg_x = f'{px}+({nx - px})*(t-{t0:.2f})/{rise:.2f}'
            seg_y = f'{py}+({ny - py})*(t-{t0:.2f})/{rise:.2f}'
        expr_x = f'if(lt(t,{t1:.2f}),{seg_x},{expr_x})'
        expr_y = f'if(lt(t,{t1:.2f}),{seg_y},{expr_y})'
    return expr_x, expr_y


def plan(path, out_w, out_h, src_w=None, src_h=None):
    """Crop filter fragment 'w:h:x_expr:y_expr' for layouts.render_graph, or None."""
    expr = crop_expression(path, out_w, out_h, src_w, src_h)
    if not expr:
        return None
    return f'{out_w}:{out_h}:{expr[0]}:{expr[1]}'
