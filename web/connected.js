/* EdgeScribe — connected features: Today (tasks + topics + digest), Study (flashcards), links between
   items, jobs, and the System panel. Uses helpers from app.js ($, api, esc, fmt, stat, toast, err, Voice, sayBtn, showTab). */
const fmtDue = ts => {
  if (ts == null) return null;
  const d = new Date(ts * 1000), now = new Date(), day = 864e5;
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diff = Math.floor((d.getTime() - start) / day);
  const time = (d.getHours() === 23 && d.getMinutes() === 59) ? '' : ' ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const cls = d < now ? 'overdue' : diff === 0 ? 'today' : diff <= 2 ? 'soon' : '';
  const label = d < now && diff < 0 ? `overdue · ${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}`
    : diff === 0 ? 'today' + time : diff === 1 ? 'tomorrow' + time
      : diff < 7 ? d.toLocaleDateString([], { weekday: 'long' }) + time : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + time;
  return { cls, label };
};

/* ------------------------------------------------------------------ Today */
const Today = {
  filter: 'open', topic: null, data: null,
  async load() {
    try { this.data = await api.get('/api/today'); } catch (e) { return err(e); }
    const d = this.data, st = d.cards, b = d.buckets;
    $('#todayGreeting').textContent = d.greeting; $('#todayDate').textContent = d.date;
    $('#nextAction').textContent = d.next.label; $('#nextAction').dataset.action = d.next.action;
    $('#todayStats').innerHTML =
      stat('Overdue', b.overdue.length, b.overdue.length ? 'clear these first' : 'nothing late') +
      stat('Due today', b.today.length, `${b.week.length} more this week`) +
      stat('Cards to review', st.due, `${st.new} new · ${st.total} total`) +
      stat('Streak', d.game.streak + '<small> days</small>', `${d.game.xp} XP · level ${d.game.level}`);
    this.renderTasks(); this.loadTopics();
  },
  async renderTasks() {
    const box = $('#taskList');
    if (this.filter === 'open') {
      const b = this.data.buckets, order = [['overdue', 'Overdue'], ['today', 'Today'], ['week', 'This week'], ['later', 'Later'], ['undated', 'No date']];
      const html = order.filter(([k]) => b[k].length).map(([k, t]) => `<div class="bucket ${k}"><h4>${t}<span class="n">${b[k].length}</span></h4>${b[k].map(taskRow).join('')}</div>`).join('');
      box.innerHTML = html || '<div class="empty"><b>All clear</b>Action items from your sessions show up here with their due dates.</div>';
    } else {
      const r = await api.get('/api/tasks?status=' + this.filter);
      box.innerHTML = r.items.length ? r.items.map(taskRow).join('') : `<div class="empty small">No ${this.filter} tasks.</div>`;
    }
    $('#todayDot').classList.toggle('hidden', !(this.data.buckets.overdue.length || this.data.buckets.today.length));
  },
  async loadTopics() {
    let t; try { t = await api.get('/api/topics?limit=24'); } catch (e) { return; }
    $('#topicCloud').innerHTML = t.length ? t.map(x => `<button class="pill ${x.items > 1 ? 'shared' : ''} ${this.topic === x.phrase ? 'on' : ''}" data-p="${esc(x.phrase)}">${esc(x.phrase)}<small>${x.items}</small></button>`).join('')
      : '<div class="empty small">Topics appear after your first session or note.</div>';
    if (this.topic) this.openTopic(this.topic);
  },
  async openTopic(phrase) {
    this.topic = phrase;
    $$('#topicCloud .pill').forEach(p => p.classList.toggle('on', p.dataset.p === phrase));
    const d = await api.get('/api/topics/items?phrase=' + encodeURIComponent(phrase));
    const items = d.items.map(i => `<button data-open="${i.item_type}:${i.item_id}">${esc(i.title)}<small>${esc(i.subtitle)} · ${esc(i.snippet.slice(0, 70))}…</small></button>`).join('');
    const tasks = d.tasks.map(t => `<div>${esc(t.text)}<small>task · ${t.status}${t.due_text ? ' · ' + esc(t.due_text) : ''}</small></div>`).join('');
    const cards = d.cards.map(c => `<div>${esc(c.front)}<small>card · ${esc(c.back)}</small></div>`).join('');
    $('#topicDetail').innerHTML = `<div class="mini">${items ? '<div class="k">Sessions & notes</div>' + items : ''}${tasks ? '<div class="k">Tasks</div>' + tasks : ''}${cards ? '<div class="k">Flashcards</div>' + cards : ''}</div>`;
  },
};
function taskRow(t) {
  const due = fmtDue(t.due), done = t.status === 'done';
  const src = t.session_id ? `<button class="linkish" data-open="session:${t.session_id}">Session ${t.session_id}</button>` : `<span>${esc(t.origin === 'manual' ? 'added by you' : t.origin)}</span>`;
  return `<div class="task ${done ? 'done' : ''}" data-id="${t.id}">
    <button class="check ${done ? 'on' : ''}" data-act="${done ? 'open' : 'done'}" aria-label="${done ? 'Mark not done' : 'Mark done'}" title="${done ? 'Mark not done' : 'Done'}"></button>
    <div class="body"><div class="t">${t.priority === 2 && !done ? '<span class="prio">! </span>' : ''}${esc(t.text)}</div>
      <div class="m">${due ? `<span class="due ${due.cls}">${esc(due.label)}</span>` : ''}${src}</div></div>
    <span class="pill-btns">${sayBtn(t.text + (due ? '. Due ' + due.label : ''))}${t.status === 'open' ? '<button data-act="dismissed" title="Not a task: teaches the classifier">Not a task</button>' : t.status === 'dismissed' ? '<button data-act="open">Restore</button>' : ''}</span>
  </div>`;
}
$('#taskFilter').onclick = e => { const b = e.target.closest('button'); if (!b) return; Today.filter = b.dataset.v; $$('#taskFilter button').forEach(x => x.classList.toggle('on', x === b)); Today.renderTasks(); };
$('#taskForm').onsubmit = async e => {
  e.preventDefault(); const text = $('#taskInput').value.trim(); if (!text) return;
  try {
    const r = await api.post('/api/tasks', { text }); $('#taskInput').value = '';
    const due = fmtDue(r.task.due);
    toast(r.created ? `Added${due ? ' · due ' + due.label : ''} · the classifier learned from it` : 'That task already exists');
    Today.load();
  } catch (x) { err(x); }
};
$('#taskList').onclick = async e => {
  const b = e.target.closest('[data-act]'); if (!b) return;
  const row = b.closest('.task');
  try {
    await api.post('/api/tasks/' + row.dataset.id, { status: b.dataset.act });
    if (b.dataset.act === 'done') { row.classList.add('done'); b.classList.add('on'); toast('Done · +5 XP', '', 1500); }
    if (b.dataset.act === 'dismissed') toast('Dismissed · the classifier learned this isn’t an action item', '', 2200);
    setTimeout(() => Today.load(), 350);
  } catch (x) { err(x); }
};
$('#topicCloud').onclick = e => { const b = e.target.closest('.pill'); if (b) Today.openTopic(b.dataset.p); };
$('#readDay').onclick = () => Voice.speak(Today.data ? Today.data.spoken : '');
$('#nextAction').onclick = () => { const a = $('#nextAction').dataset.action; if (a === 'tasks') $('#taskList').scrollIntoView({ behavior: 'smooth' }); else showTab(a); };

