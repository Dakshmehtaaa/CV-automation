// Mercury 3 help drawer (26 Oct 2026): one saved chat per job, tucked-away chat list,
// per-chat running + unread state. History is saved through /api/help-threads so it survives reload.
import { api, items } from './api.js';

const EDIT_ASK = /\b(cv|resume|curriculum)\b.*\b(redo|rebuild|remake|edit|change|update|refait|refaire|modifi|change|swap|replace|remplace|add|ajoute|remove|enl[eè]ve)|\b(redo|rebuild|remake|edit|refai|modifi)\w*\b.*\bcv\b/i;

// 23 Oct: a CV edit takes 2–3 minutes (answer ~10 s, then the FR + EN builds) — now timed per chat.
function busyText(run) {
  const s = Math.round((Date.now() - run.since) / 1000);
  if (!run.edit || s < 8) return `Thinking… ${s}s`;
  const step = s < 70 ? 'Writing and checking the first page' : s < 150 ? 'Building the second-language page' : 'Joining both pages into one PDF';
  return `${step}… ${s}s · a CV edit takes about 2–3 minutes`;
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const genId = () => 'c' + Date.now().toString(36) + Array.from({ length: 4 }, () => Math.floor(Math.random() * 36).toString(36)).join('');

let open = false, roles = null, pickedRole = '';
let current = null;          // the chat being viewed (may be an unsaved empty draft)
let listOpen = false, doneOpen = false, loaded = false;

const threads = new Map();   // id -> saved chat (per-job history)
const drafts = new Map();    // role_id -> unsaved empty chat
const running = new Map();   // id -> {since, edit}  (memory only)
const draftMap = new Map();  // chat id -> textarea draft
const saveChains = new Map(); // chat id -> serialized PUT chain
let ticker = null;

const root = document.createElement('div');
root.id = 'help-bot';
document.body.appendChild(root);

/* ---------------------------------------------------------------- model ---- */

function newChat(roleId) {
  const r = (roles || []).find(x => x.id === roleId) || {};
  const now = new Date().toISOString();
  return {
    id: genId(), role_id: roleId || '', company: r.company || '', title: r.title || '',
    status: 'open', unread: false, messages: [], created_at: now, updated_at: now,
  };
}
function allChats() {
  const m = new Map();
  for (const t of threads.values()) m.set(t.id, t);
  if (current && current.messages.length) m.set(current.id, current);
  return [...m.values()];
}
function newestFor(roleId) {
  let best = null;
  for (const t of threads.values()) {
    if ((t.role_id || '') !== roleId) continue;
    if (t.status === 'done') continue;
    if (!best || String(t.updated_at || '') > String(best.updated_at || '')) best = t;
  }
  if (best) return best;
  for (const t of threads.values()) {
    if ((t.role_id || '') !== roleId) continue;
    if (!best || String(t.updated_at || '') > String(best.updated_at || '')) best = t;
  }
  return best;
}
function selectRole(roleId) {
  roleId = String(roleId || '');
  const keepDraft = current && (current.role_id || '') === roleId && !threads.has(current.id)
    && (current.messages.length || (draftMap.get(current.id) || '').trim());
  if (!keepDraft) {
    const found = newestFor(roleId);
    if (found) current = found;
    else {
      let d = drafts.get(roleId);
      if (!d || threads.has(d.id)) { d = newChat(roleId); drafts.set(roleId, d); }
      current = d;
    }
  }
  pickedRole = roleId;
  clearUnread(current);
}
function captureDraft() {
  const box = root.querySelector('#hb-input');
  if (!box) return;
  const id = box.dataset.chat || (current && current.id);
  if (id) draftMap.set(id, box.value);
}
function clearUnread(chat) {
  if (chat && chat.unread) { chat.unread = false; save(chat); }
}
function fillRoleMeta(chat) {
  if (!chat || !chat.role_id) return;
  if (chat.company && chat.title) return;
  const r = (roles || []).find(x => x.id === chat.role_id);
  if (!r) return;
  if (!chat.company) chat.company = r.company || '';
  if (!chat.title) chat.title = r.title || '';
}
function counts() {
  let unread = 0;
  for (const c of allChats()) if (c.unread) unread++;
  return { running: running.size, unread };
}
function chatLabel(chat) {
  const parts = [chat.company, chat.title].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'General';
}
function relTime(iso) {
  const t = Date.parse(iso || '');
  if (!t) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45) return 'now';
  if (s < 3600) return Math.round(s / 60) + 'm';
  if (s < 86400) return Math.round(s / 3600) + 'h';
  return Math.round(s / 86400) + 'd';
}

