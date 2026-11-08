// Outreach Desk (5 Nov 2026, Daksh): replaces the old Shortlist + Contact library tabs.
// One screen: reach someone (LinkedIn link or name, optional linked job) -> Sonnet 5.5 finds a credible work email ->
// Sonnet 5.5 drafts in Daksh's format + signature (editable) -> he copies into Gmail and sends himself ->
// "Mark sent" -> after 5 days a follow-up (max 2) shows up in Today. Same history file as the Find email drawer.
// Plan: plans/outreach-revamp/ (Gemini 3.8 Flash agents, 3 rounds; Claude picked the practical parts).
import { api } from './api.js';
import { escape as e, icon } from './ui.js';
import { replaceStudioContent } from './prospect.js';

const DAY = 86400000;
const FOLLOW_AFTER_DAYS = 5;
const ctx = { roles: [], shortlist: {}, roleById: new Map() };
const S = { view: 'home', list: 'all', showJob: false, openMore: false, signature: '', contacts: [], loaded: false, filter: 'active', query: '', roleId: '', busy: null, since: 0, error: '',
  openKey: '', draft: null, lang: '', note: '', showSources: false, followupFor: '' };
let page = null, ticker = null;

const days = t => t ? Math.floor((Date.now() - new Date(t).getTime()) / DAY) : null;
const ago = t => { const d = days(t); return d == null ? '' : d <= 0 ? 'today' : d === 1 ? '1 day ago' : `${d} days ago`; };
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return u; } };
const initials = s => String(s || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();

function followupDue(c) {
  return c.status === 'emailed' && !c.archived && (c.followups || []).length < 2 && days(c.emailed_at) >= FOLLOW_AFTER_DAYS;
}
function stageOf(c) {
  if (c.archived) return 'archived';
  if (c.status === 'replied') return 'replied';
  if (followupDue(c)) return 'due';
  if (c.status === 'emailed') return 'waiting';
  if (c.status === 'drafted') return 'to_send';
  return 'found';
}
const STAGE = { found: ['Email found', 'neutral'], to_send: ['Draft ready', 'amber'], waiting: ['Waiting reply', 'blue'],
  due: ['Follow-up due', 'red'], replied: ['Replied', 'green'], archived: ['Archived', 'neutral'] };

function stageText(c) {
  const st = stageOf(c);
  if (st === 'waiting') return `Emailed ${ago(c.emailed_at)}${(c.followups || []).length ? ` · ${(c.followups || []).length} follow-up` : ''}`;
  if (st === 'due') return `Emailed ${ago(c.emailed_at)} · no reply`;
  if (st === 'replied') return `Replied ${ago(c.replied_at)}`;
  if (st === 'to_send') return `Drafted ${ago(c.drafted_at)}`;
  return `Found ${ago(c.found_at)}`;
}

async function load() {
  try { const r = await api.emailContacts(); S.contacts = r.contacts || []; S.signature = r.signature || S.signature || ''; S.loaded = true; } catch (err) { S.error = err.message || String(err); S.loaded = true; }
}
const contact = key => S.contacts.find(c => c.key === key);

/* ------------------------------------------------------------------ jobs --- */
function jobOptions(selected) {
  const starred = Object.keys(ctx.shortlist || {});
  const roles = ctx.roles.filter(r => (r.collection || 'current') !== 'inactive');
  const label = r => `${r.company || 'Company'} · ${r.title || 'Role'}`;
  const sorted = [...roles].sort((a, b) => label(a).localeCompare(label(b)));
  const star = sorted.filter(r => starred.includes(r.id));
  const rest = sorted.filter(r => !starred.includes(r.id));
  const opt = r => `<option value="${e(r.id)}" ${r.id === selected ? 'selected' : ''}>${e(label(r))}</option>`;
  return `<option value="">No job · just networking</option>${star.length ? `<optgroup label="Starred in Apply">${star.map(opt).join('')}</optgroup>` : ''}<optgroup label="All jobs">${rest.map(opt).join('')}</optgroup>`;
}
function guessRole(company) {
  if (!company) return '';
  const c = company.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (c.length < 3) return '';
  const hit = ctx.roles.find(r => { const rc = String(r.company || '').toLowerCase().replace(/[^a-z0-9]/g, ''); return rc && (rc.includes(c) || c.includes(rc)); });
  return hit ? hit.id : '';
}
function roleLang(roleId) { const r = ctx.roleById.get(roleId); const l = String(r?.language || '').toLowerCase(); return l.startsWith('en') ? 'en' : 'fr'; }

/* ---------------------------------------------------------------- actions -- */
function startBusy(kind) { S.busy = kind; S.since = Date.now(); S.error = ''; clearInterval(ticker); ticker = setInterval(() => { const n = page?.querySelector('.od-busy-t'); if (n) n.textContent = busyText(); }, 1000); }
function stopBusy() { S.busy = null; clearInterval(ticker); }
function busyText() {
  const s = Math.round((Date.now() - S.since) / 1000);
  if (S.busy === 'find') return `Sonnet 5.5 is finding who this is and a credible work email… ${s}s (1–3 min)`;
  if (S.busy === 'followup') return `Sonnet 5.5 is writing the follow-up… ${s}s`;
  return `Sonnet 5.5 is writing the email in your style… ${s}s`;
}

async function runFind() {
  const q = (page.querySelector('#od-query')?.value || '').trim();
  S.roleId = page.querySelector('#od-job')?.value || '';
  if (q.length < 3 || S.busy) return;
  S.query = q; startBusy('find'); paint();
  try {
    const res = await api.findEmail(q, 'sonnet');
    await load();
    const key = res.contact?.key;
    S.query = ''; const qi = page.querySelector('#od-query'); if (qi) qi.value = '';
    if (key) {
      const roleId = S.roleId || guessRole(res.person?.company);
      if (roleId) await api.updateEmailContact(key, { role_id: roleId });
      await load();
      S.roleId = ''; S.showJob = false;
      stopBusy(); openPerson(key);
      if (res.best) await runDraft();
      return;
    }
  } catch (err) { S.error = err.message || String(err); }
  stopBusy(); paint();
}

async function runDraft() {
  const c = contact(S.openKey); if (!c || !c.email || S.busy) return;
  S.note = page.querySelector('#od-note')?.value ?? S.note;
  const roleId = page.querySelector('#od-wjob')?.value ?? c.role_id ?? '';
  startBusy('draft'); S.followupFor = ''; paint();
  try {
    if (roleId !== (c.role_id || '')) await api.updateEmailContact(c.key, { role_id: roleId });
    const person = c.result?.person || { name: c.name, title: c.title, company: c.company };
    S.draft = await api.draftEmail(person, c.email, S.note, S.lang || 'fr', 'sonnet', c.key, roleId);
    await load();
  } catch (err) { S.error = err.message || String(err); }
  stopBusy(); paint();
}

async function runFollowup(key) {
  const c = contact(key); if (!c || S.busy) return;
  S.openKey = key; S.view = 'person'; startBusy('followup'); S.draft = null; paint();
  try { S.draft = await api.followupEmail(key, c.role_id || ''); S.followupFor = key; await load(); }
  catch (err) { S.error = err.message || String(err); }
  stopBusy(); paint();
}

function currentDraftFromEditor() {
  if (!S.draft) return null;
  const subject = page.querySelector('#od-subject')?.value ?? S.draft.subject;
  const body = page.querySelector('#od-body');
  const bodyHtml = body ? body.innerHTML : S.draft.body_html;
  const sig = S.signature || S.draft.html.slice(S.draft.html.indexOf('</div><br>') + 10);
  const html = `<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1d2a24;">${bodyHtml}</div><br>${sig}`;
  const plain = (body ? body.innerText : S.draft.plain) + '';
  return { ...S.draft, subject, body_html: bodyHtml, html, plain };
}

async function copyRich() {
  const d = currentDraftFromEditor(); if (!d) return;
  try {
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([d.html], { type: 'text/html' }), 'text/plain': new Blob([d.plain], { type: 'text/plain' }) })]);
    toast('Email + signature copied. Paste it in the Gmail body.');
  } catch { copyText(d.plain, 'Email'); }
}
async function copyText(t, label) { try { await navigator.clipboard.writeText(t); toast(`${label} copied`); } catch { toast('Copy failed'); } }
function toast(msg) { const n = page?.querySelector('.od-toast'); if (!n) return; n.textContent = msg; n.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { n.hidden = true; }, 2600); }

