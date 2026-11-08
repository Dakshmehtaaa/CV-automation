// Find email drawer (5 Nov 2026, Daksh): find a person's professional email on public pages (Sonnet 5.5 or Haiku 5.5),
// with sources + confidence, then "Draft email" (always Sonnet 5.5) in Daksh's approved format + HTML signature.
// Nothing is sent: the draft is copied, Daksh pastes it into Gmail himself.
import { api } from './api.js';

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return u; } };
const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };

const S = { open: false, query: '', model: store.get('ef-model', 'sonnet'), busy: null, since: 0, error: '', result: null,
  draftFor: '', note: '', lang: 'fr', draft: null, contacts: [], key: '' };
async function loadContacts() { try { S.contacts = (await api.emailContacts()).contacts || []; } catch { /* history stays as is */ } }
let ticker = null;

const root = document.createElement('div');
root.id = 'email-finder';
document.body.appendChild(root);

function ensureLauncher() {
  const slot = document.querySelector('.sidebar-bottom');
  let btn = document.querySelector('#ef-launch');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'ef-launch'; btn.type = 'button'; btn.className = 'hb-launch ef-launch';
    btn.setAttribute('aria-label', 'Open Find email');
    btn.innerHTML = '<span class="hb-launch-mark ef-mark" aria-hidden="true">@</span><span class="hb-launch-copy"><strong>Find email</strong><small>Sonnet 5.5 · work emails + draft</small></span>';
    btn.addEventListener('click', () => setOpen(!S.open));
  }
  const mercury = document.querySelector('#hb-launch');
  if (slot) {
    const after = mercury && mercury.parentElement === slot ? mercury.nextSibling : slot.firstChild;
    if (btn.parentElement !== slot || (mercury && btn.previousSibling !== mercury && mercury.parentElement === slot)) slot.insertBefore(btn, after);
  } else if (!btn.isConnected) document.body.appendChild(btn);
  btn.classList.toggle('hb-in-sidebar', !!slot);
  btn.classList.toggle('ef-running', !!S.busy);
  btn.setAttribute('aria-expanded', String(S.open));
}
new MutationObserver(() => {
  const btn = document.querySelector('#ef-launch'), slot = document.querySelector('.sidebar-bottom'), m = document.querySelector('#hb-launch');
  if (!btn || !btn.isConnected || (slot && btn.parentElement !== slot) || (slot && m && m.parentElement === slot && btn.previousSibling !== m)) ensureLauncher();
}).observe(document.body, { childList: true, subtree: true });
ensureLauncher();

async function setOpen(v) { S.open = v; paint(); if (v) { setTimeout(() => root.querySelector('#ef-query')?.focus(), 30); await loadContacts(); if (S.open) paint(); } }

function tick() {
  clearInterval(ticker);
  if (!S.busy) return;
  ticker = setInterval(() => { const el = root.querySelector('.ef-busy-t'); if (el) el.textContent = busyText(); }, 1000);
}
function busyText() {
  const s = Math.round((Date.now() - S.since) / 1000);
  if (S.busy === 'draft') return `Sonnet 5.5 is writing the email… ${s}s`;
  const step = s < 25 ? 'Finding who this is' : s < 70 ? 'Looking for the company email domain and published emails' : 'Checking the email pattern and sources';
  return `${step}… ${s}s · usually 1–3 min`;
}

async function runFind() {
  const q = (root.querySelector('#ef-query')?.value || S.query).trim();
  S.query = q;
  if (q.length < 3 || S.busy) return;
  S.busy = 'find'; S.since = Date.now(); S.error = ''; S.result = null; S.draft = null; S.draftFor = '';
  paint(); tick();
  try {
    S.result = await api.findEmail(q, S.model);
    S.key = S.result.contact?.key || '';
    await loadContacts();
  } catch (e) { S.error = e.message || String(e); }
  S.busy = null; tick(); paint();
}

