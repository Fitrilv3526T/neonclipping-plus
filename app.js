/* NeonClipping Plus - core interface.
   Plain JavaScript, no build step, no external resources. Everything dynamic is built here;
   the box editor / preview / word editor live in editor.js and mount into this page. */
'use strict';

/* ---------------------------------------------------------------- helpers */
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;            // only used with our own strings
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids) if (kid) node.append(kid);
  return node;
};
const fmtBytes = (b) => {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  return (b / 1024 ** Math.min(4, Math.floor(Math.log(b) / Math.log(1024)))).toFixed(b > 1024 ** 2 ? 0 : 1) + ' ' + u[Math.min(4, Math.floor(Math.log(b) / Math.log(1024)))];
};
const fmtTime = (s) => {
  s = Math.max(0, Math.round(s));
  return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
};
const fmtStamp = (t) => {
  t = Math.max(0, t);
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = Math.floor(t % 60);
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

function toast(message, kind = '', ms = 4200) {
  const box = $('toasts');
  const t = el('div', { class: 'toast ' + kind, role: 'status', text: message });
  box.append(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, ms);
}

function confirmDialog(text, confirmLabel = 'Delete') {
  return new Promise((resolve) => {
    const back = el('div', { class: 'modal-back' });
    const modal = el('div', { class: 'modal', role: 'alertdialog', 'aria-label': text },
      el('p', { text }),
      el('div', { class: 'actions' },
        el('button', { class: 'btn ghost', type: 'button', text: 'Keep it', onclick: () => { back.remove(); resolve(false); } }),
        el('button', { class: 'btn danger', type: 'button', text: confirmLabel, onclick: () => { back.remove(); resolve(true); } })));
    back.append(modal);
    back.addEventListener('click', (e) => { if (e.target === back) { back.remove(); resolve(false); } });
    document.body.append(back);
    modal.querySelector('button.btn.danger').focus();
  });
}

/* ---------------------------------------------------------------- theme */
const Theme = {
  pref: 'system',
  apply() {
    const dark = this.pref === 'dark' || (this.pref === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.documentElement.dataset.themePref = this.pref;
    $('meta-theme').setAttribute('content', dark ? '#0e0c16' : '#f4f4f8');
    document.querySelectorAll('[data-theme-set]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.themeSet === this.pref)));
  },
  set(pref) {
    this.pref = pref;
    try { localStorage.setItem('nc-theme', pref); } catch (e) { /* private mode */ }
    this.apply();
  },
  init() {
    try { this.pref = localStorage.getItem('nc-theme') || 'system'; } catch (e) { /* keep default */ }
    if (!['light', 'dark', 'system'].includes(this.pref)) this.pref = 'system';
    this.apply();
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (this.pref === 'system') this.apply(); });
    document.querySelectorAll('[data-theme-set]').forEach((b) => b.addEventListener('click', () => this.set(b.dataset.themeSet)));
  },
};

/* ---------------------------------------------------------------- api client (with backoff + reconnect banner) */
const Api = {
  online: true,
  consecutiveFails: 0,
  mark(ok) {
    if (ok) {
      if (!this.online) { this.online = true; $('conn-banner').hidden = true; }
      this.consecutiveFails = 0;
    } else if (++this.consecutiveFails >= 2 && this.online) {
      this.online = false;
      $('conn-banner').hidden = false;
    }
  },
  async raw(path, options, timeoutMs = 30000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try { return await fetch(path, { ...options, signal: ctrl.signal }); }
    finally { clearTimeout(timer); }
  },
  async request(path, body, { timeoutMs = 60000 } = {}) {
    const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    let response;
    try { response = await this.raw(path, options, timeoutMs); } catch (e) { this.mark(false); throw new OfflineError(); }
    this.mark(true);
    let data = null;
    try { data = await response.json(); } catch (e) { data = {}; }
    if (!response.ok) throw new ApiError(data.detail || data.error || `Request failed (${response.status})`, response.status);
    return data;
  },
};
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
class OfflineError extends Error {
  constructor() { super('Cannot reach the NeonClipping app. It may have stopped - check its window. Retrying happens automatically.'); }
}

/* poll a job endpoint with gentle backoff (0.8s -> 3s) until it leaves "running" */
async function pollJob(path, onTick, { cancelled = () => false } = {}) {
  let delay = 800;
  for (;;) {
    if (cancelled()) return null;
    let row;
    try { row = await Api.request(path); } catch (e) {
      if (e instanceof OfflineError) { await sleep(Math.min(delay, 4000)); continue; }   // server restarting: keep trying
      if (e.status === 404) throw e;                                                     // gone for good
      await sleep(delay); delay = Math.min(3000, delay * 1.4); continue;
    }
    if (onTick) onTick(row);
    if (row && !['running', 'queued', 'starting', 'recording'].includes(row.status)) return row;
    await sleep(delay);
    delay = Math.min(3000, delay * 1.35);
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------------------------------------------------------- app state */
const S = {
  source: null,          // {id, w, h, duration} of the uploaded/downloaded video
  sourceId: null,
  layout: 'blur',
  captionStyle: 'gta',
  preset: null,
  cam: null, game: null,
  makeJob: null,
  makeCount: 4,
  running: false,
  library: [],
  makeClips: [],
  picked: new Set(),
  liveId: null,
};

/* ---------------------------------------------------------------- tabs */
function showView(name) {
  for (const v of ['create', 'library', 'live', 'creators']) {
    $(`view-${v}`).hidden = v !== name;
    document.querySelector(`.tab[data-view="${v}"]`).setAttribute('aria-selected', String(v === name));
  }
  if (name === 'library') loadLibrary();
  if (name === 'creators') Creators.refresh();
  if (name === 'live') Queue.refresh();
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  document.body.classList.toggle('has-sticky', name === 'create');
}

/* ---------------------------------------------------------------- readiness */
async function loadReadiness() {
  try {
    const r = await Api.request('/readiness');
    const bad = r.items.filter((i) => !i.ok);
    const dot = $('ready-dot');
    dot.classList.toggle('bad', bad.length > 0);
    dot.classList.toggle('warn', bad.length > 0 && r.ok);
    $('ready-label').textContent = bad.length ? `${bad.length} to check` : 'All systems go';
    const list = $('ready-list');
    list.replaceChildren();
    for (const item of r.items) {
      list.append(el('div', { class: 'item ' + (item.ok ? 'ok' : 'bad') },
        el('span', { class: 'mark', text: item.ok ? '✔' : '✖' }),
        el('span', {}, el('b', { text: item.label + ' ' }), el('span', { text: item.detail }),
          item.ok ? '' : el('span', { class: 'fix', text: item.fix || '' }))));
    }
  } catch (e) { $('ready-label').textContent = 'offline'; }
}

/* ---------------------------------------------------------------- URL validation */
const URL_RE = /^https?:\/\/(www\.|m\.)?(youtube\.com\/watch|youtube\.com\/shorts|youtu\.be\/|twitch\.tv\/|clips\.twitch\.tv\/|kick\.com\/)/i;
function validateUrl() {
  const input = $('url');
  const msg = $('url-msg');
  const v = input.value.trim();
  input.setCustomValidity('');
  if (!v) { msg.textContent = ''; msg.className = 'inline-msg'; input.removeAttribute('aria-invalid'); return false; }
  if (URL_RE.test(v)) { msg.textContent = 'Link looks good.'; msg.className = 'inline-msg ok'; input.removeAttribute('aria-invalid'); return true; }
  msg.textContent = 'Paste a full YouTube, Twitch or Kick link - or upload the MP4 below.';
  msg.className = 'inline-msg err';
  input.setAttribute('aria-invalid', 'true');
  return false;
}

/* ---------------------------------------------------------------- import (link) */
async function importLink() {
  const url = $('url').value.trim();
  if (!validateUrl()) return;
  const btn = $('btn-import');
  btn.disabled = true;
  $('import-msg').className = 'inline-msg';
  $('import-msg').textContent = 'Starting…';
  const body = { url, source_height: Number($('srcq').value) };
  const ps = parseStamp($('part-start').value), pe = parseStamp($('part-end').value);
  if (ps !== null && pe !== null) { body.start = ps; body.end = pe; }
  try {
    const r = await Api.request('/link', body);
    $('import-msg').textContent = 'Frames arrive first; the full download continues in the background.';
    const done = await pollJob(`/status/${r.job}`, (row) => {
      if (row.stage) $('import-msg').textContent = row.stage;
      if (row.progress) { $('import-progress-wrap').hidden = false; $('import-progress').style.width = Math.round(row.progress * 100) + '%'; }
      if (row.preview_frames) renderFrames(row);
      if (row.status === 'needs_range') {
        $('import-msg').textContent = row.message;
        $('part-start').focus();
        throw new ApiError(row.message);
      }
    });
    $('import-progress-wrap').hidden = true;
    if (done && done.status === 'done') {
      S.source = { id: done.id, w: done.w, h: done.h, duration: done.duration };
      S.sourceId = done.id;
      $('src-ready-note').hidden = false;
      $('src-ready-note').textContent = `Source ready · ${done.w}×${done.h} · ${fmtTime(done.duration)}`;
      $('import-msg').className = 'inline-msg ok';
      $('import-msg').textContent = 'Video ready. Choose your style and press Make clips.';
      if (window.Editor) Editor.attachSource(S.source);
      toast('Video downloaded and ready.');
    }
  } catch (e) {
    $('import-msg').className = 'inline-msg err';
    $('import-msg').textContent = friendly(e);
  } finally { btn.disabled = false; }
}
function parseStamp(text) {
  if (!text || !text.trim()) return null;
  const parts = text.trim().split(':').map(Number);
  if (parts.some((n) => Number.isNaN(n))) return null;
  return parts.reduce((a, b) => a * 60 + b, 0);
}
function renderFrames(row) {
  const box = $('frames');
  box.replaceChildren();
  for (const f of row.preview_frames || []) {
    box.append(el('button', {
      class: 'layout-card', type: 'button', title: `Jump to ${fmtStamp(f.time)}`,
      onclick: () => { if (window.Editor) { Editor.open(); Editor.seekTo(f.time); } },
    }, el('img', { src: f.url, alt: `Preview frame at ${fmtStamp(f.time)}`, loading: 'lazy', style: 'width:100%;border-radius:8px;aspect-ratio:16/9;object-fit:cover' })));
  }
}

/* ---------------------------------------------------------------- upload (file, with progress) */
function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    $('import-progress-wrap').hidden = false;
    $('import-progress').style.width = '4%';
    $('import-msg').className = 'inline-msg';
    $('import-msg').textContent = `Uploading ${file.name} (${fmtBytes(file.size)})…`;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) $('import-progress').style.width = Math.max(4, Math.round(e.loaded / e.total * 96)) + '%';
    };
    xhr.onload = () => {
      $('import-progress').style.width = '100%';
      setTimeout(() => { $('import-progress-wrap').hidden = true; }, 600);
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status === 200) resolve(data);
      else reject(new ApiError(data.detail || 'The upload failed.', xhr.status));
    };
    xhr.onerror = () => reject(new OfflineError());
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}
async function handleFile(file) {
  if (!file) return;
  try {
    const info = await uploadFile(file);
    S.source = info; S.sourceId = info.id;
    $('src-ready-note').hidden = false;
    $('src-ready-note').textContent = `Source ready · ${info.w}×${info.h} · ${fmtTime(info.duration)}`;
    $('import-msg').className = 'inline-msg ok';
    $('import-msg').textContent = `${file.name} ready. Choose your style and press Make clips.`;
    if (window.Editor) Editor.attachSource(S.source);
    toast('Video uploaded.');
  } catch (e) {
    $('import-msg').className = 'inline-msg err';
    $('import-msg').textContent = friendly(e);
  }
}