async function setStatus(key, status) {
  try {
    if (status === 'emailed' && S.openKey === key && S.draft && !S.followupFor) {
      const d = currentDraftFromEditor();
      await api.updateEmailContact(key, { last_subject: d.subject, last_body: d.body_html });
    }
    await api.setEmailStatus(key, status);
    if (status === 'emailed' && S.openKey === key) { S.draft = null; S.view = 'home'; }
    await load(); paint();
    if (status === 'emailed') toast('Marked as sent. A follow-up shows up in Today after 5 days.');
  } catch (err) { toast(err.message || 'Could not save'); }
}
async function archive(key, value) { try { await api.updateEmailContact(key, { archived: value }); await load(); if (value && S.view === 'person') S.view = 'home'; paint(); } catch (err) { toast(err.message || 'Could not save'); } }

/* -------------------------------------------------------------- rendering -- */
// 5 Nov evening (Daksh: "start UI should be clean, dashboard first, then follow-up options"):
// three views. home = one search box + 3 action cards + 5 recent people. list = one group. person = the writer.
function chip(c) { const [t, tone] = STAGE[stageOf(c)]; return `<span class="od-chip od-${tone}">${t}</span>`; }
const byRecent = (a, b) => String(b.emailed_at || b.drafted_at || b.found_at).localeCompare(String(a.emailed_at || a.drafted_at || a.found_at));
const LISTS = { due: ['Follow up', 'No reply after 5 days. One click writes a short follow-up.'], to_send: ['Ready to send', 'Drafts you have not sent yet.'],
  waiting: ['Waiting for a reply', 'Emailed, less than 5 days ago.'], replied: ['Replied', 'They answered. Well done.'], all: ['Everyone', 'Everyone you looked up.'], archived: ['Archived', ''] };
