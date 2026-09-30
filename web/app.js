/* EdgeScribe dashboard. Talks only to the local server (127.0.0.1) — plus the browser's cloud
   voices, and only when the user switches Voice to Online. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = Charts.esc, fmt = Charts.fmt;
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { } } };
const ICON_SAY = '<svg viewBox="0 0 24 24"><path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/></svg>';

/* ---------------- api ---------------- */
async function j(r) { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || r.statusText); return d; }
const H = { 'X-EdgeScribe': '1' };
const api = {
  get: p => fetch(p).then(j),
  post: (p, b) => fetch(p, { method: 'POST', headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) }).then(j),
  raw: (p, body) => fetch(p, { method: 'POST', headers: { ...H, 'Content-Type': 'application/octet-stream' }, body }).then(j),
};
function toast(html, cls = '', ms = 3200) {
  const t = document.createElement('div'); t.className = 'toast ' + cls; t.innerHTML = html;
  $('#toasts').appendChild(t); setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = 0; setTimeout(() => t.remove(), 300); }, ms);
}
const err = e => toast(esc(e && e.message || e), 'err', 5000);

/* ---------------- theme ---------------- */
/* dark is the designed default; 'auto' follows the OS */
function applyTheme(t) {
  const resolved = t === 'auto' ? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : (t === 'light' ? 'light' : 'dark');
  document.documentElement.dataset.theme = resolved;
  $$('#themeSel button').forEach(b => b.classList.toggle('on', b.dataset.v === t));
  setTimeout(() => { readPal(); if (S.tab === 'insights') loadLab(); }, 30);
}
function applyAccent(a) {
  if (['mist', 'slate'].includes(a)) document.documentElement.dataset.accent = a; else delete document.documentElement.dataset.accent;
  $$('#accentSel .swatch').forEach(b => { const on = b.dataset.v === (a || 'steel'); b.classList.toggle('on', on); b.setAttribute('aria-checked', on); });
  setTimeout(() => { readPal(); if (S.tab === 'insights') loadLab(); }, 30);
}
matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => { if (store.get('es-theme', 'dark') === 'auto') applyTheme('auto'); });
/* sliding tab indicator */
function moveInk() {
  const b = $('#tabs button.on'), ink = $('#tabInk');
  if (!b || !ink) return;
  ink.style.width = b.offsetWidth + 'px';
  ink.style.transform = `translateX(${b.offsetLeft}px)`;
}
addEventListener('resize', moveInk);
if (document.fonts) document.fonts.ready.then(moveInk);

/* ---------------- state ---------------- */
const S = { state: null, tab: 'live', sessionId: null, audioKind: '', cols: [], audioSec: 0, awakeSec: 0, spikes: 0, nWakes: 0, werE: 0, werN: 0,
  mic: null, w: new Array(16).fill(1), thr: 2.5, act: new Array(16).fill(0), flare: 0 };
const BAND_KHZ = Array.from({ length: 16 }, (_, b) => { const e = i => (2 + i * 199 / 16) * 40 / 1000; return ((e(b) + e(b + 1)) / 2).toFixed(1); });

/* ---------------- tabs ---------------- */
function showTab(name) {
  S.tab = name;
  $$('#tabs button').forEach(b => { const on = b.dataset.tab === name; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
  moveInk();
  const active = $('#tabs button.on'); if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  $$('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + name));
  if (name === 'insights') loadLab(); if (name === 'teach') loadCard(); if (name === 'library') loadLibrary();
  if (name === 'today') Today.load(); if (name === 'study') StudyUI.load();
  scrollTo({ top: 0 });
}
$('#tabs').onclick = e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); };
addEventListener('keydown', e => {
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey || e.altKey || $('#settings').open) return;
  if (/^[1-7]$/.test(e.key) && S.tab !== 'study') showTab(['live', 'today', 'study', 'teach', 'insights', 'ask', 'library'][+e.key - 1]);
  if (e.key === 'Escape') Voice.stop();
  if (S.tab === 'teach') {
    if (e.key === 'y' || e.key === 'ArrowRight') answerCard(1);
    if (e.key === 'n' || e.key === 'ArrowLeft') answerCard(0);
    if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); nextCard(); }
    if (e.key === 's') Voice.speak($('#teachText').textContent);
  }
});