/* ---------------------------------------------------------------- settings -> make/export payload */
const LANGUAGES = [["ur", "Urdu"], ["bn", "Bengali"], ["ta", "Tamil"], ["te", "Telugu"], ["mr", "Marathi"], ["gu", "Gujarati"], ["kn", "Kannada"], ["ml", "Malayalam"], ["pa", "Punjabi"], ["ne", "Nepali"], ["si", "Sinhala"], ["ar", "Arabic"], ["fa", "Persian"], ["he", "Hebrew"], ["tr", "Turkish"], ["ru", "Russian"], ["uk", "Ukrainian"], ["pl", "Polish"], ["cs", "Czech"], ["sk", "Slovak"], ["hu", "Hungarian"], ["ro", "Romanian"], ["bg", "Bulgarian"], ["sr", "Serbian"], ["hr", "Croatian"], ["el", "Greek"], ["de", "German"], ["fr", "French"], ["es", "Spanish"], ["pt", "Portuguese"], ["it", "Italian"], ["nl", "Dutch"], ["ca", "Catalan"], ["sv", "Swedish"], ["da", "Danish"], ["no", "Norwegian"], ["fi", "Finnish"], ["is", "Icelandic"], ["id", "Indonesian"], ["ms", "Malay"], ["tl", "Filipino"], ["vi", "Vietnamese"], ["th", "Thai"], ["zh", "Chinese"], ["ja", "Japanese"], ["ko", "Korean"], ["sw", "Swahili"], ["af", "Afrikaans"], ["am", "Amharic"], ["az", "Azerbaijani"]];