const inList = (c, l) => l === 'all' ? !c.archived : l === 'archived' ? !!c.archived : stageOf(c) === l;

function personRow(c) {
  const action = stageOf(c) === 'due' ? `<button class="button button-primary button-small" data-od="followup" data-k="${e(c.key)}">Follow up</button>`
    : stageOf(c) === 'to_send' ? `<button class="button button-secondary button-small" data-od="open" data-k="${e(c.key)}">Open draft</button>`
    : stageOf(c) === 'found' && c.email ? `<button class="button button-secondary button-small" data-od="open" data-k="${e(c.key)}">Write</button>` : '';
  return `<li class="od-row"><button class="od-row-main" data-od="open" data-k="${e(c.key)}"><span class="od-av">${e(initials(c.name))}</span>
    <span class="od-who"><strong>${e(c.name || c.query)}</strong><small>${e(c.company || '')}${c.email ? ' · ' + e(c.email) : ''}</small></span></button>
    <span class="od-state">${chip(c)}<small>${e(stageText(c))}</small></span><span class="od-acts">${action}</span></li>`;
}

function searchBox(big) {
  return `<section class="od-search ${big ? 'is-big' : ''}">
    ${big ? '<h2>Who do you want to reach?</h2>' : ''}
    <div class="od-search-row"><input id="od-query" type="text" placeholder="Paste a LinkedIn link, or name + company" value="${e(S.query)}" aria-label="Person to reach" ${S.busy ? 'disabled' : ''}>
      <button class="button button-primary" data-od="find" ${S.busy ? 'disabled' : ''}>Find email</button></div>
    ${S.showJob || S.roleId ? `<label class="od-jobpick">For a job (optional)<select id="od-job">${jobOptions(S.roleId)}</select></label>` : `<button class="od-link" data-od="showjob">+ Link it to a job</button>`}
    ${S.busy === 'find' ? `<div class="od-busy"><span class="spinner" aria-hidden="true"></span><span class="od-busy-t">${e(busyText())}</span></div>` : ''}
    ${S.error && S.view === 'home' ? `<p class="od-error">${e(S.error)}</p>` : ''}
    <p class="od-fine">Sonnet 5.5 · public pages only · work emails only · nothing is sent for you</p></section>`;
}