async function runDraft() {
  if (!S.result || !S.draftFor || S.busy) return;
  S.note = root.querySelector('#ef-note')?.value || S.note;
  S.busy = 'draft'; S.since = Date.now(); S.error = ''; S.draft = null;
  paint(); tick();
  try { S.draft = await api.draftEmail(S.result.person || {}, S.draftFor, S.note, S.lang, 'sonnet', S.key); await loadContacts(); }
  catch (e) { S.error = e.message || String(e); }
  S.busy = null; tick(); paint();
}

async function copyText(text, label) {
  try { await navigator.clipboard.writeText(text); toast(label + ' copied'); } catch { toast('Copy failed'); }
}
async function copyRich() {
  const d = S.draft; if (!d) return;
  try {
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([d.html], { type: 'text/html' }), 'text/plain': new Blob([d.plain], { type: 'text/plain' }) })]);
    toast('Email + signature copied. Paste it into Gmail.');
  } catch { copyText(d.plain, 'Plain email'); }
}
function toast(msg) {
  const el = root.querySelector('.ef-toast'); if (!el) return;
  el.textContent = msg; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true; }, 2600);
}

const CONF = { high: 'High', medium: 'Medium', low: 'Low' };
const KIND = { published: 'Published on a page', pattern: 'Built from company pattern', generic: 'Generic company inbox' };
function checksLine(c) {
  const out = [];
  if (c.domain_receives_mail === true) out.push('✓ domain receives mail');
  if (c.domain_receives_mail === false) out.push('✗ domain has no mail server');
  if (c.seen_on_source_page === true) out.push('✓ seen on the source page');
  if (c.seen_on_source_page === false) out.push('✗ not found on the source page');
  if (c.pattern_examples) out.push(`✓ ${c.pattern_examples} real colleague emails match`);
  return out.join(' · ');
}