function gatherSettings() {
  const b = {
    layout: S.layout, caption_style: S.captionStyle,
    caption_anim: $('caption-anim').value, caption_y: Number($('caption-y').value),
    caption_emoji: $('caption-emoji').checked,
    mode: $('mode').value, accurate: $('accurate').value, language: $('language').value || null,
    aspect: $('aspect').value, resolution: Number($('resolution').value), fps: Number($('fps').value),
    count: Number($('count').value), min_len: Number($('minlen').value), max_len: Number($('length').value),
    hook_enabled: $('hook-enabled').checked, hook_style: $('hook-style').value,
    hook_duration: Number($('hook-duration').value), fade_inout: $('fade-inout').checked,
    focus_brief: $('focus-brief').value,
    cam: S.cam || { x: 0, y: 0, w: .25, h: .3 }, game: S.game || { x: .25, y: 0, w: .75, h: 1 },
  };
  if (window.Extras) Object.assign(b, Extras.styleFields());
  if (S.preset) applyPresetTo(b, S.preset);
  return b;
}
function applyPresetTo(body, preset) {
  const map = {
    tl: { cam: { x: 0, y: 0, w: .25, h: .3 }, game: { x: .25, y: 0, w: .75, h: 1 } },
    tr: { cam: { x: .75, y: 0, w: .25, h: .3 }, game: { x: 0, y: 0, w: .75, h: 1 } },
    bl: { cam: { x: 0, y: .7, w: .25, h: .3 }, game: { x: .25, y: 0, w: .75, h: 1 } },
    br: { cam: { x: .75, y: .7, w: .25, h: .3 }, game: { x: 0, y: 0, w: .75, h: 1 } },
    bc: { cam: { x: .375, y: .68, w: .25, h: .3 }, game: { x: 0, y: 0, w: 1, h: .68 } },
  };
  if (preset === 'none') { body.cam = { x: 0, y: 0, w: .25, h: .3 }; body.game = { x: 0, y: 0, w: 1, h: 1 }; return; }
  const p = map[preset];
  if (p) { body.cam = p.cam; body.game = p.game; }
}

/* ---------------------------------------------------------------- layouts + caption styles */
const LAYOUTS = [
  ['auto', 'Auto-follow speaker', 'The crop follows the speaker'],
  ['stack', 'Face + gameplay', 'Camera above the action'],
  ['bottom', 'Gameplay + face', 'Camera below the action'],
  ['blur', 'Blurred fill', 'Keep the whole frame'],
  ['fill', 'Fill screen', 'Bold centre crop'],
  ['game', 'Gameplay only', 'Your selected area'],
  ['pip_tl', 'Camera top-left', 'Floating facecam'],
  ['pip_tr', 'Camera top-right', 'Floating facecam'],
  ['pip_bl', 'Camera bottom-left', 'Floating facecam'],
  ['pip_br', 'Camera bottom-right', 'Floating facecam'],
  ['circle', 'Circle camera', 'Round facecam'],
  ['side', 'Side by side', 'Two columns'],
];
function renderLayouts() {
  const box = $('layouts');
  box.replaceChildren();
  for (const [id, name, desc] of LAYOUTS) {
    box.append(el('button', { class: 'layout-card', type: 'button', 'aria-pressed': String(S.layout === id), onclick: () => { S.layout = id; renderLayouts(); if (window.Editor) Editor.drawPreview(); } },
      el('span', { class: 'layout-thumb', 'aria-hidden': 'true', html: layoutThumb(id) }),
      el('b', { text: name }), el('small', { text: desc })));
  }
}
function layoutThumb(id) {
  const cam = (x, y, w, h) => `<i class="t-cam" style="left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%"></i>`;
  const box = (x, y, w, h) => `<i style="left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%;background:var(--text-faint);opacity:.35"></i>`;
  switch (id) {
    case 'auto': return cam(.35, .15, .3, .6) + `<i style="left:30%;top:0;width:40%;height:100%;background:transparent;border:2px solid var(--accent);border-radius:3px"></i>`;
    case 'stack': return cam(0, 0, 1, .34) + box(0, .34, 1, .66);
    case 'bottom': return box(0, 0, 1, .66) + cam(0, .66, 1, .34);
    case 'blur': return `<i style="inset:0;opacity:.25"></i><i class="t-cam" style="left:18%;top:12%;width:64%;height:76%;opacity:.45"></i>`;
    case 'fill': return `<i class="t-cam" style="left:30%;top:0;width:40%;height:100%;opacity:.35"></i>`;
    case 'game': return box(0, 0, 1, 1);
    case 'pip_tl': return box(0, 0, 1, 1) + cam(.05, .08, .3, .3);
    case 'pip_tr': return box(0, 0, 1, 1) + cam(.65, .08, .3, .3);
    case 'pip_bl': return box(0, 0, 1, 1) + cam(.05, .6, .3, .3);
    case 'pip_br': return box(0, 0, 1, 1) + cam(.65, .6, .3, .3);
    case 'circle': return box(0, .3, 1, .7) + `<i class="t-cam" style="left:32%;top:4%;width:36%;height:26%;border-radius:50%"></i>`;
    case 'side': return cam(0, 0, .4, 1) + box(.4, 0, .6, 1);
    default: return '';
  }
}
const CAPTION_STYLES = [
  ['gta', 'GTA Neon', 'Pink / cyan glow', 'THIS IS <strong>WILD</strong>', 'gta'],
  ['bold', 'Impact', 'Bold active word', 'THIS IS <strong>WILD</strong>', 'bold'],
  ['hormozi', 'Hormozi', 'White + yellow pop', 'THIS IS <strong>WILD</strong>', 'hormozi'],
  ['mrbeast', 'MrBeast', 'Yellow + orange', 'THIS IS <strong>WILD</strong>', 'mrbeast'],
  ['clean', 'Clean', 'Quiet subtitles', 'This is wild.', 'clean'],
];
function renderCaptionStyles() {
  const box = $('caption-styles');
  box.replaceChildren();
  for (const [id, name, desc, demo, cls] of CAPTION_STYLES) {
    box.append(el('button', { class: 'cap-card', type: 'button', 'aria-pressed': String(S.captionStyle === id), onclick: () => { S.captionStyle = id; renderCaptionStyles(); if (window.Editor) Editor.drawPreview(); } },
      el('span', { class: 'demo ' + cls, html: demo, 'aria-hidden': 'true' }),
      el('b', { text: name }), el('small', { text: desc })));
  }
}
function renderPresets() {
  document.querySelectorAll('#presets .chip').forEach((chip) => {
    chip.setAttribute('aria-pressed', String(S.preset === chip.dataset.preset));
    chip.addEventListener('click', () => { S.preset = chip.dataset.preset; renderPresets(); });
  });
}

