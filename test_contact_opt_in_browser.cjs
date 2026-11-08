/* Fixture-only Add job consent UI. No production queue, model, or external request. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'));
const folder = fs.mkdtempSync(path.join(os.tmpdir(),'cv-contact-choice-'));
const ready = path.join(folder,'ready.json');
const fixture = spawn('python3',[path.join(__dirname,'browser_fixture.py'),'--ready',ready],{cwd:__dirname,stdio:['ignore','pipe','pipe']});
const evidence = path.resolve(__dirname,'../Master/auto/evidence/manual/2026-10-06-hunt-repair-145408Z/contacts');
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
let browser;
(async()=>{
 try {
  for(let i=0;i<100&&!fs.existsSync(ready);i++) await pause(100);
  const {url} = JSON.parse(fs.readFileSync(ready));
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[]; page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',route=>route.request().url().startsWith(url)?route.continue():route.abort());
  await page.goto(url+'/#apply');
  await page.locator('.manual-add-job summary').click();
  const choice=page.getByRole('combobox',{name:'Find people to contact',exact:true});
  assert.equal(await choice.inputValue(),'no');
  fs.mkdirSync(evidence,{recursive:true});
  await page.screenshot({path:path.join(evidence,'add-job-default-off.png'),fullPage:true});
  await page.locator('[name=url]').fill('https://jobs.example/off');
  await page.getByRole('button',{name:'Save job',exact:true}).click();
  await page.getByText('Contact search off',{exact:true}).waitFor();
  let queue=await (await page.request.get(url+'/api/manual-jobs')).json();
  assert.equal(queue.items[0].find_contacts,false);
  await page.locator('.manual-add-job summary').click();
  assert.equal(await choice.inputValue(),'no');
  await page.locator('[name=url]').fill('https://jobs.example/on');
  await choice.selectOption('yes');
  await page.getByRole('button',{name:'Save job',exact:true}).click();
  await page.getByText('Public contacts: Queued',{exact:true}).waitFor();
  queue=await (await page.request.get(url+'/api/manual-jobs')).json();
  assert.equal(queue.items.find(x=>x.url.endsWith('/on')).find_contacts,true);
  await page.reload();
  await page.getByText('Public contacts: Queued',{exact:true}).waitFor();
  await page.screenshot({path:path.join(evidence,'saved-job-contact-states.png'),fullPage:true});
  const fixtureContact = {name:'Fixture recruiter',title:'Talent Acquisition Partner',organization:'Fixture employer',verified:true,
    email:{value:'recruiter@employer.example',verified:true,source_url:'https://employer.example/team',checked_at:'2026-10-06T10:00:00Z'},linkedin:null};
  fs.writeFileSync(path.join(evidence,'fixture-only-contact-result.json'),JSON.stringify({fixture_only:true,live_public_evidence:false,contact:fixtureContact},null,2));
  await page.route(url+'/api/manual-jobs',async route=>{
    if(route.request().method()!=='GET') return route.continue();
    const response=await route.fetch(); const body=await response.json();
    body.items=body.items.map(item=>item.url.endsWith('/on')?{...item,contact_research:{status:'completed',summary:'One verified fixture result.',contacts:[fixtureContact]}}:item);
    return route.fulfill({response,json:body});
  });
  await page.reload();
  await page.getByText('Fixture recruiter',{exact:true}).waitFor();
  assert.equal(await page.locator('.manual-job-item .contact-email').textContent(),'recruiter@employer.example');
  assert.equal(await page.locator('.manual-job-item a[href="https://employer.example/team"]').count(),1);
  await page.screenshot({path:path.join(evidence,'fixture-contact-result-sources.png'),fullPage:true});
  await page.unroute(url+'/api/manual-jobs');
  await page.reload();

  const toggle=page.locator('[data-manual-contact-choice]').filter({has:page.locator('never')});
  const onRow=page.locator('.manual-job-item').filter({has:page.locator('a[href="https://jobs.example/on"]')});
  await onRow.locator('input[type=checkbox]').uncheck();
  await onRow.getByText('Contact search off',{exact:true}).waitFor();
  await page.reload();
  queue=await (await page.request.get(url+'/api/manual-jobs')).json();
  assert.equal(queue.items.find(x=>x.url.endsWith('/on')).find_contacts,false);
  assert.deepEqual(errors,[]);
  console.log('PASS: default off, explicit yes, API persistence, reload, independent contact status. Fixture only; no worker started.');
 } finally { if(browser) await browser.close(); fixture.kill('SIGTERM'); fs.rmSync(folder,{recursive:true,force:true}); }
})().catch(e=>{console.error(e);process.exitCode=1;});
