import { icon, loading } from './ui.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
const hasNumber = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const pct = (value, digits = 1) => hasNumber(value) ? `${Number(value).toFixed(digits)}%` : '—';
const count = value => hasNumber(value) ? Number(value).toLocaleString('en-GB') : '—';
const tone = score => !hasNumber(score) ? 'none' : Number(score) >= 99 ? 'good' : Number(score) >= 95 ? 'warn' : 'bad';
const when = iso => {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? esc(iso) : date.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
};

const LAYER_ROWS = [
  ['names', 'Names'],
  ['duplicates', 'Duplicates'],
  ['cv_format', 'CV pages & language'],
  ['letter', 'Cover letter'],
  ['claims', 'Claims'],
  ['sync', 'Studio sync'],
];
const CARDS = [
  { index: 0, title: 'Script check', logo: `<span class="audit-logo audit-logo-script">${icon('terminal')}</span>` },
  { index: 1, title: 'Gemini 3.8 Flash High', logo: '<img class="audit-logo" src="/static/assets/ai-logos/gemini-flash.svg" alt="" width="36" height="36">' },
  { index: 2, title: 'Argon High', logo: '<img class="audit-logo" src="/static/assets/ai-logos/argon.svg" alt="" width="36" height="36">' },
];

function auditCard(card, layer) {
  if (!layer) {
    return `<article class="audit-card audit-none"><header>${card.logo}<div><span class="eyebrow">Layer ${card.index}</span><h3>${esc(card.title)}</h3></div></header><p class="audit-not-run">Not run yet</p></article>`;
  }
  const score = layer.flow_score;
  const before = hasNumber(layer.flow_score_before)
    ? `<span class="audit-before">${esc(pct(layer.flow_score_before))} → ${esc(pct(score))}</span>` : '';
  const unresolved = Array.isArray(layer.unresolved) ? layer.unresolved.length : 0;
  return `<article class="audit-card audit-${tone(score)}"><header>${card.logo}<div><span class="eyebrow">Layer ${card.index}</span><h3>${esc(card.title)}</h3></div></header>`
    + `<div class="audit-score"><strong>${esc(pct(score))}</strong>${before}</div>`
    + `<dl class="audit-stats"><div><dt>Clean jobs</dt><dd>${esc(count(layer.clean_jobs))} / ${esc(count(layer.jobs_checked))}</dd></div>`
    + `<div><dt>Fixes</dt><dd>${esc(count(layer.fixes_ok))} fixed</dd></div>`
    + `<div><dt>Unresolved</dt><dd class="${unresolved ? 'is-bad' : ''}">${esc(count(unresolved))}</dd></div></dl></article>`;
}

function funnel(run) {
  const byIndex = index => run.layers.find(layer => layer.index === index);
  const total = run.layers.find(layer => hasNumber(layer.jobs_checked))?.jobs_checked;
  const stages = [
    { label: 'Jobs checked', value: total },
    { label: 'Clean after script', value: byIndex(0)?.clean_jobs },
    { label: 'Clean after Gemini audit', value: byIndex(1)?.clean_jobs },
    { label: 'Clean after Argon audit', value: byIndex(2)?.clean_jobs },
  ];
  const bars = stages.map(stage => {
    const ratio = hasNumber(stage.value) && hasNumber(total) && Number(total) > 0 ? Number(stage.value) / Number(total) : null;
    const share = ratio === null ? null : `${(ratio * 100).toFixed(1)}%`;
    const width = ratio === null ? 30 : Math.max(30, Math.min(100, ratio * 100));
    return `<div class="audit-funnel-row"><div class="audit-funnel-bar${share ? '' : ' is-empty'}" style="width:${width.toFixed(1)}%"><span>${esc(stage.label)}</span><strong>${esc(count(stage.value))}${share ? ` · ${esc(share)}` : ''}</strong></div></div>`;
  }).join('');
  return `<section class="panel audit-panel audit-funnel"><div class="section-heading"><div><h2>Clean-job funnel</h2><p class="section-subtitle">Jobs left clean after each check</p></div></div><div class="audit-funnel-bars">${bars}</div></section>`;
}