/* ---------------------------------------------------------------- make clips */
async function makeClips() {
  if (!S.sourceId && !$('url').value.trim()) {
    if (!validateUrl()) { toast('Add a video link or upload an MP4 first.', 'err'); $('url').focus(); return; }
  }
  const settings = gatherSettings();
  settings.count = Math.max(1, Math.min(12, Number($('count').value) || 4));
  S.makeCount = settings.count;
  if ($('url').value.trim() && URL_RE.test($('url').value.trim())) settings.url = $('url').value.trim();
  else if (S.sourceId) settings.id = S.sourceId;
  else { toast('Add a video first.', 'err'); return; }

  $('results-card').hidden = false;
  $('make-progress').hidden = false;
  $('btn-cancel').hidden = false;
  $('make-grid').replaceChildren(...skeletons(settings.count));
  $('make-bulk').hidden = true;
  S.makeClips = []; S.picked = new Set();
  S.running = true;
  $('btn-make').disabled = true; $('btn-make-mobile').disabled = true;
  const started = performance.now();
  document.body.classList.add('has-sticky');
  $('results-card').scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const r = await Api.request('/make', settings, { timeoutMs: 120000 });
    S.makeJob = r.job;
    const final = await pollJob(`/make/${r.job}`, (row) => updateMakeProgress(row, started));
    if (!final) return;
    updateMakeProgress(final, started);
    if (final.status === 'done') {
      if (!final.clips || !final.clips.length) {
        $('make-stage').textContent = final.stage || 'No moments matched. Broaden the focus or required phrases.';
        $('make-grid').replaceChildren(emptyState('No clips this time', 'Try again with a broader focus, longer max length, or the Fast mode.'));
      }
      toast(final.clips ? `${final.clips.length} clip(s) ready.` : 'Finished.', 'ok');
    } else if (final.status === 'canceled') {
      toast('Canceled. Clips finished before the cancel are kept.', '');
    } else {
      $('make-stage').textContent = 'Something failed.';
      toast(friendly(new ApiError(final.error || 'The job failed.')), 'err', 8000);
    }
  } catch (e) {
    toast(friendly(e), 'err', 8000);
    $('make-stage').textContent = 'Failed.';
  } finally {
    S.running = false; S.makeJob = null;
    $('btn-cancel').hidden = true;
    $('btn-make').disabled = false; $('btn-make-mobile').disabled = false;
    $('make-fill').style.width = '100%';
    loadLibrary(false);
  }
}
function skeletons(n) {
  return Array.from({ length: n }, () => el('div', { class: 'skeleton', 'aria-hidden': 'true' },
    el('div', { class: 'sk-thumb sk-anim' }),
    el('div', { class: 'sk-line sk-anim' }), el('div', { class: 'sk-line short sk-anim' }),
    el('div', { class: 'sk-block sk-anim' })));
}
function updateMakeProgress(row, started) {
  if (row.stage) $('make-stage').textContent = row.stage;
  const p = Math.max(0, Math.min(1, Number(row.progress) || 0));
  $('make-fill').style.width = Math.round(p * 100) + '%';
  $('make-track').setAttribute('aria-valuenow', String(Math.round(p * 100)));
  const elapsed = (performance.now() - started) / 1000;
  if (p > 0.04 && p < 0.995) $('make-eta').textContent = `about ${fmtTime(elapsed / p - elapsed)} left`;
  else $('make-eta').textContent = row.elapsed_seconds ? `took ${fmtTime(row.elapsed_seconds)}` : '';
  if (Array.isArray(row.clips)) {
    const grid = $('make-grid');
    const seen = new Set(S.makeClips.map((c) => c.file));
    for (const clip of row.clips) {
      if (seen.has(clip.file)) continue;
      S.makeClips.push(clip);
      grid.append(clipCard(clip, 'make'));
    }
    // skeleton placeholders for the clips still rendering
    grid.querySelectorAll('.skeleton').forEach((s) => s.remove());
    if (['running', 'queued'].includes(row.status)) {
      const remaining = Math.max(0, S.makeCount - row.clips.length);
      if (remaining) grid.append(...skeletons(remaining));
    } else if (S.makeClips.length > 1) {
      $('make-bulk').hidden = false;
    }
  }
}
async function cancelMake() {
  if (!S.makeJob) return;
  if (!(await confirmDialog('Stop this job? Clips already finished are kept.', 'Stop it'))) return;
  try {
    await Api.request(`/make/${S.makeJob}/cancel`, {});
    $('make-stage').textContent = 'Canceling…';
  } catch (e) { toast(friendly(e), 'err'); }
}