/* open any linked item (session or note) from anywhere */
document.addEventListener('click', e => {
  const b = e.target.closest('[data-open]'); if (!b) return;
  e.preventDefault(); const [type, id] = b.dataset.open.split(':');
  if (type === 'session') { showTab('library'); setTimeout(() => openSession(+id), 80); }
  else if (type === 'note') { showTab('ask'); }
});

/* ------------------------------------------------------------------ Study */
const StudyUI = {
  card: null, shown: false,
  async load() {
    let r; try { r = await api.get('/api/study/next'); } catch (e) { return err(e); }
    this.card = r.card; this.shown = false; const st = r.stats;
    $('#cardsLeft').textContent = st.due; $('#studyDot').classList.toggle('hidden', !st.due);
    $('#studyStats').innerHTML = stat('Due now', st.due) + stat('New', st.new) + stat('Mature', st.mature, '21+ day interval') +
      stat('Retention', st.retention == null ? '–' : Math.round(st.retention * 100) + '<small>%</small>', `${st.reviews_30d} reviews in 30 days`);
    $('#cardBack').classList.add('hidden'); $('#gradeRow').classList.add('hidden'); $('#showRow').classList.remove('hidden');
    if (!this.card) {
      $('#flash').classList.add('empty-card');
      $('#cardKind').textContent = st.total ? 'All caught up' : 'No cards yet';
      $('#cardFront').textContent = st.total ? 'Nothing is due. Come back later, or learn new cards tomorrow.' : 'Add notes on the Ask tab or run a session — definitions and key terms become cards automatically.';
      $('#cardSource').textContent = ''; $('#showRow').classList.add('hidden'); return;
    }
    const c = this.card;
    $('#cardKind').textContent = c.kind === 'cloze' ? 'Fill in the blank' : 'Question';
    $('#cardFront').textContent = c.front; $('#cardBack').textContent = c.back;
    $('#cardSource').innerHTML = c.source ? `from <button class="linkish" data-open="${c.source.item_type}:${c.source.item_id}">${esc(c.source.title)}</button>${c.due == null ? ' · new' : ''}` : '';
    $$('#gradeRow .grade').forEach(b => b.querySelector('small').textContent = c.preview[b.dataset.g]);
  },
  show() {
    if (!this.card || this.shown) return; this.shown = true;
    $('#cardBack').classList.remove('hidden'); $('#showRow').classList.add('hidden'); $('#gradeRow').classList.remove('hidden');
  },
  async grade(g) {
    if (!this.card || !this.shown) return;
    try { await api.post('/api/study/review', { id: this.card.id, grade: g }); } catch (e) { return err(e); }
    this.load();
  },
};
$('#showAnswer').onclick = () => StudyUI.show();
$('#flash').onclick = e => { if (!e.target.closest('button')) StudyUI.show(); };
$('#gradeRow').onclick = e => { const b = e.target.closest('.grade'); if (b) StudyUI.grade(+b.dataset.g); };
$('#cardSpeak').onclick = e => { e.stopPropagation(); if (StudyUI.card) Voice.speak(StudyUI.shown ? StudyUI.card.front.replace('_____', StudyUI.card.back) : StudyUI.card.front.replace('_____', 'blank')); };
$('#genCards').onclick = async () => { try { await api.post('/api/study/generate'); toast('Making flashcards from all sessions and notes…'); } catch (e) { err(e); } };
addEventListener('keydown', e => {
  if (S.tab !== 'study' || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || $('#settings').open) return;
  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); StudyUI.show(); }
  const g = { '1': 1, '2': 3, '3': 4, '4': 5 }[e.key];
  if (g && StudyUI.shown) { e.preventDefault(); e.stopImmediatePropagation(); StudyUI.grade(g); }
}, true);