function home() {
  const n = l => S.contacts.filter(c => inList(c, l)).length;
  const card = (l, tone, hint) => `<button class="od-card ${tone} ${n(l) ? '' : 'is-zero'}" data-od="list" data-v="${l}"><strong>${n(l)}</strong><span>${LISTS[l][0]}</span><small>${hint}</small></button>`;
  const recent = S.contacts.filter(c => !c.archived).sort(byRecent).slice(0, 5);
  return `${searchBox(true)}
    <div class="od-cards">${card('due', 'is-hot', 'Write the follow-up')}${card('to_send', 'is-warm', 'Copy, send from Gmail')}${card('waiting', '', 'Nothing to do yet')}</div>
    ${S.loaded && recent.length ? `<section class="od-recent"><div class="od-recent-head"><h3>Recent</h3><button class="od-link" data-od="list" data-v="all">See all ${n('all')}${n('replied') ? ` · ${n('replied')} replied` : ''}</button></div><ul class="od-list">${recent.map(personRow).join('')}</ul></section>` : ''}`;
}

function listView() {
  const [title, sub] = LISTS[S.list] || LISTS.all;
  const rows = S.contacts.filter(c => inList(c, S.list)).sort(byRecent);
  const tabs = ['all', 'due', 'to_send', 'waiting', 'replied', 'archived'].map(l => `<button class="od-tab ${S.list === l ? 'is-on' : ''}" data-od="list" data-v="${l}">${LISTS[l][0]} <span>${S.contacts.filter(c => inList(c, l)).length}</span></button>`).join('');
  return `<button class="od-back" data-od="home">← Outreach</button>
    <div class="od-tabs">${tabs}</div>
    <section class="od-recent"><div class="od-recent-head"><h3>${e(title)}</h3><small>${e(sub)}</small></div>
    ${rows.length ? `<ul class="od-list">${rows.map(personRow).join('')}</ul>` : '<p class="od-muted">Nobody here.</p>'}</section>`;
}