/* ------------------------------------------------------------- persistence - */

function bodyOf(chat) {
  return {
    role_id: chat.role_id || '',
    company: chat.company || '',
    title: chat.title || '',
    status: chat.status === 'done' ? 'done' : 'open',
    unread: Boolean(chat.unread),
    messages: chat.messages.map(m => {
      const out = { role: m.role, content: String(m.content == null ? '' : m.content).slice(0, 20000), at: m.at || new Date().toISOString() };
      if (m.failed) out.failed = true;
      if (m.edited && typeof m.edited === 'object') out.edited = m.edited;
      return out;
    }),
    created_at: chat.created_at,
    updated_at: chat.updated_at || new Date().toISOString(),
  };
}
// Fire-and-forget: never block the UI, never throw. PUTs are serialized per chat.
function save(chat) {
  if (!chat || !chat.messages.length) return;   // an empty chat is only saved once it has its first message
  fillRoleMeta(chat);
  threads.set(chat.id, chat);
  if (drafts.get(chat.role_id) === chat) drafts.delete(chat.role_id);
  chat.updated_at = new Date().toISOString();
  if (typeof api.saveHelpThread !== 'function') return;

  const id = chat.id;
  const prev = saveChains.get(id) || Promise.resolve();
  const next = prev.catch(() => {}).then(async () => {
    const body = bodyOf(chat); // build at send time, with latest state
    const res = await api.saveHelpThread(id, body);
    const t = res && res.thread;
    if (t) {
      if (t.created_at && !chat.created_at) chat.created_at = t.created_at;
      if (t.updated_at && (!chat.updated_at || String(t.updated_at) > String(chat.updated_at))) chat.updated_at = t.updated_at;
    }
  }).catch(() => {});
  saveChains.set(id, next);
  next.then(() => { if (saveChains.get(id) === next) saveChains.delete(id); });
}
async function loadThreads() {
  loaded = true;
  if (typeof api.helpThreads !== 'function') return;
  try {
    const res = await api.helpThreads();
    const list = (res && res.threads) || [];
    for (const t of list) if (t && t.id) threads.set(t.id, t);
    if (current && threads.has(current.id)) current = threads.get(current.id);
  } catch (_) { /* keep working in memory */ }
}
async function loadRoles() {
  if (roles) return;
  try { roles = items(await api.roles()).slice(0, 400); } catch (_) { roles = []; }
}

/* --------------------------------------------------------------- launching - */

function ensureLauncher() {
  const slot = document.querySelector('.sidebar-bottom');
  let btn = document.querySelector('#hb-launch');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'hb-launch'; btn.type = 'button'; btn.className = 'hb-launch';
    btn.setAttribute('aria-label', 'Open the help chat'); btn.dataset.hb = 'toggle';
    btn.innerHTML = '<img class="hb-launch-mark" src="/static/assets/inception-logo.png" alt="" width="28" height="28"><span class="hb-launch-copy"><strong>Mercury 3</strong><small>by Inception · help, answers, CV edits</small></span>';
    btn.addEventListener('click', () => { setOpen(!open); });
  }
  if (slot && btn.parentElement !== slot) slot.insertBefore(btn, slot.firstChild);
  else if (!slot && !btn.isConnected) document.body.appendChild(btn);
  btn.classList.toggle('hb-in-sidebar', !!slot);
  btn.setAttribute('aria-expanded', String(open));
  let badge = btn.querySelector('.hb-badge');
  if (!badge) { badge = document.createElement('span'); badge.className = 'hb-badge'; badge.hidden = true; btn.appendChild(badge); }
  const n = counts().unread;
  badge.textContent = n > 99 ? '99+' : String(n);
  badge.hidden = !n;
  btn.classList.toggle('hb-running', running.size > 0);
}
new MutationObserver(() => { if (!document.querySelector('#hb-launch')?.isConnected || (document.querySelector('.sidebar-bottom') && document.querySelector('#hb-launch')?.parentElement !== document.querySelector('.sidebar-bottom'))) ensureLauncher(); }).observe(document.body, { childList: true, subtree: true });

async function setOpen(v) {
  open = v;
  if (!open) { paint(); return; }

  paint(); // show the drawer immediately
  if (!roles) await loadRoles();
  if (!loaded) await loadThreads();
  if (!current) selectRole(pickedRole || '');
  else clearUnread(current);
  fillRoleMeta(current);
  paint();
}

