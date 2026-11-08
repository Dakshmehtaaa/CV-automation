// Pure invented model fixtures; no browser, connectors, or production records.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'static/home-beta.js'), 'utf8')
  .replace(/^import .*;\n/m, '')
  .replace(/^export /gm, '');
const FixedDate = class extends Date {
  constructor(...args) { super(...(args.length ? args : ['2026-10-10T00:15:00Z'])); }
};
const sandbox = { Date: FixedDate, Intl, URL, Map, Set, Number, Math,
  formatNumber: value => String(value), date: value => String(value || ''),
  escape: value => String(value ?? ''), icon: () => '' };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const plain = value => JSON.parse(JSON.stringify(value));
const context = (roles, analytics = {}, errors = {}, timeframe = 'all') => ({
  state: { roles, runs: [], loaded: true, errors, homeTimeframe: timeframe,
    overview: { generated_at: '2020-01-01T00:00:00Z', dashboard_analytics: analytics } },
});
const role = (id, app, url = `https://example.org/jobs/${id}`) => ({ id, folder: `Tailored CVs/${id}`, source_url: url, application: app });
const model = input => plain(sandbox.buildColourHomeModel(input));
let checks = 0;
function test(name, fn) { fn(); checks++; console.log(`PASS ${name}`); }

test('Seven Paris days include today and ignore selected Home period', () => {
  const ctx = context([role('one', { status: 'rejected', applied_at: '2026-10-08T22:30:00Z', rejected_at: '2026-10-10T00:00:00Z' })]);
  const first = model(ctx);
  assert.deepEqual(first.applicationWeek.map(x => x.date), ['2026-10-04','2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-09','2026-10-10']);
  assert.equal(first.applicationWeek.find(x => x.date === '2026-10-09').count, 1);
  assert.equal(first.applicationWeek.find(x => x.date === '2026-10-10').count, 0);
  ctx.state.homeTimeframe = 'yesterday';
  assert.deepEqual(model(ctx).applicationWeek, first.applicationWeek);
});

test('Aliases count once; earliest applied event is used', () => {
  const value = model(context([
    role('alias-one', { status: 'offer', applied_at: '2026-10-09T12:00:00Z', status_history: [{ status: 'applied', at: '2026-10-06T09:00:00Z' }] }, 'https://linkedin.com/jobs/view/123'),
    role('alias-two', { status: 'applied', applied_at: '2026-10-09T12:00:00Z' }, 'https://www.linkedin.com/jobs/view/analyst-123?trk=test'),
  ]));
  assert.equal(value.applicationWeek.reduce((sum, row) => sum + row.count, 0), 1);
  assert.equal(value.applicationWeek.find(row => row.date === '2026-10-06').count, 1);
});

test('Undated applications and later offers are excluded', () => {
  const value = model(context([
    role('undated', { status: 'applied', canonical_applied: true }),
    role('offered', { status: 'offer', offer_at: '2026-10-10T00:00:00Z' }),
  ]));
  assert.equal(value.applicationWeek.reduce((sum, row) => sum + row.count, 0), 0);
  assert.equal(value.applicationWeekUndated, 2);
  assert.match(value.applicationWeekNote, /2 recorded applications.*excluded/);
});

test('Unavailable role catalog produces seven unknowns', () => {
  const value = model(context([], {}, { roles: 'Unavailable' }));
  assert.equal(value.applicationWeek.length, 7);
  assert.ok(value.applicationWeek.every(row => row.count === null));
  assert.equal(value.applicationWeekUndated, null);
});

test('Collection mode excludes historical read estimate', () => {
  const value = model(context([], {
    unique_jobs_collected: 2, collection_events_partial: false,
    collection_events: [
      { identity: 'posting:linkedin:123', captured_at: '2026-10-08T23:30:00Z' },
      { identity: 'posting:linkedin:124', captured_at: null },
    ],
    hark_feed: { available: true, captured_at_utc: '2026-10-10T00:00:00Z' },
    historical_adjustments: { available: true, recorded_counts: { unique_full_ads_read: 246, validated_jds_chosen_for_cv: 80 } },
  }));
  assert.equal(value.periods.all.readValue, 2);
  assert.equal(value.periods.yesterday.readValue, 1);
  assert.equal(value.periods.all.chosenValue, 80);
  assert.doesNotMatch(value.periods.all.funnelNote, /246 ads read/);
  assert.match(value.periods.all.chosenNote, /80 jobs from the saved historical estimate/);
  assert.doesNotMatch(value.periods.all.funnelNote, /80/);
  assert.match(value.periods.yesterday.readNote, /1 jobs lack a saved collection date/);
});

test('Old analytics fallback retains saved read-estimate semantics', () => {
  const value = model(context([], { unique_jobs_read: 1,
    historical_adjustments: { available: true, recorded_counts: { unique_full_ads_read: 246, validated_jds_chosen_for_cv: 80 } },
  }));
  assert.equal(value.periods.all.readValue, 247);
  assert.match(value.periods.all.funnelNote, /246 ads read/);
});

