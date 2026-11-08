import { api, items } from './api.js';
import { escape as e, text, formatNumber, date, relativeDate, initials, statusLabel as rawStatusLabel, badge, safeUrl, icon, empty, loading, errorState, markdown } from './ui.js';
import { buildColourHomeModel } from './home-beta.js';
import { renderHomeNext, bindHomeNext, periodIds, DEFAULT_SECTIONS } from './home-next.js';
import { mountColourHome, unmountColourHome, savedColourHomePeriod } from './home-colour.js';
import { mountProspect, refreshProspect, replaceStudioContent } from './prospect.js';
import { renderAudits } from './audits.js';
import { mountOutreach } from './outreach.js';

const pages = {
  overview: { name: 'Home', icon: 'grid', title: 'Home' },
  apply: { name: 'Apply', icon: 'upRight', title: 'Apply' },
  shortlist: { name: 'Outreach', icon: 'star', title: 'Outreach' },
  library: { name: 'CVs', icon: 'file', title: 'CVs' },
  contacts: { name: 'Contacts', icon: 'mail', title: 'Outreach' },
  prospect: { name: 'Prospect', icon: 'search', title: 'Prospect' },
  settings: { name: 'Settings', icon: 'settings', title: 'Settings' },
  workspace: { name: 'Profile & search', icon: 'book', title: 'Profile & search' },
  activity: { name: 'History', icon: 'clock', title: 'Run history' },
  audits: { name: 'Audits', icon: 'shield', title: 'Audits' },
};
const fallbackStages = [
  { id: 'hunt', label: 'Discover', model: 'Sol 6.1 high', detail: 'Read public adverts across six sources in parallel when provider capacity allows: 20 minutes of collection and a 30 minute hard ceiling per source. LinkedIn, APEC and Indeed start at 24 hours, then widen to seven days. Keep full advert evidence and honest shortfalls.' },
  { id: 'inbox', label: 'Inbox review', model: 'Sol 6.1 medium', detail: 'Review the inbox under the established read-only gates.' },
  { id: 'hunt_audit', label: 'Hunt review', model: 'Sol 6.1 medium', detail: 'Independently review shortlisted sources, fit, language, and history before Build.' },
  { id: 'build', label: 'Build CVs', model: 'Sol 6.1 low', detail: 'Build each CV package from the verified word bank and the established layout.' },
  { id: 'build_audit', label: 'Build review', model: 'Sol 6.1 medium', detail: 'Review the finished documents and their artifact evidence.' },
  { id: 'sol_review', label: 'Final review', model: 'Sol 6.1 high', detail: 'Review the independent audits and approve the completed handoff.' },
  { id: 'sync', label: 'CV Studio sync', model: 'Sol 6.1 low', detail: 'Verify approved role artifacts in the local CV Studio catalog.' },];
const contactNoticeStorageKey = 'cv-studio.dismissed-contact-searches.v1';
const contactNoticeCacheStorageKey = 'cv-studio.contact-search-notices.v1';
function loadDismissedContactNotices() {
  try {
    const value = JSON.parse(localStorage.getItem(contactNoticeStorageKey) || '[]');
    return new Set(Array.isArray(value) ? value.filter(item => typeof item === 'string') : []);
  } catch { return new Set(); }
}
function loadContactNoticeCache() {
  try {
    const value = JSON.parse(localStorage.getItem(contactNoticeCacheStorageKey) || '[]');
    if (!Array.isArray(value)) return new Map();
    return new Map(value.filter(item => item && typeof item.id === 'string' && typeof item.roleId === 'string')
      .map(item => [item.id, {
        id: item.id,
        roleId: item.roleId,
        company: typeof item.company === 'string' ? item.company : 'Company not recorded',
        title: typeof item.title === 'string' ? item.title : 'Untitled role',
        status: typeof item.status === 'string' ? item.status : 'completed',
        summary: typeof item.summary === 'string' ? item.summary : '',
        searchCompleted: item.searchCompleted === true,
        time: Number.isFinite(item.time) ? item.time : 0,
      }]));
  } catch { return new Map(); }
}
function statusLabel(value) {
  const normalized = String(value || '').toLowerCase().replace(/[_\s-]+/g, ' ');
  if (/\b(?:held|wait|waiting|await|awaiting|missing|pending)\b/.test(normalized) && /\bsource\b/.test(normalized)) return 'Waiting for source';
  if (/\bheld\b|\bon hold\b/.test(normalized)) return 'Needs review';
  return rawStatusLabel(value);
}
/* ---- Top-bar quick links (redesign, Junie draft) ---- */

/**
 * Quick-Links icon buttons for the top bar.
 *
 * Exports:
 *   QUICK_LINKS  — array of 4 link descriptors
 *   renderQuickLinks() — returns the full inner HTML string for the `.topbar-links` pill
 */

// ── Brand SVG Icons (inline, no external requests) ──────────────────────────

/** LinkedIn "in" — simple-icons filled path (viewBox 0 0 24 24) */
const SVG_LINKEDIN =
  '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>';

/** Website: browser window with a globe */
const SVG_GLOBE =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19M12 2.5c2.6 2.7 3.9 5.9 3.9 9.5s-1.3 6.8-3.9 9.5c-2.6-2.7-3.9-5.9-3.9-9.5S9.4 5.2 12 2.5z"/><path d="M4.4 7.2h15.2M4.4 16.8h15.2" stroke-width="1.2" opacity=".6"/></svg>';

/** Check mark (copied confirmation) */
const SVG_CHECK =
  '<svg class="icon topbar-link-done" viewBox="0 0 24 24" fill="none"' +
    ' stroke="currentColor" stroke-width="1.6" stroke-linecap="round"' +
    ' stroke-linejoin="round" aria-hidden="true">' +
    '<path d="m5 12 4.5 4.5L19 7"/>' +
  '</svg>';

// ── Link Data ────────────────────────────────────────────────────────────────

const QUICK_LINKS = [
  {
    href:  'https://www.linkedin.com/',
    label: 'My LinkedIn',
    kind:  'linkedin',
    owner: 'me',
    svg:   SVG_LINKEDIN,
  },
  {
    href:  'https://dakshmehtaaa.github.io',
    label: 'My website',
    kind:  'site',
    owner: 'me',
    svg:   SVG_GLOBE,
  },
  {
    href:  'https://www.linkedin.com/company/146731866/',
    label: 'Honeybees LinkedIn',
    kind:  'linkedin',
    owner: 'bee',
    svg:   SVG_LINKEDIN,
  },
  {
    href:  'https://honeybees.consulting',
    label: 'Honeybees website',
    kind:  'site',
    owner: 'bee',
    svg:   SVG_GLOBE,
  },
];

// ── Owner badge (bee hexagon) ────────────────────────────────────────────────

function ownerBadge(owner) {
  if (owner === 'bee') return '<i class="topbar-link-bee"></i>';
  return '';
}

// ── Render ───────────────────────────────────────────────────────────────────

function renderQuickLinks() {
  const buttons = QUICK_LINKS.map((entry, i) => {
    const bee = ownerBadge(entry.owner);

    // Insert a thin separator before the first "site" button (index 2)
    const sep = i === 2 ? '<i class="topbar-sep"></i>' : '';

    return (
      sep +
      '<button type="button" class="topbar-link" data-action="copy-link"' +
      ` data-url="${entry.href}" data-label="${entry.label}" data-kind="${entry.kind}" data-owner="${entry.owner}"` +
      ` title="Copy: ${entry.label}" aria-label="Copy link: ${entry.label}">` +
      '  <span class="topbar-link-icon">' +
      entry.svg +
      SVG_CHECK +
      bee +
      '  </span>' +
      '</button>'
    );
  });

  return buttons.join('');
}

const sourceMarks = { hellowork: 'hw', france_travail: 'ft', linkedin: 'in', apec: 'a', welcome_to_the_jungle: 'w', wttj: 'w', indeed: 'i', mail: '@' };
const runningStatuses = new Set(['queued', 'running', 'waiting_input', 'stopping']);
const state = {
  route: routeFromHash(), overview: null, roles: [], runs: [], documents: [], companyLogos: [], companyLogosLoaded: false, settings: { theme: 'system', density: 'comfortable', refresh_seconds: 15 },
  errors: {}, loaded: false, refreshing: false, stage: null, query: '', filter: 'all', sort: 'newest', activityQuery: '', activityFilter: 'all',
  selectedRun: null, runDetail: null, runLoading: false, doc: null, draft: '', docMode: 'split', docLoading: false, docError: '', saving: false,
  jobs: [], job: null, selectedJob: null, timer: null, navigation: null, dialogRole: null, activeArtifact: null,
  documentSequence: 0, showAllLogs: false,
  applyQuery: '', applyFilter: 'ready', applicationSaving: new Set(), applicationEpoch: 0, applicationUpdates: new Map(), stopTarget: null,
  applyLanguage: 'all', applyDocuments: 'all', applyFit: 'all', applyContract: 'all', applySource: 'all', applySort: 'fit', applySelected: '', applyAdverts: new Map(), applyFiltersOpen: false, noticesOpen: false,
  roleActions: new Map(), contactJobs: new Map(), contactResults: new Map(), savedContacts: [], contactsIndexLoaded: false, contactsRoleFilter: '', contactsQuery: '', actionBusy: new Set(), dismissedContactNotices: loadDismissedContactNotices(), contactNoticeCache: loadContactNoticeCache(), themeSaving: false,
  manualJobs: [], manualJobsLoaded: false, jdInsights: null, homeTimeframe: savedColourHomePeriod(), jdCategory: 'all', jdTerm: '',
  pollingRoleJobs: new Set(), pipelineSections: new Set(), showStudioActivity: false,
  renderedRoute: null,
  applyFlow: {}, shortlist: {}, shortlistRemote: null, shortlistStage: 'all',
  jdInsightsLoading: false, jdInsightsRefreshedAt: null,
};
const app = document.querySelector('#app');
const detailDialog = document.querySelector('#detail-dialog');
const actionDialog = document.querySelector('#action-dialog');
document.addEventListener('error', event => {
  const image = event.target;
  if (image instanceof HTMLImageElement && image.hasAttribute('data-company-logo')) image.remove();
}, true);

