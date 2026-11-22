# Changelog — NeonClipping Plus

Everything below is on top of upstream NeonClipping (iamajmeena/neonclipping, MIT, by Ajay K
Meena), whose features all keep working. Grouped by the upgrade plan A–H.

## A — Colab-first, one-click run
- **`colab/NeonClipping_Colab.ipynb`** (new): one-cell idempotent setup — apt FFmpeg+zstd, pip
  install, Deno, Ollama + llama3.2:3b, caption fonts, app start, free Cloudflare quick tunnel;
  optional Google-Drive mount + "save clips to Drive" cells; troubleshooting notes.
- **`colab/run_colab.py`** (new): the idempotent launcher the notebook calls; `--drive`,
  `--save-drive`, `--no-tunnel` flags; every step checks before doing.
- **`phone.py`** (rewritten): real remote access. `NEONCLIPPING_REMOTE=1` binds all interfaces
  and requires a random startup token (`?token=…` → HttpOnly cookie via 303 redirect;
  constant-time compare; per-IP rate limit; `NEONCLIPPING_REMOTE_TOKEN` to pin). Replaces the
  old blanket `{"detail": "Phone access is off on this computer."}` 403 for everything, so no
  header-stripping proxy is needed. The same-Wi-Fi PIN phone mode (`/m`, `NEONCLIPPING_LAN=1`)
  is unchanged. New `/remote/status`, `/remote/login`, `/favicon.svg`.
- **`app.py`**: binds `0.0.0.0` when remote is on and prints the token link at startup;
  `POST /bundles/all` downloads every finished clip as one ZIP.

## B — YouTube reliability
- **`ytdl.py`**: friendly, specific errors for bot check, 429, private, members-only,
  age-gated, live/premiere, geo-blocked, removed (`fail_kind`, `human_error`); more player
  clients in the fallback chain (`tv, web_safari / ios / mweb / android / web_embedded`);
  `BlockedError` with a Colab-aware guide.
- **`app.py`**: `POST /ytdl/cookies` (validated Netscape cookies.txt upload, never logged) and
  `/ytdl/cookies/clear`; upload cap `NEONCLIPPING_MAX_UPLOAD_GB` (default 10 GB) with clear
  errors; the MP4 path is first-class in the new UI (drag-and-drop drop zone).

## C — Better moment detection
- **`captions.py`**: speech model picked by free VRAM (`pick_model`: large-v3-turbo ≥6 GB free
  — a T4 gets turbo; small ≥2.5 GB; base/small on CPU; env `NEONCLIPPING_ASR_MODEL` override);
  transcript cache keyed by file hash (name+size+mtime) instead of the uuid stem, so re-imports
  reuse work.
- **`smart.py`**: richer LLM rubric — hook strength in the first 3 s, emotional peak,
  self-contained, payoff, works-alone — blended into the score; asks for title, hook, 3
  hashtags and verbatim evidence; JSON answer validated with one retry; unsupported hooks still
  fall back to a real transcript line.
- **`llm.py`**: Ollama model configurable (`ollama_model` in `data/llm.json`,
  `NEONCLIPPING_OLLAMA_MODEL`, `NEONCLIPPING_OLLAMA_URL`); Ollama calls retried once; exposed
  through `/llm/settings`.
- **`scenes.py`** (new): FFmpeg scene-cut + silence-gap detection with disk cache;
  `smart.find` snaps candidate boundaries to cuts, silences and sentence ends.
- **`curation.py`**: diversity window — picks stay ≥90 s apart unless there is nothing else.

## D — Auto-reframe
- **`autofollow.py`** (new): samples frames at 2 fps, finds the speaker with the bundled YuNet
  detector, smooths with EMA + dead-zone + step limit, and compiles the path into per-frame
  FFmpeg crop expressions. CPU-only, no downloads.
- **`layouts.py`**: new `auto` ("Auto-follow speaker") layout; falls back to a centre crop when
  no face is found. `render_graph` takes the source (and size) for tracked crops.

## E — Captions
- **`captions.py`**: new `karaoke` animation (ASS `\k` fill); optional rule-based emoji
  (`caption_emoji`); bundled-font support.
- **`emojis.py`** (new): local rule table, URL-safe, hook + caption modes.
- **`fonts.py`** (new): Anton + Montserrat (SIL OFL) bundling via `python fonts.py --download`;
  `fontsdir` is passed to libass so captions look right on bare Linux/Colab.