test('Sheet ACCEPTED events merge with existing canonical URLs', () => {
  const value = model(context([], {
    manual_shortlists: [{ canonical_url: 'https://linkedin.com/jobs/view/123', accepted_at: '2026-10-09T09:00:00Z', source: 'linkedin' }],
    sheet_shortlists: [{ identity: 'posting:linkedin:123', canonical_url: 'https://linkedin.com/jobs/view/analyst-123', accepted_at: '2026-10-09T10:00:00Z', source: 'hark' },
      { identity: 'job:invented', accepted_at: '2026-10-09T10:00:00Z', source: 'hark' }],
  }));
  assert.equal(value.periods.all.chosenValue, 2);
  assert.equal(value.periods.yesterday.chosenValue, 2);
});

test('All supported shortlist aliases merge transitively across different hosts', () => {
  const ctx = context([], {
    sheet_shortlists: [
      { identity: 'job:one', canonical_url: 'https://jobs.example.org/vacancy/123',
        identity_keys: ['url:https://jobs.example.org/vacancy/123', 'url:https://careers.example.org/jobs/123'],
        accepted_at: '2026-10-09T10:00:00Z', source: 'hark' },
      { identity: 'job:two', canonical_url: 'https://other.example.org/jobs/123',
        identity_keys: ['url:https://other.example.org/jobs/123', 'url:https://jobs.example.org/vacancy/123'],
        accepted_at: '2026-10-09T11:00:00Z', source: 'hark' },
    ],
  });
  ctx.state.runs = [{ kind: 'batch', stage_metrics: [{ id: 'hunt_audit', finished_at: '2026-10-09T09:00:00Z' }],
    summary_metrics: { jobs_shortlisted: 1 },
    jobs: [{ status: 'accepted', role_path: 'Tailored CVs/Invented', source_url: 'https://careers.example.org/jobs/123', accepted_at: '2026-10-09T09:00:00Z' }] }];
  const value = model(ctx);
  assert.equal(value.periods.all.chosenValue, 1);
  assert.equal(value.periods.yesterday.chosenValue, 1);
  assert.equal(value.periods.all.chosen, '1');
});

test('Reused role folder does not merge different saved posting identities', () => {
  const ctx = context([], {});
  ctx.state.runs = ['123', '456'].map(id => ({ kind: 'batch',
    stage_metrics: [{ id: 'hunt_audit', finished_at: '2026-10-09T09:00:00Z' }],
    summary_metrics: { jobs_shortlisted: 1 }, jobs: [{ status: 'accepted',
      role_path: 'Tailored CVs/Company Role', source_url: `https://linkedin.com/jobs/view/${id}`,
      accepted_at: '2026-10-09T09:00:00Z' }] }));
  const value = model(ctx);
  assert.equal(value.periods.all.chosenValue, 2);
  assert.equal(value.periods.yesterday.chosenValue, 2);
  const apps = context(['123','456'].map(id => ({ ...role(id, { status: 'applied', applied_at: '2026-10-09T09:00:00Z' },
    `https://linkedin.com/jobs/view/${id}`), folder: 'Tailored CVs/Company Role' })));
  assert.equal(model(apps).applicationWeek.find(row => row.date === '2026-10-09').count, 2);
});

test('Unavailable Hark screening makes supported shortlist counts a minimum', () => {
  for (const gap of [{ invalid_saved_sources: 1 }, { omitted_source_directories: 1 }]) {
    const ctx = context([], { sheet_shortlists: [], hark_feed: gap });
    ctx.state.runs = [{ kind: 'batch', stage_metrics: [{ id: 'hunt_audit', finished_at: '2026-10-10T00:00:00Z' }],
      summary_metrics: { jobs_shortlisted: 1 }, jobs: [{ status: 'accepted', role_path: 'Tailored CVs/Invented',
        source_url: 'https://linkedin.com/jobs/view/123', accepted_at: '2026-10-10T00:00:00Z' }] }];
    const value = model(ctx);
    for (const period of ['today', 'all']) {
      assert.equal(value.periods[period].chosenValue, 1);
      assert.equal(value.periods[period].chosen, '1+');
      assert.match(value.periods[period].chosenNote, /screening evidence is unavailable.*minimum/);
    }
    ctx.state.runs[0].summary_metrics.jobs_shortlisted = 0;
    ctx.state.runs[0].jobs = [];
    const unsupported = model(ctx);
    for (const period of ['today', 'all']) {
      assert.equal(unsupported.periods[period].chosenValue, null);
      assert.equal(unsupported.periods[period].chosen, '—');
      assert.match(unsupported.periods[period].chosenNote, /screening evidence is unavailable/);
    }
  }
});

console.log(`${checks} Home model checks passed`);
