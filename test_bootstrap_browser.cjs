/* Slow startup and API deadlines. Fictional loopback fixture; no live Studio reads or writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.CV_PLAYWRIGHT || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'));

const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-bootstrap-'));
const ready = path.join(temp, 'ready.json');
const output = path.join(__dirname, '.local/qa/bootstrap');
fs.mkdirSync(output, { recursive: true });
const fixture = spawn(process.env.CV_PYTHON || 'python3', [path.join(__dirname, 'browser_fixture.py'), '--ready', ready], { cwd: __dirname, stdio: ['ignore', 'pipe', 'pipe'] });
let fixtureOutput = '', browser, activePage;
fixture.stdout.on('data', chunk => fixtureOutput += chunk);
fixture.stderr.on('data', chunk => fixtureOutput += chunk);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const checks = [], errors = [], external = [], apiEscapes = [];
const now = new Date('2026-10-10T08:00:00Z');

async function until(check, label) {
  for (let i = 0; i < 100; i++) {
    if (await check()) return;
    await pause(20);
  }
  throw Error('Browser did not reach: ' + label);
}

async function advance(page, milliseconds) {
  // Browser virtual time drives the real API AbortController and fictional reads.
  await page.clock.runFor(milliseconds);
  await page.waitForTimeout(30);
}

async function settleRefresh(page) {
  // JSON stream promises can schedule the next zero-delay read after a clock step.
  for (let i = 0; i < 10 && await page.locator('.refresh-button.is-refreshing').count(); i++) await advance(page, 1);
  assert.equal(await page.locator('.refresh-button.is-refreshing').count(), 0, 'The owned fixture refresh has settled before the next action');
}

function installFixture({ responses, initialMode }) {
  const realFetch = window.fetch.bind(window);
  const catalog = new Set(['/api/jd-insights', '/api/runs', '/api/documents', '/api/apply-options', '/api/manual-jobs', '/api/contacts', '/api/company-logos']);
  const state = window.__bootstrapFixture = { mode: initialMode, calls: [], activeCatalogs: 0, peakCatalogs: 0 };
  window.fetch = (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return realFetch(input, options);
    const method = (options.method || 'GET').toUpperCase();
    const pathname = url.pathname;
    const call = { path: pathname, method, started: Date.now(), finished: null, aborted: false };
    state.calls.push(call);
    const mode = state.mode;
    const isCatalog = method === 'GET' && catalog.has(pathname);
    if (isCatalog) { state.activeCatalogs++; state.peakCatalogs = Math.max(state.peakCatalogs, state.activeCatalogs); }
    const critical = pathname === '/api/overview' || pathname === '/api/roles';
    let delay = mode === 'slow' ? critical ? 31000 + state.activeCatalogs * 500 : isCatalog ? 40000 : 0 : 0;
    if (mode === 'slow' && pathname === '/api/contacts') delay = 25000;
    if (mode === 'hang' || mode === 'slow-body') delay = null;
    return new Promise((resolve, reject) => {
      let settled = false, timer;
      const finish = () => {
        if (settled) return false;
        settled = true;
        clearTimeout(timer);
        call.finished = Date.now();
        if (isCatalog) state.activeCatalogs--;
        options.signal?.removeEventListener('abort', abort);
        return true;
      };
      const abort = () => {
        if (!finish()) return;
        call.aborted = true;
        reject(new DOMException('Fictional request aborted', 'AbortError'));
      };
      options.signal?.addEventListener('abort', abort, { once: true });
      if (options.signal?.aborted) { abort(); return; }
      if (mode === 'slow-body') {
        // Headers arrive immediately; the deadline must still cover JSON-body reading.
        resolve({ ok: true, status: 200, json: () => new Promise((bodyResolve, bodyReject) => {
          const bodyAbort = () => { if (finish()) { call.aborted = true; bodyReject(new DOMException('Fictional body aborted', 'AbortError')); } };
          options.signal?.removeEventListener('abort', abort);
          options.signal?.addEventListener('abort', bodyAbort, { once: true });
          timer = setTimeout(() => { if (finish()) { options.signal?.removeEventListener('abort', bodyAbort); bodyResolve(structuredClone(responses[pathname])); } }, 31000);
        }) });
        return;
      }
      if (delay === null) return;
      timer = setTimeout(() => {
        if (!finish()) return;
        const failed = { 'overview-error': '/api/overview', 'roles-error': '/api/roles', 'contacts-error': '/api/contacts' }[mode] === pathname;
        const failureMessages = { '/api/overview': 'Fixture overview unavailable', '/api/roles': 'Fixture roles unavailable', '/api/contacts': 'Fixture contacts unavailable' };
        let data = failed ? { error: failureMessages[pathname] } : responses[pathname];
        if (pathname === '/api/overview' && (mode === 'catalogs' || mode.startsWith('malformed-'))) {
          data = { ...data, catalogs: { version: 1, roles: responses['/api/roles'], runs: responses['/api/runs'], documents: responses['/api/documents'], settings: responses['/api/settings'] } };
          for (const key of ['roles', 'runs', 'recent_roles', 'recent_runs', 'documents', 'settings']) delete data[key];
          if (mode === 'malformed-catalogs') data.catalogs.roles = { items: [{ id: 'invalid-undecorated-role' }] };
          if (mode === 'malformed-runs') data.catalogs.runs = { items: [null] };
          if (mode === 'malformed-documents') data.catalogs.documents = { items: [null] };
          if (mode === 'malformed-document-id') data.catalogs.documents = { items: [{}] };
          if (mode === 'malformed-run-id') data.catalogs.runs = { items: [{ id: 17 }] };
        }
        if (data === undefined) { reject(Error('Unspecified fixture API: ' + method + ' ' + pathname)); return; }
        resolve(new Response(JSON.stringify(data), { status: failed ? 503 : 200, headers: { 'Content-Type': 'application/json' } }));
      }, delay);
    });
  };
}

(async () => {
  try {
    await until(() => fs.existsSync(ready) || fixture.exitCode !== null, 'isolated fixture server');
    assert.ok(fs.existsSync(ready), fixtureOutput || 'Fixture server started');
    const { url } = JSON.parse(fs.readFileSync(ready));
    assert.equal(new URL(url).hostname, '127.0.0.1');
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const fetchContext = await browser.newContext();
    const paths = ['/api/overview', '/api/roles', '/api/runs', '/api/documents', '/api/settings', '/api/jobs', '/api/apply-options', '/api/manual-jobs', '/api/contacts', '/api/company-logos', '/api/jd-insights'];
    const entries = [];
    // Prime the small fixture's shared catalog sequentially. Tested browser reads
    // below are simulated and can compete without using any backend connection.
    for (const pathname of paths) {
      const response = await fetchContext.request.get(url + pathname);
      assert.equal(response.status(), 200, pathname + ' fixture read');
      entries.push([pathname, await response.json()]);
    }
    const responses = Object.fromEntries(entries);
    responses['/api/settings'].refresh_seconds = 0;
    responses['/api/jobs'] = { items: [], total: 0 };
    responses['/api/overview'].dashboard_analytics_refreshing = false;
    const role = responses['/api/roles'].items[0];
    role.application = { ...role.application, status: 'applied', applied_at: now.toISOString() };
    role.canonical_applied = true;
    const contactRole = responses['/api/roles'].items.find(item => item.id !== role.id && item.cv_available);
    assert.ok(contactRole, 'A second fictional ready CV is available for contact controls');
    responses['/api/contacts'] = { items: [{ role_id: contactRole.id, company: contactRole.company, title: contactRole.title, contacts: [], search_completed: true, last_job: { id: 'fixture-completed-empty', status: 'done', started_at: now.toISOString(), finished_at: now.toISOString(), summary: 'Fictional completed search: zero verified contacts.' } }], total: 1 };
    await fetchContext.close();

    async function open(mode, apiOnly = false) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      await context.route('**/*', route => {
        const requestUrl = route.request().url();
        if (!requestUrl.startsWith(url + '/') && !requestUrl.startsWith('data:')) { external.push(requestUrl); return route.abort(); }
        if (new URL(requestUrl).pathname.startsWith('/api/')) { apiEscapes.push(requestUrl); return route.abort(); }
        return route.continue();
      });
      if (apiOnly) await context.route('**/__bootstrap_api_test.html', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Isolated API timing check</title>' }));
      await context.addInitScript(installFixture, { responses, initialMode: mode });
      const page = await context.newPage();
      activePage = page;
      page.on('pageerror', error => errors.push(error.message));
      await page.clock.install({ time: now });
      await page.clock.pauseAt(now);
      await page.goto(url + (apiOnly ? '/__bootstrap_api_test.html' : '/#overview'), { waitUntil: 'commit' });
      await until(() => page.evaluate(apiOnly ? () => !!window.__bootstrapFixture : () => !!document.querySelector('.nav-item[data-nav="overview"]')), 'fixture page bootstrap');
      return { context, page };
    }

    let handle = await open('catalogs');
    let page = handle.page;
    await advance(page, 1);
    await settleRefresh(page);
    await until(() => page.locator('#colour-home .cd-app').count(), 'consolidated Home snapshot');
    assert.equal(await page.locator('[data-metric="applied"]').innerText(), '1');
    assert.equal(await page.evaluate(() => window.__bootstrapFixture.calls.some(call => ['/api/roles', '/api/runs', '/api/settings', '/api/documents'].includes(call.path))), false, 'The versioned Home snapshot skips duplicate role, run, settings and document reads');
    await page.locator('[data-period="today"]').click();
    await page.locator('.refresh-button').click();
    await advance(page, 1);
    await settleRefresh(page);
    assert.equal(await page.locator('[data-period="today"]').getAttribute('aria-pressed'), 'true', 'A consolidated refresh retains the chosen period');
    checks.push('Versioned decorated catalogs render Home with one overview read and no duplicate catalog calls; refresh preserves the period');
    await handle.context.close();

    for (const mode of ['malformed-catalogs', 'malformed-runs', 'malformed-documents', 'malformed-document-id', 'malformed-run-id']) {
      handle = await open(mode); page = handle.page;
      await advance(page, 1);
      await settleRefresh(page);
      await until(() => page.locator('#colour-home .cd-app').count(), 'fallback Home snapshot');
      assert.equal(await page.locator('[data-metric="applied"]').innerText(), '1');
      assert.equal(await page.evaluate(() => window.__bootstrapFixture.calls.some(call => call.path === '/api/roles')), true, mode + ' uses the normal truthful fallback');
      await page.locator('.nav-item[data-nav="workspace"]').click();
      assert.equal(await page.locator('.error-state').count(), 0, mode + ' leaves Workspace usable');
      await page.locator('.nav-item[data-nav="activity"]').click();
      assert.equal(await page.locator('.error-state').count(), 0, mode + ' leaves History usable');
      await handle.context.close();
    }
    checks.push('Undecorated roles and invalid run/document rows or IDs fall back to standalone decorated reads without breaking Home, Workspace or History');

    // A first load crosses the old 30s deadline while catalog reads also compete.
    handle = await open('slow'); page = handle.page;
    await until(() => page.evaluate(() => window.__bootstrapFixture.calls.some(call => call.path === '/api/overview')), 'slow overview started');
    await page.locator('.refresh-button').click();
    await advance(page, 30000);
    assert.equal(await page.locator('.error-state').count(), 0, 'Slow cold reads must not produce a premature server-timeout error');
    assert.equal(await page.locator('[data-metric="applied"]').count(), 0, 'No zero application count before the role snapshot arrives');
    assert.equal(await page.evaluate(() => window.__bootstrapFixture.calls.filter(call => call.path === '/api/overview').length), 1, 'Refresh during cold load does not launch a duplicate read');
    for (let i = 0; i < 11 && !await page.locator('#colour-home .cd-app').count(); i++) await advance(page, 10000);
    await until(() => page.locator('#colour-home .cd-app').count(), 'slow cold Home snapshot');
    assert.equal(await page.locator('[data-metric="applied"]').innerText(), '1');
    assert.equal(await page.locator('.error-state').count(), 0);
    assert.ok(!(await page.locator('#sync-time').innerText()).includes('no snapshot yet'));
    const slowCalls = await page.evaluate(() => window.__bootstrapFixture.calls);
    const overviewCall = slowCalls.find(call => call.path === '/api/overview');
    const rolesCall = slowCalls.find(call => call.path === '/api/roles');
    assert.equal(overviewCall.aborted, false);
    assert.equal(rolesCall.aborted, false);
    assert.ok(rolesCall.started >= overviewCall.finished, 'The decorated role catalog waits for the overview scan');
    for (const pathname of ['/api/documents', '/api/manual-jobs', '/api/contacts', '/api/company-logos', '/api/jd-insights']) {
      const call = slowCalls.find(entry => entry.path === pathname);
      assert.ok(call && call.started >= rolesCall.finished, pathname + ' begins after the core snapshot');
    }
    assert.ok(slowCalls.some(call => ['/api/jd-insights', '/api/apply-options', '/api/contacts'].includes(call.path)), 'Competing or deferred catalog reads remain covered');
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, 'slow-cold-load.png'), fullPage: true });
    checks.push('Cold load succeeds after staged31s overview/role reads; auxiliary scans follow the core snapshot; pending reads have no fabricated zeros or duplicate refresh');
    await page.locator('.nav-item[data-nav="contacts"]').click();
    assert.equal(await page.locator('[data-contact-library-summary]').innerText(), 'Loading saved contacts…');
    assert.ok((await page.locator('#saved-contact-results').innerText()).includes('Loading the saved contact library…'));
    assert.equal(await page.getByText('No saved contacts yet.', { exact: true }).count(), 0, 'Pending contact reads do not claim an empty library');
    assert.ok(!(await page.locator('.contacts-panel').innerText()).includes('0 verified contacts'), 'Pending contact reads do not claim zero saved contacts');
    await page.locator('.nav-item[data-nav="apply"]').click();
    const contactCard = page.locator(`[data-application-row="${contactRole.id}"]`);
    const pendingContactSearch = contactCard.locator('[data-action="find-contacts"]');
    assert.equal(await pendingContactSearch.isDisabled(), true);
    assert.equal(await pendingContactSearch.innerText(), 'Loading contacts…');
    await page.evaluate(id => {
      // Exercise the internal guard even if an old/stale DOM button is enabled.
      const button = document.querySelector(`[data-application-row="${id}"] [data-action="find-contacts"]`);
      button.disabled = false;
      button.click();
    }, contactRole.id);
    assert.equal(await page.evaluate(() => window.__bootstrapFixture.calls.some(call => call.method !== 'GET')), false, 'The pending contact guard starts no research or mutation');
    assert.equal(await pendingContactSearch.isDisabled(), true, 'The guarded action restores its pending control');
    // The shared refresh remains coalesced while its ancillary reads are pending.
    await advance(page, 30001);
    await settleRefresh(page);
    assert.equal(await contactCard.locator('[data-action="find-contacts"]').count(), 0, 'A completed zero-result contact search cannot rerun');
    await contactCard.locator('[data-action="view-saved-contacts"]').click();
    assert.ok((await page.locator(`[data-saved-contact-role="${contactRole.id}"]`).innerText()).includes('0 verified contacts saved'), 'Returned zero is now supported by a completed saved receipt');
    assert.equal(await page.evaluate(() => window.__bootstrapFixture.calls.some(call => call.method !== 'GET')), false, 'Opening the saved empty result starts no research');
    checks.push('Deferred Contacts stay loading, Apply blocks pending research, and a returned completed zero-result receipt becomes View saved contacts without rerunning');
    await page.locator('.nav-item[data-nav="overview"]').click();
    const savedReadValue = await page.locator('[data-funnel="read"] strong').innerText();

    // A genuine refresh failure keeps the last snapshot and recovers on explicit retry.
    await page.evaluate(() => { window.__bootstrapFixture.mode = 'overview-error'; });
    await page.locator('.refresh-button').click();
    await advance(page, 1);
    await until(() => page.locator('.stale-notice').count(), 'failed refresh notice');
    await settleRefresh(page);
    assert.equal(await page.locator('[data-funnel="read"] strong').innerText(), savedReadValue, 'Failed refresh preserves the last saved read evidence');
    assert.equal(await page.locator('[data-metric="applied"]').innerText(), '—', 'Skipped dependent role read remains unknown instead of becoming zero');
    assert.equal(await page.locator('.error-state').count(), 0, 'A saved snapshot remains usable');
    await page.evaluate(() => { window.__bootstrapFixture.mode = 'normal'; });
    await page.locator('.stale-notice [data-action="refresh"]').click();
    await advance(page, 1);
    await settleRefresh(page);
    await until(async () => !await page.locator('.stale-notice').count(), 'successful refresh clears stale notice');
    checks.push('Post-load failure preserves the real snapshot and successful explicit retry clears stale state');
    await handle.context.close();

    // A genuine cold failure still shows the retry error, rather than a false empty Home.
    handle = await open('overview-error'); page = handle.page;
    await advance(page, 1);
    await settleRefresh(page);
    await until(() => page.locator('.error-state').count(), 'cold failure error');
    assert.ok((await page.locator('.error-state').innerText()).includes('Fixture overview unavailable'));
    assert.equal(await page.locator('#colour-home .cd-app').count(), 0);
    assert.ok((await page.locator('#sync-time').innerText()).includes('no snapshot yet'));
    assert.equal(await page.evaluate(() => window.__bootstrapFixture.calls.some(call => ['/api/roles', '/api/documents', '/api/manual-jobs', '/api/contacts', '/api/company-logos', '/api/jd-insights'].includes(call.path))), false, 'A failed overview does not launch dependent catalog scans');
    await page.evaluate(() => { window.__bootstrapFixture.mode = 'normal'; });
    await page.locator('.error-state [data-action="refresh"]').click();
    await advance(page, 1);
    await settleRefresh(page);
    await until(() => page.locator('#colour-home .cd-app').count(), 'cold retry Home');
    assert.equal(await page.locator('[data-metric="applied"]').innerText(), '1');
    assert.equal(await page.locator('.error-state').count(), 0);
    checks.push('Cold overview503 remains a true no-snapshot failure and skips dependent scans; explicit Retry recovers without a reload');
    await handle.context.close();

    handle = await open('roles-error'); page = handle.page;
    await advance(page, 1);
    await settleRefresh(page);
    await until(() => page.locator('#colour-home .cd-app').count(), 'partial snapshot Home');
    assert.equal(await page.locator('[data-metric="applied"]').innerText(), '—', 'Failed roles are unknown, never zero');
    assert.ok((await page.locator('#colour-home').innerText()).includes('Role records unavailable'));
    await page.evaluate(() => { window.__bootstrapFixture.mode = 'normal'; });
    await page.locator('.refresh-button').click();
    await advance(page, 1);
    await settleRefresh(page);
    await until(async () => await page.locator('[data-metric="applied"]').innerText() === '1', 'roles retry restores applications');
    checks.push('A partial snapshot distinguishes failed roles from an empty catalog and recovers on retry');
    await handle.context.close();

    handle = await open('contacts-error'); page = handle.page;
    await advance(page, 1);
    await settleRefresh(page);
    await page.locator('.nav-item[data-nav="contacts"]').click();
    assert.equal(await page.locator('[data-contact-library-summary]').innerText(), 'Saved contact counts are unavailable. Please retry.');
    assert.ok((await page.locator('#saved-contact-results').innerText()).includes('Fixture contacts unavailable'));
    assert.equal(await page.getByText('No saved contacts yet.', { exact: true }).count(), 0);
    assert.ok(!(await page.locator('.contacts-panel').innerText()).includes('0 verified contacts'), 'A failed first contact read remains unknown');
    await page.evaluate(() => { window.__bootstrapFixture.mode = 'normal'; });
    await page.locator('#saved-contact-results [data-action="refresh"]').click();
    await advance(page, 1);
    await settleRefresh(page);
    assert.ok((await page.locator(`[data-saved-contact-role="${contactRole.id}"]`).innerText()).includes('0 verified contacts saved'));
    checks.push('A failed first contact catalog remains unknown and its Retry restores completed saved results');
    await handle.context.close();

    // API-only checks keep response-body and abort behavior independent of app rendering.
    handle = await open('slow-body', true); page = handle.page;
    await page.evaluate(async () => { window.__fixtureApi = (await import('/static/api.js')).api; window.__readResult = null; window.__fixtureApi.roles().then(value => { window.__readResult = { value }; }, error => { window.__readResult = { error: error.message, status: error.status }; }); });
    await advance(page, 30000);
    assert.equal(await page.evaluate(() => window.__readResult), null, 'GET deadline also permits a slow JSON body past30s');
    await advance(page, 1001);
    assert.equal(await page.evaluate(() => window.__readResult.value.items.length), responses['/api/roles'].items.length);
    checks.push('Read deadline covers slow JSON-body parsing, as well as delayed response headers');

    await page.evaluate(() => { window.__bootstrapFixture.mode = 'hang'; window.__readResult = null; Promise.all(['overview', 'roles', 'runs'].map(key => window.__fixtureApi[key]().then(value => ({ value }), error => ({ error: error.message, status: error.status })))).then(results => { window.__readResult = results; }); });
    await advance(page, 30001);
    assert.equal(await page.evaluate(() => window.__readResult), null, 'GET requests retain a longer read allowance');
    await advance(page, 59999);
    const readFailures = await page.evaluate(() => window.__readResult);
    assert.equal(readFailures.length, 3);
    for (const failure of readFailures) { assert.equal(failure.status, 0); assert.match(failure.error, /took too long to respond/); }
    const coreReadCalls = await page.evaluate(() => window.__bootstrapFixture.calls.slice(-3));
    assert.deepEqual(coreReadCalls.map(call => call.path), ['/api/overview', '/api/roles', '/api/runs']);
    for (const call of coreReadCalls) { assert.equal(call.aborted, true); assert.equal(call.finished - call.started, 90000, call.path + ' has a real bounded90s abort'); }
    checks.push('All three permanently stalled core reads abort at90000ms and retain the timeout distinction');

    await page.evaluate(() => { window.__ordinaryReadResult = null; window.__fixtureApi.documents().then(value => { window.__ordinaryReadResult = { value }; }, error => { window.__ordinaryReadResult = { error: error.message, status: error.status }; }); });
    await advance(page, 29999);
    assert.equal(await page.evaluate(() => window.__ordinaryReadResult), null);
    await advance(page, 1);
    const ordinaryReadFailure = await page.evaluate(() => window.__ordinaryReadResult);
    assert.equal(ordinaryReadFailure.status, 0);
    assert.match(ordinaryReadFailure.error, /took too long to respond/);
    const ordinaryReadCall = await page.evaluate(() => window.__bootstrapFixture.calls.at(-1));
    assert.equal(ordinaryReadCall.finished - ordinaryReadCall.started, 30000, 'Non-core GET requests retain their original deadline');
    checks.push('Non-core reads retain the original30000ms abort');

    await page.evaluate(() => { window.__mutationResult = null; window.__fixtureApi.saveSettings({ theme: 'dark' }).then(value => { window.__mutationResult = { value }; }, error => { window.__mutationResult = { error: error.message, status: error.status }; }); });
    await advance(page, 29999);
    assert.equal(await page.evaluate(() => window.__mutationResult), null);
    await advance(page, 1);
    const mutationFailure = await page.evaluate(() => window.__mutationResult);
    assert.equal(mutationFailure.status, 0);
    assert.match(mutationFailure.error, /took too long to respond/);
    const mutationCall = await page.evaluate(() => window.__bootstrapFixture.calls.at(-1));
    assert.equal(mutationCall.method, 'PUT');
    assert.equal(mutationCall.aborted, true);
    assert.equal(mutationCall.finished - mutationCall.started, 30000, 'The read allowance does not extend write deadlines');
    checks.push('Mutations keep their original30000ms abort; no mutation reaches a server');
    await handle.context.close();
    activePage = null;

    assert.deepEqual(errors, [], 'No unhandled browser errors');
    assert.deepEqual(external, [], 'No external requests');
    assert.deepEqual(apiEscapes, [], 'No browser API request reaches a backend');
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ status: 'passed', checks, slowCalls }, null, 2));
    console.log(JSON.stringify({ status: 'passed', checks, evidence: output }, null, 2));
  } catch (error) {
    if (activePage && !activePage.isClosed()) await activePage.screenshot({ path: path.join(output, 'failure.png'), fullPage: true });
    const browserState = activePage && !activePage.isClosed() ? await activePage.evaluate(() => ({ calls: window.__bootstrapFixture?.calls, mode: window.__bootstrapFixture?.mode, error: document.querySelector('.error-state')?.textContent, stale: document.querySelector('.stale-notice')?.textContent, refreshing: document.querySelector('.refresh-button')?.className })) : null;
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ status: 'failed', checks, error: error.stack, errors, external, apiEscapes, browserState, fixtureOutput }, null, 2));
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.kill('SIGTERM');
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
