// Home beta (13 Oct 2026): a calmer, easier-to-read Home. Same numbers as Home (it reads the
// model built by buildColourHomeModel), laid out as: what to do now (top 3) → results → the last
// 7 days → search funnel → shortlist rate per website → French/English JD split.
// Each card below the results has its own small period toggle, default this week.
// Charts are plain HTML/SVG with hover tooltips; motion respects prefers-reduced-motion.
import { escape as e } from './ui.js';
import { renderFeedAudit } from './home-feeds.js';
import { collectedBySource, collectedLanguagesBySource, normalSource, periodLabel, sourceLabel } from './home-beta.js';

const UNITS = [['day', 'Today'], ['week', 'Week'], ['month', 'Month'], ['all', 'All']];
const NAMED = { day: ['today', 'yesterday'], week: ['this_week', 'last_week'], month: ['this_month'] };
const MAX_BACK = { day: 60, week: 26, month: 12, all: 0 };
export const DEFAULT_SECTIONS = { results: 'today', funnel: 'today', sources: 'today', language: 'today', feeds: 'today' };
const HIDDEN_SOURCES = new Set(['hark', 'unknown', 'mail']);
// One colour per website, used by the waterfall bars, the donut slices and the language rows.
const SOURCE_COLOURS = { hellowork: '#f59e0b', linkedin: '#3b82f6', france_travail: '#a855f7', apec: '#ef4444',
  indeed: '#14b8a6', welcome_to_the_jungle: '#eab308', jobs_that_make_sense: '#ec4899' };
const sourceColour = id => SOURCE_COLOURS[id] || '#94a3b8';
// A period id is a unit plus how many steps back (0 = now). Named ids keep the model's own labels.
function parts(id) {
  if (id === 'all') return ['all', 0];
  for (const [unit, names] of Object.entries(NAMED)) { const i = names.indexOf(id); if (i >= 0) return [unit, i]; }
  const m = /^([dwm]):(\d+)$/.exec(id);
  return m ? [{ d: 'day', w: 'week', m: 'month' }[m[1]], Number(m[2])] : ['all', 0];
}
function idOf(unit, back) {
  if (unit === 'all') return 'all';
  const names = NAMED[unit] || [];
  return back < names.length ? names[back] : `${unit[0]}:${back}`;
}
export function periodIds(selected) { return [...new Set([...Object.values({ ...DEFAULT_SECTIONS, ...selected }), 'this_week'])]; }
const fmt = value => (value == null || Number.isNaN(value) ? '—' : new Intl.NumberFormat('en-GB').format(value));
const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : null);

function tile(label, value, note, tone = '', tip = '') {
  const known = Number.isFinite(value);
  return `<article class="hn-tile ${tone}" ${tip ? `data-tip="${e(tip)}" tabindex="0"` : ''}><span class="hn-tile-label">${e(label)}</span>
    <strong class="hn-tile-value" ${known ? `data-count="${value}"` : ''}>${known ? '0' : '—'}</strong>
    <span class="hn-tile-note">${e(note || '')}</span></article>`;
}

// All time: the Hark sheet tracks every rejection email (including applications made before Studio),
// so its total is the truth when it is higher than what Studio has recorded by status.
function rejectedTile(p, periodId, sheet) {
  const total = Number.isFinite(sheet?.count) ? sheet.count : null;
  if (periodId === 'all' && total != null && total >= (p.rejected || 0)) {
    return tile('Rejected', total, 'All time · from your email sheet', 'hn-muted');
  }
  // Hover (15 Oct): who rejected this period, as "Company · Role" when Studio has them.
  const items = Array.isArray(p.rejectedItems) ? p.rejectedItems : [];
  const tip = items.map(i => [i.company, i.title].filter(Boolean).join(' · ')).filter(Boolean).join('\n');
  return tile('Rejected', p.rejected, p.label, 'hn-muted', tip);
}