/* ---------------------------------------------------------------- clip cards */
function clipCard(clip, context) {
  const file = clip.file;
  const card = el('article', { class: 'clip-card', 'data-file': file });
  const score = Math.round(clip.viral ?? clip.score ?? 0);
  const thumb = el('div', { class: 'thumb', role: 'button', tabindex: '0', 'aria-label': `Play ${clip.title || 'clip'}` },
    el('span', { class: 'badge', text: `⚡ ${score}` }),
    el('span', { class: 'pick' }, el('input', { type: 'checkbox', 'aria-label': 'Select clip', onchange: (e) => { if (e.target.checked) S.picked.add(file); else S.picked.delete(file); updatePickCount(context); } })),
    el('video', { src: `/media/${file}`, preload: 'metadata', playsinline: '', controls: 'true', 'aria-label': (clip.title || 'clip') + ' preview' }),
  );
  const why = whyBlock(clip);
  const actions = el('div', { class: 'clip-actions' },
    el('a', { class: 'btn small', href: `/media/${file}`, download: `neonclipping-${file}.mp4` }, 'MP4'),
    clip.srt ? el('a', { class: 'btn small', href: clip.srt, download: '' }, 'SRT') : '',
    el('button', { class: 'btn small', type: 'button', text: 'Edit', onclick: () => window.Editor && Editor.openSavedClip(file, context) }),
    el('button', { class: 'btn small danger', type: 'button', text: 'Delete', onclick: () => deleteClip(file) }),
  );
  card.append(thumb, el('div', { class: 'body' },
    el('h3', { text: clip.title || 'Clip' }),
    clip.hook ? el('p', { class: 'hookline', text: clip.hook }) : '',
    (clip.hashtags && clip.hashtags.length) ? el('div', { class: 'tags' }, ...clip.hashtags.map((t) => el('span', { text: '#' + t }))) : '',
    starsBlock(file, clip.rating),
    why || '',
    actions));
  return card;
}
function starsBlock(file, rating) {
  const box = el('div', { class: 'stars', role: 'group', 'aria-label': 'Rate this clip' });
  for (let n = 1; n <= 5; n++) {
    box.append(el('button', { class: n <= (rating || 0) ? 'on' : '', type: 'button', 'aria-label': `${n} star${n > 1 ? 's' : ''}`,
      onclick: async () => {
        try {
          await Api.request('/feedback', { file, rating: n });
          box.querySelectorAll('button').forEach((b, i) => b.classList.toggle('on', i < n));
          toast('Thanks! Your ratings teach the picker what you like.', 'ok');
        } catch (e) { toast(friendly(e), 'err'); }
      } }, '★'));
  }
  return box;
}
function whyBlock(clip) {
  const axes = clip.axes || {};
  const names = { energy: 'Energy', action: 'Action', words: 'Words', ai: 'AI', chat: 'Chat' };
  const rows = Object.entries(axes).filter(([, v]) => v !== null && v !== undefined);
  if (!rows.length && !clip.rubric) return '';
  const why = el('details', { class: 'why' },
    el('summary', { style: 'cursor:pointer;font-weight:600', text: 'Why this clip?' }),
    el('div', {}, clip.reason ? el('p', { style: 'margin-bottom:6px', text: clip.reason }) : '',
      ...rows.map(([k, v]) => el('div', { class: 'bar-row' },
        el('span', { class: 'lbl', text: names[k] || k }),
        el('span', { class: 'bar' }, el('i', { style: `width:${Math.round(v)}%` })),
        el('span', { class: 'val', text: String(Math.round(v)) })))),
    rubricRows(clip.rubric),
    clip.why_weights ? el('p', { class: 'weights', text: 'Weights: ' + Object.entries(clip.why_weights).slice(0, 4).map(([k, v]) => `${k} ${Math.round(v * 100)}`).join(' · ') }) : '',
  );
  return why;
}
function rubricRows(rubric) {
  if (!rubric || !Object.keys(rubric).length) return '';
  const names = { hook_strength: 'First 3 s hook', emotional_peak: 'Emotional peak', self_contained: 'Self-contained', payoff: 'Payoff', standalone: 'Works alone' };
  return el('div', { style: 'margin-top:6px;border-top:1px solid var(--border);padding-top:6px' },
    ...Object.entries(rubric).map(([k, v]) => el('div', { class: 'bar-row' },
      el('span', { class: 'lbl', text: names[k] || k }),
      el('span', { class: 'bar' }, el('i', { style: `width:${Math.round(v * 10)}%` })),
      el('span', { class: 'val', text: String(v) }))));
}
function emptyState(title, hint) {
  return el('div', { class: 'empty', style: 'grid-column:1/-1' },
    el('svg', { width: '44', height: '44', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'aria-hidden': 'true',
      html: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10.5 21 8v8l-5-2.5"/>' }),
    el('b', { text: title }), el('span', { text: hint }));
}

/* ---------------------------------------------------------------- library */
async function loadLibrary(notify = true) {
  try {
    S.library = await Api.request('/library');
    $('lib-count').hidden = !S.library.length;
    $('lib-count').textContent = S.library.length;
    renderLibrary();
  } catch (e) { if (notify) toast(friendly(e), 'err'); }
}
function renderLibrary() {
  const grid = $('library-grid');
  const q = ($('lib-search').value || '').toLowerCase();
  const sort = $('lib-sort').value;
  let rows = S.library.filter((r) => !q || `${r.title} ${r.settings.hook || ''} ${(r.settings.hashtags || []).join(' ')}`.toLowerCase().includes(q));
  rows.sort((a, b) => sort === 'best'
    ? (b.meta && b.meta.viral || b.settings && b.settings.score || 0) - (a.meta && a.meta.viral || a.settings && a.settings.score || 0)
    : (b.created || 0) - (a.created || 0));
  grid.replaceChildren();
  if (!rows.length) {
    grid.append(emptyState('No clips yet', 'Make your first clips in Create - they collect here.'));
    $('lib-bulk').hidden = true;
    return;
  }
  for (const r of rows) {
    const clip = {
      file: r.id, title: r.title, hook: r.settings.hook || '', score: r.meta && r.meta.viral, viral: r.meta && r.meta.viral,
      axes: r.meta && r.meta.axes, hashtags: r.settings.hashtags || [], srt: r.srt ? `/text/${r.id}/srt` : null,
      rating: r.settings.my_rating,
    };
    grid.append(clipCard(clip, 'library'));
  }
  $('lib-bulk').hidden = false;
  updatePickCount('library');
}
async function deleteClip(file) {
  if (!(await confirmDialog(`Delete this clip permanently? The MP4 and captions will be removed.`))) return;
  try {
    await Api.request(`/clips/${file}/delete`, {});
    toast('Clip deleted.', 'ok');
    loadLibrary(false);
  } catch (e) { toast(friendly(e), 'err'); }
}
async function bulkZip(endpoint, files) {
  try {
    toast('Preparing the ZIP…');
    const r = await Api.request(endpoint, files ? { files: [...files] } : {});
    location.href = r.download;
  } catch (e) { toast(friendly(e), 'err'); }
}
function updatePickCount(context) {
  if (context === 'make') { $('make-picked').textContent = `${S.picked.size} selected`; }
  else { $('lib-picked').textContent = `${S.picked.size} selected`; }
}

/* ---------------------------------------------------------------- live */
const Live = {
  async start() {
    const url = $('live-url').value.trim();
    if (!url) { toast('Paste a Twitch or YouTube Live link first.', 'err'); return; }
    try {
      const r = await Api.request('/live/start', { url, ...pickLiveOptions() });
      S.liveId = r.live;
      $('live-box').hidden = false;
      toast('Live session starting…');
      this.poll();
    } catch (e) { toast(friendly(e), 'err'); }
  },
  pickLiveOptions() {
    const b = { layout: S.layout, caption_style: S.captionStyle, language: $('language').value || null, captions: true };
    if (window.Extras) Object.assign(b, Extras.styleFields());
    return b;
  },
  async reattach() {
    try {
      const r = await Api.request('/live-active');
      if (r && r.live) { S.liveId = r.live; $('live-box').hidden = false; this.poll(); }
    } catch (e) { /* fine */ }
  },
  poll() {
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(async () => {
      if (!S.liveId) return;
      try {
        const st = await Api.request(`/live/${S.liveId}`);
        if (['stopped', 'ended', 'error'].includes(st.status)) {
          clearInterval(this.timer);
          toast(st.status === 'error' ? `Live session failed: ${st.error}` : 'Live session ended.', st.status === 'error' ? 'err' : 'ok');
        }
        this.render(st);
      } catch (e) { if (e.status === 404) { clearInterval(this.timer); } }
    }, 2500);
  },
  render(st) {
    $('live-hype-fill').style.width = `${st.hype || 0}%`;
    $('live-hype-label').textContent = st.hype_label || 'quiet';
    const stats = $('live-stats');
    stats.replaceChildren(
      stat('Elapsed', fmtTime(st.elapsed || 0)), stat('Messages / 30 s', String(st.msgs30 || 0)),
      stat('Chat', st.chat || '-'), stat('Clips cut', String((st.clips || []).length)),
      st.title ? stat('Stream', st.title.slice(0, 42)) : '',
    );
    const grid = $('live-grid');
    const seen = new Set([...grid.children].map((c) => c.dataset.file));
    for (const clip of st.clips || []) {
      if (clip.error || !clip.file || seen.has(clip.file)) continue;
      grid.append(clipCard(clip, 'make'));
    }
    function stat(name, value) { return el('div', { class: 'stat' }, el('b', { text: value }), el('small', { text: name })); }
  },
  async clipNow() {
    if (!S.liveId) return;
    try { await Api.request(`/live/${S.liveId}/clip-now`, { seconds: 30 }); toast('Cutting the last 30 s…'); } catch (e) { toast(friendly(e), 'err'); }
  },
  async stop() {
    if (!S.liveId) return;
    if (!(await confirmDialog('Stop the live session? Clips already made stay in your library.', 'Stop'))) return;
    try { await Api.request(`/live/${S.liveId}/stop`, {}); } catch (e) { toast(friendly(e), 'err'); }
    clearInterval(this.timer);
  },
};

/* ---------------------------------------------------------------- creators */
const Creators = {
  async refresh() {
    try {
      const r = await Api.request('/creators');
      const list = $('creator-list');
      list.replaceChildren();
      if (!r.creators.length) { list.append(el('p', { class: 'muted', text: 'No creators yet. Add a Twitch name or a YouTube channel above.' })); return; }
      for (const c of r.creators) {
        list.append(el('div', { class: 'creator-row' },
          el('div', { style: 'flex:1 1 140px' }, el('span', { class: 'name', text: c.name }), ' ', el('span', { class: 'platform', text: c.platform })),
          el('span', { class: `status-pill ${c.status}`, text: c.status }),
          el('span', { class: 'muted', text: `${c.clips} clips` }),
          el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: c.auto, onchange: (e) => Api.request(`/creators/${c.id}/auto`, { auto: e.target.checked }).catch((err) => toast(friendly(err), 'err')) }), 'Auto'),
          el('button', { class: 'btn small', type: 'button', text: 'Check', onclick: async () => { toast('Checking…'); const rr = await Api.request(`/creators/${c.id}/check`, {}); toast(rr.live ? `${c.name} is LIVE.` : `${c.name} is offline.`, rr.live ? 'ok' : ''); } }),
          el('button', { class: 'btn small', type: 'button', text: 'Use my layout', onclick: () => Api.request(`/creators/${c.id}/layout`, this.layoutOptions()).then(() => toast('Saved your current layout for this creator.', 'ok')).catch((err) => toast(friendly(err), 'err')) }),
          el('button', { class: 'btn small danger', type: 'button', text: 'Remove', onclick: () => Api.request(`/creators/${c.id}/remove`, {}).then(() => this.refresh()).catch((err) => toast(friendly(err), 'err')) })));
      }
    } catch (e) { toast(friendly(e), 'err'); }
  },
  layoutOptions() {
    const b = { layout: S.layout, caption_style: S.captionStyle, language: $('language').value || null };
    if (window.Extras) Object.assign(b, Extras.styleFields());
    return b;
  },
  async add() {
    const url = $('creator-url').value.trim();
    if (!url) return;
    try { await Api.request('/creators', { url, ...this.layoutOptions() }); $('creator-url').value = ''; toast('Creator added.', 'ok'); this.refresh(); }
    catch (e) { toast(friendly(e), 'err'); }
  },
};