function layerTable(run) {
  const byIndex = index => run.layers.find(layer => layer.index === index);
  const rows = LAYER_ROWS.map(([key, label]) => {
    const cells = [0, 1, 2].map(index => {
      const entry = byIndex(index)?.layers?.[key];
      return entry && hasNumber(entry.score) ? `<td>${esc(pct(entry.score))}<small>${esc(count(entry.passed))}/${esc(count(entry.total))}</small></td>` : '<td class="muted">—</td>';
    }).join('');
    return `<tr><th scope="row">${esc(label)}</th>${cells}</tr>`;
  }).join('');
  return `<section class="panel audit-panel"><div class="section-heading"><div><h2>Checks by layer</h2><p class="section-subtitle">Score per check group, with passed / total</p></div></div><div class="audit-table-wrap"><table class="audit-table"><thead><tr><th scope="col">Check</th><th scope="col">Script</th><th scope="col">Gemini</th><th scope="col">Argon</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function unresolvedPanel(run) {
  const final = run.layers[run.layers.length - 1];
  const items = run.layers.flatMap(layer => layer.unresolved || []);
  const emailSent = run.layers.some(layer => layer.email_sent);
  const rows = (final?.unresolved || []).map(item => `<li class="audit-unresolved-row"><strong>${esc(item.company || item.job || 'Unknown company')}</strong><span> — ${esc(item.problem || 'No detail recorded')}</span></li>`).join('');
  const body = rows
    ? `<ul class="audit-unresolved">${rows}</ul>`
    : `<p class="audit-clear">${items.length ? 'Nothing unresolved in the final layer.' : 'No unresolved jobs in the latest run.'}</p>`;
  return `<section class="panel audit-panel"><div class="section-heading"><div><h2>Unresolved</h2><p class="section-subtitle">Jobs the audits could not clear</p></div>${emailSent ? '<span class="badge audit-chip">Email sent</span>' : ''}</div>${body}</section>`;
}

function trendChart(trend) {
  const points = trend.filter(item => hasNumber(item.final_score));
  if (!points.length) return '<p class="audit-clear">Trend appears once an audit has a score.</p>';
  const width = 640, height = 220, left = 44, right = 56, top = 14, bottom = 26;
  const yFor = value => top + (100 - Math.min(100, Math.max(80, Number(value)))) / 20 * (height - top - bottom);
  const xFor = index => points.length === 1 ? (left + width - right) / 2 : left + index * ((width - right - left) / (points.length - 1));
  const grid = [80, 85, 90, 95, 100].map(value => `<line class="audit-grid" x1="${left}" x2="${width - right}" y1="${yFor(value)}" y2="${yFor(value)}"/><text class="audit-axis" x="${left - 8}" y="${yFor(value) + 4}" text-anchor="end">${value}%</text>`).join('');
  const line = points.length > 1 ? `<polyline class="audit-line" fill="none" points="${points.map((item, i) => `${xFor(i)},${yFor(item.final_score)}`).join(' ')}"/>` : '';
  const dots = points.map((item, i) => `<circle class="audit-dot" cx="${xFor(i)}" cy="${yFor(item.final_score)}" r="3.5"><title>${esc(item.id)}: ${esc(pct(item.final_score))}</title></circle>`).join('');
  const last = points[points.length - 1];
  const label = `<text class="audit-last" x="${xFor(points.length - 1) + 8}" y="${yFor(last.final_score) + 4}">${esc(pct(last.final_score))}</text>`;
  return `<svg class="audit-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="Final flow score per run, last ${points.length} runs">${grid}${line}${dots}${label}</svg><p class="audit-note">${points.length} run${points.length === 1 ? '' : 's'} · oldest to newest</p>`;
}

export function renderAudits(data, h = '') {
  if (data === null || data === undefined) {
    return `${h}<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><span>Reading audits…</span></div>`;
  }
  const runs = Array.isArray(data.runs) ? data.runs : [];
  if (!runs.length) {
    return `${h}<section class="panel audit-empty"><span class="empty-icon">${icon('shield')}</span><p>No audits yet. The first one runs with tomorrow's 06:00 pipeline.</p></section>`;
  }
  const run = data.latest || runs[0];
  const byIndex = index => run.layers.find(layer => layer.index === index);
  const cards = CARDS.map(card => auditCard(card, byIndex(card.index))).join('');
  return `${h}<p class="audits-subtitle">Latest run <strong>${esc(run.id)}</strong> · ${esc(when(run.at))}</p>`
    + `<div class="audit-cards">${cards}</div>`
    + `<div class="audits-grid">${funnel(run)}${layerTable(run)}</div>`
    + `<div class="audits-grid">${unresolvedPanel(run)}<section class="panel audit-panel"><div class="section-heading"><div><h2>Flow score trend</h2><p class="section-subtitle">Final score per run, last ${esc(Math.min(runs.length, 30))}</p></div></div>${trendChart(Array.isArray(data.trend) ? data.trend.slice(-30) : [])}</section></div>`;
}