function emailCard(r) {
  const best = S.result.best === r.email;
  const ex = (r.pattern_examples || []).map(e => `<li><code>${esc(e.email)}</code> · <a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(host(e.url))}</a></li>`).join('');
  return `<article class="ef-card ${best ? 'ef-best' : ''}">
    <div class="ef-row"><code class="ef-email">${esc(r.email)}</code>
      <span class="ef-chip ef-${esc(r.confidence)}">${CONF[r.confidence] || r.confidence}</span>${best ? '<span class="ef-chip ef-pick">Best</span>' : ''}</div>
    <div class="ef-kind">${esc(KIND[r.kind] || r.kind)}${r.why ? ' — ' + esc(r.why) : ''}</div>
    ${checksLine(r.checks || {}) ? `<div class="ef-checks">${esc(checksLine(r.checks || {}))}</div>` : ''}
    ${r.sources?.length ? `<div class="ef-src">Sources: ${r.sources.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(host(u))}</a>`).join(' · ')}</div>` : ''}
    ${ex ? `<details class="ef-ex"><summary>Pattern examples</summary><ul>${ex}</ul></details>` : ''}
    <div class="ef-actions"><button type="button" class="ef-btn" data-ef="copy" data-v="${esc(r.email)}">Copy</button>
      <button type="button" class="ef-btn ef-primary" data-ef="pick" data-v="${esc(r.email)}">Draft email</button></div>
  </article>`;
}

function draftBlock() {
  if (!S.draftFor) return '';
  const d = S.draft;
  return `<section class="ef-draft">
    <h3>Draft email to <code>${esc(S.draftFor)}</code> <small>Sonnet 5.5 · your format + signature</small></h3>
    <label class="ef-lbl" for="ef-note">What do you want? (optional)</label>
    <textarea id="ef-note" rows="2" placeholder="e.g. ask for a 15-min call about sustainability roles at NEXUS, or apply for the CSR Analyst job (ref 123)">${esc(S.note)}</textarea>
    <div class="ef-row"><div class="ef-seg" role="group" aria-label="Language">
      <button type="button" data-ef="lang" data-v="fr" aria-pressed="${S.lang === 'fr'}">FR</button>
      <button type="button" data-ef="lang" data-v="en" aria-pressed="${S.lang === 'en'}">EN</button></div>
      <button type="button" class="ef-btn ef-primary" data-ef="draft" ${S.busy ? 'disabled' : ''}>${d ? 'Write again' : 'Write draft'}</button></div>
    ${d ? `<div class="ef-subject"><span>Subject</span><strong>${esc(d.subject)}</strong><button type="button" class="ef-btn" data-ef="copysub">Copy</button></div>
      <iframe class="ef-preview" sandbox title="Email preview" srcdoc="${esc('<!doctype html><meta charset=utf-8><body style="margin:12px;background:#fff">' + d.html)}"></iframe>
      <div class="ef-actions"><button type="button" class="ef-btn ef-primary" data-ef="copyrich">Copy email + signature</button>
        <a class="ef-btn" href="https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(d.to)}&su=${encodeURIComponent(d.subject)}" target="_blank" rel="noopener noreferrer">Open Gmail</a></div>
      <p class="ef-hint">Open Gmail fills To + Subject. Paste the copied email in the body, check it, attach your CV if needed, then send yourself.</p>` : ''}
  </section>`;
}

function resultBlock() {
  const r = S.result; if (!r) return '';
  const p = r.person || {};
  const ids = (p.identity_sources || []).filter(u => /^https?:/.test(u));
  return `<section class="ef-person"><strong>${esc(p.name || 'Unknown person')}</strong>
      <div>${esc([p.title, p.company].filter(Boolean).join(' · '))}${p.location ? ' · ' + esc(p.location) : ''}</div>
      ${ids.length ? `<div class="ef-src">Who: ${ids.slice(0, 4).map(u => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(host(u))}</a>`).join(' · ')}</div>` : ''}</section>
    ${(r.emails || []).length ? r.emails.map(emailCard).join('') : '<p class="ef-empty">No credible work email found.</p>'}
    ${r.notes ? `<p class="ef-notes">${esc(r.notes)}</p>` : ''}
    ${(r.dropped || []).length ? `<details class="ef-ex"><summary>Removed ${r.dropped.length} not credible</summary><ul>${r.dropped.map(d => `<li><code>${esc(d.email)}</code> — ${esc(d.reason)}</li>`).join('')}</ul></details>` : ''}
    <p class="ef-meta">${esc(r.model)}${r.seconds ? ' · ' + esc(r.seconds) + 's' : ''}</p>
    ${statusBar()}
    ${draftBlock()}`;
}

const STATUS = { found: 'Found', drafted: 'Drafted', emailed: 'Emailed' };
const day = t => t ? new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '';
function contactRow() { return S.contacts.find(c => c.key === S.key); }
function statusBar() {
  const c = contactRow(); if (!c) return '';
  const emailed = c.status === 'emailed';
  return `<div class="ef-status"><span class="ef-chip ef-st-${esc(c.status)}">${STATUS[c.status] || c.status}${emailed && c.emailed_at ? ' · ' + esc(day(c.emailed_at)) : ''}</span>
    <button type="button" class="ef-btn" data-ef="status" data-k="${esc(c.key)}" data-v="${emailed ? (c.drafted_at ? 'drafted' : 'found') : 'emailed'}">${emailed ? 'Undo emailed' : 'Mark as emailed'}</button></div>`;
}
function historyBlock() {
  if (!S.contacts.length) return '';
  const rows = [...S.contacts].sort((a, b) => String(b.found_at).localeCompare(String(a.found_at)));
  const n = s => rows.filter(r => r.status === s).length;
  return `<section class="ef-hist"><h3>History <small>${rows.length} people · ${n('emailed')} emailed · ${n('drafted')} drafted</small></h3>
    ${rows.map(c => `<div class="ef-h"><button type="button" class="ef-h-main" data-ef="hist" data-v="${esc(c.key)}"><span><strong>${esc(c.name || c.query)}</strong><small>${esc(c.company || '')}</small></span><code>${esc(c.email || '—')}</code></button>
      <span class="ef-chip ef-st-${esc(c.status)}">${STATUS[c.status] || c.status}</span>
      <button type="button" class="ef-btn ef-mini" data-ef="status" data-k="${esc(c.key)}" data-v="${c.status === 'emailed' ? (c.drafted_at ? 'drafted' : 'found') : 'emailed'}" title="${c.status === 'emailed' ? 'Undo emailed' : 'Mark as emailed'}">${c.status === 'emailed' ? '↺' : '✓ Emailed'}</button></div>`).join('')}
  </section>`;
}