/* ---------------------------------------------------------------- queue */
const Queue = {
  async refresh() {
    try {
      const r = await Api.request('/queue');
      const list = $('queue-list');
      list.replaceChildren();
      $('queue-pause').textContent = r.paused ? 'Resume queue' : 'Pause queue';
      if (!r.jobs.length) { list.append(el('p', { class: 'muted', text: 'The queue is empty.' })); return; }
      for (const j of r.jobs.slice(0, 20)) {
        list.append(el('div', { class: 'queue-row' },
          el('span', { class: 'kind', text: j.kind }),
          el('span', { class: 'state ' + j.status, text: j.status }),
          el('span', { class: 'muted', text: (j.result && (j.result.stage || j.result.error) || j.status).slice(0, 90) }),
          ['error', 'interrupted', 'canceled'].includes(j.status) ? el('button', { class: 'btn small', type: 'button', text: 'Retry', onclick: () => Api.request(`/queue/${j.id}/retry`, {}).then(() => this.refresh()) }) : '',
          j.status === 'queued' ? el('button', { class: 'btn small', type: 'button', text: 'Cancel', onclick: () => Api.request(`/queue/${j.id}/cancel`, {}).then(() => this.refresh()) }) : ''));
      }
    } catch (e) { /* quiet */ }
  },
  async add() {
    const urls = $('queue-urls').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!urls.length) { toast('Paste at least one video link.', 'err'); return; }
    const when = $('queue-time').value ? Date.parse($('queue-time').value) / 1000 : 0;
    try {
      for (const url of urls) await Api.request('/queue', { kind: 'make', payload: { url, ...gatherSettings() }, not_before: when });
      $('queue-urls').value = '';
      toast(`${urls.length} video(s) queued.`, 'ok');
      this.refresh();
    } catch (e) { toast(friendly(e), 'err'); }
  },
  async pause() {
    try {
      const r = await Api.request('/queue/pause', {});
      $('queue-pause').textContent = r.paused ? 'Resume queue' : 'Pause queue';
    } catch (e) { toast(friendly(e), 'err'); }
  },
};

