/* Tiny dependency-free SVG chart kit: line + bar (vertical/horizontal, diverging), hover tooltips,
   legends for >=2 series, and a table view for every chart. Colours come from CSS variables. */
const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const tip = () => document.getElementById('tip');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = (v, d = 2) => { if (v == null || Number.isNaN(v)) return '–'; if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString(); const s = (+v).toFixed(d); return s.includes('.') ? s.replace(/\.?0+$/, '') : s; };
  const col = c => `var(--${c})`;
  let gradId = 0;

  function nice(min, max, n = 4) {
    if (min === max) { max = min + 1; }
    const raw = (max - min) / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p, step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step, t = [];
    for (let v = lo; v <= hi + step / 2; v += step) t.push(+v.toFixed(10));
    return { lo, hi, ticks: t };
  }

  function shell(el, o) {
    if (!el._built) {
      el.classList.add('cv');
      el.innerHTML = '<div class="ttl"></div><div class="plot"></div><div class="legend"></div><div class="tbl tbl-wrap hidden"></div>' +
        '<button class="tblbtn" type="button">Show data</button>';
      el.querySelector('.tblbtn').onclick = () => {
        const t = el.querySelector('.tbl'), on = t.classList.toggle('hidden');
        el.querySelector('.plot').classList.toggle('hidden', !on);
        el.querySelector('.tblbtn').textContent = on ? 'Show data' : 'Show chart';
      };
      el._built = true;
    }
    el.querySelector('.ttl').innerHTML = o.subtitle || '';
    return el.querySelector('.plot');
  }

  function legend(el, series) {
    const l = el.querySelector('.legend');
    l.innerHTML = series.length >= 2 ? series.map(s => `<span><i class="sw" style="background:${col(s.color)}"></i>${esc(s.name)}</span>`).join('') : '';
  }

  function empty(el, o) {
    const p = shell(el, o);
    p.innerHTML = `<div class="empty small">${esc(o.empty || 'No data yet.')}</div>`;
    el.querySelector('.legend').innerHTML = ''; el.querySelector('.tbl').innerHTML = '';
  }

  function showTip(ev, html) {
    const t = tip(); t.innerHTML = html; t.style.display = 'block';
    const w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.min(ev.clientX + 14, innerWidth - w - 8) + 'px';
    t.style.top = Math.max(ev.clientY - h - 10, 6) + 'px';
  }
  const hideTip = () => { tip().style.display = 'none'; };

  function line(el, o) {
    const S = (o.series || []).filter(s => s.points && s.points.length);
    if (!S.length) return empty(el, o);
    const W = 560, H = o.h || 220, m = { l: 40, r: 40, t: 10, b: 34 };
    const xs = S.flatMap(s => s.points.map(p => p[0])), ys = S.flatMap(s => s.points.map(p => p[1]));
    let x0 = Math.min(...xs), x1 = Math.max(...xs); if (x0 === x1) x1 = x0 + 1;
    const yn = nice(o.yMin ?? Math.min(...ys), o.yMax ?? Math.max(...ys));
    const X = x => m.l + (x - x0) / (x1 - x0) * (W - m.l - m.r), Y = y => H - m.b - (y - yn.lo) / (yn.hi - yn.lo) * (H - m.t - m.b);
    let g = yn.ticks.map(t => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}" style="stroke:var(--grid)"/><text x="${m.l - 6}" y="${Y(t) + 3}" text-anchor="end">${fmt(t, 2)}</text>`).join('');
    const xt = nice(x0, x1, 5).ticks.filter(t => t >= x0 && t <= x1);
    g += xt.map(t => `<text x="${X(t)}" y="${H - 20}" text-anchor="middle">${fmt(t, 0)}</text>`).join('');
    if (o.xLabel) g += `<text x="${(m.l + W - m.r) / 2}" y="${H - 4}" text-anchor="middle" style="fill:var(--tx3)">${esc(o.xLabel)}</text>`;
    if (o.refLine != null) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(o.refLine.y)}" y2="${Y(o.refLine.y)}" style="stroke:var(--tx3);stroke-dasharray:4 4"/><text x="${W - m.r}" y="${Y(o.refLine.y) - 4}" text-anchor="end">${esc(o.refLine.label)}</text>`;
    const ends = [], uid = 'g' + (++gradId);
    S.forEach((s, si) => {
      const d = s.points.map((p, j) => (j ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join('');
      if (S.length <= 2 && s.points.length > 1) {        // soft area under up to two series
        const base = Y(Math.max(yn.lo, Math.min(0, yn.hi))), f = s.points[0], l = s.points[s.points.length - 1];
        g += `<defs><linearGradient id="${uid}-${si}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:${col(s.color)};stop-opacity:.22"/><stop offset="1" style="stop-color:${col(s.color)};stop-opacity:0"/></linearGradient></defs>`;
        g += `<path d="${d}L${X(l[0]).toFixed(1)} ${base}L${X(f[0]).toFixed(1)} ${base}Z" fill="url(#${uid}-${si})" stroke="none"/>`;
      }
      g += `<path d="${d}" fill="none" style="stroke:${col(s.color)}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
      const lp = s.points[s.points.length - 1];
      g += `<circle cx="${X(lp[0])}" cy="${Y(lp[1])}" r="7" style="fill:${col(s.color)};opacity:.18"/><circle cx="${X(lp[0])}" cy="${Y(lp[1])}" r="4" style="fill:${col(s.color)};stroke:var(--card);stroke-width:2"/>`;
      ends.push({ x: X(lp[0]) + 8, y: Y(lp[1]) + 4, v: lp[1] });
    });
    if (S.length <= 4) {                      // direct end labels, nudged apart so they never collide
      ends.sort((a, b) => a.y - b.y);
      for (let k = 1; k < ends.length; k++) ends[k].y = Math.max(ends[k].y, ends[k - 1].y + 12);
      const over = ends.length ? ends[ends.length - 1].y - (H - m.b) : 0;
      if (over > 0) ends.forEach(e => e.y -= over);
      ends.forEach(e => { g += `<text x="${e.x}" y="${e.y}" style="fill:var(--tx2)">${fmt(e.v, 2)}</text>`; });
    }
    const p = shell(el, o);
    p.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.title || 'chart')}">${g}<line class="xh" y1="${m.t}" y2="${H - m.b}" style="stroke:var(--tx3);display:none"/><g class="dots"></g><rect class="hit" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent"/></svg>`;
    legend(el, S);
    const svg = p.querySelector('svg'), hit = svg.querySelector('.hit'), xh = svg.querySelector('.xh'), dots = svg.querySelector('.dots');
    hit.onmousemove = ev => {
      const r = svg.getBoundingClientRect(), vx = (ev.clientX - r.left) / r.width * W, xv = x0 + (vx - m.l) / (W - m.l - m.r) * (x1 - x0);
      let rows = '', dd = '', nx = null;
      S.forEach(s => {
        const p2 = s.points.reduce((a, b) => Math.abs(b[0] - xv) < Math.abs(a[0] - xv) ? b : a);
        nx = nx ?? p2[0];
        rows += `<div><span class="sw" style="background:${col(s.color)}"></span>${esc(s.name)}: <b>${fmt(p2[1], 3)}</b></div>`;
        dd += `<circle cx="${X(p2[0])}" cy="${Y(p2[1])}" r="4.5" style="fill:${col(s.color)};stroke:var(--card);stroke-width:2"/>`;
      });
      xh.setAttribute('x1', X(nx)); xh.setAttribute('x2', X(nx)); xh.style.display = ''; dots.innerHTML = dd;
      showTip(ev, `<div style="color:var(--tx2);margin-bottom:3px">${esc(o.xName || 'step')} ${fmt(nx, 0)}</div>${rows}`);
    };
    hit.onmouseleave = () => { hideTip(); xh.style.display = 'none'; dots.innerHTML = ''; };
    const allx = [...new Set(xs)].sort((a, b) => a - b);
    const tb = el.querySelector('.tbl');
    tb.innerHTML = `<table class="t"><tr><th>${esc(o.xName || 'step')}</th>${S.map(s => `<th>${esc(s.name)}</th>`).join('')}</tr>` +
      allx.slice(-60).map(x => `<tr><td>${fmt(x, 0)}</td>${S.map(s => { const q = s.points.find(p => p[0] === x); return `<td>${q ? fmt(q[1], 3) : '–'}</td>`; }).join('')}</tr>`).join('') + '</table>';
  }

  /* items: [{label, value, color, tip}] — vertical or horizontal; negatives diverge from a zero baseline. */
  function bars(el, o) {
    const I = o.items || [];
    if (!I.length || I.every(i => i.value == null)) return empty(el, o);
    const horiz = !!o.horizontal, n = I.length;
    const W = 560, H = o.h || (horiz ? Math.max(120, 22 * n + 34) : 200);
    const m = horiz ? { l: o.labelW || 120, r: 40, t: 8, b: 22 } : { l: 40, r: 10, t: 12, b: 26 };
    const vals = I.map(i => i.value ?? 0), lo = Math.min(0, ...vals), hi = Math.max(o.yMax ?? 0, ...vals, 1e-9);
    const yn = nice(lo, hi), gap = 2;
    let g = '';
    if (!horiz) {
      const Y = v => H - m.b - (v - yn.lo) / (yn.hi - yn.lo) * (H - m.t - m.b), bw = (W - m.l - m.r) / n;
      g += yn.ticks.map(t => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}" style="stroke:var(--grid)"/><text x="${m.l - 6}" y="${Y(t) + 3}" text-anchor="end">${(o.yFmt || (v => fmt(v, 2)))(t)}</text>`).join('');
      if (o.baseline) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(o.baseline.value)}" y2="${Y(o.baseline.value)}" style="stroke:var(--tx3);stroke-dasharray:4 4"/><text x="${W - m.r}" y="${Y(o.baseline.value) - 4}" text-anchor="end">${esc(o.baseline.label)}</text>`;
      const step = Math.ceil(n / 16);
      I.forEach((it, k) => {
        const v = it.value ?? 0, x = m.l + k * bw + gap / 2, w = Math.max(bw - gap, 1), y0 = Y(0), y1 = Y(v), r = Math.min(4, w / 2);
        const top = Math.min(y0, y1), h = Math.max(Math.abs(y1 - y0), 1);
        g += `<path class="bar" data-k="${k}" d="M${x} ${top + h}V${top + r}Q${x} ${top} ${x + r} ${top}H${x + w - r}Q${x + w} ${top} ${x + w} ${top + r}V${top + h}Z" style="fill:${col(it.color || 's1')}"/>`;
        if (k % step === 0) g += `<text x="${x + w / 2}" y="${H - 8}" text-anchor="middle">${esc(it.label)}</text>`;
        g += `<rect class="hb" data-k="${k}" x="${m.l + k * bw}" y="${m.t}" width="${bw}" height="${H - m.t - m.b}" fill="transparent"/>`;
      });
    } else {
      const X = v => m.l + (v - yn.lo) / (yn.hi - yn.lo) * (W - m.l - m.r), bh = (H - m.t - m.b) / n;
      g += yn.ticks.map(t => `<line y1="${m.t}" y2="${H - m.b}" x1="${X(t)}" x2="${X(t)}" style="stroke:var(--grid)"/><text x="${X(t)}" y="${H - 8}" text-anchor="middle">${fmt(t, 2)}</text>`).join('');
      I.forEach((it, k) => {
        const v = it.value ?? 0, y = m.t + k * bh + gap / 2, h = Math.max(bh - gap, 1), x0 = X(0), x1 = X(v), r = Math.min(4, h / 2);
        const left = Math.min(x0, x1), w = Math.max(Math.abs(x1 - x0), 1);
        g += `<rect class="bar" data-k="${k}" x="${left}" y="${y}" width="${w}" height="${h}" rx="${r}" style="fill:${col(it.color || 's1')}"/>`;
        g += `<text x="${m.l - 6}" y="${y + h / 2 + 3}" text-anchor="end" style="fill:var(--tx2)">${esc(it.label)}</text>`;
        g += `<rect class="hb" data-k="${k}" x="0" y="${m.t + k * bh}" width="${W}" height="${bh}" fill="transparent"/>`;
      });
    }
    const p = shell(el, o);
    p.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.title || 'chart')}">${g}</svg>`;
    const legItems = o.legend || [];
    el.querySelector('.legend').innerHTML = legItems.map(s => `<span><i class="sw" style="background:${col(s.color)}"></i>${esc(s.name)}</span>`).join('');
    p.querySelectorAll('.hb').forEach(r => {
      const it = I[+r.dataset.k], bar = p.querySelector(`.bar[data-k="${r.dataset.k}"]`);
      r.onmousemove = ev => { bar.style.filter = 'brightness(1.3)'; showTip(ev, it.tip || `<b>${esc(it.label)}</b><br>${fmt(it.value, 3)}`); };
      r.onmouseleave = () => { bar.style.filter = ''; hideTip(); };
    });
    el.querySelector('.tbl').innerHTML = `<table class="t"><tr><th>${esc(o.labelName || 'item')}</th><th>${esc(o.valueName || 'value')}</th></tr>` +
      I.map(i => `<tr><td>${esc(i.label)}</td><td>${fmt(i.value, 3)}</td></tr>`).join('') + '</table>';
  }

  return { line, bars, fmt, esc };
})();