function paint() {
  ensureLauncher();
  if (!S.open) { root.innerHTML = ''; return; }
  const box = root.querySelector('#ef-query'); if (box) S.query = box.value;
  const nb = root.querySelector('#ef-note'); if (nb) S.note = nb.value;
  root.innerHTML = `<div class="ef-panel" role="dialog" aria-label="Find email">
    <header><div class="hb-title"><span class="hb-launch-mark ef-mark" aria-hidden="true">@</span><div><strong>Find email</strong><small>Public pages only · work emails · nothing is sent</small></div></div>
      <div class="ef-head-actions">${S.result ? `<button type="button" class="ef-btn" data-ef="back">History (${S.contacts.length})</button>` : ''}<button type="button" class="ef-x" data-ef="close" aria-label="Close Find email">×</button></div></header>
    <div class="ef-body">
      <label class="ef-lbl" for="ef-query">Who? Name, LinkedIn link, company…</label>
      <textarea id="ef-query" rows="2" placeholder="https://www.linkedin.com/in/… or Jane Doe, Head of ESG at Acme">${esc(S.query)}</textarea>
      <div class="ef-row"><div class="ef-seg" role="group" aria-label="Model">
        <button type="button" data-ef="model" data-v="sonnet" aria-pressed="${S.model === 'sonnet'}">Sonnet 5.5</button>
        <button type="button" data-ef="model" data-v="haiku" aria-pressed="${S.model === 'haiku'}">Haiku 5.5</button></div>
        <button type="button" class="ef-btn ef-primary" data-ef="find" ${S.busy ? 'disabled' : ''}>Find email</button></div>
      ${S.busy ? `<div class="ef-busy"><span class="ef-spin"></span><span class="ef-busy-t">${esc(busyText())}</span></div>` : ''}
      ${S.error ? `<p class="ef-error">${esc(S.error)}</p>` : ''}
      ${resultBlock()}
      ${!S.result && !S.busy ? historyBlock() : ''}
    </div><div class="ef-toast" hidden></div></div>`;
}

root.addEventListener('click', e => {
  const b = e.target.closest('[data-ef]'); if (!b) return;
  const a = b.dataset.ef, v = b.dataset.v;
  if (a === 'close') setOpen(false);
  else if (a === 'find') runFind();
  else if (a === 'model') { S.model = v; store.set('ef-model', v); paint(); }
  else if (a === 'copy') copyText(v, 'Email');
  else if (a === 'pick') { S.draftFor = v; S.draft = null; paint(); root.querySelector('.ef-draft')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  else if (a === 'lang') { S.lang = v; paint(); }
  else if (a === 'draft') runDraft();
  else if (a === 'copysub') copyText(S.draft?.subject || '', 'Subject');
  else if (a === 'copyrich') copyRich();
  else if (a === 'hist') { const h = S.contacts.find(c => c.key === v); if (h) { S.query = h.query; const qb = root.querySelector('#ef-query'); if (qb) qb.value = h.query; S.result = h.result || null; S.key = h.key; S.draft = null; S.draftFor = ''; S.error = ''; paint(); } }
  else if (a === 'back') { S.result = null; S.draft = null; S.draftFor = ''; S.key = ''; S.query = ''; S.error = ''; loadContacts().then(paint); paint(); }
  else if (a === 'status') { api.setEmailStatus(b.dataset.k, v).then(loadContacts).then(paint).catch(err => toast(err.message || 'Could not save')); }
});
root.addEventListener('keydown', e => {
  if (e.target.id === 'ef-query' && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runFind(); }
  if (e.key === 'Escape') setOpen(false);
});