/* ---------------------------------------------------------------- settings drawer */
const Drawer = {
  open() { $('drawer').hidden = false; $('drawer-back').hidden = false; this.load(); },
  close() { $('drawer').hidden = true; $('drawer-back').hidden = true; },
  async load() {
    loadReadiness();
    try {
      const llm = await Api.request('/llm/settings');
      $('llm-ollama-model').value = llm.ollama_model || '';
    } catch (e) { /* ignore */ }
    try {
      const y = await Api.request('/ytdl/settings');
      $('yt-browser').value = y.cookies_browser || '';
      $('cookies-note').textContent = y.cookie_file ? 'A cookies.txt file is active.' : 'No cookies.txt uploaded yet.';
    } catch (e) { /* ignore */ }
    try {
      const r = await Api.request('/remote/status');
      $('remote-note').textContent = r.enabled
        ? `Remote access is ON (port ${r.port}). The access link with the token was printed on the server console.`
        : 'Off. Start the app with NEONCLIPPING_REMOTE=1 to open it from any device with a token link.';
    } catch (e) { /* ignore */ }
    try {
      const u = await Api.request('/data/usage');
      const list = $('usage-list');
      list.replaceChildren();
      const rows = [['Sources', u.usage.sources], ['Clips', u.usage.clips], ['Previews', u.usage.previews], ['Transcripts', u.usage.transcripts], ['Bundles', u.usage.bundles]];
      for (const [name, v] of rows) {
        if (!v || !(v.bytes > 0)) continue;
        list.append(el('div', { class: 'usage-row' }, el('span', { text: name + (v.files !== undefined ? ` (${v.files})` : '') }), el('b', { text: fmtBytes(v.bytes) })));
      }
      list.append(el('div', { class: 'usage-row' }, el('span', { text: 'Total' }), el('b', { text: fmtBytes(u.usage.total.bytes) })));
      if (u.ttl_days > 0) list.append(el('p', { class: 'muted', style: 'font-size:12.5px', text: `Sources not used for ${u.ttl_days} days are deleted automatically.` }));
    } catch (e) { /* ignore */ }
    try {
      const rep = await Api.request('/report/info?note=');
      $('report-issue').href = rep.issue_url;
    } catch (e) { /* ignore */ }
    try {
      const sup = await Api.request('/supporters');
      if (sup.supporters && sup.supporters.length) {
        $('supporters-note').hidden = false;
        $('supporters-list').textContent = sup.supporters.map((s) => s.name).join(', ');
      }
    } catch (e) { /* ignore */ }
  },
};

/* ---------------------------------------------------------------- friendly errors */
function friendly(e) {
  const msg = String((e && e.message) || e);
  if (e instanceof OfflineError) return msg;
  if (/blocked this server|bot check|cookies\.txt|Upload the MP4/i.test(msg)) return msg + ' (Upload cookies.txt in Settings, or upload the MP4 file instead.)';
  if (/private|age-restricted|geo|live right now/i.test(msg)) return msg;
  return msg;
}

