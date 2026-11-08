// Job feed audit (14 Oct 2026): what Hark's daily audit recorded for the six feeds, on Home.
// Data: Studio/.local/hark-kpi-audit.json, saved from the sheet's "Audit KPIs" tab by hark_kpi.py and
// served as overview.hark_kpi. All period maths happens here so the Today/Week/Month/All toggle is instant.
// 20 Oct 2026: the Firecrawl hunt writes the same Audit KPIs layout (overview.firecrawl_kpi, from
// .local/firecrawl-kpi-audit.json). A Hark / Firecrawl / Both toggle switches panels without a re-render;
// the choice is kept per browser (localStorage, optional).
import { escape as e } from './ui.js';

const DAY = 86400000;
const iso = ms => new Date(ms).toISOString().slice(0, 10);
const utc = key => Date.parse(`${key}T00:00:00Z`);
const parisToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Paris' }).format(new Date());
const SHORT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const dayName = key => SHORT.format(utc(key));
const fmt = n => (n == null || Number.isNaN(n) ? '—' : new Intl.NumberFormat('en-GB').format(n));
const mean = list => { const v = list.filter(Number.isFinite); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
const sum = list => list.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
const ratio = (a, b) => (b ? (a / b) * 100 : null);
const pct = (n, digits = 0) => (n == null ? '—' : `${n.toFixed(digits).replace(/\.0$/, '')}%`);
const pct1 = n => (n == null ? '—' : `${n.toFixed(1)}%`);  // headline figures keep the sheet's one decimal (76.0%)
const mins = n => {
  if (n == null) return '—';
  if (n < 1) return `${Math.round(n * 60)} s`;
  const m = Math.round(n);
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}` : `${m} min`;
};

// [unit, back] -> inclusive date range, Paris calendar, weeks start on Monday (like the rest of Home).
export function rangeOf(unit, back, today = parisToday()) {
  if (unit === 'all') return { from: '0000-00-00', to: '9999-99-99' };
  const t = utc(today);
  if (unit === 'day') { const d = iso(t - back * DAY); return { from: d, to: d }; }
  if (unit === 'week') {
    const monday = t - ((new Date(t).getUTCDay() + 6) % 7) * DAY - back * 7 * DAY;
    return { from: iso(monday), to: iso(monday + 6 * DAY) };
  }
  const base = new Date(t);
  const first = Date.UTC(base.getUTCFullYear(), base.getUTCMonth() - back, 1);
  const last = Date.UTC(base.getUTCFullYear(), base.getUTCMonth() - back + 1, 0);
  return { from: iso(first), to: iso(last) };
}

export function selectDays(kpi, unit, back, today) {
  const days = kpi?.days || [];
  const range = rangeOf(unit, back, today);
  const inside = days.filter(d => d.date >= range.from && d.date <= range.to);
  if (inside.length || unit !== 'day' || back !== 0 || !days.length) return { days: inside, range, fallback: false };
  return { days: [days[days.length - 1]], range, fallback: true };
}

// Fastest / slowest successful run and best / worst accuracy a feed has ever recorded, plus its average.
// Computed from every saved audit day, so the average moves each time a new day is imported.
export function history(allDays, id) {
  const runs = allDays.map(d => d.feeds?.[id]).filter(f => f && f.ran);
  const spread = list => {
    const v = list.filter(Number.isFinite);
    return v.length ? { min: Math.min(...v), max: Math.max(...v), avg: mean(v), n: v.length } : null;
  };
  return { time: spread(runs.filter(f => f.status === 'complete').map(f => f.duration_min)),
    accuracy: spread(runs.filter(f => f.accuracy_pct != null).map(f => f.accuracy_pct)) };
}

export function summarize(days, feeds, allDays = days) {
  const feedRows = feeds.map(({ id, label }) => {
    const runs = days.map(d => ({ date: d.date, f: d.feeds?.[id] })).filter(r => r.f);
    const ran = runs.filter(r => r.f.ran);
    const planned = runs.filter(r => r.f.status !== 'not_scheduled');
    const accuracy = ran.filter(r => r.f.accuracy_pct != null);
    return {
      id, label, runs: ran.length, planned: planned.length, days: runs, history: history(allDays, id),
      perMinute: (() => {
        const timed = ran.filter(r => Number.isFinite(r.f.duration_min) && r.f.duration_min > 0);
        const total = sum(timed.map(r => r.f.duration_min));
        return total ? sum(timed.map(r => r.f.added)) / total : null;
      })(),
      complete: ran.filter(r => r.f.status === 'complete').length,
      partial: ran.filter(r => r.f.status === 'partial').length,
      missed: planned.filter(r => !r.f.ran).length,
      onTime: ran.filter(r => r.f.on_time === true).length,
      duration: mean(ran.map(r => r.f.duration_min)),
      approx: ran.some(r => r.f.approx_time),
      accuracy: mean(accuracy.map(r => r.f.accuracy_pct)), audited: accuracy.length,
      scraped: sum(ran.map(r => r.f.scraped)), added: sum(ran.map(r => r.f.added)),
      duplicates: sum(ran.map(r => r.f.duplicates)),
      jds: sum(ran.map(r => r.f.jds)), jdsOf: sum(ran.map(r => r.f.jds_of)),
      errorsFound: sum(accuracy.map(r => r.f.errors_found)), errorsFixed: sum(accuracy.map(r => r.f.errors_fixed)),
      holdOpen: sum(ran.map(r => r.f.onhold_open)),
      emailMissed: ran.filter(r => r.f.email_sent === false).length,
    };
  });
  const intake = days.map(d => d.email_intake || {}).filter(i => i.reported);
  return {
    days: days.length,
    automation: mean(days.map(d => d.automation_score_pct)),
    rowAccuracy: mean(days.map(d => d.row_accuracy_pct)),
    // 15 Oct: counted from the feed rows (a Partial run did run), not Hark's stricter "completed" tally.
    schedulesDone: sum(days.map(d => Object.values(d.feeds || {}).filter(x => x.ran).length)),
    schedulesPlanned: sum(days.map(d => Object.values(d.feeds || {}).filter(x => x.status !== 'not_scheduled').length)),
    jdPct: ratio(sum(days.map(d => d.jds_captured)), sum(days.map(d => d.new_rows))),
    newRows: sum(days.map(d => d.new_rows)),
    emailSent: sum(days.map(d => d.report_emails_sent)), emailTotal: sum(days.map(d => d.report_emails_total)),
    errorsFound: sum(days.map(d => d.errors_found)), errorsFixed: sum(days.map(d => d.errors_fixed)),
    holdFixed: sum(days.map(d => d.onhold_fixed)), holdOpen: sum(days.map(d => d.onhold_open)),
    intakeDays: intake.length,
    rejections: sum(intake.map(i => i.rejections)), jobsAdded: sum(intake.map(i => i.jobs_added)),
    intakeDuplicates: sum(intake.map(i => i.duplicates)),
    feeds: feedRows,
  };
}

// 5 Nov 2026 (Daksh): the second feed is removed; only Hark is shown (old panels stay in code for a later feed).
export const FEED_SOURCES = [['hark', 'Hark']];
const SOURCE_KEY = 'studio.feedAuditSource';
const FC_COLOURS = ['#f97316', '#fb923c', '#ea580c', '#fdba74', '#c2410c', '#fed7aa'];
const WHO = { hark: 'Hark', firecrawl: 'Firecrawl', both: 'the feeds' };

export function savedSource() {
  try {
    const value = localStorage.getItem(SOURCE_KEY);
    return FEED_SOURCES.some(([id]) => id === value) ? value : 'hark';
  } catch { return 'hark'; }
}

const SUM_FIELDS = ['new_rows', 'jds_captured', 'rows_with_audit_errors', 'errors_found', 'errors_fixed',
  'report_emails_sent', 'report_emails_total', 'onhold_fixed', 'onhold_open', 'schedules_completed', 'schedules_planned'];
const MEAN_FIELDS = ['automation_score_pct', 'row_accuracy_pct', 'jd_capture_pct', 'email_delivery_pct'];

// Both feeds in one audit: feed ids get a 'hark:' / 'fc:' prefix, day totals add up, day percentages average.
export function mergeKpi(hark, firecrawl) {
  const parts = [['hark', hark, ''], ['fc', firecrawl, 'Firecrawl · ']].filter(([, k]) => k?.days?.length);
  if (!parts.length) return null;
  const feeds = parts.flatMap(([tag, k, prefix]) => k.feeds.map(f => ({ id: `${tag}:${f.id}`, label: `${prefix}${f.label}` })));
  const byDate = new Map();
  for (const [tag, k] of parts) {
    for (const day of k.days) {
      const list = byDate.get(day.date) || [];
      list.push([tag, day]);
      byDate.set(day.date, list);
    }
  }
  const days = [...byDate.keys()].sort().map(date => {
    const list = byDate.get(date);
    const out = { date, audited_at: list.map(([, d]) => d.audited_at).filter(Boolean).join(' · '), feeds: {},
      email_intake: (list.find(([tag]) => tag === 'hark')?.[1].email_intake) || { reported: false } };
    for (const key of SUM_FIELDS) {
      const v = list.map(([, d]) => d[key]).filter(Number.isFinite);
      out[key] = v.length ? v.reduce((a, b) => a + b, 0) : null;
    }
    for (const key of MEAN_FIELDS) out[key] = mean(list.map(([, d]) => d[key]));
    for (const [tag, d] of list) for (const [id, f] of Object.entries(d.feeds || {})) out.feeds[`${tag}:${id}`] = f;
    return out;
  });
  const stamps = parts.map(([, k]) => k.updated_at).filter(Boolean).sort();
  return { kind: 'merged', source: 'Hark + Firecrawl', feeds, days, updated_at: stamps[stamps.length - 1] || null };
}

const band = value => (value == null ? 'none' : value >= 90 ? 'good' : value >= 75 ? 'mid' : 'low');

// 21 Oct (Daksh): Gemini accuracy follows the chosen period and feed, and shows "—" until the daily review has
// recorded a score for it (it used to keep showing the last score). Runs are summed: kept / checked.
export function geminiForPeriod(gemini, range, source = 'hark') {
  const feedOf = r => (/^\s*firecrawl:/i.test(r.note || '') ? 'firecrawl' : 'hark');
  const runs = (gemini?.runs || []).filter(r => r.run_date >= range.from && r.run_date <= range.to
    && Number.isFinite(r.checked) && r.checked > 0 && (source === 'both' || feedOf(r) === source));
  if (!runs.length) return null;
  const kept = sum(runs.map(r => r.kept)), checked = sum(runs.map(r => r.checked));
  return { kept, checked, runs: runs.length, score_pct: ratio(kept, checked), last: runs[runs.length - 1].run_date };
}

function scoreCards(s, gemini, source = 'hark', range = rangeOf('day', 0)) {
  const g = geminiForPeriod(gemini, range, source);
  const gDay = g ? (range.from === range.to ? dayName(range.from) : `${g.runs} reviewed run${g.runs === 1 ? '' : 's'}`) : '';
  const gNote = g ? `${g.kept} of ${g.checked} kept by the daily review · ${gDay}` : 'not reviewed yet for this period';
  const card = (cls, label, value, note, logo = '') => `<div class="hf-score ${cls}"><span class="hf-score-label">${e(label)}</span>${logo ? `<span class="hf-score-logo">${logo}</span>` : ''}<strong>${value}</strong><small>${e(note)}</small></div>`;
  return `<div class="hf-scores">
    ${source === 'hark'
    ? card('hf-score-hark', 'Hark accuracy', s.automation == null ? '—' : pct1(s.automation), s.automation == null ? 'No Hark rating in this period' : 'final automation score, rated by Hark', '<img class="hf-logo hf-logo-on-dark" src="/static/assets/hark-mark-light.png" alt="" width="40" height="40"><img class="hf-logo hf-logo-on-light" src="/static/assets/hark-mark-dark.png" alt="" width="40" height="40">')
    : card(`hf-score-${source}`, source === 'firecrawl' ? 'Firecrawl accuracy' : 'Feeds accuracy', s.automation == null ? '—' : pct1(s.automation),
      s.automation == null ? `No ${source === 'firecrawl' ? 'Firecrawl' : 'feed'} rating in this period` : source === 'firecrawl' ? 'automation score of the Firecrawl hunt' : 'average of Hark and Firecrawl')}
    ${card('hf-score-gemini', 'Gemini accuracy', g ? pct1(g.score_pct) : '—', gNote, '<img class="hf-logo" src="/static/assets/gemini-icon.svg" alt="" width="40" height="40">')}</div>`;
}
function figures(s, source = 'hark') {
  const item = (label, value, note) => `<div class="hf-fig"><dt>${e(label)}</dt><dd>${value}</dd><small>${e(note)}</small></div>`;
  return `<dl class="hf-figs">
    ${item('Automation score', pct1(s.automation), source === 'hark' ? 'Hark’s own rating of the day' : 'automation rating of the day')}
    ${item('JDs added', fmt(s.newRows ?? sum(s.feeds.map(f => f.added))), source === 'hark' ? 'new jobs added to the Hark sheet' : source === 'firecrawl' ? 'new jobs added to the Firecrawl sheet' : 'new jobs added to both sheets')}
    ${item('Rows right first time', pct1(s.rowAccuracy), source === 'hark' ? 'before Hark’s fixes' : 'before fixes')}
    ${item('Full job descriptions', pct(s.jdPct), `${fmt(s.newRows)} new rows`)}</dl>`;
}

// A fastest-to-slowest track: the dot is where this period landed, the tick is the running average.
function range(span, value, unit, label) {
  if (!span) return '<div class="hf-range hf-range-empty"><span class="hf-track"></span></div>';
  const at = v => (span.max === span.min ? 50 : Math.min(100, Math.max(0, ((v - span.min) / (span.max - span.min)) * 100)));
  const fmtv = v => (unit === '%' ? pct(v) : mins(v));
  const tip = `${label}: ${value == null ? 'not recorded' : fmtv(value)} this period · range ${fmtv(span.min)} to ${fmtv(span.max)} over ${span.n} run${span.n === 1 ? '' : 's'}`;
  return `<div class="hf-range" data-tip="${e(tip)}"><small>${e(fmtv(span.min))}</small>
    <span class="hf-line">${value == null ? '' : `<b class="hf-dot" style="left:${at(value)}%"></b>`}</span>
    <small>${e(fmtv(span.max))}</small></div>`;
}

const perMin = n => (n == null ? '' : `${n < 10 ? n.toFixed(1).replace(/\.0$/, '') : Math.round(n)} jobs/min`);

function timeCard(s, colourOf) {
  const rows = s.feeds.filter(f => f.runs || f.planned);
  const total = sum(rows.map(f => f.duration));
  const list = rows.map(f => {
    if (!f.runs) return `<li class="hf-row hf-off"><span class="hf-name"><i style="background:${colourOf(f.id)}"></i>${e(f.label)}</span>
      <strong>—</strong><small class="hf-sub-note">${f.planned ? 'did not run' : 'not scheduled'}</small></li>`;
    return `<li class="hf-row"><span class="hf-name"><i style="background:${colourOf(f.id)}"></i>${e(f.label)}</span>
      <strong>${f.approx ? '~' : ''}${mins(f.duration)}</strong><small class="hf-sub-note">${e(perMin(f.perMinute))}${f.perMinute != null ? ` · ${fmt(f.added)} added` : ''}</small>
      ${range(f.history.time, f.duration, 'min', f.label)}</li>`;
  }).join('');
  return `<section class="hn-card"><div class="hf-card-head"><h2>Time spent on each website</h2>${total ? `<strong class="hf-big">${mins(total)}</strong>` : ''}</div>
    ${total ? '' : '<p class="hf-lead">No timed runs in this period.</p>'}
    <ul class="hf-rows">${list}</ul></section>`;
}

function accuracyCard(s, colourOf, who = 'Hark') {
  const rows = s.feeds.filter(f => f.runs);
  const list = rows.map(f => {
    const note = f.audited ? `${f.errorsFound} found · ${f.errorsFixed} fixed` : 'not audited yet';
    return `<li class="hf-row"><span class="hf-name"><i style="background:${colourOf(f.id)}"></i>${e(f.label)}</span>
      <strong>${f.accuracy == null ? '—' : pct(f.accuracy)}</strong><small class="hf-sub-note">${e(note)}</small>
      ${range(f.history.accuracy, f.accuracy, '%', f.label)}</li>`;
  }).join('');
  return `<section class="hn-card"><div class="hf-card-head"><h2>Accuracy rated by ${e(who)}</h2>${s.rowAccuracy == null ? '' : `<strong class="hf-big">${pct1(s.rowAccuracy)}</strong>`}</div>
    ${s.rowAccuracy == null ? '<p class="hf-lead">No accuracy rating in this period.</p>' : ''}
    <ul class="hf-rows">${list || '<li class="hn-empty">No audited runs.</li>'}</ul></section>`;
}

function healthCard(s, colourOf) {
  const rows = s.feeds.filter(f => f.runs || f.planned).map(f => {
    const state = !f.runs ? '<span class="hf-flag hf-low">No run</span>'
      : f.partial ? '<span class="hf-flag hf-mid">Partial</span>' : '<span class="hf-flag hf-good">Complete</span>';
    return `<tr><th scope="row"><i style="background:${colourOf(f.id)}"></i>${e(f.label)}</th><td>${state}</td>
      <td>${fmt(f.scraped)}</td><td>${fmt(f.added)}</td><td>${fmt(f.duplicates)}</td>
      <td>${f.jdsOf ? `${fmt(f.jds)}/${fmt(f.jdsOf)}` : '—'}</td><td>${f.holdOpen ? fmt(f.holdOpen) : '0'}</td></tr>`;
  }).join('');
  return `<section class="hn-card hn-card-wide"><h2>Feed health</h2>
    <div class="hf-table-wrap"><table class="hf-table"><thead><tr><th scope="col">Feed</th><th scope="col">Run</th>
      <th scope="col">Scraped</th><th scope="col">Added</th><th scope="col">Duplicates</th><th scope="col">Full ads</th><th scope="col">ON HOLD open</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
  </section>`;
}

function sourceToggle(active) {
  if (FEED_SOURCES.length < 2) return '';  // one feed: no toggle
  return `<div class="hn-mini hf-src" role="group" aria-label="Job feed source">${FEED_SOURCES.map(([id, label]) =>
    `<button type="button" data-hf-src="${id}" aria-pressed="${id === active}">${e(label)}</button>`).join('')}</div>`;
}

function auditBody({ kpi, gemini, unit, back, periodName, colourOf, today, source }) {
  const who = WHO[source];
  if (!kpi?.days?.length) {
    const hint = source === 'firecrawl' ? 'The Firecrawl hunt has not saved an audit yet. It fills in after its first run.'
      : 'No audit saved yet. Ask Claude to refresh the audit from the sheet’s Audit KPIs tab.';
    return `<p class="hn-empty">${e(hint)}</p>`;
  }
  const { days, fallback } = selectDays(kpi, unit, back, today);
  if (!days.length) return `<p class="hn-empty">${e(source === 'both' ? 'No feed' : who)} recorded no audit for ${e(periodName.toLowerCase())}.</p>`;
  const s = summarize(days, kpi.feeds, kpi.days);
  // 15 Oct: both cards list the websites by jobs per minute, fastest first; feeds without a rate go last.
  const ranked = { ...s, feeds: [...s.feeds].sort((a, b) =>
    (b.perMinute ?? -1) - (a.perMinute ?? -1) || (b.added - a.added) || a.label.localeCompare(b.label)) };
  const latest = days[days.length - 1];
  const age = kpi.updated_at ? `Saved from the sheet ${new Date(kpi.updated_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}` : '';
  const sub = fallback ? `No audit yet today. Showing the latest one, ${dayName(latest.date)}.`
    : `${s.days} audit day${s.days === 1 ? '' : 's'} · latest ${dayName(latest.date)}`;
  return `<p class="hf-sub">${e(sub)}${age ? ` · ${e(age)}` : ''}</p>
    <div class="hn-card hf-summary">${scoreCards(s, gemini, source, rangeOf(unit, back, today))}${figures(s, source)}</div>
    <div class="hn-grid">${timeCard(ranked, colourOf)}${accuracyCard(ranked, colourOf, who)}${healthCard(s, colourOf)}</div>`;
}

export function renderFeedAudit({ kpi, firecrawl = null, gemini = null, unit, back, toggleHtml, periodName, colourOf, today, source = savedSource() }) {
  if (!FEED_SOURCES.some(([id]) => id === source)) source = FEED_SOURCES[0][0];
  const fcIndex = id => Math.max(0, (firecrawl?.feeds || []).findIndex(f => f.id === id));
  const colour = id => (id.startsWith('fc:') ? FC_COLOURS[fcIndex(id.slice(3)) % FC_COLOURS.length]
    : colourOf(id.replace(/^hark:/, '')));
  const fcColour = id => FC_COLOURS[fcIndex(id) % FC_COLOURS.length];
  const head = `<div class="hn-wide-head"><div class="hn-wide-title"><h2>Job feed audit</h2>${sourceToggle(source)}${toggleHtml}</div></div>`;
  const common = { gemini, unit, back, periodName, today };
  const panels = [
    ['hark', auditBody({ ...common, kpi, colourOf, source: 'hark' })],
    ['firecrawl', auditBody({ ...common, kpi: firecrawl, colourOf: fcColour, source: 'firecrawl' })],
    ['both', auditBody({ ...common, kpi: mergeKpi(kpi, firecrawl), colourOf: colour, source: 'both' })],
  ].filter(([id]) => FEED_SOURCES.some(([s]) => s === id)).map(([id, body]) => `<div class="hf-src-panel" data-hf-panel="${id}"${id === source ? '' : ' hidden'}>${body}</div>`).join('');
  return `<section class="hf-section" aria-label="Job feed audit">${head}${panels}</section>`;
}

// Switch the visible feed panel in place (no re-render), and remember the choice for the next render.
if (typeof document !== 'undefined') {
  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-hf-src]');
    if (!button) return;
    const root = button.closest('.hf-section');
    const id = button.dataset.hfSrc;
    try { localStorage.setItem(SOURCE_KEY, id); } catch { /* private window: choice lasts until the next render */ }
    root?.querySelectorAll('[data-hf-src]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    root?.querySelectorAll('[data-hf-panel]').forEach(panel => { panel.hidden = panel.dataset.hfPanel !== id; });
  });
}
