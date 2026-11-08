/* Real Chrome, isolated data and fixture jobs. Never runs the user's pipeline. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const playwrightPath = process.env.CV_PLAYWRIGHT || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core');
const {chromium} = require(playwrightPath);
const root = __dirname;
const output = path.join(root, '.local', 'qa');
fs.mkdirSync(output, {recursive: true});
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-studio-browser-ready-'));
const ready = path.join(temp, 'ready.json');
const fixture = spawn(process.env.CV_PYTHON || 'python3', [path.join(root, 'browser_fixture.py'), '--ready', ready], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let fixtureOutput = '';
fixture.stdout.on('data', chunk => fixtureOutput += chunk);
fixture.stderr.on('data', chunk => fixtureOutput += chunk);
const checks = [];
const errors = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function run() {
  let browser, page;
  try {
    for (let i = 0; i < 100 && !fs.existsSync(ready); i++) {
      if (fixture.exitCode !== null) throw new Error('Fixture server failed: ' + fixtureOutput);
      await pause(100);
    }
    assert.ok(fs.existsSync(ready), 'Fixture started');
    const {url, root: fixtureRoot} = JSON.parse(fs.readFileSync(ready));
    browser = await chromium.launch({channel: 'chrome', headless: true});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    const external = [];
    await context.route('**/*', async route => {
      const requestUrl = route.request().url();
      const browserResource = requestUrl.startsWith('chrome://') || requestUrl.startsWith('chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/');
      if (!requestUrl.startsWith(url) && !requestUrl.startsWith('data:') && !browserResource) {
        external.push(requestUrl); await route.abort();
      } else await route.continue();
    });
    await context.route('**/api/overview', async route => {
      const response = await route.fetch({headers:{...route.request().headers(),'x-studio-catalogs':'0'}});
      const body = await response.json();
      // Keep this suite's standalone run fixtures effective for old servers.
      // The versioned combined response has its own bootstrap acceptance.
      delete body.catalogs;
      await route.fulfill({ response, json: body });
    });
    const historyFixtureJob = {company:'Example Green', title:'Sustainability Analyst', role_id:'fixture-example-green', status:'shortlisted', cv_built:true};
    const historyFixturePolicy4 = {
      policy_version:4,
      summary_metrics:{jobs_found:17,jobs_proposed:12,jobs_shortlisted:1,ads_reviewed:49,cvs_built:1,roles_synced:1},
      stages:[
        {id:'hunt',label:'Find opportunities',status:'done',jobs_found:17,jobs_proposed:12,ads_reviewed:49},
        {id:'hunt_audit',label:'Hunt Audit',status:'done',jobs_shortlisted:1},
        {id:'build',label:'Build CVs',status:'done',cvs_built:1},
        {id:'build_audit',label:'Build Audit',status:'done'},
        {id:'sol_review',label:'Final Sol review',status:'done'},
        {id:'sync',label:'CV Studio sync',status:'done',roles_synced:1},
      ],
    };
    historyFixturePolicy4.stage_metrics = historyFixturePolicy4.stages;
    await context.route('**/api/runs', async route => {
      const response = await route.fetch();
      const body = await response.json();
      const run = (body.items || []).find(item => item.id === '2026-09-01-fixture');
      if (run) Object.assign(run, historyFixturePolicy4, {jobs:[historyFixtureJob]});
      await route.fulfill({response,json:body});
    });
    await context.route('**/api/runs/2026-09-01-fixture', async route => {
      const response = await route.fetch();
      const body = await response.json();
      Object.assign(body, historyFixturePolicy4, {jobs:[historyFixtureJob]});
      await route.fulfill({response,json:body});
    });
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.locator('.nav-item[data-nav="overview"]').waitFor();
    await page.locator('#colour-home .cd-app').waitFor();
    await page.locator('[data-metric="applied"]').waitFor();
    assert.equal(await page.locator('.cd-more').getAttribute('open'),null,'Secondary Home insights start collapsed');
    await page.locator('.cd-more>summary').click();
    for (const title of ['Your search funnel','Source outcomes','Automation performance','Top locations','JD requirements & mentions']) await page.getByRole('heading',{name:title,exact:true}).waitFor();
    assert.equal(await page.getByRole('heading',{name:'Applications',exact:true}).count(),0,'Home has no application pie chart');
    await page.evaluate(()=>{document.querySelector('.skip-link')?.blur();scrollTo(0,0)});
    await page.screenshot({path: path.join(output, 'fixture-overview.png'), fullPage: true});
    checks.push('Local overview loads actual fixture records');
    assert.equal(await page.locator('.nav-item[data-nav="pipeline"]').count(),0,'Retired navigation items stay absent (Home beta returned 13 Oct at Daksh request)');
    assert.equal(await page.locator('.nav-item[data-nav="library"] .nav-count').count(),0,'CV navigation has no count badge');
    await page.locator('.nav-item[data-nav="contacts"]').waitFor();
    for (const route of ['contacts', 'library', 'activity', 'workspace', 'settings']) {
      if (['activity', 'workspace'].includes(route)) await page.goto(url + '/#' + route);
      else await page.locator(`.nav-item[data-nav="${route}"]`).click();
      await page.waitForURL('**/#' + route);
      await page.locator('h1').waitFor();
      assert.equal(await page.locator('h1').count(), 1, route + ' has one page heading');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
      assert.equal(overflow, false, route + ' desktop has no horizontal page overflow');
      if (route === 'activity') {
        await page.locator('.run-company-chip img[data-company-logo]').first().waitFor();
        await page.locator('.run-job img[data-company-logo]').first().waitFor();
        assert.ok(await page.locator('.run-job img[data-company-logo]').first().evaluate(node => node.complete && node.naturalWidth > 0),'History shows the saved company logo');
        await page.locator('[data-select-run="2026-09-01-fixture"]').click();
        await page.getByText('Accepted roles',{exact:true}).waitFor();
        const runDetail = page.locator('#run-detail');
        const detailsText = await runDetail.innerText();
        for (const value of ['Jobs found\n17','Draft proposals\n12','Accepted roles\n1','CV Studio synced\n1']) {
          assert.ok(detailsText.includes(value),`Policy-4 readback keeps distinct count ${value}`);
        }
        await runDetail.locator('.run-stage').filter({has:page.locator('summary strong',{hasText:'Find opportunities'})}).locator('summary').first().click();
        await page.getByText('17 jobs found · 12 proposals · 49 ads reviewed',{exact:true}).waitFor();
        await runDetail.locator('.run-stage').nth(2).locator('summary').first().click();
        await page.getByText('1 accepted roles',{exact:true}).waitFor();
        await runDetail.locator('.run-stage').nth(6).locator('summary').first().click();
        await page.getByText('1 CV Studio roles verified',{exact:true}).waitFor();
        checks.push('Policy-4 history separates found jobs, draft proposals, accepted roles and local Studio sync');
        checks.push('History chips and run details show only manifest-backed company logos');
      }
      if (route === 'settings') await page.getByText('CV Studio sync',{exact:true}).waitFor();
    }
    checks.push('Home, Contacts, CVs, History, Profile and Settings navigate without desktop overflow; retired navigation stays absent');

    await page.locator('.nav-item[data-nav="apply"]').click();
    const beforeQueueJobs = await (await context.request.get(url + '/api/jobs')).json();
    await page.locator('.manual-add-job summary').click();
    await page.locator('#manual-job-form input[name="url"]').fill('https://example.com/jobs/saved-for-later');
    await page.locator('#manual-job-form input[name="company"]').fill('Queue Fixture');
    await page.locator('#manual-job-form input[name="title"]').fill('Climate Analyst');
    await page.locator('#manual-job-form textarea[name="jd"]').fill('Full saved advert. Excel reporting and climate strategy experience.');
    const saveQueueResponse = page.waitForResponse(response => response.url().endsWith('/api/manual-jobs') && response.request().method() === 'POST');
    await page.locator('#manual-job-form button[type="submit"]').click();
    const queueResponse = await saveQueueResponse;
    assert.equal(queueResponse.status(),201);
    const queued = (await queueResponse.json()).item;
    await page.locator(`[data-manual-job="${queued.id}"]`).waitFor();
    await page.getByText('Job saved. The next pipeline run will review it for a CV build.', {exact:true}).waitFor();
    assert.ok((await page.locator('.manual-queue-panel .section-subtitle').innerText()).includes('The next pipeline run reviews saved jobs and builds eligible CVs.'),'Saved job copy explains the ordinary next-run pickup');
    assert.equal((await(await context.request.get(url + '/api/jobs')).json()).total,beforeQueueJobs.total,'Saving a job does not create an AI job');
    await page.reload(); await page.locator(`[data-manual-job="${queued.id}"]`).waitFor();
    assert.ok((await page.locator(`[data-manual-job="${queued.id}"]`).innerText()).includes('Climate Analyst'),'Saved queue item survives page refresh');
    const overviewToken = (await(await context.request.get(url + '/api/overview')).json()).token;
    const duplicateQueueResponse = await context.request.post(url + '/api/manual-jobs',{headers:{'X-Studio-Token':overviewToken,'Origin':url},data:{url:'https://example.com/jobs/saved-for-later',jd:'',company:'Queue Fixture',title:'Climate Analyst',find_contacts:false}});
    assert.equal(duplicateQueueResponse.status(),200);
    assert.equal((await duplicateQueueResponse.json()).duplicate,true,'Duplicate queue input returns the existing saved job');
    assert.equal((await(await context.request.get(url + '/api/manual-jobs')).json()).total,1);
    assert.equal((await(await context.request.get(url + '/api/jobs')).json()).total,beforeQueueJobs.total,'Duplicate save still creates no AI work');
    const queuedBuild=page.locator(`[data-manual-job="${queued.id}"] [data-action="build-manual-job"]`);
    await queuedBuild.waitFor();assert.equal(await queuedBuild.isVisible(),true,'Build now is exposed on the saved job');
    assert.equal(await queuedBuild.isEnabled(),true,'Idle workspace enables Build now');
    await page.locator('.manual-add-job summary').click();
    await page.screenshot({path:path.join(output,'fixture-add-job-queue.png'),fullPage:true});
    checks.push('Add job saves once, survives refresh, dedupes, and starts no model or builder');
    const buildResponse=page.waitForResponse(response=>response.url().endsWith('/api/manual-jobs/'+queued.id+'/build') && response.request().method()==='POST');
    await queuedBuild.click();const built=await buildResponse;assert.equal(built.status(),202);
    const buildBody=await built.json();assert.equal(buildBody.job.kind,'manual_build');assert.equal(buildBody.job.status,'held');
    assert.equal((await(await context.request.get(url+'/api/manual-jobs')).json()).items.find(item=>item.id===queued.id).build_job_id,buildBody.job.id,'Build now connects the exact saved queue item to its isolated fixture job');
    checks.push('Build now posts the exact saved item through the ordinary API and records only an in-memory held fixture job');

    await page.locator('.nav-item[data-nav="contacts"]').click();
    assert.equal(await page.locator('.saved-contact-group').count(),0,'An empty contact library does not invent recovered addresses');
    await page.screenshot({path:path.join(output,'fixture-empty-contacts.png'),fullPage:true});
    const contactRoles = await (await context.request.get(url + '/api/roles')).json();
    const contactRole = contactRoles.items.find(role => role.cv_available);
    const emptyContactJobId = 'aabbccddeeff0011';
    const emptyContactReceipt = {role_id:contactRole.id,provider:'luna',model_used:'gpt-6-luna',finished_at:'2026-10-05T10:00:00Z',verified_count:0,rejected_count:5,review:{status:'completed',actual_model:'deterministic-public-source-check-v1'},summary:'0 verified public contacts; 5 unverified candidates skipped.'};
    fs.writeFileSync(path.join(fixtureRoot,'Resources','Studio','.local','application-contacts.json'),JSON.stringify({version:1,roles:{[contactRole.id]:[]},jobs:{[emptyContactJobId]:emptyContactReceipt}}));
    await page.reload();await page.locator(`[data-saved-contact-role="${contactRole.id}"]`).waitFor();
    assert.ok((await page.locator(`[data-saved-contact-role="${contactRole.id}"]`).innerText()).includes('0 verified contacts saved'),'Completed zero-result search stays visible without fabricated people');
    const emptyContactReadback = await (await context.request.get(url + '/api/applications/' + contactRole.id + '/contacts')).json();
    assert.equal(emptyContactReadback.search_completed,true,'The successful empty receipt remains terminal on a fresh read');
    const beforeDuplicateContactJobs = (await(await context.request.get(url + '/api/jobs')).json()).total;
    const duplicateContact = await context.request.post(url + '/api/applications/' + contactRole.id + '/contacts',{headers:{'X-Studio-Token':overviewToken,'Origin':url},data:{provider:'luna'}});
    assert.equal(duplicateContact.status(),200);
    assert.equal((await duplicateContact.json()).search_completed,true,'Repeat contact action returns the saved empty receipt');
    assert.equal((await(await context.request.get(url + '/api/jobs')).json()).total,beforeDuplicateContactJobs,'Saved zero-result search cannot rerun AI research');
    await page.screenshot({path:path.join(output,'fixture-completed-empty-contacts.png'),fullPage:true});
    await page.locator('.contact-search-notice').waitFor();
    await page.goto(url+'/#library');await page.locator('h1').waitFor();
    assert.equal(await page.locator('.contact-search-notice').count(),1,'Contact notice survives route changes');
    await page.reload();await page.locator('.contact-search-notice').waitFor();
    const secondContactRole=contactRoles.items.find(item=>item.id!==contactRole.id);
    await context.route(url+'/api/contacts',async route=>{const response=await route.fetch();const body=await response.json();body.items.push({role_id:secondContactRole.id,company:secondContactRole.company,title:secondContactRole.title,contacts:[],search_completed:false,last_job:{id:'second-contact-fixture',status:'running',summary:'Isolated second contact search.',started_at:'2026-10-05T10:01:00Z'}});return route.fulfill({response,json:body});});
    await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.contact-search-notice').length===2);
    assert.equal(await page.locator('.contact-search-notice').filter({hasText:contactRole.company}).count(),1,'Completed undismissed notice remains when another search starts');
    const noticePosition=await page.locator('#contact-search-notices').evaluate(node=>({position:getComputedStyle(node).position,left:node.getBoundingClientRect().left,bottom:innerHeight-node.getBoundingClientRect().bottom}));
    assert.equal(noticePosition.position,'fixed');assert.ok(noticePosition.left<720 && noticePosition.bottom<60,'Contact notice stays bottom left');
    await page.locator('.contact-search-notice').filter({hasText:contactRole.company}).locator('[data-action="view-contact-search"]').click();await page.waitForURL('**/#contacts');
    await page.locator(`[data-saved-contact-role="${contactRole.id}"]`).waitFor();
    await page.locator('.contact-search-notice').filter({hasText:contactRole.company}).locator('[data-action="dismiss-contact-notice"]').click();assert.equal(await page.locator('.contact-search-notice').count(),1,'Only explicitly dismissed notice closes');
    await page.locator('[data-action="dismiss-contact-notice"]').click();assert.equal(await page.locator('.contact-search-notice').count(),0);
    await page.reload();await page.locator('h1').waitFor();assert.equal(await page.locator('.contact-search-notice').count(),0,'Explicit contact dismissal persists on reload');
    await context.unroute(url+'/api/contacts');
    checks.push('Contact notices persist across navigation/reload and later searches at bottom left, open results, and explicit dismissal persists');
    await page.locator('.nav-item[data-nav="apply"]').click();
    const contactCard=page.locator(`[data-application-row="${contactRole.id}"]`);
    assert.equal(await contactCard.locator('[data-action="find-contacts"]').count(),0,'Completed contact action is replaced with saved result access');
    await contactCard.locator('[data-action="view-saved-contacts"]').waitFor();
    checks.push('Empty Contacts stays truthful; persisted completed zero-result search survives reload and cannot rerun research');

    await page.locator('.nav-item[data-nav="library"]').click();
    await page.locator('#library-search').fill('Example Green');
    assert.equal(await page.locator('.library-table tbody tr').count(), 1);
    await page.locator('.library-table img[data-company-logo]').waitFor();
    await page.locator('.table-role').first().click();
    await page.locator('#detail-dialog .company-avatar img[data-company-logo]').waitFor();
    checks.push('CV table and package drawer show the saved company logo');
    await page.locator('#detail-dialog iframe').waitFor();
    const src = await page.locator('#detail-dialog iframe').getAttribute('src');
    const pdfResponse = await context.request.get(url + src.split('#')[0]);
    assert.equal(pdfResponse.status(), 200);
    assert.equal(pdfResponse.headers()['content-type'], 'application/pdf');
    await page.keyboard.press('Escape');
    await page.locator('#library-search').fill('not-a-fixture-company');
    await page.getByText('No packages match this view.', {exact:true}).waitFor();
    await page.locator('[data-action="clear-filters"]').click();
    assert.equal(await page.locator('.library-table tbody tr').count(), 3);
    checks.push('CV search, empty state, reset, detail drawer and real PDF response work');

    await page.goto(url + '/#workspace');
    await page.locator('[data-document="search"]').click();
    await page.locator('#document-content').waitFor();
    const initial = await page.locator('#document-content').inputValue();
    const changed = initial.replace('Sustainability roles in France.', 'Sustainability and climate roles in France.');
    await page.locator('#document-content').fill(changed);
    await page.locator('.nav-item[data-nav="settings"]').click();
    await page.locator('#action-dialog[open]').waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#document-content').inputValue(), changed);
    await page.locator('[data-action="save-document"]').click();
    await page.getByText('Changes saved to your workspace.', {exact:true}).waitFor();
    assert.equal(fs.readFileSync(path.join(fixtureRoot, 'Resources', 'SEARCH.md'), 'utf8'), changed);
    checks.push('Unsaved navigation guard, save, real source readback and revision-backed edit work');

    // A slow save may not discard words typed while the request is in flight.
    await page.route('**/api/documents/search', async route => {
      if (route.request().method() === 'PUT') await pause(700);
      await route.continue();
    });
    const sent = changed.replace('climate roles', 'climate strategy roles');
    await page.locator('#document-content').fill(sent);
    const firstSave = page.waitForResponse(response => response.url().endsWith('/api/documents/search') && response.request().method() === 'PUT');
    await page.locator('[data-action="save-document"]').click();
    const field = page.locator('#document-content');
    if (await field.isEditable()) {
      const later = sent.replace('strategy roles', 'strategy and reporting roles');
      await field.fill(later);
      assert.equal((await firstSave).status(), 200);
      await page.waitForFunction(() => !document.querySelector('[data-action="save-document"]').disabled);
      assert.equal(await field.inputValue(), later, 'Newer draft survives a slow save');
      const secondSave = page.waitForResponse(response => response.url().endsWith('/api/documents/search') && response.request().method() === 'PUT');
      await page.locator('[data-action="save-document"]').click();
      assert.equal((await secondSave).status(), 200);
    } else assert.equal((await firstSave).status(), 200);
    await page.waitForFunction(() => document.querySelector('#save-status')?.textContent === 'Saved locally');
    await page.unroute('**/api/documents/search');
    checks.push('Slow-save race preserves newer edits');

    await page.locator('.nav-item[data-nav="settings"]').click();
    await page.locator('[data-action="start-check"]').click();
    await page.waitForURL('**/#activity');
    await page.getByText('Fixture checks passed.', {exact:true}).waitFor({timeout:15000});
    await page.locator('.nav-item[data-nav="settings"]').click();
    await page.locator('[data-action="start-pipeline"]').click();
    await page.locator('[data-action="confirm-pipeline"]').click();
    await page.locator('[data-job-request]').first().waitFor();
    await page.locator('[data-job-request]').first().click();
    await page.locator('#answer-0').selectOption('Continue');
    await page.locator('#job-input-form button[type="submit"]').click();
    await page.getByText('Fixture answer received.', {exact:true}).waitFor({timeout:10000});
    checks.push('Fixture checks, live-log status, launch review and in-browser question roundtrip work');

    await page.route('**/api/overview', async route => {
      const response = await route.fetch();
      const body = await response.json();
      body.runtime.capabilities.can_run = false;
      body.runtime.capabilities.can_check = true;
      body.runtime.capabilities.blocked_reason = 'Fixture usage is exhausted. Wait for the provider reset.';
      await route.fulfill({response, json:body});
    });
    const blockedOverviewResponse = page.waitForResponse(response => response.url().endsWith('/api/overview') && response.request().method() === 'GET');
    await page.locator('[data-action="refresh"]').click();
    const blockedOverview = await blockedOverviewResponse;
    assert.equal(blockedOverview.status(),200);
    assert.equal((await blockedOverview.json()).runtime.capabilities.can_run,false,'Fixture returns exhausted model allowance');
    await page.locator('.nav-item[data-nav="settings"]').click();
    await page.waitForFunction(() => document.querySelector('[data-action="start-pipeline"]')?.disabled === true, null, {timeout:5000});
    assert.equal(await page.locator('[data-action="start-pipeline"]').isDisabled(), true, 'Exhausted usage disables model launch');
    await page.locator('.nav-item[data-nav="settings"]').click();
    assert.equal(await page.locator('[data-action="start-check"]').isEnabled(), true, 'Offline checks remain usable without model allowance');
    await page.unroute('**/api/overview');
    checks.push('Exhausted allowance blocks AI launch while offline work stays available');

    await page.locator('.nav-item[data-nav="settings"]').click();
    await page.locator('.theme-option:has(input[value="dark"])').click();
    assert.equal(await page.locator('input[name="theme"][value="dark"]').isChecked(), true);
    await page.locator('select[name="density"]').selectOption('compact');
    await page.locator('select[name="refresh_seconds"]').selectOption('30');
    await page.locator('#preferences-form button[type="submit"]').click();
    await pause(250);
    const settingsReadback = await (await context.request.get(url + '/api/settings')).json();
    assert.equal(settingsReadback.theme, 'dark');
    assert.equal(settingsReadback.density, 'compact');
    assert.equal(settingsReadback.refresh_seconds, 30);
    await page.screenshot({path: path.join(output, 'fixture-settings.png'), fullPage:true});
    checks.push('Theme, density and refresh preferences persist to the fixture workspace');
    await page.keyboard.press('Meta+k');
    await page.locator('#command-search').waitFor();
    await page.keyboard.press('Escape');
    checks.push('Keyboard command palette opens and closes');

    await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.toast').length === 0);
    if (process.env.CV_STUDIO_SKIP_MOBILE !== '1') {
      await page.setViewportSize({width:390,height:844});
      for (const route of ['overview','contacts','library','activity','workspace','settings']) {
        await page.goto(url + '/#' + route);
        await page.locator('h1').waitFor();
        if (route === 'overview') {
          await page.locator('#colour-home .cd-app').waitFor();
          await page.locator('.cd-more>summary').click();
          await page.locator('.cd-requirements h2').waitFor();
          await page.evaluate(()=>{document.querySelector('.skip-link')?.blur();scrollTo(0,0)});
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
        assert.equal(overflow,false,route + ' mobile has no horizontal page overflow');
        await page.screenshot({path:path.join(output,'fixture-mobile-' + route + '.png'),fullPage:true});
      }
      checks.push('Six pages fit 390px mobile width');
    }
    assert.deepEqual(errors, [], 'No unhandled browser errors');
    assert.deepEqual(external, [], 'No external assets or CDN requests');
    checks.push('No browser errors or external resource requests');
    fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({status:'passed',checks},null,2));
    console.log(JSON.stringify({status:'passed',checks,screenshots:output},null,2));
  } catch (error) {
    if (page) {
      await page.screenshot({path:path.join(output,'browser-failure.png'), fullPage:true});
      error.message += '\nBrowser errors: ' + JSON.stringify(errors) + '\nBrowser state: ' + JSON.stringify(await page.evaluate(() => ({url: location.href, dialog: document.querySelector('dialog[open]')?.textContent, editorError:document.querySelector('.editor-error')?.textContent, saveStatus:document.querySelector('#save-status')?.textContent})));
    }
    throw error;
  } finally {
    if (page) await page.unrouteAll({behavior:'ignoreErrors'});
    if (browser) await browser.close();
    fixture.kill('SIGTERM');
    fs.rmSync(temp,{recursive:true,force:true});
  }
}
run().catch(error => {
  fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({status:'failed',checks,error:error.stack,fixtureOutput},null,2));
  console.error(error.stack); process.exitCode=1;
});