/* ---------------------------------------------------------------- boot */
function init() {
  Theme.init();
  renderLayouts(); renderCaptionStyles(); renderPresets();
  const more = $('lang-more');   // already rendered statically in index.html; fill only if missing
  if (!more.children.length) for (const [code, name] of LANGUAGES) more.append(el('option', { value: code, text: name }));

  // tabs
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', (e) => { e.preventDefault(); showView(t.dataset.view); }));
  const initial = (location.hash || '#create').slice(1);
  showView(['create', 'library', 'live', 'creators'].includes(initial) ? initial : 'create');

  // step 1
  $('url').addEventListener('input', validateUrl);
  $('url').addEventListener('paste', () => setTimeout(validateUrl, 0));
  $('url').addEventListener('keydown', (e) => { if (e.key === 'Enter') importLink(); });
  $('btn-import').addEventListener('click', importLink);
  const dz = $('dropzone');
  dz.addEventListener('click', () => $('file').click());
  dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });
  $('file').addEventListener('change', (e) => handleFile(e.target.files[0]));
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', (e) => handleFile(e.dataTransfer.files[0]));

  // step 2
  $('btn-make').addEventListener('click', makeClips);
  $('btn-make-mobile').addEventListener('click', makeClips);
  $('btn-find').addEventListener('click', () => { window.Editor && Editor.previewHighlights(); });
  $('btn-open-editor').addEventListener('click', () => window.Editor && Editor.open());
  $('btn-close-editor').addEventListener('click', () => { $('editor-card').hidden = true; });

  // results
  $('btn-cancel').addEventListener('click', cancelMake);
  $('make-select-all').addEventListener('change', (e) => {
    document.querySelectorAll('#make-grid .pick input').forEach((c) => { c.checked = e.target.checked; if (e.target.checked) S.picked.add(c.closest('.clip-card').dataset.file); else S.picked.delete(c.closest('.clip-card').dataset.file); });
    updatePickCount('make');
  });
  $('make-zip').addEventListener('click', () => bulkZip('/bundles', S.makeClips.map((c) => c.file).filter((f) => !S.picked.size || S.picked.has(f))));

  // library
  $('lib-search').addEventListener('input', renderLibrary);
  $('lib-sort').addEventListener('change', renderLibrary);
  $('lib-refresh').addEventListener('click', () => loadLibrary());
  $('lib-zip').addEventListener('click', () => bulkZip('/bundles/all'));
  $('lib-zip-selected').addEventListener('click', () => bulkZip('/bundles', S.picked.size ? [...S.picked] : S.library.map((r) => r.id)));
  $('lib-select-all').addEventListener('change', (e) => {
    document.querySelectorAll('#library-grid .pick input').forEach((c) => { c.checked = e.target.checked; if (e.target.checked) S.picked.add(c.closest('.clip-card').dataset.file); else S.picked.delete(c.closest('.clip-card').dataset.file); });
    updatePickCount('library');
  });
  $('lib-delete-selected').addEventListener('click', async () => {
    if (!S.picked.size) { toast('Select clips first.', 'err'); return; }
    if (!(await confirmDialog(`Delete ${S.picked.size} selected clip(s) permanently?`))) return;
    for (const file of [...S.picked]) { try { await Api.request(`/clips/${file}/delete`, {}); } catch (e) { toast(friendly(e), 'err'); } }
    S.picked.clear();
    toast('Deleted.', 'ok');
    loadLibrary(false);
  });

  // live + queue + creators
  $('live-start').addEventListener('click', () => Live.start());
  $('live-now').addEventListener('click', () => Live.clipNow());
  $('live-stop').addEventListener('click', () => Live.stop());
  $('queue-add').addEventListener('click', () => Queue.add());
  $('queue-pause').addEventListener('click', () => Queue.pause());
  $('queue-refresh').addEventListener('click', () => Queue.refresh());
  $('creator-add').addEventListener('click', () => Creators.add());

  // drawer
  $('open-settings').addEventListener('click', () => Drawer.open());
  $('drawer-back').addEventListener('click', () => Drawer.close());
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') Drawer.close(); });
  $('llm-save').addEventListener('click', async () => {
    try {
      await Api.request('/llm/settings', { enabled: false, ollama_model: $('llm-ollama-model').value.trim() });
      $('llm-note').textContent = 'Saved.';
      toast('AI model saved.', 'ok');
    } catch (e) { toast(friendly(e), 'err'); }
  });
  $('cookies-file').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const form = new FormData(); form.append('file', f, f.name);
    try {
      const r = await fetch('/ytdl/cookies', { method: 'POST', body: form });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || 'Upload failed');
      $('cookies-note').textContent = 'Cookies saved. Try the link again.';
      toast('Cookies saved. Try the video again.', 'ok');
    } catch (err) { toast(friendly(err), 'err', 7000); }
  });
  $('yt-save').addEventListener('click', async () => {
    try { await Api.request('/ytdl/settings', { cookies_browser: $('yt-browser').value }); toast('Saved.', 'ok'); } catch (e) { toast(friendly(e), 'err'); }
  });
  $('cleanup-temp').addEventListener('click', async () => {
    try { const r = await Api.request('/data/cleanup', { temp: true, previews: true, bundles: true }); toast(`Freed ${fmtBytes(r.bytes)}.`, 'ok'); Drawer.load(); } catch (e) { toast(friendly(e), 'err'); }
  });
  $('cleanup-sources').addEventListener('click', async () => {
    if (!(await confirmDialog('Delete every downloaded source video? Finished clips are kept.', 'Delete sources'))) return;
    try { const r = await Api.request('/data/cleanup', { sources: true }); toast(`Freed ${fmtBytes(r.bytes)}.`, 'ok'); Drawer.load(); } catch (e) { toast(friendly(e), 'err'); }
  });
  $('report-download').addEventListener('click', () => { location.href = '/report/download'; });
  $('open-aiparts').addEventListener('click', () => { if (window.AiParts) window.AiParts.open(); });

  // readiness dot
  $('ready-dot').addEventListener('click', () => Drawer.open());

  // first loads
  loadReadiness();
  loadLibrary(false);
  Live.reattach();
  setInterval(loadReadiness, 60000);
  document.body.classList.add('has-sticky');
}

document.addEventListener('DOMContentLoaded', init);

/* expose for editor.js */
window.NC = { $, el, S, Api, ApiError, toast, confirmDialog, friendly, gatherSettings, showView, loadLibrary, clipCard, pollJob, fmtTime, fmtStamp, sleep,
  updateMakeProgress, skeletons, renderLayouts, renderCaptionStyles };