function personView() {
  const c = contact(S.openKey); if (!c) { S.view = 'home'; return home(); }
  const r = c.result || {};
  const best = (r.emails || []).find(x => x.email === c.email) || {};
  const role = c.role_id && ctx.roleById.get(c.role_id);
  const files = (role?.artifacts || []).filter(a => ['CV', 'Cover letter'].includes(a.label));
  const d = S.draft;
  const isFollow = !!S.followupFor;
  const st = stageOf(c);
  const next = st === 'due' ? `<button class="button button-primary" data-od="followup" data-k="${e(c.key)}" ${S.busy ? 'disabled' : ''}>Write follow-up ${(c.followups || []).length + 1}</button>`
    : (st === 'found' || st === 'to_send') && !d && c.email ? `<button class="button button-primary" data-od="draft" ${S.busy ? 'disabled' : ''}>Write the email</button>` : '';
  const more = [];
  if (st === 'waiting' || st === 'due') more.push(`<button class="button button-ghost button-small" data-od="status" data-v="replied" data-k="${e(c.key)}">They replied</button>`);
  if (c.status === 'emailed' && st !== 'due' && (c.followups || []).length < 2) more.push(`<button class="button button-ghost button-small" data-od="followup" data-k="${e(c.key)}">Follow up now</button>`);
  more.push(`<button class="button button-ghost button-small" data-od="archive" data-v="${c.archived ? '0' : '1'}" data-k="${e(c.key)}">${c.archived ? 'Restore' : 'Archive'}</button>`);
  return `<button class="od-back" data-od="home">← Outreach</button>
  <section class="od-person">
    <header><span class="od-av is-big">${e(initials(c.name))}</span><div><h2>${e(c.name || c.query)}</h2><p>${e([c.title, c.company].filter(Boolean).join(' · '))}</p></div>${chip(c)}</header>
    <div class="od-mailbox"><code>${e(c.email || 'No credible work email found')}</code>${c.confidence ? `<span class="od-conf od-c-${e(c.confidence)}">${e(c.confidence)} confidence</span>` : ''}
      ${c.email ? `<button class="od-link" data-od="copy" data-v="${e(c.email)}">Copy</button>` : ''}<button class="od-link" data-od="sources">${S.showSources ? 'Hide proof' : 'Why this email?'}</button>
      <span class="od-muted">${e(stageText(c))}</span></div>
    ${S.showSources ? `<div class="od-sources"><p>${e(best.why || r.notes || '')}</p>${(best.pattern_examples || []).map(x => `<div><code>${e(x.email)}</code> · <a href="${e(x.url)}" target="_blank" rel="noopener noreferrer">${e(host(x.url))}</a></div>`).join('')}${(best.sources || []).map(u => `<div><a href="${e(u)}" target="_blank" rel="noopener noreferrer">${e(host(u))}</a></div>`).join('')}</div>` : ''}
    ${next ? `<div class="od-next">${next}</div>` : ''}
    ${S.busy && S.busy !== 'find' ? `<div class="od-busy"><span class="spinner" aria-hidden="true"></span><span class="od-busy-t">${e(busyText())}</span></div>` : ''}
    ${S.error ? `<p class="od-error">${e(S.error)}</p>` : ''}
    ${d ? `
    <div class="od-draft">
      <input id="od-subject" value="${e(d.subject)}" aria-label="Subject">
      <div id="od-body" class="od-body" contenteditable="true" aria-label="Email, editable">${d.body_html}</div>
      <div class="od-sig-note">+ your signature is added when you copy</div>
      <div class="od-send"><button class="button button-primary" data-od="copyrich">1 · Copy email</button>
        <a class="button button-secondary" target="_blank" rel="noopener noreferrer" href="https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(d.to)}&su=${encodeURIComponent(d.subject)}">2 · Open Gmail</a>
        <button class="button button-secondary" data-od="status" data-v="emailed" data-k="${e(c.key)}">3 · ✓ I sent it</button></div>
      <details class="od-more" ${S.openMore ? 'open' : ''}><summary>Change or rewrite</summary>
        <div class="od-w-controls"><label>Job<select id="od-wjob">${jobOptions(c.role_id || '')}</select></label>
          <div class="od-seg" role="group" aria-label="Language"><button data-od="lang" data-v="fr" aria-pressed="${(S.lang || c.language || 'fr') === 'fr'}">FR</button><button data-od="lang" data-v="en" aria-pressed="${(S.lang || c.language) === 'en'}">EN</button></div></div>
        <textarea id="od-note" rows="2" placeholder="What you want, in your words (e.g. a 15-min call about their CSRD team)">${e(S.note)}</textarea>
        <button class="button button-secondary button-small" data-od="draft" ${S.busy ? 'disabled' : ''}>${isFollow ? 'Write a first email instead' : 'Rewrite'}</button>
        ${files.length ? `<div class="od-files"><span>Attach in Gmail:</span>${files.map(a => `<a href="${e(a.url)}&download=1" download>${icon('file')}${e(a.label)}</a>`).join('')}</div>` : ''}
        ${d.linkedin_note ? `<div class="od-li"><span>LinkedIn note (${d.linkedin_note.length}/300)</span><p>${e(d.linkedin_note)}</p><button class="od-link" data-od="copyli">Copy note</button></div>` : ''}
      </details>
    </div>` : ''}
    <div class="od-person-more">${more.join('')}</div>
  </section>`;
}

