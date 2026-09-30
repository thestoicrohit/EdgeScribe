/* Guided demo tour: walks through every section, spotlighting the parts that matter.
   Start it from the "Tour" button in the header (or press T). Keys: → / ← / Esc. Local only, no network. */
(function () {
  const STEPS = [
    { title: 'Welcome to EdgeScribe', body: 'A private study and meeting assistant. Everything runs on this laptop: the server listens on 127.0.0.1, nothing is downloaded, and audio never leaves the machine. This 2-minute tour shows how it is put together.' },
    { title: 'How it works', flow: ['Microphone / WAV / demo', 'Spiking listener (CPU)', 'Speech recognition', 'Sentences → action items', 'Summary + local search'],
      body: 'A tiny spiking neural network listens all the time for almost no compute, and wakes the heavy models only while someone is talking. Its output feeds recognition, then the action-item classifier, the summary and the search index.' },
    { tab: 'live', sel: '.controls', title: 'Start here: Live', body: 'Press <b>Start listening</b> to use your microphone, <b>Run demo</b> for scripted audio you can score, or <b>Import audio</b> for a WAV file. The slider sets how noisy the demo room is.' },
    { tab: 'live', sel: '#raster', title: 'The spiking listener', body: '16 frequency bands produce spikes; a leaky integrate-and-fire neuron adds them up. When its membrane potential crosses the threshold, it wakes the recognizer (amber marks, shaded while models are awake).' },
    { tab: 'live', sel: '#brain', title: 'A neuron that learns', body: 'Each line is a learned synapse. Green ones excite the neuron, orange ones inhibit it. Your ✓ / ✗ feedback on wake events changes them.' },
    { tab: 'live', sel: '#feed', title: 'Transcript and teaching', body: 'Sentences appear here with the known script next to them in demos, plus per-segment word error. Flag a sentence as an action item, or dismiss it, and the classifier learns from you.' },
    { tab: 'today', sel: '#taskList', title: 'Today: tasks made for you', body: 'Action items become tasks automatically, with due dates parsed from speech ("by Friday at 4pm"). Type your own in plain English. <b>Read my day</b> speaks a digest.' },
    { tab: 'today', sel: '#topicCloud', title: 'Topics link everything', body: 'Key phrases are pulled from sessions and notes and connect them. Click one to see where it appears. Phrases from real speech must be corroborated so misheard words do not become topics.' },
    { tab: 'study', sel: '#flash', title: 'Study: flashcards', body: 'Cards are generated from your notes and clean transcripts (definitions and fill-in-the-blank) and scheduled with spaced repetition. Space reveals the answer; 1–4 grades it.' },
    { tab: 'teach', sel: '#teachCard', title: 'Teach: a card game', body: 'Label sentences as action items or not. The model shows its guess and confidence, the least-sure cards come first, and every answer retrains it instantly. Streaks and XP make it addictive.' },
    { tab: 'insights', sel: '#labTiles', title: 'Insights: measured, not claimed', body: 'Held-out F1, word error, spikes processed, compute saved. The charts below show learning curves, calibration and synapse weights, and the System panel shows every background reactor and job.' },
    { tab: 'insights', sel: '.toolbar', title: 'Train it yourself', body: '<b>Train both</b> runs cancellable background jobs for the listener and the classifier. Watch the curves move. <b>Back up</b> downloads the whole database.' },
    { tab: 'ask', sel: '#askForm', title: 'Ask: local search', body: 'BM25 search over all transcripts and notes. 👍 / 👎 on a result re-ranks future searches. Results link back to their session.' },
    { tab: 'library', sel: '#archList', title: 'Library: every session', body: 'Open a session for its transcript, script, summary, topics, tasks, related sessions and cards. Export it as Markdown.' },
    { sel: '#btnSettings', title: 'Settings', body: 'Choose offline or opt-in online voices, speech speed, noise suppression, and the theme: Dark, Light or Auto, with Steel, Mist or Slate accents.' },
    { title: 'That is the whole loop', body: 'Listen → transcribe → tasks, topics, cards → search → teach → measure. Want to see it move? I can run a live demo for you now.', finish: true }
  ];

  let i = -1, els = null;
  const $ = s => document.querySelector(s);

  function build() {
    const root = document.createElement('div');
    root.className = 'tour'; root.setAttribute('role', 'dialog'); root.setAttribute('aria-label', 'Guided tour');
    root.innerHTML = '<div class="tour-spot"></div><div class="tour-card"><div class="tour-step"></div><h3></h3><div class="tour-flow hidden"></div><p></p>' +
      '<div class="tour-dots"></div><div class="tour-actions"><button class="btn small ghost" data-a="skip">Skip</button><span></span>' +
      '<button class="btn small" data-a="back">Back</button><button class="btn small primary" data-a="next">Next</button></div></div>';
    document.body.appendChild(root);
    root.querySelector('.tour-actions').onclick = e => {
      const a = e.target.closest('button'); if (!a) return;
      ({ skip: () => { i = 0; end(); }, back: () => go(i - 1), next: () => go(i + 1) })[a.dataset.a]();
    };
    return { root, spot: root.querySelector('.tour-spot'), card: root.querySelector('.tour-card') };
  }

  function place(step) {
    const spot = els.spot, card = els.card, vw = innerWidth, vh = innerHeight;
    const t = step.sel && $(step.sel);
    if (!t) { spot.style.cssText = 'left:50%;top:50%;width:0;height:0'; card.style.cssText = 'left:50%;top:50%;transform:translate(-50%,-50%)'; return; }
    t.scrollIntoView({ block: 'center', behavior: 'instant' });
    const r = t.getBoundingClientRect(), pad = 10;
    spot.style.cssText = `left:${r.left - pad}px;top:${r.top - pad}px;width:${r.width + pad * 2}px;height:${r.height + pad * 2}px`;
    const cw = Math.min(400, vw - 24), ch = card.offsetHeight || 260;
    let x = Math.min(Math.max(12, r.left), vw - cw - 12), y;
    if (r.bottom + ch + 24 < vh) y = r.bottom + 20; else if (r.top - ch - 24 > 0) y = r.top - ch - 20;
    else { y = Math.max(12, vh - ch - 16); x = r.left > vw / 2 ? 16 : vw - cw - 16; }
    card.style.cssText = `left:${x}px;top:${y}px;transform:none`;
  }

  function go(n) {
    if (n < 0) return;
    if (n >= STEPS.length) return end();
    i = n; const s = STEPS[i];
    if (s.tab && window.showTab) showTab(s.tab);
    setTimeout(() => {
      els.card.querySelector('.tour-step').textContent = `STEP ${i + 1} / ${STEPS.length}`;
      els.card.querySelector('h3').textContent = s.title;
      els.card.querySelector('p').innerHTML = s.body;
      const fl = els.card.querySelector('.tour-flow');
      fl.classList.toggle('hidden', !s.flow);
      fl.innerHTML = (s.flow || []).map((f, k) => `<span>${f}</span>${k < s.flow.length - 1 ? '<i>↓</i>' : ''}`).join('');
      els.card.querySelector('.tour-dots').innerHTML = STEPS.map((_, k) => `<i class="${k === i ? 'on' : k < i ? 'done' : ''}"></i>`).join('');
      els.card.querySelector('[data-a=back]').disabled = i === 0;
      const nx = els.card.querySelector('[data-a=next]'); nx.textContent = s.finish ? 'Run live demo' : 'Next';
      place(s); nx.focus({ preventScroll: true });
    }, s.tab && S.tab !== s.tab ? 80 : 0);
  }

  function end() {
    const fin = i === STEPS.length - 1;
    if (els) { els.root.remove(); els = null; }
    document.body.classList.remove('touring'); removeEventListener('keydown', keys, true); removeEventListener('resize', re);
    const was = i; i = -1;
    if (fin && was === STEPS.length - 1) { if (window.showTab) showTab('live'); const b = $('#btnSim'); if (b) b.click(); }
  }
  function keys(e) {
    if (e.key === 'Escape') { i = 0; e.preventDefault(); e.stopPropagation(); end(); }
    else if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); go(i + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); go(i - 1); }
  }
  const re = () => { if (els && i >= 0) place(STEPS[i]); };

  function start() {
    if (els) return;
    els = build(); document.body.classList.add('touring');
    addEventListener('keydown', keys, true); addEventListener('resize', re);
    go(0);
  }
  window.Tour = { start };

  const btn = $('#btnTour'); if (btn) btn.onclick = start;
  addEventListener('keydown', e => {
    if (e.key.toLowerCase() === 't' && !e.metaKey && !e.ctrlKey && !e.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !$('#settings').open && !els) start();
  });
})();