## F — Quality, safety, robustness
- **`app.py`**: cooperative job cancellation (`Cancelled`, `cancel_job`, `run_ffmpeg` kills the
  active ffmpeg) — `POST /make/{job}/cancel`, `/export/{job}/cancel`; started/elapsed on jobs;
  upload cap; path/id validation kept strict everywhere.
- **`reedit.py`**: `POST /clips/{file}/delete` (removes mp4/srt/ass/vtt/json/cover and drops
  the card from its batch); cancel support in saved-edit batches.
- **`maintenance.py`** (new): `GET /data/usage`, `POST /data/cleanup` (sources/previews/temp/
  bundles/failed, optional keep-newer-than-days), hourly TTL sweeper
  (`NEONCLIPPING_SOURCE_TTL_DAYS`, default 14; clips never touched).
- **`updater.py`**: fork-safe — in-app updates off unless `NEONCLIPPING_UPDATE_REPO` is set
  (a fork can no longer overwrite itself with upstream code).
- **`report.py`**: issue-repo configurable (`NEONCLIPPING_ISSUE_REPO`).
- **`tests/conftest.py`, `tests/test_smoke.py`** (new): 23 pytest smoke tests that need no GPU
  and no internet — synthetic 10 s video → highlight finder, scene/silence detection, caption
  writer (all styles + karaoke + emoji), every layout graph, autofollow expressions, upload →
  export render, full make-clips run (speech faked), cancel, library + ZIP bundle, cookies
  validation, data cleanup, remote-token gate.
- **`scripts/selftest.py`** (new): PASS/FAIL for Python, FFmpeg, ffprobe, yt-dlp,
  faster-whisper, OpenCV, YuNet model, Ollama, GPU, NVENC, fonts, fontconfig.
- **`.github/workflows/ci.yml`** (new): smoke job (ubuntu, no GPU/network) + unit matrix
  (ubuntu/windows/macos). **`test.yml`**: fixed the broken `branches: ain]` trigger; now only
  runs the real installer job.
- **`requirements.txt`**: bounded pins; live extras split to `requirements-live.txt`; GPU
  helper `requirements-gpu.txt`.
- Updated affected unit tests (`test_phone`, `test_report`, `test_updater`, `test_reedit`,
  `test_curation`) to the new APIs; 213 unit tests + 23 smoke tests pass.

## G — Front-end redesign (index.html, style.css, app.js, editor.js — all new)
- Light / Dark / System theme with CSS design tokens on `:root`/`[data-theme=dark]`, pre-paint
  inline script (no flash), localStorage in try/catch, live `prefers-color-scheme`, synced
  `<meta name="color-scheme">` + `theme-color`, ~150 ms transitions (off under
  reduced-motion). No CDN fonts — the old Google-Fonts `@import` is gone; system font stack.
- 3-step Create flow (Add video → Choose style → Results), link validation as you type,
  drag-and-drop upload with progress, one big "Make clips" button (sticky mobile bar).
- Live progress: stage, percent, ETA, working Cancel; clip cards appear one by one with
  skeleton placeholders; "Why this clip?" breakdown (axes + rubric + weights), 5-star rating,
  hashtags, MP4/SRT download, Edit, Delete; bulk select + "Download all as ZIP".
- Library with search + sort (newest/best), delete with confirm, bulk ZIP/delete.
- Box-drawing editor with pointer events (touch works), facecam detect, 9:16 live preview,
  safe-zone overlay, editable word list (text/timings), shift-all, hook suggest.
- Live panel (hype meter, clip-now, stop, reattach after reload), Creators, Queue, Settings
  drawer (readiness list, Ollama model, cookies.txt upload, remote status, data usage +
  cleanup, AI parts, report, about).
- Accessibility: semantic HTML, labelled inputs, visible focus rings, aria-live for progress
  and errors, 44 px touch targets, `100dvh`, safe-area insets, no horizontal scroll at 360 px,
  reduced-motion support, keyboard navigation (Escape closes the drawer).
- Polling with progressive backoff (0.8 s → 3 s), offline banner + auto-retry, server-restart
  handling.
- Old `app.js`/`extras.js`/`style.css`/`transcripts.js`/`vision.js`/`report.js` replaced by the
  four new files; `mobile.html` (PIN phone page) kept.

## H — Docs
- README rewritten (Colab badge, install, env-var table, troubleshooting, honest limits,
  credits). This changelog. `LICENSE` carries the second copyright line.
  `fonts/LICENCE.md` for the OFL fonts.