function paint() {
  if (!page || !page.isConnected) return;
  const q = page.querySelector('#od-query'); if (q && !S.busy && !q.disabled) S.query = q.value;
  const n = page.querySelector('#od-note'); if (n) S.note = n.value;
  const j = page.querySelector('#od-job'); if (j) S.roleId = j.value;
  const m = page.querySelector('.od-more'); if (m) S.openMore = m.open;
  const body = S.view === 'person' ? personView() : S.view === 'list' ? listView() : home();
  page.innerHTML = `<div class="page-heading"><h1>Outreach</h1></div><div class="od-wrap">${body}</div><div class="od-toast" hidden></div>`;
}

function show(view, extra = {}) { Object.assign(S, { view, error: '' }, extra); paint(); window.scrollTo({ top: 0 }); }

function bind() {
  page.addEventListener('click', ev => {
    const b = ev.target.closest('[data-od]'); if (!b) return;
    const a = b.dataset.od, v = b.dataset.v, k = b.dataset.k;
    if (a === 'find') runFind();
    else if (a === 'home') show('home');
    else if (a === 'list') show('list', { list: v });
    else if (a === 'showjob') { S.showJob = true; paint(); }
    else if (a === 'open') openPerson(k);
    else if (a === 'draft') runDraft();
    else if (a === 'followup') { openPerson(k, true); runFollowup(k); }
    else if (a === 'lang') { S.lang = v; S.openMore = true; paint(); }
    else if (a === 'sources') { S.showSources = !S.showSources; paint(); }
    else if (a === 'status') setStatus(k, v);
    else if (a === 'archive') archive(k, v === '1');
    else if (a === 'copy') copyText(v, 'Email');
    else if (a === 'copyrich') copyRich();
    else if (a === 'copyli') copyText(S.draft?.linkedin_note || '', 'LinkedIn note');
  });
  page.addEventListener('keydown', ev => { if (ev.target.id === 'od-query' && ev.key === 'Enter') { ev.preventDefault(); runFind(); } });
}

function openPerson(k, keepDraft = false) {
  const c = contact(k); if (!c) return;
  if (S.openKey !== k || !keepDraft) { S.draft = null; S.followupFor = ''; S.note = ''; S.showSources = false; S.openMore = false; }
  S.openKey = k; S.lang = c.language || (c.role_id ? roleLang(c.role_id) : 'fr');
  if (!keepDraft && c.last_body && c.status === 'drafted') S.draft = { to: c.email, subject: c.last_subject || '', body_html: c.last_body, html: `<div>${c.last_body}</div><br>`, plain: c.last_body.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, ''), linkedin_note: c.linkedin_note || '' };
  show('person');
}

export function mountOutreach(main, data) {
  ctx.roles = data.roles || []; ctx.shortlist = data.shortlist || {};
  ctx.roleById = new Map(ctx.roles.map(r => [r.id, r]));
  const existing = main.querySelector('.page-outreach');
  if (existing && existing.isConnected) { page = existing; return; }   // keep typing, drafts and scroll on background refreshes
  replaceStudioContent(main, '<div class="page page-outreach is-entering"></div>');
  page = main.querySelector('.page-outreach');
  bind(); paint();
  load().then(paint);
}

// The Find email drawer (sidebar) shares the same history: refresh when it changes something.
window.addEventListener('focus', () => { if (page?.isConnected && !S.busy) load().then(paint); });
