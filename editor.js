/* NeonClipping Plus - editor: box drawing (mouse + touch), 9:16 preview, word editor,
   saved-clip re-editing, highlight preview, style extras and the chatbot transcript parts.
   Loaded after app.js; talks to the backend only through the NC.Api client. */
'use strict';
(() => {
  const { $, el, S, Api, ApiError, toast, confirmDialog, friendly, gatherSettings, showView, loadLibrary, pollJob, fmtTime, fmtStamp } = window.NC;
  const sleep = window.NC.sleep;

  /* ================================================================= Editor */
  const Editor = {
    mode: 'cam',                 // which box is being drawn
    drag: null,
    still: null,                 // current frame as an Image
    layoutSpecs: {},
    words: [],
    wordSource: '',
    editingFile: null,           // saved clip being edited (reedit path)
    meta: null,                  // source meta {w,h,duration}

    open() { $('editor-card').hidden = false; $('editor-card').scrollIntoView({ behavior: 'smooth' }); if (!this.mounted) this.mount(); },
    close() { $('editor-card').hidden = true; },

    attachSource(meta) {
      this.meta = meta;
      S.sourceId = meta.id;
      S.cam = S.cam || { x: 0, y: 0, w: .25, h: .3 };
      S.game = S.game || { x: .25, y: 0, w: .75, h: 1 };
      this.editingFile = null;
      if (!this.mounted) this.mount();
      const v = this.video;
      v.src = '/media/' + meta.id;
      v.onloadeddata = () => { this.captureFrame(); };
      this.loadLayoutSpecs();
    },

    async mount() {
      this.mounted = true;
      const mount = $('editor-mount');
      mount.replaceChildren();
      this.video = el('video', { controls: 'true', preload: 'metadata', playsinline: '', style: 'width:100%;max-height:340px;border-radius:12px;background:#000' });
      this.canvas = el('canvas', { width: 960, height: 540, 'aria-label': 'Draw the facecam and gameplay boxes on this frame' });
      this.preview = el('canvas', { width: 405, height: 720, 'aria-label': 'Vertical preview' });
      this.rangeStart = el('input', { type: 'number', value: '0', min: '0', step: '.1', 'aria-label': 'Start seconds' });
      this.rangeEnd = el('input', { type: 'number', value: '30', min: '0', step: '.1', 'aria-label': 'End seconds' });
      this.wordBox = el('div', { class: 'word-editor', 'aria-label': 'Editable caption words' });

      mount.append(
        el('div', { class: 'editor-split' },
          el('div', {},
            el('div', { class: 'row', style: 'margin-bottom:10px' },
              el('button', { class: 'chip', type: 'button', 'aria-pressed': 'true', text: '▣ Facecam box', onclick: (e) => this.setMode('cam', e.target) }),
              el('button', { class: 'chip', type: 'button', 'aria-pressed': 'false', text: '▣ Gameplay box', onclick: (e) => this.setMode('game', e.target) }),
              el('button', { class: 'btn small', type: 'button', text: 'Detect facecam', onclick: () => this.detectFacecam() }),
              el('label', { class: 'check' }, el('input', { type: 'checkbox', onchange: (e) => this.drawSafeZones = e.target.checked }), 'Platform safe zones')),
            el('div', { class: 'canvas-wrap' }, this.canvas,
              el('span', { class: 'canvas-hint', text: 'Drag to draw · the crop follows the speaker in Auto-follow layout' })),
            el('div', { class: 'row', style: 'margin-top:12px' },
              el('label', { class: 'field' }, 'Start / sec', this.rangeStart),
              el('label', { class: 'field' }, 'End / sec', this.rangeEnd),
              el('button', { class: 'btn small', type: 'button', text: 'Generate captions', onclick: () => this.transcribeRange() }),
              el('button', { class: 'btn small', type: 'button', text: 'Export this clip', onclick: () => this.exportClip() })),
            el('div', { style: 'margin-top:12px' }, this.video)),
          el('div', { class: 'preview-wrap' },
            el('small', { class: 'muted', text: 'Vertical preview (9:16)' }),
            this.preview)),
        el('details', { class: 'adv', style: 'margin-top:14px' },
          el('summary', { text: 'Caption words (edit before export)' }),
          el('div', { class: 'adv-body' },
            el('div', { class: 'row' },
              el('label', { class: 'field' }, 'Shift captions (s)', el('input', { id: 'ed-shift', type: 'number', step: '0.1', value: '0.5' })),
              el('button', { class: 'btn small', type: 'button', style: 'align-self: flex-end', text: 'Shift all words', onclick: () => this.shiftWords(Number($('ed-shift').value) || 0) }),
              el('button', { class: 'btn small', type: 'button', style: 'align-self: flex-end', text: 'Add word', onclick: () => this.addWordRow() })),
            this.wordBox)),
        el('div', { id: 'ed-actions', class: 'row', style: 'margin-top:14px', hidden: 'true' },
          el('button', { class: 'btn primary', type: 'button', text: 'Save changes (this clip)', onclick: () => this.applyReedit('one') }),
          el('button', { class: 'btn', type: 'button', text: 'Apply to all clips of this video', onclick: () => this.applyReedit('video') }),
          el('button', { class: 'btn ghost', type: 'button', text: 'Suggest a hook', onclick: () => this.suggestHook() })),
      );

      this.bindCanvas();
      this.loadLayoutSpecs();
    },

    setMode(mode, btn) {
      this.mode = mode;
      document.querySelectorAll('#editor-mount .chip').forEach((c, i) => c.setAttribute('aria-pressed', String(i === (mode === 'cam' ? 0 : 1))));
    },

    /* ---- box drawing: pointer events work for mouse, pen and touch */
    bindCanvas() {
      const c = this.canvas;
      const point = (e) => {
        const r = c.getBoundingClientRect();
        return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
      };
      c.addEventListener('pointerdown', (e) => {
        c.setPointerCapture(e.pointerId);
        const p = point(e);
        this.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
        this.draw();
      });
      c.addEventListener('pointermove', (e) => {
        if (!this.drag) return;
        const p = point(e);
        this.drag.x1 = p.x; this.drag.y1 = p.y;
        this.draw();
      });
      const finish = () => {
        if (!this.drag) return;
        const d = this.drag; this.drag = null;
        const box = {
          x: Math.min(d.x0, d.x1), y: Math.min(d.y0, d.y1),
          w: Math.abs(d.x1 - d.x0), h: Math.abs(d.y1 - d.y0),
        };
        if (box.w > 0.02 && box.h > 0.02) {
          S[this.mode] = box;
          S.preset = null;
          document.querySelectorAll('#presets .chip').forEach((chip) => chip.setAttribute('aria-pressed', 'false'));
          this.draw();
        }
      };
      c.addEventListener('pointerup', finish);
      c.addEventListener('pointercancel', finish);
    },

    captureFrame() {
      const v = this.video;
      if (!v.videoWidth) return;
      const c = el('canvas', { width: v.videoWidth, height: v.videoHeight });
      c.getContext('2d').drawImage(v, 0, 0);
      const img = new Image();
      img.onload = () => { this.still = img; this.draw(); };
      img.src = c.toDataURL('image/jpeg', 0.85);
    },

    seekTo(t) {
      this.open();
      const v = this.video;
      if (!v.src) return;
      v.currentTime = Math.max(0, Math.min(t, (this.meta && this.meta.duration) || t));
      v.addEventListener('seeked', () => this.captureFrame(), { once: true });
    },

    draw() {
      const c = this.canvas, ctx = c.getContext('2d');
      ctx.clearRect(0, 0, c.width, c.height);
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, c.width, c.height);
      if (this.still) ctx.drawImage(this.still, 0, 0, c.width, c.height);
      const boxes = [
        { box: S.game, color: '#58e8f7', label: 'GAME' },
        { box: S.cam, color: '#ff4ba5', label: 'CAM' },
      ];
      const current = this.drag ? { x: Math.min(this.drag.x0, this.drag.x1), y: Math.min(this.drag.y0, this.drag.y1), w: Math.abs(this.drag.x1 - this.drag.x0), h: Math.abs(this.drag.y1 - this.drag.y0) } : null;
      for (const { box, color, label } of boxes) {
        if (!box) continue;
        ctx.strokeStyle = color; ctx.lineWidth = 3;
        ctx.setLineDash(label === 'CAM' ? [] : []);
        ctx.strokeRect(box.x * c.width, box.y * c.height, box.w * c.width, box.h * c.height);
        ctx.fillStyle = color;
        ctx.font = '700 13px system-ui, sans-serif';
        ctx.fillText(label, box.x * c.width + 6, box.y * c.height + 18);
      }
      if (current) {
        ctx.strokeStyle = '#ffe14d'; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
        ctx.strokeRect(current.x * c.width, current.y * c.height, current.w * c.width, current.h * c.height);
        ctx.setLineDash([]);
      }
      if (this.drawSafeZones) {
        ctx.fillStyle = 'rgba(255,90,60,.14)';
        ctx.fillRect(0, 0, c.width, c.height * .10);
        ctx.fillRect(0, c.height * .82, c.width, c.height * .18);
        ctx.fillRect(0, 0, c.width * .08, c.height);
        ctx.fillRect(c.width * .92, 0, c.width * .08, c.height);
      }
      this.drawPreview();
    },

    async loadLayoutSpecs() {
      try {
        const specs = await Api.request(`/layouts?top=.32&aspect=${$('aspect') ? $('aspect').value : 'vertical'}&resolution=1080`);
        this.layoutSpecs = {};
        for (const s of specs) this.layoutSpecs[s.id] = s;
        this.drawPreview();
      } catch (e) { /* preview just stays blank */ }
    },

    drawPreview() {
      const p = this.preview, ctx = p.getContext('2d');
      const spec = this.layoutSpecs[S.layout] || this.layoutSpecs.blur;
      ctx.clearRect(0, 0, p.width, p.height);
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, p.width, p.height);
      if (this.still && spec) {
        const sx = p.width / spec.width, sy = p.height / spec.height;
        if (spec.mode === 'blur') {
          ctx.save(); ctx.filter = 'blur(6px)';
          ctx.drawImage(this.still, 0, 0, p.width * .3, p.height * .3);
          ctx.restore();
          ctx.drawImage(this.still, 0, 0, p.width * .3, p.height * .3, 0, 0, p.width, p.height);
          const scale = Math.min(p.width / (this.still.width), p.height / (this.still.height));
          const w = this.still.width * scale, h = this.still.height * scale;
          ctx.drawImage(this.still, (p.width - w) / 2, (p.height - h) / 2, w, h);
        } else {
          for (const layer of spec.layers || []) {
            const src = layer.source === 'cam' ? S.cam : layer.source === 'game' ? S.game : { x: 0, y: 0, w: 1, h: 1 };
            if (!src) continue;
            const sw = this.still.width, sh = this.still.height;
            const sx0 = src.x * sw, sy0 = src.y * sh, sw0 = Math.max(2, src.w * sw), sh0 = Math.max(2, src.h * sh);
            const dw = layer.w * sx, dh = layer.h * sy;
            const scale = Math.max(dw / sw0, dh / sh0);            // cover the layer box
            const cw = sw0 * scale, ch = sh0 * scale;
            ctx.save();
            if (layer.circle) {
              ctx.beginPath();
              ctx.arc(layer.x * sx + dw / 2, layer.y * sy + dh / 2, Math.min(dw, dh) / 2, 0, Math.PI * 2);
              ctx.clip();
            }
            ctx.drawImage(this.still, sx0, sy0, sw0, sh0,
              layer.x * sx + (dw - cw) / 2, layer.y * sy + (dh - ch) / 2, cw, ch);
            ctx.restore();
          }
        }
      }
      // caption position marker
      if (spec && spec.caption_y) {
        const y = spec.caption_y / spec.height * p.height;
        ctx.fillStyle = 'rgba(255,225,77,.9)';
        ctx.font = `900 ${Math.round(p.width * .07)}px Impact, system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.fillText('CAPTIONS HERE', p.width / 2, y);
      }
    },

    async detectFacecam() {
      if (!S.sourceId) { toast('Add a video first.', 'err'); return; }
      toast('Looking for the facecam…');
      try {
        const r = await Api.request('/facecam/detect', { id: S.sourceId });
        if (r.found) {
          S.cam = r.cam; S.game = r.game || { x: 0, y: 0, w: 1, h: 1 };
          S.preset = null;
          this.draw();
          toast(`Facecam found (${r.method}, confidence ${Math.round((r.confidence || 0) * 100)}%). Boxes updated.`, 'ok');
        } else toast('No facecam found. A full-frame crop or Auto-follow is the right layout here.');
      } catch (e) { toast(friendly(e), 'err'); }
    },

    /* ---- captions + export */
    currentRange() {
      const start = Math.max(0, Number(this.rangeStart.value) || 0);
      const end = Math.max(start + 1, Number(this.rangeEnd.value) || 30);
      return [start, Math.min(end, (this.meta && this.meta.duration) || end)];
    },
    async transcribeRange() {
      if (!S.sourceId) { toast('Add a video first.', 'err'); return; }
      const [start, end] = this.currentRange();
      toast('Transcribing locally (first use downloads the model)…');
      try {
        const r = await Api.request('/transcribe', { id: S.sourceId, start, end, language: $('language').value || null });
        const done = await pollJob(`/status/${r.job}`, (row) => { if (row.message) toast(row.message); });
        if (!done || done.status !== 'done') throw new ApiError((done && done.error) || 'Transcription failed.');
        this.setWords(done.words || []);
        toast(`${(done.words || []).length} words transcribed.`, 'ok');
      } catch (e) { toast(friendly(e), 'err'); }
    },
    setWords(words) {
      this.words = words;
      this.renderWords();
    },
    renderWords() {
      const box = this.wordBox;
      box.replaceChildren();
      if (!this.words.length) { box.append(el('p', { class: 'muted', text: 'No caption words yet. Press "Generate captions" for the selected range, or open a saved clip.' })); return; }
      this.words.forEach((w, i) => {
        box.append(el('div', { class: 'word-row' },
          el('input', { type: 'text', value: w.text, 'aria-label': `Word ${i + 1} text`, onchange: (e) => { this.words[i].text = e.target.value; } }),
          el('input', { class: 'num', type: 'number', step: '.05', value: String(w.start), 'aria-label': `Word ${i + 1} start`, onchange: (e) => { this.words[i].start = Number(e.target.value); } }),
          el('input', { class: 'num', type: 'number', step: '.05', value: String(w.end), 'aria-label': `Word ${i + 1} end`, onchange: (e) => { this.words[i].end = Number(e.target.value); } }),
          el('button', { class: 'btn ghost small', type: 'button', 'aria-label': `Delete word ${i + 1}`, text: '✕', onclick: () => { this.words.splice(i, 1); this.renderWords(); } })));
      });
    },
    addWordRow() {
      const last = this.words[this.words.length - 1];
      const t = last ? last.end + 0.2 : 0;
      this.words.push({ start: t, end: t + 0.3, text: 'word' });
      this.renderWords();
    },
    shiftWords(delta) {
      if (!this.words.length) return;
      for (const w of this.words) { w.start = Math.max(0, w.start + delta); w.end = Math.max(0.05, w.end + delta); }
      this.renderWords();
      toast(`Shifted ${delta > 0 ? '+' : ''}${delta}s.`, 'ok');
    },

    exportClip() {
      if (!S.sourceId) { toast('Add a video first.', 'err'); return; }
      const [start, end] = this.currentRange();
      const body = { ...gatherSettings(), id: S.sourceId, start, end, captions: true, hook: $('ed-hook') ? $('ed-hook').value : '', title: $('ed-title') ? $('ed-title').value : 'Clip' };
      if (this.words.length) body.words = this.words;
      toast('Rendering the clip…');
      Api.request('/export', body).then((r) => pollJob(`/status/${r.job}`, () => {})).then((done) => {
        if (!done || done.status !== 'done') throw new ApiError((done && done.error) || 'Export failed.');
        toast('Clip ready - saved to your library.', 'ok');
        loadLibrary(false);
      }).catch((e) => toast(friendly(e), 'err', 7000));
    },

    async suggestHook() {
      if (!this.words.length) { toast('Generate captions first.', 'err'); return; }
      try {
        const r = await Api.request('/hooks/suggest', { text: this.words.map((w) => w.text).join(' ') });
        toast(`Hook idea: ${r.hook}`, 'ok', 9000);
      } catch (e) { toast(friendly(e), 'err'); }
    },

    /* ---- highlight preview (no export) */
    async previewHighlights() {
      const body = { count: Number($('count').value) || 4, length: Number($('length').value) || 60, min_len: Number($('minlen').value) || 30, language: $('language').value || null };
      if ($('url').value.trim() && /^https?:/.test($('url').value.trim())) body.url = $('url').value.trim();
      else if (S.sourceId) body.id = S.sourceId;
      else { toast('Add a video first.', 'err'); return; }
      $('results-card').hidden = false;
      const grid = $('make-grid');
      grid.replaceChildren(...Array.from({ length: body.count }, () => el('div', { class: 'skeleton' }, el('div', { class: 'sk-thumb sk-anim' }), el('div', { class: 'sk-line sk-anim' }))));
      $('make-progress').hidden = false;
      $('btn-cancel').hidden = true;
      $('make-stage').textContent = 'Scanning movement + audio…';
      try {
        const r = await Api.request('/analyze', body);
        const done = await pollJob(`/analysis/${r.job}`, (row) => { if (row.stage) $('make-stage').textContent = row.stage; if (row.progress) $('make-fill').style.width = Math.round(row.progress * 100) + '%'; });
        grid.replaceChildren();
        $('make-progress').hidden = true;
        if (!done || !done.clips || !done.clips.length) {
          grid.append(el('p', { class: 'muted', style: 'grid-column:1/-1', text: 'No moments found for this video.' }));
          return;
        }
        for (const c of done.clips) {
          grid.append(el('div', { class: 'clip-card' },
            el('div', { class: 'body' },
              el('h3', { text: c.title || 'Moment' }),
              el('p', { class: 'hookline', text: `${fmtStamp(c.start)} - ${fmtStamp(c.end)} · ${c.reason || ''}` }),
              el('button', { class: 'btn small', type: 'button', text: 'Open in editor', onclick: () => { this.open(); this.rangeStart.value = c.start; this.rangeEnd.value = c.end; } }))));
        }
      } catch (e) {
        $('make-progress').hidden = true;
        toast(friendly(e), 'err');
      }
    },

    /* ---- saved clip editing (reedit) */
    async openSavedClip(file, context) {
      this.open();
      toast('Loading the saved clip…');
      try {
        const r = await Api.request(`/clips/${file}/edit`);
        this.editingFile = r.file;
        const s = r.settings;
        // reflect the saved settings in the UI
        S.layout = s.layout || 'blur'; S.captionStyle = s.caption_style || 'gta';
        S.cam = s.cam; S.game = s.game;
        for (const [id, value] of [['caption-anim', s.caption_anim], ['caption-y', s.caption_y], ['mode', s.mode], ['accurate', s.accurate], ['language', s.language], ['aspect', s.aspect], ['resolution', s.resolution], ['fps', s.fps], ['hook-style', s.hook_style], ['hook-duration', s.hook_duration], ['focus-brief', s.focus_brief]]) {
          const node = $(id); if (node && value !== undefined && value !== null) node.value = value;
        }
        if ($('count')) $('count').value = 1;
        document.dispatchEvent(new CustomEvent('nc-load-style', { detail: s }));
        window.NC.renderLayouts(); window.NC.renderCaptionStyles();
        this.attachSource(r.source);
        this.rangeStart.value = s.start; this.rangeEnd.value = s.end;
        this.setWords(r.words || []);
        $('ed-actions').hidden = false;
        toast('Saved clip loaded. Change the style and press Save changes.', 'ok');
      } catch (e) { toast(friendly(e), 'err'); }
    },

    async applyReedit(scope) {
      if (!this.editingFile) { toast('Open a saved clip first (Library > Edit).', 'err'); return; }
      const [start, end] = this.currentRange();
      const changes = { ...gatherSettings(), start, end };
      delete changes.count; delete changes.min_len; delete changes.max_len; delete changes.mode; delete changes.accurate; delete changes.focus_brief;
      const body = { file: this.editingFile, changes, scope };
      if (this.words.length) body.words = this.words;
      try {
        toast('Re-rendering…');
        const r = await Api.request('/clips/reedit', body);
        const done = await pollJob(`/status/${r.job || r.job_id || this.editingFile}`, () => {});
        toast('Edits applied. The new version is in your library.', 'ok');
        loadLibrary(false);
      } catch (e) { toast(friendly(e), 'err'); }
    },
  };

  /* ================================================================= Extras (style options) */
  const Extras = {
    assets: { bg_image: '', watermark_image: '', music: '' },
    mount() {
      const host = $('extras-mount');
      if (!host || this.mounted) return;
      this.mounted = true;
      host.append(el('details', { class: 'adv' },
        el('summary', { text: 'Style extras (card, watermark, music, credit…)' }),
        el('div', { class: 'adv-body' },
          el('div', { class: 'row' },
            sel('Template', 'st-template', [['none', 'None'], ['x_light', 'X post · light'], ['x_dark', 'X post · dark'], ['tiktok_hook', 'TikTok hook'], ['breaking_news', 'Breaking news']]),
            inp('Name on the card', 'st-tpl-name', { maxlength: '28', placeholder: 'Your Name' }),
            inp('@handle', 'st-tpl-handle', { maxlength: '28', placeholder: 'yourhandle' })),
          el('div', { class: 'row' },
            sel('Background', 'st-bg-mode', [['', 'None'], ['blur', 'Blurred copy'], ['color', 'Colour'], ['gradient', 'Gradient'], ['image', 'My image']]),
            color('Colour', 'st-bg-color', '#0b0912'), color('Colour 2', 'st-bg-color2', '#ff4ba5'),
            range('Padding %', 'st-padding', 0, 25, 0), range('Corners', 'st-radius', 0, 80, 0)),
          el('div', { class: 'row' },
            fileBtn('Background image', 'st-bg-file', 'image'),
            fileBtn('Logo / watermark', 'st-wm-file', 'image'),
            sel('Logo position', 'st-wm-pos', [['tr', 'Top right'], ['tl', 'Top left'], ['br', 'Bottom right'], ['bl', 'Bottom left']]),
            range('Logo size %', 'st-wm-size', 5, 40, 16)),
          el('div', { class: 'row' },
            inp('Credit line', 'st-credit', { maxlength: '60', placeholder: 'Clip: Channel name' }),
            inp('Outro ribbon', 'st-outro', { maxlength: '40', placeholder: 'FOLLOW @yourhandle' }),
            inp('Outro seconds', 'st-outro-sec', { type: 'number', min: '1.5', max: '8', step: '.5', value: '3' })),
          el('div', { class: 'row' },
            fileBtn('Music track', 'st-music-file', 'audio'),
            range('Music volume %', 'st-music-vol', 0, 100, 15),
            range('Peak volume %', 'st-music-peak', 0, 100, 85),
            inp('Riser seconds', 'st-music-riser', { type: 'number', min: '1', max: '15', value: '4' })),
          el('div', { class: 'row' },
            range('Facecam contrast', 'cam-contrast', 0.5, 2, 1, .05), range('Facecam brightness', 'cam-brightness', -0.3, 0.3, 0, .02),
            range('Facecam colour', 'cam-saturation', 0, 2, 1, .05), range('Facecam clarity', 'cam-clarity', 0, 1.5, 0, .05)),
          el('div', { class: 'row' },
            label2('Caption font size', 'st-caption-size', 'number', { min: '24', max: '140', value: '' , placeholder: 'style default'}),
            inp('Caption font family', 'st-caption-font', { maxlength: '40', placeholder: 'Arial' }),
            color('Caption colour', 'st-caption-color', ''), color('Active word colour', 'st-caption-active', '')),
        )));

      function label2(text, id, type, attrs) { return el('label', { class: 'field' }, text, el('input', { id, type, ...attrs })); }
      function inp(text, id, attrs) { return label2(text, id, 'text', attrs); }
      function sel(text, id, options) { return el('label', { class: 'field' }, text, el('select', { id }, ...options.map(([v, t]) => el('option', { value: v, text: t })))); }
      function color(text, id, value) { return el('label', { class: 'field' }, text, el('input', { id, type: 'color', ...(value ? { value } : {}) })); }
      function range(text, id, min, max, value, step = 1) { return el('label', { class: 'field' }, text, el('input', { id, type: 'range', min: String(min), max: String(max), value: String(value), step: String(step) })); }
      function fileBtn(text, id, kind) {
        const note = el('span', { class: 'muted', style: 'align-self:flex-end;padding-bottom:10px', text: 'none' });
        const input = el('input', { type: 'file', accept: kind === 'image' ? 'image/*' : 'audio/*', hidden: 'true', onchange: async (e) => {
          const f = e.target.files[0]; if (!f) return;
          const form = new FormData(); form.append('file', f, f.name);
          try {
            const r = await fetch(`/upload-asset?kind=${kind}`, { method: 'POST', body: form });
            const data = await r.json();
            if (!r.ok) throw new Error(data.detail || 'upload failed');
            Extras.assets[id.replace('st-', '').replace('-file', '_file')] = data.id;
            note.textContent = f.name.slice(0, 22);
          } catch (err) { toast(friendly(err), 'err'); }
        } });
        return el('label', { class: 'btn small', style: 'align-self:flex-end' }, '↑ ' + text, input, note);
      }

      document.addEventListener('nc-load-style', (e) => this.loadFields(e.detail));
    },

    styleFields() {
      const v = (id) => { const n = $(id); return n ? n.value : ''; };
      const n = (id, dflt = 0) => { const x = Number(v(id)); return Number.isFinite(x) && v(id) !== '' ? x : dflt; };
      const o = {};
      if (v('st-template') && v('st-template') !== 'none') { o.template = v('st-template'); o.tpl_name = v('st-tpl-name'); o.tpl_handle = v('st-tpl-handle'); }
      if (v('st-bg-mode')) { o.bg_mode = v('st-bg-mode'); o.bg_color = v('st-bg-color') || '#0b0912'; o.bg_color2 = v('st-bg-color2') || '#ff4ba5'; o.bg_dim = .25; if (v('st-bg-mode') === 'image' && this.assets.bg_image) o.bg_image = this.assets.bg_image; }
      if (n('st-padding') > 0) { o.padding = n('st-padding'); o.radius = n('st-radius'); }
      if (this.assets.watermark_image) { o.watermark_image = this.assets.watermark_image; o.watermark_pos = v('st-wm-pos') || 'tr'; o.watermark_size = n('st-wm-size', 16); o.watermark_opacity = .85; }
      if (v('st-credit')) o.credit = v('st-credit');
      if (v('st-outro')) { o.outro_text = v('st-outro'); o.outro_seconds = n('st-outro-sec', 3); }
      if (this.assets.music) { o.music = this.assets.music; o.music_volume = n('st-music-vol', 15) / 100; o.music_peak = n('st-music-peak', 85) / 100; o.music_riser = n('st-music-riser', 4); }
      const fx = { cam_contrast: n('cam-contrast', 1), cam_brightness: n('cam-brightness', 0), cam_saturation: n('cam-saturation', 1), cam_clarity: n('cam-clarity', 0) };
      if (Object.values(fx).some((x, i) => Math.abs(x - [1, 0, 1, 0][i]) > 1e-6)) Object.assign(o, fx);
      if (v('st-caption-size')) o.caption_size = n('st-caption-size');
      if (v('st-caption-font')) o.caption_font = v('st-caption-font');
      if (v('st-caption-color')) o.caption_color = v('st-caption-color');
      if (v('st-caption-active')) o.caption_active = v('st-caption-active');
      return o;
    },

    loadFields(s) {
      s = s || {};
      const set = (id, v) => { const n = $(id); if (n && v !== undefined && v !== null) n.value = v; };
      set('st-template', s.template || 'none'); set('st-tpl-name', s.tpl_name); set('st-tpl-handle', s.tpl_handle);
      set('st-bg-mode', s.bg_mode || ''); set('st-credit', s.credit); set('st-outro', s.outro_text);
      set('st-outro-sec', s.outro_seconds); set('st-wm-pos', s.watermark_pos || 'tr');
      set('st-caption-size', s.caption_size); set('st-caption-font', s.caption_font);
      set('st-caption-color', s.caption_color); set('st-caption-active', s.caption_active);
      if (s.cam_contrast !== undefined) set('cam-contrast', s.cam_contrast);
    },
  };

  /* ================================================================= AiParts (chatbot transcript parts) */
  const AiParts = {
    open() { DrawerOpen(); },
  };
  function DrawerOpen() {
    // The AI-parts UI lives inside the settings drawer (#aiparts-mount).
    const host = $('aiparts-mount');
    if (host.childElementCount) { window.NC.$('drawer').hidden = false; window.NC.$('drawer-back').hidden = false; return; }
    const list = el('div', { style: 'display:flex;flex-direction:column;gap:10px' });
    const status = el('p', { class: 'muted', text: 'Paste a YouTube link with captions, split it, feed the parts to any free chatbot, then paste the answers back.' });
    const url = el('input', { type: 'text', placeholder: 'YouTube video link' });
    const parts = el('input', { type: 'number', placeholder: 'parts (auto)', min: '1', max: '40' });
    const per = el('input', { type: 'number', value: '3', min: '1', max: '10' });
    const answers = el('textarea', { rows: '4', placeholder: '[{"start":"00:12:30","end":"00:13:10","title":"Crash","hook":"bhai plane gir gaya"}]  or  12:30-13:10 Crash' });
    const go = el('button', { class: 'btn small', type: 'button', text: '1 · Split into parts', onclick: split });
    const read = el('button', { class: 'btn small', type: 'button', text: '2 · Read the answers', onclick: readAnswers });
    const make = el('button', { class: 'btn small primary', type: 'button', text: '3 · Make clips from these moments', disabled: 'true', onclick: makeClipsFromMoments });
    let moments = [];
    host.append(url, el('div', { class: 'row' }, el('label', { class: 'field' }, 'Parts', parts), el('label', { class: 'field' }, 'Moments per part', per)), go, status, list, answers, el('div', { class: 'row' }, read, make));

    async function split() {
      status.textContent = 'Reading the transcript…';
      try {
        const r = await Api.request('/ai/parts', { url: url.value.trim(), parts: parts.value ? Number(parts.value) : null, per_part: Number(per.value) || 3 });
        list.replaceChildren();
        for (const p of r.parts) {
          list.append(el('details', { class: 'part-card' },
            el('summary', { text: `Part ${p.index}: ${p.label} (${p.lines} lines)` }),
            el('button', { class: 'btn small', type: 'button', style: 'margin:8px 0', text: 'Copy the prompt', onclick: (e) => { navigator.clipboard.writeText(p.prompt).then(() => toast('Prompt copied - paste it into any chatbot.', 'ok')); } }),
            el('textarea', { rows: '4', readonly: 'readonly', text: p.prompt.slice(0, 400) + '…' })));
        }
        status.textContent = `${r.parts.length} parts ready. Copy each prompt into a free chatbot, then paste all answers below.`;
      } catch (e) { status.textContent = friendly(e); }
    }
    async function readAnswers() {
      try {
        const r = await Api.request('/ai/answers', { text: answers.value });
        moments = r.moments;
        status.textContent = `${moments.length} moments understood.`;
        make.disabled = !moments.length;
      } catch (e) { status.textContent = friendly(e); }
    }
    async function makeClipsFromMoments() {
      if (!moments.length) return;
      const body = { ...gatherSettings(), url: url.value.trim(), moments, mode: 'fast' };
      try {
        const r = await Api.request('/make', body);
        window.NC.showView('create');
        toast('Making clips from the chatbot moments…', 'ok');
        const started = performance.now();
        S.makeJob = r.job; S.makeCount = moments.length; S.makeClips = [];
        $('results-card').hidden = false; $('make-progress').hidden = false; $('btn-cancel').hidden = false;
        $('make-grid').replaceChildren(...window.NC.skeletons(moments.length));
        await pollJob(`/make/${r.job}`, (row) => window.NC.updateMakeProgress(row, started));
      } catch (e) { toast(friendly(e), 'err'); }
    }
  }

  /* expose */
  window.Editor = Editor;
  window.Extras = Extras;
  window.AiParts = { open: DrawerOpen };

  /* boot after app.js DOMContentLoaded */
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => Extras.mount());
  else Extras.mount();
})();
