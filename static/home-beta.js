// Home dashboard. Counts come from saved role, application, run, and JD evidence.
import { escape as e, formatNumber, date, icon } from './ui.js';

const PARIS = 'Europe/Paris';
const FILTERS = [
  ['today', 'Today'], ['yesterday', 'Yesterday'], ['this_week', 'This week'],
  ['last_week', 'Last week'], ['this_month', 'This month'], ['all', 'All time'],
];
const SOURCE_NAMES = {
  hellowork: 'HelloWork', france_travail: 'France Travail', francetravail: 'France Travail',
  linkedin: 'LinkedIn', apec: 'APEC', welcometothejungle: 'Welcome to the Jungle',
  welcome_to_the_jungle: 'Welcome to the Jungle', indeed: 'Indeed', hark: 'Hark sheet', careers_page: 'Careers pages', mail: 'Email', unknown: 'Not recorded',
};
const SENT = new Set(['applied', 'interviewing', 'offer', 'rejected', 'withdrawn']);
const REPLIED = new Set(['interviewing', 'offer', 'rejected']);
const NUMBER = value => Number.isFinite(value) && value >= 0 ? formatNumber(value) : '—';

// Home lag (13 Oct): a new Intl.DateTimeFormat per call, thousands of calls per render, made one
// model build take 2–20 s. One shared formatter plus a per-string memo keeps results identical.
let parisFormat = null;
const parisDayMemo = new Map();
function memo(map, key, compute) {
  if (map.has(key)) return map.get(key);
  if (map.size > 50000) map.clear();
  const value = compute();
  map.set(key, value);
  return value;
}
function parisDay(value) {
  if (typeof value !== 'string' && !(value instanceof Date)) return null;
  const raw = value instanceof Date ? value.toISOString() : value.trim();
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  return memo(parisDayMemo, raw, () => {
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return null;
    parisFormat ||= new Intl.DateTimeFormat('en-GB', { timeZone: PARIS, year: 'numeric', month: '2-digit', day: '2-digit' });
    const parts = Object.fromEntries(parisFormat.formatToParts(parsed).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  });
}

function shiftDay(key, amount) {
  const day = new Date(`${key}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + amount);
  return day.toISOString().slice(0, 10);
}

function weekStart(key) {
  const day = new Date(`${key}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
  return day.toISOString().slice(0, 10);
}

function monthStart(key) { return `${key.slice(0, 7)}-01`; }

// Generic ids (Home, 13 Oct): "d:N" = N days ago, "w:N" = N weeks ago (Monday-Sunday), "m:N" = N months ago.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function monthShift(key, back) {
  const day = new Date(`${key.slice(0, 7)}-01T12:00:00Z`);
  day.setUTCMonth(day.getUTCMonth() - back);
  return day.toISOString().slice(0, 10);
}
function timeRange(value, now = new Date()) {
  const current = parisDay(now);
  const thisMonday = weekStart(current);
  const generic = /^(wt|[dwm]):(\d{1,3})$/.exec(String(value));
  if (generic) {
    const n = Number(generic[2]);
    const range = { id: String(value), start: null, end: null, label: '' };
    if (generic[1] === 'd') { range.start = range.end = shiftDay(current, -n); range.label = `${n} days ago`; }
    if (generic[1] === 'w') {
      range.start = shiftDay(thisMonday, -7 * n); range.end = shiftDay(range.start, 6);
      range.label = `${n} weeks ago`;
    }
    if (generic[1] === 'wt') {  // N weeks back, Monday up to the same weekday as today
      range.start = shiftDay(thisMonday, -7 * n); range.end = shiftDay(current, -7 * n);
      range.label = n ? `${n} weeks ago (to same day)` : 'This week';
    }
    if (generic[1] === 'm') {
      range.start = monthShift(current, n);
      range.end = shiftDay(monthShift(range.start, -1), -1);
      range.label = `${MONTHS[Number(range.start.slice(5, 7)) - 1]} ${range.start.slice(0, 4)}`;
    }
    return range;
  }
  const selected = FILTERS.some(([id]) => id === value) ? value : 'all';
  const range = { id: selected, start: null, end: null, label: FILTERS.find(([id]) => id === selected)[1] };
  if (selected === 'today') range.start = range.end = current;
  if (selected === 'yesterday') range.start = range.end = shiftDay(current, -1);
  if (selected === 'this_week') { range.start = thisMonday; range.end = current; }
  if (selected === 'last_week') { range.start = shiftDay(thisMonday, -7); range.end = shiftDay(thisMonday, -1); }
  if (selected === 'this_month') { range.start = monthStart(current); range.end = current; }
  return range;
}
export function periodLabel(id) { return timeRange(id).label; }

function inRange(value, range) {
  if (range.id === 'all') return true;
  const key = parisDay(value);
  return Boolean(key && key >= range.start && key <= range.end);
}

function scoped(range) { return range.id !== 'all'; }
function plural(value, one, many = `${one}s`) { return `${NUMBER(value)} ${value === 1 ? one : many}`; }
function percent(part, whole) { return Number.isFinite(part) && Number.isFinite(whole) && whole > 0 ? `${Math.round(part * 100 / whole)}%` : '—'; }

function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  if (seconds < 60) return '<1 min';
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function tokenTotal(value) {
  if (Number.isFinite(value) && value >= 0) return value;
  if (value && typeof value === 'object') {
    const total = value.total_tokens ?? value.total;
    if (Number.isFinite(total) && total >= 0) return total;
  }
  return null;
}

function compactTokens(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  const rounded = Math.round(value);
  const scale = rounded >= 1_000_000_000_000 ? [1_000_000_000_000, 'T']
    : rounded >= 1_000_000_000 ? [1_000_000_000, 'B']
      : rounded >= 1_000_000 ? [1_000_000, 'M'] : null;
  const formatted = scale
    ? `${new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 }).format(rounded / scale[0])}${scale[1]}`
    : NUMBER(rounded);
  return `${formatted} tokens`;
}

function quotaDeltaForRun(run) {
  const usage = run?.usage;
  if (!usage || typeof usage !== 'object' || usage.status !== 'account_observed' ||
    typeof usage.approximate !== 'boolean') return null;
  const delta = usage.account_delta_pp;
  const provider = typeof usage.provider === 'string' ? usage.provider.trim() : '';
  const window = typeof usage.window === 'string' ? usage.window.trim() : '';
  if (!Number.isFinite(delta) || delta < 0 || delta > 100 || !provider || !window) return null;
  if (Array.isArray(usage.windows) && usage.windows.length) {
    const matchCount = usage.windows.filter(row => row && row.status === 'observed' && row.approximate === usage.approximate &&
      row.provider === provider && row.window === window && row.account_delta_pp === delta).length;
    if (matchCount !== 1) return null;
  }
  return { delta, approximate: usage.approximate, identity: `${provider.toLocaleLowerCase('en')}|${window}` };
}

function rolesFor(ctx) { return Array.isArray(ctx.state?.roles) ? ctx.state.roles : []; }
function runsFor(ctx) {
  if (Array.isArray(ctx.state?.runs)) return ctx.state.runs;
  const runs = ctx.state?.overview?.runs?.items;
  return Array.isArray(runs) ? runs : [];
}
function analyticsFor(ctx) { return ctx.state?.overview?.dashboard_analytics || {}; }
function historicalAdjustments(ctx) {
  const saved = analyticsFor(ctx).historical_adjustments || {};
  const counts = saved.available ? (saved.recorded_counts || {}) : {};
  return {
    read: Number.isSafeInteger(counts.unique_full_ads_read) && counts.unique_full_ads_read >= 0 ? counts.unique_full_ads_read : 0,
    shortlist: Number.isSafeInteger(counts.validated_jds_chosen_for_cv) && counts.validated_jds_chosen_for_cv >= 0 ? counts.validated_jds_chosen_for_cv : 0,
    available: saved.available === true,
  };
}
function roleDate(role) { return role.found_at || role.found || role.captured_at || null; }

function appRecord(role, ctx) {
  try {
    return typeof ctx.applicationOf === 'function' ? (ctx.applicationOf(role) || {}) : (role.application || {});
  } catch { return role.application || {}; }
}

function sourceOf(role, ctx) {
  try {
    const value = typeof ctx.advertSource === 'function' ? ctx.advertSource(role) : 'unknown';
    return SOURCE_NAMES[value] ? value : 'unknown';
  } catch { return 'unknown'; }
}

function hasStatusEvidence(role, statuses, ctx) {
  const record = appRecord(role, ctx);
  if (statuses.includes(record.status)) return true;
  if (statuses.includes('applied') && (record.canonical_applied === true || record.applied_at)) return true;
  return Array.isArray(record.status_history) && record.status_history.some(item => statuses.includes(item?.status));
}

function statusDates(role, statuses, ctx) {
  const record = appRecord(role, ctx);
  const dates = [];
  let undatedEvent = false;
  for (const status of statuses) {
    const explicit = record[`${status}_at`];
    if (explicit) {
      const day = parisDay(explicit);
      if (day) dates.push(day); else undatedEvent = true;
    }
  }
  if (statuses.includes('applied') && record.applied_at) {
    const day = parisDay(record.applied_at);
    if (day) dates.push(day); else undatedEvent = true;
  }
  if (Array.isArray(record.status_history)) {
    for (const item of record.status_history) {
      if (!statuses.includes(item?.status)) continue;
      if (!item.at) { undatedEvent = true; continue; }
      const day = parisDay(item.at);
      if (day) dates.push(day); else undatedEvent = true;
    }
  }
  const currentStatusIsEvidence = statuses.includes(record.status)
    || (statuses.includes('applied') && record.canonical_applied === true);
  if (currentStatusIsEvidence && dates.length === 0) undatedEvent = true;
  return { dates: [...new Set(dates)], undatedEvent };
}

function roleEventState(role, statuses, range, ctx, dateStatuses = statuses) {
  const evidence = hasStatusEvidence(role, statuses, ctx);
  if (!evidence) return { included: false, undated: false };
  const recorded = statusDates(role, dateStatuses, ctx);
  if (range.id === 'all') return { included: true, undated: recorded.dates.length === 0 || recorded.undatedEvent };
  const matches = recorded.dates.some(day => inRange(day, range));
  return { included: matches, undated: !matches && (recorded.undatedEvent || recorded.dates.length === 0) };
}

function roleCounts(ctx, range) {
  const unavailable = Boolean(ctx.state?.errors?.roles);
  const roles = rolesFor(ctx);
  const counts = { applied: 0, rejected: 0, replies: 0 };
  const unknown = { applied: 0, rejected: 0, replies: 0 };
  const sourceEvents = new Map();
  const rejectedItems = [];

  for (const role of roles) {
    const source = sourceOf(role, ctx);
    for (const [key, statuses] of Object.entries({
      applied: [...SENT], rejected: ['rejected'], replies: [...REPLIED],
    })) {
      const event = roleEventState(role, statuses, range, ctx, key === 'applied' ? ['applied'] : statuses);
      if (event.included) {
        counts[key] += 1;
        if (key === 'rejected') rejectedItems.push({ company: typeof role.company === 'string' ? role.company : '',
          title: typeof role.title === 'string' ? role.title : '' });
        const row = sourceEvents.get(source) || { applied: 0, replies: 0 };
        if (key === 'applied') row.applied += 1;
        if (key === 'replies') row.replies += 1;
        sourceEvents.set(source, row);
      }
      if (event.undated) unknown[key] += 1;
    }
  }
  return { roles, unavailable, counts, unknown, sourceEvents, rejectedItems };
}

function stageFor(run, id) {
  return (Array.isArray(run.stage_metrics) ? run.stage_metrics : []).find(stage => stage?.id === id) || null;
}

function runStart(run) { return run.started_at || run.date || null; }
function runStageDate(run, id) {
  const stage = stageFor(run, id);
  return stage?.finished_at || stage?.started_at || null;
}

function rolePathKey(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  return value.trim().replace(/\\/g, '/').replace(/^\.\//, '').replace(/^Tailored CVs\//i, '')
    .normalize('NFKC').toLocaleLowerCase('en').replace(/\/{2,}/g, '/');
}

const canonicalJobUrlMemo = new Map();
function canonicalJobUrl(value) {
  return typeof value === 'string' ? memo(canonicalJobUrlMemo, value, () => canonicalJobUrlUncached(value)) : canonicalJobUrlUncached(value);
}
function canonicalJobUrlUncached(value) {
  const safe = publicSourceUrl(value);
  if (!safe) return null;
  try {
    const url = new URL(safe);
    url.hostname = url.hostname.toLocaleLowerCase('en').replace(/^www\./, '');
    for (const key of [...url.searchParams.keys()]) {
      const folded = key.toLocaleLowerCase('en');
      if (folded.startsWith('utm_') || ['trk','trackingid','refid','ref_id','source','src'].includes(folded)) {
        url.searchParams.delete(key);
      }
    }
    url.searchParams.sort();
    url.hash = '';
    if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/+$/, '');
    return url.href;
  } catch { return null; }
}

const postingIdentityMemo = new Map();
function postingIdentity(value) {
  return typeof value === 'string' ? memo(postingIdentityMemo, value, () => postingIdentityUncached(value)) : postingIdentityUncached(value);
}
function postingIdentityUncached(value) {
  const canonical = canonicalJobUrl(value);
  if (!canonical) return null;
  const url = new URL(canonical);
  const host = url.hostname;
  const definitions = [
    ['linkedin.com', 'linkedin', /\/jobs\/view\/(?:[^/?]*-)?(\d+)(?:\/|$)/],
    ['hellowork.com', 'hellowork', /\/emplois\/(\d+)(?:\.html)?(?:\/|$)/],
    ['hellowork.fr', 'hellowork', /\/emplois\/(\d+)(?:\.html)?(?:\/|$)/],
    ['francetravail.fr', 'france_travail', /\/detail\/([\w-]+)/],
    ['pole-emploi.fr', 'france_travail', /\/detail\/([\w-]+)/],
    ['apec.fr', 'apec', /detail-offre\/([\w-]+)/],
  ];
  for (const [domain, source, pattern] of definitions) {
    if (host !== domain && !host.endsWith(`.${domain}`)) continue;
    const found = url.pathname.match(pattern);
    if (found) return `posting:${source}:${found[1]}`;
  }
  if (['indeed.com', 'indeed.fr'].some(domain => host === domain || host.endsWith(`.${domain}`))) {
    const id = url.searchParams.get('jk') || url.searchParams.get('vjk');
    if (id) return `posting:indeed:${id}`;
  }
  return `url:${canonical.replace(/^http:/, 'https:')}`;
}

function applicationWeek(ctx, now, unavailable = false) {
  const today = parisDay(now);
  const days = Array.from({ length: 7 }, (_, index) => shiftDay(today, index - 6));
  const groups = [];
  if (!unavailable) for (const role of rolesFor(ctx)) {
    if (!hasStatusEvidence(role, [...SENT], ctx)) continue;
    const record = appRecord(role, ctx);
    const alias = role.approved_document_alias || {};
    const posting = postingIdentity(alias.canonical_url || role.source_url || record.apply_url);
    const keys = new Set([
      record.application_id ? `application:${record.application_id}` : null,
      record.canonical_role_id ? `role:${record.canonical_role_id}` : null,
      role.id ? `role:${role.id}` : null,
      alias.canonical_studio_role_id ? `role:${alias.canonical_studio_role_id}` : null,
      alias.approved_document_studio_role_id ? `role:${alias.approved_document_studio_role_id}` : null,
      posting,
      !posting && rolePathKey(role.folder) ? `path:${rolePathKey(role.folder)}` : null,
    ].filter(Boolean));
    if (!keys.size) keys.add(`unidentified:${groups.length}`);
    const dates = statusDates(role, ['applied'], ctx).dates;
    const matches = groups.map((group, index) => [...keys].some(key => group.keys.has(key)) ? index : -1).filter(index => index >= 0);
    const combinedDates = [...dates];
    for (const index of matches.reverse()) {
      const prior = groups.splice(index, 1)[0];
      for (const key of prior.keys) keys.add(key);
      combinedDates.push(...prior.dates);
    }
    groups.push({ keys, dates: combinedDates });
  }
  const counts = new Map(days.map(day => [day, 0]));
  let undated = 0;
  for (const group of groups) {
    const applied = [...new Set(group.dates)].sort()[0];
    if (!applied) { undated += 1; continue; }
    if (counts.has(applied)) counts.set(applied, counts.get(applied) + 1);
  }
  return { rows: days.map(day => ({ date: day,
    label: new Intl.DateTimeFormat('en-GB', { timeZone: PARIS, weekday: 'short' }).format(new Date(`${day}T12:00:00Z`)),
    count: unavailable ? null : counts.get(day) })),
    note: unavailable ? 'Application records are unavailable.' : undated
      ? `${NUMBER(undated)} recorded applications have no saved applied date and are excluded from this chart.`
      : 'Original applied dates · Paris calendar days', undated: unavailable ? null : undated };
}

function acceptedSummary(ctx, range) {
  if (analyticsFor(ctx).loading || analyticsFor(ctx).unavailable) return { value: null, partial: false, undated: 0, sourceEvents: new Map(), runs: 0 };
  const runs = runsFor(ctx).filter(run => run && run.kind !== 'legacy_stage');
  const selected = new Map();
  const undated = new Set();
  let identityGap = false;
  let missingCounts = 0;
  const rolesByPath = new Map(rolesFor(ctx).map(role => [rolePathKey(role.folder), role]).filter(([key]) => key));
  const keysFor = item => {
    const keys = new Set();
    const add = raw => {
      if (typeof raw !== 'string' || !raw) return;
      if (raw.startsWith('url:')) {
        const canonical = canonicalJobUrl(raw.slice(4));
        if (canonical) {
          keys.add(`url:${canonical.replace(/^http:/, 'https:')}`);
          keys.add(postingIdentity(canonical));
        }
      } else if (/^(?:posting|job|path):/.test(raw)) keys.add(raw);
    };
    for (const raw of Array.isArray(item.identity_keys) ? item.identity_keys : []) add(raw);
    add(item.identity);
    const canonical = canonicalJobUrl(item.canonical);
    if (canonical) add(`url:${canonical}`);
    for (const job of item.jobs || []) {
      if (job?.source_url) add(`url:${job.source_url}`);
      for (const raw of Array.isArray(job?.identity_keys) ? job.identity_keys : []) add(raw);
    }
    if ([...keys].some(key => /^(?:url|posting|job):/.test(key))) {
      for (const key of keys) if (key.startsWith('path:')) keys.delete(key);
    } else if (item.path) keys.add(`path:${item.path}`);
    return keys;
  };
  const addSelected = item => {
    const keys = keysFor(item);
    if (!keys.size) return;
    const matches = [...selected].filter(([, prior]) => [...keys].some(key => prior.keys.has(key)));
    const merged = { ...item, keys, dates: [...item.dates], jobs: [...item.jobs] };
    for (const [id, prior] of matches) {
      selected.delete(id);
      for (const key of prior.keys) keys.add(key);
      merged.dates.push(...prior.dates);
      merged.jobs.push(...prior.jobs);
      merged.path ||= prior.path;
      merged.canonical ||= prior.canonical;
      merged.source ||= prior.source;
    }
    // A shared alias may connect more than two earlier records. Merge until
    // every saved identity key has one group, regardless of insertion order.
    let connected = true;
    while (connected) {
      connected = false;
      for (const [id, prior] of selected) {
        if (![...keys].some(key => prior.keys.has(key))) continue;
        selected.delete(id);
        for (const key of prior.keys) keys.add(key);
        merged.dates.push(...prior.dates);
        merged.jobs.push(...prior.jobs);
        merged.path ||= prior.path;
        merged.canonical ||= prior.canonical;
        merged.source ||= prior.source;
        connected = true;
      }
    }
    selected.set([...keys].sort()[0], merged);
  };

  for (const run of runs) {
    const stageDate = runStageDate(run, 'hunt_audit');
    if (range.id !== 'all' && stageDate && !inRange(stageDate, range)) continue;
    // History's normalized jobs originate from sealed acceptance. Keep any
    // explicitly negative row out if a mixed legacy snapshot is supplied.
    const jobs = (Array.isArray(run.jobs) ? run.jobs : []).filter(job => {
      const status = String(job?.status || '').toLocaleLowerCase('en');
      return !['rejected', 'proposed', 'not_shortlisted', 'declined', 'not_accepted'].includes(status);
    });
    const paths = [...new Set(jobs.map(job => rolePathKey(job?.role_path)).filter(Boolean))];
    const rawCount = run.summary_metrics?.jobs_shortlisted;
    const count = Number.isFinite(rawCount) && rawCount >= 0 ? rawCount : null;
    if (count == null && paths.length === 0) { missingCounts += 1; continue; }
    if (count === 0) continue;
    if (count != null && count > paths.length) identityGap = true;
    if (count != null && count < paths.length) { identityGap = true; continue; }
    for (const path of paths) {
      const candidates = jobs.filter(job => rolePathKey(job?.role_path) === path);
      const dates = candidates.map(job => job?.shortlisted_at || job?.accepted_at).filter(Boolean);
      if (stageDate) dates.push(stageDate);
      const days = [...new Set(dates.map(parisDay).filter(Boolean))];
      if (range.id !== 'all' && !days.some(day => inRange(day, range))) {
        if (days.length === 0 || (!stageDate && candidates.some(job => !job?.shortlisted_at && !job?.accepted_at))) undated.add(path);
        continue;
      }
      if (days.length === 0) undated.add(path);
      const role = rolesByPath.get(path);
      const canonical = canonicalJobUrl(candidates.find(job => job?.source_url)?.source_url || role?.source_url);
      addSelected({ path, jobs: candidates, dates: days, canonical });
    }
    if (range.id !== 'all' && !stageDate && paths.length === 0 && count > 0) missingCounts += 1;
  }

  const manualEvents = [
    ...(Array.isArray(analyticsFor(ctx).manual_shortlists) ? analyticsFor(ctx).manual_shortlists : []),
    ...(Array.isArray(analyticsFor(ctx).sheet_shortlists) ? analyticsFor(ctx).sheet_shortlists : []),
  ];
  for (const event of manualEvents) {
    const canonical = canonicalJobUrl(event?.canonical_url || event?.url);
    const key = canonical ? postingIdentity(canonical) : event?.identity;
    if (!key) continue;
    const day = parisDay(event?.accepted_at);
    if (range.id !== 'all' && (!day || !inRange(day, range))) {
      if (!day) undated.add(key);
      continue;
    }
    addSelected({
      path: null, jobs: [{ source: event?.source || null }], dates: day ? [day] : [],
      canonical, source: event?.source || null,
      identity: event?.identity || key, identity_keys: event?.identity_keys,
    });
  }

  const hark = analyticsFor(ctx).hark_feed || {};
  const screeningEvidenceUnavailable = Number(hark.invalid_saved_sources) > 0 || Number(hark.omitted_source_directories) > 0;
  const uncertain = screeningEvidenceUnavailable || (range.id === 'all'
    ? missingCounts > 0 || identityGap : undated.size > 0 || missingCounts > 0 || identityGap);
  const value = selected.size ? selected.size : (!screeningEvidenceUnavailable && missingCounts === 0 &&
    !identityGap && undated.size === 0 && runs.length > 0 ? 0 : null);
  const sourceEvents = new Map();
  for (const item of selected.values()) {
    const role = rolesByPath.get(item.path);
    const sample = item.jobs.find(job => job?.source) || {};
    let source = role ? sourceOf(role, ctx) : 'unknown';
    if (source === 'unknown' && item.source) {
      const candidate = String(item.source).toLocaleLowerCase('en');
      source = Object.keys(SOURCE_NAMES).find(id => candidate.includes(id.replaceAll('_', ' ')) || candidate.includes(id)) || 'unknown';
    }
    if (source === 'unknown' && sample.source) {
      const candidate = String(sample.source).toLocaleLowerCase('en');
      source = Object.keys(SOURCE_NAMES).find(id => candidate.includes(id.replaceAll('_', ' ')) || candidate.includes(id)) || 'unknown';
    }
    if (['unknown', 'hark', 'mail'].includes(source)) {  // the sheet feed hides the real website: read it from the URL
      const url = String(item.canonical || '').toLocaleLowerCase('en');
      const byHost = HOST_SOURCES.find(([host]) => url.includes(host));
      if (byHost) source = byHost[1];
    }
    const row = sourceEvents.get(source) || { shortlisted: 0, applied: 0, replies: 0 };
    row.shortlisted += 1;
    sourceEvents.set(source, row);
  }
  const adjustment = range.id === 'all' ? historicalAdjustments(ctx).shortlist : 0;
  const adjustedValue = value == null ? (adjustment ? adjustment : null) : value + adjustment;
  return { value: adjustedValue, verifiedValue: value, historicalAdjustment: adjustment,
    partial: (uncertain && value != null) || adjustment > 0, undated: undated.size,
    sourceEvents, runs: runs.length, screeningEvidenceUnavailable };
}

function readSummary(ctx, range) {
  if (analyticsFor(ctx).loading || analyticsFor(ctx).unavailable) return { value: null, partial: false };
  const analytics = analyticsFor(ctx);
  if (Array.isArray(analytics.collection_events)) {
    const collection = analytics.collection_events.filter(event => event?.identity || postingIdentity(event?.canonical_url || event?.url));
    const matching = collection.filter(event => range.id === 'all' || inRange(event?.captured_at, range));
    const keys = new Set(matching.map(event => event?.identity || postingIdentity(event?.canonical_url || event?.url)));
    const undated = collection.filter(event => !parisDay(event?.captured_at)).length;
    const known = Number.isSafeInteger(analytics.unique_jobs_collected) && analytics.unique_jobs_collected >= 0
      ? analytics.unique_jobs_collected : collection.length ? collection.length : null;
    const hark = analytics.hark_feed || {};
    const source = hark.available && hark.captured_at_utc
      ? ` Hark checked ${parisDay(hark.captured_at_utc)}.` : '';
    const dateNote = range.id !== 'all' && undated
      ? ` ${NUMBER(undated)} jobs lack a saved collection date and are excluded.` : '';
    return { value: range.id === 'all' ? known : known == null ? null : keys.size,
      verifiedValue: range.id === 'all' ? known : keys.size,
      historicalAdjustment: 0,
      partial: known != null && (analytics.collection_events_partial === true || range.id !== 'all' && undated > 0),
      note: `Saved adverts and Hark job descriptions, counted once per job.${source}${dateNote}` };
  }
  const events = Array.isArray(analytics.read_events) ? analytics.read_events : [];
  const matching = events.filter(event => canonicalJobUrl(event?.canonical_url || event?.url) &&
    (range.id === 'all' || inRange(event?.captured_at, range)));
  const urls = new Set(matching.map(event => canonicalJobUrl(event?.canonical_url || event?.url)));
  if (range.id !== 'all' && (events.length || Array.isArray(analytics.read_event_partial_dates))) {
    const partialDates = Array.isArray(analytics.read_event_partial_dates)
      ? analytics.read_event_partial_dates : [];
    const unscannedDates = Array.isArray(analytics.read_event_unscanned_dates)
      ? analytics.read_event_unscanned_dates : [];
    const unscannedInRange = analytics.read_event_dates_unavailable === true ||
      unscannedDates.some(day => inRange(day, range));
    if (unscannedInRange) {
      return urls.size
        ? { value: urls.size, verifiedValue: urls.size, partial: true, unknown: null, missingIdentity: null,
          note: 'Verified unique full-ad reads are a minimum; other saved runs from this date lack dated read receipts.' }
        : { value: null, partial: false, unknown: null, missingIdentity: null,
          note: 'Older saved runs from this date have no event-level read projection; zero cannot be confirmed.' };
    }
    const hasDateGap = range.id === 'all'
      ? analytics.read_events_partial === true
      : partialDates.some(day => inRange(day, range));
    return { value: urls.size, verifiedValue: urls.size, partial: hasDateGap,
      unknown: null, missingIdentity: null,
      note: hasDateGap ? 'Verified unique full-ad reads; this date also has unsupported capture evidence, so the count is a minimum.'
        : 'Verified unique full-ad reads from saved capture and screening receipts.' };
  }
  if (range.id !== 'all') {
    return { value: null, partial: false, unknown: null, missingIdentity: null,
      note: 'Unique ad reads have no verified event dates for this period.' };
  }
  const missing = analytics.missing_evidence || {};
  const gap = ['read_count_without_indexed_urls', 'runs_without_complete_read_coverage',
    'runs_omitted_by_bound', 'historical_hunt_counts_without_urls']
    .some(key => Number(missing[key]) > 0);
  const verifiedValue = Number.isFinite(analytics.unique_jobs_read) ? analytics.unique_jobs_read : null;
  const adjustment = historicalAdjustments(ctx).read;
  const value = verifiedValue == null ? (adjustment ? adjustment : null) : verifiedValue + adjustment;
  const historicalNote = adjustment ? `Includes +${NUMBER(adjustment)} user-supplied historical estimate; no event dates or source breakdown were supplied.` : '';
  return { value, verifiedValue, historicalAdjustment: adjustment,
    partial: gap || adjustment > 0 || (verifiedValue == null && adjustment > 0), unknown: null, missingIdentity: null,
    note: historicalNote || (gap ? 'Known distinct read URLs; saved read indexes have gaps.' : value == null ? 'All-time indexed read evidence is unavailable.' : 'Distinct public URLs from saved run read indexes.') };
}

// Home (13 Oct): unique collected jobs per source and period, for the Home source cards. The sheet
// feed is labelled "hark" in the saved events, so the real site is read from the advert URL.
const HOST_SOURCES = [['linkedin.', 'linkedin'], ['hellowork.', 'hellowork'], ['francetravail.', 'france_travail'],
  ['pole-emploi.', 'france_travail'], ['apec.', 'apec'], ['indeed.', 'indeed'],
  ['welcometothejungle.', 'welcome_to_the_jungle'], ['jobsthatmakesense.', 'jobs_that_make_sense'],
  // 5 Nov 2026: Hark's careers-pages feed (ATS boards) replaces Jobs That Make Sense
  ['greenhouse.io', 'careers_page'], ['lever.co', 'careers_page'], ['ashbyhq.', 'careers_page'], ['smartrecruiters.', 'careers_page'],
  ['workable.', 'careers_page'], ['myworkdayjobs.', 'careers_page']];
export function normalSource(id) {
  const key = String(id || 'unknown').toLocaleLowerCase('en');
  if (key === 'francetravail') return 'france_travail';
  if (key === 'welcometothejungle' || key === 'wttj') return 'welcome_to_the_jungle';
  return key || 'unknown';
}
export function sourceLabel(id) { return SOURCE_NAMES[id] || SOURCE_NAMES[normalSource(id)] || String(id).replaceAll('_', ' '); }
export function collectedBySource(analytics, rangeId, now = new Date()) {
  const events = Array.isArray(analytics?.collection_events) ? analytics.collection_events : [];
  const range = timeRange(rangeId, now);
  const seen = new Map();
  for (const event of events) {
    if (!inRange(event?.captured_at, range)) continue;
    const key = event?.identity || postingIdentity(event?.canonical_url || event?.url);
    if (!key || seen.has(key)) continue;
    const url = String(event?.canonical_url || event?.url || '').toLocaleLowerCase('en');
    const byHost = HOST_SOURCES.find(([host]) => url.includes(host));
    seen.set(key, byHost ? byHost[1] : normalSource(event?.source));
  }
  const counts = new Map();
  for (const source of seen.values()) counts.set(source, (counts.get(source) || 0) + 1);
  return counts;
}


// Home (15 Oct): jobs collected on one day, split by website (same de-duplication as collectedBySource).
export function collectedBySourceForRange(analytics, range) {
  const events = Array.isArray(analytics?.collection_events) ? analytics.collection_events : [];
  const seen = new Map();
  for (const event of events) {
    if (!inRange(event?.captured_at, range)) continue;
    const key = event?.identity || postingIdentity(event?.canonical_url || event?.url);
    if (!key || seen.has(key)) continue;
    const url = String(event?.canonical_url || event?.url || '').toLocaleLowerCase('en');
    const byHost = HOST_SOURCES.find(([host]) => url.includes(host));
    seen.set(key, byHost ? byHost[1] : normalSource(event?.source));
  }
  const counts = {};
  for (const source of seen.values()) counts[source] = (counts[source] || 0) + 1;
  return counts;
}

// Home (13 Oct): French/English split of the screened (collected) job descriptions in a period.
export function collectedLanguages(analytics, rangeId, now = new Date()) {
  const events = Array.isArray(analytics?.collection_events) ? analytics.collection_events : [];
  const range = timeRange(rangeId, now);
  const seen = new Map();
  for (const event of events) {
    if (!inRange(event?.captured_at, range)) continue;
    const key = event?.identity || postingIdentity(event?.canonical_url || event?.url);
    if (!key || seen.has(key)) continue;
    seen.set(key, event?.language || null);
  }
  const result = { fr: 0, en: 0, unknown: 0 };
  for (const language of seen.values()) result[language === 'fr' || language === 'en' ? language : 'unknown'] += 1;
  return result;
}


export function collectedLanguagesBySource(analytics, rangeId, now = new Date()) {
  const events = Array.isArray(analytics?.collection_events) ? analytics.collection_events : [];
  const range = timeRange(rangeId, now);
  const seen = new Map();
  for (const event of events) {
    if (!inRange(event?.captured_at, range)) continue;
    const key = event?.identity || postingIdentity(event?.canonical_url || event?.url);
    if (!key || seen.has(key)) continue;
    const url = String(event?.canonical_url || event?.url || '').toLocaleLowerCase('en');
    const byHost = HOST_SOURCES.find(([host]) => url.includes(host));
    seen.set(key, { source: byHost ? byHost[1] : normalSource(event?.source), language: event?.language || null });
  }
  const result = new Map();
  for (const { source, language } of seen.values()) {
    const row = result.get(source) || { fr: 0, en: 0, unknown: 0 };
    row[language === 'fr' || language === 'en' ? language : 'unknown'] += 1;
    result.set(source, row);
  }
  return result;
}

function visibleValue(value, partial = false) {
  return value == null ? '—' : `${NUMBER(value)}${partial ? '+' : ''}`;
}

function applicationSummary(ctx, range) {
  const base = roleCounts(ctx, range);
  return { ...base, value: base.counts.applied, rejected: base.counts.rejected,
    rejectedItems: base.rejectedItems, replies: base.counts.replies };
}

function usageCounter(usage, id = null) {
  if (!usage || typeof usage !== 'object' || usage.approximate === true) return null;
  const scope = String(usage.token_scope || '').toLocaleLowerCase('en');
  if (usage.usage_scope === 'worker_thread_cumulative') {
    const observed = Date.parse(String(usage.usage_observed_at || ''));
    const threadId = String(usage.usage_thread_id || '').trim();
    const basis = usage.usage_counter_basis;
    const trustedBasis = basis === 'codex_appserver_thread_token_usage' ||
      basis === 'codex_rollout_token_count' && usage.usage_source_integrity_verified === true;
    const value = usage.usage_binding_verified === true &&
      trustedBasis &&
      threadId && Number.isFinite(observed) ? tokenTotal(usage.recorded_tokens) : null;
    return Number.isFinite(value) && value >= 0
      ? { value, kind: 'worker_thread', scope: 'thread_cumulative', id: threadId, basis }
      : null;
  }
  if (!['request', 'recorded_request', 'task', 'stage', 'job'].includes(scope)) return null;
  const exact = tokenTotal(usage.exact_tokens ?? usage.exact_token_usage);
  const recorded = tokenTotal(usage.recorded_tokens ?? usage.token_usage);
  const value = exact ?? recorded;
  if (!Number.isFinite(value) || value < 0) return null;
  const identity = String(usage.counter_id || usage.usage_id || usage.counter_identity || id || scope);
  return { value, kind: exact == null ? 'reported' : 'exact', scope, id: identity };
}

function usageCountersForRun(run) {
  const runCounter = usageCounter(run?.usage, 'run');
  if (runCounter && runCounter.kind !== 'worker_thread') return [runCounter];
  const stages = Array.isArray(run?.stage_metrics) ? run.stage_metrics : [];
  const counters = [];
  const seen = new Set();
  if (runCounter) {
    const key = `worker:${runCounter.id}`;
    seen.add(key);
    counters.push(runCounter);
  }
  for (const stage of stages) {
    const id = String(stage?.id || 'stage');
    const usages = [
      ...(Array.isArray(stage?.usage_counters) ? stage.usage_counters : []),
      stage?.usage,
    ];
    for (const usage of usages) {
      const counter = usageCounter(usage, `stage:${id}`);
      if (!counter) continue;
      const key = counter.kind === 'worker_thread'
        ? `worker:${counter.id}` : `${counter.scope}:${counter.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      counters.push(counter);
    }
  }
  return counters;
}

function runPerformance(ctx, range) {
  const state = ctx.state || {};
  const unavailable = Boolean(state.errors?.runs);
  const runs = runsFor(ctx).filter(run => run?.kind !== 'legacy_stage');
  const datedRuns = range.id === 'all' ? runs : runs.filter(run => runStart(run) && inRange(runStart(run), range));
  const unknownDateRuns = runs.filter(run => !parisDay(runStart(run))).length;
  const runCount = unavailable ? null : range.id === 'all' ? runs.length : datedRuns.length;
  const timed = datedRuns.map(run => run.duration_seconds ?? run.timing?.duration_seconds)
    .filter(value => Number.isFinite(value) && value >= 0);
  const averageDuration = timed.length ? timed.reduce((sum, value) => sum + value, 0) / timed.length : null;
  const untimedRuns = Math.max(0, datedRuns.length - timed.length);
  const seenWorkers = new Set();
  const usageByRun = datedRuns.map(run => usageCountersForRun(run).filter(counter => {
    if (counter.kind !== 'worker_thread') return true;
    if (seenWorkers.has(counter.id)) return false;
    seenWorkers.add(counter.id);
    return true;
  }));
  const usageRows = usageByRun.flat();
  const usageRuns = usageByRun.filter(rows => rows.length > 0).length;
  const reportedPerRun = usageByRun.filter(rows => rows.length > 0)
    .map(rows => rows.reduce((sum, row) => sum + row.value, 0));
  const averageTokens = reportedPerRun.length
    ? reportedPerRun.reduce((sum, value) => sum + value, 0) / reportedPerRun.length : null;
  const quotaRows = datedRuns.map(quotaDeltaForRun).filter(Boolean);
  const quotaWindows = new Set(quotaRows.map(row => `${row.identity}|approximate:${row.approximate}`));
  const averageQuotaUsed = quotaRows.length && quotaWindows.size === 1
    ? quotaRows.reduce((sum, row) => sum + row.delta, 0) / quotaRows.length : null;
  const exactUsageCounters = usageRows.filter(row => row.kind === 'exact').length;
  const workerUsageCounters = usageRows.filter(row => row.kind === 'worker_thread').length;
  const rolloutUsageCounters = usageRows.filter(row => row.kind === 'worker_thread' &&
    row.basis === 'codex_rollout_token_count').length;
  const rolesUnavailable = Boolean(state.errors?.roles);
  const cvRoles = rolesFor(ctx).filter(role => role?.canonical_cv_available === true);
  const datedCvRoles = cvRoles.filter(role => role.cv_built_at && inRange(role.cv_built_at, range));
  const unknownCvDates = cvRoles.filter(role => !parisDay(role.cv_built_at)).length;
  const cvs = rolesUnavailable ? null : range.id === 'all' ? cvRoles.length : datedCvRoles.length;
  return {
    unavailable, runCount, unknownDateRuns, averageDuration, timedRuns: timed.length, untimedRuns,
    cvs, unknownCvDates, averageTokens, averageQuotaUsed, quotaRows: quotaRows.length,
    averageQuotaApproximate: quotaRows.length > 0 && quotaRows.every(row => row.approximate),
    usageRuns, usageCounters: usageRows.length,
    exactUsageCounters, workerUsageCounters, rolloutUsageCounters,
    usageOmittedRuns: Math.max(0, datedRuns.length - usageRuns),
    usageCoverageRuns: datedRuns.length,
  };
}

function shortlistWithAppliedFloor(shortlist, apps, range) {
  if (range.id !== 'all' || apps.unavailable || !Number.isFinite(apps.value) ||
      !Number.isFinite(shortlist.value) || apps.value <= shortlist.value) return shortlist;
  return { ...shortlist, value: apps.value, partial: true, estimatedFloor: apps.value };
}

const FRANCE_CITIES = new Map([
  ['aix en provence', 'Aix-en-Provence'], ['annecy', 'Annecy'], ['angers', 'Angers'],
  ['bordeaux', 'Bordeaux'], ['brest', 'Brest'], ['clermont ferrand', 'Clermont-Ferrand'],
  ['dijon', 'Dijon'], ['grenoble', 'Grenoble'], ['le mans', 'Le Mans'], ['lille', 'Lille'],
  ['lyon', 'Lyon'], ['marseille', 'Marseille'], ['montpellier', 'Montpellier'],
  ['nantes', 'Nantes'], ['nice', 'Nice'], ['nimes', 'Nîmes'], ['orleans', 'Orléans'],
  ['paris', 'Paris'], ['reims', 'Reims'], ['rennes', 'Rennes'], ['rouen', 'Rouen'],
  ['strasbourg', 'Strasbourg'], ['toulon', 'Toulon'], ['toulouse', 'Toulouse'],
  ['tours', 'Tours'], ['la rochelle', 'La Rochelle'],
]);
const GREATER_PARIS_SUBURBS = [
  'boulogne billancourt', 'courbevoie', 'la defense', 'levallois perret', 'nanterre',
  'neuilly sur seine', 'puteaux', 'rueil malmaison', 'saint denis', 'saint ouen',
  'suresnes', 'clichy', 'issy les moulineaux', 'vincennes', 'montreuil', 'creteil',
  'ivry sur seine', 'aubervilliers', 'pantin', 'bobigny', 'noisy le grand',
  'evry courcouronnes', 'versailles', 'guyancourt', 'massy', 'orsay', 'saclay',
  'velizy villacoublay',
];

function folded(value) {
  return String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('fr').replace(/[’']/g, ' ').replace(/[-‐‑‒–—]/g, ' ')
    .replace(/\s+/g, ' ').trim();
}

function locationName(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;
  const normalized = folded(raw);
  const foreign = /\b(usa|united states|canada|texas|new york|california|united kingdom|uk|germany|deutschland|italy|spain|switzerland|belgium)\b/i.test(normalized);
  if (foreign) return raw.replace(/\s*[,;]\s*/g, ', ').replace(/\s+/g, ' ');
  const postal = raw.match(/\b(75|77|78|91|92|93|94|95)\d{3}\b/);
  const idfRegion = /\b(?:ile de france|greater paris|paris saclay)\b/.test(normalized);
  const parisCity = /\bparis(?:\s*\([^)]*\))?(?:\s+(?:\d{1,2}(?:er|e|eme)?|\d{5}))?\b/.test(normalized);
  const suburb = GREATER_PARIS_SUBURBS.some(name => normalized.includes(name));
  if (postal || idfRegion || parisCity || suburb) return 'Greater Paris';
  const withoutCountry = raw.replace(/\s*,\s*france\s*$/i, '').trim();
  const cityPart = withoutCountry.split(/[;,]/, 1)[0]
    .replace(/\s+\d{5}\b/g, '').replace(/\s*\((?:\d{2,3}|75|77|78|91|92|93|94|95)\)\s*/g, ' ')
    .replace(/\s+/g, ' ').trim();
  const cityKey = folded(cityPart);
  const canonicalCity = FRANCE_CITIES.get(cityKey);
  const country = /\bfrance\b/i.test(raw) || canonicalCity ? ', France' : '';
  return `${canonicalCity || cityPart}${country}`.replace(/\s*[,;]\s*/g, ', ').replace(/\s+/g, ' ');
}

function roleDistribution(ctx, range) {
  const roles = rolesFor(ctx);
  const unavailable = Boolean(ctx.state?.errors?.roles);
  const selected = roles.filter(role => inRange(roleDate(role), range));
  const undated = roles.filter(role => !parisDay(roleDate(role))).length;
  const language = { fr: 0, en: 0, unknown: 0 };
  const offices = new Map();
  for (const role of selected) {
    const languageId = typeof ctx.languageGroup === 'function' ? ctx.languageGroup(role.language) : 'unknown';
    language[languageId in language ? languageId : 'unknown'] += 1;
    const office = locationName(role.location);
    if (office) offices.set(office, (offices.get(office) || 0) + 1);
  }
  return { selected, language, offices: [...offices].map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)).slice(0, 6), undated, unavailable };
}

function languagePanel(model, range) {
  if (model.unavailable) return `<section class="panel db-panel db-compact"><div class="db-section-heading"><div><h2>Advert language</h2><p>Saved job descriptions</p></div></div><p class="db-muted">Role records are unavailable.</p></section>`;
  const rows = [['French', model.language.fr, 'fr'], ['English', model.language.en, 'en'], ['Other / unknown', model.language.unknown, 'other']];
  const total = rows.reduce((sum, [, count]) => sum + count, 0);
  let boundary = 0;
  const stops = rows.map(([, count, key]) => {
    const start = boundary;
    boundary += total ? count / total * 100 : 0;
    return `var(--db-language-${key}) ${start.toFixed(2)}% ${boundary.toFixed(2)}%`;
  });
  const gradient = total ? `conic-gradient(${stops.join(', ')})` : 'conic-gradient(var(--line) 0 100%)';
  const note = model.undated ? range.id === 'all'
    ? `${NUMBER(model.undated)} descriptions have no capture date; included in All time.`
    : `${NUMBER(model.undated)} descriptions have no capture date; excluded from ${e(range.label)}.` : '';
  return `<section class="panel db-panel db-compact" aria-labelledby="db-language-title"><div class="db-section-heading"><div><h2 id="db-language-title">Advert language</h2></div></div><div class="db-language-chart"><div class="db-language-donut" role="img" aria-label="${e(rows.map(([label, count]) => `${label} ${percent(count, total)}`).join(', '))}" style="background:${gradient}"><span>${NUMBER(total)}</span></div><div class="db-language-legend">${rows.map(([label, count, key]) => `<div class="db-language-legend-row"><i class="db-language-dot db-language-${key}"></i><span>${e(label)}</span><strong>${NUMBER(count)}</strong><b>${percent(count, total)}</b></div>`).join('')}</div></div>${note ? `<p class="db-note">${note}</p>` : ''}</section>`;
}

function officesPanel(model) {
  const offices = model.offices;
  const max = Math.max(1, ...offices.map(row => row.count));
  return `<section class="panel db-panel db-compact" aria-labelledby="db-office-title"><div class="db-section-heading"><div><h2 id="db-office-title">Location</h2></div></div>${model.unavailable ? '<p class="db-muted">Role records are unavailable.</p>' : offices.length ? `<div class="db-office-list">${offices.map(row => `<div class="db-office-row"><span>${e(row.label)}</span><div class="db-bar-track"><i style="width:${Math.max(2, Math.round(row.count / max * 100))}%"></i></div><strong>${NUMBER(row.count)}</strong></div>`).join('')}</div>` : '<p class="db-muted">No office location is recorded for this period.</p>'}</section>`;
}

function categoriesFor(catalog) {
  return Array.isArray(catalog?.categories) ? catalog.categories : [];
}

function publicSourceUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function filteredCategory(category, catalog, range) {
  const documentDates = new Map((Array.isArray(catalog?.documents) ? catalog.documents : [])
    .map(doc => [doc.id || doc.source, doc.captured_at]));
  const terms = [];
  let undated = 0;
  for (const row of Array.isArray(category?.terms) ? category.terms : []) {
    const hits = (Array.isArray(row.hits) ? row.hits : []).filter(hit => {
      const capturedAt = hit.captured_at || documentDates.get(hit.document_id || hit.source) || null;
      if (!parisDay(capturedAt)) { undated += 1; return range.id === 'all'; }
      return inRange(capturedAt, range);
    });
    if (hits.length) terms.push({ ...row, hits, count: hits.length });
  }
  terms.sort((a, b) => b.count - a.count || String(a.term).localeCompare(String(b.term)));
  return { ...category, terms, undated };
}

function jdHitsPanel(ctx, range) {
  const catalog = ctx.state?.jdInsights;
  if (!catalog || ctx.state?.errors?.jdInsights) {
    return `<section class="panel db-panel db-jd" aria-labelledby="db-jd-title"><div class="db-section-heading"><div><h2 id="db-jd-title">JD requirements &amp; mentions</h2><p>Literal terms found in saved adverts</p></div></div><p class="db-muted">${e(ctx.state?.errors?.jdInsights || 'Saved advert analysis is loading.')}</p></section>`;
  }
  const categories = categoriesFor(catalog).map(category => filteredCategory(category, catalog, range));
  const preferred = ['it', 'soft_skills', 'languages', 'tasks', 'experience_years', 'other'];
  categories.sort((a, b) => preferred.indexOf(a.id) - preferred.indexOf(b.id));
  const requested = ctx.state?.jdCategory;
  const active = categories.find(category => category.id === requested) || categories[0];
  if (!active) return `<section class="panel db-panel db-jd"><h2 id="db-jd-title">JD requirements &amp; mentions</h2><p class="db-muted">No saved advert descriptions were found.</p></section>`;
  const requestedTerm = ctx.state?.jdTerm;
  const activeTerm = active.terms.find(term => term.term === requestedTerm) || active.terms[0] || null;
  const max = Math.max(1, ...active.terms.map(term => term.count));
  const docsById = new Map((Array.isArray(catalog.documents) ? catalog.documents : [])
    .map(doc => [doc.id || doc.source, doc]));
  const coverage = catalog.coverage || {};
  const unknownDocs = Number(coverage.undated_documents) || 0;
  const selectedNote = range.id === 'all'
    ? `${NUMBER(unknownDocs)} saved descriptions have no captured date; all-time results include their literal matches.`
    : `${NUMBER(unknownDocs)} saved descriptions have no captured date and are excluded from ${e(range.label)}.`;
  return `<section class="panel db-panel db-jd" aria-labelledby="db-jd-title"><div class="db-section-heading"><div><h2 id="db-jd-title">JD requirements &amp; mentions</h2><p>Mentions in saved JDs</p></div><span class="db-evidence-tag">Saved text only</span></div>
    <div class="db-category-tabs" role="tablist" aria-label="Requirement categories">${categories.map(category => `<button role="tab" type="button" class="db-category-tab${category.id === active.id ? ' is-selected' : ''}" data-jd-category="${e(category.id)}" aria-selected="${category.id === active.id}"><span>${e(category.label)}</span><b>${NUMBER(category.terms.reduce((sum, term) => sum + term.count, 0))}</b></button>`).join('')}</div>
    ${active.terms.length ? `<div class="db-jd-layout"><div class="db-term-bars" role="list" aria-label="${e(active.label)} terms">${active.terms.slice(0, 12).map(term => `<button type="button" role="listitem" class="db-term-row${term.term === activeTerm?.term ? ' is-selected' : ''}" data-jd-term="${e(term.term)}" aria-pressed="${term.term === activeTerm?.term}"><span class="db-term-name" title="${e(term.term)}">${e(term.term)}</span><span class="db-bar-track"><i style="width:${Math.max(2, Math.round(term.count / max * 100))}%"></i></span><b>${NUMBER(term.count)}</b></button>`).join('')}</div>
      <div class="db-jd-detail"><div class="db-jd-detail-head"><div><small>SELECTED MENTION</small><h3>${e(activeTerm?.term || '')}</h3></div><span>${plural(activeTerm?.count || 0, 'literal mention')}</span></div>${activeTerm?.hits?.length ? `<ul class="db-quote-list">${activeTerm.hits.slice(0, 30).map(hit => {
        const doc = docsById.get(hit.document_id || hit.source) || {};
        const company = hit.company || doc.company;
        const title = hit.title || doc.title;
        const sourceLabel = hit.source || doc.source || 'Source not recorded';
        const capturedAt = hit.captured_at || doc.captured_at;
        const sourceUrl = publicSourceUrl(hit.source_url || doc.source_url);
        const source = sourceUrl
          ? `<a href="${e(sourceUrl)}" target="_blank" rel="noopener noreferrer">${e(sourceUrl)}</a> · ${e(sourceLabel)}`
          : e(sourceLabel);
        return `<li><blockquote>“${e(hit.quote || '')}”</blockquote><p>${e([company, title].filter(Boolean).join(' · ') || 'Saved advert')}${capturedAt ? ` · ${e(date(capturedAt, { year: undefined }))}` : ' · captured date unknown'}</p><small>${source}</small></li>`;
      }).join('')}</ul>${activeTerm.hits.length > 30 ? `<p class="db-note">Showing 30 of ${NUMBER(activeTerm.hits.length)} literal mentions.</p>` : ''}` : '<p class="db-muted">No matching saved source quote is available.</p>'}</div></div>` : '<p class="db-muted">No matching terms were found in this date range.</p>'}
    <p class="db-note">${selectedNote} ${coverage.truncated || coverage.omitted_documents ? 'The saved advert scan reached its documented bound.' : ''} Literal matches. Open a quote to check whether a term is required or preferred. Proficiency is not inferred.</p></section>`;
}

function filterBar(range) {
  return `<nav class="db-filter" aria-label="Filter all Home sections by Paris time">${FILTERS.map(([id, label]) => `<button type="button" data-home-timeframe="${id}" class="db-filter-btn${range.id === id ? ' is-selected' : ''}" aria-pressed="${range.id === id}">${label}</button>`).join('')}<span class="db-filter-zone">${icon('clock')}Paris time</span></nav>`;
}

function topCards(ctx, apps) {
  const rolesUnavailable = Boolean(ctx.state?.errors?.roles);
  const applied = rolesUnavailable ? null : apps.value;
  const dated = (ctx.state?.homeTimeframe || 'today') !== 'all';
  const rows = [
    { label: 'Replies', value: rolesUnavailable ? '—' : NUMBER(apps.replies), note: rolesUnavailable ? 'Role records unavailable' : dated ? 'Replies received in this period' : applied ? `${percent(apps.replies, applied)} of recorded applications` : 'No recorded applications in this period', accent: 'blue' },
    { label: 'Rejected', value: rolesUnavailable ? '—' : NUMBER(apps.rejected), note: rolesUnavailable ? 'Role records unavailable' : apps.unknown.rejected ? `${NUMBER(apps.unknown.rejected)} without a saved event date` : '', accent: 'rose' },
  ];
  const appliedNote = rolesUnavailable ? 'Role records unavailable' : apps.unknown.applied ? `${NUMBER(apps.unknown.applied)} application records have no saved event date` : '';
  return `<section class="db-topline" aria-label="Job search outcomes"><article class="db-applied-card"><div><p class="db-card-kicker">APPLICATIONS</p><h2>Applied</h2></div><strong>${rolesUnavailable ? '—' : visibleValue(applied)}</strong>${appliedNote ? `<small>${e(appliedNote)}</small>` : ''}</article><div class="db-outcome-cards">${rows.map(row => `<article class="db-outcome-card db-accent-${row.accent}"><span>${e(row.label)}</span><strong>${e(row.value)}</strong>${row.note ? `<small>${e(row.note)}</small>` : ''}</article>`).join('')}</div></section>`;
}

function funnelPanel(read, shortlist, range) {
  const steps = [
    { id: 'read', label: 'Unique full ads read', value: read.value, partial: read.partial },
    { id: 'shortlisted', label: 'Validated JDs chosen for CV', value: shortlist.value, partial: shortlist.partial },
  ];
  const max = Math.max(1, ...steps.map(step => Number.isFinite(step.value) ? step.value : 0));
  return `<section class="panel db-panel db-funnel" aria-labelledby="db-funnel-title"><div class="db-section-heading"><div><h2 id="db-funnel-title">Your search funnel</h2><p>Read ads and validated JDs selected for a tailored CV</p></div></div><div class="db-funnel-list">${steps.map((step, index) => {
    const width = Number.isFinite(step.value) ? Math.max(step.value ? 4 : 0, Math.round(step.value / max * 100)) : 0;
    return `<div class="db-funnel-step db-funnel-${step.id}"><div class="db-funnel-order">${String(index + 1).padStart(2, '0')}</div><div class="db-funnel-main"><div class="db-funnel-label"><strong>${e(step.label)}</strong></div><div class="db-bar-track"><i style="width:${width}%"></i></div></div><strong class="db-funnel-value">${visibleValue(step.value, step.partial)}</strong></div>`;
  }).join('')}</div></section>`;
}

function sourcePanel(ctx, range, shortlist) {
  const rolesUnavailable = Boolean(ctx.state?.errors?.roles);
  const apps = applicationSummary(ctx, range);
  const shortlistPending = analyticsFor(ctx).loading || analyticsFor(ctx).unavailable;
  const counts = new Map();
  for (const [id, row] of shortlist.sourceEvents) counts.set(id, { shortlisted: row.shortlisted, applied: 0, replies: 0 });
  for (const [id, row] of apps.sourceEvents) {
    const current = counts.get(id) || { shortlisted: 0, applied: 0, replies: 0 };
    current.applied = row.applied;
    current.replies = row.replies;
    counts.set(id, current);
  }
  const rows = [...counts].map(([id, values]) => ({ id, label: SOURCE_NAMES[id] || id, ...values }))
    .sort((a, b) => b.shortlisted - a.shortlisted || b.applied - a.applied || a.label.localeCompare(b.label));
  const max = Math.max(1, ...rows.flatMap(row => [row.shortlisted, row.applied, row.replies]));
  const unknownDates = apps.unknown.applied + apps.unknown.replies + shortlist.undated;
  const dateNote = unknownDates ? range.id === 'all'
    ? `${NUMBER(unknownDates)} events have no saved date and remain in All time.`
    : `${NUMBER(unknownDates)} events have no date and are excluded from ${e(range.label)}.` : '';
  const historicalNote = shortlistPending ? '<p class="db-note">Shortlist counts will appear after saved evidence is checked.</p>' : range.id === 'all' && (shortlist.historicalAdjustment || historicalAdjustments(ctx).read)
    ? '<p class="db-note">Historical estimates have no source allocation; these rows show saved evidence only.</p>' : '';
  return `<section class="panel db-panel db-sources" aria-labelledby="db-sources-title"><div class="db-section-heading"><div><h2 id="db-sources-title">Source outcomes</h2></div></div><div class="db-source-head"><span>Source</span><span>Shortlisted</span><span>Applied</span><span>Replied</span></div>${rolesUnavailable ? '<p class="db-muted">Application source counts are unavailable.</p>' : rows.length ? `<div class="db-source-list">${rows.map(row => `<div class="db-source-row"><strong>${e(row.label)}</strong><span><i style="width:${Math.max(row.shortlisted ? 3 : 0, Math.round(row.shortlisted / max * 100))}%"></i><b>${shortlistPending ? '—' : NUMBER(row.shortlisted)}</b></span><span><i style="width:${Math.max(row.applied ? 3 : 0, Math.round(row.applied / max * 100))}%"></i><b>${NUMBER(row.applied)}</b></span><span><i style="width:${Math.max(row.replies ? 3 : 0, Math.round(row.replies / max * 100))}%"></i><b>${NUMBER(row.replies)}</b></span></div>`).join('')}</div>` : '<p class="db-muted">No source outcomes are recorded in this period.</p>'}${dateNote ? `<p class="db-note">${dateNote}</p>` : ''}${historicalNote}</section>`;
}

function runPanel(metrics, range, ctx) {
  const runsUnavailable = Boolean(ctx.state?.errors?.runs);
  const rolesUnavailable = Boolean(ctx.state?.errors?.roles);
  const quotaDisplay = Number.isFinite(metrics.averageQuotaUsed)
    ? `${metrics.averageQuotaApproximate ? '~' : ''}${new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 }).format(metrics.averageQuotaUsed)}%` : '—';
  const baseUsageNote = runsUnavailable ? 'Run history unavailable' : metrics.usageCoverageRuns === 0
    ? 'No runs in this period'
    : metrics.usageRuns === 0
      ? `No token counters recorded for ${NUMBER(metrics.usageCoverageRuns)} runs`
      : `Recorded for ${NUMBER(metrics.usageRuns)} of ${NUMBER(metrics.usageCoverageRuns)} runs; average uses runs with counters`;
  const usageNote = metrics.rolloutUsageCounters > 0
    ? `${baseUsageNote}. Selected worker-thread totals only; cached input is included and whole-task usage is unknown.`
    : baseUsageNote;
  const quotaNote = runsUnavailable ? 'Run history unavailable' : metrics.usageCoverageRuns === 0
    ? 'No runs in this period'
    : metrics.quotaRows === 0
      ? 'No saved before-and-after quota change for this period'
      : metrics.averageQuotaUsed == null
        ? 'Saved quota changes span different provider windows'
        : `Average saved account-wide change from ${NUMBER(metrics.quotaRows)} readings`;
  const cvNote = rolesUnavailable ? 'Role catalog unavailable' : metrics.unknownCvDates
    ? range.id === 'all' ? `${NUMBER(metrics.unknownCvDates)} PDF dates unknown; included in All time` : `${NUMBER(metrics.unknownCvDates)} PDF dates unknown; excluded from ${range.label}`
    : '';
  const values = [
    ['Runs', runsUnavailable ? null : metrics.runCount, runsUnavailable ? 'Run history unavailable' : metrics.unknownDateRuns ? `${NUMBER(metrics.unknownDateRuns)} without a start date` : ''],
    ['Average duration', metrics.averageDuration == null ? '—' : duration(metrics.averageDuration), runsUnavailable ? 'Run history unavailable' : metrics.untimedRuns ? `${NUMBER(metrics.untimedRuns)} runs untimed` : metrics.timedRuns ? `From ${NUMBER(metrics.timedRuns)} timed runs` : 'No recorded durations'],
    ['Average usage', runsUnavailable ? '—' : metrics.averageTokens == null ? '—' : compactTokens(metrics.averageTokens), usageNote],
    ['Quota used', runsUnavailable ? '—' : quotaDisplay, quotaNote],
    ['CVs built', rolesUnavailable ? '—' : NUMBER(metrics.cvs), cvNote || 'Canonical tailored CV PDFs in the role catalog'],
  ];
  return `<section class="panel db-panel db-performance" aria-labelledby="db-performance-title"><div class="db-section-heading"><div><h2 id="db-performance-title">Automation performance</h2><p>${e(range.label)}</p></div><a class="text-link" href="#activity" data-nav="activity">History${icon('arrow')}</a></div><div class="db-performance-grid">${values.map(([label, value, note]) => `<article><span>${e(label)}</span><strong>${typeof value === 'number' ? NUMBER(value) : e(value == null ? '—' : String(value))}</strong>${note ? `<small>${e(note)}</small>` : ''}</article>`).join('')}</div></section>`;
}

function savedAdvertsPanel(ctx) {
  const inventory = analyticsFor(ctx).saved_adverts;
  const heading = '<div class="db-section-heading"><div><h2 id="db-saved-adverts-title">Saved advert library</h2><p>All time · One advert per website ID</p></div></div>';
  if (inventory?.available !== true) return `<section class="panel db-panel db-saved-adverts" aria-labelledby="db-saved-adverts-title">${heading}<p class="db-muted">Saved advert totals are not available yet.</p></section>`;
  const latest = inventory.latest_run || {};
  const cards = [
    ['Unique saved adverts', inventory.unique_postings],
    ['New in latest Hunt', latest.new_postings],
    ['Seen again in latest Hunt', latest.repeated_postings],
    ['Repeat captures · all time', inventory.repeat_captures],
    ['Shortlisted saved adverts', inventory.shortlisted_postings],
  ];
  const labels = { ...SOURCE_NAMES, wttj: 'Welcome to the Jungle' };
  const rows = Array.isArray(inventory.by_website) ? inventory.by_website : [];
  return `<section class="panel db-panel db-saved-adverts" aria-labelledby="db-saved-adverts-title">${heading}<div class="db-inventory-kpis">${cards.map(([label, value]) => `<article><span>${e(label)}</span><strong>${NUMBER(value)}</strong></article>`).join('')}</div><div class="db-inventory-table"><div class="db-inventory-row db-inventory-head"><span>Website</span><span>Unique adverts</span><span>Shortlisted</span></div>${rows.map(row => `<div class="db-inventory-row" data-inventory-source="${e(row.website)}"><strong>${e(labels[row.website] || row.website)}</strong><span>${NUMBER(row.unique_postings)}</span><span>${NUMBER(row.shortlisted_postings)}</span></div>`).join('')}</div><p class="db-note">Library totals cover indexed saved history and stay the same when you change the period above. The same vacancy on two websites counts once on each. New means first recorded in the indexed history; unknown dates stay blank. Shortlisted counts require an accepted audit decision.</p>${inventory.coverage_note ? `<p class="db-note">${e(inventory.coverage_note)}</p>` : ''}</section>`;
}

function liveNote(ctx) {
  const live = ctx.live || ctx.state?.overview?.live_pipeline;
  if (!live?.running && !live?.active) return '';
  return `<div class="db-live-note" role="status"><span class="db-live-dot"></span><span><strong>Automation is running.</strong> New saved evidence will appear after it is recorded.</span><a href="#activity" data-nav="activity">View history${icon('arrow')}</a></div>`;
}

function greeting() {
  const hour = Number(new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hour12: false, timeZone: PARIS }).format(new Date()));
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

function nextStep(ctx) {
  const unavailable = Boolean(ctx.state?.errors?.roles);
  const ready = rolesFor(ctx).filter(role => appRecord(role, ctx).status === 'ready').length;
  return `<section class="db-next-step" aria-label="Your next step">
    <div class="db-next-icon" aria-hidden="true">${icon('upRight')}</div>
    <div><p class="db-card-kicker">NEXT STEP</p>
    <h2>${unavailable ? 'Your workspace needs a refresh' : ready ? `${plural(ready, 'opportunity', 'opportunities')} ready for you` : 'Make room for your next opportunity'}</h2>
    <p>${unavailable ? 'Role records could not be loaded. Try refreshing the workspace.' : ready ? 'Review your tailored CVs, choose a role, and take the next step.' : 'Save a job in Apply, or review your search brief to keep your search focused.'}</p></div>
    <a class="button button-primary" href="#apply" data-nav="apply">Open Apply${icon('arrow')}</a>
  </section>`;
}

export function renderDashboard(ctx) {
  const state = ctx.state || {};
  const range = timeRange(state.homeTimeframe || 'today');
  const read = readSummary(ctx, range);
  const apps = applicationSummary(ctx, range);
  const shortlist = shortlistWithAppliedFloor(acceptedSummary(ctx, range), apps, range);
  const performance = runPerformance(ctx, range);
  const distribution = roleDistribution(ctx, range);
  const generated = state.overview?.generated_at;
  return `<div class="db-dashboard">
    <header class="db-hero"><div><p class="db-eyebrow">YOUR NEXT CHAPTER</p><h1>${greeting()}.</h1></div><div class="db-updated">${icon('refresh')}${generated ? `Updated ${e(date(generated, { hour: '2-digit', minute: '2-digit', year: undefined }))}` : 'Saved evidence only'}</div></header>
    ${nextStep(ctx)}
    ${filterBar(range)}
    ${liveNote(ctx)}
    ${topCards(ctx, apps)}
    ${analyticsFor(ctx).loading ? '<p class="db-analytics-status" role="status">Checking saved advert evidence. Search totals will appear when the check finishes.</p>' : state.overview?.dashboard_analytics_error ? '<p class="db-analytics-status" role="status">The latest evidence check failed. Available totals are from the last completed check.</p>' : ''}
    ${funnelPanel(read, shortlist, range)}
    ${savedAdvertsPanel(ctx)}
    <div class="db-two-col">${runPanel(performance, range, ctx)}${sourcePanel(ctx, range, shortlist)}</div>
    <div class="db-two-col">${languagePanel(distribution, range)}${officesPanel(distribution)}</div>
    ${jdHitsPanel(ctx, range)}
    <p class="db-footnote">Paris time · Shortlist means validated JDs chosen for a tailored CV. Historical totals include user-supplied estimates with no assigned date or source; source outcomes show saved evidence only. Periods use event, capture, or CV PDF dates; unknown dates are excluded from dated views. Île-de-France locations share the Greater Paris label.</p>
  </div>`;
}

// Compatibility exports for callers upgraded in separate, coordinated changes.
export function renderHomeBeta(ctx) { return renderDashboard(ctx); }
export function renderHomeApplicationKpis(ctx) {
  const range = timeRange(ctx.state?.homeTimeframe || 'today');
  const apps = applicationSummary(ctx, range);
  return `<section class="db-kpi-compat" aria-label="Application progress"><strong>Applied</strong><span>${visibleValue(apps.value, apps.unknown.applied > 0 && range.id === 'all')}</span></section>`;
}
export function renderHomeInsights(ctx) { return renderDashboard(ctx); }

// Data bridge for the colourful Home. Keep the counting rules in the existing
// Home helpers, and expose unavailable values separately from confirmed zeros.
export function buildColourHomeModel(ctx) {
  const input = ctx || {};
  const state = input.state || {};
  const overview = state.overview || null;
  const errors = { ...(state.errors || {}) };
  const analytics = overview?.dashboard_analytics;
  const now = new Date();
  const generated = overview?.generated_at;
  const capturedAt = typeof generated === 'string' && Number.isFinite(Date.parse(generated))
    ? generated : now.toISOString();
  const record = value => value && typeof value === 'object' && !Array.isArray(value);
  const count = value => Number.isFinite(value) && value >= 0 ? value : null;
  const rows = value => Array.isArray(value) ? value.filter(record) : [];
  const text = value => typeof value === 'string' ? value : '';
  const rolesStatus = errors.roles ? 'unavailable' : state.loaded === false || !Array.isArray(state.roles)
    ? 'loading' : 'available';
  const hasRuns = Array.isArray(state.runs) || Array.isArray(overview?.runs?.items);
  const runsStatus = errors.runs ? 'unavailable' : state.loaded === false || !hasRuns
    ? 'loading' : 'available';
  const analyticsStatus = analytics?.unavailable ? 'unavailable' : analytics?.loading
    ? 'loading' : record(analytics) ? 'available' : !overview || state.loaded === false
      ? 'loading' : 'unavailable';
  const catalog = state.jdInsights;
  const requirementsStatus = errors.jdInsights || catalog?.unavailable ? 'unavailable'
    : !catalog || catalog.loading ? 'loading' : 'available';
  const savedInventory = analytics?.saved_adverts;
  const inventoryStatus = savedInventory?.available === true ? 'available'
    : analyticsStatus === 'loading' || savedInventory?.loading ? 'loading' : 'unavailable';
  const availability = {
    overview: overview ? 'available' : errors.overview ? 'unavailable' : 'loading',
    roles: rolesStatus, runs: runsStatus, analytics: analyticsStatus,
    requirements: requirementsStatus, inventory: inventoryStatus,
  };
  const rolesUnavailable = rolesStatus !== 'available';
  const runsUnavailable = runsStatus !== 'available';
  const analyticsUnavailable = analyticsStatus !== 'available';
  // The helper input is a new object. Loading an independent catalogue must
  // neither mutate the app state nor make an empty initial array look complete.
  const helperErrors = { ...errors };
  if (rolesUnavailable) helperErrors.roles ||= 'Role records are unavailable.';
  if (runsUnavailable) helperErrors.runs ||= 'Run history is unavailable.';
  const helperAnalytics = analyticsUnavailable
    ? { ...(record(analytics) ? analytics : {}),
      loading: analyticsStatus === 'loading', unavailable: analyticsStatus === 'unavailable' }
    : analytics;
  const helperState = { ...state, errors: helperErrors, roles: rows(state.roles),
    overview: { ...(overview || {}), dashboard_analytics: helperAnalytics } };
  if (Array.isArray(state.runs)) helperState.runs = rows(state.runs);
  const helperCtx = { ...input, state: helperState };
  const ready = rolesUnavailable ? null : rolesFor(helperCtx)
    .filter(role => appRecord(role, helperCtx).status === 'ready').length;
  const weekBack = Math.max(0, Math.min(52, Number(input.weekOffset) || 0));  // 7-day windows back from today
  const week = applicationWeek(helperCtx, new Date(now.getTime() - weekBack * 7 * 86400000), rolesUnavailable);
  // Home beta (13 Oct): the same 7 Paris days with jobs collected and shortlisted next to applications.
  const weekSeries = week.rows.map(row => {
    const range = { id: 'day', start: row.date, end: row.date, label: row.label };
    return { ...row, applied: row.count, collected: readSummary(helperCtx, range).value ?? null,
      shortlisted: acceptedSummary(helperCtx, range).value ?? null,
      bySource: analyticsUnavailable ? {} : collectedBySourceForRange(analytics, range),
      // 15 Oct: the same day split by website for shortlisted jobs and applications.
      shortlistedBySource: analyticsUnavailable ? {} : Object.fromEntries([...(acceptedSummary(helperCtx, range).sourceEvents || [])]
        .map(([id, v]) => [id, v.shortlisted || 0]).filter(([, n]) => n)),
      appliedBySource: rolesUnavailable ? {} : Object.fromEntries([...(applicationSummary(helperCtx, range).sourceEvents || [])]
        .map(([id, v]) => [id, v.applied || 0]).filter(([, n]) => n)) };
  });
  const safeCatalog = requirementsStatus === 'available' ? {
    ...catalog,
    documents: rows(catalog.documents),
    categories: rows(catalog.categories).map(category => ({ ...category,
      terms: rows(category.terms).map(term => ({ ...term, hits: rows(term.hits) })) })),
  } : null;
  const documents = new Map((safeCatalog?.documents || []).map(doc => [doc.id || doc.source, doc]));
  const savedCoverage = safeCatalog?.coverage || {};
  const coverage = { ...savedCoverage };
  for (const key of ['documents', 'undated_documents', 'omitted_documents', 'role_jds', 'full_captures']) {
    coverage[key] = count(savedCoverage[key]);
  }
  const inventory = { ...(record(savedInventory) ? savedInventory : {}),
    available: inventoryStatus === 'available',
    latest_run: { ...(record(savedInventory?.latest_run) ? savedInventory.latest_run : {}) },
    by_website: inventoryStatus === 'available' ? rows(savedInventory.by_website).map(row => {
      const result = { ...row };
      for (const key of ['unique_postings', 'shortlisted_postings', 'saved_captures', 'repeat_captures']) {
        if (key in row || ['unique_postings', 'shortlisted_postings'].includes(key)) result[key] = count(row[key]);
      }
      return result;
    }) : [],
  };
  for (const key of ['unique_postings', 'repeat_captures', 'shortlisted_postings', 'saved_captures', 'import_refusals']) {
    inventory[key] = inventory.available ? count(savedInventory[key]) : null;
  }
  for (const key of ['unique_postings', 'new_postings', 'repeated_postings']) {
    inventory.latest_run[key] = inventory.available ? count(savedInventory.latest_run?.[key]) : null;
  }
  if (!inventory.coverage_note) inventory.coverage_note = inventoryStatus === 'loading'
    ? 'Saved advert totals are loading.' : inventory.available ? '' : 'Saved advert totals are unavailable.';
  const savedHistorical = analytics?.historical_adjustments;
  const historical = record(savedHistorical) ? { ...savedHistorical,
    recorded_counts: { ...(savedHistorical.recorded_counts || {}) } } : {
    available: false, recorded_counts: { unique_full_ads_read: null, validated_jds_chosen_for_cv: null },
  };
  const adjustment = historicalAdjustments(helperCtx);
  const periods = {};
  const wanted = Array.isArray(input.periodIds) && input.periodIds.length ? [...new Set(input.periodIds)] : FILTERS.map(([id]) => id);
  for (const id of wanted) {
    const label = timeRange(id, now).label;
    // The update timestamp is metadata. Today and the other dated views use the
    // current Paris clock, exactly as renderDashboard does.
    const range = timeRange(id, now);
    const apps = applicationSummary(helperCtx, range);
    const read = readSummary(helperCtx, range);
    const shortlist = shortlistWithAppliedFloor(acceptedSummary(helperCtx, range), apps, range);
    const rawPerformance = runPerformance(helperCtx, range);
    const performance = { ...rawPerformance };
    if (runsUnavailable) {
      for (const key of ['runCount', 'unknownDateRuns', 'averageDuration', 'timedRuns', 'untimedRuns',
        'averageTokens', 'averageQuotaUsed', 'quotaRows', 'usageRuns', 'usageCounters',
        'exactUsageCounters', 'workerUsageCounters', 'rolloutUsageCounters', 'usageOmittedRuns', 'usageCoverageRuns']) {
        performance[key] = null;
      }
    }
    if (rolesUnavailable) performance.unknownCvDates = null;
    const distribution = roleDistribution(helperCtx, range);
    const sources = new Map();
    if (!rolesUnavailable) {
      for (const [key, row] of shortlist.sourceEvents) sources.set(key, { shortlisted: row.shortlisted, applied: 0, replies: 0 });
      for (const [key, row] of apps.sourceEvents) {
        const current = sources.get(key) || { shortlisted: analyticsUnavailable ? null : 0, applied: 0, replies: 0 };
        current.applied = row.applied;
        current.replies = row.replies;
        sources.set(key, current);
      }
    }
    const sourceRows = [...sources].map(([key, row]) => ({ id: key, label: SOURCE_NAMES[key] || key, ...row }))
      .sort((a, b) => (b.shortlisted ?? 0) - (a.shortlisted ?? 0) || b.applied - a.applied || a.label.localeCompare(b.label));
    const categories = (safeCatalog?.categories || []).map(category => filteredCategory(category, safeCatalog, range))
      .map(category => ({ id: category.id, label: category.label,
        count: category.terms.reduce((sum, term) => sum + term.count, 0),
        terms: category.terms.slice(0, 12).map(term => ({ term: text(term.term), count: term.count,
          hits: term.hits.slice(0, 3).map(hit => {
            const doc = documents.get(hit.document_id || hit.source) || {};
            return { quote: text(hit.quote), company: text(hit.company || doc.company),
              title: text(hit.title || doc.title), captured: hit.captured_at || doc.captured_at || null,
              source: text(hit.source || doc.source),
              url: publicSourceUrl(hit.url || hit.source_url || doc.url || doc.source_url || '') };
          }) })),
      }));
    const baseUsageNote = runsUnavailable ? 'Run history unavailable' : performance.usageCoverageRuns === 0
      ? 'No runs in this period' : performance.usageRuns === 0
        ? `No token counters recorded for ${NUMBER(performance.usageCoverageRuns)} runs`
        : `Recorded for ${NUMBER(performance.usageRuns)} of ${NUMBER(performance.usageCoverageRuns)} runs; average uses runs with counters`;
    const usageNote = performance.rolloutUsageCounters > 0
      ? `${baseUsageNote}. Selected worker-thread totals only; cached input is included and whole-task usage is unknown.` : baseUsageNote;
    const quotaNote = runsUnavailable ? 'Run history unavailable' : performance.usageCoverageRuns === 0
      ? 'No runs in this period' : performance.quotaRows === 0
        ? 'No saved before-and-after quota change for this period' : performance.averageQuotaUsed == null
          ? 'Saved quota changes span different provider windows'
          : `Average saved account-wide change from ${NUMBER(performance.quotaRows)} readings`;
    const cvNote = rolesUnavailable ? 'Role catalog unavailable' : performance.unknownCvDates
      ? id === 'all' ? `${NUMBER(performance.unknownCvDates)} PDF dates unknown; included in All time`
        : `${NUMBER(performance.unknownCvDates)} PDF dates unknown; excluded from ${label}`
      : 'Canonical tailored CV PDFs in the role catalog';
    const quotaDisplay = Number.isFinite(performance.averageQuotaUsed)
      ? `${performance.averageQuotaApproximate ? '~' : ''}${new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 }).format(performance.averageQuotaUsed)}%` : '—';
    const performanceItems = [
      { label: 'Runs', value: NUMBER(performance.runCount), note: runsUnavailable ? 'Run history unavailable'
        : performance.unknownDateRuns ? `${NUMBER(performance.unknownDateRuns)} without a start date` : '' },
      { label: 'Average duration', value: duration(performance.averageDuration), note: runsUnavailable ? 'Run history unavailable'
        : performance.untimedRuns ? `${NUMBER(performance.untimedRuns)} runs untimed`
          : performance.timedRuns ? `From ${NUMBER(performance.timedRuns)} timed runs` : 'No recorded durations' },
      { label: 'Average usage', value: compactTokens(performance.averageTokens), note: usageNote },
      { label: 'Quota used', value: quotaDisplay, note: quotaNote },
      { label: 'CVs built', value: NUMBER(performance.cvs), note: cvNote },
    ];
    const outcomeNotes = [
      rolesUnavailable ? 'Role records unavailable' : apps.unknown.applied
        ? `${NUMBER(apps.unknown.applied)} application records have no saved event date` : '',
      rolesUnavailable ? 'Role records unavailable' : scoped(range) ? 'Replies received in this period'
        : apps.value ? `${percent(apps.replies, apps.value)} of recorded applications` : 'No recorded applications in this period',
      rolesUnavailable ? 'Role records unavailable' : apps.unknown.rejected
        ? `${NUMBER(apps.unknown.rejected)} without a saved event date` : '',
    ];
    const undated = apps.unknown.applied + apps.unknown.replies + shortlist.undated;
    const dateNote = undated ? id === 'all' ? `${NUMBER(undated)} events have no saved date and remain in All time.`
      : `${NUMBER(undated)} events have no saved date and are excluded from ${label}.` : '';
    const readNote = read.note || (analyticsStatus === 'loading' ? 'Checking saved advert evidence.'
      : analyticsStatus === 'unavailable' ? 'Saved advert evidence is unavailable.' : '');
    const collectedMode = Array.isArray(analytics?.collection_events);
    let chosenNote = analyticsUnavailable ? readNote : shortlist.estimatedFloor
      ? 'The all-time total is at least the number of recorded applications.'
      : shortlist.historicalAdjustment
        ? collectedMode
          ? `Includes ${NUMBER(shortlist.historicalAdjustment)} jobs from the saved historical estimate; dates and sources were not recorded.`
          : `Includes +${NUMBER(shortlist.historicalAdjustment)} user-supplied historical estimate; no event dates or source breakdown were supplied.`
        : shortlist.partial ? 'Known shortlisted jobs are a minimum; saved acceptance evidence has gaps.' : '';
    if (!analyticsUnavailable && shortlist.screeningEvidenceUnavailable) {
      const evidenceNote = shortlist.value == null ? 'Saved screening evidence is unavailable.'
        : 'Some saved screening evidence is unavailable; this count is a minimum.';
      chosenNote = shortlist.historicalAdjustment || shortlist.estimatedFloor
        ? `${chosenNote} ${evidenceNote}` : evidenceNote;
    }
    const funnelNote = analyticsUnavailable ? readNote : id === 'all' && (adjustment.shortlist || !collectedMode && adjustment.read)
      ? collectedMode
        ? 'Counts use saved evidence; missing records are not treated as zero.'
        : `Includes unverified historical estimates: ${NUMBER(adjustment.read)} ads read and ${NUMBER(adjustment.shortlist)} JDs chosen, with no saved date or source.`
      : scoped(range) ? 'Undated records and historical estimates are excluded from dated views.'
        : 'Counts use saved evidence; missing records are not treated as zero.';
    periods[id] = { id, label,
      applied: rolesUnavailable ? null : apps.value, replies: rolesUnavailable ? null : apps.replies,
      rejected: rolesUnavailable ? null : apps.rejected, rejectedItems: rolesUnavailable ? [] : (apps.rejectedItems || []), ready,
      replyRate: rolesUnavailable ? '—' : percent(apps.replies, apps.value),
      unknown: rolesUnavailable ? { applied: null, rejected: null, replies: null } : { ...apps.unknown },
      read: visibleValue(read.value, read.partial), chosen: visibleValue(shortlist.value, shortlist.partial),
      readValue: read.value, chosenValue: shortlist.value, readNote, chosenNote, funnelNote,
      performance, performanceItems, outcomeNotes, sources: sourceRows,
      sourceNote: rolesUnavailable ? 'Application source counts are unavailable.' : dateNote,
      language: rolesUnavailable ? { fr: null, en: null, unknown: null } : { ...distribution.language },
      languageUndated: rolesUnavailable ? null : distribution.undated,
      locations: rolesUnavailable ? [] : distribution.offices, categories, coverage: { ...coverage },
      availability: { ...availability },
      footer: 'Paris time · Shortlisted Jobs counts saved accepted decisions for tailored CVs, plus the existing all-time estimate. Jobs collected combines saved public captures and supplied Hark JD text. Unknown dates are excluded from dated views.',
    };
  }
  const notices = [];
  if (errors.overview && overview) notices.push('The latest refresh failed. This view still shows the last available snapshot.');
  if (analyticsStatus === 'loading') notices.push('Checking saved advert evidence. Search totals will appear when the check finishes.');
  else if (analyticsStatus === 'unavailable') notices.push('Saved advert evidence is unavailable. Search totals cannot be confirmed.');
  else if (overview?.dashboard_analytics_error) notices.push('The latest evidence check failed. Available totals are from the last completed check.');
  const selectedPeriod = FILTERS.some(([id]) => id === state.homeTimeframe) ? state.homeTimeframe : 'today';
  return { capturedAt, selectedPeriod, source: 'CV Studio Home, saved live evidence', periods, inventory, historical,
    applicationWeek: week.rows, applicationWeekSeries: weekSeries, applicationWeekNote: week.note, applicationWeekUndated: week.undated,
    availability, errors, notices,
    refreshing: Boolean(state.refreshing || overview?.dashboard_analytics_refreshing || state.jdInsightsLoading),
    live: { ...(input.live || overview?.live_pipeline || {}) },
  };
}