function toApply(roles) {
  if (!roles.length) return `<p class="hn-empty">Nothing is waiting. New CVs appear here after the next pipeline run.</p>`;
  // Only the three strongest profile matches: fit first, then company English.
  const pct = role => typeof role.fit_score?.score === 'number' ? role.fit_score.score : (Number.isInteger(role.fit) ? role.fit * 20 : null);
  const score = role => [pct(role) ?? 0, Number(role.ratings?.company_english?.score) || 0];
  const top = [...roles].sort((a, b) => score(b)[0] - score(a)[0] || score(b)[1] - score(a)[1]).slice(0, 3);
  const rows = top.map(role => {
    const fit = pct(role);
    const english = role.ratings?.company_english?.score;
    return `<li><a href="#apply" class="hn-apply-row">
      <span class="hn-apply-main"><strong>${e(role.company)}</strong><span>${e(role.title || '')}</span></span>
      <span class="hn-apply-meta">${role.location_label || role.location ? `<span>${e(role.location_label || role.location)}</span>` : ''}
        ${fit != null ? `<span class="hn-chip" title="Profile fit">Fit ${fit.toFixed(1)}</span>` : ''}</span>
      <span class="hn-apply-go" aria-hidden="true">→</span></a></li>`;
  }).join('');
  const more = roles.length > top.length ? `<a class="hn-link hn-more" href="#apply">See all ${fmt(roles.length)} →</a>` : '';
  return `<div><ol class="hn-apply-list">${rows}</ol>${more}</div>`;
}

const SERIES = [['collected', 'JDs collected'], ['shortlisted', 'Shortlisted'], ['applied', 'Applied']];

function periodWeekLabel(offset) { return offset === 1 ? 'Last week' : `${offset} weeks ago`; }
function weekHead(rows, offset) {
  const day = value => { const d = new Date(`${value}T12:00:00Z`); return `${d.getUTCDate()} ${d.toLocaleString('en-GB', { month: 'short', timeZone: 'UTC' })}`; };
  const range = rows.length ? `${day(rows[0].date)} – ${day(rows[rows.length - 1].date)}` : '';
  return `<div class="hn-card-head"><h2>${offset ? 'Week' : 'Last 7 days'}${range ? ` <small class="hn-range">${e(range)}</small>` : ''}</h2>
    <div class="hn-mini" role="group" aria-label="Week to show">
      <button type="button" class="hn-step" data-hn-week="${offset + 1}" aria-label="Previous 7 days" ${offset >= 52 ? 'disabled' : ''}>‹</button>
      <button type="button" data-hn-week="0" aria-pressed="true">${offset ? periodWeekLabel(offset) : 'Week'}</button>
      <button type="button" class="hn-step" data-hn-week="${Math.max(0, offset - 1)}" aria-label="Next 7 days" ${offset === 0 ? 'disabled' : ''}>›</button></div></div>`;
}