/* ---------------- voice (TTS: offline + online) ---------------- */
const Voice = {
  server: [], web: [], token: 0, url: null,
  async load() {
    try { this.server = (await api.get('/api/voices')).server || []; } catch { this.server = []; }
    this.loadWeb();
    if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = () => { this.loadWeb(); if ($('#settings').open) renderVoiceSelect(); };
  },
  loadWeb() { this.web = 'speechSynthesis' in window ? speechSynthesis.getVoices() : []; },
  options(mode) {
    const o = this.server.map(v => ({ id: 'srv:' + v.name, label: v.name.replace(/^Microsoft /, '').replace(/ Desktop$/, '') + ' · Windows, offline', online: false }));
    this.web.filter(v => v.localService).forEach(v => o.push({ id: 'web:' + v.voiceURI, label: v.name + ' · this device', online: false }));
    if (mode === 'online') this.web.filter(v => !v.localService).forEach(v => o.push({ id: 'web:' + v.voiceURI, label: v.name + ' · cloud', online: true }));
    return o;
  },
  resolve() {
    const st = S.state ? S.state.settings : { tts_mode: 'offline', tts_voice: '', tts_rate: 0 };
    const opts = this.options(st.tts_mode);
    return opts.find(o => o.id === st.tts_voice) || opts[0] || null;
  },
  async speak(text) {
    text = String(text || '').replace(/\s+/g, ' ').trim(); if (!text) return;
    this.stop(); const tok = ++this.token;
    const v = this.resolve(); if (!v) return toast('No voice is available on this device.', 'err');
    const st = S.state.settings; setSpeaking(true);
    try {
      if (v.id.startsWith('srv:')) {
        const r = await fetch('/api/tts', { method: 'POST', headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify({ text, voice: v.id.slice(4), rate: st.tts_rate }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'speech failed');
        const blob = await r.blob(); if (tok !== this.token) return;
        this.url = URL.createObjectURL(blob); const p = $('#player'); p.src = this.url;
        p.onended = () => { if (tok === this.token) this.stop(); }; await p.play();
      } else {
        const wv = this.web.find(w => 'web:' + w.voiceURI === v.id);
        if (!wv || (st.tts_mode === 'offline' && !wv.localService)) throw new Error('That voice needs Online mode.');
        const u = new SpeechSynthesisUtterance(text); u.voice = wv; u.rate = Math.max(.5, Math.min(2, 1 + st.tts_rate * .15));
        u.onend = u.onerror = () => { if (tok === this.token) this.stop(); };
        speechSynthesis.speak(u);
      }
    } catch (e) { if (tok === this.token) { this.stop(); err(e); } }
  },
  stop() {
    this.token++;
    const p = $('#player'); p.onended = null; p.pause(); p.removeAttribute('src');
    if (this.url) { URL.revokeObjectURL(this.url); this.url = null; }
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    setSpeaking(false);
  },
};
function setSpeaking(on) { $('#speakingPill').classList.toggle('hidden', !on); }
$('#stopSpeak').onclick = () => Voice.stop();
document.addEventListener('click', e => { const b = e.target.closest('[data-say]'); if (b) { e.stopPropagation(); Voice.speak(b.dataset.say); } });
const sayBtn = text => `<button class="say" data-say="${esc(text)}" title="Read aloud" aria-label="Read aloud">${ICON_SAY}</button>`;

/* ---------------- header / chips ---------------- */
function renderHeader() {
  const st = S.state; if (!st) return;
  const g = st.game, C = 2 * Math.PI * 14, frac = Math.min(1, g.into / g.span);
  $('#rank').innerHTML = `<span class="lvnum"><svg viewBox="0 0 34 34"><circle class="bg" cx="17" cy="17" r="14"/><circle class="fg" cx="17" cy="17" r="14" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - frac)}"/></svg><span>${g.level}</span></span><span class="txt"><b>${esc(g.name)}</b>${g.xp} XP${g.streak ? ' · ' + g.streak + '-day streak' : ''}</span>`;
  $('#rank').title = `Level ${g.level} · ${g.into} of ${g.span} XP to the next level`;
  const chips = [];
  chips.push(st.asr.available ? `<span class="chip ok"><i></i>${esc(st.asr.name)}</span>` : `<span class="chip off"><i></i>No speech recognizer</span>`);
  chips.push(st.hardware.npu ? `<span class="chip ok"><i></i>NPU</span>` : `<span class="chip warn" title="${esc(st.hardware.npu_note)}"><i></i>CPU only · no NPU</span>`);
  chips.push(st.settings.tts_mode === 'online' ? `<span class="chip warn"><i></i>Online voices on</span>` : `<span class="chip ok"><i></i>Private · on-device</span>`);
  if (st.bank.building) chips.push(`<span class="chip accent"><i></i>Building voice bank ${st.bank.clips}/${st.bank.target}</span>`);
  if (S.audioKind) chips.push(`<span class="chip accent">Audio: ${esc(S.audioKind)}</span>`);
  $('#chips').innerHTML = chips.join('');
  $$('#simSource button').forEach(b => b.classList.toggle('on', b.dataset.v === st.settings.sim_source));
}
async function refreshState() { S.state = await api.get('/api/state'); S.w = S.state.snn.w; S.thr = S.state.snn.lif_thr; renderHeader(); renderLiveKpis(); }
function setLevel(lv) { if (S.state && lv) { Object.assign(S.state.game, lv); renderHeader(); } }

/* ---------------- live canvases ---------------- */
const COLS = 400;
const raster = $('#raster'), rc = raster.getContext('2d'), brain = $('#brain'), bc = brain.getContext('2d');
let PAL = {};
function readPal() { PAL = Object.fromEntries(['--s1', '--s2', '--s3', '--s4', '--s8', '--grid', '--tx2', '--tx3', '--fill2'].map(k => [k, cssVar(k)])); }
readPal();
const fontFor = (cv, px) => `${Math.round(px * cv.width / Math.max(cv.clientWidth, 1))}px -apple-system, "Segoe UI", system-ui, sans-serif`;
function drawRaster() {
  const W = raster.width, Hh = raster.height, top = Math.round(Hh * .7), cw = W / COLS, rh = top / 16;
  rc.clearRect(0, 0, W, Hh);
  rc.strokeStyle = PAL['--grid']; rc.lineWidth = 1;
  for (let b = 0; b <= 16; b += 4) { rc.beginPath(); rc.moveTo(0, b * rh + .5); rc.lineTo(W, b * rh + .5); rc.stroke(); }
  const F = fontFor(raster, 11), fs = parseInt(F); rc.fillStyle = PAL['--tx3']; rc.font = F; rc.fillText('8 kHz', 8, fs + 4); rc.fillText('0 Hz', 8, top - 6); rc.fillText('membrane', 8, top + fs + 6);
  const off = COLS - S.cols.length;
  rc.fillStyle = PAL['--s3']; rc.globalAlpha = .13;
  S.cols.forEach((c, i) => { if (c.awake) rc.fillRect((off + i) * cw, 0, cw + .6, Hh); });
  rc.globalAlpha = 1;
  S.cols.forEach((c, i) => c.s.forEach((v, b) => { if (v) rc.fillRect((off + i) * cw, top - (b + 1) * rh + 1.5, Math.max(cw - .6, 1.5), rh - 3); }));
  const vh = Hh - top - 12, vmax = Math.max(S.thr * 1.5, 1), ty = Hh - 6 - S.thr / vmax * vh;
  rc.strokeStyle = PAL['--s8']; rc.setLineDash([5, 5]); rc.beginPath(); rc.moveTo(0, ty); rc.lineTo(W, ty); rc.stroke(); rc.setLineDash([]);
  rc.strokeStyle = PAL['--s1']; rc.lineWidth = 2; rc.lineJoin = 'round'; rc.beginPath();
  S.cols.forEach((c, i) => { const y = Hh - 6 - Math.min(c.v, vmax) / vmax * vh, x = (off + i) * cw; i ? rc.lineTo(x, y) : rc.moveTo(x, y); });
  rc.stroke();
  rc.strokeStyle = PAL['--s4']; rc.lineWidth = 2;
  S.cols.forEach((c, i) => { if (c.wake) { const x = (off + i) * cw; rc.beginPath(); rc.moveTo(x, 0); rc.lineTo(x, Hh); rc.stroke(); } });
  rc.lineWidth = 1;
  if (!S.cols.length) { rc.fillStyle = PAL['--tx3']; rc.font = fontFor(raster, 15); rc.textAlign = 'center'; rc.fillText('Waiting for audio', W / 2, Hh / 2); rc.textAlign = 'start'; }
}
const nodePos = Array.from({ length: 16 }, (_, b) => [74, 22 + b * 17.2]);
function drawBrain() {
  const W = brain.width, Hh = brain.height; bc.clearRect(0, 0, W, Hh);
  const out = [282, 150];
  S.act = S.act.map(a => a * .9); S.flare *= .92;
  S.w.forEach((w, b) => {
    const [x, y] = nodePos[b];
    bc.strokeStyle = w >= 0 ? PAL['--s3'] : PAL['--s2']; bc.globalAlpha = .25 + Math.min(Math.abs(w) / 3, 1) * .55 + S.act[b] * .2;
    bc.lineWidth = .6 + Math.min(Math.abs(w), 3) * 1.3; bc.beginPath(); bc.moveTo(x, y); bc.lineTo(out[0], out[1]); bc.stroke();
  });
  bc.globalAlpha = 1;
  nodePos.forEach(([x, y], b) => {
    const a = S.act[b];
    bc.fillStyle = a > .05 ? PAL['--s3'] : PAL['--fill2']; bc.shadowColor = PAL['--s3']; bc.shadowBlur = a * 14;
    bc.beginPath(); bc.arc(x, y, 5 + a * 2.5, 0, 7); bc.fill(); bc.shadowBlur = 0;
    if (b % 3 === 0) { bc.fillStyle = PAL['--tx3']; bc.font = fontFor(brain, 11); bc.textAlign = 'right'; bc.fillText(BAND_KHZ[b] + 'k', x - 12, y + 4); bc.textAlign = 'start'; }
  });
  bc.fillStyle = S.flare > .1 ? PAL['--s4'] : PAL['--fill2']; bc.shadowColor = PAL['--s4']; bc.shadowBlur = S.flare * 28;
  bc.beginPath(); bc.arc(out[0], out[1], 17 + S.flare * 5, 0, 7); bc.fill(); bc.shadowBlur = 0;
  bc.fillStyle = PAL['--tx2']; bc.font = fontFor(brain, 11); bc.textAlign = 'center';
  bc.fillText('LIF neuron', out[0], out[1] + 36); bc.fillText('threshold ' + fmt(S.thr, 1), out[0], out[1] - 28); bc.textAlign = 'start';
}
(function loop() { drawRaster(); drawBrain(); requestAnimationFrame(loop); })();

function pushFrame(m) {
  for (let i = 0; i < m.spikes.length; i++) {
    const s = m.spikes[i]; s.forEach((v, b) => { if (v) S.act[b] = 1; });
    S.cols.push({ s, v: m.vmem[i], awake: m.awake, wake: false });
    S.spikes += s.reduce((a, b) => a + b, 0);
  }
  if (S.cols.length > COLS) S.cols.splice(0, S.cols.length - COLS);
  S.audioSec += m.spikes.length * m.dt; if (m.awake) S.awakeSec += m.spikes.length * m.dt; S.thr = m.thr;
}
function stat(label, value, detail, meter) {
  return `<div class="stat"><div class="l">${label}</div><div class="v">${value}</div>${detail ? `<div class="d">${detail}</div>` : ''}${meter != null ? `<div class="meter"><i style="width:${Math.max(0, Math.min(100, meter * 100))}%"></i></div>` : ''}</div>`;
}
function renderLiveKpis() {
  const k = S.state ? S.state.kpis : {}, running = S.audioSec > 0;
  const duty = running ? S.awakeSec / S.audioSec : k.duty;
  const wer = S.werN ? S.werE / S.werN : (running ? null : k.asr_wer);
  $('#liveKpis').innerHTML =
    stat('Heavy models awake', duty == null ? '–' : Math.round(duty * 100) + '<small>%</small>', running ? 'this session' : 'all sessions', duty) +
    stat('Compute saved', duty == null ? '–' : Math.round((1 - duty) * 100) + '<small>%</small>', 'vs. always-on transcription') +
    stat('Word error rate', wer == null ? '–' : Math.round(wer * 100) + '<small>%</small>', wer == null ? 'needs a known script' : (running ? 'this session vs. script' : `${k.asr_words} words scored`)) +
    stat('Wake events', fmt(running ? S.nWakes : (k.events || 0), 0), `${fmt(running ? S.spikes : (k.spikes || 0), 0)} spikes`);
}

/* ---------------- live events (SSE) ---------------- */
function connect() {
  const es = new EventSource('/api/stream');
  es.onmessage = ev => { try { handle(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
  es.onerror = () => { $('#liveStatus').textContent = 'Reconnecting…'; };
  es.onopen = () => { if ($('#liveStatus').textContent === 'Reconnecting…') $('#liveStatus').textContent = 'Ready'; };
}
let labTimer = null;
function handle(m) {
  if (typeof Connected !== 'undefined') Connected.event(m);
  switch (m.type) {
    case 'session_start':
      Object.assign(S, { sessionId: m.id, audioKind: m.audio || '', cols: [], audioSec: 0, awakeSec: 0, spikes: 0, nWakes: 0, werE: 0, werN: 0, w: m.params.w, thr: m.params.lif_thr });
      $('#feed').innerHTML = ''; $('#wakes').innerHTML = ''; $('#summaryCard').classList.add('hidden');
      $('#liveStatus').textContent = `Session ${m.id} · ${m.asr}`; setBusy(true); renderHeader();
      document.body.classList.add('is-live'); break;
    case 'frame':
      if (m.session !== S.sessionId) {          // joined mid-session (stream connected late): adopt it
        Object.assign(S, { sessionId: m.session, cols: [], audioSec: 0, awakeSec: 0, spikes: 0, nWakes: 0, werE: 0, werN: 0 });
        $('#feed').innerHTML = ''; $('#wakes').innerHTML = ''; setBusy(true);
        $('#liveStatus').textContent = `Session ${m.session}`;
        document.body.classList.add('is-live');
      }
      pushFrame(m); renderLiveKpis(); break;
    case 'wake': { S.nWakes++; const last = S.cols[S.cols.length - 1]; if (last) last.wake = true; S.flare = 1; addWake(m); break; }
    case 'segment': addSegment(m); break;
    case 'session_end': document.body.classList.remove('is-live'); endSession(m); break;
    case 'learn': if (m.params) { S.w = m.params.w; S.thr = m.params.lif_thr; if (S.state) S.state.snn = m.params; } scheduleLab(); break;
    case 'achievement': toast(`<b>🏆 ${esc(m.title)}</b>${esc(m.desc)}`, 'award', 6000); refreshState(); break;
    case 'kpi': if (S.state) { S.state.kpis = m; renderLiveKpis(); } scheduleLab(); break;
    case 'bank': if (S.state) { S.state.bank.clips = m.clips; S.state.bank.building = !m.done; renderHeader(); if (m.done) toast('Voice bank ready: spoken demo audio is available'); } break;
    case 'settings': if (S.state) { const { type, ts, ...rest } = m; S.state.settings = rest; renderHeader(); } break;
    case 'warn': toast(esc(m.msg), 'err', 6000); break;
  }
}
function scheduleLab() { if (S.tab !== 'insights') return; clearTimeout(labTimer); labTimer = setTimeout(loadLab, 600); }
function setBusy(b) { $('#btnSim').disabled = b; $('#fileWav').disabled = b; $('#btnMic').disabled = b && !S.mic; }

function addWake(m) {
  const box = $('#wakes'); if (box.querySelector('.empty')) box.innerHTML = '';
  const d = document.createElement('div'); d.className = 'wake'; d.dataset.id = m.id;
  d.innerHTML = `<span class="t">${m.t.toFixed(1)}s</span><span class="r">Woke the models</span><span class="pill-btns"><button data-v="1" title="Real speech">Speech</button><button data-v="0" title="False alarm">Noise</button></span>`;
  box.prepend(d); while (box.children.length > 40) box.lastChild.remove();
}
$('#wakes').onclick = async e => {
  const b = e.target.closest('button[data-v]'); if (!b) return; const row = b.closest('.wake');
  try {
    const r = await api.post('/api/feedback/event', { id: +row.dataset.id, verdict: +b.dataset.v });
    const ok = b.dataset.v === '1';
    row.classList.add(ok ? 'good' : 'bad'); row.querySelector('.r').textContent = ok ? 'Speech · synapses reinforced' : 'Noise · threshold raised';
    row.querySelector('.pill-btns').remove(); S.w = r.params.w; S.thr = r.params.lif_thr; setLevel(r.level); toast('+4 XP · listener updated', '', 1600);
  } catch (x) { err(x); }
};
$('#btnMissed').onclick = async () => { try { const r = await api.post('/api/feedback/missed'); S.w = r.params.w; S.thr = r.params.lif_thr; setLevel(r.level); toast('Listener made more sensitive · +4 XP', '', 2000); } catch (x) { err(x); } };

function addSegment(m) {
  const feed = $('#feed'); if (feed.querySelector('.empty')) feed.innerHTML = '';
  const d = document.createElement('div'); d.className = 'seg';
  const refN = m.ref ? (m.ref.match(/[a-z0-9']+/gi) || []).length : 0;
  if (m.wer != null && refN) { S.werE += m.wer * refN; S.werN += refN; renderLiveKpis(); }
  let h = `<div class="meta"><b>${m.t0.toFixed(1)}–${m.t1.toFixed(1)} s</b><span>awake ${(m.t1 - m.t0).toFixed(1)} s</span>${m.asr_ms ? `<span>recognized in ${m.asr_ms} ms</span>` : ''}${m.conf ? `<span>confidence ${Math.round(m.conf * 100)}%</span>` : ''}${m.wer != null ? `<span>word error ${Math.round(m.wer * 100)}%</span>` : ''}</div>`;
  if (!m.text) h += `<div class="empty small">${m.asr_available ? 'Speech detected, but nothing was recognized.' : 'Speech detected. No recognizer is installed, so there is no transcript.'}</div>`;
  m.sentences.forEach(s => h += sentRow(s));
  if (m.ref) h += `<div class="ref"><b>Script:</b> ${esc(m.ref)}</div>`;
  d.innerHTML = h; feed.appendChild(d); feed.scrollTop = feed.scrollHeight;
}
function sentRow(s) {
  const trained = Math.abs(s.score - .5) > .001, act = s.score > .5;
  return `<div class="sent ${act ? 'act' : ''}" data-id="${s.id}"><span class="txt">${act ? '<span class="flag">ACTION</span>' : ''}${esc(s.text)}</span><span class="sc">${trained ? Math.round(s.score * 100) + '%' : 'untrained'}</span><span class="pill-btns">${sayBtn(s.text)}<button class="lab" data-l="1" title="This is an action item">Action</button><button class="lab" data-l="0" title="Not an action item">No</button></span></div>`;
}
$('#feed').onclick = async e => {
  const b = e.target.closest('button[data-l]'); if (!b) return; const row = b.closest('.sent');
  try {
    const text = row.querySelector('.txt').textContent.replace(/^ACTION/, '').trim();
    const r = await api.post('/api/feedback/sentence', { text, label: +b.dataset.l, id: +row.dataset.id, origin: 'session' });
    row.classList.add('done'); row.classList.toggle('act', +b.dataset.l === 1);
    row.querySelector('.sc').textContent = `${Math.round(r.before * 100)}% → ${Math.round(r.after * 100)}%`;
    setLevel(r.level); toast(`+5 XP · ${r.model_was_right ? 'the model agreed' : 'model corrected'} · F1 ${r.heldout.f1.toFixed(2)}`, '', 2000);
  } catch (x) { err(x); }
};

function spokenSummary(m) {
  const parts = [];
  if (m.summary) parts.push('Summary. ' + m.summary.replace(/^- /gm, '').replace(/\n/g, '. '));
  if (m.actions && m.actions.length) parts.push(`${m.actions.length} action item${m.actions.length > 1 ? 's' : ''}. ` + m.actions.join(' '));
  return parts.join(' ');
}
function endSession(m) {
  setBusy(false); if (S.mic) { const mm = S.mic; S.mic = null; try { mm.proc.disconnect(); mm.stream.getTracks().forEach(t => t.stop()); mm.ctx.close(); } catch { } } stopMicUI();
  $('#liveStatus').textContent = `Session ${m.id} finished`;
  const c = $('#summaryCard'); c.classList.remove('hidden');
  const said = spokenSummary(m);
  const vs = m.f1 != null ? `<span>Trigger precision</span><span>${fmt(m.precision, 2)}</span><span>Trigger recall</span><span>${fmt(m.recall, 2)}</span>` : '';
  c.innerHTML = `<div class="card-head"><h2>Session ${m.id}</h2><div class="row"><span class="muted">${esc(m.summary_backend === 'extractive' ? 'Extractive summary' : m.summary_backend)}</span>${said ? `<button class="btn small ghost" data-say="${esc(said)}">Read aloud</button>` : ''}</div></div>
    <div class="summary-grid"><div>
      <h4>Summary</h4>${m.summary ? `<ul>${m.summary.split('\n').map(l => `<li>${esc(l.replace(/^- /, ''))}</li>`).join('')}</ul>` : '<p class="muted">No transcript, so there is nothing to summarize.</p>'}
      <h4 style="margin-top:18px">Action items</h4>${m.actions.length ? '<ul>' + m.actions.map(a => `<li>${esc(a)}</li>`).join('') + '</ul>' : '<p class="muted">None flagged. Teach the model on the Teach tab.</p>'}
    </div><div><h4>Measured</h4><div class="kv"><span>Audio</span><span>${m.audio_sec} s</span><span>Models awake</span><span>${m.awake_sec} s · ${(m.duty * 100).toFixed(1)}%</span><span>Wake events</span><span>${m.events}</span>${vs}${m.wer != null ? `<span>End-to-end word error</span><span>${Math.round(m.wer * 100)}%</span>` : ''}<span>Listener speed</span><span>${m.snn_rtf}× realtime</span></div></div></div>`;
  setLevel(m.level); refreshState();
  if (S.state && S.state.settings.auto_read && said) Voice.speak(said);
  if (m.f1 != null) api.get('/api/sessions/' + m.id).then(d => d.events.forEach(ev => {
    const row = $(`.wake[data-id="${ev.id}"]`); if (!row || ev.verdict == null || !ev.auto) return;
    row.classList.add(ev.verdict ? 'good' : 'bad'); row.querySelector('.r').textContent = ev.verdict ? 'Speech (known script)' : 'False alarm (known script)';
    const pb = row.querySelector('.pill-btns'); if (pb) pb.remove();
  })).catch(() => { });
}

/* controls */
$('#simSource').onclick = async e => { const b = e.target.closest('button'); if (!b) return; try { S.state.settings = await api.post('/api/settings', { sim_source: b.dataset.v }); renderHeader(); } catch (x) { err(x); } };
$('#btnSim').onclick = async () => {
  try { const r = await api.post('/api/sim', { hard: +$('#hard').value, speed: 6 }); if (S.state.settings.sim_source === 'voice' && !/voice/.test(r.audio)) toast('Voice bank is still building, so this demo uses synthetic audio'); }
  catch (e) { err(e); }
};
$('#fileWav').onchange = async e => { const f = e.target.files[0]; if (!f) return; try { await api.raw('/api/upload?name=' + encodeURIComponent(f.name), f); } catch (x) { err(x); } e.target.value = ''; };
$('#btnMic').onclick = async () => {
  if (S.mic) return stopMic();
  let id = null;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    id = (await api.post('/api/live/start')).id;
    const ctx = new AudioContext(), src = ctx.createMediaStreamSource(stream), proc = ctx.createScriptProcessor(4096, 1, 1), mute = ctx.createGain(); mute.gain.value = 0;
    const ratio = ctx.sampleRate / 16000; let pos = 0, carry = [];
    proc.onaudioprocess = ev => {
      const x = ev.inputBuffer.getChannelData(0);
      for (; pos < x.length - 1; pos += ratio) { const i = Math.floor(pos), f = pos - i; carry.push(x[i] * (1 - f) + x[i + 1] * f); }
      pos -= x.length;
      if (carry.length >= 8000) { const b = new Int16Array(carry.length); carry.forEach((v, k) => b[k] = Math.max(-1, Math.min(1, v)) * 32767); carry = []; fetch('/api/live/chunk?sid=' + id, { method: 'POST', headers: H, body: b.buffer }).catch(() => { }); }
    };
    src.connect(proc); proc.connect(mute); mute.connect(ctx.destination);
    S.mic = { id, stream, ctx, proc }; $('#btnMic').classList.add('recording'); $('#btnMic').querySelector('.lbl').textContent = 'Stop listening'; $('#btnMic').disabled = false;
  } catch (e) { if (id) api.post('/api/live/stop', { sid: id }).catch(() => { }); err(e.name === 'NotAllowedError' ? new Error('Microphone permission was denied.') : e); }
};
function stopMicUI() { $('#btnMic').classList.remove('recording'); $('#btnMic').querySelector('.lbl').textContent = 'Start listening'; }
async function stopMic() {
  const m = S.mic; S.mic = null; stopMicUI();
  try { m.proc.disconnect(); m.stream.getTracks().forEach(t => t.stop()); m.ctx.close(); } catch { }
  try { await api.post('/api/live/stop', { sid: m.id }); } catch (e) { err(e); }
}

/* ---------------- teach ---------------- */
const T = { card: null, answered: false, combo: 0, n: 0, agree: 0, refN: 0, timer: null, f1: null, preq: null };
async function loadCard() {
  clearTimeout(T.timer); T.answered = false;
  try { T.card = await api.get('/api/teach/next'); } catch (e) { return err(e); }
  $('#teachText').textContent = T.card.text; $('#teachAnswer').classList.add('hidden'); $('#teachCard').className = 'card teach-card';
  $('#teachOrigin').textContent = T.card.origin === 'session' ? 'From your recordings' : 'Practice card';
  renderTeachStats();
}
function nextCard() { if (T.answered) loadCard(); }
async function answerCard(label) {
  if (T.answered || !T.card) return; T.answered = true; const c = T.card;
  try {
    const r = await api.post('/api/feedback/sentence', { text: c.text, label, id: c.id, origin: c.origin, oracle: c.oracle });
    T.n++; let matched = null;
    if (c.origin === 'practice') { T.refN++; matched = label === c.oracle; if (matched) T.agree++; T.combo = matched ? T.combo + 1 : 0; } else T.combo++;
    $('#comboN').textContent = T.combo; $('#combo').classList.toggle('hot', T.combo >= 5);
    $('#teachCard').classList.add(label ? 'yes' : 'no');
    const untrained = Math.abs(r.before - .5) < .001, guess = r.before > .5 ? 'Action' : 'Not action';
    const bar = p => `<div class="conf"><span></span><i style="left:${Math.min(p, .5) * 100}%;right:${100 - Math.max(p, .5) * 100}%"></i></div>`;
    $('#teachAnswer').innerHTML = `<div class="r2"><span>Model's guess</span><b class="${untrained ? '' : r.model_was_right ? 'ok' : 'no'}">${untrained ? 'No idea yet' : `${guess} · ${Math.round(Math.max(r.before, 1 - r.before) * 100)}% sure ${r.model_was_right ? '✓' : '✗'}`}</b></div>${bar(r.before)}
      <div class="r2"><span>After learning from you</span><b>${Math.round(r.after * 100)}% action</b></div>
      ${matched != null ? `<div class="r2"><span>Reference answer</span><b class="${matched ? 'ok' : 'no'}">${c.oracle ? 'Action' : 'Not action'}${matched ? ' · you matched (+3 XP)' : ' · learned from the reference'}</b></div>` : ''}
      <div class="r2"><span>Held-out F1</span><b>${r.heldout.f1.toFixed(3)}</b></div>`;
    $('#teachAnswer').classList.remove('hidden'); setLevel(r.level); T.f1 = r.heldout.f1; T.preq = r.preq; renderTeachStats();
    T.timer = setTimeout(loadCard, 1800);
  } catch (e) { T.answered = false; err(e); }
}
$('#teachYes').onclick = () => answerCard(1); $('#teachNo').onclick = () => answerCard(0);
$('#teachSpeak').onclick = e => { e.stopPropagation(); Voice.speak($('#teachText').textContent); };
$('#teachCard').onclick = () => nextCard();
function renderTeachStats() {
  $('#teachStats').innerHTML = stat('Labels this session', T.n) + stat('Matched reference', T.refN ? Math.round(T.agree / T.refN * 100) + '<small>%</small>' : '–') +
    stat('Held-out F1', T.f1 != null ? T.f1.toFixed(2) : '–') + stat('Model right, lifetime', T.preq != null ? Math.round(T.preq * 100) + '<small>%</small>' : '–');
}

/* ---------------- insights ---------------- */
function setTrainBusy(b) { $$('[data-train]').forEach(x => x.disabled = b); }
$$('[data-train]').forEach(b => b.onclick = async () => {
  try { setTrainBusy(true); const j = await api.post('/api/train', { kind: b.dataset.train, episodes: 10 }); $('#trainBar').classList.remove('hidden'); $('#trainTxt').textContent = 'Starting…'; $('#trainCancel').dataset.cancel = j.id; }
  catch (e) { setTrainBusy(false); err(e); }
});
async function loadLab() {
  let d; try { d = await api.get('/api/stats'); } catch (e) { return err(e); }
  if (S.state) S.state.kpis = d.kpis;
  const k = d.kpis, lastC = d.clf[d.clf.length - 1], firstC = d.clf[0];
  const isVoice = r => /^voice/.test(r.note || ''), snnV = d.snn.filter(isVoice), snnS = d.snn.filter(r => !isVoice(r));
  const snnMain = snnV.length ? snnV : snnS, lastS = snnMain[snnMain.length - 1], firstS = snnMain[0];
  const from = (a, b) => a && b && a !== b ? ` · from ${a.f1.toFixed(2)}` : '';
  $('#labTiles').innerHTML =
    stat('Heavy models awake', k.duty == null ? '–' : (k.duty * 100).toFixed(1) + '<small>%</small>', `${k.awake_sec} s of ${k.audio_sec} s`, k.duty) +
    stat('Word error rate', k.asr_wer == null ? '–' : Math.round(k.asr_wer * 100) + '<small>%</small>', k.asr_wer == null ? 'run a spoken demo' : `${k.asr_words} words scored`) +
    stat('Action-item F1', lastC ? lastC.f1.toFixed(2) : '–', lastC ? `unseen phrasing${from(firstC, lastC)}` : 'press Train') +
    stat('Listener F1', lastS ? lastS.f1.toFixed(2) : '–', lastS ? `${snnV.length ? 'spoken' : 'synthetic'} audio${from(firstS, lastS)}` : 'press Train') +
    stat('Your teaching', k.preq_acc == null ? '–' : Math.round(k.preq_acc * 100) + '<small>%</small>', `model right on ${d.model.clf_feedback} labels`) +
    stat('Spikes processed', fmt(k.spikes, 0), `${k.events} wakes · ${k.sessions} sessions`);

  const g = $('#labGrid');
  const ids = ['clfCurve', 'clfLoss', 'snnCurve', 'preqCurve', 'werSess', 'werConf', 'bandW', 'bandAct', 'feats', 'duty', 'calib', 'hist'];
  if (!g.children.length) g.innerHTML = ids.map(id => `<div class="card"><div id="ch-${id}"></div></div>`).join('') +
    `<div class="card third"><div class="card-head"><h2>Listener</h2><span class="muted">learned parameters</span></div><div id="kvSnn" class="kv"></div></div>
     <div class="card third"><div class="card-head"><h2>Models</h2><span class="muted">what is learning</span></div><div id="kvModel" class="kv"></div></div>
     <div class="card third"><div class="card-head"><h2>This device</h2><span class="muted">hardware</span></div><div id="kvHw" class="kv"></div></div>
     <div class="card full"><div class="card-head"><h2>Sessions</h2><span class="muted">per-session measurements</span></div><div id="sessTable" class="tbl-wrap"></div></div>`;
  const ser = (rows, key) => rows.filter(r => r[key] != null).map((r, i) => [i, r[key]]);
  const trend = (rows, key) => rows.length > 1 ? `<em>${rows[0][key].toFixed(2)} → ${rows[rows.length - 1][key].toFixed(2)}</em> over ${rows.length - 1} updates` : '';
  Charts.line($('#ch-clfCurve'), { title: 'Classifier', subtitle: `<b>Action-item classifier</b>Held-out accuracy and F1 on unseen phrasing. ${trend(d.clf, 'f1')}`, series: [{ name: 'Accuracy', color: 's1', points: ser(d.clf, 'acc') }, { name: 'F1', color: 's3', points: ser(d.clf, 'f1') }], yMin: 0, yMax: 1, xName: 'Update', xLabel: 'updates', empty: 'Untrained. Press Train, or label cards on the Teach tab.' });
  Charts.line($('#ch-clfLoss'), { title: 'Loss', subtitle: '<b>Held-out log-loss</b>Lower means more confident and more correct.', series: [{ name: 'Log-loss', color: 's4', points: ser(d.clf, 'loss') }], yMin: 0, xName: 'Update', empty: 'No training yet.' });
  const snnSeries = [];
  if (snnV.length) snnSeries.push({ name: 'F1 · spoken', color: 's3', points: ser(snnV, 'f1') }, { name: 'Precision · spoken', color: 's1', points: ser(snnV, 'prec') }, { name: 'Recall · spoken', color: 's7', points: ser(snnV, 'rec') });
  if (snnS.length) snnSeries.push({ name: 'F1 · synthetic', color: 's2', points: ser(snnS, 'f1') });
  Charts.line($('#ch-snnCurve'), { title: 'Listener', subtitle: `<b>Spiking listener</b>Held-out wake detection on ${snnV.length ? 'spoken (voice bank)' : 'synthetic'} audio. ${trend(snnMain, 'f1')}`, series: snnSeries, yMin: 0, yMax: 1, xName: 'Episode', xLabel: 'training episodes', empty: 'Untrained. Press Train.' });
  Charts.line($('#ch-preqCurve'), { title: 'Teaching', subtitle: '<b>Model right on your labels</b>Running accuracy, measured before each update.', series: [{ name: 'Running accuracy', color: 's5', points: d.preq }], yMin: 0, yMax: 1, xName: 'Label', xLabel: 'your labels', refLine: { y: .5, label: 'chance' }, empty: 'Label a few cards on the Teach tab.' });
  const ws = d.sessions.filter(s => s.wer != null);
  Charts.bars($('#ch-werSess'), { title: 'Word error', subtitle: `<b>Speech recognition word error</b>End-to-end per session vs. the known script · ${esc(d.asr.name)}`, items: ws.map(s => ({ label: '#' + s.id, value: s.wer, color: 's2', tip: `<b>Session ${s.id}</b><br>${Math.round(s.wer * 100)}% word error<br>${esc(s.mode)} · ${s.audio_sec.toFixed(0)} s` })), yMax: 1, yFmt: v => Math.round(v * 100) + '%', labelName: 'session', valueName: 'word error', empty: 'Run a spoken demo to measure recognition accuracy.' });
  Charts.bars($('#ch-werConf'), { title: 'Recognizer confidence', subtitle: '<b>Does the recognizer know when it is wrong?</b>Word error grouped by the recognizer\'s own confidence.', items: d.asr_conf_bins.map(c => ({ label: `${Math.round(c.lo * 100)}–${Math.round(c.lo * 100) + 20}%`, value: c.wer, color: 's2', tip: `<b>confidence ${Math.round(c.lo * 100)}–${Math.round(c.lo * 100) + 20}%</b><br>${c.n} segments · ${c.wer == null ? 'no data' : Math.round(c.wer * 100) + '% word error'}` })), yFmt: v => Math.round(v * 100) + '%', labelName: 'confidence', valueName: 'word error', empty: 'Run a spoken demo first.' });
  Charts.bars($('#ch-bandW'), { title: 'Synapses', subtitle: '<b>Learned synaptic weight per band</b>Negative weights are inhibitory.', items: d.band_w.map((w, b) => ({ label: BAND_KHZ[b], value: w, color: w >= 0 ? 's3' : 's2', tip: `<b>${BAND_KHZ[b]} kHz</b><br>weight ${w.toFixed(2)}${w < 0 ? ' (inhibitory)' : ''}` })), legend: [{ name: 'Excitatory', color: 's3' }, { name: 'Inhibitory', color: 's2' }], labelName: 'band (kHz)', valueName: 'weight' });
  Charts.bars($('#ch-bandAct'), { title: 'Band activity', subtitle: '<b>Spikes per frequency band</b>Across all sessions.', items: d.band_totals.map((v, b) => ({ label: BAND_KHZ[b], value: v, color: 's1', tip: `<b>${BAND_KHZ[b]} kHz</b><br>${fmt(v, 0)} spikes` })), yFmt: v => fmt(v, 0), labelName: 'band (kHz)', valueName: 'spikes', empty: 'Run a session first.' });
  const nice = n => n === '__modal' ? 'deadline / modal cue' : n === '__question' ? 'is a question' : /^__len/.test(n) ? 'length bucket ' + n.slice(5) : n.replace(/_/g, ' ');
  const fp = d.features.pos.map(f => ({ label: nice(f[0]), value: f[1], color: 's3' })), fn = d.features.neg.map(f => ({ label: nice(f[0]), value: f[1], color: 's2' }));
  Charts.bars($('#ch-feats'), { title: 'Features', subtitle: '<b>What the classifier learned</b>Strongest signals for and against an action item.', items: [...fp, ...fn.reverse()], horizontal: true, labelW: 150, legend: [{ name: 'Toward action item', color: 's3' }, { name: 'Away from it', color: 's2' }], labelName: 'feature', valueName: 'weight', empty: 'Untrained.' });
  Charts.bars($('#ch-duty'), { title: 'Duty cycle', subtitle: '<b>Heavy-model duty cycle</b>Per session; lower means more compute saved.', items: d.sessions.map(s => ({ label: '#' + s.id, value: s.audio_sec ? s.awake_sec / s.audio_sec : 0, color: 's3', tip: `<b>Session ${s.id}</b> · ${esc(s.mode)}<br>awake ${s.awake_sec.toFixed(1)} of ${s.audio_sec.toFixed(1)} s<br>${s.n_events} wake events` })), yMax: 1, yFmt: v => Math.round(v * 100) + '%', baseline: { value: 1, label: 'always-on' }, labelName: 'session', valueName: 'duty', empty: 'Run a session first.' });
  Charts.bars($('#ch-calib'), { title: 'Calibration', subtitle: '<b>Calibration on your labels</b>When the model says X%, how often is it an action item?', items: d.calibration.map(c => ({ label: `${Math.round(c.lo * 100)}–${Math.round(c.lo * 100) + 20}%`, value: c.pos, color: 's1', tip: `<b>score ${Math.round(c.lo * 100)}–${Math.round(c.lo * 100) + 20}%</b><br>${c.n} labels · ${c.pos == null ? 'no data' : Math.round(c.pos * 100) + '% were action items'}` })), yMax: 1, yFmt: v => Math.round(v * 100) + '%', labelName: 'score', valueName: 'actual rate', empty: 'Label sentences from your recordings to calibrate.' });
  Charts.bars($('#ch-hist'), { title: 'Model confidence', subtitle: '<b>Model confidence</b>Action-item scores on recent sentences.', items: d.score_hist.map((v, i) => ({ label: (i / 10).toFixed(1), value: v, color: 's7', tip: `<b>score ${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}</b><br>${v} sentences` })), yFmt: v => fmt(v, 0), labelName: 'score', valueName: 'sentences', empty: 'Transcribe something first.' });
  const p = d.snn_params, cf = d.confusion, h = d.hardware;
  $('#kvSnn').innerHTML = `<span>Band threshold</span><span>${p.thresh.toFixed(2)}</span><span>Fire threshold</span><span>${p.lif_thr.toFixed(2)}</span><span>Persistence</span><span>${p.sustain.toFixed(1)} of 25 frames</span><span>Leak</span><span>${p.beta}</span><span>Refractory</span><span>${p.refractory * 10} ms</span><span>Training episodes</span><span>${d.model.snn_episodes}</span><span>Your feedback</span><span>${d.model.snn_feedback}</span>`;
  $('#kvModel').innerHTML = `<span>Classifier weights</span><span>${d.model.clf_params}</span><span>SGD steps</span><span>${d.model.clf_steps}</span><span>Your labels</span><span>${d.model.clf_feedback}</span><span>Personal layer</span><span>${d.model.personal_feats} weights · norm ${d.model.personal_norm}</span><span>Held-out TP / FP</span><span>${cf.tp} / ${cf.fp}</span><span>Held-out FN / TN</span><span>${cf.fn} / ${cf.tn}</span><span>Search boosts</span><span>${d.model.rag_boosts}</span><span>Voice bank</span><span>${d.bank.clips} clips</span>`;
  $('#kvHw').innerHTML = `<span>System</span><span>${esc(h.system)}</span><span>CPU</span><span>${esc(h.cpu)}</span><span>Cores</span><span>${h.cores}</span><span>NPU (QNN)</span><span>${h.npu ? 'Available' : 'Not available'}</span><span>Listener speed</span><span>${d.bench.rtf}× realtime</span><span>Per frame</span><span>${d.bench.us_per_frame} µs</span><span>Recognizer</span><span>${esc(d.asr.name)}</span>`;
  renderSystem();
  $('#sessTable').innerHTML = d.sessions.length ? `<table class="t"><tr><th>Session</th><th>Audio</th><th>Length</th><th>Awake</th><th>Wakes</th><th>Precision</th><th>Recall</th><th>F1</th><th>Word error</th><th>ASR time</th></tr>` + d.sessions.slice().reverse().map(s => `<tr><td>#${s.id}</td><td>${esc(s.mode)}</td><td>${s.audio_sec.toFixed(0)} s</td><td>${s.audio_sec ? Math.round(s.awake_sec / s.audio_sec * 100) : 0}%</td><td>${s.n_events}</td><td>${fmt(s.precision, 2)}</td><td>${fmt(s.recall, 2)}</td><td>${fmt(s.f1, 2)}</td><td>${s.wer == null ? '–' : Math.round(s.wer * 100) + '%'}</td><td>${Math.round(s.asr_ms)} ms</td></tr>`).join('') + '</table>' : '<div class="empty small">No sessions yet.</div>';
}

/* ---------------- ask ---------------- */
$('#askForm').onsubmit = async e => {
  e.preventDefault(); const q = $('#askQ').value.trim(); if (!q) return;
  try {
    const kind = ($('#askKind .on') || {}).dataset?.v || null;
    const { results } = await api.post('/api/ask', { q, kind: kind || null }); const words = q.toLowerCase().split(/\W+/).filter(w => w.length > 2);
    $('#askOut').innerHTML = results.length ? results.map(r => `<div class="hit"><div class="src"><span>${r.kind === 'session' ? `<button class="linkish" data-open="session:${r.ref_id}"><b>${esc(r.source)}</b></button>` : `<b>${esc(r.source)}</b>`} · relevance <span class="relbar"><i style="width:${Math.round(r.score * 100)}%"></i></span>${r.boost ? ` · learned ${r.boost > 0 ? '+' : ''}${r.boost.toFixed(2)}` : ''}</span><span class="pill-btns">${sayBtn(r.text)}<button data-k="${r.key}" data-v="1" title="Helpful">👍</button><button data-k="${r.key}" data-v="-1" title="Not helpful">👎</button></span></div>${hl(r.text, words)}</div>`).join('')
      : '<div class="card empty"><b>No matches</b>Add notes on the right, or run a session first.</div>';
  } catch (x) { err(x); }
};
function hl(t, words) { let h = esc(t); words.forEach(w => { h = h.replace(new RegExp('\\b(' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\w*)', 'gi'), '<mark>$1</mark>'); }); return h; }
$('#askKind').onclick = e => { const b = e.target.closest('button'); if (!b) return; $$('#askKind button').forEach(x => x.classList.toggle('on', x === b)); if ($('#askQ').value.trim()) $('#askForm').requestSubmit(); };
$('#askOut').onclick = async e => { const b = e.target.closest('button[data-k]'); if (!b) return; try { await api.post('/api/ask/feedback', { key: b.dataset.k, val: +b.dataset.v }); toast(`Got it: this result will rank ${b.dataset.v > 0 ? 'higher' : 'lower'}`, '', 1800); } catch (x) { err(x); } };
$('#docFile').onchange = async e => { const f = e.target.files[0]; if (f) { $('#docText').value = await f.text(); $('#docName').value = f.name; } };
$('#docAdd').onclick = async () => { const text = $('#docText').value.trim(); if (!text) return; try { const r = await api.post('/api/docs', { text, name: $('#docName').value || 'Note' }); $('#docNote').textContent = `Added · ${r.chunks} passages indexed on this device.`; $('#docText').value = ''; $('#docName').value = ''; } catch (x) { err(x); } };

/* ---------------- library ---------------- */
async function loadLibrary() {
  let rows; try { rows = await api.get('/api/sessions'); } catch (e) { return err(e); }
  $('#archList').innerHTML = rows.length ? rows.map(s => `<button data-id="${s.id}"><b>Session ${s.id} · ${esc(s.source)}</b><small>${new Date(s.started * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })} · ${s.audio_sec.toFixed(0)} s · awake ${s.audio_sec ? Math.round(s.awake_sec / s.audio_sec * 100) : 0}%</small></button>`).join('') : '<div class="empty small">No sessions yet.</div>';
}
$('#archList').onclick = async e => {
  const b = e.target.closest('button'); if (!b) return; $$('#archList button').forEach(x => x.classList.toggle('on', x === b));
  try {
    const d = await api.get('/api/sessions/' + b.dataset.id), s = d.session;
    const acts = (s.actions || '').split('\n').filter(Boolean), said = spokenSummary({ summary: s.summary, actions: acts });
    $('#archDetail').innerHTML = `<div class="detail"><div class="detail-head"><h2>Session ${s.id}</h2>${said ? `<button class="btn small ghost" data-say="${esc(said)}">Read aloud</button>` : ''}</div>
      <div class="kv"><span>Source</span><span>${esc(s.source)} · ${esc(s.mode)}</span><span>Recognizer</span><span>${esc(s.asr_backend || '–')}</span><span>Audio / awake</span><span>${s.audio_sec.toFixed(1)} s / ${s.awake_sec.toFixed(1)} s</span><span>Wake events</span><span>${s.n_events}</span>${s.wer != null ? `<span>Word error</span><span>${Math.round(s.wer * 100)}%</span>` : ''}</div>
      <h4>Summary</h4>${s.summary ? `<ul>${s.summary.split('\n').map(l => `<li>${esc(l.replace(/^- /, ''))}</li>`).join('')}</ul>` : '<p class="muted">None</p>'}
      <div id="linksSlot"></div><h4>Transcript</h4>${d.segments.map(g => `<div class="seg"><div class="meta"><b>${g.t0.toFixed(1)}–${g.t1.toFixed(1)} s</b>${g.wer_n ? `<span>word error ${Math.round(g.wer_err / g.wer_n * 100)}%</span>` : ''}</div>${g.text ? `<div class="sent"><span class="txt">${esc(g.text)}</span><span class="pill-btns">${sayBtn(g.text)}</span></div>` : '<p class="muted small">(no transcript)</p>'}${g.ref ? `<div class="ref"><b>Script:</b> ${esc(g.ref)}</div>` : ''}</div>`).join('') || '<p class="muted">No transcript.</p>'}</div>`;
    renderLinksInto(s.id);
  } catch (x) { err(x); }
};
async function renderLinksInto(sid) { if (typeof renderLinks === 'function') { await renderLinks(sid); const box = $('#archDetail .detail:last-child'), slot = $('#linksSlot'); if (box && slot && box !== slot.closest('.detail')) slot.replaceWith(box); } }

/* ---------------- settings ---------------- */
function segSet(sel, v) { $$(sel + ' button').forEach(b => { const on = b.dataset.v === v; b.classList.toggle('on', on); b.setAttribute('aria-checked', on); }); }
function renderVoiceSelect() {
  const st = S.state.settings, opts = Voice.options(st.tts_mode), cur = Voice.resolve();
  $('#ttsVoice').innerHTML = opts.length ? opts.map(o => `<option value="${esc(o.id)}"${cur && o.id === cur.id ? ' selected' : ''}>${esc(o.label)}</option>`).join('') : '<option>No voices available</option>';
  const cloud = Voice.web.filter(v => !v.localService).length, note = $('#ttsModeNote');
  note.className = 'cell note' + (st.tts_mode === 'online' ? ' warn' : '');
  note.textContent = st.tts_mode === 'online'
    ? `Online voices send the text being read to your browser's speech provider (for example Google or Microsoft). ${cloud ? cloud + ' cloud voices found.' : 'This browser offers no cloud voices, so offline voices are used.'}`
    : 'Offline: speech is generated on this device. Nothing leaves it.';
}
async function renderSettings() {
  const st = S.state.settings;
  segSet('#ttsMode', st.tts_mode); segSet('#themeSel', store.get('es-theme', 'dark'));
  applyAccent(store.get('es-accent', 'steel'));
  $('#ttsRate').value = st.tts_rate; $('#ttsRateOut').textContent = (st.tts_rate > 0 ? '+' : '') + st.tts_rate;
  $('#autoRead').checked = st.auto_read; $('#denoise').checked = st.denoise;
  $('#bankCount').textContent = `${S.state.bank.clips} of ${S.state.bank.target}`;
  $('#bankNote').textContent = 'Spoken sentences used to train and score the listener';
  renderVoiceSelect();
  try {
    const h = await api.get('/api/health');
    $('#healthList').innerHTML = h.components.map(c => `<div class="cell"><span class="hrow"><i class="hdot ${c.status}"></i>${esc(c.name)}</span><span class="muted" style="text-align:right">${esc(c.detail)}</span></div>`).join('') + `<div class="cell"><span>Network</span><span class="muted">${esc(h.network)}</span></div>`;
  } catch (e) { $('#healthList').innerHTML = `<div class="cell">${esc(e.message)}</div>`; }
}
async function saveSetting(patch) { try { S.state.settings = await api.post('/api/settings', patch); renderHeader(); renderVoiceSelect(); } catch (e) { err(e); renderSettings(); } }
$('#btnSettings').onclick = async () => { try { await Voice.load(); await refreshState(); } catch (e) { err(e); } renderSettings(); $('#settings').showModal(); };
$('#setDone').onclick = () => $('#settings').close();
$('#settings').addEventListener('click', e => { if (e.target === $('#settings')) $('#settings').close(); });
$('#ttsMode').onclick = e => { const b = e.target.closest('button'); if (b) { segSet('#ttsMode', b.dataset.v); saveSetting({ tts_mode: b.dataset.v }); } };
$('#themeSel').onclick = e => { const b = e.target.closest('button'); if (b) { store.set('es-theme', b.dataset.v); applyTheme(b.dataset.v); } };
$('#accentSel').onclick = e => { const b = e.target.closest('.swatch'); if (b) { store.set('es-accent', b.dataset.v); applyAccent(b.dataset.v); } };
$('#ttsVoice').onchange = e => saveSetting({ tts_voice: e.target.value });
$('#ttsRate').oninput = e => $('#ttsRateOut').textContent = (e.target.value > 0 ? '+' : '') + e.target.value;
$('#ttsRate').onchange = e => saveSetting({ tts_rate: +e.target.value });
$('#autoRead').onchange = e => saveSetting({ auto_read: e.target.checked });
$('#denoise').onchange = e => saveSetting({ denoise: e.target.checked });
$('#ttsTest').onclick = () => { const v = Voice.resolve(); $('#ttsTestNote').textContent = v ? v.label : ''; Voice.speak('Reminder: submit the lab report by Friday. This is EdgeScribe speaking.'); };
$('#btnReset').onclick = async () => {
  if (!confirm('Erase every session, learned weight, note and XP on this device? Settings are kept.')) return;
  try { await api.post('/api/reset'); Object.assign(T, { n: 0, agree: 0, refN: 0, combo: 0, f1: null, preq: null }); await refreshState(); $('#labGrid').innerHTML = ''; toast('Reset: the models are back to zero knowledge'); $('#settings').close(); }
  catch (e) { err(e); }
};

/* ---------------- boot ---------------- */
(async function init() {
  applyTheme(store.get('es-theme', 'dark'));
  applyAccent(store.get('es-accent', 'steel'));
  moveInk();
  try { await refreshState(); } catch (e) { err(e); }
  Voice.load(); connect(); renderLiveKpis(); renderTeachStats();
  if (S.state && S.state.training) setTrainBusy(true);
  if (S.state && S.state.running.length) setBusy(true);
})();