/* ------------------------------------------------------------------- paint - */

function bubble(row, i, chat) {
  // 23 Oct: an edited CV gets its own card that opens the job's CV package (Edited CV is listed there).
  const roleId = row.roleId || (chat ? chat.role_id : '');
  const edited = row.edited && roleId
    ? `<div class="hb-result"><strong>Edited CV saved</strong><span>${esc(row.edited.folder || 'Mercury 3 edited CV')} · original untouched</span><button type="button" class="hb-result-open" data-action="open-role" data-role-id="${esc(roleId)}">Open CV package</button></div>` : '';
  return `<div class="hb-msg hb-${esc(row.role)}">${row.role === 'assistant' ? '<img class="hb-avatar" src="/static/assets/inception-logo.png" alt="">' : ''}<div class="hb-text">${esc(row.content).replace(/\n/g, '<br>')}</div>${edited}` +
    (row.role === 'assistant' ? `<button type="button" class="hb-copy" data-copy="${i}" data-copy-id="${esc(chat ? chat.id : '')}">Copy</button>` : '') + '</div>';
}
const suggestions = [
  ['Answer a form question', 'Draft my answer to this application question: '],
  ['Edit this CV', 'Make an edited CV for this job: '],
  ['How does Studio work?', 'How do I '],
];