function weekChart(rows, enabled) {
  // Grouped bars on one shared scale; the legend pills switch each series on and off.
  const active = SERIES.filter(([key]) => enabled[key] !== false);
  // 15 Oct: bars are split by website, in the website colours, whenever "JDs collected" is not shown together
  // with another series: collected alone, shortlisted alone, applied alone, or shortlisted + applied.
  const stacked = active.length > 0 && (active.length === 1 || !active.some(([key]) => key === 'collected'));
  const mapKey = { collected: 'bySource', shortlisted: 'shortlistedBySource', applied: 'appliedBySource' };
  const partsFor = (row, key) => {
    const value = row[key];
    const list = Object.entries(row[mapKey[key]] || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
    const known = list.reduce((n, [, c]) => n + c, 0);
    if (Number.isFinite(value) && value > known) list.push(['unknown', value - known]);
    return list;
  };
  // 23 Oct: a "nice" top value so the y-axis ticks and grid lines land on round numbers.
  const peak = Math.max(1, ...rows.flatMap(row => active.map(([key]) => row[key] || 0)));
  const step = (() => { const raw = peak / 4; const mag = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 2.5, 5, 10].map(m => m * mag).find(v => v >= raw) || raw; })();
  const max = Math.max(1, Math.ceil(peak / step) * step);
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const totals = Object.fromEntries(SERIES.map(([key]) => [key, rows.reduce((sum, row) => sum + (row[key] || 0), 0)]));
  const legend = `<ul class="hn-legend">${SERIES.map(([key, label], i) =>
      `<li><button type="button" class="hn-series" data-hn-series="${key}" aria-pressed="${enabled[key] !== false}"><i class="hn-s${i + 1}" aria-hidden="true"></i>${e(label)} <strong>${fmt(totals[key])}</strong></button></li>`).join('')}</ul>`;
  const present = stacked ? [...new Set(rows.flatMap(r => active.flatMap(([key]) => partsFor(r, key).map(([id]) => id))))]
    .sort((a, b) => {
      const total = id => rows.reduce((n, r) => n + active.reduce((m, [key]) => m + (partsFor(r, key).find(([x]) => x === id)?.[1] || 0), 0), 0);
      return total(b) - total(a);
    }) : [];
  const striped = stacked && active.length > 1;
  const sourceKey = stacked ? `<ul class="hn-legend hn-source-key" aria-label="Websites">${present.map(id =>
      `<li><span class="hn-source-chip"><i style="background:${sourceColour(id)}" aria-hidden="true"></i>${e(id === 'unknown' ? 'Other / unknown' : sourceLabel(id))}</span></li>`).join('')}${
      striped ? '<li><span class="hn-source-chip hn-source-note">solid = shortlisted · striped = applied</span></li>' : ''}</ul>` : '';
  const tipFor = row => {
    const lines = active.map(([key, label]) => {
      const head = `${label} ${row[key] ?? '—'}`;
      if (!stacked) return head;
      const parts = partsFor(row, key).filter(([, n]) => n);
      return parts.length ? `${head}: ${parts.map(([id, n]) => `${id === 'unknown' ? 'Other' : sourceLabel(id)} ${fmt(n)}`).join(' · ')}` : head;
    });
    return stacked ? `${row.date}\n${lines.join('\n')}` : `${row.date} · ${lines.join(' · ')}`;
  };
  return `${legend}${sourceKey}
    <div class="hn-axis-chart"><div class="hn-yaxis" aria-hidden="true">${ticks.map(t => `<span style="--y:${(t / max) * 100}%">${fmt(t)}</span>`).join('')}</div>
    <div class="hn-plot"><div class="hn-gridlines" aria-hidden="true">${ticks.map(t => `<i style="--y:${(t / max) * 100}%"></i>`).join('')}</div>
    <div class="hn-week" role="img" aria-label="Last 7 days: ${rows.map(r => `${r.label} ${active.map(([k, l]) => `${l} ${r[k] ?? 'unknown'}`).join(', ')}`).join('; ')}">
    ${rows.map(row => `<div class="hn-week-col" data-tip="${e(tipFor(row))}">
        <span class="hn-week-track" style="--n:${Math.max(1, active.length)}">${SERIES.map(([key], i) => {
          if (enabled[key] === false) return '';
          const value = row[key];
          const height = value ? Math.max(3, (value / max) * 100) : 0;
          if (stacked && value) {
            const parts = partsFor(row, key);
            if (parts.length) {
              return `<span class="hn-week-bar"><b>${fmt(value)}</b><span class="hn-week-stack${key === 'applied' && striped ? ' hn-stack-applied' : ''}" style="--h:${height}%">${parts.map(([id, n]) =>
                `<i style="flex:${n};background:${sourceColour(id)}" title="${e(id === 'unknown' ? 'Other' : sourceLabel(id))} ${fmt(n)}"></i>`).join('')}</span></span>`;
            }
          }
          return `<span class="hn-week-bar"><b>${value ? fmt(value) : ''}</b><i class="hn-s${i + 1}" style="--h:${height}%"></i></span>`;
        }).join('')}</span>
        <span class="hn-week-label">${e(row.label)}</span></div>`).join('')}</div></div></div>`;
}

function toggle(section, value) {
  const [unit, back] = parts(value);
  const step = (target, glyph, label) => `<button type="button" class="hn-step" data-hn-sec="${e(section)}" data-hn-val="${target || ''}"
    aria-label="${label}" ${target ? '' : 'disabled'}>${glyph}</button>`;
  const older = unit !== 'all' && back < MAX_BACK[unit] ? idOf(unit, back + 1) : null;
  const newer = unit !== 'all' && back > 0 ? idOf(unit, back - 1) : null;
  return `<div class="hn-mini" role="group" aria-label="Period for ${e(section)}">${step(older, '‹', 'Earlier period')}${UNITS.map(([id, label]) => {
    const on = id === unit;
    const shown = on && back > 0 ? periodLabel(value) : label;
    return `<button type="button" data-hn-sec="${e(section)}" data-hn-val="${idOf(id, 0)}" aria-pressed="${on}">${e(shown)}</button>`;
  }).join('')}${step(newer, '›', 'Later period')}</div>`;
}