/* ------------------------------------------------------------------ Library links */
async function openSession(id) {
  const b = $(`#archList button[data-id="${id}"]`);
  if (b) b.click(); else { await loadLibrary(); const b2 = $(`#archList button[data-id="${id}"]`); if (b2) b2.click(); }
}
async function renderLinks(sid) {
  let d; try { d = await api.get('/api/sessions/' + sid); } catch (e) { return; }
  const L = d.links, box = document.createElement('div'); box.className = 'detail';
  box.innerHTML = `${L.topics.length ? `<h4>Topics</h4><div class="chiprow">${L.topics.map(t => `<button class="pill" data-topic="${esc(t.phrase)}">${esc(t.phrase)}</button>`).join('')}</div>` : ''}
    ${L.tasks.length ? `<h4>Tasks from this session</h4>${L.tasks.map(t => `<div class="sent"><span class="txt">${esc(t.text)}</span><span class="sc">${t.status}${t.due_text ? ' · ' + esc(t.due_text) : ''}</span></div>`).join('')}` : ''}
    ${L.related.length ? `<h4>Related</h4><div class="mini">${L.related.map(r => `<button data-open="${r.item_type}:${r.item_id}">${esc(r.title)}<small>shares: ${esc(r.shared)}</small></button>`).join('')}</div>` : ''}
    ${L.cards.length ? `<h4>Flashcards</h4><p class="muted">${L.cards.length} card${L.cards.length > 1 ? 's' : ''} made from this session.</p>` : ''}
    <p style="margin-top:16px"><a class="btn small ghost" href="/api/sessions/${sid}/export.md" download>Export as Markdown</a></p>`;
  $('#archDetail').appendChild(box);
}
document.addEventListener('click', e => { const b = e.target.closest('[data-topic]'); if (b) { showTab('today'); setTimeout(() => Today.openTopic(b.dataset.topic), 150); } });