function chatsLabel(c) {
  const bits = ['Chats'];
  if (c.running) bits.push(c.running + ' running');
  if (c.unread) bits.push(c.unread + ' unread');
  return bits.join(' · ');
}
function rowHtml(chat) {
  const isCur = current && current.id === chat.id;
  const run = running.has(chat.id);
  const last = chat.messages[chat.messages.length - 1];
  const prev = last ? String(last.content || '').replace(/\s+/g, ' ').slice(0, 140) : 'No messages yet';
  const dot = run ? '<span class="hb-dot-run" title="Running"></span>' : chat.unread ? '<span class="hb-dot-unread" title="Unread"></span>' : '';
  return `<button type="button" class="hb-chat-row${isCur ? ' is-current' : ''}" data-hb="open" data-id="${esc(chat.id)}">
    <span class="hb-chat-main"><span class="hb-chat-name">${esc(chatLabel(chat))}</span><span class="hb-chat-preview">${esc(prev)}</span></span>
    <span class="hb-chat-time">${esc(relTime(chat.updated_at))}</span>${dot}</button>`;
}
function chatListHtml() {
  const chats = allChats();
  const active = chats.filter(c => c.status !== 'done').sort((a, b) =>
    (running.has(b.id) ? 1 : 0) - (running.has(a.id) ? 1 : 0) ||
    (b.unread ? 1 : 0) - (a.unread ? 1 : 0) ||
    String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  const done = chats.filter(c => c.status === 'done')
    .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  let html = '<div class="hb-chat-group-title">Active</div>';
  html += active.length ? active.map(rowHtml).join('') : '<div class="hb-chat-row"><span class="hb-chat-main"><span class="hb-chat-preview">No chats yet</span></span></div>';
  if (done.length) {
    html += `<button type="button" class="hb-done-toggle" data-hb="done-toggle" aria-expanded="${doneOpen}">Done (${done.length})</button>`;
    if (doneOpen) html += done.map(rowHtml).join('');
  }
  return html;
}

function paint() {
  ensureLauncher();
  if (!open) { root.innerHTML = ''; return; }
  if (!current) selectRole(pickedRole || '');
  const chat = current;

  const prevInput = root.querySelector('#hb-input');
  const hadFocus = !!prevInput && document.activeElement === prevInput;
  if (prevInput && prevInput.dataset.chat) draftMap.set(prevInput.dataset.chat, prevInput.value);

  const options = (roles || []).map(r => `<option value="${esc(r.id)}">${esc((r.company || '') + ' · ' + (r.title || ''))}</option>`).join('');
  const empty = `<div class="hb-empty"><p class="hb-empty-title">How can I help?</p><p>Ask how Studio works, paste an application-form question, or pick a job above and ask for an edited CV.</p><div class="hb-suggest">${suggestions.map(([label, text]) => `<button type="button" data-hb-suggest="${esc(text)}">${esc(label)}</button>`).join('')}</div></div>`;
  const run = chat ? running.get(chat.id) : null;
  const msgs = chat ? chat.messages : [];
  const busyHtml = run ? `<div class="hb-msg hb-assistant"><img class="hb-avatar" src="/static/assets/inception-logo.png" alt=""><div class="hb-text hb-busy"><span class="hb-thinking"><i></i><i></i><i></i></span><span id="hb-busy-text">${esc(busyText(run))}</span></div></div>` : '';
  const c = counts();

  const steady = !!root.querySelector('.hb-panel');  // a repaint of an open drawer must not replay the slide-in
  root.innerHTML = `<section class="hb-panel${steady ? ' hb-steady' : ''}" role="dialog" aria-label="Mercury chat">
    <header><div class="hb-title"><img class="hb-launch-mark" src="/static/assets/inception-logo.png" alt="" width="28" height="28"><div><strong>Mercury 3</strong><small>Inception · reads your profile, CVs and adverts</small></div></div><div class="hb-head-actions">${msgs.length ? '<button type="button" class="hb-clear" data-hb="new">New chat</button>' : ''}${chat && chat.status === 'open' && msgs.length ? '<button type="button" class="hb-clear" data-hb="done">Done</button>' : ''}<button type="button" class="hb-x" data-hb="toggle" aria-label="Close Mercury chat">×</button></div></header>
    <label class="hb-ctx"><span>Job</span><select id="hb-role"><option value="">${roles ? 'No job · Studio, CV rules, profile' : 'Loading jobs…'}</option>${options}</select></label>
    <button type="button" class="hb-chats" data-hb="chats" aria-expanded="${listOpen}">${esc(chatsLabel(c))}</button>
    ${listOpen ? `<div class="hb-chat-list" id="hb-chat-list">${chatListHtml()}</div>` : ''}
    <p class="hb-note" id="hb-note"></p>
    <div class="hb-log" id="hb-log" aria-live="polite">${msgs.length ? msgs.map((m, i) => bubble(m, i, chat)).join('') : empty}${busyHtml}</div>
    <form id="hb-form"><textarea id="hb-input" data-chat="${esc(chat ? chat.id : '')}" rows="2" placeholder="Message Mercury…" aria-label="Your question"></textarea><button type="submit" class="hb-send" ${run ? 'disabled' : ''} aria-label="Send">↑</button></form>
  </section>`;

  const sel = root.querySelector('#hb-role');
  if (sel && chat) sel.value = chat.role_id || '';
  const chosen = (roles || []).find(r => r.id === (chat ? chat.role_id : ''));
  const note = root.querySelector('#hb-note');
  if (note) note.textContent = chosen ? 'Loaded for ' + (chosen.company || '') + ': the CV we built, the advert, the cover letter, the motivation note and your profile.' : '';
  const input = root.querySelector('#hb-input');
  if (input) {
    const draft = (chat && draftMap.get(chat.id)) || '';
    if (draft) {
      input.value = draft;
      try { input.setSelectionRange(draft.length, draft.length); } catch (_) {}
    }
    if (hadFocus || !prevInput) input.focus();
  }
  const log = root.querySelector('#hb-log');
  if (log) log.scrollTop = log.scrollHeight;
}

window.openHelpBot = async roleId => {
  open = true;
  const want = String(roleId || '');
  paint();
  if (!roles) await loadRoles();
  if (!loaded) await loadThreads();
  pickedRole = want;
  selectRole(want);
  fillRoleMeta(current);
  paint();
  root.querySelector('#hb-input')?.focus();
};

/* -------------------------------------------------------------------- send - */

async function send(text) {
  const chat = current;
  if (!chat) return;
  const role_id = chat.role_id || '';
  chat.messages.push({ role: 'user', content: text, at: new Date().toISOString() });
  chat.status = 'open';                       // sending in a done chat reopens it
  threads.set(chat.id, chat);
  if (drafts.get(chat.role_id) === chat) drafts.delete(chat.role_id);
  save(chat);

  running.set(chat.id, { since: Date.now(), edit: Boolean(role_id) && EDIT_ASK.test(text) });
  ensureTicker();
  paint();

  try {
    const turns = chat.messages.filter(row => !row.failed).map(({ role, content }) => ({ role, content }));
    let res;
    try { res = await api.helpChat(turns, role_id); }
    catch (error) {
      if (error.status !== 403 && error.status !== 0) throw error;
      // 26 Oct: status 0 = Studio restarted mid-answer (the request died with it); wait for it, then retry once
      if (error.status === 0) await studioBack();
      await api.overview();  // loads the session token (new after a restart), then retry once
      res = await api.helpChat(turns, role_id);
    }
    chat.messages.push({ role: 'assistant', content: res.reply, at: new Date().toISOString(), edited: res.edited_cv || null, roleId: role_id });
  } catch (error) {
    chat.messages.push({ role: 'assistant', content: 'I could not answer: ' + (error.message || 'unknown error'), at: new Date().toISOString(), failed: true });
  }

  running.delete(chat.id);
  chat.updated_at = new Date().toISOString();
  chat.unread = !open || current !== chat || document.hidden;
  save(chat);
  ensureTicker();
  if (open) paint();
  else ensureLauncher();
}

async function studioBack() {
  for (let n = 0; n < 30; n++) {
    try { if ((await fetch('/api/health', { cache: 'no-store' })).ok) return; } catch (_) { /* still restarting */ }
    await new Promise(done => setTimeout(done, 2000));
  }
  throw new Error('CV Studio is not running. Start it again, then retry.');
}

/* ------------------------------------------------------------------ ticker - */

function tick() {
  if (current) {
    const run = running.get(current.id);
    const node = root.querySelector('#hb-busy-text');
    if (run && node) node.textContent = busyText(run);
  }
  if (open && listOpen) {
    const el = root.querySelector('#hb-chat-list');
    if (el) el.innerHTML = chatListHtml();
  }
  if (!running.size && ticker) { clearInterval(ticker); ticker = null; }
}
function ensureTicker() {
  if (running.size) { if (!ticker) ticker = setInterval(tick, 1000); }
  else if (ticker) { clearInterval(ticker); ticker = null; }
}

/* ------------------------------------------------------------------ events - */

root.addEventListener('click', async event => {
  const hint = event.target.closest('[data-hb-suggest]');
  if (hint) { const box = root.querySelector('#hb-input'); if (box) { box.value = hint.dataset.hbSuggest; box.focus(); } return; }
  const t = event.target.closest('[data-hb],[data-copy]');
  if (!t) return;

  if (t.dataset.copy !== undefined) {
    const chat = allChats().find(c => c.id === t.dataset.copyId) || current;
    const msg = chat && chat.messages[Number(t.dataset.copy)];
    if (msg) { try { await navigator.clipboard.writeText(msg.content); t.textContent = 'Copied'; } catch (_) {} }
    return;
  }

  const act = t.dataset.hb;
  if (act === 'toggle') { await setOpen(!open); }
  else if (act === 'chats') { listOpen = !listOpen; paint(); }
  else if (act === 'done-toggle') { doneOpen = !doneOpen; paint(); }
  else if (act === 'new') {
    captureDraft();
    const chat = current;
    const role = chat ? chat.role_id : '';
    if (chat && chat.messages.length) { chat.status = 'done'; save(chat); }
    const fresh = newChat(role);
    drafts.set(role, fresh);
    current = fresh;
    paint();
  }
  else if (act === 'done') {
    const chat = current;
    if (chat && chat.messages.length) { chat.status = 'done'; save(chat); paint(); }
  }
  else if (act === 'open') {
    const chat = allChats().find(c => c.id === t.dataset.id);
    if (!chat) return;
    captureDraft();
    current = chat;
    pickedRole = chat.role_id || '';
    listOpen = false;
    clearUnread(chat);
    fillRoleMeta(chat);
    paint();
  }
});

root.addEventListener('change', event => {
  if (event.target.id !== 'hb-role') return;
  captureDraft();
  pickedRole = event.target.value;
  listOpen = false;
  selectRole(pickedRole);
  fillRoleMeta(current);
  paint();
});

root.addEventListener('submit', event => {
  event.preventDefault();
  const box = root.querySelector('#hb-input');
  const text = box.value.trim();
  if (!text || !current || running.has(current.id)) return;
  draftMap.set(current.id, '');
  box.value = '';
  send(text);
});

root.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && event.target.id === 'hb-input') { event.preventDefault(); root.querySelector('#hb-form')?.requestSubmit(); }
  if (event.key === 'Escape' && open) { setOpen(false); }
});

// A reply that landed while the tab was hidden is read once Daksh comes back to the open chat.
document.addEventListener('visibilitychange', () => { if (!document.hidden && open && current && current.unread) { clearUnread(current); paint(); } });
ensureLauncher();
paint();
loadThreads().then(() => { ensureLauncher(); if (open) paint(); });
