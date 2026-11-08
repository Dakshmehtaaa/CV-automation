// 20 Oct 2026: Job feed audit panels (Hark only since 5 Nov). Run: node test_feed_source_toggle.mjs
import assert from 'node:assert/strict';
import { mergeKpi, renderFeedAudit, summarize } from './static/home-feeds.js';

const feed = (added, accuracy = null) => ({ status: 'complete', ran: true, on_time: true, duration_min: 10, scraped: 20, added,
  duplicates: 1, jds: added, jds_of: added, accuracy_pct: accuracy, errors_found: null, errors_fixed: null, onhold_open: 0 });
const hark = { kind: 'hark_kpi_audit_v1', updated_at: '2026-10-20T11:00:00Z', feeds: [{ id: 'linkedin', label: 'LinkedIn' }],
  days: [{ date: '2026-10-20', automation_score_pct: 90, row_accuracy_pct: 80, new_rows: 10, jds_captured: 10,
    email_intake: { reported: true, rejections: 1 }, feeds: { linkedin: feed(10, 80) } }] };
const fc = { kind: 'hark_kpi_audit_v1', updated_at: '2026-10-21T06:00:00Z', feeds: [{ id: 'tier_1_ats', label: 'Tier 1 ATS' }],
  days: [{ date: '2026-10-20', automation_score_pct: null, row_accuracy_pct: 60, new_rows: 4, jds_captured: 3, feeds: { tier_1_ats: feed(4) } },
    { date: '2026-10-21', automation_score_pct: null, row_accuracy_pct: null, new_rows: 2, jds_captured: 2, feeds: { tier_1_ats: feed(2) } }] };

const both = mergeKpi(hark, fc);
assert.deepEqual(both.feeds.map(f => f.id), ['hark:linkedin', 'fc:tier_1_ats']);
assert.equal(both.feeds[1].label, 'Firecrawl · Tier 1 ATS');
assert.deepEqual(both.days.map(d => d.date), ['2026-10-20', '2026-10-21']);
assert.equal(both.days[0].new_rows, 14);
assert.equal(both.days[0].row_accuracy_pct, 70);       // mean of 80 and 60
assert.equal(both.days[0].automation_score_pct, 90);   // unknown Firecrawl score is not counted as 0
assert.equal(both.days[1].automation_score_pct, null);
assert.equal(both.days[0].email_intake.rejections, 1);
assert.equal(both.updated_at, '2026-10-21T06:00:00Z');
assert.equal(mergeKpi(null, null), null);
assert.equal(mergeKpi(hark, null).feeds[0].id, 'hark:linkedin');
const s = summarize(both.days, both.feeds);
assert.equal(s.newRows, 16);
assert.equal(s.feeds.find(f => f.id === 'fc:tier_1_ats').added, 6);

const args = { kpi: hark, firecrawl: fc, unit: 'all', back: 0, toggleHtml: '', periodName: 'All', colourOf: () => '#000', today: '2026-10-21' };
// 5 Nov 2026: Hark is the only feed: one visible panel, no source toggle (mergeKpi stays for a later second feed).
for (const source of ['hark', 'firecrawl', 'both']) {
  const html = renderFeedAudit({ ...args, source });
  assert.equal((html.match(/data-hf-panel=/g) || []).length, 1);
  assert.equal((html.match(/data-hf-src=/g) || []).length, 0);
}
assert.match(renderFeedAudit({ ...args, source: 'hark' }), /data-hf-panel="hark">/);
assert.match(renderFeedAudit({ ...args, source: 'firecrawl' }), /data-hf-panel="hark">/);  // an old saved choice still shows Hark
console.log('feed source toggle: all checks passed');