/* ------------------------------------------------------------------ System panel (Insights) */
async function renderSystem() {
  const g = $('#labGrid'); if (!g) return;
  let box = $('#sysPanel');
  if (!box) {
    g.insertAdjacentHTML('beforeend', `<div class="card full" id="sysPanel"><div class="card-head"><h2>System</h2><span class="muted">event reactors · jobs · HTTP</span></div>
      <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr))"><div><h4 class="muted">Reactors (how features connect)</h4><div id="sysReactors" class="tbl-wrap"></div></div>
      <div><h4 class="muted">Background jobs</h4><div id="sysJobs"></div></div><div><h4 class="muted">API</h4><div id="sysHttp" class="tbl-wrap"></div></div></div></div>`);
    g.insertAdjacentHTML('beforeend', `<div class="card"><div id="ch-study"></div></div><div class="card"><div id="ch-tasks"></div></div>`);
    box = $('#sysPanel');
  }
  let m, st, tk;
  try { [m, st, tk] = await Promise.all([api.get('/api/metrics'), api.get('/api/study/stats'), api.get('/api/tasks?status=all')]); } catch (e) { return; }
  $('#sysReactors').innerHTML = `<table class="t"><tr><th>Reactor</th><th>Calls</th><th>Errors</th><th>ms</th></tr>${m.events.reactors.map(r => `<tr title="${esc(r.last_error || '')}"><td>${esc(r.name)}</td><td>${r.calls}</td><td class="${r.errors ? 'err-cell' : ''}">${r.errors}</td><td>${fmt(r.ms, 0)}</td></tr>`).join('')}</table>`;
  $('#sysJobs').innerHTML = m.jobs.length ? m.jobs.slice(0, 8).map(j => `<div class="jobrow"><span class="st ${j.state}">${j.state}</span><span style="flex:2">${esc(j.label)}</span>${j.total ? `<span class="bar"><i style="width:${Math.round(j.done / j.total * 100)}%"></i></span>` : ''}${j.state === 'running' ? `<button class="btn small" data-cancel="${j.id}">Cancel</button>` : `<span class="muted">${j.seconds != null ? j.seconds + ' s' : ''}</span>`}</div>`).join('') : '<div class="empty small">No jobs yet.</div>';
  $('#sysHttp').innerHTML = `<p class="muted small">${m.http.requests} requests · ${m.http.errors} server errors · up ${Math.round(m.http.uptime_s / 60)} min</p><table class="t"><tr><th>Route</th><th>n</th><th>p95 ms</th></tr>${m.http.routes.slice(0, 8).map(r => `<tr><td>${esc(r.route.replace(/\\/g, ''))}</td><td>${r.count}</td><td>${r.p95_ms ?? '–'}</td></tr>`).join('')}</table>`;
  Charts.bars($('#ch-study'), { title: 'Study', subtitle: `<b>Flashcard reviews</b>per day, last 30 days · retention ${st.retention == null ? '–' : Math.round(st.retention * 100) + '%'}`, items: st.per_day.map(d => ({ label: d.day, value: d.reviews, color: 's7', tip: `<b>${d.day}</b><br>${d.reviews} reviews · ${d.recalled} recalled` })), yFmt: v => fmt(v, 0), labelName: 'day', valueName: 'reviews', empty: 'Review some cards on the Study tab.' });
  const by = s => tk.items.filter(t => t.status === s).length;
  Charts.bars($('#ch-tasks'), { title: 'Tasks', subtitle: '<b>Tasks by outcome</b>dismissed tasks were fed back to the classifier', items: [{ label: 'Open', value: by('open'), color: 's1' }, { label: 'Done', value: by('done'), color: 's3' }, { label: 'Dismissed', value: by('dismissed'), color: 's2' }], yFmt: v => fmt(v, 0), labelName: 'status', valueName: 'tasks', empty: 'No tasks yet.' });
}
document.addEventListener('click', async e => { const b = e.target.closest('[data-cancel]'); if (b) { try { await api.post(`/api/jobs/${b.dataset.cancel}/cancel`); } catch (x) { err(x); } } });

/* ------------------------------------------------------------------ live events */
let refreshTimer = null;
const Connected = {
  event(m) {
    if (['tasks', 'cards', 'topics', 'xp'].includes(m.type)) {
      if (m.type === 'tasks' && m.created) toast(`${m.created} new task${m.created > 1 ? 's' : ''} from session ${m.session_id}`, '', 2500);
      if (m.type === 'cards' && m.created) toast(`${m.created} new flashcard${m.created > 1 ? 's' : ''}`, '', 2200);
      if (m.type === 'xp') setLevel(m);
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => { if (S.tab === 'today') Today.load(); if (S.tab === 'study' && !StudyUI.shown) StudyUI.load(); if (S.tab === 'insights') renderSystem(); badges(); }, 400);
    }
    if (m.type === 'job') {
      if (m.kind === 'train') {
        const bar = $('#trainBar'), running = m.state === 'running' || m.state === 'queued';
        bar.classList.toggle('hidden', !running); $('#trainCancel').dataset.cancel = m.id;
        $('#trainFill').style.width = (m.total ? m.done / m.total * 100 : 0) + '%';
        $('#trainTxt').textContent = `${m.label} · ${m.done} of ${m.total}${m.msg ? ' · ' + m.msg : ''}`;
        if (!running) { setTrainBusy(false); if (m.state === 'cancelled') toast('Training cancelled'); refreshState(); }
      }
      if (m.kind === 'cards' && m.state === 'done') toast(`Flashcards ready · ${m.msg}`);
      if (S.tab === 'insights') { clearTimeout(refreshTimer); refreshTimer = setTimeout(renderSystem, 300); }
    }
  },
};
async function badges() {
  try { const d = await api.get('/api/today'); $('#todayDot').classList.toggle('hidden', !(d.buckets.overdue.length || d.buckets.today.length)); $('#studyDot').classList.toggle('hidden', !d.cards.due); } catch { }
}
badges();