function routeFromHash() { return Object.hasOwn(pages, location.hash.slice(1).split('?')[0]) ? location.hash.slice(1).split('?')[0] : 'overview'; }
function announce(message) { document.querySelector('#announcer').textContent = message; }
function toast(message, type = 'success', options = {}) {
  const node = document.createElement('div'); node.className = `toast toast-${type}`;
  const undo = typeof options.undo === 'function' ? `<button type="button" class="toast-undo">Undo</button>` : '';
  node.innerHTML = `${icon(type === 'error' ? 'alert' : 'check')}<span>${e(message)}</span>${undo}<button class="toast-dismiss" data-action="dismiss-toast" aria-label="Dismiss notification">${icon('close')}</button>`;
  const region = document.querySelector('#toast-region'); region.classList.toggle('has-undo', typeof options.undo === 'function'); region.replaceChildren(node);
  const life = options.undo ? 30000 : type === 'error' ? 8000 : 4000;
  const timer = setTimeout(() => node.remove(), life);
  node.querySelector('.toast-undo')?.addEventListener('click', () => {
    clearTimeout(timer); node.remove(); options.undo();
  }, { once: true });
}
function activePipeline() { return state.overview?.runtime?.active_pipeline || state.overview?.active_pipeline || { active: false }; }
function livePipeline() { return state.overview?.live_pipeline || null; }
function capabilities() { return state.overview?.runtime?.capabilities || state.overview?.capabilities || {}; }
function ownedWork() { return state.jobs.some(job => runningStatuses.has(job.status) || job.recovery_required); }
function checkDisabled() { const active = activePipeline(); return active.active || active.uncertain || ownedWork() || capabilities().can_check !== true; }
function editingLocked() { const active = activePipeline(); return Boolean(active.edit_locked || active.active || active.uncertain); }
function dirty() { return Boolean(state.doc && state.draft !== state.doc.content); }
function liveShown(live) { return Boolean(live && (live.active || live.stale || (live.finalized && live.admission_blocked))); }
function stages() { const live = currentRun(); return liveShown(live) ? (live.stages?.length ? live.stages : fallbackStages.map(stage => ({ ...stage, status: 'not_recorded' }))) : state.overview?.pipeline?.stages?.length ? state.overview.pipeline.stages : fallbackStages; }
function stageId(stage) { return String(stage.id || stage.label); }
function sourceList() { const live = livePipeline(); return liveShown(live) ? live.sources || [] : state.overview?.pipeline?.sources || []; }
function roleTitle(role) { return role.title || role.role || role.name || role.company || 'Untitled role'; }
function roleCompany(role) { return role.company || 'Company not recorded'; }
function companyKey(value) { return String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().replace(/[^a-z0-9]+/g, ''); }
function companyLogoFor(company, roleId) {
  const logos = state.companyLogos;
  if (!Array.isArray(logos)) return null;
  if (typeof roleId === 'string' && roleId) {
    const linked = logos.find(item => Array.isArray(item.role_ids) && item.role_ids.includes(roleId));
    if (linked) return linked;
  }
  const key = companyKey(company);
  if (!key) return null;
  return logos.find(item => [item.company,...(Array.isArray(item.aliases) ? item.aliases : [])].some(alias => companyKey(alias) === key)) || null;
}
// 23 Oct: companies without a logo get an industry icon (Studio/company_icons.py) on its colour,
// or their initials on a colour picked from the name, so the Apply list stays easy to scan.
const industryIcons = {
  defense: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/>',
  luxury: '<path d="M6 4h12l3.5 5L12 20 2.5 9z"/><path d="M2.5 9h19M9 4l3 5 3-5M8 9l4 11 4-11"/>',
  fmcg: '<path d="M5.5 8h13l-1 12.5h-11z"/><path d="M9 8a3 3 0 0 1 6 0"/>',
  energy: '<path d="M13 2.5 5 13.5h6l-1 8 8.5-11.5h-6z"/>',
  finance: '<path d="M3 9.5 12 4l9 5.5M5 10v7.5M9.5 10v7.5M14.5 10v7.5M19 10v7.5M3 20.5h18"/>',
  consulting: '<rect x="3" y="7.5" width="18" height="12.5" rx="2"/><path d="M9 7.5V5.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5.5v2M3 13h18"/>',
  logistics: '<path d="M2.5 6.5h11v10h-11zM13.5 10h4.5l3.5 3.5v3h-8"/><circle cx="7" cy="18" r="1.8"/><circle cx="17.5" cy="18" r="1.8"/>',
  tech: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  public: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>',
  climate: '<path d="M5 19.5C5 10.5 11 5 20 4.5c0 9-5.5 15-15 15z"/><path d="M5 19.5l7.5-7.5"/>',
  construction: '<path d="M4 21V9l8-5 8 5v12"/><path d="M9.5 21v-6h5v6M3 21h18"/>',
  health: '<path d="M9.5 3.5h5v6h6v5h-6v6h-5v-6h-6v-5h6z"/>',
  industry: '<path d="M3 20.5v-9l5 3v-3l5 3V6.5h4l1 3h3v11z"/>',
  media: '<path d="M3.5 10v4h3.5l6.5 4.5v-13L7 10z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11"/>',
};
let companyIcons = {};
fetch('/static/company-icons.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : {}).then(data => { companyIcons = data || {}; if (state.loaded) render(); }).catch(() => {});
function companyAvatar(company, roleId = '', variation = 0) {
  const label0 = company || 'Company not recorded';
  if (!companyLogoFor(label0, roleId)) {
    const known = companyIcons[companyKey(label0)];
    const hue = known?.hue ?? [...companyKey(label0)].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 7);
    const glyph = known?.industry && industryIcons[known.industry];
    return `<span class="company-monogram company-avatar company-ind${glyph ? ' has-glyph' : ''}" style="--hue:${hue}" title="${e(label0)}${known?.industry ? ` · ${e(known.industry)}` : ''}" aria-hidden="true">${glyph ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${glyph}</svg>` : `<span class="company-avatar-fallback">${e(initials(label0))}</span>`}</span>`;
  }
  const label = company || 'Company not recorded';
  const logo = companyLogoFor(label, roleId);
  const id = typeof logo?.id === 'string' && /^[a-f0-9]{20}$/.test(logo.id) ? logo.id : '';
  const darkBackdrop = logo?.render_hint === 'light_ink/use_dark_background';
  return `<span class="company-monogram company-avatar monogram-${Math.abs(Number(variation) || 0) % 4}${darkBackdrop ? ' company-avatar-dark-backdrop' : ''}" title="${e(label)}" aria-hidden="true"><span class="company-avatar-fallback">${e(initials(label))}</span>${id ? `<img data-company-logo src="/api/company-logos/${id}" alt="" loading="lazy" decoding="async">` : ''}</span>`;
}
function roleArtifacts(role) { return role.artifacts || []; }
function runTitle(run) { return run.label || run.summary || run.id || 'Recorded run'; }
function runDate(run) { return run.started_at || run.date || run.updated_at; }
function roleDate(role) { return role.updated_at || role.created_at; }
function shortRoleStatus(role) { return role.status === 'artifact_present' ? 'CV available' : role.status === 'source_only' ? 'Source saved' : statusLabel(role.status); }
function roleBadge(role) { return badge(role.status === 'artifact_present' ? 'ready' : role.status, shortRoleStatus(role)); }
function jobStatus(job) { return job.status === 'agent_finished' ? 'Coordinator finished' : statusLabel(job.status); }
function selectedStage() { return stages().find(stage => stageId(stage) === state.stage) || stages().find(stage => stageId(stage) === livePipeline()?.current_stage) || stages()[0]; }
function button(label, action, symbol = '', className = 'button-secondary', attrs = '') { return `<button class="button ${className}" data-action="${e(action)}" ${attrs}>${symbol ? icon(symbol) : ''}${e(label)}</button>`; }
function navLink(id, label = pages[id].name, extra = '') { return `<a href="#${id}" data-nav="${id}" class="text-link ${extra}">${e(label)}${icon('arrow')}</a>`; }

// 23 Oct redesign: daily tools on top, rarely used pages folded under "More".
function navItem(id) {
  const count = id === 'apply' ? '<span class="nav-count" id="apply-count" aria-label="Ready to apply">0</span>' : id === 'shortlist' ? `<span class="nav-count" id="shortlist-count" aria-label="Shortlisted jobs">${Object.keys(state.shortlist).length}</span>` : '';
  const on = state.route === id || (id === 'shortlist' && state.route === 'contacts');
  return `<a href="#${id}" data-nav="${id}" class="nav-item ${on ? 'active' : ''}" ${on ? 'aria-current="page"' : ''}>${icon(pages[id].icon)}<span>${pages[id].name}</span>${count}</a>`;
}
function renderShell() {
  app.innerHTML = `<div class="studio-shell"><div class="mobile-shade" data-action="close-nav"></div>
    <aside class="sidebar" aria-label="Main navigation">
  <a class="brand" href="#overview" data-nav="overview" aria-label="CV Studio home"><span class="brand-symbol" aria-hidden="true"><img src="/static/assets/studio-mark.svg?v=2" alt="" width="38" height="38"></span><span>CV <em>Studio</em></span></a>
      <nav class="main-nav">${['overview', 'apply', 'shortlist', 'audits'].map(id => navItem(id)).join('')}</nav>
      <details class="nav-more" ${['library', 'prospect', 'workspace', 'activity', 'settings'].includes(state.route) ? 'open' : ''}><summary class="nav-more-toggle">${icon('more')}<span>More</span>${icon('chevron')}</summary><nav class="secondary-nav" aria-label="Less often used">${['library', 'prospect', 'workspace', 'activity', 'settings'].map(id => navItem(id)).join('')}</nav></details>
      <div class="sidebar-bottom"><div class="profile-chip"><span class="avatar">DM</span><div><strong>the candidate</strong></div></div></div>
    </aside>
    <div class="main-shell"><header class="topbar"><div class="breadcrumb">${button('Open navigation', 'toggle-nav', 'menu', 'button-icon mobile-menu')}<span>My workspace</span>${icon('chevron')}<strong id="breadcrumb-current">${pages[state.route].name}</strong></div><div class="topbar-actions"><nav class="topbar-links" aria-label="Quick links">${renderQuickLinks()}</nav><span class="topbar-date">${date(new Date().toISOString(), { weekday: 'short', year: undefined })}</span><button class="quick-search" data-action="command" aria-label="Quick navigation, Command K">${icon('search')}<span>Find anything</span><kbd>⌘ K</kbd></button><button class="icon-button notice-bell" data-action="toggle-notices" aria-expanded="false" aria-controls="contact-search-notices" aria-label="Contact search updates" title="Contact search updates">${icon('bell')}<span class="notice-dot" hidden></span></button><button class="icon-button theme-toggle" data-action="toggle-theme" aria-pressed="false" aria-label="Switch to dark mode" title="Switch to dark mode"></button><button class="icon-button refresh-button" data-action="refresh" title="Refresh workspace" aria-label="Refresh workspace">${icon('refresh')}</button></div></header>
    <main id="main" tabindex="-1">${loading()}</main><footer class="page-footer"><span><span class="footer-dot"></span>CV Studio</span><span id="sync-time">Loading…</span></footer><aside id="contact-search-notices" class="contact-search-notices" role="region" aria-label="Contact search updates"></aside></div></div>`;
  setNavigation(false);
}
function setNavigation(open, focus = false) {
  const shell = document.querySelector('.studio-shell'); if (!shell) return;
  const mobile = matchMedia('(max-width: 620px)').matches;
  shell.classList.toggle('nav-open', mobile && open);
  const sidebar = document.querySelector('.sidebar'); sidebar.inert = mobile && !open;
  document.querySelector('.main-shell').inert = mobile && open;
  const trigger = document.querySelector('[data-action="toggle-nav"]'); trigger.setAttribute('aria-expanded', String(mobile && open)); trigger.setAttribute('aria-controls', 'studio-navigation'); sidebar.id = 'studio-navigation';
  if (focus && mobile) (open ? sidebar.querySelector('.nav-item.active') : trigger)?.focus();
}

function applySettings(settings = state.settings) {
  const theme = ['light', 'dark', 'system'].includes(settings.theme) ? settings.theme : 'system';
  const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.density = settings.density === 'compact' ? 'compact' : 'comfortable';
  document.querySelector('#theme-color-meta')?.setAttribute('content', dark ? '#000000' : '#f6f9fc');  // 26 Oct (Daksh): match page background, no coloured band
  updateThemeControl();
}
function updateThemeControl() {
  const control = document.querySelector('.theme-toggle');
  if (!control) return;
  const dark = document.documentElement.dataset.theme === 'dark';
  const target = dark ? 'light' : 'dark';
  control.innerHTML = icon(dark ? 'sun' : 'moon');
  control.setAttribute('aria-pressed', String(dark));
  control.setAttribute('aria-label', 'Switch to ' + target + ' mode');
  control.title = 'Switch to ' + target + ' mode';
  control.disabled = Boolean(state.themeSaving);
}
async function toggleTheme() {
  if (state.themeSaving) return;
  const previous = state.settings;
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  state.themeSaving = true;
  state.settings = { ...state.settings, theme };
  applySettings(state.settings);
  try {
    state.settings = await api.saveSettings({
      theme,
      density: state.settings.density,
      refresh_seconds: state.settings.refresh_seconds,
    });
    applySettings(state.settings);
    toast((theme === 'dark' ? 'Dark' : 'Light') + ' mode saved.');
  } catch (error) {
    state.settings = previous;
    applySettings(state.settings);
    toast('Theme could not be saved. ' + error.message, 'error');
  } finally {
    state.themeSaving = false;
    updateThemeControl();
  }
}
function updateShell() {
  document.title = `${pages[state.route].name} · CV Studio`;
  document.querySelector('#breadcrumb-current').textContent = pages[state.route].name;
  document.querySelectorAll('.nav-item').forEach(node => { const active = node.dataset.nav === state.route || (node.dataset.nav === 'shortlist' && state.route === 'contacts'); node.classList.toggle('active', active); active ? node.setAttribute('aria-current', 'page') : node.removeAttribute('aria-current'); });
  const applyCount = document.querySelector('#apply-count');
  if (applyCount) {
    const count = state.roles.filter(role => applicationOf(role).status === 'ready').length;
    applyCount.textContent = formatNumber(count);
    applyCount.setAttribute('aria-label', `${count} ${count === 1 ? 'job' : 'jobs'} ready to apply`);
  }
  document.querySelector('#sync-time').textContent = state.overview?.generated_at ? '' : 'Local files · no snapshot yet';
  document.querySelector('.refresh-button').classList.toggle('is-refreshing', state.refreshing);
}
function rerenderHomePreservingInput(input) {
  const selector = input?.id ? `#${CSS.escape(input.id)}` : '[data-jd-term]';
  const start = typeof input?.selectionStart === 'number' ? input.selectionStart : null;
  const end = typeof input?.selectionEnd === 'number' ? input.selectionEnd : null;
  render();
  const next = document.querySelector(selector);
  next?.focus({preventScroll:true});
  if (start !== null && typeof next?.setSelectionRange === 'function') next.setSelectionRange(start,end);
}
function heading(actions = '') {
  const page = pages[state.route];
  return `<div class="page-heading"><h1>${page.title}</h1>${actions ? `<div class="heading-actions">${actions}</div>` : ''}</div>`;
}
function observedBanner() { return ''; }
function elapsedLabel(startedAt) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return 'Start time not recorded';
  return `${spanLabel(seconds)} elapsed`;
}
function spanLabel(seconds) {
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
function clockTime(value) { return date(value, { hour: '2-digit', minute: '2-digit', day: undefined, month: undefined, year: undefined }); }
function deadlineLabel(live) {
  if (!live.batch_deadline_at || live.finalized) return '';
  const left = Math.floor((new Date(live.batch_deadline_at).getTime() - Date.now()) / 1000);
  if (!Number.isFinite(left)) return '';
  return left >= 0 ? `<span class="run-deadline">Deadline ${e(clockTime(live.batch_deadline_at))} · ${e(spanLabel(left))} left</span>`
    : `<span class="run-deadline is-overdue">${icon('alert')}Past deadline by ${e(spanLabel(-left))}</span>`;
}
function currentRun() {
  const live = livePipeline();
  if (liveShown(live)) return live;
  const job = state.jobs.find(item => item.kind === 'pipeline' && runningStatuses.has(item.status));
  return job ? { active:true, status:job.status, job_id:job.id, title:job.label, started_at:job.started_at, last_activity_at:job.logs?.at(-1)?.at || job.started_at, current_stage:job.current_stage || null, summary:job.summary, stages:fallbackStages.map(stage => ({...stage,status:stage.id === job.current_stage ? 'running' : 'not_recorded'})) } : null;
}
function runCardState(live) {
  const current = stages().find(stage => stageId(stage) === live.current_stage);
  const position = Number(live.stage_position) || (current ? stages().indexOf(current) + 1 : 0);
  const running = live.active && !live.stale && !live.finalized;
  const stop = state.overview?.runtime?.external_stops?.find(receipt => receipt.thread_id === live.thread_id);
  const stopping = live.status === 'stopping' || stop?.action_sent;
  const finished = Boolean(live.finalized);
  const eyebrow = stopping ? 'STOP REQUESTED' : finished ? `RUN FINISHED · ${statusLabel(live.run_raw_status || live.run_status || 'recorded').toUpperCase()}` : `${running ? 'PIPELINE RUNNING' : 'UPDATES PAUSED'}${position ? ` · STEP ${position} OF ${stages().length}` : ''}`;
  const title = finished ? (live.status === 'follow_up' ? (live.manual_stage ? `Manual ${live.manual_stage.stage === 'build' ? 'CV build' : statusLabel(live.manual_stage.stage).toLowerCase()} running${live.manual_stage.role ? ` · ${live.manual_stage.role}` : ''}` : 'Follow-up chat open') : live.status === 'left_open' ? `${live.left_open_workers || 'Some'} chat${live.left_open_workers === 1 ? '' : 's'} left open` : 'Run finished') : current?.label || (live.job_id ? 'Starting automation' : 'Pipeline activity');
  const startedAt = live.batch_started_at || live.started_at;
  const timing = finished ? `Finished ${clockTime(live.finished_at)}${live.batch_started_at && live.finished_at ? ` · ran ${spanLabel(Math.max(0, (new Date(live.finished_at) - new Date(live.batch_started_at)) / 1000))}` : ''}` : elapsedLabel(startedAt);
  const canStop = live.active || (finished && live.admission_blocked && !live.job_id);
  const stopButton = canStop ? button(stopping ? 'Stopping…' : finished ? 'Stop open chats' : 'Stop run', 'stop-run', 'stop', 'button-danger button-small', stopping ? 'disabled' : '') : '';
  return { current, position, running, stop, stopping, finished, eyebrow, title, timing, stopButton };
}
function activeRunCard() {
  if (state.errors.overview) return '';
  const observed = currentRun();
  const observation = activePipeline();
  const live = observed || (observation.active && !observation.uncertain
    ? { active: true, status: 'running', started_at: observation.started_at, current_stage: observation.current_stage }
    : null);
  const active = Boolean(live && live.active && !live.finalized);
  const stale = Boolean(live?.stale);
  const blocked = Boolean(live?.finalized && live.admission_blocked);
  if (!active && !stale && !blocked) return '';
  const card = runCardState(live);
  const stepDots = stages().map((stage, index) => {
    const status = stage.status || 'not_recorded';
    const current = stageId(stage) === live.current_stage;
    return `<span class="step-dot step-${e(status)} ${current ? 'is-current' : ''}" title="${e(stage.label || stage.id)} · ${e(statusLabel(status))}" aria-hidden="true"></span>`;
  }).join('');
  const statusLabelText = card.stopping ? 'Stop requested' : blocked ? 'Follow-up needs attention' : card.eyebrow;
  return `<section class="active-run-bar ${card.finished ? 'is-finished' : ''} ${stale ? 'is-stale' : ''}" role="status" aria-label="${e(statusLabelText)}">
    <div class="active-run-bar-main"><p class="eyebrow"><span class="live-indicator ${card.finished ? 'is-finished' : ''}"></span>${e(statusLabelText)}</p><strong>${e(card.title)}</strong></div>
    <div class="active-run-bar-steps" aria-label="Pipeline stage progress">${stepDots}</div>
    <div class="active-run-bar-meta"><span>${e(card.timing)}</span>${card.position ? `<span>Step ${card.position} of ${stages().length}</span>` : ''}</div>
    <div class="active-run-actions">${card.stopButton}<a class="text-link" href="#activity" data-nav="activity">${icon('clock')}History</a></div>
  </section>`;
}
function stopRecoveryNotice() {
  const receipt = state.overview?.runtime?.external_stops?.find(item => item.recovery_required);
  return receipt ? `<div class="stop-recovery-note" role="status">${icon('alert')}<span>${e(receipt.summary || 'Cleanup needs review before starting another run.')}</span></div>` : '';
}

const routineLogs = new Set(['Using tools.', 'Assistant progress updated.']);
const workerOrder = { running: 0, left_open: 1, stale: 1, failed: 2, interrupted: 2, completed: 3 };
function groupedLogs(logs) {
  const shown = state.showAllLogs ? logs : logs.filter(log => !routineLogs.has(log.message));
  const groups = [];
  for (const log of shown) {
    const previous = groups.at(-1);
    if (previous && previous.label === log.label && previous.message === log.message) { previous.count += 1; previous.at = log.at; }
    else groups.push({ ...log, count: 1 });
  }
  return groups.reverse();
}
function workerStatusLabel(worker) { return worker.status === 'left_open' ? 'Left open' : worker.status === 'stale' ? 'Quiet' : statusLabel(worker.status || 'not_recorded'); }
function externalActivity() {
  const live = livePipeline();
  if (!live?.thread_id) return '';
  const workers = [...(live.workers || [])].sort((a, b) => (workerOrder[a.status] ?? 2) - (workerOrder[b.status] ?? 2));
  const logs = live.logs || [];
  const groups = groupedLogs(logs);
  const hidden = logs.length - logs.filter(log => !routineLogs.has(log.message)).length;
  const summaryBadge = live.finalized ? badge(live.status === 'finished' ? 'done' : 'warning', live.status === 'follow_up' ? 'Follow-up chat open' : live.status === 'left_open' ? 'Chats left open' : 'Finished')
    : badge(live.active && !live.stale ? 'running' : live.status, live.active && !live.stale ? 'Running' : live.stale ? 'Updates paused' : live.status === 'completed' ? 'Coordinator turn finished' : statusLabel(live.status));
  return `<section class="panel external-activity" id="external-activity"><div class="section-heading"><div><h2>${live.finalized ? 'Last run' : 'Live activity'}</h2></div><span class="live-update-label">${icon('refresh')}Updates every 5 seconds</span></div><div class="external-run-summary"><div>${summaryBadge}<strong>${e(live.title || 'CV Weekday Pipeline')}</strong>${live.run_id ? `<span class="run-id-chip">${e(live.run_id)}</span>` : ''}</div><span>Observed ${e(date(live.observed_at, { hour: '2-digit', minute: '2-digit', second: '2-digit', year: undefined }))}</span></div>${live.finalized ? `<div class="inline-notice">${icon('info')}${e(live.summary)}</div>` : live.stale ? `<div class="inline-notice">${icon('info')}Showing the last observed update.</div>` : ''}${workers.length ? `<div class="live-worker-grid">${workers.map(worker => `<article class="live-worker worker-${e(worker.status || 'unknown')}"><div><strong>${e(worker.label || worker.lane || worker.id)}</strong>${badge(worker.status === 'left_open' ? 'warning' : worker.status || 'not_recorded', workerStatusLabel(worker))}</div><p>${e(worker.model || 'Model not recorded')}</p><div class="live-worker-count">${countOf(worker) !== null ? `<strong>${e(countOf(worker))}</strong><span>${worker.screened != null && worker.target ? ` / ${e(worker.target)}` : ''} ${e(worker.count_label || (worker.screened == null ? 'saved captures' : 'full ads'))}</span>` : '<span>No count recorded</span>'}</div><small>Latest activity ${e(relativeDate(worker.last_activity_at))}</small></article>`).join('')}</div>` : ''}<div class="log-toolbar"><span>${icon('terminal')}RECENT UPDATES · NEWEST FIRST</span><span>${hidden ? `<button class="text-link log-toggle" data-action="toggle-logs">${state.showAllLogs ? 'Hide routine updates' : `Show ${hidden} routine update${hidden === 1 ? '' : 's'}`}</button>` : `${logs.length} ${logs.length === 1 ? 'update' : 'updates'}`}</span></div><div class="log-output external-log-output" tabindex="0" aria-label="Existing pipeline updates">${groups.length ? groups.slice(0, 40).map(log => `<div class="log-line log-${e(log.level || 'info')}"><time>${e(date(log.at, { hour: '2-digit', minute: '2-digit', second: '2-digit', day: undefined, month: undefined, year: undefined }))}</time><pre>${log.label ? `<strong>${e(log.label)}</strong>${log.count > 1 ? ` <span class="log-count">×${log.count}</span>` : ''}\n` : ''}${e(log.message || '')}</pre></div>`).join('') : '<p class="log-placeholder">Only routine tool activity so far.</p>'}</div></section>`;
}
function render() {
  // Never wipe a shortlist note while it is being typed; the next refresh catches up.
  if (state.route === 'shortlist' && state.renderedRoute === 'shortlist' && document.activeElement?.matches?.('textarea[data-shortlist-note]')) return;
  updateShell();
  updateContactNotifications();
  const main = document.querySelector('#main');
  if (state.route !== 'overview') unmountColourHome();
  // Keep the live frame attached during every same-route refresh. Reattaching
  // an iframe resets its document, including unsaved Prospector work.
  if (state.route === 'prospect') {
    mountProspect(main);
    state.renderedRoute = state.route;
    return;
  }
  if (!state.loaded) { unmountColourHome(); replaceStudioContent(main, loading()); return; }
  if (state.errors.overview && !state.overview) { unmountColourHome(); replaceStudioContent(main, heading() + errorState(state.errors.overview)); return; }
  if (state.route === 'shortlist' || state.route === 'contacts') {  // 5 Nov: Outreach Desk (outreach.js) replaces Shortlist + Contact library
    unmountColourHome(); mountOutreach(main, { roles: state.roles, shortlist: state.shortlist }); state.renderedRoute = state.route; return;
  }
  const views = { overview: renderHomeBetaPage, apply: renderApply, shortlist: renderShortlist, library: renderLibrary, contacts: renderContacts, activity: renderActivity, workspace: renderWorkspace, settings: renderSettings, audits: renderAuditsPage };
  const samePage = state.renderedRoute === state.route;
  const colourHome = state.route === 'overview' && main.querySelector('#colour-home');
  if (samePage && colourHome) {
    colourHome.closest('.page')?.classList.remove('is-entering');
    refreshLiveHeader();
    updateColourOverview(colourHome);
    return;
  }
  unmountColourHome();
  const focused = main.contains(document.activeElement) ? document.activeElement : null;
  const focusAttribute = focused && ['id', 'data-home-timeframe', 'data-jd-category', 'data-jd-term'].find(name => focused.hasAttribute(name));
  const focusSelector = focusAttribute ? `[${focusAttribute}="${CSS.escape(focused.getAttribute(focusAttribute))}"]` : null;
  const scrollPositions = samePage ? [...main.querySelectorAll('.db-filter, .db-category-tabs, .db-jd-detail')].map(node => [node.className, node.scrollLeft, node.scrollTop]) : [];
  replaceStudioContent(main, `<div class="page page-${state.route}${samePage ? '' : ' is-entering'}">${state.errors.overview ? `<div class="stale-notice" role="status">${icon('info')}<span>The latest refresh failed. This view still shows the last available snapshot.</span>${button('Retry', 'refresh', 'refresh', 'button-ghost button-small')}</div>` : ''}${activeRunCard()}${stopRecoveryNotice()}${views[state.route]()}</div>`);
  state.renderedRoute = state.route;
  if (state.route === 'overview') updateColourOverview(main.querySelector('#colour-home'));
  for (const [className, left, top] of scrollPositions) {
    const node = [...main.querySelectorAll('.db-filter, .db-category-tabs, .db-jd-detail')].find(item => item.className === className);
    if (node) { node.scrollLeft = left; node.scrollTop = top; }
  }
  if (samePage && focusSelector) main.querySelector(focusSelector)?.focus({ preventScroll: true });
  if (state.route === 'workspace') bindEditor();
  if (state.route === 'overview') bindHomeNext(main, (section, value) => {
    // The Results toggle is the master: it pushes its period to every card, which can then be changed one by one.
    const next = section === 'results' ? Object.fromEntries(Object.keys(DEFAULT_SECTIONS).map(id => [id, value]))
      : { ...(state.homeNextSections || DEFAULT_SECTIONS), [section]: value };
    state.homeNextSections = next; state.renderedRoute = null; render();
  }, key => { state.homeNextSeries = { ...(state.homeNextSeries || {}), [key]: (state.homeNextSeries || {})[key] === false }; state.renderedRoute = null; render(); },
  weeks => { state.homeNextWeek = weeks; state.renderedRoute = null; render(); });
}
// Top bar: animated strip + chip while Mercury 3 is building a queued CV (22 Oct, Daksh).
function updateBuildingBar() {
  const bar = document.querySelector('header.topbar'); if (!bar) return;
  const live = (state.jobs || []).filter(job => job.kind === 'manual_build' && ['queued','running'].includes(String(job.status || '').toLowerCase()));
  let chip = bar.querySelector('.build-chip');
  bar.classList.toggle('is-building', live.length > 0);
  if (!live.length) { chip?.remove(); return; }
  const first = live[0];
  const label = 'Mercury 3 is building' + (live.length > 1 ? ' ' + live.length + ' CVs' : ': ' + (first.manual_company || first.manual_title || 'a queued CV'));
  if (!chip) { bar.querySelector('.topbar-actions')?.insertAdjacentHTML('afterbegin', '<span class="build-chip" role="status"><i class="build-dot" aria-hidden="true"></i><span></span></span>'); chip = bar.querySelector('.build-chip'); }
  const text = chip?.querySelector('span'); if (text && text.textContent !== label) text.textContent = label;
}
function refreshLiveHeader() {
  updateBuildingBar();
  const page = document.querySelector('#main > .page:not([hidden])');
  if (!page) return;
  const previous = page.querySelector(':scope > .active-run-bar');
  const content = activeRunCard();
  if (previous) { if (content) previous.outerHTML = content; else previous.remove(); }
  else if (content) page.insertAdjacentHTML('afterbegin', content);
  const previousRecovery = page.querySelector(':scope > .stop-recovery-note');
  const recovery = stopRecoveryNotice();
  if (previousRecovery) { if (recovery) previousRecovery.outerHTML = recovery; else previousRecovery.remove(); }
  else if (recovery) page.insertAdjacentHTML('afterbegin', recovery);
  let notice = page.querySelector(':scope > .stale-notice');
  if (state.errors.overview && !notice) page.insertAdjacentHTML('afterbegin', `<div class="stale-notice" role="status">${icon('info')}Live updates unavailable. Showing the last update.${button('Retry','refresh','refresh','button-ghost button-small')}</div>`);
  else if (!state.errors.overview && notice) notice.remove();
}

function applicationOf(role) {
  return role.application || { status: role.collection === 'applied' ? 'applied' : role.collection === 'inactive' ? 'inactive' : role.cv_available ? 'ready' : 'pending', apply_url: safeUrl(role.source_url || role.url), revision: null };
}
const applicationLabels = { ready: 'To apply', applied: 'Applied', inactive: 'Inactive', pending: 'Review first', saved: 'Saved for later', contacting: 'Contact to find', interviewing: 'Interviewing', offer: 'Offer', rejected: 'Rejected', withdrawn: "Won't apply" };
const applicationStatusFilterLabels = {
  submitted: 'Applied at any stage', replied: 'Got a reply', progressed: 'Interview or offer',
};
const submittedStatuses = new Set(['applied', 'interviewing', 'offer', 'rejected', 'withdrawn']);
const repliedStatuses = new Set(['interviewing', 'offer', 'rejected']);
const progressedStatuses = new Set(['interviewing', 'offer']);
function hasApplicationStatus(role, status) {
  const record = applicationOf(role);
  return record.status === status || (Array.isArray(record.status_history) && record.status_history.some(event => event?.status === status));
}
function isSubmitted(role) {
  const record = applicationOf(role);
  return submittedStatuses.has(record.status) || record.canonical_applied === true || Boolean(record.applied_at)
    || (Array.isArray(record.status_history) && record.status_history.some(event => event?.status === 'applied'));
}
// 23 Oct (Daksh): a Build-now CV waiting for approval sits in "To apply", marked "To be approved".
function approvalItemFor(role) {
  if (!role) return null;
  return state.manualJobs.find(item => String(item.status || '').toLowerCase() === 'built_pending_approval'
    && item.role_path && [role.folder, role.document_folder, role.path].includes(item.role_path)) || null;
}
function approvalBar(role) {
  const item = approvalItemFor(role); if (!item) return '';
  return `<div class="approve-bar"><p><strong>To be approved</strong><span>Check the CV, then approve it or ask Mercury for changes.</span></p><div class="approve-bar-actions">${button('View CV', 'view-application-cv', 'file', 'button-secondary button-small', `data-role-id="${e(role.id)}"`)}${button('Ask Mercury for edits', 'chat-mercury', 'sparkle', 'button-secondary button-small', `data-role-id="${e(role.id)}"`)}${button('Approve', 'approve-manual-job', 'check', 'button-primary button-small', `data-manual-job-id="${e(item.id)}"`)}</div></div>`;
}
function applicationStatusMatches(role, filter) {
  if (filter === 'all') return true;
  if (filter === 'ready' && approvalItemFor(role)) return true;
  const status = applicationOf(role).status;
  if (filter === 'submitted') return isSubmitted(role);
  if (filter === 'replied') return repliedStatuses.has(status);
  if (filter === 'progressed') return progressedStatuses.has(status);
  return status === filter;
}
function validFitScore(role) {
  const raw = role.ratings?.profile_fit?.score ?? role.fit;
  const score = typeof raw === 'number' || (typeof raw === 'string' && raw.trim()) ? Number(raw) : NaN;
  return Number.isInteger(score) && score >= 1 && score <= 5 ? score : null;
}
// 26 Oct (Daksh): fit out of 100 with one decimal, from Studio/fit_score.py (offline model, no AI).
// Jobs the model cannot read fall back to the saved 1–5 rating × 20.
function fitPercent(role) {
  const s = role.fit_score?.score;
  if (typeof s === 'number' && s >= 0 && s <= 100) return s;
  const fit = validFitScore(role);
  return fit == null ? null : fit * 20;
}
function fitBand(pct) { return pct == null ? 'unknown' : Math.min(5, Math.max(1, Math.round(pct / 20))); }
function fitText(pct) { return pct == null ? '—' : pct.toFixed(1); }
function fitScoreMatches(score, filter) {
  if (filter === 'all' || filter.startsWith('exact:')) return true;
  if (filter === 'unknown') return score === null;
  if (filter === 'low') return score !== null && score < 50;
  return score !== null && score >= Number(filter);
}
function applicationCounts() {
  const counts = { ...Object.fromEntries(Object.keys(applicationLabels).map(key => [key, 0])), total: state.roles.length, submitted: 0, replied: 0, progressed: 0 };
  if (state.errors.roles) return Object.fromEntries(Object.keys(counts).map(key => [key, null]));
  for (const role of state.roles) {
    const status = applicationOf(role).status;
    if (status === 'ready' && applyIsSnoozed(role)) continue;
    counts[status in applicationLabels ? status : 'pending']++;
    if (status !== 'ready' && approvalItemFor(role)) counts.ready++;
    if (isSubmitted(role)) counts.submitted++;
    if (repliedStatuses.has(status)) counts.replied++;
    if (progressedStatuses.has(status)) counts.progressed++;
  }
  return counts;
}
function runDisabledReason() {
  const active = activePipeline(); const live = livePipeline();
  if (live?.finalized && live.admission_blocked) return live.summary;
  if (active.active || active.uncertain) return active.reason || 'A pipeline run is still active.';
  if (ownedWork()) return 'A Studio process is still running.';
  if (capabilities().can_run !== true) return capabilities().blocked_reason || 'Check connections before starting.';
  return '';
}
function runDisabledShort() {
  const active = activePipeline(); const live = livePipeline();
  if (live?.finalized && live.admission_blocked) return live.status === 'left_open' ? 'Paused until open chats close' : 'Paused while the pipeline chat is open';
  if (active.active || active.uncertain) return 'Paused while a run is active';
  if (ownedWork()) return 'Paused while a Studio process runs';
  return 'Check connections first';
}
function automationButton() { const reason = runDisabled() ? runDisabledReason() : ''; return `<span class="automation-start" ${reason ? `title="${e(reason)}"` : ''}>${button('Start automation', 'start-pipeline', 'play', 'button-primary', reason ? `disabled aria-describedby="start-reason"` : '')}${reason ? `<span id="start-reason" class="start-reason">${e(runDisabledShort())}<span class="sr-only">. ${e(reason)}</span></span>` : ''}</span>`; }
function runShortlisted(run) { return run.summary_metrics?.jobs_shortlisted ?? null; }
function runStageResults(run, stage) {
  const metrics = run.summary_metrics || {};
  const pieces = [];
  if (run.policy_version === 4 && stage.id === 'hunt') {
    if (stage.jobs_found != null) pieces.push(`${e(stage.jobs_found)} jobs found`);
    if (stage.jobs_proposed != null) pieces.push(`${e(stage.jobs_proposed)} proposals`);
    if (stage.ads_reviewed != null) pieces.push(`${e(stage.ads_reviewed)} ads reviewed`);
  } else if (run.policy_version === 4 && stage.id === 'hunt_audit') {
    if (stage.jobs_shortlisted != null) pieces.push(`${e(stage.jobs_shortlisted)} accepted roles`);
  } else if (run.policy_version === 4 && stage.id === 'sync') {
    if (stage.roles_synced != null) pieces.push(`${e(stage.roles_synced)} CV Studio roles verified`);
  } else {
    if (stage.jobs_found != null) pieces.push(`${e(stage.jobs_found)} jobs`);
    if (stage.cvs_built != null) pieces.push(`${e(stage.cvs_built)} CVs`);
    if (stage.ads_reviewed != null) pieces.push(`${e(stage.ads_reviewed)} ads reviewed`);
    if (stage.rows_synced != null) pieces.push(`${e(stage.rows_synced)} rows synced`);
  }
  return pieces.join(' · ') || 'Not recorded';
}
function runDuration(run) { return run.duration_seconds ?? run.timing?.duration_seconds ?? run.summary_metrics?.duration_seconds ?? null; }
function languageGroup(value) { const language = String(value || '').trim().toLowerCase(); return /\b(fr|french|français|francais)\b/.test(language) ? 'fr' : /\b(en|english|anglais)\b/.test(language) ? 'en' : 'unknown'; }
function advertSource(role) {
  let host = ''; try { host = new URL(role.source_url || applicationOf(role).apply_url || '').hostname.toLowerCase(); } catch { return 'unknown'; }
  const sources = {hellowork:['hellowork.com'],francetravail:['francetravail.fr','pole-emploi.fr'],linkedin:['linkedin.com'],indeed:['indeed.com','indeed.fr','indeed.co.uk','indeed.de','indeed.ca','indeed.ie','indeed.es','indeed.it','indeed.nl','indeed.be','indeed.ch','indeed.com.au','indeed.co.in','indeed.sg','indeed.com.br','indeed.jp','indeed.com.mx'],apec:['apec.fr'],welcometothejungle:['welcometothejungle.com']};
  return Object.entries(sources).find(([,domains]) => domains.some(domain => host === domain || host.endsWith(`.${domain}`)))?.[0] || 'unknown';
}
function outcomesChart() {
  const runs = state.runs.filter(run => run.kind !== 'legacy_stage').slice(0,7).reverse();
  const maximum = Math.max(1,...runs.flatMap(run => [runShortlisted(run) || 0,run.summary_metrics?.cvs_built || 0]));
  const midTick = maximum / 2;
  return `<section class="panel outcome-chart"><div class="section-heading"><div><h2>Recent run results</h2><p class="section-subtitle">Shortlisted jobs and CVs built per run</p></div>${navLink('activity','Run history')}</div><div class="outcome-legend"><span><i class="legend-shortlisted"></i>Shortlisted</span><span><i class="legend-built"></i>CVs built</span><span class="legend-unknown"><i class="legend-dash"></i>Unrecorded (—)</span></div>${runs.length ? `<div class="outcome-chart-wrap"><div class="outcome-grid-ticks" aria-hidden="true"><div class="tick-row"><span>${maximum}</span><div class="tick-line"></div></div><div class="tick-row"><span>${midTick}</span><div class="tick-line"></div></div><div class="tick-row tick-zero"><span>0</span><div class="tick-line"></div></div></div><div class="outcome-bars" role="group" aria-label="Recent run outcomes">${runs.map((run, idx) => { const shortlisted = runShortlisted(run); const built = run.summary_metrics?.cvs_built; const shortHeight = shortlisted == null ? 0 : shortlisted > 0 ? shortlisted/maximum*95 : 0; const builtHeight = built == null ? 0 : built > 0 ? built/maximum*95 : 0; return `<button data-select-run="${e(run.id)}" class="outcome-day" aria-label="${e(`${date(runDate(run))}: ${shortlisted ?? 'unrecorded'} shortlisted, ${built ?? 'unrecorded'} CVs built`)}"><div class="outcome-columns"><div class="${shortlisted == null ? 'is-unrecorded' : ''}"><span class="outcome-val">${e(shortlisted ?? '—')}</span><i style="height:${shortHeight}px" class="outcome-shortlisted ${shortlisted == null ? 'outcome-unknown-mark' : ''}"></i></div><div class="${built == null ? 'is-unrecorded' : ''}"><span class="outcome-val">${e(built ?? '—')}</span><i style="height:${builtHeight}px" class="outcome-built ${built == null ? 'outcome-unknown-mark' : ''}"></i></div></div><small class="outcome-run-label" title="${e(date(runDate(run),{year:undefined}))}">${e(date(runDate(run),{year:undefined}))}</small></button>`; }).join('')}</div></div><p class="chart-caption">— means no count recorded. Click a run to see its jobs.</p>` : '<p class="chart-caption">Run results appear when receipts are saved.</p>'}</section>`;
}
function languageChart() {
  if (state.errors.roles) return `<section class="panel language-panel"><div class="section-heading"><div><h2>Jobs by language</h2><p class="section-subtitle">Saved advert language</p></div></div><p class="muted analytics-empty">Language distribution is unavailable until job records load.</p></section>`;
  const groups = [['French',0],['English',0],['Other / not recorded',0]];
  state.roles.forEach(role => groups[{fr:0,en:1,unknown:2}[languageGroup(role.language)]][1]++);
  const total = groups.reduce((sum, [,count]) => sum + count, 0);
  let offset = 0;
  const arcs = groups.map(([,count], index) => {
    const size = total ? (count / total) * 100 : 0;
    if (!size) return '';
    const gap = groups.filter(([,c]) => c > 0).length > 1 ? 0.75 : 0;
    const slice = size - Math.min(gap, size * 0.08);
    const arc = `<circle class="language-arc language-arc-${index}" cx="50" cy="50" r="38" pathLength="100" stroke-dasharray="${slice} ${100 - slice}" stroke-dashoffset="${-offset}"/>`;
    offset += size;
    return arc;
  }).join('');
  return `<section class="panel language-panel"><div class="section-heading"><div><h2>Jobs by language</h2><p class="section-subtitle">Distribution across saved job descriptions</p></div>${navLink('apply','Filter jobs')}</div><div class="language-chart"><div class="language-donut-wrap"><svg viewBox="0 0 100 100" role="img" aria-label="${e(`Jobs by language: ${groups.map(([label,count]) => `${label}, ${count}, ${percentOrUnknown(total ? count/total*100 : null)}`).join('; ')}`)}"><circle class="language-arc-track" cx="50" cy="50" r="38"/>${arcs}</svg><div class="language-donut-center"><strong>${formatNumber(total)}</strong><span>JOBS TOTAL</span></div></div><div class="language-legend">${groups.map(([label,count],index) => `<button class="language-row" data-language-filter="${['fr','en','unknown'][index]}" title="Filter ${e(label)} jobs"><span class="language-row-left"><i class="language-swatch language-swatch-${index}"></i><span class="language-name">${e(label)}</span></span><strong class="language-row-right"><span class="language-count">${formatNumber(count)}</span><span class="language-pct-badge language-badge-${index}">${percentOrUnknown(total ? count/total*100 : null)}</span></strong></button>`).join('')}</div></div><p class="chart-caption">Based on saved advert language. Click a row to filter.</p></section>`;
}
function dashboardAnalytics() { return state.overview?.dashboard_analytics || {}; }
function numberOrUnknown(value) { return Number.isFinite(value) && value >= 0 ? formatNumber(value) : '—'; }
function shortlistCount(value, known, complete) {
  if (Number.isFinite(value) && value >= 0) return formatNumber(value);
  if (complete === false && Number.isFinite(known) && known > 0) return `${formatNumber(known)}+`;
  return '—';
}
function percentOrUnknown(value) { return Number.isFinite(value) && value >= 0 ? `${Math.round(value)}%` : '—'; }
function shortlistCompletenessNote(analytics) {
  return analytics.total_shortlisted_complete === false || analytics.shortlisted_companies_complete === false
    ? '<p class="analytics-note shortlist-completeness-note">“+” means a minimum supported by saved records; incomplete older history may add more.</p>'
    : '';
}
function shortlistSources() {
  const analytics = dashboardAnalytics();
  const sources = Array.isArray(analytics.sources) ? [...analytics.sources] : [];
  sources.sort((a,b) => (Number.isFinite(b.shortlisted) ? b.shortlisted : -1) - (Number.isFinite(a.shortlisted) ? a.shortlisted : -1));
  return sources.slice(0,5);
}
function sourceAnalytics() {
  const analytics = dashboardAnalytics();
  const sources = shortlistSources();
  const maxShortlisted = Math.max(1, ...sources.map(s => Number.isFinite(s.shortlisted) && s.shortlisted >= 0 ? s.shortlisted : 0));
  return `<section class="panel source-panel"><div class="section-heading"><div><h2>Shortlisted by source</h2><p class="section-subtitle">Shortlisted volume (bar) vs full ads read · rate</p></div>${navLink('activity','Run history')}</div>${sources.length ? `<div class="source-analytics-header"><span class="source-col-label">Source</span><span class="source-col-metrics"><span>Shortlisted / read</span><span>Rate</span></span></div><div class="source-analytics-list">${sources.map((source, sIdx) => { const count = Number.isFinite(source.shortlisted) && source.shortlisted >= 0 ? source.shortlisted : null; const barPct = count !== null ? Math.round((count / maxShortlisted) * 100) : 0; return `<div class="source-analytics-row source-row-${sIdx}"><strong title="${e(source.label || source.id || 'Source')}"><span class="source-label-text">${e(source.label || source.id || 'Source')}</span><span class="source-bar-track" title="Shortlist volume relative to top source"><span class="source-bar-fill source-fill-${sIdx}" style="width:${barPct}%"></span></span></strong><span class="source-counts" title="Shortlisted / unique full adverts read">${numberOrUnknown(source.shortlisted)} / ${numberOrUnknown(source.unique_jobs_read)}</span><span class="source-rate" title="Shortlist conversion rate">${percentOrUnknown(source.rate_pct)}</span></div>`; }).join('')}</div>` : '<p class="muted analytics-empty">Source totals appear when evidence is available.</p>'}${analytics.coverage_note ? `<p class="analytics-note">${e(analytics.coverage_note)}</p>` : ''}${analytics.missing_evidence?.length ? `<p class="analytics-note">Some source counts are not recorded; those values stay blank.</p>` : ''}<p class="analytics-note source-bar-note">Bar width compares shortlisted counts to top source; rates indicate shortlisted per full ad read.</p></section>`;
}
function shortlistRateIndicator(analytics) {
  if (!Number.isFinite(analytics.shortlist_rate_pct) || analytics.shortlist_rate_pct < 0) return '<p class="analytics-note tracker-rate-unknown">Rate is unavailable from recorded full-ad evidence.</p>';
  const rate = Math.min(100,analytics.shortlist_rate_pct);
  const label = percentOrUnknown(analytics.shortlist_rate_pct);
  return `<div class="tracker-progress" role="img" aria-label="Shortlist rate ${e(label)}"><div class="tracker-progress-labels"><span>Shortlist rate</span><strong>${e(label)}</strong></div><div class="tracker-progress-track"><span style="width:${rate}%"></span></div></div>`;
}
function locationAnalytics() {
  const locations = Array.isArray(dashboardAnalytics().locations)
    ? dashboardAnalytics().locations.filter(location => typeof location.label === 'string' && location.label.trim())
    : [];
  locations.sort((a,b) => (Number.isFinite(b.count) ? b.count : -1) - (Number.isFinite(a.count) ? a.count : -1));
  const topLocations = locations.slice(0,5);
  const maxCount = Math.max(1, ...topLocations.map(l => Number.isFinite(l.count) && l.count >= 0 ? l.count : 0));
  return `<section class="panel location-panel top-jd-locations" aria-labelledby="top-jd-locations-title"><div class="section-heading"><div><h2 id="top-jd-locations-title">Top JD office locations</h2><p class="section-subtitle">Saved office location labels, as recorded in the adverts</p></div></div>${topLocations.length ? `<ol class="top-jd-location-list">${topLocations.map((location, idx) => { const count = Number.isFinite(location.count) && location.count >= 0 ? location.count : null; const barPct = count !== null ? Math.round((count / maxCount) * 100) : 0; return `<li class="top-jd-location"><span class="top-jd-rank" aria-hidden="true">${String(idx + 1).padStart(2, '0')}</span><span class="top-jd-pin" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg></span><div class="top-jd-location-main"><strong class="location-name" title="${e(location.label)}">${e(location.label)}</strong><span class="top-jd-location-track" aria-hidden="true"><i style="width:${barPct}%"></i></span></div><strong class="location-count">${count === null ? '—' : formatNumber(count)}</strong></li>`; }).join('')}</ol>` : '<p class="muted analytics-empty">Office locations appear when saved advert evidence includes them.</p>'}<p class="analytics-note">Up to five saved locality labels. Bar length compares recorded counts; no place names are inferred.</p></section>`;
}
function countOf(item) { return item.screened ?? item.captured ?? item.count ?? null; }
function progressRows() {
  return sourceList().map(source => {
    const count = countOf(source);
    const captured = source.screened == null && count != null;
    const percent = count === null || !source.target ? 0 : Math.min(100, count / source.target * 100);
    return `<div class="home-progress-row"><div><strong>${e(source.label)}</strong><span>${count === null ? 'No count yet' : `${e(count)}${!captured && source.target ? ` / ${e(source.target)}` : ''} ${captured ? 'saved captures' : 'full ads'}`}</span></div><div class="home-progress-track ${captured ? 'captures' : ''}"><span style="width:${percent}%"></span></div></div>`;
  }).join('');
}
function renderOverview() {
  return '<div id="colour-home" aria-label="Home KPI dashboard"></div>';
}
const homeModelCache = { key: null, model: null };
function renderHomeBetaPage() {
  const run = currentRun();
  const running = Boolean(run && run.active && !run.stale && !run.finalized);
  // Period toggles only change the view: rebuild the (slow) model only when the data itself changed.
  const ids = periodIds(state.homeNextSections);
  const key = [state.overview, state.roles, state.runs, state.manualJobs, state.loaded, running, ids.join(','), state.homeNextWeek || 0];
  if (!homeModelCache.key || key.some((part, i) => part !== homeModelCache.key[i])) {
    homeModelCache.key = key;
    homeModelCache.model = buildColourHomeModel({ state, applicationOf, advertSource, languageGroup, automationButton,
      live: { running, step: null, stageLabel: '' }, periodIds: ids, weekOffset: state.homeNextWeek || 0 });
  }
  const model = homeModelCache.model;
  const readyRoles = (state.roles || []).filter(role => role.cv_available && applicationOf(role).status === 'ready');
  return renderHomeNext({ model, readyRoles, sections: state.homeNextSections, series: state.homeNextSeries || {}, weekOffset: state.homeNextWeek || 0, sheetRejections: state.overview?.sheet_rejections || null, hark: state.overview?.hark_kpi || null, firecrawl: state.overview?.firecrawl_kpi || null, gemini: state.overview?.gemini_accuracy || null,
    analytics: state.overview?.dashboard_analytics });
}
function updateColourOverview(target) {
  if (!target) return;
  const run = currentRun();
  const running = Boolean(run && run.active && !run.stale && !run.finalized);
  const card = running ? runCardState(run) : null;
  const model = buildColourHomeModel({
    state, applicationOf, advertSource, languageGroup, automationButton,
    live: { running, step: card?.position || null, stageLabel: card?.current?.label || '' },
  });
  mountColourHome(target, model, id => {
    state.homeTimeframe = id;
    target.closest('.page')?.classList.remove('is-entering');
  });
}
function applicationContractGroup(contract, title = '') {
  const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const savedContract = normalize(contract);
  const placeholder = savedContract.replace(/[^a-z0-9]+/g, ' ').trim();
  const missingContract = !savedContract || /^[–—-]$/.test(savedContract) || ['unknown', 'not recorded', 'not specified', 'unspecified', 'not provided', 'not available', 'n a', 'na', 'other', 'other verify', 'other to verify', 'non renseigne', 'non precise', 'inconnu', 'inconnue', 'a verifier'].includes(placeholder);
  // A substantive saved contract takes priority. Only a placeholder allows the
  // saved title to supply evidence; English stage occupations are not internships.
  const text = missingContract ? normalize(title)
    .replace(/\b(?:early|late|mid|multi)[\s\-‐‑–—]+stages?\b/g, ' ')
    .replace(/\bstages?[\s\-‐‑–—]+(?:managers?|management|directors?|technicians?|hands?|crew|coordinators?)\b/g, ' ') : savedContract;
  const internship = /\b(?:stages?|stagiaires?|internships?|interns?)\b/.test(text);
  const freelance = /\b(?:freelances?|freelancers?|freelancing|independant(?:e)?s?|contractors?)\b|\bself[\s\-‐‑–—]+employed\b/.test(text);
  // Unclear labels or conflicting special types stay with employment. This never changes
  // the saved wording and reads no advert, company or profile evidence.
  return internship === freelance ? 'employment' : internship ? 'internship' : 'freelance';
}
// Apply later (26 Oct): a snoozed job leaves "To apply" until its date, then comes back by itself.
function applyIsSnoozed(role) {
  const item = state.applyFlow[role.id];
  return item?.kind === 'later' && Date.parse(item.until) > Date.now();
}
function applyLaterHidden(role) { return state.applyFilter === 'ready' && applyIsSnoozed(role); }
const wontReasonLabels = { internship: 'Internship', not_a_fit: 'Not a fit', already_applied: 'Already applied', job_closed: 'Job closed', location: 'Location', other: 'Other' };
async function loadApplyFlow() {
  try { state.applyFlow = (await api.applyFlow()).items || {}; if (state.route === 'apply') updateApply(); } catch (_) {}
}
async function saveApplyFlow(id, body) {
  const result = await api.saveApplyFlow(id, body);
  state.applyFlow = result.items || {};
  return result;
}
async function applyLater(id, anchor) {
  if (state.applicationSaving.has(id)) return;
  const days = await window.studioApplyFlow?.askLater(anchor);
  if (!days) return;
  state.applicationSaving.add(id);
  const row = document.querySelector(`[data-application-row="${CSS.escape(id)}"]`);
  const list = filteredApplications(); const at = list.findIndex(item => item.id === id);
  try {
    await saveApplyFlow(id, { kind: 'later', days });
    if (row) await window.studioApplyFlow?.playLater(row);
    if (row && state.applySelected === id) state.applySelected = (list[at + 1] || list[at - 1])?.id || '';
    if (row) await removeApplicationRow(row);
    toast(`Back in ${days} ${days === 1 ? 'day' : 'days'}.`, 'success', { undo: async () => { try { await saveApplyFlow(id, { kind: 'clear' }); updateApply(); } catch (error) { toast(error.message, 'error'); } } });
  } catch (error) { toast(error.message, 'error'); if (row) row.style.cssText = ''; }
  finally { state.applicationSaving.delete(id); if (state.route === 'apply') updateApply(); }
}
async function wontApply(id, anchor) {
  if (state.applicationSaving.has(id)) return;
  const reason = await window.studioApplyFlow?.askWont(anchor);
  if (!reason) return;
  try { await saveApplyFlow(id, { kind: 'wont', reason }); } catch (error) { toast(error.message, 'error'); return; }
  await setApplication(id, 'withdrawn', { reason });
}
function filteredApplications() {
  const query = state.applyQuery.toLocaleLowerCase();
  return state.roles.filter(role => {
    const language = String(role.language || '').toLowerCase();
    const fit = fitPercent(role);
    return applicationStatusMatches(role, state.applyFilter) && !applyLaterHidden(role)
      && (!query || [role.company,role.title,role.location].join(' ').toLocaleLowerCase().includes(query))
      && (state.applyLanguage === 'all' || languageGroup(language) === state.applyLanguage)
      && (state.applyDocuments === 'all' || (state.applyDocuments === 'cv' ? role.cv_available : state.applyDocuments === 'letter' ? role.cover_letter_available : !role.cv_available))
      && fitScoreMatches(fit, state.applyFit)
      && (state.applyContract === 'all' || applicationContractGroup(role.contract, role.title) === state.applyContract)
      && (state.applySource === 'all' || advertSource(role) === state.applySource);
  }).sort((a,b) => { const sort = state.applyFilter !== 'ready' && state.applySort === 'fit' ? 'recent' : state.applySort; const when = r => new Date(applicationEventDate(r, 'applied') || applicationEventDate(r, 'rejected') || applicationOf(r).updated_at || roleDate(r) || 0); return sort === 'recent' ? when(b) - when(a) : sort === 'company' ? roleCompany(a).localeCompare(roleCompany(b)) : state.applySort === 'fit' ? ((fitPercent(b) ?? -1) - (fitPercent(a) ?? -1) || new Date(roleDate(b) || 0) - new Date(roleDate(a) || 0)) : state.applySort === 'oldest' ? new Date(roleDate(a) || 0) - new Date(roleDate(b) || 0) : new Date(roleDate(b) || 0) - new Date(roleDate(a) || 0); });
}
function applicationSelect(role, extra = '') {
  const record = applicationOf(role);
  return `<select class="application-status" data-application-role="${e(role.id)}" aria-label="Application status for ${e(roleCompany(role))}" ${state.applicationSaving.has(role.id) ? 'disabled' : ''} ${extra}>${record.status === 'pending' ? '<option value="pending" selected disabled>Review first</option>' : ''}${Object.keys(applicationLabels).filter(status => status !== 'pending').map(status => `<option value="${status}" ${record.status === status ? 'selected' : ''}>${applicationLabels[status]}</option>`).join('')}</select>`;
}
// 23 Oct (Daksh): only the profile fit, shown out of 100 (the 1–5 rating × 20). JD/company English are hidden.
function jobRatings(role) {
  const pct = fitPercent(role);
  return `<div class="job-ratings fit-only" aria-label="Profile fit"><div class="fit-meter fit-${fitBand(pct)}" role="meter" aria-valuemin="0" aria-valuemax="100" ${pct == null ? 'aria-valuetext="Not assessed"' : `aria-valuenow="${pct}"`}><strong>${pct == null ? '—' : `${fitText(pct)}<small>%</small>`}</strong><span>Profile fit</span><i style="--p:${pct ?? 0}%"></i></div></div>`;
}
// Circular fit gauge (23 Oct): Daksh's r-progress "circular" design, ported to plain SVG (Studio has no React).
// Arc colour runs the full spectrum: red at 0 %, amber, yellow-green, bright green at 100 %.
function fitGauge(pct) {
  const r = 45, c = 2 * Math.PI * r, px = c / 100, gap = 5;
  if (pct == null) return `<div class="fit-gauge is-empty" role="meter" aria-label="Profile fit" aria-valuetext="Not assessed"><svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="${r}" class="fit-gauge-track" stroke-dasharray="${(100 - 2 * gap) * px} ${c}" transform="rotate(${-90 + gap * 3.6} 50 50)"/></svg><span>—</span></div>`;
  const hue = Math.round(pct * 1.3);
  const rest = 100 - pct - 2 * gap;
  return `<div class="fit-gauge" role="meter" aria-label="Profile fit" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" style="--fit:hsl(${hue} 95% 55%);--fit-glow:hsl(${hue} 95% 55% / .45);--len:${pct * px}"><svg viewBox="0 0 100 100">${rest > 0 ? `<circle cx="50" cy="50" r="${r}" class="fit-gauge-track" stroke-dasharray="${rest * px} ${c}" transform="rotate(${-90 + (pct + gap) * 3.6} 50 50)"/>` : ''}<circle cx="50" cy="50" r="${r}" class="fit-gauge-arc" stroke-dasharray="${pct * px} ${c}" transform="rotate(-90 50 50)"/></svg><span>${fitText(pct)}<small>%</small></span></div>`;
}
function jobRatingsAll(role) {
  const ratings = role.ratings || {};
  return `<div class="job-ratings" aria-label="Job ratings">${[['jd_english','JD English'],['profile_fit','Profile fit'],['company_english','Company English']].map(([key,label]) => {
    const rating = ratings[key] || {};
    const score = Number.isInteger(rating.score) && rating.score >= 1 && rating.score <= 5 ? rating.score : null;
    const sources = (rating.sources || []).filter(source => safeUrl(source.url));
    const tile = `<span class="rating-heading"><span class="rating-label">${label}</span></span><span class="readiness-ring" role="meter" aria-label="${e(label)}" aria-valuemin="1" aria-valuemax="5" ${score == null ? 'aria-valuetext="Not assessed"' : `aria-valuenow="${score}" aria-valuetext="${score} out of 5"`}><span class="readiness-center"><strong class="readiness-val">${score ?? '—'}</strong></span></span>`;
    if (key !== 'company_english') return `<div class="rating-gauge rating-static rating-${score ?? 'unknown'}"><div class="rating-summary">${tile}</div></div>`;
    return `<details class="rating-gauge rating-${score ?? 'unknown'}"><summary class="rating-summary" aria-label="${e(label)}: ${score == null ? 'not assessed' : `${score} out of 5`}. Show details">${tile}</summary><div class="rating-details"><p>${e(rating.reason || 'No assessment saved yet.')}</p>${rating.basis === 'user_benchmark' ? '<small>Your example rating.</small>' : ''}${sources.slice(0,2).map(source => `<a href="${e(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">${e(source.label || 'Company source')}${icon('upRight')}</a>`).join('')}</div></details>`;
  }).join('')}</div>`;
}
function contactsForRole(roleId) {
  return state.savedContacts.find(item => item?.role_id === roleId) || null;
}
function contactSearchComplete(roleId) {
  const saved = contactsForRole(roleId);
  const result = state.contactResults.get(roleId);
  const job = state.contactJobs.get(roleId) || saved?.last_job || saved?.job;
  return Boolean(saved?.search_completed || result?.search_completed || job?.search_completed
    || (Array.isArray(saved?.contacts) && saved.contacts.length)
    || (Array.isArray(result?.contacts) && result.contacts.length)
    || ['completed', 'done', 'succeeded'].includes(String(job?.status || '').toLowerCase()));
}
function contactWorkflowButton(role) {
  if (contactSearchComplete(role.id)) {
    return '<button class="button button-secondary button-small contact-saved-link" data-action="view-saved-contacts" data-role-id="' + e(role.id) + '">' + icon('mail') + 'View saved contacts</button>';
  }
  const saved = contactsForRole(role.id);
  const job = state.contactJobs.get(role.id) || saved?.last_job || saved?.job;
  const busy = state.actionBusy.has(role.id + ':contacts') || runningStatuses.has(String(job?.status || '').toLowerCase());
  const canStop = busy && typeof job?.id === 'string' && runningStatuses.has(String(job.status || '').toLowerCase());
  const stopping = job?.status === 'stopping';
  const pending = !state.contactsIndexLoaded;
  return '<div class="contact-search-actions"><button class="button button-secondary button-small contact-workflow-start ' + (busy ? 'is-searching' : '') + '" data-action="find-contacts" data-role-id="' + e(role.id) + '" title="Opens the advert in Chrome and copies the saved Haiku 5.5 contact prompt" ' + (busy || pending ? 'disabled aria-busy="true"' : '') + '>' + icon(busy ? 'refresh' : pending ? 'clock' : 'search', busy ? 'icon-spin' : '') + (busy ? 'Searching…' : pending ? 'Loading contacts…' : 'Find contacts') + '</button>' +
    (canStop ? '<button class="button button-ghost button-small contact-search-stop" data-action="stop-contact-search" data-job-id="' + e(job.id) + '" ' + (stopping ? 'disabled' : '') + '>' + icon('stop') + (stopping ? 'Stopping…' : 'Stop search') + '</button>' : '') + '</div>';
}
function modelName(value) {
  const normalized = String(value || '').toLowerCase();
  if (normalized.includes('luna')) return 'GPT-6 Luna';
  if (normalized.includes('gemini') || normalized.includes('flash')) return 'Gemini 3.8 Flash';
  if (normalized.includes('gpt-6.1-sol') || normalized.includes('sol 6.1')) return 'GPT-6.1 Sol';
  if (normalized.includes('sol')) return 'GPT-6 Sol';
  if (normalized.includes('opus')) return 'Claude Opus';
  return value || 'Model not recorded';
}
function providerEffort(provider, model, effort) {
  const source = provider === 'codex' ? 'Codex' : provider === 'antigravity' ? 'Antigravity' : provider;
  return [source, modelName(model), effort ? String(effort).toUpperCase() : ''].filter(Boolean).join(' · ');
}
function workflowStepMarkup(item, isReview = false) {
  const title = isReview ? 'Final review' : item.purpose || item.label || item.id || 'Research step';
  const requested = providerEffort(item.requested_provider, item.requested_model, item.requested_effort);
  const actual = providerEffort(item.actual_provider, item.actual_model, item.actual_effort);
  return `<li class="contact-workflow-step"><div><strong>${e(title)}</strong><span>${e(statusLabel(item.status || 'not_recorded'))}</span></div>${requested ? `<small>Requested: ${e(requested)}</small>` : ''}${actual ? `<small>Ran: ${e(actual)}</small>` : ''}${item.fallback_reason ? `<small class="workflow-fallback">Route change: ${e(item.fallback_reason)}</small>` : ''}${item.summary ? `<small>${e(item.summary)}</small>` : ''}${Number.isFinite(item.verified_count) || Number.isFinite(item.rejected_count) ? `<small>${numberOrUnknown(item.verified_count)} verified · ${numberOrUnknown(item.rejected_count)} set aside</small>` : ''}</li>`;
}
function renderContactWorkflow(workflow) {
  if (!workflow || (!Array.isArray(workflow.lanes) && !workflow.review)) return '';
  const lanes = Array.isArray(workflow.lanes) ? workflow.lanes : [];
  return `<section class="contact-workflow-progress" aria-label="Contact search workflow"><strong>Workflow status: ${e(statusLabel(workflow.status || 'not_recorded'))}</strong>${lanes.length ? `<ol>${lanes.map(item => workflowStepMarkup(item)).join('')}</ol>` : ''}${workflow.review ? `<ol class="contact-review-list">${workflowStepMarkup(workflow.review,true)}</ol>` : ''}</section>`;
}
const terminalContactStatuses = new Set(['completed', 'done', 'succeeded', 'failed', 'interrupted', 'cancelled', 'canceled', 'stopped', 'error']);
function persistContactNoticeCache() {
  try { localStorage.setItem(contactNoticeCacheStorageKey, JSON.stringify([...state.contactNoticeCache.values()])); } catch {}
}
function contactNoticeRecords() {
  const records = new Map();
  const add = (roleId, job, source = {}) => {
    if (!roleId || !job) return;
    const id = String(job.id || source.id || ('role:' + roleId));
    const status = String(job.status || (source.search_completed ? 'completed' : '')).toLowerCase();
    if (!runningStatuses.has(status) && !terminalContactStatuses.has(status)) return;
    const role = state.roles.find(item => item.id === roleId);
    const stamp = job.updated_at || job.finished_at || job.started_at || job.created_at || source.saved_at || '';
    const time = new Date(stamp).getTime();
    const record = {
      id,
      roleId,
      company: source.company || roleCompany(role || {}),
      title: source.title || roleTitle(role || {}),
      status,
      summary: job.summary || job.error || source.summary || '',
      searchCompleted: source.search_completed === true || job.search_completed === true,
      time: Number.isFinite(time) ? time : 0,
    };
    if (state.dismissedContactNotices.has(id)) return;
    const previous = records.get(id);
    if (!previous || record.time >= previous.time || runningStatuses.has(status)) records.set(id, record);
  };
  state.savedContacts.forEach(item => {
    if (item?.last_job) add(item.role_id, item.last_job, item);
    if (item?.job) add(item.role_id, item.job, item);
    if (!item?.last_job && !item?.job && item?.search_completed) add(item.role_id, { status: 'completed' }, item);
  });
  state.contactJobs.forEach((job, roleId) => {
    const source = state.savedContacts.find(item => item?.role_id === roleId) || {};
    add(roleId, job, source);
  });
  let changed = false;
  records.forEach((record, id) => {
    const previous = state.contactNoticeCache.get(id);
    let next = record;
    if (previous && terminalContactStatuses.has(previous.status) && !terminalContactStatuses.has(record.status)) {
      next = { ...record, status:previous.status, summary:previous.summary || record.summary, searchCompleted:previous.searchCompleted || record.searchCompleted };
    } else if (previous && terminalContactStatuses.has(record.status)) {
      next = { ...previous, ...record, summary:record.summary || '', searchCompleted:previous.searchCompleted || record.searchCompleted };
    } else if (previous) {
      next = { ...previous, ...record, summary:record.summary || previous.summary };
    }
    if (!previous || JSON.stringify(previous) !== JSON.stringify(next)) {
      state.contactNoticeCache.set(id, next);
      changed = true;
    }
  });
  if (changed) persistContactNoticeCache();
  return [...state.contactNoticeCache.values()]
    .filter(item => !state.dismissedContactNotices.has(item.id))
    .sort((a, b) => Number(runningStatuses.has(b.status)) - Number(runningStatuses.has(a.status)) || b.time - a.time);
}
function contactNoticeStatus(record) {
  if (record.searchCompleted || ['completed', 'done', 'succeeded'].includes(record.status)) return 'Search complete';
  if (['failed', 'error'].includes(record.status)) return 'Search needs attention';
  if (['interrupted', 'cancelled', 'canceled', 'stopped'].includes(record.status)) return 'Search stopped';
  return statusLabel(record.status);
}
function updateContactNotifications() {
  const host = document.querySelector('#contact-search-notices');
  if (!host) return;
  const notices = contactNoticeRecords().filter(item => !state.dismissedContactNotices.has(item.id));
  const bell = document.querySelector('.notice-bell');
  if (bell) { bell.querySelector('.notice-dot').hidden = !notices.length; bell.setAttribute('aria-label', notices.length ? `Contact search updates, ${notices.length} new` : 'Contact search updates'); }
  host.classList.toggle('is-open', state.noticesOpen);
  if (!notices.length && state.noticesOpen) { host.innerHTML = '<p class="contact-notice-empty">No contact search updates.</p>'; return; }
  host.innerHTML = notices.map(item => {
    const label = contactNoticeStatus(item);
    const summary = item.summary || (label === 'Search complete' ? 'Review the saved public results.' : 'Public sources only. No messages are sent.');
    return '<article class="contact-search-notice" role="status" aria-label="' + e(label + ': ' + item.company) + '">' +
      '<span class="contact-notice-mark" aria-hidden="true">' + icon(runningStatuses.has(item.status) ? 'refresh' : 'mail', runningStatuses.has(item.status) ? 'icon-spin' : '') + '</span>' +
      '<div class="contact-notice-copy"><span class="contact-notice-status">' + e(label) + '</span><strong>' + e(item.company) + '</strong>' +
      '<span class="contact-notice-title">' + e(item.title) + '</span><p>' + e(summary) + '</p>' +
      '<button class="text-link contact-notice-link" data-action="view-contact-search" data-role-id="' + e(item.roleId) + '">View contacts ' + icon('arrow') + '</button></div>' +
      '<button class="icon-button contact-notice-dismiss" data-action="dismiss-contact-notice" data-contact-notice-id="' + e(item.id) + '" aria-label="Dismiss contact search update for ' + e(item.company) + '">' + icon('close') + '</button></article>';
  }).join('');
}
function toggleNotices(open = !state.noticesOpen) {
  state.noticesOpen = open;
  document.querySelector('#contact-search-notices')?.classList.toggle('is-open', open);
  document.querySelector('.notice-bell')?.setAttribute('aria-expanded', String(open));
}
function dismissContactNotice(id) {
  if (!id) return;
  state.dismissedContactNotices.add(id);
  try { localStorage.setItem(contactNoticeStorageKey, JSON.stringify([...state.dismissedContactNotices])); } catch {}
  state.contactNoticeCache.delete(id);
  persistContactNoticeCache();
  updateContactNotifications();
}
function applicationEventDate(role, status) {
  const record = applicationOf(role);
  const explicit = record[`${status}_at`];
  if (explicit) return explicit;
  if (Array.isArray(record.status_history)) {
    const event = record.status_history.find(item => item?.status === status && item.at);
    if (event?.at) return event.at;
  }
  return null;
}
function buildReviewNotice(role) {
  return role.build_review?.required ? `<p class="inline-notice package-review-note" role="status">${icon('info')}${e(role.build_review.reason)}</p>` : '';
}
function applicationDateFacts(role) {
  const record = applicationOf(role);
  const appliedEvidence = isSubmitted(role) || hasApplicationStatus(role, 'applied');
  const rejectedEvidence = Boolean(record.rejected_at) || hasApplicationStatus(role, 'rejected');
  if (!appliedEvidence && !rejectedEvidence) return '';
  const markedByline = 'Date this status was marked in Studio; actual send or reply dates may differ.';
  const appliedDate = appliedEvidence ? applicationEventDate(role, 'applied') : null;
  const rejectedDate = rejectedEvidence ? applicationEventDate(role, 'rejected') : null;
  const fact = (status, evidence, value) => `<span class="application-date-slot" ${evidence ? '' : 'aria-hidden="true"'}>${evidence ? `<span title="${e(markedByline)}">${value ? `${status === 'applied' ? 'Applied' : 'Rejected'} on ${e(date(value))}` : `${status === 'applied' ? 'Applied' : 'Rejected'} date not recorded`}</span>` : ''}</span>`;
  return `<div class="application-date-facts" aria-label="Recorded application dates">${fact('applied', appliedEvidence, appliedDate)}${fact('rejected', rejectedEvidence, rejectedDate)}</div>`;
}
function applicationCopyButton(role) {
  const language = languageGroup(role.language);
  const hasLetter = Boolean(role.cover_letter_available);
  const suffix = language === 'unknown' ? '' : ' · ' + language.toUpperCase();
  const label = (hasLetter ? 'Copy cover letter' : 'Copy motivation') + suffix;
  const key = hasLetter ? role.id + ':cover-letter' : role.id + ':motivation:' + language;
  const busy = state.actionBusy.has(key);
  const missingLanguage = !hasLetter && language === 'unknown';
  const title = missingLanguage ? 'A French or English job language is needed to prepare motivation.' : label;
  const attrs = 'title="' + e(title) + '" aria-label="' + e(label) + '" data-role-id="' + e(role.id) +
    '" data-language="' + (language === 'unknown' ? '' : language) + '" data-letter="' + (hasLetter ? 'true' : 'false') + '"' +
    (busy || missingLanguage ? ' disabled' : '');
  // Label sits in .apply-step-copy so it ellipsises like the other step buttons (3 Nov, Daksh).
  return '<div class="motivation-buttons" role="group" aria-label="Cover letter or company motivation">' +
    '<button class="button button-secondary button-small application-copy-button" data-action="copy-application-text" ' + attrs + '>' + icon('copy') + '<span class="apply-step-copy"><strong>' + e(label) + '</strong></span></button></div>';
}
// Old running Studio servers may not yet supply the compact API label.
function applicationLocationLabel(role) {
  const raw = String(role.location_label ?? role.location ?? '').trim();
  if (!raw) return '—';
  if (/^(?:Greater Paris|Paris Region|Région parisienne|[ÎI]le[- ]de[- ]France)\b/i.test(raw)) return 'Île-de-France';
  const paris = raw.match(/\bParis(?=\s|[(),·-]|\d|$)(?:\s*\(?\s*(?:(750)(\d{2})|(0?[1-9]|1\d|20)(?:e|er|ème|eme)?(?:\s+arrondissement)?)(?=\D|$))?/i);
  if (paris) {
    const arrondissement = Number(paris[2] || paris[3] || raw.match(/\b750(0[1-9]|1\d|20)\b/)?.[1]);
    return `Paris${arrondissement >= 1 && arrondissement <= 20 ? ` ${arrondissement}${arrondissement === 1 ? 'er' : 'e'}` : ''}`;
  }
  const parts = raw.split(/[·,;\n|]/).map(part => part.trim()).filter(Boolean);
  const first = /^\d{5}$/.test(parts[0]) ? (parts[1] || '') : (parts[0] || '');
  if (/^[ÎI]le[- ]de[- ]France\b/i.test(first)) return 'Île-de-France';
  const city = first.replace(/\s+[-–—]\s+(?=CDI|CDD|intérim|contrat|langue|français|anglais).*$/i, '').replace(/^\d{5}\s+/, '').replace(/\s*(?:\([^)]*\)|[-–—]?\s*\b\d{2,5}\b).*$/, '').trim();
  if (!city || /^France$/i.test(city) || city.length > 45 || city.split(/\s+/).length > 5 || /\b(?:rue|avenue|boulevard|poste|basé|basée|situé|située|recherche|recrute|CDI|CDD|remote|télétravail)\b/i.test(city) || !/^[\p{L}][\p{L}’' –-]*$/u.test(city)) return '—';
  return city;
}
function renderApplicationResults() {
  if (state.errors.roles) return errorState(state.errors.roles);
  const roles = filteredApplications();
  if (!roles.length) { state.applySelected = ''; return empty('No jobs in this view.', 'Choose another status or clear your search.', 'search', button('Show all jobs','clear-apply','','button-secondary button-small')); }
  const chosen = selectedApplication(roles);
  const index = roles.indexOf(chosen);
  return `<div class="apply-split"><ol class="apply-queue" aria-label="Jobs in this view">${roles.map((role,i) => applyQueueItem(role,i,role === chosen)).join('')}</ol><div class="application-list apply-focus">${applicationDetail(chosen,index,roles.length)}</div></div><div class="table-footer"><span>${roles.length} ${roles.length === 1 ? 'job' : 'jobs'}</span><span>Status saved on this Mac</span></div>`;
}
// Apply works one job at a time: a slim queue on the left, the chosen job on the right.
// The three steps (open, copy, CV) come first, then the applied / won't apply decision.
function selectedApplication(roles) {
  const chosen = roles.find(role => role.id === state.applySelected) || roles[0];
  state.applySelected = chosen?.id || '';
  return chosen;
}
function applyQueueItem(role, index, selected) {
  const fit = fitPercent(role);
  const language = languageGroup(role.language);
  const location = applicationLocationLabel(role);
  const duplicateTitle = roleTitle(role).trim().toLocaleLowerCase() === roleCompany(role).trim().toLocaleLowerCase();
  const subtitle = [duplicateTitle ? '' : roleTitle(role), location === '—' ? '' : location].filter(Boolean).join(' · ');
  return `<li><button class="apply-queue-item ${selected ? 'is-selected' : ''}" data-apply-select="${e(role.id)}" ${selected ? 'aria-current="true"' : ''} aria-label="${e(`${roleCompany(role)}, ${duplicateTitle ? 'role title not recorded' : roleTitle(role)}, profile fit ${fit ?? 'not scored'}`)}">${companyAvatar(roleCompany(role),role.id,index)}<span class="apply-queue-copy">${approvalItemFor(role) ? '<em class="approve-tag">To be approved</em>' : manualJobFor(role) ? '<em class="added-tag" title="Added by you, not found by the pipeline">You</em>' : ''}<strong>${e(roleCompany(role))}${state.shortlist[role.id] ? `<span class="apply-queue-star" title="On your shortlist">${icon('star')}</span>` : ''}</strong><span>${e(subtitle || 'Role title not recorded')}</span></span><span class="apply-queue-fit apply-fit-${fitBand(fit)}">${language === 'unknown' ? '' : `<small>${language.toUpperCase()}</small>`}<b>${fit == null ? '—' : `${fitText(fit)}<small>%</small>`}</b></span></button></li>`;
}
function applicationDetail(role, index, total) {
  const record = applicationOf(role); const url = safeUrl(record.apply_url);
  const open = ['ready', 'saved', 'contacting', 'pending'].includes(record.status);
  const language = languageGroup(role.language);
  const languageName = language === 'fr' ? 'French' : language === 'en' ? 'English' : '';
  const locationLabel = applicationLocationLabel(role);
  const duplicateTitle = roleTitle(role).trim().toLocaleLowerCase() === roleCompany(role).trim().toLocaleLowerCase();
  const identity = duplicateTitle
    ? `<strong class="application-company-only" title="${e(roleCompany(role))}">${e(roleCompany(role))}</strong><small class="application-missing-title">Role title not recorded</small>`
    : `<strong title="${e(roleCompany(role))}">${e(roleCompany(role))}</strong><span title="${e(roleTitle(role))}">${e(roleTitle(role))}</span>`;
  const chips = [manualJobFor(role) ? addedChip : '', locationLabel === '—' ? '' : locationLabel, languageName ? `${languageName} advert` : '', role.cv_available ? '' : 'No CV yet'].filter(Boolean);
  const kbd = key => `<kbd aria-hidden="true">${key}</kbd>`;
  const openJob = url
    ? `<a class="apply-step apply-step-primary" data-apply-open href="${e(url)}" target="_blank" rel="noopener noreferrer">${icon('upRight')}<span class="apply-step-copy"><strong>View job &amp; apply</strong><small>Opens in a new tab</small></span>${kbd('O')}</a>`
    : '<span class="apply-step apply-step-missing application-no-link">No advert link saved</span>';
  const viewCv = role.cv_available
    ? `<button class="apply-step application-view-cv" data-action="view-application-cv" data-role-id="${e(role.id)}" title="View CV" aria-label="View CV for ${e(roleCompany(role))}">${icon('file')}<span class="apply-step-copy"><strong>View CV</strong><small>${e(languageName ? `PDF, ${languageName}` : 'PDF')}</small></span>${kbd('V')}</button>`
    : '<span class="apply-step apply-step-missing application-no-cv">No CV yet</span>';
  const decide = open
    ? `<button class="apply-decide-button apply-mark-applied" data-action="mark-applied" data-role-id="${e(role.id)}" aria-label="Mark ${e(roleCompany(role))} as applied">${icon('check')}<span>Mark applied</span>${kbd('A')}</button><button class="apply-decide-button application-wont-apply" data-action="wont-apply" data-role-id="${e(role.id)}" title="I will not apply to this job" aria-label="Will not apply to ${e(roleCompany(role))}">${icon('close')}<span>Won't apply</span>${kbd('W')}</button><button class="apply-decide-button application-job-inactive" data-action="mark-inactive" data-role-id="${e(role.id)}" title="This job is no longer online" aria-label="Mark ${e(roleCompany(role))} as inactive">${icon('alert')}<span>Job inactive</span>${kbd('I')}</button><button class="apply-decide-button application-apply-later" data-action="apply-later" data-role-id="${e(role.id)}" title="Bring this job back in 1 to 4 days" aria-label="Apply to ${e(roleCompany(role))} later">${icon('clock')}<span>Apply later</span>${kbd('L')}</button>`
    : '';
  return `<article class="application-row apply-detail" data-application-row="${e(role.id)}">
    <header class="apply-detail-head">${companyAvatar(roleCompany(role),role.id,index)}<div class="apply-detail-title"><button class="application-role" data-open-role="${e(role.id)}"><span class="application-role-copy" data-lang="${e(language || '')}">${identity}<small class="application-location" title="${e(locationLabel)}">${e(locationLabel)}</small>${record.canonical_applied && record.status !== 'applied' ? '<small class="application-history-note">Previously applied</small>' : ''}</span></button><div class="apply-chips"><span data-apply-contract="${e(role.id)}">${e(applyContractLabel(role))}</span>${chips.map(chip => `<span${chip === addedChip ? ' class="apply-chip-added" title="You added this job; it was not found by the pipeline"' : ''}>${e(chip)}</span>`).join('')}</div>${applicationDateFacts(role)}</div><div class="apply-head-tools">${shortlistToggle(role)}<span class="apply-position" title="J / K: next and previous job">${index + 1}<small>/${total}</small></span></div></header>
    ${buildReviewNotice(role)}
    <aside class="application-card-sidebar" aria-label="Job ratings">${jobRatings(role)}</aside>
    <div class="apply-steps">${openJob}${applicationCopyButton(role)}${viewCv}<button class="apply-step apply-step-chat" data-action="chat-mercury" data-role-id="${e(role.id)}" title="Chat with Mercury about this job">${icon('info')}<span class="apply-step-copy"><strong>Chat with Mercury</strong><small>CV, advert, letter, profile loaded</small></span></button></div>
    ${approvalItemFor(role) ? approvalBar(role) : ''}<div class="apply-decide" ${approvalItemFor(role) ? 'hidden' : ''}>${open ? '' : `<p><strong>${e(applicationLabels[record.status] || 'Status')}</strong></p>`}${decide}<label class="apply-status-field"><span>Status</span>${applicationSelect(role)}</label>
      <details class="apply-more"><summary aria-label="More actions">${icon('more')}<span>More</span></summary><div class="apply-more-menu"><div class="contact-button-slot" data-contact-button="${e(role.id)}">${contactWorkflowButton(role)}</div><details class="contact-discovery" data-contact-role="${e(role.id)}"><summary>Search results and sources${icon('chevron')}</summary><div class="contact-results" data-contact-results="${e(role.id)}">${renderContactSummary(role.id)}</div></details></div></details></div>
    <p class="role-action-note" role="status" ${state.roleActions.has(role.id) ? '' : 'hidden'}>${e(state.roleActions.get(role.id) || '')}</p>
    <section class="apply-advert" aria-label="Saved job advert"><h3>Job advert</h3><div class="apply-advert-body" data-apply-advert="${e(role.id)}">${renderApplyAdvert(role.id)}</div></section>
    <p class="apply-keys" aria-hidden="true">Keys: ${kbd('O')} open · ${kbd('C')} copy letter · ${kbd('A')} applied · ${kbd('W')} won't apply · ${kbd('L')} later · ${kbd('J')} ${kbd('K')} next and previous</p>
  </article>`;
}
// Shortlist (23 Oct 2026): jobs put aside to find a person and reach out, independent of the
// application status. Saved in Studio's local files; before Studio restarts with the new
// endpoint it falls back to this browser and moves the items over on the next load.
const shortlistStorageKey = 'cv-studio.shortlist.v1';
const shortlistStages = [['to_contact', 'To contact'], ['contact_found', 'Contact found'], ['reached_out', 'Reached out'], ['replied', 'Replied']];
const shortlistNoteTimers = new Map();
function localShortlist() {
  try { const value = JSON.parse(localStorage.getItem(shortlistStorageKey) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; }
  catch { return {}; }
}
function storeLocalShortlist() { try { localStorage.setItem(shortlistStorageKey, JSON.stringify(state.shortlist)); } catch {} }
async function loadShortlist() {
  try {
    const value = await api.shortlist();
    state.shortlistRemote = true; state.shortlist = value.items || {};
    const pending = Object.entries(localShortlist()).filter(([id]) => !state.shortlist[id]);
    let moved = 0;
    for (const [id, entry] of pending) {
      try { state.shortlist = (await api.saveShortlist(id, { saved: true, stage: entry.stage || 'to_contact', note: entry.note || '' })).items || state.shortlist; moved += 1; } catch {}
    }
    if (moved === pending.length) { try { localStorage.removeItem(shortlistStorageKey); } catch {} }
  } catch {
    state.shortlistRemote = false; state.shortlist = localShortlist();
  }
  updateShortlistCount();
  if (state.loaded && ['shortlist', 'apply'].includes(state.route)) render();
}
function updateShortlistCount() { const node = document.querySelector('#shortlist-count'); if (node) node.textContent = String(Object.keys(state.shortlist).length); }
async function saveShortlistEntry(id, patch, { quiet = false } = {}) {
  if (!id) return false;
  const before = state.shortlist[id];
  const now = new Date().toISOString();
  if (patch.saved === false) delete state.shortlist[id];
  else { const { saved, ...rest } = patch; state.shortlist[id] = { added_at: now, stage: 'to_contact', note: '', ...before, ...rest, updated_at: now }; }
  if (state.shortlistRemote) {
    try {
      let saved;
      try { saved = await api.saveShortlist(id, patch); }
      catch (error) { if (error.status !== 403) throw error; await api.overview(); saved = await api.saveShortlist(id, patch); }  // Studio restarted: fetch the new token once
      state.shortlist = saved.items || state.shortlist;
    }
    catch (error) {
      if (error.status === 404 || error.status === 405) { state.shortlistRemote = false; storeLocalShortlist(); }
      else { if (before) state.shortlist[id] = before; else delete state.shortlist[id]; toast(error.message, 'error'); refreshShortlistViews(id); return false; }
    }
  } else storeLocalShortlist();
  updateShortlistCount();
  if (!quiet) refreshShortlistViews(id);
  return true;
}
async function toggleShortlist(id) {
  const on = Boolean(state.shortlist[id]);
  const role = state.roles.find(item => item.id === id);
  if (await saveShortlistEntry(id, { saved: !on })) toast(on ? 'Removed from your shortlist.' : `${role ? roleCompany(role) : 'Job'} is on your shortlist.`);
}
function refreshShortlistViews(id) {
  if (state.route === 'shortlist') { render(); return; }
  document.querySelectorAll(`[data-shortlist-toggle="${CSS.escape(id)}"]`).forEach(node => { node.outerHTML = shortlistToggle({ id }); });
  const item = document.querySelector(`[data-apply-select="${CSS.escape(id)}"]`);
  if (item) { item.querySelector('.apply-queue-star')?.remove(); if (state.shortlist[id]) item.querySelector('.apply-queue-copy strong')?.insertAdjacentHTML('beforeend', `<span class="apply-queue-star" title="On your shortlist">${icon('star')}</span>`); }
}
function shortlistToggle(role) {
  const on = Boolean(state.shortlist[role.id]);
  return `<button type="button" class="shortlist-toggle ${on ? 'is-on' : ''}" data-action="toggle-shortlist" data-shortlist-toggle="${e(role.id)}" data-role-id="${e(role.id)}" aria-pressed="${on}" title="${on ? 'Remove from your shortlist' : 'Put aside to find a contact'}">${icon('star')}<span class="shortlist-label">${on ? 'Shortlisted' : 'Shortlist'}</span></button>`;
}
function shortlistEntries() {
  return Object.entries(state.shortlist)
    .map(([id, entry]) => ({ id, entry, role: state.roles.find(role => role.id === id) }))
    .sort((a, b) => String(b.entry.added_at || '').localeCompare(String(a.entry.added_at || '')));
}
// Outreach (23 Oct): the shortlist and the saved-contact library are two tabs of one page.
function outreachTabs() {
  const tab = (id, label, count) => `<a href="#${id}" data-nav="${id}" class="outreach-tab ${state.route === id ? 'is-on' : ''}" ${state.route === id ? 'aria-current="page"' : ''}>${label}${count != null ? `<span>${count}</span>` : ''}</a>`;
  return `<nav class="outreach-tabs" aria-label="Outreach">${tab('shortlist', `${icon('star')}Shortlist`, Object.keys(state.shortlist).length)}${tab('contacts', `${icon('mail')}Contact library`, state.savedContacts.length)}</nav>`;
}
function renderShortlist() {
  const entries = shortlistEntries();
  const stageOf = item => item.entry.stage || 'to_contact';
  const shown = state.shortlistStage === 'all' ? entries : entries.filter(item => stageOf(item) === state.shortlistStage);
  const tabs = `<div class="filter-tabs shortlist-tabs" role="group" aria-label="Contact step filter">${[['all', 'All'], ...shortlistStages].map(([key, label]) => `<button class="filter-tab ${state.shortlistStage === key ? 'selected' : ''}" data-action="shortlist-filter" data-shortlist-stage="${key}" aria-pressed="${state.shortlistStage === key}">${label}<span>${key === 'all' ? entries.length : entries.filter(item => stageOf(item) === key).length}</span></button>`).join('')}</div>`;
  const local = state.shortlistRemote === false ? `<div class="inline-notice shortlist-local">${icon('info')}<span>Saved in this browser for now. It moves into Studio's files after the next Studio restart.</span></div>` : '';
  const body = !entries.length
    ? empty('Nothing shortlisted yet.', 'In Apply, press the star on a job you like. It lands here so you can find a person to write to.', 'star', '<a class="button button-secondary button-small" href="#apply" data-nav="apply">Go to Apply</a>')
    : !shown.length ? empty('No jobs at this step.', 'Pick another step above.', 'star') : `<ol class="shortlist-list">${shown.map(shortlistCard).join('')}</ol>`;
  return `${heading(outreachTabs())}<p class="page-lede">Jobs you put aside to reach a real person. Apply first, then find a contact and write.</p>${local}<section class="panel shortlist-panel"><div class="library-toolbar shortlist-toolbar">${tabs}</div>${body}</section>`;
}
function shortlistCard({ id, entry, role }, index) {
  const company = role ? roleCompany(role) : 'Job no longer in Studio';
  const record = role ? applicationOf(role) : null;
  const fit = role ? fitPercent(role) : null;
  const stage = entry.stage || 'to_contact';
  const steps = shortlistStages.map(([key, label], position) => `<button class="shortlist-step ${key === stage ? 'is-current' : ''} ${shortlistStages.findIndex(([k]) => k === stage) > position ? 'is-done' : ''}" data-action="shortlist-stage" data-shortlist-stage="${key}" data-role-id="${e(id)}" aria-pressed="${key === stage}">${e(label)}</button>`).join('');
  const actions = role
    ? `<span class="contact-button-slot" data-contact-button="${e(id)}">${contactWorkflowButton(role)}</span>${button('Ask Mercury', 'chat-mercury', 'sparkle', 'button-ghost button-small', `data-role-id="${e(id)}"`)}${button('Open in Apply', 'view-job-in-apply', 'upRight', 'button-ghost button-small', `data-role-id="${e(id)}"`)}${button('CV package', 'open-role', 'file', 'button-ghost button-small', `data-role-id="${e(id)}"`)}`
    : '';
  return `<li class="shortlist-card" data-shortlist-card="${e(id)}" data-application-row="${e(id)}">
    <div class="shortlist-card-head">${role ? companyAvatar(company, id, index) : ''}<div class="shortlist-card-title"><strong>${e(company)}</strong><span>${e(role ? roleTitle(role) : id)}</span></div><div class="shortlist-card-meta">${record ? `<span class="shortlist-chip">${e(applicationLabels[record.status] || 'Status')}</span>` : ''}${fit != null ? `<span class="shortlist-chip">Fit ${e(fitText(fit))}%</span>` : ''}<small>Added ${e(date(entry.added_at, { year: undefined }))}</small></div></div>
    <div class="shortlist-steps" role="group" aria-label="Contact step">${steps}</div>
    <label class="shortlist-note"><span class="sr-only">Note for ${e(company)}</span><textarea rows="2" data-shortlist-note="${e(id)}" placeholder="Who to contact, what you sent, when to follow up…">${e(entry.note || '')}</textarea></label>
    <div class="shortlist-actions">${actions}${button('Remove', 'toggle-shortlist', 'close', 'button-ghost button-small shortlist-remove', `data-role-id="${e(id)}"`)}</div>
    <p class="role-action-note" role="status" ${state.roleActions.has(id) ? '' : 'hidden'}>${e(state.roleActions.get(id) || '')}</p>
  </li>`;
}
document.addEventListener('input', event => {
  const id = event.target.dataset?.shortlistNote; if (!id) return;
  clearTimeout(shortlistNoteTimers.get(id));
  shortlistNoteTimers.set(id, setTimeout(() => saveShortlistEntry(id, { note: event.target.value }, { quiet: true }), 600));
});
// Internship when the contract, title or saved advert says so (stage, stagiaire, intern); otherwise CDD / CDI.
function applyContractLabel(role) {
  const advert = state.applyAdverts.get(role.id);
  const text = [role.contract, role.title, typeof advert === 'string' ? advert : ''].join(' ').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  if (/\b(stage|stages|stagiaire|stagiaires|internship|internships|intern|interns)\b/.test(text.replace(/\b(stage|back)\s*(manager|director|coordinator|technician|developer)\b/g, ''))) return 'Internship';
  if (applicationContractGroup(role.contract, role.title) === 'freelance') return 'Freelance';
  return 'CDD / CDI';
}
// JD highlighter (23 Oct): words that match the CV bank are marked; list built by Studio/jd_keywords.py.
let jdKeywords = null;
fetch('/static/jd-keywords.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null).then(data => {
  if (!data) return;
  const kinds = new Map(); const sensitive = []; const loose = [];
  const esc = term => e(term).replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  for (const kind of ['gap', 'language', 'match']) for (const term of data[kind] || []) {
    const key = term.length <= 4 ? e(term) : e(term).toLocaleLowerCase().replace(/\s+/g, ' ');
    if (!kinds.has(key)) { kinds.set(key, kind); (term.length <= 4 ? sensitive : loose).push(esc(term)); }
  }
  const wrap = list => list.length ? new RegExp(`(?<![\\p{L}\\p{N}])(${list.join('|')})(?![\\p{L}\\p{N}])`, list === loose ? 'giu' : 'gu') : null;
  jdKeywords = { kinds, loose: wrap(loose), sensitive: wrap(sensitive), facts: data.facts || null };
  document.querySelectorAll('[data-apply-advert]').forEach(node => { node.innerHTML = renderApplyAdvert(node.dataset.applyAdvert); });
}).catch(() => {});
function highlightJd(html, found) {
  if (!jdKeywords) return html;
  const mark = (match, kindKey) => { const kind = jdKeywords.kinds.get(kindKey); if (!kind) return match; found?.set(match.replace(/\s+/g, ' '), kind); return `<mark class="jd-${kind}">${match}</mark>`; };
  // Only touch text between tags so markup stays intact.
  return html.split(/(<[^>]+>)/).map(part => part.startsWith('<') ? part : (() => {
    let out = jdKeywords.loose ? part.replace(jdKeywords.loose, m => mark(m, m.toLocaleLowerCase().replace(/\s+/g, ' '))) : part;
    if (jdKeywords.sensitive) out = out.split(/(<mark[^>]*>.*?<\/mark>)/).map(seg => seg.startsWith('<mark') ? seg : seg.replace(jdKeywords.sensitive, m => mark(m, m))).join('');
    return out;
  })()).join('');
}
function renderApplyAdvert(id) {
  const advert = state.applyAdverts.get(id);
  if (advert === undefined) { queueMicrotask(() => loadApplyAdvert(id)); return '<p class="apply-advert-note"><span class="spinner" aria-hidden="true"></span>Loading the saved advert…</p>'; }
  if (advert === null) return '<p class="apply-advert-note"><span class="spinner" aria-hidden="true"></span>Loading the saved advert…</p>';
  if (advert === false) return '<p class="apply-advert-note">The saved advert could not be loaded. Open the job to read it.</p>';
  if (!advert.trim()) return '<p class="apply-advert-note">No advert text was saved for this job.</p>';
  const inline = text => e(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Skip the pipeline's record header (source, hashes, run ids); show the advert itself.
  let lines = advert.split('\n');
  const body = lines.findIndex(line => /^#+\s*(full advert|job description|description du poste|annonce|advert)\b/i.test(line.trim()));
  if (body >= 0) lines = lines.slice(body + 1);
  else lines = lines.filter(line => !/^\s*\*\*[^*]{1,40}:\*\*/.test(line));
  let html = ''; let list = false; let first = body < 0;
  for (const raw of lines) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    if (!bullet && list) { html += '</ul>'; list = false; }
    if (!line) continue;
    if (bullet) { if (!list) { html += '<ul>'; list = true; } html += `<li>${inline(bullet[1])}</li>`; }
    else if (/^#+\s/.test(line)) { if (!first) html += `<h4>${inline(line.replace(/^#+\s*/, ''))}</h4>`; }
    else html += `<p>${inline(line)}</p>`;
    first = false;
  }
  if (list) html += '</ul>';
  // 23 Oct: the advert itself folds away; a short fact sheet (languages, years, CV matches) leads.
  const full = html; const found = new Map();
  if (jdKeywords) html = highlightJd(html, found);
  const facts = jdFactsOf(lines.join('\n'));
  const chips = kind => [...found].filter(([, k]) => k === kind).map(([term]) => `<span class="jd-chip jd-${kind}">${term}</span>`).join('');
  const matches = [...found.values()].filter(k => k === 'match').length;
  const row = (label, body) => `<div class="jd-fact"><span class="jd-fact-label">${label}</span><div class="jd-fact-body">${body}</div></div>`;
  const none = text => `<span class="jd-none">${text}</span>`;
  const fitRole = state.roles.find(item => item.id === id); const fit = fitRole ? fitPercent(fitRole) : null;
  const sheet = `<div class="jd-sheet has-gauge"><div class="jd-gauge">${fitGauge(fit)}<span class="jd-gauge-label">Profile fit</span></div><div class="jd-rows">
    ${row('Languages', facts.languages.length ? facts.languages.map(l => `<span class="jd-chip jd-language">${e(l.language)}${l.level ? ` · ${e(l.level)}` : ''}</span>`).join('') : none('None named'))}
    ${row('Experience', facts.min != null ? `<span class="jd-years">${facts.min === facts.max ? `${facts.min}+` : `${facts.min}–${facts.max}`} yrs</span>${facts.quotes.map(q => `<span class="jd-quote">“${e(q)}”</span>`).join('')}` : facts.junior ? `<span class="jd-years jd-years-junior">Junior</span><span class="jd-quote">“${e(facts.junior)}”</span>` : none('Not stated'))}
    ${row(`On your CV <b>${matches}</b>`, chips('match') || none('No direct matches'))}
    ${chips('gap') ? row('Not on your CV', chips('gap')) : ''}
  </div></div>`;
  return `${sheet}<details class="jd-full" open><summary>${icon('file')}Full advert<span>${icon('down')}</span></summary><div class="jd-full-body">${jdKeywords ? html : full}</div></details>`;
}
// Same patterns as Studio/jd_facts.py (tuned on 443 saved adverts), exported in jd-keywords.json.
function jdFactsOf(text) {
  const f = jdKeywords?.facts; const out = { languages: [], min: null, max: null, quotes: [], junior: '' };
  if (!f) return out;
  const low = text.toLocaleLowerCase();
  for (const [name, words] of Object.entries(f.languages)) {
    const hit = words.map(w => low.search(new RegExp(`(?<![a-zà-ÿ])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![a-zà-ÿ])`))).find(i => i >= 0);
    if (hit === undefined) continue;
    const level = low.slice(Math.max(0, hit - 15), hit + 45).match(new RegExp(f.level, 'i'));
    out.languages.push({ language: name, level: level ? level[1] : '' });
  }
  if (out.languages.length > 5) out.languages = out.languages.filter(l => l.level || ['English', 'French'].includes(l.language));
  const num = w => /^\d+$/.test(w) ? Number(w) : f.num_words[w.trim().toLocaleLowerCase()];
  for (const m of text.matchAll(new RegExp(f.years, 'gi'))) {
    const window = text.slice(Math.max(0, m.index - 70), m.index + m[0].length + 70);
    if (!new RegExp(f.context, 'i').test(window)) continue;
    const lo = num(m[1]); const hi = m[2] ? num(m[2]) : lo;
    if (lo == null || lo > 25) continue;
    out.min = out.min == null ? lo : Math.min(out.min, lo); out.max = out.max == null ? hi : Math.max(out.max, hi);
    const quote = m[0].split(/\s+/).join(' '); if (!out.quotes.includes(quote) && out.quotes.length < 3) out.quotes.push(quote);
  }
  const junior = text.match(new RegExp(f.junior, 'i')); out.junior = junior ? junior[0] : '';
  return out;
}
async function loadApplyAdvert(id) {
  if (state.applyAdverts.has(id)) return;
  state.applyAdverts.set(id, null);
  let text = false;
  try { const detail = await api.role(id); text = String(detail?.jd || ''); } catch {}
  state.applyAdverts.set(id, text);
  const node = document.querySelector(`[data-apply-advert="${CSS.escape(id)}"]`); if (node) node.innerHTML = renderApplyAdvert(id);
  const role = state.roles.find(item => item.id === id);
  const chip = document.querySelector(`[data-apply-contract="${CSS.escape(id)}"]`); if (chip && role) chip.textContent = applyContractLabel(role);
}
function selectApplyRole(id, focusQueue = false) {
  if (!id || id === state.applySelected) return;
  state.applySelected = id;
  updateApply();
  const item = document.querySelector(`[data-apply-select="${CSS.escape(id)}"]`);
  item?.scrollIntoView({block:'nearest'});
  if (focusQueue) item?.focus({preventScroll:true});
}
function stepApplyRole(delta) {
  const roles = filteredApplications(); if (!roles.length) return;
  const at = Math.max(0, roles.findIndex(role => role.id === state.applySelected));
  const next = roles[Math.min(roles.length - 1, Math.max(0, at + delta))];
  if (next) selectApplyRole(next.id, document.activeElement?.matches?.('[data-apply-select]'));
}
function applyShortcut(event) {
  if (state.route !== 'apply' || event.metaKey || event.ctrlKey || event.altKey || detailDialog.open || actionDialog.open) return false;
  if (event.target.closest?.('input, textarea, select, [contenteditable="true"]')) return false;
  const key = event.key.toLowerCase();
  const detail = document.querySelector('.apply-detail');
  const press = selector => { const node = detail?.querySelector(selector); if (node && !node.disabled) node.click(); return Boolean(node); };
  if (key === 'j' || key === 'arrowdown') { stepApplyRole(1); return true; }
  if (key === 'k' || key === 'arrowup') { stepApplyRole(-1); return true; }
  if (key === '/') { document.querySelector('#apply-search')?.focus(); return true; }
  if (key === 'o') return press('[data-apply-open]');
  if (key === 'c') return press('[data-action="copy-application-text"]');
  if (key === 'v') return press('[data-action="view-application-cv"]');
  if (key === 'a') return press('[data-action="mark-applied"]');
  if (key === 'w') return press('[data-action="wont-apply"]');
  if (key === 'i') return press('[data-action="mark-inactive"]');
  if (key === 'l') return press('[data-action="apply-later"]');
  return false;
}
function manualJobRunning(item) {
  const job = item.build_job_id ? state.jobs.find(candidate => candidate.id === item.build_job_id) : null;
  return job && runningStatuses.has(job.status) ? job : null;
}
function manualJobLabel(item) {
  if (String(item.status || '').toLowerCase() === 'built_pending_approval') return 'Built · review pending';
  const job = item.build_job_id ? state.jobs.find(candidate => candidate.id === item.build_job_id) : null;
  if (job) return jobStatus(job);
  return statusLabel(item.status || 'saved');
}
function manualJobEligible(item) {
  const status = String(item.status || '').toLowerCase();
  return !['built', 'built_pending_approval', 'completed', 'done', 'building', 'running', 'applied', 'consumed'].includes(status);
}
function manualJobBuildBlockReason(item) {
  const active = activePipeline();
  if (item.owner_kind === 'pipeline') return 'Queued for a pipeline run. Manual build stays paused.';
  if (active.uncertain) return active.reason || 'Studio cannot confirm whether pipeline work is active. Refresh to check.';
  if (active.active) return active.reason || 'A pipeline run is active. Build now will be available when it ends.';
  const job = state.jobs.find(candidate => candidate.kind !== 'application_contacts' && (runningStatuses.has(candidate.status) || candidate.recovery_required));
  if (job?.recovery_required) return job.summary || 'A Studio process needs recovery review before another build can start.';
  if (job) return (job.label || 'A Studio process') + ' is ' + jobStatus(job).toLowerCase() + '. Wait for it to finish.';
  if (state.jobs.some(candidate => candidate.kind !== 'application_contacts' && (runningStatuses.has(candidate.status) || candidate.recovery_required))) return 'A Studio process is still active. Wait for it to finish.';
  if (item.build_job_id) return 'A build task is already linked to this saved job.';
  return '';
}
function manualJobCanBuild(item) {
  return manualJobEligible(item) && !manualJobRunning(item) && !manualJobBuildBlockReason(item);
}
function renderManualContactResearch(item) {
  const research = item.contact_research || {status:'off'};
  const contacts = Array.isArray(research.contacts) ? research.contacts : [];
  const choice = ['queued','building'].includes(item.status) ? '<label class="manual-contact-switch"><input type="checkbox" data-manual-contact-choice="' + e(item.id) + '" ' + (item.find_contacts === true ? 'checked' : '') + '>Find people to contact during the pipeline</label>' : '';
  const status = item.find_contacts === true ? '<strong>Public contacts: ' + e(statusLabel(research.status)) + '</strong>' : '<small>Contact search off</small>';
  return '<div class="manual-contact-status" role="status">' + choice + status +
    (research.summary ? '<p>' + e(research.summary) + '</p>' : item.find_contacts === true ? '<small>Starts alongside CV Build during the pipeline.</small>' : '') +
    (contacts.length ? renderVerifiedContacts(contacts,'') : '') + '</div>';
}
function queuedManualJobs() {
  return state.manualJobs.filter(item => String(item.status || "").toLowerCase() === "queued");
}
// 3 Nov (Daksh): queued jobs live in a small "Queued" pop-over in the Apply toolbar, so the
// fixed Apply layout never moves. Red badge = queued + newly built manual jobs; a dot when none.
// Jobs Daksh added himself are tagged "Added by you" in the list and detail.
const addedChip = 'Added by you';
const manualBuiltSeenKey = 'cv-studio.manual-built-seen.v1';
const manualBuiltStatuses = new Set(['built', 'built_pending_approval', 'completed', 'done']);
function rolePaths(role) { return [role?.folder, role?.document_folder, role?.path].filter(Boolean).map(String); }
function manualJobFor(role) {
  if (!role) return null;
  const paths = rolePaths(role);
  return state.manualJobs.find(item => item.role_path && paths.includes(String(item.role_path))) || null;
}
function manualJobRole(item) {
  const path = String(item?.role_path || '');
  return path ? state.roles.find(role => rolePaths(role).includes(path)) || null : null;
}
function builtManualJobs() {
  return state.manualJobs.filter(item => manualBuiltStatuses.has(String(item.status || '').toLowerCase()) && manualJobRole(item));
}
function readBuiltSeen() {
  try { const raw = localStorage.getItem(manualBuiltSeenKey); if (raw === null) return null; const list = JSON.parse(raw); return new Set(Array.isArray(list) ? list.map(String) : []); }
  catch { return new Set(); }
}
function writeBuiltSeen(seen) { try { localStorage.setItem(manualBuiltSeenKey, JSON.stringify([...seen])); } catch {} }
function unseenBuiltManualJobs() {
  // Wait for both lists before the first run, or every old build would count as new.
  if (!state.manualJobsLoaded || !state.roles.length) return [];
  const built = builtManualJobs();
  let seen = readBuiltSeen();
  if (seen === null) { seen = new Set(built.map(item => String(item.id))); writeBuiltSeen(seen); }
  return built.filter(item => !seen.has(String(item.id)));
}
function markBuiltSeen(ids) {
  if (!ids.length) return;
  const seen = readBuiltSeen() || new Set(); ids.forEach(id => seen.add(String(id))); writeBuiltSeen(seen);
}
function queueBadgeCount() { return queuedManualJobs().length + unseenBuiltManualJobs().length; }
function renderQueueBadge() {
  const count = queueBadgeCount();
  return count ? `<span class="queue-badge is-count" data-queue-badge aria-label="${count} queued or just built">${count}</span>` : '<span class="queue-badge is-dot" data-queue-badge aria-label="Nothing queued"></span>';
}
function renderJustBuilt() {
  const rows = (state.manualJustBuiltIds || []).map(id => {
    const item = state.manualJobs.find(candidate => String(candidate.id) === String(id));
    const role = item && manualJobRole(item);
    return role ? `<li><span><strong>${e(roleCompany(role))}</strong>${e(roleTitle(role))}</span>${button('Open', 'view-job-in-apply', 'upRight', 'button-secondary button-small', `data-role-id="${e(role.id)}"`)}</li>` : '';
  }).join('');
  return rows ? `<div class="manual-just-built"><h3><span class="queue-badge is-count">${(state.manualJustBuiltIds || []).length}</span>CV built · added by you</h3><ul>${rows}</ul></div>` : '';
}
function renderManualQueueToggle() {
  return `<details class="manual-queue-pop" ${state.manualQueueOpen ? 'open' : ''}><summary class="button button-secondary button-small" aria-label="Queued jobs">${icon('clock')}<span>Queued</span>${renderQueueBadge()}</summary><div class="manual-queue-pop-panel" data-queue-pop-panel>${renderJustBuilt()}${renderManualQueue()}</div></details>`;
}
function updateQueueBadge() {
  const pop = document.querySelector('.manual-queue-pop'); if (!pop) return;
  pop.querySelector('[data-queue-badge]')?.replaceWith(document.createRange().createContextualFragment(renderQueueBadge()));
  const panel = pop.querySelector('[data-queue-pop-panel]');
  if (panel && !panel.contains(document.activeElement)) panel.innerHTML = renderJustBuilt() + renderManualQueue();
}
document.addEventListener('toggle', event => {
  const pop = event.target;
  if (!pop.matches?.('.manual-queue-pop')) return;
  state.manualQueueOpen = pop.open;
  if (pop.open) { state.manualJustBuiltIds = unseenBuiltManualJobs().map(item => String(item.id)); markBuiltSeen(state.manualJustBuiltIds); }
  else state.manualJustBuiltIds = [];
  updateQueueBadge();
}, true);
document.addEventListener('pointerdown', event => {
  const pop = document.querySelector('.manual-queue-pop[open]');
  if (pop && !pop.contains(event.target)) pop.open = false;
});
document.addEventListener('keydown', event => {
  const pop = document.querySelector('.manual-queue-pop[open]');
  if (event.key === 'Escape' && pop) { pop.open = false; pop.querySelector('summary')?.focus(); }
});
function renderManualJobQueue() {
  const queue = queuedManualJobs();
  if (state.errors.manualJobs && !queue.length) return '<div class="manual-queue-message" role="status">' + icon('info') + e(state.errors.manualJobs) + '</div>';
  const stale = state.errors.manualJobs ? '<div class="manual-queue-message" role="status">' + icon('info') + 'Showing the last saved queue. ' + e(state.errors.manualJobs) + '</div>' : '';
  if (!state.manualJobsLoaded) return '<div class="manual-queue-message" role="status"><span class="spinner" aria-hidden="true"></span>Loading saved jobs…</div>';
  if (!queue.length) return stale;
  const rows = queue.map(item => {
    const title = item.title || item.company || item.url || 'Saved job';
    const meta = [item.company, item.source_kind ? statusLabel(item.source_kind) : null, item.created_at ? 'Added ' + date(item.created_at, {year:undefined}) : null].filter(Boolean);
    const job = manualJobRunning(item);
    const status = String(item.status || '').toLowerCase() === 'built_pending_approval' ? item.status : job?.status || item.status || 'saved';
    const reviewPending = String(item.status || '').toLowerCase() === 'built_pending_approval';
    const description = item.summary || (item.jd ? String(item.jd).trim().slice(0,180) : 'Ready for a future pipeline run.');
    const source = safeUrl(item.url);
    const eligible = manualJobEligible(item);
    const blocked = manualJobBuildBlockReason(item);
    const action = job
      ? '<span class="manual-build-live" role="status">' + icon('refresh','icon-spin') + 'Build in progress</span>'
      : eligible
        ? '<div class="manual-job-action">' + button('Build', 'build-manual-job', 'play', 'button-ghost button-small manual-build-action', 'data-manual-job-id="' + e(item.id) + '" title="' + e(blocked || 'Build this CV now') + '"' + (manualJobCanBuild(item) ? '' : ' disabled')) +
          (blocked ? '<small class="manual-build-reason" role="status">' + icon('info') + e(blocked) + '</small>' : '') + '</div>'
        : '';
    return '<li class="manual-job-item" data-manual-job="' + e(item.id) + '"><div class="manual-job-copy"><div class="manual-job-title-row"><strong title="' + e(title) + '">' + e(title) + '</strong>' + badge(status,manualJobLabel(item)) + '</div><span>' + e(meta.join(' · ') || 'Added job') + '</span><p>' + e(description) + '</p>' +
      (source ? '<a href="' + e(source) + '" target="_blank" rel="noopener noreferrer">Open saved advert' + icon('upRight') + '</a>' : '') +
      renderManualContactResearch(item) + '</div>' + action + '</li>';
  }).join('');
  return stale + '<ul class="manual-job-list">' + rows + '</ul>';
}
state.reviewDrafts = state.reviewDrafts || {};
function approvalItems() {
  return state.manualJobs.filter(item => String(item.status || '').toLowerCase() === 'built_pending_approval');
}
function renderApprovalPanel() {
  const items = approvalItems();
  if (!items.length) return '';
  const rows = items.map(item => {
    const role = state.roles.find(candidate => candidate.folder === item.role_path);
    const title = item.title || item.company || 'Saved job';
    const rounds = item.review_rounds ? ' · retry ' + Number(item.review_rounds) : '';
    const open = role ? button('Open CV package', 'open-role', 'file', 'button-ghost button-small', 'data-role-id="' + e(role.id) + '"') : '';
    return '<li class="manual-approval-item" data-approval-job="' + e(item.id) + '"><div class="manual-approval-copy"><strong>' + e(title) + '</strong><span>' + e(item.company || '') + e(rounds) + '</span><p>' + e(item.summary || 'Built by Mercury 3. Check the CV, then approve or send it back.') + '</p></div>' +
      '<div class="manual-approval-actions">' + open +
      '<label>Comment for Mercury 3 <span>(needed to send back)</span><textarea rows="2" data-review-comment="' + e(item.id) + '" placeholder="What should Mercury 3 fix?">' + e(state.reviewDrafts[item.id] || '') + '</textarea></label>' +
      '<div class="manual-approval-buttons">' + button('Approve', 'approve-manual-job', 'check', 'button-primary button-small', 'data-manual-job-id="' + e(item.id) + '"') +
      button('Send back to Mercury 3', 'reject-manual-job', 'refresh', 'button-secondary button-small', 'data-manual-job-id="' + e(item.id) + '"') + '</div></div></li>';
  }).join('');
  return '<section class="panel manual-approval-panel" aria-labelledby="manual-approval-title"><div class="section-heading"><div><h2 id="manual-approval-title">Manual approval required</h2><p class="section-subtitle">Mercury 3 built these. Nothing is ready to apply until you approve.</p></div><span class="count-pill">' + items.length + ' waiting</span></div><ul class="manual-approval-list">' + rows + '</ul></section>';
}
function updateApprovalPanel() {
  const node = document.querySelector('#manual-approval'); if (!node) return;
  const typing = document.activeElement?.matches?.('textarea[data-review-comment]');
  if (typing) return;
  node.innerHTML = renderApprovalPanel();
}
async function reviewManualJob(id, decision, target) {
  const comment = (state.reviewDrafts[id] || '').trim();
  if (decision === 'reject' && !comment) { toast('Write what Mercury 3 should fix first.', 'error'); return; }
  if (target) target.disabled = true;
  try {
    const response = await api.reviewManualJob(id, decision, comment);
    delete state.reviewDrafts[id];
    if (decision === 'approve') toast(response.application_status === 'ready' ? 'Approved. The CV is now Ready to apply.' : 'Approved.');
    else { const job = response.job; if (job?.id) { state.jobs = [job,...state.jobs.filter(item => item.id !== job.id)]; state.job = job; state.selectedJob = job.id; } toast('Sent back to Mercury 3 with your comment.'); scheduleRefresh(); }
    await refreshManualJobs(); await refreshJobs();
    if (typeof updateApply === 'function') updateApply();
    if (decision === 'approve') { if (detailDialog.open) detailDialog.close(); try { await refresh({ quiet: true }); } catch (_) {} render(); }
  } catch (error) { toast(error.message, 'error'); if (target?.isConnected) target.disabled = false; }
}
document.addEventListener('input', event => {
  const area = event.target.closest?.('textarea[data-review-comment]');
  if (area) state.reviewDrafts[area.dataset.reviewComment] = area.value;
});
function renderManualQueue() {
  const emptyQueue = state.manualJobsLoaded && !state.errors.manualJobs && !queuedManualJobs().length;
  if (emptyQueue) return `<section class="panel manual-queue-panel manual-queue-empty-panel" aria-labelledby="manual-queue-title"><div class="section-heading"><h2 id="manual-queue-title">Queued jobs</h2><span class="manual-queue-count">0 queued</span></div><div id="manual-job-queue"></div></section>`;
  return `<section class="panel manual-queue-panel" aria-labelledby="manual-queue-title"><div class="section-heading"><div><h2 id="manual-queue-title">Queued jobs</h2><p class="section-subtitle">The next pipeline run reviews saved jobs and builds eligible CVs. Build now builds the CV with Gemini 3.8 Flash from the master template, then runs the checks and a Gemini audit.</p></div><span class="manual-queue-count">${state.manualJobsLoaded ? `${queuedManualJobs().length} queued` : 'Loading'}</span></div><div id="manual-job-queue">${renderManualJobQueue()}</div></section>`;
}
function updateManualQueue() {
  let node = document.querySelector('#manual-job-queue');
  if (!node) return;
  const panel = node.closest('.manual-queue-panel');
  const shouldBeCompact = state.manualJobsLoaded && !state.errors.manualJobs && !queuedManualJobs().length;
  if (panel && panel.classList.contains('manual-queue-empty-panel') !== shouldBeCompact) {
    panel.outerHTML = renderManualQueue();
    return;
  }
  node = document.querySelector('#manual-job-queue');
  if (node) node.innerHTML = renderManualJobQueue();
}
function renderApply() {
  const counts = applicationCounts();
  const addJob = `<details class="manual-add-job"><summary class="button button-primary button-small"><span aria-hidden="true">+</span>Add job</summary><form id="manual-job-form" class="manual-job-form"><p>Add a job link, paste the advert text, or include both.</p><div class="manual-job-form-grid"><label>Job link<input name="url" type="url" placeholder="https://…" autocomplete="url"></label><label>Company<input name="company" type="text" maxlength="200" placeholder="Company name"></label><label>Job title<input name="title" type="text" maxlength="240" placeholder="Role title"></label><label class="manual-job-description">Job description<textarea name="jd" rows="5" placeholder="Paste the full job description"></textarea></label></div><label class="manual-contact-choice">Find people to contact?<select name="find_contacts" aria-label="Find people to contact"><option value="no" selected>No — don’t find contacts</option><option value="yes">Yes — find public contacts with AI</option></select><small>Public sources only. No messages sent. Runs with the pipeline, alongside CV Build.</small></label><div class="manual-job-form-actions"><span>Build now starts a Gemini 3.8 Flash CV build with checks and a Gemini audit when the workspace is idle.</span><button class="button button-primary button-small" type="submit">Save job</button></div></form></details>`;
  const filter = (key,label,options) => `<label>${label}<select data-apply-option="${key}" aria-label="${label}">${options.map(([value,name]) => `<option value="${value}" ${state[key] === value ? 'selected' : ''}>${name}</option>`).join('')}</select></label>`;
  const fitOptions = [['all','All scores'],['80','80+'],['70','70+'],['60','60+'],['50','50+'],['low','Below 50'],['unknown','Not scored']];
  const contractOptions = [['all','All contracts'],['employment','CDD / CDI'],['freelance','Freelancing'],['internship','Internship']];
  const activeFilters = ['applyLanguage','applyDocuments','applyFit','applyContract','applySource'].filter(key => state[key] !== 'all').length;
  const statusOptions = [['all','All jobs',counts.total], ...Object.entries(applicationLabels).map(([key,label]) => [key,label,counts[key]]), ...Object.entries(applicationStatusFilterLabels).map(([key,label]) => [key,label,counts[key]])];
  const tabs = `<div class="filter-tabs apply-tabs" role="group" aria-label="Application status filter">${['ready','applied','withdrawn','inactive','rejected','all'].map(status => `<button class="filter-tab ${state.applyFilter === status ? 'selected' : ''}" data-apply-filter="${status}" aria-pressed="${state.applyFilter === status}">${status === 'all' ? 'All jobs' : applicationLabels[status]}<span>${numberOrUnknown(counts[status === 'all' ? 'total' : status])}</span></button>`).join('')}</div>`;
  const filters = `<div class="apply-filters">${filter('applyLanguage','Language',[['all','Any language'],['fr','French'],['en','English'],['unknown','Other / not recorded']])}${filter('applyDocuments','Documents',[['all','All packages'],['cv','CV available'],['letter','Letter available'],['missing','No CV yet']])}${filter('applyFit','Profile fit',fitOptions)}${filter('applyContract','Contract',contractOptions)}${filter('applySource','Advert source',[['all','All sources'],['hellowork','HelloWork'],['francetravail','France Travail'],['linkedin','LinkedIn'],['indeed','Indeed'],['apec','APEC'],['welcometothejungle','Welcome to the Jungle'],['unknown','Other / no advert link']])}${filter('applySort','Sort jobs',[['fit','Highest profile fit'],['newest','Recently updated'],['oldest','Oldest first'],['company','Company A–Z']])}<label>Status<select data-apply-status-filter aria-label="Filter all application statuses">${statusOptions.map(([key,label,count]) => `<option value="${key}" ${state.applyFilter === key ? 'selected' : ''}>${e(label)} (${numberOrUnknown(count)})</option>`).join('')}</select></label></div>`;
  return `${heading()}<div id="manual-approval">${renderApprovalPanel()}</div><section class="panel apply-panel"><div class="library-toolbar apply-toolbar">${tabs}<div class="apply-toolbar-tools"><div class="search-field">${icon('search')}<input id="apply-search" type="search" placeholder="Search jobs, companies or locations" aria-label="Search jobs to apply" value="${e(state.applyQuery)}"><kbd aria-hidden="true">/</kbd></div><details class="apply-filter-drawer" ${state.applyFiltersOpen ? 'open' : ''}><summary class="button button-secondary button-small">${icon('filter')}Filters<span class="apply-filter-count" ${activeFilters ? '' : 'hidden'}>${activeFilters}</span></summary><div class="apply-filter-panel">${filters}${button('Reset filters','clear-apply','refresh','button-ghost button-small')}</div></details>${renderManualQueueToggle()}${addJob}</div></div><div id="application-results">${renderApplicationResults()}</div></section>`;
}
function updateApply() {
  const focused = document.activeElement?.dataset.applicationRole;
  const openContacts = [...document.querySelectorAll('.contact-discovery[open]')].map(node => node.dataset.contactRole);
  const openMore = [...document.querySelectorAll('.apply-more[open]')].map(node => node.closest('[data-application-row]')?.dataset.applicationRow);
  const openRatings = [...document.querySelectorAll('.rating-gauge[open]')].map(node => [node.closest('[data-application-row]')?.dataset.applicationRow,[...node.parentNode.children].indexOf(node)]);
  const results = document.querySelector('#application-results'); if (results) results.innerHTML = renderApplicationResults();
  updateManualQueue();
  updateApprovalPanel();
  updateQueueBadge();
  const queueCount = document.querySelector('.manual-queue-count'); if (queueCount) queueCount.textContent = `${queuedManualJobs().length} queued`;
  openContacts.forEach(id => { const node = document.querySelector(`[data-contact-role="${CSS.escape(id)}"]`); if (node) node.open = true; });
  openMore.forEach(id => { const node = document.querySelector(`[data-application-row="${CSS.escape(id || '')}"] .apply-more`); if (node) node.open = true; });
  openRatings.forEach(([id,index]) => { const node = document.querySelector(`[data-application-row="${CSS.escape(id || '')}"]`)?.querySelectorAll('.rating-gauge')[index]; if (node) node.open = true; });
  if (focused) document.querySelector(`select[data-application-role="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
  updateApplyCounts();
}
// Counts, tabs and footer only, so one status change does not rebuild every card.
function updateApplyCounts() {
  const counts = applicationCounts();
  document.querySelectorAll('.filter-tab[data-apply-filter]').forEach(node => { node.classList.toggle('selected',node.dataset.applyFilter === state.applyFilter); node.setAttribute('aria-pressed',String(node.dataset.applyFilter === state.applyFilter)); node.querySelector('span').textContent = numberOrUnknown(counts[node.dataset.applyFilter === 'all' ? 'total' : node.dataset.applyFilter]); });
  const statusFilter = document.querySelector('[data-apply-status-filter]'); if (statusFilter) { statusFilter.value = state.applyFilter; [...statusFilter.options].forEach(option => { if (option.value !== 'all') { const label = applicationLabels[option.value] || applicationStatusFilterLabels[option.value]; option.textContent = `${label} (${numberOrUnknown(counts[option.value])})`; } else option.textContent = `All jobs (${numberOrUnknown(counts.total)})`; }); }
  const shown = filteredApplications().length;
  const footer = document.querySelector('#application-results .table-footer span'); if (footer) footer.textContent = `${shown} ${shown === 1 ? 'job' : 'jobs'}`;
  updateShell();
  announce(`${shown} jobs`);
}
// Fade the card out, then slide the cards after it into the gap (FLIP, transforms only).
// "Won't apply": the card turns to dust (SVG displacement filter + drifting particles), then leaves.
function ensureDustFilter() {
  if (document.querySelector('#dust-filter')) return;
  document.body.insertAdjacentHTML('beforeend', `<svg width="0" height="0" aria-hidden="true" style="position:absolute"><defs>
    <filter id="dust-filter" x="-20%" y="-30%" width="140%" height="160%" color-interpolation-filters="sRGB">
      <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="3" seed="7" result="noise"/>
      <feDisplacementMap id="dust-map" in="SourceGraphic" in2="noise" scale="0" xChannelSelector="R" yChannelSelector="G"/></filter></defs></svg>`);
}
function dustOff(row) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !row.animate || document.hidden) return Promise.resolve();
  ensureDustFilter();
  const rect = row.getBoundingClientRect();
  const color = getComputedStyle(row).color || '#888';
  const layer = document.createElement('div');
  layer.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;pointer-events:none;z-index:1000;overflow:visible`;
  const count = Math.min(140, Math.max(60, Math.round(rect.width * rect.height / 900)));
  const total = 900;
  for (let i = 0; i < count; i++) {
    const x = Math.random() * rect.width, y = Math.random() * rect.height, size = 2 + Math.random() * 5;
    const dot = document.createElement('i');
    dot.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:${size}px;height:${size}px;border-radius:50%;background:${color};opacity:0`;
    const delay = (x / rect.width) * 450 + Math.random() * 120;   // the wave sweeps left to right
    dot.animate([{ transform: 'translate(0,0) scale(1)', opacity: 0 },
      { opacity: .85, offset: .15 },
      { transform: `translate(${60 + Math.random() * 140}px,${-70 + Math.random() * 90}px) scale(0)`, opacity: 0 }],
      { duration: 700 + Math.random() * 300, delay, easing: 'ease-out', fill: 'forwards' });
    layer.appendChild(dot);
  }
  document.body.appendChild(layer);
  const map = document.querySelector('#dust-map');
  row.style.filter = 'url(#dust-filter)';
  row.style.pointerEvents = 'none';
  const started = performance.now();
  return new Promise(resolve => {
    const tick = now => {
      const t = Math.min(1, (now - started) / total);
      if (map) map.setAttribute('scale', String(Math.round(t * t * 160)));
      row.style.opacity = String(Math.max(0, 1 - t * 1.25));
      row.style.transform = `translate(${t * 24}px,${-t * 10}px)`;
      if (t < 1) requestAnimationFrame(tick);
      else { layer.remove(); row.style.filter = ''; resolve(); }
    };
    requestAnimationFrame(tick);
  });
}
function removeApplicationRow(row, dust = false) {
  if (dust) return dustOff(row).then(() => { if (row.isConnected) row.remove(); });
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !row.animate) { row.remove(); return Promise.resolve(); }
  const after = []; for (let node = row.nextElementSibling; node; node = node.nextElementSibling) after.push(node);
  const before = new Map();
  for (const node of after) { const rect = node.getBoundingClientRect(); if (rect.top > innerHeight + 200) break; if (rect.bottom > -200) before.set(node,rect); }
  row.style.pointerEvents = 'none';
  const fade = row.animate([{opacity:1,transform:'none'},{opacity:0,transform:'scale(.96)'}],{duration:140,easing:'cubic-bezier(.4,0,1,1)',fill:'forwards'}).finished.catch(() => {});
  // Hidden tabs pause animations; the timer makes sure the card still leaves.
  return Promise.race([fade,new Promise(resolve => setTimeout(resolve,200))]).then(() => {
    if (!row.isConnected) return;
    row.remove();
    before.forEach((old,node) => { const now = node.getBoundingClientRect(); const dx = old.left - now.left, dy = old.top - now.top; if (dx || dy) node.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'none'}],{duration:220,easing:'cubic-bezier(.2,.8,.2,1)'}); });
  });
}
function renderVerifiedContacts(contacts, summary = '') {
  if (!Array.isArray(contacts) || !contacts.length) return `<p>${e(summary || 'No verified public contact found. Unverified or guessed addresses are not shown.')}</p>`;
  return contacts.map(contact => {
    const verifiedField = field => field?.verified === true && safeUrl(field.source_url).startsWith('https:') && field.checked_at;
    const email = verifiedField(contact.email) ? contact.email.value : '';
    const companyService = contact.recipient_type === 'company_service';
    const contactName = contact.name || (companyService ? 'Company service' : 'Company contact');
    const title = contact.title && contact.title !== contactName ? `<span>${e(contact.title)}</span>` : '';
    const organization = companyService && contact.organization ? `<span class="contact-organization">For ${e(contact.organization)}</span>` : '';
    let linkedin = verifiedField(contact.linkedin) ? safeUrl(contact.linkedin.url) : '';
    try { const parsed = new URL(linkedin); if (parsed.protocol !== 'https:' || !(parsed.hostname === 'linkedin.com' || parsed.hostname.endsWith('.linkedin.com')) || !parsed.pathname.startsWith('/in/')) linkedin = ''; } catch { linkedin = ''; }
    const sources = [contact.email,contact.linkedin].filter(item => verifiedField(item));
    return `<article class="verified-contact">${companyService ? '<small class="contact-recipient-type">Company service</small>' : ''}<strong>${e(contactName)}</strong>${title}${organization}${email ? `<span class="contact-email">${e(email)}</span>` : '<small>No published work email found</small>'}${linkedin ? `<a href="${e(linkedin)}" target="_blank" rel="noopener noreferrer">LinkedIn${icon('upRight')}</a>` : ''}${sources.map(source => `<a class="contact-source" href="${e(safeUrl(source.source_url))}" target="_blank" rel="noopener noreferrer">${e(source.source_name || 'Source')}${icon('upRight')}</a><small>${e(source.source_role || '')} · Checked ${e(date(source.checked_at))}</small>`).join('')}</article>`;
  }).join('');
}
function renderContactSummary(id) {
  const job = state.contactJobs.get(id);
  const result = state.contactResults.get(id);
  const indexed = contactsForRole(id);
  const contacts = result?.contacts || result?.items || indexed?.contacts || [];
  const progress = renderContactWorkflow(job?.contact_workflow || result?.job?.contact_workflow || result?.contact_workflow || indexed?.last_job?.contact_workflow);
  if (job && runningStatuses.has(job.status)) return `<p class="contact-search-status" role="status">${icon('refresh','icon-spin')}${e(job.summary || 'Searching public sources…')}</p>${progress}`;
  if ((job?.status === 'failed' || job?.status === 'interrupted') && !contactSearchComplete(id)) return `<p role="status">${e(job.summary || job.error || 'Search did not finish. You can retry.')}</p>${progress}`;
  if (!result && !indexed) return `${progress}<p class="muted">Results are saved here after a search. No messages are sent.</p>`;
  const summary = result?.summary || indexed?.summary;
  const cleanup = job?.recovery_required || indexed?.last_job?.recovery_required || indexed?.last_job?.cleanup_status === 'recovery_required';
  return `${cleanup ? `<p class="inline-notice contact-cleanup-note" role="status">${icon('alert')}Contact search history needs cleanup review. Saved verified results remain available.</p>` : ''}${progress}${renderVerifiedContacts(contacts,summary)}`;
}
function savedContactRows() {
  const query = state.contactsQuery.trim().toLocaleLowerCase();
  return state.savedContacts.filter(item => {
    if (state.contactsRoleFilter && item.role_id !== state.contactsRoleFilter) return false;
    const contactText = (item.contacts || []).flatMap(contact => [contact.name, contact.title, contact.email?.value, contact.linkedin?.url]).join(' ');
    return !query || [item.company, item.title, item.summary, contactText].join(' ').toLocaleLowerCase().includes(query);
  });
}
function renderSavedContactGroups() {
  if (!state.contactsIndexLoaded) return '<p class="muted" role="status">Loading the saved contact library…</p>';
  if (state.errors.contactsIndex && !state.savedContacts.length) return errorState(state.errors.contactsIndex);
  if (!state.savedContacts.length) return empty('No saved contacts yet.', 'Completed public contact searches will appear here with their verified emails and source links.', 'mail');
  const rows = savedContactRows();
  if (!rows.length) return empty('No matching contacts.', 'Try another company or contact name.', 'search', button('Clear search','clear-contact-search','refresh','button-secondary button-small'));
  return `${state.errors.contactsIndex ? `<div class="stale-notice" role="status">${icon('info')}Showing the last saved contact results. ${e(state.errors.contactsIndex)}</div>` : ''}<div class="saved-contact-list">${rows.map((item,index) => {
    const role = state.roles.find(candidate => candidate.id === item.role_id) || {id:item.role_id,company:item.company,title:item.title};
    const contacts = Array.isArray(item.contacts) ? item.contacts : [];
    const summary = item.summary || item.last_job?.summary || '';
    const jobStatusValue = item.last_job?.status || (item.search_completed ? 'completed' : 'not_recorded');
    const cleanup = item.last_job?.recovery_required || item.recovery_required || item.last_job?.cleanup_status === 'recovery_required';
    const statusBadge = cleanup ? badge('warning','Cleanup needs review') : badge(jobStatusValue,item.search_completed ? 'Search complete' : statusLabel(jobStatusValue));
    return `<article class="saved-contact-group" data-saved-contact-role="${e(item.role_id)}"><header class="saved-contact-header">${companyAvatar(item.company || roleCompany(role),item.role_id,index)}<div class="saved-contact-heading"><strong>${e(item.company || roleCompany(role))}</strong><span>${e(item.title || roleTitle(role))}</span><small>${item.saved_at ? `Saved ${e(date(item.saved_at))}` : 'Save date not recorded'}</small></div><div class="saved-contact-status">${statusBadge}</div></header><div class="saved-contact-summary">${summary ? `<p>${e(summary)}</p>` : ''}<span>${contacts.length} verified ${contacts.length === 1 ? 'contact' : 'contacts'} saved</span></div>${cleanup ? `<p class="inline-notice contact-cleanup-note" role="status">${icon('alert')}Saved results remain available. The latest contact run needs cleanup review.</p>` : ''}<div class="saved-contact-results">${renderVerifiedContacts(contacts, item.search_completed ? 'No verified public contacts were found for this job.' : 'No contact results are recorded for this job.')}</div><footer class="saved-contact-footer"><button class="text-link" data-action="view-job-in-apply" data-role-id="${e(item.role_id)}">Open job in Apply${icon('arrow')}</button><span>Public sources only · no messages sent</span></footer></article>`;
  }).join('')}</div>`;
}
function contactsLibraryCaption() {
  if (!state.contactsIndexLoaded) return 'Loading saved contacts…';
  if (state.errors.contactsIndex && !state.savedContacts.length) return 'Saved contact counts are unavailable. Please retry.';
  const allCount = state.savedContacts.reduce((total,item) => total + (Array.isArray(item.contacts) ? item.contacts.length : 0),0);
  return `${allCount} verified ${allCount === 1 ? 'contact' : 'contacts'} saved on this Mac. Emails are shown with their public source.`;
}
function updateContactsCaption() {
  const caption = document.querySelector('[data-contact-library-summary]');
  if (caption) caption.textContent = contactsLibraryCaption();
}
function renderContacts() {
  const selected = state.contactsRoleFilter ? state.savedContacts.find(item => item.role_id === state.contactsRoleFilter) : null;
  return `${heading(outreachTabs() + button('Refresh contacts','refresh','refresh','button-ghost button-small'))}<section class="panel contacts-panel"><div class="contacts-toolbar"><div><p class="eyebrow">SAVED PUBLIC RESULTS</p><h2>${selected ? e(selected.company || selected.title || 'Saved contacts') : 'Contact library'}</h2><p data-contact-library-summary>${e(contactsLibraryCaption())}</p></div>${selected ? button('Show all contacts','clear-contact-role','','button-secondary button-small') : ''}</div><div class="contacts-search search-field">${icon('search')}<input id="contacts-search" type="search" placeholder="Search companies, people or emails" aria-label="Search saved contacts" value="${e(state.contactsQuery)}"></div><div id="saved-contact-results">${renderSavedContactGroups()}</div></section>`;
}
function updateRoleAction(id) {
  const row = document.querySelector(`[data-application-row="${CSS.escape(id)}"]`); if (!row) return;
  const results = row.querySelector('[data-contact-results]'); if (results) results.innerHTML = renderContactSummary(id);
  const note = row.querySelector('.role-action-note'); if (note) { note.textContent = state.roleActions.get(id) || ''; note.hidden = !note.textContent; }
  const contactButton = row.querySelector(`[data-contact-button="${CSS.escape(id)}"]`); if (contactButton) contactButton.innerHTML = contactWorkflowButton(state.roles.find(role => role.id === id) || {id});
  const copyButton = row.querySelector('[data-action="copy-application-text"]');
  if (copyButton) {
    const hasLetter = copyButton.dataset.letter === 'true';
    const language = copyButton.dataset.language;
    const key = hasLetter ? id + ':cover-letter' : id + ':motivation:' + language;
    copyButton.disabled = state.actionBusy.has(key) || (!hasLetter && !['fr', 'en'].includes(language));
  }
}
async function readContacts(id) {
  try { const result = await api.contacts(id); state.contactResults.set(id,result); if (result.job || result.last_job) { const job = result.job || result.last_job; state.contactJobs.set(id,job); updateContactNotifications(); if (runningStatuses.has(job.status) && !state.pollingRoleJobs.has(job.id)) { state.actionBusy.add(`${id}:contacts`); watchRoleJob(id,job.id,'contacts'); } } const node = document.querySelector(`[data-contact-results="${CSS.escape(id)}"]`); if (node) node.innerHTML = renderContactSummary(id); await refreshContactsIndex(); if (state.route === 'apply') updateRoleAction(id); }
  catch (error) { const node = document.querySelector(`[data-contact-results="${CSS.escape(id)}"]`); if (node) node.innerHTML = `<p role="status">${e(error.message)}</p>`; }
}
async function refreshContactsIndex() {
  try {
    state.savedContacts = items(await api.contactsIndex());
    state.contactsIndexLoaded = true;
    delete state.errors.contactsIndex;
    updateContactsCaption();
    updateContactNotifications();
    // Update contact rows in place. A full Apply redraw restores open details
    // elements, whose toggle handlers fetch contacts again and can loop.
    if (state.route === 'apply') state.roles.forEach(role => updateRoleAction(role.id));
    if (state.route === 'contacts') { const results = document.querySelector('#saved-contact-results'); if (results) results.innerHTML = renderSavedContactGroups(); }
    return true;
  } catch (error) { state.contactsIndexLoaded = true; state.errors.contactsIndex = error.message; updateContactsCaption(); return false; }
}
function motivationPreview(id,language,value) {
  const role = state.roles.find(item => item.id === id);
  const content = value.text || value.motivation || value.result?.text || '';
  state.copyPreview = {id,language,text:content};
  const provenance = value.provenance || {};
  showAction(`<p class="eyebrow">${e(language.toUpperCase())} · COMPANY MOTIVATION</p><h2 id="action-title">${e(role ? roleCompany(role) : 'Motivation')}</h2><p>Read the wording before copying.</p><textarea class="motivation-preview" id="motivation-copy-text" readonly aria-label="Motivation to copy">${e(content)}</textarea><p class="motivation-source">${e(value.source === 'studio-generated' ? 'Prepared from saved sources' : 'Saved company motivation')} · ${content.length} characters${provenance.source_path ? `<br>${e(provenance.source_path)}` : ''}${safeUrl(role?.source_url) ? `<br><a href="${e(safeUrl(role.source_url))}" target="_blank" rel="noopener noreferrer">Review original advert${icon('upRight')}</a>` : ''}</p><div class="dialog-actions">${button('Close','close-action','','button-secondary')}${button('Copy text','copy-preview','copy','button-primary',content ? '' : 'disabled')}</div>`);
}
async function copyText(content, message = 'Motivation copied.') {
  try { await navigator.clipboard.writeText(content); toast(message); return true; }
  catch { const field = document.querySelector('#motivation-copy-text'); if (field) { field.focus(); field.select(); } toast('Select the text and press ⌘ C to copy.','error'); return false; }
}
// The top-bar links copy a profile or website address for pasting into application forms; they never open it.
async function copyLink(button) {
  const url = button.dataset.url || '';
  let copied = false;
  try { await navigator.clipboard.writeText(url); copied = true; }
  catch {
    const field = document.createElement('textarea'); field.value = url; field.setAttribute('readonly', ''); field.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
    document.body.appendChild(field); field.select();
    try { copied = document.execCommand('copy'); } catch {}
    field.remove();
  }
  if (!copied) { toast('Could not copy. Link: ' + url, 'error'); return; }
  button.classList.add('is-copied');
  toast(`Copied ${button.dataset.label}: ${url}`);
  setTimeout(() => button.classList.remove('is-copied'), 1500);
}
async function copyApplicationText(id, language, hasLetter) {
  if (hasLetter) return copyCoverLetter(id, language);
  if (!['fr', 'en'].includes(language)) {
    toast('The job language is not recorded, so Studio cannot choose French or English motivation safely.', 'error');
    return;
  }
  return prepareMotivation(id, language);
}
async function copyCoverLetter(id, fallbackLanguage) {
  const key = `${id}:cover-letter`; if (state.actionBusy.has(key)) return;
  state.actionBusy.add(key); if (state.route === 'apply') updateRoleAction(id);
  try {
    const role = await api.role(id);
    const text = role.cover_letter_text || '';
    if (!text.trim()) {
      if (['fr', 'en'].includes(fallbackLanguage)) {
        state.actionBusy.delete(key);
        await prepareMotivation(id, fallbackLanguage);
        return;
      }
      throw new Error('No saved cover letter text is available for this job.');
    }
    state.copyPreview = {id,text};
    if (!await copyText(text,'Cover letter text copied.')) {
      showAction(`<h2 id="action-title">Cover letter text</h2><textarea class="motivation-preview" id="motivation-copy-text" readonly aria-label="Cover letter text">${e(text)}</textarea><div class="dialog-actions">${button('Close','close-action','','button-secondary')}${button('Copy text','copy-preview','copy','button-primary')}</div>`);
      const field = document.querySelector('#motivation-copy-text'); field?.focus(); field?.select();
    }
  } catch (error) { state.roleActions.set(id,error.message); toast(error.message,'error'); }
  finally { state.actionBusy.delete(key); if (state.route === 'apply') updateRoleAction(id); }
}
function watchRoleJob(id,jobId,kind,language) { if (state.pollingRoleJobs.has(jobId)) return; state.pollingRoleJobs.add(jobId); pollRoleJob(id,jobId,kind,language); }
async function pollRoleJob(id,jobId,kind,language,failures = 0) {
  try {
    const job = kind === 'motivation' ? await api.job(jobId) : await api.contactJob(jobId);
    if (kind === 'contacts') { state.contactJobs.set(id,job); updateContactNotifications(); }
    if (runningStatuses.has(job.status)) { if (state.route === 'apply') updateRoleAction(id); setTimeout(() => pollRoleJob(id,jobId,kind,language),3000); return; }
    state.actionBusy.delete(kind === 'contacts' ? `${id}:contacts` : `${id}:motivation:${language}`);
    state.pollingRoleJobs.delete(jobId);
    if (['done','completed','succeeded'].includes(job.status)) {
      if (kind === 'contacts') { await readContacts(id); state.roleActions.set(id,'Contact search finished. Open “Find people to contact” for results.'); }
      else { const value = await api.motivation(id,language); state.roleActions.set(id,`${language.toUpperCase()} motivation is ready. Click its copy button to review.`); if (state.pendingMotivation?.jobId === jobId && actionDialog.open && actionDialog.querySelector(`[data-motivation-wait="${CSS.escape(jobId)}"]`)) motivationPreview(id,language,value); }
      toast(kind === 'contacts' ? 'Contact search finished.' : 'Motivation is ready to review.');
    } else { state.roleActions.set(id,job.summary || job.error || 'This task did not finish. Saved work remains available.'); toast(state.roleActions.get(id),'error'); }
    if (state.route === 'apply') updateRoleAction(id);
  } catch (error) { state.roleActions.set(id,error.message); if (failures < 2 && ![404,409].includes(error.status)) setTimeout(() => pollRoleJob(id,jobId,kind,language,failures+1),10000); else { state.pollingRoleJobs.delete(jobId); state.actionBusy.delete(kind === 'contacts' ? `${id}:contacts` : `${id}:motivation:${language}`); } if (state.route === 'apply') updateRoleAction(id); }
}
async function prepareMotivation(id,language) {
  const key = `${id}:motivation:${language}`; if (state.actionBusy.has(key)) return;
  state.actionBusy.add(key); if (state.route === 'apply') updateRoleAction(id);
  try {
    let value = await api.motivation(id,language);
    if (value.status === 'missing') value = await api.prepareMotivation(id,language);
    if (value.status === 'ready' && value.text) { state.actionBusy.delete(key); motivationPreview(id,language,value); if (state.route === 'apply') updateRoleAction(id); return; }
    if (!value.job_id && !value.job?.id && !value.text) throw new Error(value.summary || 'No motivation text or active task is available.');
    if (value.job_id || value.job?.id) { const jobId = value.job_id || value.job.id; state.pendingMotivation = {jobId,id,language}; state.roleActions.set(id,'Preparing source-backed wording. You can keep browsing.'); showAction(`<p class="eyebrow">COMPANY MOTIVATION</p><h2 id="action-title" data-motivation-wait="${e(jobId)}">Preparing ${e(language.toUpperCase())} wording</h2><p>Using the saved advert and approved facts. You can close this window and return when it is ready.</p>${button('Keep browsing','close-action','','button-secondary')}`); watchRoleJob(id,jobId,'motivation',language); }
    else { state.actionBusy.delete(key); motivationPreview(id,language,value); }
  } catch (error) { state.actionBusy.delete(key); state.roleActions.set(id,error.message); toast(error.message,'error'); }
  if (state.route === 'apply') updateRoleAction(id);
}
// Find contacts hands the search to Claude in Chrome (Haiku 5.5): Studio fills the saved
// prompt (Studio/prompts/find-contact-haiku.md), copies it and opens the advert in Chrome.
async function findContacts(id) {
  if (state.actionBusy.has(`${id}:contacts`) || state.actionBusy.has(`${id}:handoff`)) return;
  if (!state.contactsIndexLoaded) { state.roleActions.set(id, 'The saved contact library is still loading. Please wait.'); if (state.route === 'apply') updateRoleAction(id); return; }
  if (contactSearchComplete(id)) { state.contactsRoleFilter = id; await navigate('contacts'); return; }
  state.actionBusy.add(`${id}:handoff`);
  try {
    const value = await api.contactHandoff(id);
    let copied = value.copied === true;
    if (!copied && value.prompt) { try { await navigator.clipboard.writeText(value.prompt); copied = true; } catch {} }
    if (!value.opened && safeUrl(value.url)) window.open(safeUrl(value.url),'_blank','noopener,noreferrer');
    const model = value.model || 'Haiku 5.5';
    if (copied) {
      const message = `Prompt copied. In Chrome, open Claude, choose ${model}, then paste and send.`;
      state.roleActions.set(id,message); toast(message);
    } else {
      state.roleActions.set(id,`Copy the prompt into Claude in Chrome and choose ${model}.`);
      showAction(`<p class="eyebrow">FIND CONTACTS</p><h2 id="action-title">Paste this into Claude in Chrome</h2><p>Studio could not copy it for you. Choose ${e(model)} in Claude in Chrome, then paste this prompt.</p><textarea class="contact-handoff-prompt" readonly rows="12" aria-label="Contact search prompt">${e(value.prompt || '')}</textarea>${button('Close','close-action','','button-secondary')}`);
    }
  } catch (error) { state.roleActions.set(id,error.message); toast(error.message,'error'); }
  finally { state.actionBusy.delete(`${id}:handoff`); }
  if (state.route === 'apply') updateRoleAction(id);
}
async function stopContactSearch(jobId,target) {
  if (typeof jobId !== 'string' || !jobId) return;
  target.disabled = true;
  try {
    const response = await api.stopJob(jobId);
    const job = response.job || response;
    const roleId = [...state.contactJobs.entries()].find(([,item]) => item?.id === jobId)?.[0];
    if (roleId) {
      state.contactJobs.set(roleId,{...state.contactJobs.get(roleId),...job,status:job.status || 'stopping'});
      state.roleActions.set(roleId,'Stop requested for this contact search.');
      updateRoleAction(roleId);
      updateContactNotifications();
    }
    toast('Stop requested for the contact search.');
  } catch (error) { toast(error.message,'error'); target.disabled = false; }
}
async function addManualJob(form) {
  const data = new FormData(form);
  const payload = {
    url: String(data.get('url') || '').trim(),
    jd: String(data.get('jd') || '').trim(),
    title: String(data.get('title') || '').trim(),
    company: String(data.get('company') || '').trim(),
    find_contacts: data.get('find_contacts') === 'yes',
  };
  if (!payload.url && !payload.jd) { toast('Add a job link or paste a job description.','error'); form.elements.url.focus(); return; }
  if (payload.url && !safeUrl(payload.url).startsWith('https://') && !safeUrl(payload.url).startsWith('http://')) { toast('Enter a valid job link.','error'); form.elements.url.focus(); return; }
  const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
  try {
    const response = await api.addManualJob(payload);
    const item = response.item || response.job || null;
    if (item) state.manualJobs = [item,...state.manualJobs.filter(existing => existing.id !== item.id)];
    state.manualJobsLoaded = true;
    delete state.errors.manualJobs;
    form.reset();
    form.closest('details')?.removeAttribute('open');
    updateManualQueue();
    const count = document.querySelector('.manual-queue-count'); if (count) count.textContent = `${queuedManualJobs().length} queued`;
    toast(response.updated ? 'Saved job details updated.' : response.duplicate ? 'That job is already saved in the queue.' : 'Job saved. The next pipeline run will review it for a CV build.');
    await refreshManualJobs();
  } catch (error) { toast(error.message,'error'); }
  finally { if (submit.isConnected) submit.disabled = false; }
}
async function refreshManualJobs() {
  try { state.manualJobs = items(await api.manualJobs()); state.manualJobsLoaded = true; delete state.errors.manualJobs; }
  catch (error) { state.errors.manualJobs = error.message; state.manualJobsLoaded = true; }
  updateManualQueue();
  updateQueueBadge();
  const count = document.querySelector('.manual-queue-count'); if (count) count.textContent = `${queuedManualJobs().length} queued`;
}
async function buildManualJob(id,target) {
  const item = state.manualJobs.find(candidate => candidate.id === id);
  const reason = !item ? 'Refresh the saved jobs before starting a build.'
    : !manualJobEligible(item) ? 'This saved job already has a build or is no longer in the queue.'
      : manualJobBuildBlockReason(item) || (manualJobRunning(item) ? 'A build is already in progress.' : '');
  if (reason) {
    toast(reason, 'error');
    await refreshJobs();
    await refreshManualJobs();
    return;
  }
  if (target) target.disabled = true;
  try {
    const response = await api.buildManualJob(id);
    const job = response.job || response;
    const existing = state.manualJobs.find(item => item.id === id);
    if (existing && job?.id) existing.build_job_id = job.id;
    if (job?.id) { state.jobs = [job,...state.jobs.filter(item => item.id !== job.id)]; state.job = job; state.selectedJob = job.id; }
    await refreshManualJobs();
    toast('Build started: Mercury 3 will fill the master CV template, then the checks and its audit run. You approve at the end.');
    scheduleRefresh();
    await navigate('activity');
  } catch (error) { toast(error.message,'error'); if (target?.isConnected) target.disabled = false; }
}
async function setApplication(id,status,options = {}) {
  if (state.applicationSaving.has(id)) return;
  const role = detailDialog.open && state.roleDetail?.id === id ? state.roleDetail : state.roles.find(item => item.id === id);
  if (!role || applicationOf(role).status === status) return;
  state.applicationSaving.add(id);
  document.querySelectorAll('[data-application-role]').forEach(node => { if (node.dataset.applicationRole === id) node.disabled = true; });
  // A card leaving the current view exits straight away; the save (about a second) finishes behind it
  // and the card comes back if the save fails.
  const original = applicationOf(role);
  const optimistic = { ...role, application: { ...original, status } };
  const row = state.route === 'apply' && applicationStatusMatches(role,state.applyFilter) && !applicationStatusMatches(optimistic,state.applyFilter)
    ? document.querySelector(`[data-application-row="${CSS.escape(id)}"]`) : null;
  if (row && state.applySelected === id) { const list = filteredApplications(); const at = list.findIndex(item => item.id === id); state.applySelected = (list[at + 1] || list[at - 1])?.id || ''; }
  if (status === 'applied' || status === 'withdrawn' || status === 'inactive') await Promise.resolve(window.studioApplyFlow?.[status === 'applied' ? 'playApplied' : status === 'inactive' ? 'playInactive' : 'playWont'](row || document.querySelector(`[data-application-row="${CSS.escape(id)}"]`))).catch(() => {});
  const exit = row ? removeApplicationRow(row) : null;
  if (row) state.roles = state.roles.map(item => item.id === id ? optimistic : item);
  let saved = false;
  try {
    const result = await api.saveApplication(id,status,original.revision);
    const updated = result.role || { ...role, application: result.application };
    state.applicationUpdates.set(id,{epoch:++state.applicationEpoch,application:updated.application});
    state.roles = state.roles.map(item => item.id === id ? { ...item, application: updated.application } : item);
    if (state.roleDetail?.id === id) state.roleDetail = { ...state.roleDetail, application: updated.application };
    saved = true;
    // Applied: the check animation is the confirmation, so no message (26 Oct).
    toast(options.reason ? `Won't apply · ${wontReasonLabels[options.reason] || options.reason}.` : `Marked ${applicationLabels[status].toLowerCase()}.`, 'success', { undo: () => setApplication(id, original.status) });
  } catch (error) {
    if (row) state.roles = state.roles.map(item => item.id === id ? { ...item, application: role.application } : item);
    toast(error.message,'error');
  }
  finally {
    state.applicationSaving.delete(id);
    if (exit) await exit;
    if (state.route === 'apply') { if (saved && row && document.querySelector('#application-results .application-row')) updateApplyCounts(); else updateApply(); }
    if (detailDialog.open && state.roleDetail?.id === id) renderRoleDrawer(state.roleDetail);
    if (!row) document.querySelector(`select[data-application-role="${CSS.escape(id)}"]`)?.focus({preventScroll:true});
  }
}

function stageRail(context = 'pipeline') {
  return `<div class="stage-rail" style="--stage-count:${stages().length}" role="group" aria-label="Pipeline stages">${stages().map((stage, index) => `<button class="stage-node ${stage.status === 'running' ? 'is-running' : ''} ${context === 'pipeline' && stageId(selectedStage()) === stageId(stage) ? 'selected' : ''}" data-stage="${e(stageId(stage))}" ${stage.status === 'running' ? 'aria-current="step"' : ''} ${context === 'pipeline' ? `aria-pressed="${stageId(selectedStage()) === stageId(stage)}"` : ''}><span class="stage-track"><span class="stage-number">${String(index + 1).padStart(2, '0')}</span><span class="stage-connector"></span></span><strong>${e(stage.label)}</strong><span class="stage-caption">${e(stage.status ? statusLabel(stage.status) : 'View stage')}</span></button>`).join('')}</div>`;
}
function connectionStrip() {
  const caps = capabilities();
  return `<div class="connection-strip"><div>${icon(caps.readiness_confirmed ? 'check' : 'info')}<span>${e(caps.blocked_reason || (caps.checking ? 'Checking connection metadata…' : caps.checked_at ? 'Connections checked.' : 'Check connections to start.'))}</span></div>${button(caps.checking ? 'Checking…' : 'Check connections', 'check-connections', 'refresh', 'button-ghost button-small', caps.checking ? 'disabled' : '')}</div>`;
}
function runDisabled() {
  const active = activePipeline();
  return active.active || active.uncertain || ownedWork() || capabilities().can_run !== true;
}
function renderPipeline() {
  const stage = selectedStage();
  const live = currentRun();
  return `${heading(automationButton())}${!live?.active ? connectionStrip() : ''}
    <section class="panel pipeline-summary"><div class="section-heading"><h2>${live?.active && !live.finalized && !live.stale ? 'Current step' : 'Pipeline at a glance'}</h2>${navLink('activity','Saved run results')}</div>${stageRail()}<div class="pipeline-current"><div><span class="eyebrow">${e(stage.status ? statusLabel(stage.status) : 'Not started')}</span><h3>${e(stage.label)}</h3><p>${e(stage.detail || stage.description || 'Select a step to see what it does.')}</p></div><span class="pipeline-model">${e(stage.model || 'Model not recorded')}</span></div></section>
    <section class="panel pipeline-sources"><div class="section-heading"><h2>Search progress</h2><span class="subtle-label">Full ads and saved captures stay distinct</span></div>${sourceList().length ? `<div class="home-progress">${progressRows()}</div>` : '<p class="chart-caption">No source counts recorded yet.</p>'}</section>
    ${live?.thread_id ? `<details class="pipeline-more" data-pipeline-panel="workers" ${state.pipelineSections.has('workers') ? 'open' : ''}><summary>Workers & recent updates${icon('down')}</summary>${externalActivity()}</details>` : ''}
    ${state.jobs.length ? `<details class="pipeline-more" data-pipeline-panel="studio" ${state.pipelineSections.has('studio') || state.showStudioActivity || state.job?.pending_requests?.length ? 'open' : ''}><summary>Studio processes${state.job?.pending_requests?.length ? ' · Your input is needed' : ''}${icon('down')}</summary><section class="panel live-panel"><div class="section-heading"><h2>Studio activity</h2><div class="live-panel-actions"><label class="sr-only" for="job-select">Choose a Studio process</label><select id="job-select">${state.jobs.map(job => `<option value="${e(job.id)}" ${job.id === state.selectedJob ? 'selected' : ''}>${e(job.label || statusLabel(job.kind))} · ${e(jobStatus(job))}</option>`).join('')}</select>${button('Refresh','refresh-jobs','refresh','button-ghost button-small')}</div></div><div id="job-content">${renderJob()}</div></section></details>` : ''}`;
}

function renderJob() {
  if (state.errors.jobs) return errorState(state.errors.jobs, 'refresh-jobs');
  if (!state.job) return empty('No Studio jobs yet.', '', 'terminal', navLink('activity', 'View history'));
  const job = state.job;
  const pending = job.pending_requests || [];
  const logs = Array.isArray(job.logs) ? job.logs : [];
  return `<div class="job-summary"><div>${badge(job.status, jobStatus(job))}<span class="job-label">${e(job.label || statusLabel(job.kind))}</span></div><div><span class="subtle-label">Started ${e(date(job.started_at, { hour: '2-digit', minute: '2-digit', year: undefined }))}</span>${runningStatuses.has(job.status) ? button('Stop this process', 'stop-job', 'stop', 'button-danger button-small', job.status === 'stopping' ? 'disabled' : '') : ''}</div></div>${job.summary ? `<p class="job-explanation">${e(job.summary)}</p>` : ''}${job.recovery_required ? `<div class="inline-notice recovery-notice">${icon('alert')}This process needs a cleanup review. New work is paused; saved files and output remain available. Studio will not retry it automatically.</div>` : ['failed', 'interrupted', 'stopped'].includes(job.status) ? `<div class="inline-notice">${icon('info')}This process did not complete. Partial work stays available and will not restart automatically.</div>` : ''}${job.status === 'agent_finished' ? `<div class="inline-notice">${icon('info')}The coordinator finished. Check saved stage evidence before treating the pipeline as complete.</div>` : ''}
    ${pending.length ? `<div class="pending-requests">${pending.map((request, index) => `<div class="pending-request">${icon('info')}<div><strong>${e(request.title || 'Your input is needed')}</strong><p>${e(request.description || request.method || 'Review this request to continue.')}</p></div><button class="button button-secondary button-small" data-job-request="${index}">Review request${icon('arrow')}</button></div>`).join('')}</div>` : ''}
    <div class="log-toolbar"><span>${icon('terminal')}PROCESS OUTPUT</span><span>${logs.length ? `${logs.length} entries` : 'Waiting for output'}</span></div><div class="log-output" tabindex="0" aria-label="Process output">${logs.length ? logs.map(log => typeof log === 'string' ? `<div class="log-line"><span></span><pre>${e(log)}</pre></div>` : `<div class="log-line log-${e(log.level || 'info')}"><time>${e(log.at ? date(log.at, { hour: '2-digit', minute: '2-digit', second: '2-digit', day: undefined, month: undefined, year: undefined }) : '')}</time><pre>${e(log.message ?? log.text ?? '')}</pre></div>`).join('') : '<p class="log-placeholder">No output has been recorded for this process.</p>'}</div>`;
}
function renderStudioJobsPanel() {
  if (!state.jobs.length) return '';
  return `<section class="panel live-panel activity-jobs-panel" id="studio-jobs-panel"><div class="section-heading"><div><h2>Studio processes</h2><p class="section-subtitle">Live prompts, output and stop controls</p></div><div class="live-panel-actions"><label class="sr-only" for="job-select">Choose a Studio process</label><select id="job-select">${state.jobs.map(job => `<option value="${e(job.id)}" ${job.id === state.selectedJob ? 'selected' : ''}>${e(job.label || statusLabel(job.kind))} · ${e(jobStatus(job))}</option>`).join('')}</select>${button('Refresh','refresh-jobs','refresh','button-ghost button-small')}</div></div><div id="job-content">${renderJob()}</div></section>`;
}

function filteredRoles() {
  const query = state.query.toLocaleLowerCase();
  return state.roles.filter(role => (!query || [role.company, role.title, role.folder, role.location, role.language].join(' ').toLocaleLowerCase().includes(query)) && (state.filter === 'all' || (state.filter === 'cv' ? role.cv_available || role.status === 'artifact_present' : ['applied', 'inactive', 'current'].includes(state.filter) ? role.collection === state.filter : state.filter === 'source' ? !role.cv_available : role.status === state.filter))).sort((a, b) => state.sort === 'company' ? roleCompany(a).localeCompare(roleCompany(b)) : state.sort === 'oldest' ? new Date(roleDate(a) || 0) - new Date(roleDate(b) || 0) : new Date(roleDate(b) || 0) - new Date(roleDate(a) || 0));
}
function renderLibrary() {
  const masters = state.overview?.master_cvs || [];
  return `${heading()}${masters.length ? `<div class="master-cv-strip"><div><span class="master-symbol">${icon('file')}</span><span><strong>Master CVs</strong><small>English and French</small></span></div><div>${masters.filter(master => safeUrl(master.url)).map(master => `<a class="button button-secondary button-small" href="${e(safeUrl(master.url))}" target="_blank" rel="noopener noreferrer">${e(master.name)}${icon('upRight')}</a>`).join('')}</div></div>` : ''}<section class="panel library-panel"><div class="library-toolbar"><div class="search-field">${icon('search')}<input id="library-search" type="search" placeholder="Search companies, roles, or locations" aria-label="Search CV library" value="${e(state.query)}"><span class="search-key">/</span></div><div class="library-controls"><label for="library-sort" class="sr-only">Sort CV library</label><select id="library-sort"><option value="newest" ${state.sort === 'newest' ? 'selected' : ''}>Recently updated</option><option value="oldest" ${state.sort === 'oldest' ? 'selected' : ''}>Oldest first</option><option value="company" ${state.sort === 'company' ? 'selected' : ''}>Company A–Z</option></select></div></div><div class="library-filter-row"><div class="filter-tabs" role="group" aria-label="Filter CV packages">${[['all', 'All packages'], ['cv', 'With a CV'], ['applied', 'Applied archive'], ['inactive', 'Inactive'], ['source', 'Sources only']].map(([id, label]) => `<button class="filter-tab ${state.filter === id ? 'selected' : ''}" data-filter="${id}" aria-pressed="${state.filter === id}">${label}${id === 'all' ? `<span>${state.roles.length}</span>` : ''}</button>`).join('')}</div><span id="library-result-count" class="subtle-label"></span></div><div id="library-results">${renderRoleResults()}</div></section>`;
}
function renderRoleResults() {
  if (state.errors.roles) return errorState(state.errors.roles);
  const roles = filteredRoles();
  if (!roles.length) return empty(state.roles.length ? 'No packages match this view.' : 'Your library has room to grow.', state.roles.length ? 'Try a different search or clear your filters.' : 'Saved role folders and CV documents will appear here.', 'search', state.roles.length ? button('Clear filters', 'clear-filters', 'refresh', 'button-secondary button-small') : '');
  return `<div class="table-scroll"><table class="library-table"><thead><tr><th scope="col">COMPANY & ROLE</th><th scope="col">DOCUMENTS</th><th scope="col">STATUS</th><th scope="col">UPDATED</th><th scope="col"><span class="sr-only">Open package</span></th></tr></thead><tbody>${roles.map((role, index) => `<tr><td><button class="table-role" data-open-role="${e(role.id)}">${companyAvatar(roleCompany(role),role.id,index)}<span><strong>${e(roleCompany(role))}</strong><span>${e(roleTitle(role))}</span>${role.location ? `<small>${e(role.location)}</small>` : ''}</span></button></td><td><div class="document-chips">${role.cv_available ? `<span class="document-chip">${icon('file')}CV</span>` : '<span class="muted">—</span>'}${role.cover_letter_available ? '<span class="document-chip letter-chip">Letter</span>' : ''}</div></td><td>${roleBadge(role)}</td><td class="date-cell">${e(date(roleDate(role), { year: undefined }))}</td><td><button class="icon-button" data-open-role="${e(role.id)}" aria-label="Open ${e(roleCompany(role))} package">${icon('arrow')}</button></td></tr>`).join('')}</tbody></table></div><div class="table-footer"><span>${roles.length} ${roles.length === 1 ? 'package' : 'packages'}${state.query || state.filter !== 'all' ? ` of ${state.roles.length}` : ''}</span><span>From your local role folders</span></div>`;
}
function updateLibrary() {
  document.querySelector('#library-results').innerHTML = renderRoleResults();
  document.querySelectorAll('[data-filter]').forEach(node => { node.classList.toggle('selected', node.dataset.filter === state.filter); node.setAttribute('aria-pressed', String(node.dataset.filter === state.filter)); });
  announce(`${filteredRoles().length} CV packages found`);
}

function runClosed(run) { return Boolean(run.ended_at || run.finished_at || run.timing?.finished_at || ['done','completed','partial','failed','interrupted','expired','stopped','cancelled','canceled'].includes(run.status)); }
function runViewStatus(run) {
  const live = livePipeline();
  return !runClosed(run) && live?.active && !live.finalized && !live.stale && live.run_id === run.id ? 'running' : run.status;
}
function filteredRuns() { const query = state.activityQuery.toLowerCase(); return state.runs.filter(run => (!query || [run.id, run.summary, run.kind, runViewStatus(run), ...(run.jobs || []).flatMap(job => [job.company,job.title,job.role_path])].join(' ').toLowerCase().includes(query)) && (state.activityFilter === 'all' || runViewStatus(run) === state.activityFilter)); }
function renderAuditsPage() {
  if (state.audits === undefined && !state.auditsLoading && !state.auditsError) loadAudits();
  if (state.auditsError && state.audits === undefined) return heading() + errorState(state.auditsError);
  return renderAudits(state.audits ?? null, heading());
}
async function loadAudits() {
  if (state.auditsLoading) return;
  state.auditsLoading = true;
  try { state.audits = await api.audits(); state.auditsError = ''; }
  catch (error) { state.auditsError = error.message; }
  finally { state.auditsLoading = false; }
  if (state.route === 'audits') render();
}
function renderActivity() {
  const statuses = [...new Set(state.runs.map(runViewStatus).filter(Boolean))].sort();
  return `${heading()}${observedBanner()}${renderStudioJobsPanel()}<section class="panel activity-panel"><div class="activity-toolbar"><div class="search-field">${icon('search')}<input id="activity-search" type="search" placeholder="Search run history" aria-label="Search run history" value="${e(state.activityQuery)}"></div><label class="sr-only" for="activity-filter">Filter run status</label><select id="activity-filter"><option value="all">All statuses</option>${statuses.map(status => `<option value="${e(status)}" ${status === state.activityFilter ? 'selected' : ''}>${e(statusLabel(status))}</option>`).join('')}</select></div><div class="activity-layout"><div id="run-list" class="run-list" aria-label="Recorded runs">${renderRunList()}</div><div id="run-detail" class="run-detail">${renderRunDetail()}</div></div></section>`;
}
function renderRunList() {
  if (state.errors.runs) return errorState(state.errors.runs);
  const runs = filteredRuns();
  return runs.length ? `<div class="run-list-label">${runs.length} RECORDED ${runs.length === 1 ? 'RUN' : 'RUNS'}</div>${runs.map(run => `<button class="run-list-item ${state.selectedRun === run.id ? 'selected' : ''}" data-select-run="${e(run.id)}" aria-pressed="${state.selectedRun === run.id}"><span class="run-item-head"><span>${e(date(runDate(run), { year: undefined, hour:'2-digit', minute:'2-digit' }))}</span>${runBadge(run)}</span><strong>${e(runHeading(run))}</strong><span class="run-item-outcomes"><span><b>${e(runShortlisted(run) ?? '—')}</b> shortlisted</span><span><b>${e(run.summary_metrics?.cvs_built ?? '—')}</b> CVs built</span><span>${e(durationLabel(runDuration(run)))}</span></span>${runCompanyChips(run)}<span class="run-item-summary">${e(run.summary || '')}</span></button>`).join('')}` : empty('No matching records.', 'Try another search or status.', 'clock');
}

function runBadge(run) { return runViewStatus(run) === 'running' ? badge('running', 'In progress') : badge(run.status, statusLabel(run.status)); }
function runHeading(run) { return run.kind === 'batch' ? 'Pipeline run' : run.kind === 'claude_sheet' ? 'Claude sheet run' : `${run.stages?.[0]?.label || 'Single'} stage run`; }
function runCompanyChips(run) {
  const seen = new Set();
  const rows = (run.jobs || []).filter(job => {
    const name = job.company || job.title;
    const key = companyKey(name);
    if (!key || seen.has(key)) return false;
    seen.add(key); return true;
  }).slice(0,3);
  return rows.length ? `<span class="run-item-companies">${rows.map((job,index) => { const name = job.company || job.title; return `<span class="run-company-chip">${companyAvatar(name,job.role_id,index)}<span>${e(name)}</span></span>`; }).join('')}</span>` : '';
}
function runJobs(run) {
  const jobs = run.jobs || [];
  if (!jobs.length) return '<section class="run-jobs"><h3 class="detail-section-title">Jobs in this run</h3><p class="run-no-data">No job-level shortlist or build receipt recorded.</p></section>';
  return `<section class="run-jobs"><h3 class="detail-section-title">Jobs in this run</h3>${jobs.map(job => `<article class="run-job">${companyAvatar(job.company || job.title || job.role_path,job.role_id,0)}<div><strong>${e(job.company || job.title || job.role_path || 'Saved role')}</strong><p>${e(job.title || job.role || '')}</p><small>${e(job.cv_built === true ? 'CV built' : job.cv_built === false ? 'CV not built' : 'CV result not recorded')}${job.duration_seconds != null ? ` · ${e(durationLabel(job.duration_seconds))}` : ''}</small></div>${badge(job.status || 'recorded', statusLabel(job.status || 'recorded'))}${job.role_id ? `<button class="icon-button" data-open-role="${e(job.role_id)}" aria-label="Open ${e(job.company || 'job')}">${icon('arrow')}</button>` : ''}</article>`).join('')}</section>`;
}
function durationLabel(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return 'Not recorded';
  const value = Math.round(seconds); const hours = Math.floor(value/3600); const minutes = Math.floor(value%3600/60);
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${value%60}s` : `${value}s`;
}
function usageLabel(usage) {
  if (typeof usage?.exact_tokens === 'number') return `${formatNumber(usage.exact_tokens)} tokens`;
  const tokens = usage?.exact_tokens || usage?.recorded_tokens;
  if (tokens && typeof tokens === 'object') {
    const counts = typeof tokens.total_tokens === 'number' ? `${formatNumber(tokens.total_tokens)} tokens` :
      [typeof tokens.input_tokens === 'number' ? `${formatNumber(tokens.input_tokens)} input` : '',
       typeof tokens.output_tokens === 'number' ? `${formatNumber(tokens.output_tokens)} output` : ''].filter(Boolean).join(' · ');
    if (counts) return `${counts}${usage.token_scope === 'thread_cumulative' ? ' · thread total' : usage.exact_tokens ? '' : ' · saved total'}`;
  }
  if (typeof usage?.account_delta_pp === 'number') return `~${usage.account_delta_pp.toFixed(2).replace(/\.00$/,'')} pp account change`;
  if (usage?.status === 'partial') return 'Saved per attempt';
  return 'Not recorded';
}
function observationText(item) { return typeof item === 'string' ? item : item?.message || item?.reason || item?.code || item?.label || 'Recorded issue'; }
function modelText(item) { return item?.model ? `${item.model}${item.effort ? ` · ${statusLabel(item.effort)}` : ''}${item.model_source === 'configured' || item.confirmed === false ? ' (configured)' : ''}` : 'Not recorded'; }
function attemptDetails(stage) {
  if (!stage.attempts?.length || stage.attempts.length < 2) return '';
  return `<details class="stage-evidence"><summary>${stage.attempts.length} worker receipts</summary><div class="table-scroll"><table class="build-history-table"><thead><tr><th>Worker</th><th>Time</th><th>Model</th><th>Usage</th><th>Results</th></tr></thead><tbody>${stage.attempts.map(attempt => `<tr><td><strong>${e(attempt.worker_id)}</strong><span>${e(statusLabel(attempt.status))}</span></td><td>${e(durationLabel(attempt.duration_seconds))}<span>${e(date(attempt.started_at,{hour:'2-digit',minute:'2-digit',second:'2-digit'}))}</span><span>${e(date(attempt.finished_at,{hour:'2-digit',minute:'2-digit',second:'2-digit'}))}</span></td><td>${e(modelText(attempt))}</td><td>${e(usageLabel(attempt.usage))}</td><td>${[attempt.ads_reviewed != null ? `${attempt.ads_reviewed} ads` : '',attempt.jobs_found != null ? `${attempt.jobs_found} jobs` : '',attempt.cvs_built != null ? `${attempt.cvs_built} CVs` : ''].filter(Boolean).map(e).join('<br>') || 'Not recorded'}</td></tr>`).join('')}</tbody></table></div></details>`;
}
function renderRunDetail() {
  if (state.runLoading) return loading('Opening the run…');
  const run = state.runDetail;
  if (!run) return empty('Choose a run.','See its steps, timing, results and failures.','clock');
  if (run.error) return errorState(run.error,'retry-run');
  const metrics = run.summary_metrics || {};
  const policy4 = run.policy_version === 4;
  const observation = livePipeline();
  const live = !runClosed(run) && observation?.run_id === run.id && !observation.finalized && (observation.active || observation.stale) ? observation : null;
  const recorded = run.stage_metrics || [];
  const runStages = fallbackStages.map(stage => {
    const saved = recorded.find(item => item.id === stage.id) || (run.stages || []).find(item => item.id === stage.id) || {id:stage.id,label:stage.label,status:'not_recorded'};
    const observed = live?.stages?.find(item => item.id === stage.id);
    return observed ? {...saved,status:observed.status,observed:true} : saved;
  });
  const totalTime = run.duration_seconds ?? run.timing?.duration_seconds;
  const failures = run.failures || [];
  const builds = run.builds || [];
  const summary = [
    [policy4 ? 'Accepted roles' : 'Shortlisted', metrics.jobs_shortlisted],
    ['CVs built', metrics.cvs_built],
    ['Total time', durationLabel(totalTime)],
    ['Ads reviewed', metrics.ads_reviewed],
  ];
  const proposalCount = policy4 ? metrics.jobs_proposed : metrics.jobs_found;
  const facts = [
    ['Started', date(run.started_at || run.date,{hour:'2-digit',minute:'2-digit',second:'2-digit'})],
    ['Finished', run.ended_at || run.finished_at || run.timing?.finished_at ? date(run.ended_at || run.finished_at || run.timing?.finished_at,{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not recorded'],
    ['Usage', usageLabel(run.usage)],
  ];
  if (policy4) {
    facts.push(['Jobs found', metrics.jobs_found ?? 'Not recorded']);
    facts.push(['Draft proposals', proposalCount ?? 'Not recorded']);
    facts.push(['CV Studio synced', metrics.roles_synced ?? 'Not recorded']);
  } else {
    facts.push(['Draft proposals', proposalCount ?? 'Not recorded']);
    if (metrics.rows_synced != null) facts.push(['Rows synced', metrics.rows_synced]);
  }
  return `<div class="run-detail-heading"><div class="run-report-heading"><h2>${e(runHeading(run))}</h2>${runBadge(run)}</div><p>${e(date(run.started_at || run.date,{hour:'2-digit',minute:'2-digit'}))}</p></div>${run.snapshot_warning ? `<p class="inline-notice">${icon('info')}${e(run.snapshot_warning)}</p>` : ''}<div class="run-summary-metrics">${summary.map(([label,value]) => `<div><span>${label}</span><strong class="${value == null || value === 'Not recorded' ? 'metric-missing' : ''}">${e(value ?? 'Not recorded')}</strong></div>`).join('')}</div><dl class="run-facts">${facts.map(([label,value]) => `<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`).join('')}</dl>${run.usage?.account_delta_pp != null ? `<p class="run-usage-note">Account change is approximate and can include other runs.${run.usage.window ? ` Window: ${e(typeof run.usage.window === 'string' ? run.usage.window : JSON.stringify(run.usage.window))}.` : ''}</p>` : ''}
    ${runJobs(run)}<h3 class="detail-section-title">Step results</h3><div class="run-stage-details">${runStages.map((stage,index) => `<details class="run-stage" ${stage.failures?.length || stage.status === 'running' ? 'open' : ''}><summary><span class="run-stage-number">${index+1}</span><span><strong>${e(stage.label || fallbackStages[index].label)}</strong><small>${e(modelText(stage))}</small></span><span class="run-stage-end">${badge(stage.status || 'not_recorded', statusLabel(stage.status || 'not_recorded'))}<small>${e(durationLabel(stage.duration_seconds ?? stage.elapsed_seconds))}${stage.duration_seconds == null && stage.elapsed_seconds != null ? ' elapsed' : ''}</small></span>${icon('down')}</summary><div class="run-stage-body"><dl class="run-facts"><div><dt>Started</dt><dd>${e(date(stage.started_at,{hour:'2-digit',minute:'2-digit',second:'2-digit'}))}</dd></div><div><dt>Finished</dt><dd>${e(date(stage.finished_at,{hour:'2-digit',minute:'2-digit',second:'2-digit'}))}</dd></div><div><dt>Usage</dt><dd>${e(usageLabel(stage.usage))}</dd></div><div><dt>Results</dt><dd>${runStageResults(run,stage)}</dd></div></dl>${stage.models?.length > 1 ? `<div class="stage-models">${stage.models.map(model => `<span>${e(modelText(typeof model === 'string' ? {model} : model))}</span>`).join('')}</div>` : ''}${stage.observations?.length ? `<ul class="run-observations">${stage.observations.map(item => `<li>${e(observationText(item))}</li>`).join('')}</ul>` : ''}${stage.failures?.length ? `<div class="run-failures">${stage.failures.map(item => `<p>${icon('alert')}${e(observationText(item))}</p>`).join('')}</div>` : ''}${attemptDetails(stage)}</div></details>`).join('')}</div>
    <h3 class="detail-section-title">CV builds</h3>${builds.length ? `<div class="table-scroll"><table class="build-history-table"><thead><tr><th>Job</th><th>Status</th><th>Build time</th><th>Model</th></tr></thead><tbody>${builds.map(build => `<tr><td class="build-company-cell">${companyAvatar(build.company || build.name || build.role || 'Role not recorded',build.role_id,0)}<span><strong>${e(build.company || build.name || build.role || 'Role not recorded')}</strong>${build.company ? `<span>${e(build.name || build.role || '')}</span>` : ''}</span></td><td>${badge(build.status || 'not_recorded', statusLabel(build.status || 'not_recorded'))}</td><td>${e(durationLabel(build.duration_seconds))}</td><td>${e(modelText(build))}</td></tr>`).join('')}</tbody></table></div>` : '<p class="run-no-data">No individual CV build receipts saved.</p>'}${failures.length ? `<h3 class="detail-section-title">Failures & holds</h3><div class="run-failures">${failures.map(item => `<p>${icon('alert')}${e(observationText(item))}</p>`).join('')}</div>` : ''}${run.observations?.length ? `<h3 class="detail-section-title">Observations</h3><ul class="run-observations">${run.observations.map(item => `<li>${e(observationText(item))}</li>`).join('')}</ul>` : ''}${run.reports?.length ? `<h3 class="detail-section-title">Saved reports</h3><div class="reports">${run.reports.map(report => `<details><summary>${icon('file')}<span>${e(report.name || report.title || 'Report')}</span>${icon('down')}</summary><div class="markdown report-content">${markdown(report.content)}</div></details>`).join('')}</div>` : ''}<details class="evidence-files"><summary>Run reference${icon('down')}</summary><p class="mono">${e(run.id)}</p>${run.files?.length ? `<ul>${run.files.map(file => `<li>${icon('file')}<span>${e(file.name)}</span></li>`).join('')}</ul>` : ''}</details>`;
}

function renderWorkspace() {
  return `${heading()}${observedBanner()}<section class="panel workspace-panel"><aside class="document-index"><div class="document-index-label">WORKSPACE DOCUMENTS</div>${state.errors.documents ? errorState(state.errors.documents) : state.documents.map(doc => `<button class="document-index-item ${state.doc?.id === doc.id ? 'selected' : ''}" data-document="${e(doc.id)}">${icon(doc.id === 'profile' ? 'book' : doc.id === 'search' ? 'search' : doc.id === 'routing' ? 'pipeline' : 'file')}<span><strong>${e(doc.title)}</strong><small>${doc.editable ? 'Editable document' : 'Reference · read only'}</small></span></button>`).join('')}</aside><div id="editor-container" class="editor-container">${renderEditor()}</div></section>`;
}
function renderEditor() {
  if (state.docLoading) return loading('Opening your document…');
  if (!state.doc) return empty('Choose a document.', '', 'book');
  const doc = state.doc; const locked = editingLocked(); const editable = doc.editable && !locked;
  return `<div class="editor-heading"><div><h2>${e(doc.title)}</h2><p>${e(doc.description || doc.path || '')}</p></div><span id="save-status" class="save-status ${dirty() ? 'unsaved' : ''}">${dirty() ? 'Unsaved changes' : doc.editable ? 'Saved locally' : 'Reference document'}</span></div>${state.docError ? `<div class="editor-error" role="alert">${icon('alert')}<span>${e(state.docError)}</span></div>` : ''}${locked && doc.editable ? `<div class="inline-notice">${icon('shield')}Editing is paused while the pipeline is active. You can still read the current document.</div>` : ''}${doc.protected_sections?.length ? `<div class="protected-note">${icon('shield')}<span>Protected: ${e(doc.protected_sections.map(section => typeof section === 'string' ? section : section.title || section.name || section.id).join(' · '))}</span></div>` : ''}<div class="editor-toolbar"><div class="segmented" role="group" aria-label="Document view">${(editable ? [['edit', 'Edit'], ['split', 'Split view'], ['preview', 'Preview']] : [['preview', 'Preview']]).map(([mode, label]) => `<button data-doc-mode="${mode}" class="${(editable ? state.docMode : 'preview') === mode ? 'selected' : ''}" aria-pressed="${(editable ? state.docMode : 'preview') === mode}">${label}</button>`).join('')}</div>${editable ? `<div class="editor-actions">${button('Revert', 'revert-document', 'refresh', 'button-ghost button-small', !dirty() ? 'disabled' : '')}${button('Save changes', 'save-document', 'save', 'button-primary button-small', !dirty() || state.saving ? 'disabled' : '')}</div>` : `<span class="subtle-label">Read only</span>`}</div><div class="editor-body editor-${editable ? state.docMode : 'preview'}">${editable ? `<div class="editor-source"><label class="sr-only" for="document-content">Edit ${e(doc.title)}</label><textarea id="document-content" spellcheck="false" aria-describedby="editor-word-count">${e(state.draft)}</textarea></div>` : ''}<div class="markdown editor-preview" id="document-preview">${doc.id === 'bank' ? `<pre><code>${e(state.draft)}</code></pre>` : markdown(state.draft)}</div></div><div class="editor-footer"><span id="editor-word-count">${state.draft.trim() ? state.draft.trim().split(/\s+/).length.toLocaleString() : 0} words</span><span>${editable ? '⌘ S to save · changes stay local' : e(doc.path || 'Canonical workspace reference')}</span></div>`;
}
function bindEditor() {
  const field = document.querySelector('#document-content');
  if (!field) return;
  field.addEventListener('input', () => {
    state.draft = field.value; state.docError = '';
    const status = document.querySelector('#save-status'); status.textContent = dirty() ? 'Unsaved changes' : 'Saved locally'; status.classList.toggle('unsaved', dirty());
    document.querySelector('[data-action="save-document"]').disabled = !dirty() || state.saving;
    document.querySelector('[data-action="revert-document"]').disabled = !dirty();
    document.querySelector('#editor-word-count').textContent = `${state.draft.trim() ? state.draft.trim().split(/\s+/).length.toLocaleString() : 0} words`;
    if (state.docMode !== 'edit') document.querySelector('#document-preview').innerHTML = markdown(state.draft);
  });
}

function runPreferences() { return state.settings.run || { model: 'gpt-6.1-sol', effort: 'high', defaults: {model:'gpt-6.1-sol',effort:'high'}, options: [] }; }
function modelOptions() {
  const preferences = runPreferences();
  const options = preferences.options || [];
  return options.length ? options : [{id:preferences.model,name:'Sol 6.1',efforts:[preferences.effort]}];
}
function effortOptions(model,effort) {
  const options = modelOptions().find(row => row.id === model)?.efforts || [effort];
  return options.map(value => `<option value="${e(value)}" ${value === effort ? 'selected' : ''}>${e(value === 'xhigh' ? 'Extra high' : statusLabel(value))}</option>`).join('');
}
function captureSettingsDraft() {
  if (state.route !== 'settings') return null;
  return Object.fromEntries(['preferences-form','run-settings-form'].map(id => [id,document.querySelector(`#${id}`) ? Object.fromEntries(new FormData(document.querySelector(`#${id}`))) : null]));
}
function restoreSettingsDraft(drafts) {
  if (!drafts || state.route !== 'settings') return;
  for (const [id,draft] of Object.entries(drafts)) {
    const form = document.querySelector(`#${id}`); if (!form || !draft) continue;
    if (id === 'run-settings-form') {
      if ([...form.elements.model.options].some(option => option.value === draft.model)) form.elements.model.value = draft.model;
      form.elements.effort.innerHTML = effortOptions(form.elements.model.value,draft.effort);
    } else {
      for (const [key,value] of Object.entries(draft)) {
        if (key === 'theme') form.querySelector(`[name="theme"][value="${CSS.escape(value)}"]`).checked = true;
        else form.elements[key].value = value;
      }
      applySettings(draft);
    }
  }
}
function renderSettings() {
  const settings = state.settings; const run = runPreferences(); const caps = capabilities();
  const schedule = settings.schedule || state.overview?.schedule;
  return `${heading()}<div class="settings-layout"><div class="settings-main"><section class="panel preferences-panel" aria-labelledby="pipeline-models-title"><div class="section-heading"><h2 id="pipeline-models-title">Pipeline models</h2></div><p class="setting-note">Who does each step of the job-feed pipeline.</p><div class="setting-row"><span>Screening</span><strong>Claude Sonnet 5.5</strong></div><div class="setting-row"><span>First CV build</span><strong>GitHub Copilot (GPT-5.6 Luna)</strong></div><div class="setting-row"><span>Build fallback and fixes</span><strong>Claude Haiku 5.5 · medium</strong></div><div class="setting-row"><span>Build audit</span><strong>Claude Sonnet 5.5</strong></div><div class="setting-row"><span>Final approval</span><strong>Claude Sonnet 5.5</strong></div><div class="setting-row"><span>Studio sync and provenance</span><strong>Pipeline script (no model)</strong></div></section>
    <form id="preferences-form" class="panel preferences-panel"><div class="section-heading"><h2>Appearance</h2>${icon('sun')}</div><div class="theme-options">${[['light','Light','sun'],['dark','Dark','moon'],['system','System','monitor']].map(([value,label,symbol]) => `<label class="theme-option"><input type="radio" name="theme" value="${value}" ${settings.theme === value ? 'checked' : ''}><span class="theme-swatch swatch-${value}"><span></span><i></i></span><span class="theme-name">${icon(symbol)}${label}<span class="choice-check">${icon('check')}</span></span></label>`).join('')}</div><div class="setting-row"><label for="density-setting">Spacing</label><select id="density-setting" name="density" aria-label="Display density"><option value="comfortable" ${settings.density === 'comfortable' ? 'selected' : ''}>Comfortable</option><option value="compact" ${settings.density === 'compact' ? 'selected' : ''}>Compact</option></select></div><div class="setting-row"><label for="refresh-setting">Refresh</label><select id="refresh-setting" name="refresh_seconds" aria-label="Workspace refresh interval">${[[5,'Every 5 seconds'],[15,'Every 15 seconds'],[30,'Every 30 seconds'],[60,'Every minute']].map(([value,label]) => `<option value="${value}" ${Number(settings.refresh_seconds) === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="settings-save"><span id="settings-save-status"></span><button type="submit" class="button button-primary">${icon('check')}Save appearance</button></div></form></div>
    <div class="settings-side"><section class="panel connections-panel"><div class="section-heading"><h2>Connections</h2>${button(caps.checking ? 'Checking…' : 'Check','check-connections','refresh','button-secondary button-small',caps.checking ? 'disabled' : '')}</div><div class="connection-items"><div><span>${icon('mail')}Gmail</span>${badge(caps.connectors?.gmail?.callable && caps.connectors?.gmail?.tools_available ? 'available' : 'not_checked',caps.connectors?.gmail?.callable && caps.connectors?.gmail?.tools_available ? 'Connected' : caps.checked_at ? 'Unavailable' : 'Not checked')}</div><div><span>${icon('file')}CV Studio sync</span>${badge(caps.pipeline_dependencies?.sync?.available ? 'available' : 'not_checked',caps.pipeline_dependencies?.sync?.available ? 'Local' : 'Not checked')}</div></div>${caps.blocked_reason && !activePipeline().active ? `<p class="setting-note">${e(caps.blocked_reason)}</p>` : ''}</section><section class="panel settings-reference"><h2>Schedule</h2>${schedule?.available ? `<div class="schedule-reference">${icon('clock')}<div><strong>${e(schedule.label)}</strong><span>${e(statusLabel(schedule.status))}</span></div></div>` : '<p class="muted">No schedule saved.</p>'}${automationButton()}</section><section class="panel settings-reference"><h2>Profile & search</h2><div class="settings-links">${navLink('workspace','Edit profile and search')}${navLink('activity','Run history')}<button class="text-link" data-action="open-routing">View workflow rules${icon('arrow')}</button>${button('Check local files','start-check','shield','button-secondary button-small',checkDisabled() ? 'disabled' : '')}</div></section></div></div>`;
}

async function refreshJdInsights(force = false) {
  if (state.jdInsightsLoading || (!force && state.jdInsightsRefreshedAt !== null && performance.now() - state.jdInsightsRefreshedAt < 60000)) return;
  state.jdInsightsLoading = true;
  try { state.jdInsights = await api.jdInsights(); delete state.errors.jdInsights; }
  catch (error) { state.errors.jdInsights = error.message; }
  finally { state.jdInsightsLoading = false; state.jdInsightsRefreshedAt = performance.now(); }
  if (state.loaded && state.route === 'overview') render();
}
function overviewCatalogs(overview) {
  const catalogs = overview?.catalogs;
  const catalogValid = key => Array.isArray(catalogs?.[key]?.items) && catalogs[key].items.every(row =>
    row && typeof row === 'object' && !Array.isArray(row) && typeof row.id === 'string' && row.id.length > 0);
  if (catalogs?.version !== 1 || !['roles', 'runs', 'documents'].every(catalogValid) ||
      !catalogs.settings || typeof catalogs.settings !== 'object' || Array.isArray(catalogs.settings) ||
      !catalogs.roles.items.every(role => role.application && typeof role.application.status === 'string')) return null;
  return catalogs;
}
async function refresh({ quiet = false } = {}) {
  if (state.route === 'prospect') {
    render();
    const available = quiet || await refreshProspect(document.querySelector('#main'));
    scheduleRefresh();
    return available;
  }
  if (state.route === 'audits' && !quiet) loadAudits();
  if (state.refreshing) return;
  // Hidden windows need no repeated catalog scans. Resume when visible again.
  if (quiet && document.hidden) { scheduleRefresh(); return; }
  state.refreshing = true;
  if (!state.overview) { state.loaded = false; render(); }
  else updateShell();
  const beforeLock = editingLocked(); const beforeChecking = capabilities().checking;
  const settingsDraft = captureSettingsDraft();
  const applicationEpoch = state.applicationEpoch;
  // A versioned overview shares the same role/run scan with the Home catalogs.
  // Older servers keep the sequential role fallback, avoiding overlapping scans.
  let freshCatalogs = null;
  const overviewRequest = api.overview({ catalogs: true }).then(overview => {
    freshCatalogs = overviewCatalogs(overview);
    return overview;
  });
  const requests = [overviewRequest,
    overviewRequest.then(() => freshCatalogs?.roles || api.roles()),
    overviewRequest.then(() => freshCatalogs?.runs || api.runs(), () => api.runs()),
    overviewRequest.then(() => freshCatalogs?.settings || api.settings(), () => api.settings()),
    api.jobs(), api.applyOptions().catch(() => null)];
  const keys = ['overview', 'roles', 'runs', 'settings', 'jobs', 'applyOptions'];
  const receive = async (requests, keys) => {
    const results = await Promise.allSettled(requests);
    results.forEach((result, index) => {
      const key = keys[index];
      if (key === 'contactsIndex') state.contactsIndexLoaded = true;
      if (result.status === 'fulfilled') {
        delete state.errors[key];
        if (['roles', 'runs', 'documents', 'jobs', 'companyLogos'].includes(key)) state[key] = items(result.value);
        else if (key === 'manualJobs') { state.manualJobs = items(result.value); state.manualJobsLoaded = true; }
        else if (key === 'contactsIndex') state.savedContacts = items(result.value);
        else state[key] = result.value;
        if (key === 'companyLogos') state.companyLogosLoaded = true;
        if (key === 'roles') state.roles = state.roles.map(role => { const saved = state.applicationUpdates.get(role.id); return saved?.epoch > applicationEpoch ? { ...role, application:saved.application } : role; });
      }
      else {
        if (key === 'companyLogos') { state.companyLogos = []; state.companyLogosLoaded = true; return; }
        if (key === 'manualJobs') state.manualJobsLoaded = true;
        state.errors[key] = result.reason.message;
        if (key === 'overview' && state.overview?.live_pipeline) state.overview.live_pipeline = { ...state.overview.live_pipeline, stale: true, summary: 'Live updates unavailable. Showing the last update.' };
      }
    });
  };
  await receive(requests, keys);
  if (state.route === 'overview') {
    state.loaded = true;
    applySettings();
    render();
  }
  // A genuine cold-load failure must leave Retry available immediately.
  if (state.overview) {
    const ancillary = [freshCatalogs?.documents || api.documents(), api.manualJobs(), api.contactsIndex()];
    const ancillaryKeys = ['documents', 'manualJobs', 'contactsIndex'];
    if (!state.companyLogosLoaded) { ancillary.push(api.companyLogos()); ancillaryKeys.push('companyLogos'); }
    await receive(ancillary, ancillaryKeys);
  }
  state.loaded = true; state.refreshing = false; if (!(quiet && state.route === 'settings')) applySettings();
  if (!state.selectedJob && state.jobs.length) state.selectedJob = state.jobs.find(job => runningStatuses.has(job.status))?.id || state.jobs[0].id;
  if (state.selectedJob) await loadJob(state.selectedJob, true);
  if (quiet && state.route === 'activity') {
    const list = document.querySelector('#run-list'); const listScroll = list?.scrollTop || 0;
    if (list) { list.innerHTML = renderRunList(); list.scrollTop = listScroll; }
    if (state.selectedRun && !state.runLoading) {
      const id = state.selectedRun;
      const opened = [...document.querySelectorAll('#run-detail details')].map((node,index) => node.open ? index : -1).filter(index => index >= 0);
      try { const detail = await api.run(id); if (state.selectedRun === id && state.route === 'activity') { state.runDetail = detail; const node = document.querySelector('#run-detail'); node.innerHTML = renderRunDetail(); opened.forEach(index => { const entry = node.querySelectorAll('details')[index]; if (entry) entry.open = true; }); } }
      catch (error) { toast(error.message,'error'); }
    }
  }
  if (!quiet || !['workspace', 'settings', 'library', 'activity', 'apply', 'contacts'].includes(state.route)) render();
  else { updateShell(); refreshLiveHeader(); if (state.route === 'apply' && !state.applicationSaving.size && !document.activeElement?.closest('.application-row')) updateApply(); if (state.route === 'contacts') { const results = document.querySelector('#saved-contact-results'); if (results) results.innerHTML = renderSavedContactGroups(); } if (state.route === 'activity') { const jobsPanel = document.querySelector('#studio-jobs-panel'); const nextPanel = renderStudioJobsPanel(); if (jobsPanel && nextPanel) jobsPanel.outerHTML = nextPanel; else if (jobsPanel) jobsPanel.remove(); else if (nextPanel) document.querySelector('.page-activity .page-heading')?.insertAdjacentHTML('afterend',nextPanel); } if (state.route === 'workspace' && beforeLock !== editingLocked()) { document.querySelector('#editor-container').innerHTML = renderEditor(); bindEditor(); } }
  if (quiet && state.route === 'settings' && (beforeChecking || capabilities().checking)) {
    render();
  }
  updateContactNotifications();
  restoreSettingsDraft(settingsDraft);
  scheduleRefresh();
  if (state.route === 'workspace' && !state.doc && state.documents.length) await openDocument(state.documents[0].id, false);
  return !Object.keys(state.errors).length;
}
function scheduleRefresh() {
  clearTimeout(state.timer);
  const seconds = Number(state.settings.refresh_seconds);
  const running = state.jobs.some(job => runningStatuses.has(job.status)) || liveShown(livePipeline());
  const checking = capabilities().checking || state.overview?.dashboard_analytics_refreshing;
  if (running || checking || seconds > 0) state.timer = setTimeout(() => refresh({ quiet: true }), running || checking ? Math.min(seconds > 0 ? seconds : 5, 5) * 1000 : seconds * 1000);
}
async function refreshJobs() {
  try { state.jobs = items(await api.jobs()); delete state.errors.jobs; if (!state.selectedJob && state.jobs.length) state.selectedJob = state.jobs[0].id; if (state.selectedJob) await loadJob(state.selectedJob, true); }
  catch (error) { state.errors.jobs = error.message; }
  if (state.route === 'activity') render(); scheduleRefresh();
}
async function loadJob(id, quiet = false) {
  state.selectedJob = id;
  try { const job = await api.job(id); if (state.selectedJob === id) { const before = state.job?.status; state.job = job; if (before && before !== job.status) announce(`${job.label || 'Process'}: ${jobStatus(job)}`); } }
  catch (error) { state.errors.jobs = error.message; }
  if (!quiet && state.route === 'activity') render();
}
async function navigate(route, options = {}) {
  if (!Object.hasOwn(pages, route)) route = 'overview';
  if (route === state.route && !options.force) { setNavigation(false, true); return; }
  if (dirty() && state.route === 'workspace' && !options.discard) { askDiscard(() => { state.draft = state.doc.content; navigate(route, { discard: true, force: true }); }); return; }
  if (state.route === 'settings' && route !== 'settings') applySettings();
  state.route = route; history.replaceState(null, '', `#${route}`); render();
  setNavigation(false);
  if (!options.noFocus) { document.querySelector('#main').focus({ preventScroll: true }); window.scrollTo({ top: 0 }); }
  if (route === 'workspace' && !state.doc && !state.docLoading && state.documents.length) await openDocument(state.documents[0].id, false);
  if (route === 'activity' && !state.selectedRun && state.runs.length) await selectRun(state.runs[0].id);
  announce(`${pages[route].name} opened`);
  if (route === 'audits' && state.audits === undefined) await loadAudits();
  if (route !== 'prospect' && !state.loaded && !state.refreshing) await refresh();
}
function askDiscard(callback) {
  state.navigation = callback;
  showAction(`<div class="dialog-symbol">${icon('edit')}</div><p class="eyebrow">UNSAVED WORDS</p><h2 id="action-title">Keep your changes?</h2><p>You have edits that have not been saved. Stay here to save them, or discard them and continue.</p><div class="dialog-actions">${button('Keep editing', 'close-action', '', 'button-secondary')}${button('Discard changes', 'confirm-discard', '', 'button-danger')}</div>`);
}
function showAction(content) { actionDialog.innerHTML = `<button class="dialog-close icon-button" data-action="close-action" aria-label="Close dialog">${icon('close')}</button>${content}`; if (!actionDialog.open) actionDialog.showModal(); }

let roleRequestSequence = 0;
async function openRole(id) {
  const sequence = ++roleRequestSequence;
  state.dialogRole = id; detailDialog.innerHTML = `<div class="drawer-header"><p class="eyebrow" id="detail-title">YOUR CV PACKAGE</p><button class="icon-button" data-action="close-detail" aria-label="Close CV package">${icon('close')}</button></div><div class="drawer-body">${loading('Opening the CV package…')}</div>`;
  if (!detailDialog.open) detailDialog.showModal();
  try { const role = await api.role(id); if (sequence !== roleRequestSequence || !detailDialog.open) return; state.activeArtifact = null; renderRoleDrawer(role); }
  catch (error) { if (sequence !== roleRequestSequence || !detailDialog.open) return; detailDialog.innerHTML = `<div class="drawer-header"><h2 id="detail-title">CV package</h2><button class="icon-button" data-action="close-detail" aria-label="Close CV package">${icon('close')}</button></div><div class="drawer-body">${errorState(error.message, 'retry-role')}</div>`; }
}
function renderRoleDrawer(role) {
  const restoreArtifactFocus = document.activeElement?.id === 'artifact-select';
  state.roleDetail = role;
  const artifacts = roleArtifacts(role).filter(artifact => safeUrl(artifact.url));
  const pdfs = artifacts.filter(artifact => /\.pdf(?:\?|$)/i.test(artifact.name || '') || /\.pdf(?:\?|$)/i.test(artifact.url || '') || artifact.kind === 'pdf' || artifact.type === 'pdf' || artifact.mime_type === 'application/pdf');
  const active = artifacts.find(artifact => artifact.key === state.activeArtifact) || pdfs[0] || null;
  state.activeArtifact = active?.key;
  const source = safeUrl(role.source_url || role.url);
  detailDialog.innerHTML = `<div class="drawer-header"><p class="eyebrow">YOUR CV PACKAGE</p><button class="icon-button" data-action="close-detail" aria-label="Close CV package">${icon('close')}</button></div><div class="drawer-body"><div class="drawer-role-heading">${companyAvatar(roleCompany(role),role.id,0)}<div><span class="company-label">${e(roleCompany(role))}</span><h2 id="detail-title">${e(roleTitle(role))}</h2></div></div><div class="drawer-meta">${roleBadge(role)}${role.location ? `<span>${e(role.location)}</span>` : ''}${role.contract ? `<span>${e(role.contract)}</span>` : ''}${role.language ? `<span>${e(role.language)}</span>` : ''}</div><p class="drawer-updated">Updated ${e(date(roleDate(role)))}${role.fit ? ` · Fit ${e(role.fit)}` : ''}</p>${approvalBar(state.roles.find(item => item.id === role.id) || role)}${buildReviewNotice(role)}${artifacts.length ? `<div class="artifact-list">${artifacts.map(artifact => `<a class="artifact-link" href="${e(safeUrl(artifact.url))}" download><span>${icon('file')}<span><strong>${e(artifact.label || artifact.name || 'Document')}</strong><small>${e(artifact.name || '')}</small></span></span>${icon('download')}</a>`).join('')}</div>` : `<div class="inline-notice">${icon('info')}This package has no downloadable artifacts.</div>`}${pdfs.length ? `<section class="pdf-section"><div class="pdf-toolbar"><h3>Document preview</h3><select id="artifact-select" aria-label="Choose document preview">${pdfs.map(artifact => `<option value="${e(artifact.key)}" ${active?.key === artifact.key ? 'selected' : ''}>${e(artifact.label || artifact.name)}</option>`).join('')}</select><a class="icon-button" href="${e(safeUrl(active.url))}" target="_blank" rel="noopener noreferrer" aria-label="Open preview in a new tab">${icon('upRight')}</a></div><iframe class="pdf-preview" src="${e(safeUrl(active.url))}#toolbar=0&navpanes=0" title="${e(active.label || active.name || 'CV PDF preview')}"></iframe><p class="pdf-hint">If the preview is unavailable, open or download the document above.</p></section>` : ''}${source ? `<a class="source-ad-link" href="${e(source)}" target="_blank" rel="noopener noreferrer">${icon('globe')}Open original job advert${icon('upRight')}</a>` : ''}${role.jd ? `<details class="drawer-details" ${!pdfs.length ? 'open' : ''}><summary>Job description${icon('down')}</summary><div class="markdown">${markdown(typeof role.jd === 'string' ? role.jd : role.jd.content || '')}</div></details>` : ''}${role.documents?.map(doc => `<details class="drawer-details"><summary>${e(doc.title || doc.name)}${icon('down')}</summary><div class="markdown">${markdown(doc.content)}</div></details>`).join('') || ''}</div>`;
  if (detailDialog.open) detailDialog.querySelector(restoreArtifactFocus ? '#artifact-select' : '[data-action="close-detail"]')?.focus({ preventScroll: true });
}
async function selectRun(id) {
  state.selectedRun = id; state.runLoading = true;
  if (state.route !== 'activity') await navigate('activity');
  else { document.querySelector('#run-list').innerHTML = renderRunList(); document.querySelector('#run-detail').innerHTML = renderRunDetail(); }
  try { const run = await api.run(id); if (state.selectedRun === id) state.runDetail = run; }
  catch (error) { if (state.selectedRun === id) state.runDetail = { error: error.message }; }
  if (state.selectedRun === id) { state.runLoading = false; if (state.route === 'activity') document.querySelector('#run-detail').innerHTML = renderRunDetail(); }
}
async function openDocument(id, confirm = true) {
  if (dirty() && confirm) { askDiscard(() => { state.draft = state.doc.content; openDocument(id, false); }); return; }
  const sequence = ++state.documentSequence;
  state.docLoading = true; state.docError = '';
  if (state.route !== 'workspace') await navigate('workspace');
  else { document.querySelector('#editor-container').innerHTML = renderEditor(); }
  try { const doc = await api.document(id); if (sequence !== state.documentSequence) return; state.doc = doc; state.draft = doc.content ?? ''; }
  catch (error) { if (sequence !== state.documentSequence) return; state.doc = null; toast(error.message, 'error'); }
  if (sequence !== state.documentSequence) return;
  state.docLoading = false; if (state.route === 'workspace') render();
}
async function saveDocument() {
  if (!state.doc || !dirty() || !state.doc.editable || state.saving) return;
  if (editingLocked()) { toast('Editing is paused while the pipeline is active.', 'error'); return; }
  const submitted = { id: state.doc.id, draft: state.draft, revision: state.doc.revision };
  state.saving = true; const submit = document.querySelector('[data-action="save-document"]'); if (submit) submit.disabled = true;
  try {
    const response = await api.saveDocument(submitted.id, submitted.draft, submitted.revision);
    if (state.doc?.id === submitted.id) { const newerDraft = state.draft !== submitted.draft; state.doc = { ...state.doc, ...response, content: response.content ?? submitted.draft }; if (!newerDraft) state.draft = state.doc.content; state.docError = ''; }
    toast('Changes saved to your workspace.');
  } catch (error) { if (state.doc?.id === submitted.id) state.docError = error.status === 409 ? `${error.message} Your edits are preserved here. Copy them before reloading the latest document.` : error.message; toast('The document was not saved. Your edits are still here.', 'error'); }
  state.saving = false; if (state.route === 'workspace') {
    const editing = document.activeElement?.id === 'document-content'; const selection = editing ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    document.querySelector('#editor-container').innerHTML = renderEditor(); bindEditor();
    if (editing) { const field = document.querySelector('#document-content'); field?.focus({ preventScroll: true }); field?.setSelectionRange(...selection); }
  }
}

async function startJob(kind) {
  if (kind === 'pipeline') {
    try { state.overview = await api.overview(); } catch (error) { toast(error.message, 'error'); return; }
    if (runDisabled()) { toast(activePipeline().reason || 'A pipeline or Studio process is already active.', 'error'); render(); return; }
    showAction(`<div class="dialog-symbol">${icon('pipeline')}</div><h2 id="action-title">Start automation?</h2><p>${e(capabilities().coordinator?.model || runPreferences().model)} · ${e(statusLabel(capabilities().coordinator?.effort || runPreferences().effort))}</p><div class="dialog-actions">${button('Cancel','close-action','','button-secondary')}${button('Start automation','confirm-pipeline','play','button-primary')}</div>`);
    return;
  }
  if (checkDisabled()) { toast(capabilities().blocked_reason || 'New local checks are paused while other work is active.', 'error'); return; }
  await dispatchJob(kind);
}
async function dispatchJob(kind) {
  actionDialog.close();
  try { state.showStudioActivity = true; const job = await api.startJob(kind); state.jobs = [job, ...state.jobs.filter(item => item.id !== job.id)]; state.job = job; state.selectedJob = job.id; await navigate('activity', { force: true }); toast(`${job.label || (kind === 'check' ? 'Local check' : 'Pipeline')} started.`); scheduleRefresh(); }
  catch (error) { toast(error.message, 'error'); await refresh({ quiet: true }); }
}
async function stopJob() {
  if (!state.job || !runningStatuses.has(state.job.status)) return;
  state.stopTarget = {kind:'job',id:state.job.id};
  showStopDialog(state.job.label || 'Studio run');
}
function showStopDialog(label) {
  showAction(`<div class="dialog-symbol">${icon('stop')}</div><h2 id="action-title">Stop this run?</h2><p>${e(label)}. Saved work stays available.</p><div class="dialog-actions">${button('Keep running','close-action','','button-secondary')}${button('Stop run','confirm-stop','stop','button-danger')}</div>`);
}
async function stopRun() {
  const live = currentRun(); if (!live?.active && !(live?.finalized && live.admission_blocked)) return;
  if (live.job_id) state.stopTarget = {kind:'job',id:live.job_id};
  else {
    const identity = state.overview?.runtime?.external_control?.safe_identity;
    if (!identity || identity.thread_id !== live.thread_id || identity.current_turn_id !== live.current_turn_id) { toast('Refresh to verify the active run before stopping it.','error'); return; }
    state.stopTarget = {kind:'external',identity:{...identity}};
  }
  showStopDialog(live.finalized ? 'The run already finished; this stops its chats that are still open' : live.title || 'CV pipeline');
}
async function confirmStop() {
  const target = state.stopTarget; state.stopTarget = null; actionDialog.close(); if (!target) return;
  try {
    if (target.kind === 'external') await api.stopExternalRun(target.identity);
    else await api.stopJob(target.id);
    await refresh({quiet:true}); if (state.route === 'activity') render();
    toast('Stop requested. Saved work stays available.');
  } catch (error) { toast(error.message,'error'); }
}

function showJobRequest(index) {
  const request = state.job?.pending_requests?.[index]; if (!request) return;
  state.currentRequest = request;
  const kind = request.answer_kind || '';
  const questions = request.questions || request.params?.questions;
  const approval = /approval|decision|permissions/.test(kind) || /approval/i.test(request.method || '');
  const allowed = request.params?.availableDecisions || ['accept', 'decline', 'cancel'];
  const connectorUrl = safeUrl(request.params?.url);
  let form;
  if (approval) form = `${request.params?.permissions ? `<pre class="request-command">${e(JSON.stringify(request.params.permissions, null, 2))}</pre><p class="input-help">Approval applies only to these requested permissions for this turn.</p>` : ''}<div class="dialog-actions">${allowed.includes('decline') ? '<button class="button button-secondary" data-approval="decline">Decline</button>' : ''}${allowed.includes('cancel') ? '<button class="button button-secondary" data-approval="cancel">Cancel request</button>' : ''}${allowed.includes('accept') ? '<button class="button button-primary" data-approval="accept">Approve request</button>' : ''}</div>`;
  else if (kind === 'elicitation') form = `<form id="connector-input-form">${connectorUrl.startsWith('https:') ? `<a class="source-ad-link" href="${e(connectorUrl)}" target="_blank" rel="noopener noreferrer">${icon('link')}Open connector request${icon('upRight')}</a>` : ''}${request.params?.requestedSchema ? `<details class="drawer-details"><summary>Requested response format${icon('down')}</summary><pre class="request-command">${e(JSON.stringify(request.params.requestedSchema, null, 2))}</pre></details><div class="input-question"><label for="connector-answer">Connector response (JSON)</label><textarea id="connector-answer" name="content" rows="6" placeholder="{}" required></textarea></div>` : ''}<div class="dialog-actions"><button type="button" class="button button-secondary" data-connector-decision="decline">Decline</button><button type="button" class="button button-secondary" data-connector-decision="cancel">Cancel request</button><button class="button button-primary" type="submit">Submit response</button></div></form>`;
  else if (Array.isArray(questions) && questions.length) form = `<form id="job-input-form">${questions.map((question, index) => `<div class="input-question"><label for="answer-${index}">${e(question.question || question.header || question.label || 'Your answer')}</label>${Array.isArray(question.options) && question.options.length ? `<select id="answer-${index}" name="${e(question.id || String(index))}"><option value="">Choose an answer</option>${question.options.map(option => `<option value="${e(typeof option === 'string' ? option : option.label || option.value)}">${e(typeof option === 'string' ? option : option.label || option.value)}</option>`).join('')}</select><div class="question-options">${question.options.filter(option => typeof option === 'object' && option.description).map(option => `<p><strong>${e(option.label)}:</strong> ${e(option.description)}</p>`).join('')}</div>${question.isOther !== false ? `<input type="${question.isSecret ? 'password' : 'text'}" name="custom_${e(question.id || String(index))}" placeholder="Or type your own answer" aria-label="Custom answer for ${e(question.header || question.question || 'question')}">` : ''}` : question.isSecret ? `<input type="password" id="answer-${index}" name="${e(question.id || String(index))}" required autocomplete="off">` : `<textarea id="answer-${index}" name="${e(question.id || String(index))}" rows="3" required></textarea>`}</div>`).join('')}<div class="dialog-actions"><button type="button" class="button button-secondary" data-action="close-action">Close</button><button class="button button-primary" type="submit">Send answer${icon('arrow')}</button></div></form>`;
  else form = `<div class="inline-notice">${icon('info')}This request cannot be answered safely in Studio. Stop this process and inspect its saved output before continuing.</div><div class="dialog-actions">${button('Close', 'close-action', '', 'button-secondary')}${button('Stop this process', 'stop-job', 'stop', 'button-danger')}</div>`;
  showAction(`<div class="dialog-symbol">${icon('info')}</div><p class="eyebrow">YOUR INPUT IS NEEDED</p><h2 id="action-title">${e(request.title || 'Review the request')}</h2><p>${e(request.description || '')}</p>${request.params?.command ? `<pre class="request-command">${e(typeof request.params.command === 'string' ? request.params.command : JSON.stringify(request.params.command, null, 2))}</pre>` : ''}${form}`);
}
async function sendJobAnswer(answer) {
  try { await api.jobInput(state.job.id, { request_id: state.currentRequest.id, answer }); actionDialog.close(); toast('Your response was sent to the process.'); await refreshJobs(); }
  catch (error) { toast(error.message, 'error'); }
}
function commandPalette() {
  showAction(`<p class="eyebrow">A SHORT WAY THERE</p><h2 id="action-title">Where would you like to go?</h2><div class="command-search search-field">${icon('search')}<input id="command-search" type="search" placeholder="Find a page or search your CVs" aria-label="Find a page or search your CVs" autofocus></div><div id="command-results" class="command-results">${commandResults('')}</div><p class="command-hint">Type to find a page. Press Enter to search your library.</p>`);
  document.querySelector('#command-search').focus();
}
function commandResults(query) { return Object.entries(pages).filter(([, page]) => page.name.toLowerCase().includes(query.toLowerCase())).map(([id, page]) => `<button data-command-nav="${id}">${icon(page.icon)}<span>${page.name}</span>${icon('arrow')}</button>`).join('') || `<button data-command-search="true">${icon('search')}<span>Search CV library for “${e(query)}”</span>${icon('arrow')}</button>`; }

function clearApplyDrilldown() {
  state.applyQuery = '';
  state.applyFilter = 'all';
  ['applyLanguage','applyDocuments','applyFit','applyContract','applySource'].forEach(key => { state[key] = 'all'; });
  state.applySort = 'fit';
}

document.addEventListener('click', async event => {
  if (event.target.closest('.skip-link')) {
    event.preventDefault();
    document.querySelector('#main').focus();
    return;
  }
  const homeLibraryFilter = event.target.closest('[data-home-library-filter]');
  if (homeLibraryFilter) {
    event.preventDefault();
    state.query = '';
    state.filter = homeLibraryFilter.dataset.homeLibraryFilter;
    state.sort = 'newest';
    await navigate('library', { force: true });
    return;
  }
  const timeframe = event.target.closest('[data-home-timeframe]');
  if (timeframe) { state.homeTimeframe = timeframe.dataset.homeTimeframe; render(); return; }
  const jdCategory = event.target.closest('[data-jd-category]');
  if (jdCategory && jdCategory.tagName !== 'SELECT') { state.jdCategory = jdCategory.dataset.jdCategory; render(); return; }
  const jdTerm = event.target.closest('[data-jd-term]');
  if (jdTerm && jdTerm.tagName !== 'INPUT' && jdTerm.tagName !== 'SELECT') { state.jdTerm = jdTerm.dataset.jdTerm; render(); return; }
  const applyPick = event.target.closest('[data-apply-select]');
  if (applyPick) { selectApplyRole(applyPick.dataset.applySelect); return; }
  const nav = event.target.closest('[data-nav]');
  if (nav) { event.preventDefault(); await navigate(nav.dataset.nav); return; }
  const target = event.target.closest('button, [data-action]'); if (!target) return;
  if (target.disabled) return;
  if (target.dataset.openRole) { await openRole(target.dataset.openRole); return; }
  if (target.dataset.openRun) { await selectRun(target.dataset.openRun); return; }
  if (target.dataset.selectRun) { await selectRun(target.dataset.selectRun); return; }
  if (target.dataset.languageFilter) { if (state.route !== 'apply') clearApplyDrilldown(); state.applyLanguage = target.dataset.languageFilter; if (state.route === 'apply') updateApply(); else await navigate('apply',{force:true}); return; }
  if (target.dataset.applySource) { if (state.route !== 'apply') clearApplyDrilldown(); state.applySource = target.dataset.applySource; if (state.route === 'apply') updateApply(); else await navigate('apply',{force:true}); return; }
  if (target.dataset.applyFit) { if (state.route !== 'apply') clearApplyDrilldown(); state.applyFit = target.dataset.applyFit; if (state.route === 'apply') updateApply(); else await navigate('apply',{force:true}); return; }
  if (target.dataset.stage) { state.stage = target.dataset.stage; if (state.route !== 'activity') await navigate('activity'); else render(); return; }
  if (target.dataset.filter) { state.filter = target.dataset.filter; updateLibrary(); return; }
  if (target.dataset.applyFilter) { if (state.route !== 'apply') clearApplyDrilldown(); state.applyFilter = target.dataset.applyFilter; if (state.route === 'apply') updateApply(); else await navigate('apply',{force:true}); return; }
  if (target.dataset.document) { await openDocument(target.dataset.document); return; }
  if (target.dataset.docMode) { state.docMode = target.dataset.docMode; document.querySelector('#editor-container').innerHTML = renderEditor(); bindEditor(); return; }
  if (target.dataset.jobRequest !== undefined) { showJobRequest(Number(target.dataset.jobRequest)); return; }
  if (target.dataset.approval) { await sendJobAnswer({ decision: target.dataset.approval }); return; }
  if (target.dataset.connectorDecision) { await sendJobAnswer({ action: target.dataset.connectorDecision }); return; }
  if (target.dataset.commandNav) { actionDialog.close(); await navigate(target.dataset.commandNav); return; }
  if (target.dataset.commandSearch) { state.query = document.querySelector('#command-search').value; state.filter = 'all'; actionDialog.close(); await navigate('library', { force: true }); return; }
  const action = target.dataset.action;
  if (action === 'refresh') { const refreshed = await refresh(); toast(refreshed ? 'Workspace refreshed.' : 'Some workspace data could not be refreshed. Please review the notices.', refreshed ? 'success' : 'error'); }
  else if (action === 'go-pipeline') await navigate('activity');
  else if (action === 'toggle-nav') setNavigation(!document.querySelector('.studio-shell').classList.contains('nav-open'), true);
  else if (action === 'close-nav') setNavigation(false, true);
  else if (action === 'dismiss-toast') target.closest('.toast')?.remove();
  else if (action === 'dismiss-contact-notice') dismissContactNotice(target.dataset.contactNoticeId);
  else if (action === 'view-contact-search') { state.contactsRoleFilter = target.dataset.roleId; state.contactsQuery = ''; await navigate('contacts'); }
  else if (action === 'toggle-theme') await toggleTheme();
  else if (action === 'close-detail') detailDialog.close();
  else if (action === 'close-action') actionDialog.close();
  else if (action === 'metric') { const metric = (target.dataset.metric || '').toLowerCase(); state.filter = /applied/.test(metric) ? 'applied' : /pdf|cv|document/.test(metric) ? 'cv' : 'all'; await navigate(/run|batch|history/.test(metric) ? 'activity' : 'library'); }
  else if (action === 'clear-filters') { state.query = ''; state.filter = 'all'; document.querySelector('#library-search').value = ''; updateLibrary(); }
  else if (action === 'clear-contact-search') { state.contactsQuery = ''; const field = document.querySelector('#contacts-search'); if (field) field.value = ''; const results = document.querySelector('#saved-contact-results'); if (results) results.innerHTML = renderSavedContactGroups(); }
  else if (action === 'clear-contact-role') { state.contactsRoleFilter = ''; state.contactsQuery = ''; render(); }
  else if (action === 'clear-apply') { state.applyQuery = ''; state.applyFilter = 'all'; ['applyLanguage','applyDocuments','applyFit','applyContract','applySource'].forEach(key => state[key] = 'all'); state.applySort = 'fit'; render(); }
  else if (action === 'copy-application-text') await copyApplicationText(target.dataset.roleId,target.dataset.language,target.dataset.letter === 'true');
  else if (action === 'copy-preview' && state.copyPreview?.text) await copyText(state.copyPreview.text);
  else if (action === 'find-contacts') await findContacts(target.dataset.roleId);
  else if (action === 'stop-contact-search') await stopContactSearch(target.dataset.jobId,target);
  else if (action === 'view-saved-contacts') { state.contactsRoleFilter = target.dataset.roleId; await navigate('contacts'); }
  else if (action === 'view-job-in-apply') { const role = state.roles.find(item => item.id === target.dataset.roleId); state.contactsRoleFilter = ''; state.applySelected = target.dataset.roleId; state.applyFilter = 'all'; state.applyQuery = role ? roleCompany(role) : ''; await navigate('apply'); }
  else if (action === 'build-manual-job') await buildManualJob(target.dataset.manualJobId,target);
  else if (action === 'approve-manual-job') await reviewManualJob(target.dataset.manualJobId,'approve',target);
  else if (action === 'reject-manual-job') await reviewManualJob(target.dataset.manualJobId,'reject',target);
  else if (action === 'open-role') await openRole(target.dataset.roleId);
  else if (action === 'home-stat') { if (target.dataset.stat === 'cv') { state.filter = 'cv'; await navigate('library'); } else { state.applyFilter = target.dataset.stat; await navigate('apply'); } }
  else if (action === 'view-application-cv') await openRole(target.dataset.roleId);
  else if (action === 'chat-mercury') { if (detailDialog.open) detailDialog.close(); window.openHelpBot?.(target.dataset.roleId); }
  else if (action === 'toggle-shortlist') await toggleShortlist(target.dataset.roleId);
  else if (action === 'shortlist-stage') await saveShortlistEntry(target.dataset.roleId, { stage: target.dataset.shortlistStage });
  else if (action === 'shortlist-filter') { state.shortlistStage = target.dataset.shortlistStage; render(); }
  else if (action === 'wont-apply') await wontApply(target.dataset.roleId, target);
  else if (action === 'apply-later') await applyLater(target.dataset.roleId, target);
  else if (action === 'copy-link') await copyLink(target);
  else if (action === 'mark-applied') await setApplication(target.dataset.roleId, 'applied');
  else if (action === 'mark-inactive') await setApplication(target.dataset.roleId, 'inactive');
  else if (action === 'toggle-notices') toggleNotices();
  else if (action === 'retry-role') await openRole(state.dialogRole);
  else if (action === 'retry-run') await selectRun(state.selectedRun);
  else if (action === 'revert-document') askDiscard(() => { state.draft = state.doc.content; state.docError = ''; document.querySelector('#editor-container').innerHTML = renderEditor(); bindEditor(); });
  else if (action === 'confirm-discard') { actionDialog.close(); const next = state.navigation; state.navigation = null; if (next) next(); }
  else if (action === 'save-document') await saveDocument();
  else if (action === 'open-routing') await openDocument('routing');
  else if (action === 'start-check') await startJob('check');
  else if (action === 'start-pipeline') await startJob('pipeline');
  else if (action === 'confirm-pipeline') await dispatchJob('pipeline');
  else if (action === 'refresh-jobs') await refreshJobs();
  else if (action === 'check-connections') {
    const draft = captureSettingsDraft();
    try { const caps = await api.checkConnections(); state.overview.runtime ||= {}; state.overview.runtime.capabilities = caps; render(); restoreSettingsDraft(draft); scheduleRefresh(); toast('Checking connections.'); }
    catch (error) { toast(error.message, 'error'); }
  }
  else if (action === 'stop-job') await stopJob();
  else if (action === 'stop-run') await stopRun();
  else if (action === 'toggle-logs') { state.showAllLogs = !state.showAllLogs; const panel = document.querySelector('#external-activity'); if (panel) panel.outerHTML = externalActivity(); }
  else if (action === 'confirm-stop') await confirmStop();
  else if (action === 'reset-run-settings') {
    target.disabled = true;
    try { state.settings.run = await api.resetRunSettings(); const form = document.querySelector('#run-settings-form'); form.elements.model.value = state.settings.run.model; form.elements.effort.innerHTML = effortOptions(state.settings.run.model,state.settings.run.effort); document.querySelector('#run-settings-status').textContent = 'Default restored: Sol 6.1 · High'; toast('Model defaults restored.'); }
    catch (error) { toast(error.message,'error'); } finally { target.disabled = false; }
  }
  else if (action === 'command') commandPalette();
});
document.addEventListener('input', event => {
  if (event.target.id === 'library-search') { state.query = event.target.value; updateLibrary(); }
  else if (event.target.id === 'apply-search') { state.applyQuery = event.target.value; updateApply(); }
  else if (event.target.id === 'contacts-search') { state.contactsQuery = event.target.value; const results = document.querySelector('#saved-contact-results'); if (results) results.innerHTML = renderSavedContactGroups(); }
  else if (event.target.matches('[data-jd-term]')) { state.jdTerm = event.target.value; if (state.route === 'overview') rerenderHomePreservingInput(event.target); }
  else if (event.target.id === 'activity-search') { state.activityQuery = event.target.value; document.querySelector('#run-list').innerHTML = renderRunList(); }
  else if (event.target.id === 'command-search') document.querySelector('#command-results').innerHTML = commandResults(event.target.value);
});
document.addEventListener('change', async event => {
  if (event.target.dataset.applicationRole) await setApplication(event.target.dataset.applicationRole,event.target.value);
  else if (event.target.dataset.manualContactChoice) {
    const field = event.target; field.disabled = true;
    try {
      const result = await api.setManualContactChoice(field.dataset.manualContactChoice, field.checked);
      state.manualJobs = state.manualJobs.map(item => item.id === result.item.id ? result.item : item); updateManualQueue();
    } catch(error) { field.checked = !field.checked; toast(error.message,'error'); }
    finally { field.disabled = false; }
  }
  else if (event.target.dataset.applyOption) { state[event.target.dataset.applyOption] = event.target.value; updateApply(); }
  else if (event.target.hasAttribute('data-apply-status-filter')) { state.applyFilter = event.target.value; updateApply(); }
  else if (event.target.id === 'run-model') { const field = document.querySelector('#run-effort'); const old = field.value; field.innerHTML = effortOptions(event.target.value,old); if (!field.value) field.value = modelOptions().find(row => row.id === event.target.value)?.efforts.includes('high') ? 'high' : field.options[0]?.value || ''; document.querySelector('#run-settings-status').textContent = 'Save to keep this model.'; }
  else if (event.target.id === 'run-effort') document.querySelector('#run-settings-status').textContent = 'Save to keep this effort level.';
  else if (event.target.id === 'library-sort') { state.sort = event.target.value; updateLibrary(); }
  else if (event.target.id === 'activity-filter') { state.activityFilter = event.target.value; document.querySelector('#run-list').innerHTML = renderRunList(); }
  else if (event.target.matches('[data-jd-category]')) { state.jdCategory = event.target.value; render(); }
  else if (event.target.matches('[data-jd-term]')) { state.jdTerm = event.target.value; render(); }
  else if (event.target.id === 'job-select') await loadJob(event.target.value);
  else if (event.target.id === 'artifact-select') { state.activeArtifact = event.target.value; renderRoleDrawer(state.roleDetail); }
  else if (event.target.closest('#preferences-form')) { const form = new FormData(document.querySelector('#preferences-form')); applySettings({ theme: form.get('theme'), density: form.get('density') }); document.querySelector('#settings-save-status').textContent = 'Previewing your changes. Save to keep them.'; }
});
document.addEventListener('toggle', event => { if (!event.target.isConnected) return; if (event.target.matches?.('.apply-filter-drawer')) state.applyFiltersOpen = event.target.open; if (event.target.dataset.pipelinePanel) { if (event.target.open) state.pipelineSections.add(event.target.dataset.pipelinePanel); else { state.pipelineSections.delete(event.target.dataset.pipelinePanel); if (event.target.dataset.pipelinePanel === 'studio') state.showStudioActivity = false; } } if (event.target.matches?.('.contact-discovery') && event.target.open) readContacts(event.target.dataset.contactRole); },true);
document.addEventListener('submit', async event => {
  if (event.target.id === 'manual-job-form') {
    event.preventDefault(); await addManualJob(event.target);
  } else if (event.target.id === 'run-settings-form') {
    event.preventDefault(); const form = new FormData(event.target); const submit = event.target.querySelector('[type="submit"]'); submit.disabled = true;
    try { state.settings.run = await api.saveRunSettings({model:form.get('model'),effort:form.get('effort')}); document.querySelector('#run-settings-status').textContent = 'Saved for the next Studio run.'; toast('Model settings saved.'); }
    catch (error) { toast(error.message,'error'); } finally { submit.disabled = false; }
  } else if (event.target.id === 'preferences-form') {
    event.preventDefault(); const form = new FormData(event.target); const preferences = { theme: form.get('theme'), density: form.get('density'), refresh_seconds: Number(form.get('refresh_seconds')) };
    const submit = event.target.querySelector('[type="submit"]'); submit.disabled = true;
    try { state.settings = await api.saveSettings(preferences); applySettings(); scheduleRefresh(); document.querySelector('#settings-save-status').textContent = 'Preferences saved on this Mac.'; toast('Your studio preferences are saved.'); }
    catch (error) { toast(error.message, 'error'); } finally { submit.disabled = false; }
  } else if (event.target.id === 'job-input-form') {
    event.preventDefault(); const form = new FormData(event.target); const answers = {};
    for (const [key, value] of form.entries()) if (!key.startsWith('custom_')) { const answer = String(form.get(`custom_${key}`) || value).trim(); if (!answer) { toast('Please answer each question before sending.', 'error'); return; } answers[key] = { answers: [answer] }; }
    await sendJobAnswer({ answers });
  } else if (event.target.id === 'connector-input-form') {
    event.preventDefault(); const raw = new FormData(event.target).get('content'); let content;
    if (raw) { try { content = JSON.parse(raw); } catch { toast('The response must be valid JSON.', 'error'); return; } if (!content || Array.isArray(content) || typeof content !== 'object') { toast('The response must be a JSON object.', 'error'); return; } }
    await sendJobAnswer({ action: 'accept', content });
  }
});
document.addEventListener('keydown', event => {
  if (applyShortcut(event)) { event.preventDefault(); return; }
  if (event.key === 'Escape' && state.noticesOpen) toggleNotices(false);
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); if (!detailDialog.open) commandPalette(); }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's' && state.route === 'workspace') { event.preventDefault(); saveDocument(); }
  if (event.key === 'Escape' && document.querySelector('.studio-shell').classList.contains('nav-open')) setNavigation(false, true);
  if (event.key === '/' && state.route === 'library' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName) && !detailDialog.open && !actionDialog.open) { event.preventDefault(); document.querySelector('#library-search').focus(); }
  if (event.key === 'Enter' && event.target.id === 'command-search') { event.preventDefault(); state.query = event.target.value; state.filter = 'all'; actionDialog.close(); navigate('library', { force: true }); }
});
[detailDialog, actionDialog].forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('visibilitychange', () => { if (!document.hidden && state.loaded) refresh({ quiet: true }); });
window.addEventListener('hashchange', () => { const route = routeFromHash(); history.replaceState(null, '', `#${state.route}`); navigate(route); });
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (state.settings.theme === 'system') applySettings(); });
matchMedia('(max-width: 620px)').addEventListener('change', () => setNavigation(false));
renderShell();
document.addEventListener('click', async event => {
  const part = event.target.closest?.('.apply-detail-head .application-role-copy > strong, .apply-detail-head .application-role-copy > span');
  if (!part) return;
  event.preventDefault(); event.stopPropagation();
  const isCompany = part.tagName === 'STRONG';
  const own = (part.getAttribute('title') || part.textContent || '').trim();
  const company = (part.parentElement.querySelector(':scope > strong')?.getAttribute('title') || '').trim();
  const text = isCompany || !company ? own : `${own} ${part.parentElement.dataset.lang === 'fr' ? 'à' : 'at'} ${company}`;
  const label = isCompany ? 'Company' : 'Job title';
  try { await navigator.clipboard.writeText(text); toast(`${label} copied.`); part.classList.remove('copy-flash'); void part.offsetWidth; part.classList.add('copy-flash'); setTimeout(() => part.classList.remove('copy-flash'), 700); }
  catch (error) { toast('Could not copy.', 'error'); }
}, true);
applySettings();
if (state.route === 'prospect') render();
loadShortlist();
loadApplyFlow();
await refresh();
