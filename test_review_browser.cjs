/* Real rendered UI, disposable inventory, no network or real worker dispatch. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.CV_PLAYWRIGHT || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'));
const output = process.env.CV_STUDIO_QA_OUTPUT || path.join(__dirname, '.local/qa/review-' + Date.now());
fs.mkdirSync(output, {recursive:true});
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-review-browser-'));
const ready = path.join(temp, 'ready.json');
const fixture = spawn(process.env.CV_PYTHON || 'python3', [path.join(__dirname, 'browser_fixture.py'), '--ready', ready], {stdio:['ignore','pipe','pipe']});
let fixtureOutput = '';
fixture.stdout.on('data', x => fixtureOutput += x);
fixture.stderr.on('data', x => fixtureOutput += x);
const checks = [], errors = [], external = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, page;
(async () => {
  try {
    for (let i=0; i<100 && !fs.existsSync(ready); i++) {
      if (fixture.exitCode !== null) throw new Error(fixtureOutput);
      await pause(100);
    }
    const {url, root} = JSON.parse(fs.readFileSync(ready));
    const folder = path.join(root, 'Tailored CVs/Example Green');
    const provenance = {kind:'requested_manual_job_role_provenance_v1', queue_id:'a'.repeat(16),
      input_sha256:'b'.repeat(64), run_id:'fixture-held', role_path:folder,
      role_jd_sha256:createHash('sha256').update(fs.readFileSync(path.join(folder,'JD.md'))).digest('hex')};
    fs.writeFileSync(path.join(folder,'requested-job-provenance.json'),JSON.stringify(provenance));
    fs.copyFileSync(path.join(folder,'CV_Candidate.pdf'),path.join(folder,'Cover_Letter_Candidate.pdf'));
    const local = path.join(root, 'Resources/Studio/.local'); fs.mkdirSync(local,{recursive:true});
    fs.writeFileSync(path.join(local,'manual-jobs.json'),JSON.stringify({schema_version:1,items:[{
      id:provenance.queue_id,input_sha256:provenance.input_sha256,role_path:folder,status:'held',
      audit:{final_approval:null,studio_sync:null}}]}));
    browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    await context.route('**/*', route => {
      const target = route.request().url();
      if (target.startsWith(url) || target.startsWith('data:') || target.startsWith('chrome://') || target.startsWith('chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/')) return route.continue();
      external.push(target); return route.abort();
    });
    page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(url+'/#apply');
    await page.getByRole('button',{name:'To apply 1',exact:true}).waitFor();
    await page.getByRole('button',{name:'Review first 2',exact:true}).click();
    await page.getByRole('searchbox',{name:'Search jobs to apply'}).fill('Example Green');
    assert.equal(await page.locator('.application-row').count(),1);
    await page.getByText('This requested CV is on hold. Final approval and Studio sync are still required.').waitFor();
    await page.screenshot({path:path.join(output,'held-review-card.png'),fullPage:true});
    checks.push('Held requested PDFs stay out of To apply and expose a clear review reason');
    await page.getByRole('button',{name:'View CV for Example Green'}).click();
    await page.locator('#artifact-select').selectOption({label:'Cover letter'});
    const letterUrl = await page.locator('.pdf-preview').getAttribute('src');
    const documentResponse = await context.request.get(new URL(letterUrl.split('#')[0],url).href);
    assert.equal(documentResponse.status(),200);
    assert.equal(documentResponse.headers()['content-type'],'application/pdf');
    const popupWait = context.waitForEvent('page');
    await page.getByRole('link',{name:'Open preview in a new tab'}).click();
    const pdfPage = await popupWait; await pdfPage.waitForLoadState();
    assert.ok(pdfPage.url().startsWith(url+'/api/assets?key=')); await pdfPage.close();
    await page.screenshot({path:path.join(output,'held-letter-preview.png'),fullPage:true});
    await page.getByRole('button',{name:'Close CV package'}).press('Escape');
    checks.push('Held local CV and letter preview/select/open remain available for review');
    await page.getByLabel('Application status for Example Green').selectOption('applied');
    await page.getByRole('button',{name:'Applied 1',exact:true}).click();
    await page.locator('.application-row .package-review-note').waitFor();
    assert.equal(await page.getByLabel('Application status for Example Green').inputValue(),'applied');
    checks.push('Explicit Applied choice is retained with package review warning');
    await page.getByRole('link',{name:'CVs',exact:true}).click();
    const catalog = await (await context.request.get(url+'/api/roles')).json();
    const heldId = catalog.items.find(row=>row.company==='Example Green').id;
    const otherId = catalog.items.find(row=>row.company==='Sample Partners').id;
    let failOldRequest;
    const oldRequest = new Promise(resolve => failOldRequest=resolve);
    await page.route(url+'/api/roles/'+heldId,async route => { await oldRequest; await route.fulfill({status:503,json:{error:{message:'Old package request failed.'}}}); });
    const pending = page.waitForRequest(url+'/api/roles/'+heldId);
    await page.getByRole('button',{name:'Open Example Green package',exact:true}).click(); await pending;
    await page.getByText('Opening the CV package…',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Close CV package'}).click();
    await page.getByRole('button',{name:'Open Sample Partners package',exact:true}).click();
    await page.locator('#detail-title').filter({hasText:'CSR Project Manager'}).waitFor();
    const oldResponse = page.waitForResponse(url+'/api/roles/'+heldId); failOldRequest(); await oldResponse;
    // Give the awaited fetch rejection a rendering turn before asserting.
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#detail-title').innerText(),'CSR Project Manager','A failed canceled package must not replace the newly selected package');
    checks.push('Late error from canceled package cannot overwrite another package');
    await page.getByRole('button',{name:'Close CV package'}).press('Escape');
    await page.route(url+'/api/roles/'+otherId,route=>route.fulfill({status:503,json:{error:{message:'Fixture package unavailable.'}}}));
    await page.getByRole('button',{name:'Open Sample Partners package',exact:true}).click();
    await page.getByText('Fixture package unavailable.',{exact:true}).waitFor();
    await page.unroute(url+'/api/roles/'+otherId);
    await page.getByRole('button',{name:'Try again',exact:true}).click();
    await page.locator('#detail-title').filter({hasText:'CSR Project Manager'}).waitFor();
    checks.push('Current package error shows working retry and returns to actual server data');
    assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({status:'passed',checks,errors,external},null,2));
    console.log(JSON.stringify({status:'passed',checks,output},null,2));
  } catch (error) {
    if (page) await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({status:'failed',checks,error:String(error),errors,external},null,2));
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.kill('SIGTERM');
    await Promise.race([new Promise(resolve=>fixture.once('exit',resolve)),pause(3000)]);
    fs.rmSync(temp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
