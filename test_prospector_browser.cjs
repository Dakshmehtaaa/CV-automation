/* Real embedded Prospector, fictional records and fake model CLIs only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'));
const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-prospect-browser-'));
const ready = path.join(folder, 'ready.json');
const output = path.join(__dirname, '.local', 'prospector-verification');
fs.mkdirSync(output, {recursive:true});
const fixture = spawn('python3', [path.join(__dirname, 'browser_fixture.py'), '--ready', ready, '--with-prospector'], {cwd:__dirname, stdio:['ignore','pipe','pipe']});
let log = '';
fixture.stderr.on('data', data => log += data);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
(async () => {
  try {
    for (let i=0; i<100 && !fs.existsSync(ready); i++) {
      if (fixture.exitCode !== null) throw new Error(log || 'Fixture startup failed');
      await pause(100);
    }
    const {url, root} = JSON.parse(fs.readFileSync(ready));
    browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    const external = [], mutations = [], errors = [];
    await context.route('**/*', route => {
      const request = route.request();
      if (!request.url().startsWith(url + '/')) { external.push(request.url()); return route.abort(); }
      if (!['GET','HEAD'].includes(request.method())) mutations.push(request.url());
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url + '/#prospect');
    const iframe = page.locator('iframe.prospect-frame');
    await iframe.waitFor({state:'visible'});
    const frame = page.frameLocator('iframe.prospect-frame');
    await frame.locator('#health[data-state="ok"]').waitFor();
    for (const name of ['today','find','leads','learn','settings']) {
      await frame.locator(`[data-route="${name}"]`).click();
      await frame.locator('#workspace-page').filter({hasText:new RegExp(name, 'i')}).waitFor();
    }
    await frame.locator('[data-mode="job"]').click();
    await frame.locator('[data-mode="job"][aria-pressed="true"]').waitFor();
    await frame.locator('[data-mode="consulting"]').click();
    await frame.locator('[data-route="find"]').click();
    await frame.getByRole('button', {name:'I know a company',exact:true}).click();
    const company = frame.getByPlaceholder('Company name or 9-digit SIREN');
    await company.fill('Fictional unsaved company');
    const handle = await iframe.elementHandle();
    await page.getByRole('button', {name:'Refresh workspace',exact:true}).click();
    assert.equal(await company.inputValue(), 'Fictional unsaved company');
    for (const route of ['overview','apply','library','contacts','settings','activity','workspace']) {
      await page.locator(`[data-nav="${route}"]`).first().click();
      await page.locator(`#main > .page-${route}:not([hidden])`).waitFor();
      await page.locator('[data-nav="prospect"]').click();
      assert.equal(await company.inputValue(), 'Fictional unsaved company', route + ' preserves draft');
      assert.equal(await handle.evaluate(node => node === document.querySelector('iframe.prospect-frame')), true);
    }
    await company.fill('');
    await frame.locator('[data-route="today"]').click();
    await page.screenshot({path:path.join(output, 'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output, 'mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await iframe.evaluate(node => node.contentDocument.documentElement.scrollWidth <= node.clientWidth), true);
    await context.route(url + '/prospect/api/integration-error', route => route.fulfill({
      status:503,contentType:'application/json',body:JSON.stringify({
        error:{code:'prospector_unavailable',message:'Prospector is unavailable. Retry.'}}),
    }));
    const message = await frame.locator('body').evaluate(async () => {
      const {api} = await import('./js/api.js');
      try { await api('/integration-error'); } catch (error) { return error.message; }
    });
    assert.equal(message, 'Prospector is unavailable. Retry.');
    assert.deepEqual(external, []);
    assert.deepEqual(mutations, [], 'Browsing and refresh must not start work or alter settings');
    assert.deepEqual(errors, []);
    assert.equal(fs.existsSync(path.join(root, 'Prospector/data/leads/fixture-expired.json')), true);
    console.log(JSON.stringify({status:'passed',checks:['real embedded assets/API','all five Prospector views','both modes','draft preserved through refresh and all seven Studio areas','mobile layout','no external requests or mutations','saved record preserved'],output},null,2));
  } finally {
    if (browser) await browser.close();
    fixture.kill('SIGTERM');
    await Promise.race([new Promise(resolve => fixture.once('exit',resolve)),pause(7000)]);
    fs.rmSync(folder,{recursive:true,force:true});
  }
})().catch(error => { console.error(error); if (log) console.error(log); process.exitCode=1; });