function cardHead(title, section, value) {
  return `<div class="hn-card-head"><h2>${e(title)}</h2>${toggle(section, value)}</div>`;
}

function funnelPath(normStart, normEnd, segH, W, layerScale) {
  // Same curved vertical segment as the shadcn FunnelChart: half-widths ease from the top to the bottom edge.
  const mx = W / 2, w0 = normStart * W * 0.44 * layerScale, w1 = normEnd * W * 0.44 * layerScale, cy = segH * 0.55;
  return `M ${mx - w0} 0 C ${mx - w0} ${cy}, ${mx - w1} ${segH - cy}, ${mx - w1} ${segH} `
    + `L ${mx + w1} ${segH} C ${mx + w1} ${segH - cy}, ${mx + w0} ${cy}, ${mx + w0} 0 Z`;
}

function funnel(period) {
  // A funnel only goes down: anything applied was shortlisted first, and anything shortlisted was collected.
  const applied = Number.isFinite(period.applied) ? period.applied : null;
  const chosen = Number.isFinite(period.chosenValue) ? Math.max(period.chosenValue, applied || 0) : applied;
  const read = Number.isFinite(period.readValue) ? Math.max(period.readValue, chosen || 0) : chosen;
  const stages = [['Jobs collected', read, ['var(--hn-s1)', 'var(--hn-s3)']], ['Shortlisted', chosen, ['var(--hn-s3)', 'var(--hn-s2)']],
    ['Applied', applied, ['var(--hn-s2)', 'var(--hn-s1)']]];
  const max = Math.max(1, read || 0);
  const W = 400, gap = 6, segH = 96, H = segH * stages.length + gap * (stages.length - 1), layers = 3;
  const norms = stages.map(([, v]) => (Number.isFinite(v) ? Math.max(0.04, v / max) : 0.04));
  const segments = stages.map(([label, value, colours], i) => {
    const start = norms[i], end = norms[Math.min(i + 1, stages.length - 1)];
    const rings = Array.from({ length: layers }, (_, l) => {
      const scale = 1 - (l / layers) * 0.35;
      const opacity = 0.18 + (l / (layers - 1)) * 0.65;
      const hover = 1 + (l / (layers - 1)) * 0.12;
      const inner = l === layers - 1;
      return `<path class="hn-ring" d="${funnelPath(start, end, segH, W, scale)}" fill="${inner ? `url(#hn-fg${i})` : colours[0]}"
        opacity="${opacity.toFixed(2)}" style="--k:${hover.toFixed(3)}"/>`;
    }).join('');
    const y = i * (segH + gap);
    return `<g class="hn-vseg" style="--i:${i}" transform="translate(0 ${y})">
      <defs><linearGradient id="hn-fg${i}" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="${colours[0]}"/><stop offset="100%" stop-color="${colours[1]}"/></linearGradient></defs>
      <g class="hn-vseg-in">${rings}</g></g>`;
  }).join('');
  const labels = stages.map(([label, value], i) => {
    const rate = Number.isFinite(value) ? Math.round((value / max) * 100) : null;
    return `<div class="hn-vlabel" style="top:${((i * (segH + gap)) / H) * 100}%;height:${(segH / H) * 100}%" data-hn-stage="${i}"
      data-tip="${e(label)}: ${fmt(value)}${i && rate != null ? ` · ${rate}% of jobs collected` : ''}">
      <span class="hn-vl-value">${fmt(value)}</span><span class="hn-vl-pill">${rate != null ? `${rate}%` : '—'}</span><span class="hn-vl-name">${e(label)}</span></div>`;
  }).join('');
  return `<div class="hn-vf" style="aspect-ratio:${W} / ${H}">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
      aria-label="Funnel: ${stages.map(([l, v]) => `${l} ${fmt(v)}`).join(', ')}">${segments}</svg>
    ${labels}</div>`;
}

const PERIOD_NAMES = { today: 'Today', yesterday: 'Yesterday', this_week: 'This week', last_week: 'Last week',
  this_month: 'This month', all: 'All time' };
const nameOf = id => PERIOD_NAMES[id] || periodLabel(id);

function sourceBlock(model, analytics, periodId) {
  // Per website: what the chosen period added on top of the previous one (waterfall, one colour per site),
  // plus the French / English split of the same jobs. The small table compares the three latest periods.
  const total = map => [...map].filter(([id]) => !HIDDEN_SOURCES.has(id)).reduce((sum, [, n]) => sum + n, 0);
  const [unit, back] = parts(periodId);
  const previous = unit === 'all' ? null : idOf(unit, back + 1);
  const unitShort = { day: 'D', week: 'W', month: 'M' }[unit];
  let trio = '';
  if (unitShort) {
    const ids = unit === 'week' && back === 0 ? ['wt:2', 'wt:1', 'wt:0'] : [idOf(unit, back + 2), idOf(unit, back + 1), periodId];
    const labels = [`${unitShort}-2`, `${unitShort}-1`, nameOf(periodId)];
    trio = `<table class="hn-trio" aria-label="Screened jobs in the last three periods"><thead><tr>${labels.map(l => `<th>${e(l)}</th>`).join('')}</tr></thead>
      <tbody><tr>${ids.map(id => `<td>${fmt(total(collectedBySource(analytics, id)))}</td>`).join('')}</tr></tbody></table>`;
  }
  const now = collectedBySource(analytics, periodId);
  const before = previous ? collectedBySource(analytics, previous) : new Map();
  const shortlisted = new Map();
  for (const row of (model.periods[periodId]?.sources || [])) {
    const id = normalSource(row.id);
    shortlisted.set(id, (shortlisted.get(id) || 0) + (Number.isFinite(row.shortlisted) ? row.shortlisted : 0));
  }
  const added = total(now), start = total(before), end = start + added;  // always goes up
  const rows = [...now].filter(([id, n]) => n > 0 && !HIDDEN_SOURCES.has(id))
    .map(([id, n]) => ({ id, label: sourceLabel(id), n, short: shortlisted.get(id) || 0 })).sort((x, y) => y.n - x.n);
  if (!end) return { trio, body: '<p class="hn-empty">No screened jobs in this period.</p>' };
  // A large base would hide the additions, so the axis starts lower than zero.
  const floor = start > added * 3 ? Math.round((start - added * 2) / 10) * 10 : 0;
  const span = Math.max(1, end - floor);
  const pctOf = v => `${(Math.max(0, v - floor) / span) * 100}%`;
  let running = start;
  const column = (label, low, high, colour, value, note, tip) => `<div class="hn-wf-col" data-tip="${e(tip)}">
      <span class="hn-wf-value">${value}</span>
      <span class="hn-wf-plot"><i class="hn-wf-bar" style="--lo:${pctOf(low)};--hi:${pctOf(high)};background:${colour}"></i></span>
      <span class="hn-wf-label">${e(label)}</span><small class="hn-wf-note">${note}</small></div>`;
  const cols = previous ? [column(nameOf(previous), floor, start, 'var(--hn-wf-base)', fmt(start), 'screened', `Screened in ${nameOf(previous)}: ${fmt(start)}`)] : [];
  for (const row of rows) {
    const from = running; running += row.n;
    cols.push(column(row.label, from, running, sourceColour(row.id), `+${fmt(row.n)}`, `${pct(row.short, row.n)}% shortlisted`,
      `${row.label}: ${fmt(row.n)} screened, ${fmt(row.short)} shortlisted`));
  }
  cols.push(column('Total', floor, end, 'var(--hn-wf-base)', fmt(end), 'screened', `Total: ${fmt(end)}`));
  const langs = collectedLanguagesBySource(analytics, periodId);
  const sumAdded = rows.reduce((sum, row) => sum + row.n, 0);
  const radius = 15.9155;
  let offset = 0;
  const slices = rows.map(row => {
    const share = (row.n / (sumAdded || 1)) * 100;
    const slice = `<circle cx="21" cy="21" r="${radius}" fill="none" stroke="${sourceColour(row.id)}" stroke-width="6"
      stroke-dasharray="${share} ${100 - share}" stroke-dashoffset="${-offset}" data-tip="${e(row.label)}: ${fmt(row.n)} (${Math.round(share)}%)"/>`;
    offset += share;
    return slice;
  }).join('');
  const langRows = rows.map(row => {
    const l = langs.get(row.id) || { fr: 0, en: 0, unknown: 0 };
    const known = l.fr + l.en;
    const fr = known ? Math.round((l.fr / known) * 100) : null;
    return `<li><span class="hn-lang-name"><i style="background:${sourceColour(row.id)}"></i>${e(row.label)}<small>${fmt(row.n)}</small></span>
      ${known ? `<span class="hn-lang-bar" data-tip="${e(row.label)}: ${fmt(l.fr)} French, ${fmt(l.en)} English"><i style="width:${fr}%"></i></span>
        <span class="hn-lang-pct">${100 - fr}%</span>` : `<span class="hn-lang-bar"></span><span class="hn-lang-pct">—</span>`}</li>`;
  }).join('');
  const frAll = rows.reduce((sum, row) => sum + ((langs.get(row.id) || {}).fr || 0), 0);
  const enAll = rows.reduce((sum, row) => sum + ((langs.get(row.id) || {}).en || 0), 0);
  const knownAll = frAll + enAll;
  const enPct = knownAll ? 100 - Math.round((frAll / knownAll) * 100) : null;
  const body = `<div class="hn-waterfall" style="--cols:${cols.length}">${cols.join('')}</div>
    <div class="hn-split"><div class="hn-donut"><svg viewBox="0 0 42 42" role="img" aria-label="Screened jobs by website">
        <circle cx="21" cy="21" r="${radius}" fill="none" stroke="var(--line)" stroke-width="6"/>${slices}
        <text x="21" y="20.5" text-anchor="middle" class="hn-donut-total">${fmt(sumAdded)}</text>
        <text x="21" y="26" text-anchor="middle" class="hn-donut-sub">${e(nameOf(periodId).toLowerCase())}</text></svg></div>
      <ul class="hn-lang-list"><li class="hn-lang-key" aria-hidden="true"><span></span><span><i class="k-fr"></i>French<i class="k-en"></i>English</span><span>English</span></li>${langRows}${knownAll ? `<li class="hn-lang-all"><span class="hn-lang-name">All websites</span>
        <span class="hn-lang-bar"><i style="width:${100 - enPct}%"></i></span><span class="hn-lang-pct">${enPct}%</span></li>` : ''}</ul></div>`;
  return { trio, body };
}

function sourceCard(model, analytics, periodId) {
  const { trio, body } = sourceBlock(model, analytics, periodId);
  return `<div class="hn-wide-head"><div class="hn-wide-title"><h2>Where jobs come from</h2>${toggle('sources', periodId)}</div>${trio}</div>${body}`;
}

function languagePie(language) {
  const fr = Number(language?.fr) || 0, en = Number(language?.en) || 0;
  const total = fr + en;
  if (!total) return '<p class="hn-empty">No French or English job descriptions in this period.</p>';
  const radius = 15.9155;  // circumference = 100 stroke units
  const frShare = (fr / total) * 100;
  const label = (name, count) => `${name} ${fmt(count)} (${pct(count, total)}%)`;
  return `<div class="hn-pie-wrap"><svg class="hn-pie" viewBox="0 0 42 42" role="img" aria-label="${e(label('French', fr))}, ${e(label('English', en))}">
      <circle cx="21" cy="21" r="${radius}" fill="none" stroke="var(--hn-s1)" stroke-width="6" data-tip="${e(label('French', fr))}"
        stroke-dasharray="${frShare} ${100 - frShare}" stroke-dashoffset="0"/>
      <circle cx="21" cy="21" r="${radius}" fill="none" stroke="var(--hn-s2)" stroke-width="6" data-tip="${e(label('English', en))}"
        stroke-dasharray="${100 - frShare} ${frShare}" stroke-dashoffset="${-frShare}"/>
      <text x="21" y="22.5" text-anchor="middle" class="hn-pie-total">${fmt(total)}</text></svg>
    <ul class="hn-legend hn-pie-legend"><li><i class="hn-s1" aria-hidden="true"></i>${e(label('French', fr))}</li>
      <li><i class="hn-s2" aria-hidden="true"></i>${e(label('English', en))}</li></ul></div>
  `;
}

function feedSection(kpi, periodId, gemini = null, firecrawl = null) {
  const [unit, back] = parts(periodId);
  return renderFeedAudit({ kpi, firecrawl, gemini, unit, back, toggleHtml: toggle('feeds', periodId), periodName: nameOf(periodId),
    colourOf: id => (id === 'linkedin_sweep' ? '#6366f1' : sourceColour(id)) });
}

export function renderHomeNext({ model, readyRoles, sections = DEFAULT_SECTIONS, analytics, series = {}, weekOffset = 0, sheetRejections = null, hark = null, gemini = null, firecrawl = null }) {
  const sec = { ...DEFAULT_SECTIONS, ...sections };
  const pick = id => model.periods[id] || model.periods.all;
  const p = pick(sec.results);
  return `<div class="hn-root">
    <section class="hn-card hn-now"><div class="hn-now-count"><span class="hn-tile-label">To apply now</span>
      <strong data-count="${readyRoles.length}">0</strong><span class="hn-tile-note">CVs ready</span></div>
      ${toApply(readyRoles)}</section>
    <div class="hn-tiles-head"><h2>Results</h2>${toggle('results', sec.results)}</div>
    <section class="hn-tiles hn-tiles-3" aria-label="Results">
      ${tile('Applied', p.applied, p.label)}${rejectedTile(p, sec.results, sheetRejections)}
      ${tile('Shortlisted', p.chosenValue, 'Jobs picked for a CV')}</section>
    <div class="hn-grid">
      <section class="hn-card">${weekHead(model.applicationWeekSeries || [], weekOffset)}${weekChart(model.applicationWeekSeries || model.applicationWeek || [], series)}</section>
      <section class="hn-card">${cardHead('Search funnel', 'funnel', sec.funnel)}${funnel(pick(sec.funnel))}</section>
      <section class="hn-card hn-card-wide">${sourceCard(model, analytics, sec.sources)}</section>
    </div>
    ${feedSection(hark, sec.feeds, gemini, firecrawl)}
    <div class="hn-tip" role="tooltip" hidden></div></div>`;
}

let handlers = {};
let animated = false;  // Studio re-renders on each data refresh; animate only the first view

export function bindHomeNext(root, onSection, onSeries, onWeek) {
  const reduce = animated || matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) root.querySelector('.hn-root')?.classList.add('hn-static', 'hn-ready');
  animated = true;
  for (const node of root.querySelectorAll('[data-count]')) {
    const target = Number(node.dataset.count);
    if (reduce || !target) { node.textContent = fmt(target); continue; }
    const start = performance.now();
    const step = now => {
      const t = Math.min(1, (now - start) / 700);
      node.textContent = fmt(Math.round(target * (1 - (1 - t) ** 3)));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  requestAnimationFrame(() => root.querySelector('.hn-root')?.classList.add('hn-ready'));
  // Studio re-renders into the same <main>: attach the listeners once and keep the latest callbacks.
  handlers = { onSection, onSeries, onWeek };
  if (root.dataset.hnBound) return;
  root.dataset.hnBound = '1';
  root.addEventListener('pointerover', event => {
    const tip = root.querySelector('.hn-tip');
    const target = event.target.closest('[data-tip]');
    if (!target || !tip) return;
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
    const box = target.getBoundingClientRect();
    tip.style.left = `${Math.min(innerWidth - 220, Math.max(8, box.left + box.width / 2 - 110))}px`;
    tip.style.top = `${box.top - 40}px`;
  });
  root.addEventListener('pointerout', event => {
    const tip = root.querySelector('.hn-tip');
    if (event.target.closest('[data-tip]') && tip) tip.hidden = true;
  });
  root.addEventListener('click', event => {
    if (!root.querySelector('.hn-root')) return;
    const week = event.target.closest('[data-hn-week]');
    if (week && handlers.onWeek && !week.disabled) { handlers.onWeek(Number(week.dataset.hnWeek)); return; }
    const series = event.target.closest('[data-hn-series]');
    if (series && handlers.onSeries) { handlers.onSeries(series.dataset.hnSeries); return; }
    const section = event.target.closest('[data-hn-sec]');
    if (section && section.dataset.hnVal && handlers.onSection) handlers.onSection(section.dataset.hnSec, section.dataset.hnVal);
  });
}
