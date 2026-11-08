/* Apply regression in real Chrome, with isolated records and no live jobs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const appSource = fs.readFileSync(path.join(__dirname,'static/app.js'),'utf8');
const contractGroup = vm.runInNewContext('(' + appSource.slice(appSource.indexOf('function applicationContractGroup('),appSource.indexOf('function filteredApplications(')).trim() + ')');
for (const [contract,expected] of [
  ['', 'employment'], [null, 'employment'], [undefined, 'employment'], ['Not recorded', 'employment'],
  ['CDI', 'employment'], ['CDD 6 mois', 'employment'], ['Permanent', 'employment'], ['Apprentissage', 'employment'],
  ['International CDI', 'employment'], ['Internal mobility', 'employment'], ['Stagecoach', 'employment'],
  ['Stage de 6 mois', 'internship'], ['Stagiaire', 'internship'], ['INTERNSHIP', 'internship'], ['Summer intern', 'internship'],
  ['Freelance', 'freelance'], ['Freelancing', 'freelance'], ['Indépendante', 'freelance'], ['Indépendants', 'freelance'],
  ['Self-employed', 'freelance'], ['Self employed', 'freelance'], ['Independent contractor', 'freelance'],
  ['Freelance / Internship', 'employment'],
]) assert.equal(contractGroup(contract),expected,`${JSON.stringify(contract)} contract group`);
for (const [contract,title,expected] of [
  ['', 'Stage - Chargé RSE', 'internship'], [null, 'Stagiaire RSE', 'internship'],
  ['Unknown', 'Sustainability Internship', 'internship'], ['Not-recorded', 'Summer Intern', 'internship'],
  ['Unspecified', 'Freelance Analyst', 'freelance'], ['OTHER-verify', 'Consultant Indépendant', 'freelance'],
  ['Not specified', 'Self-employed Consultant', 'freelance'], ['N/A', 'Independent contractor', 'freelance'],
  ['Non renseigné', 'Stage - Analyste', 'internship'], ['Not provided', 'Marketing intern', 'internship'],
  ['', 'Sustainability Analyst', 'employment'], ['', 'International Analyst', 'employment'],
  ['', 'Internal Communications', 'employment'], ['', 'Stagecoach Developer', 'employment'],
  ['', 'Backstage Technician', 'employment'], ['', 'Early-stage investor', 'employment'],
  ['', 'Early stage investor', 'employment'], ['', 'Stage Manager', 'employment'],
  ['', 'Senior Stage Director', 'employment'], ['', 'Freelance Internship', 'employment'],
  ['CDI', 'Stage Coordinator', 'employment'], ['CDD', 'Freelance Consultant', 'employment'],
  ['Full time', 'Sustainability Internship', 'employment'], ['Apprentissage', 'Stagiaire RSE', 'employment'],
  ['Freelance', 'Stage - Analyste', 'freelance'], ['Stage', 'Freelance Analyst', 'internship'],
  ['Freelance / Internship', 'Stage - Analyste', 'employment'], ['Unknown CDD', 'Stage - Analyste', 'employment'],
  ['CDI (à vérifier)', 'Stage - Analyste', 'employment'], ['OTHER-verify', 'Early-stage Internship', 'internship'],
  ['合同', 'Stage - Analyste', 'employment'], ['—', 'Stage - Analyste', 'internship'],
]) assert.equal(contractGroup(contract,title),expected,`${JSON.stringify(contract)} with title ${JSON.stringify(title)} contract group`);
const compactLocation = vm.runInNewContext('(' + appSource.slice(appSource.indexOf('function applicationLocationLabel('),appSource.indexOf('function renderApplicationResults(')).trim() + ')');
for (const [location,expected] of [['Paris (20e), Île-de-France · CDI','Paris 20e'],['75013 Paris · French','Paris 13e'],['Greater Paris','Île-de-France'],['Paris Region','Île-de-France'],['Île-de-France','Île-de-France'],['Nanterre - CDI - Français','Nanterre'],['Saint-André-de-la-Roche (06), Provence-Alpes-Côte d’Azur','Saint-André-de-la-Roche'],['Marseille (13) · CDI','Marseille'],['Le poste est basé dans nos bureaux','—'],['Roissy-en-France (95) · Contrat CDI','Roissy-en-France'],['95000, Cergy, Île-de-France','Cergy'],['Paris 1er','Paris 1er'],['Paris01','Paris 1er'],['Paris13','Paris 13e'],['France','—']]) assert.equal(compactLocation({location}),expected);

const os = require('node:os');
const {spawn} = require('node:child_process');
const {chromium} = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'));
const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-apply-test-'));
const ready = path.join(folder, 'ready.json');
const fixture = spawn('python3', [path.join(__dirname, 'browser_fixture.py'), '--ready', ready], {cwd:__dirname, stdio:['ignore','pipe','pipe']});
let output = '';
fixture.stderr.on('data', data => output += data);
const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
let browser;
(async () => {
  try {
    for (let i=0;i<100 && !fs.existsSync(ready);i++) {
      if (fixture.exitCode !== null) throw new Error(output || 'Fixture failed to start');
      await pause(100);
    }
    const {url,root} = JSON.parse(fs.readFileSync(ready));
    const batch = path.join(root,'Resources/Master/auto/evidence/runs/2026-09-01-fixture');
    const terminalPath = path.join(batch,'terminal.json');
    const terminal = JSON.parse(fs.readFileSync(terminalPath));
    terminal.usage = {exact_token_usage:{total_tokens:345},token_scope:'task'};
    terminal.stages.build = {status:'done',usage:{thread_cumulative_token_usage_at_observation:{total_tokens:678}}};
    fs.writeFileSync(terminalPath,JSON.stringify(terminal));
    const enText = 'The role connects sustainability projects with clear reporting and teamwork. I would like to support the team with careful data preparation and practical follow-up. My experience contributing to sustainability projects gives me a useful starting point, and I would be happy to learn the company’s methods. I am interested in work where reporting supports everyday decisions and where progress is shared openly with colleagues.';
    const frText = 'Le poste associe des projets de durabilité, un suivi clair des données et un travail collectif. Je souhaite contribuer aux missions de l’équipe avec une préparation rigoureuse et un suivi pratique. Mon expérience de contribution à des projets de durabilité constitue un point de départ utile, et je serais heureux d’apprendre vos méthodes. Je m’intéresse à un travail où les données soutiennent les décisions et où les progrès sont partagés avec les équipes.';
    fs.writeFileSync(path.join(root,'Tailored CVs/Example Green/why-this-company.md'),'# EN\n\n'+enText+'\n\n# FR\n\n'+frText+'\n');
    fs.writeFileSync(path.join(root,'Tailored CVs/Sample Partners/why-this-company.md'),'# EN\n\n'+enText+'\n');
    fs.copyFileSync(path.join(root,'Tailored CVs/Example Green/CV_Candidate.pdf'),path.join(root,'Tailored CVs/Example Green/Cover_Letter_Candidate.pdf'));
    fs.writeFileSync(path.join(root,'Tailored CVs/Example Green/cover-letter.json'),JSON.stringify({p1:'<b>Saved</b> cover letter &amp; motivation.',p2:'Second paragraph.',p3:'Third paragraph.',p4:'Final paragraph.'}));
    const frenchJD = path.join(root,'Tailored CVs/Example Green/JD.md');
    fs.appendFileSync(frenchJD,'\nAdvert language: French\n');
    fs.appendFileSync(path.join(root,'Tailored CVs/Sample Partners/JD.md'),'\nAdvert language: English\n');
    browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:url});
    const external = [];
    let terminalOverlayFixture = false;
    let activeStatusFixture = null;
    let titleContractFixture = false;
    await context.route('**/*', route => {
      if (!route.request().url().startsWith(url)) { external.push(route.request().url()); return route.abort(); }
      return route.continue();
    });
    await context.route(url+'/api/overview', async route => {
      const response = await route.fetch({headers:{...route.request().headers(),'x-studio-catalogs':'0'}});
      const body = await response.json();
      // This suite mocks the standalone catalogs to exercise the older-server
      // fallback. The consolidated contract is covered by bootstrap fixtures.
      delete body.catalogs;
      if (terminalOverlayFixture) body.live_pipeline = {active:true,finalized:true,stale:false,
        run_id:path.basename(batch),status:'follow_up',admission_blocked:true,run_raw_status:'DONE',
        finished_at:terminal.finished_at,summary:'A follow-up chat is still open.',stages:[{id:'build',status:'running'}]};
      if (activeStatusFixture) body.live_pipeline = {active:true,finalized:false,stale:activeStatusFixture==='stale',current_stage:'hunt',status:'running'};
      body.dashboard_analytics = {
        shortlisted_companies:6,total_shortlisted:8,unique_jobs_read:100,shortlist_rate_pct:8,
        sources:[
          {id:'linkedin',label:'LinkedIn',shortlisted:4,unique_jobs_read:40,rate_pct:10},
          {id:'apec',label:'APEC',shortlisted:2,unique_jobs_read:20,rate_pct:10},
          {id:'hellowork',label:'HelloWork',shortlisted:1,unique_jobs_read:null,rate_pct:null},
          {id:'wttj',label:'Welcome to the Jungle',shortlisted:1,unique_jobs_read:20,rate_pct:5},
          {id:'france_travail',label:'France Travail',shortlisted:0,unique_jobs_read:10,rate_pct:0},
          {id:'unknown',label:'Other',shortlisted:0,unique_jobs_read:null,rate_pct:null},
        ],
        locations:[{label:'Paris (20e)',count:4},{label:'Nanterre (92)',count:2},{label:'Marseille (13)',count:1}],
        scope_note:'Accepted shortlist roles plus legacy CV packages.',
        coverage_note:'Rates use recorded advert URLs only.',missing_evidence:[],
      };
      await route.fulfill({response,json:body});
    });
    await context.route(url+'/api/roles', async route => {
      const response = await route.fetch();
      const body = await response.json();
      const items = Array.isArray(body) ? body : body.items;
      const sample = items?.find(item => item.company === 'Sample Partners');
      if (sample) { sample.title = 'Senior Sustainability and Social Impact Portfolio Partnerships Programme Manager'; sample.contract = 'Freelance'; }
      const green = items?.find(item => item.company === 'Example Green');
      if (green) {
        green.contract = ''; // Unknown contracts belong to the CDD / CDI group.
        delete green.location_label; // Exercise the fallback with an already running backend.
        green.location = 'Paris (20e), Île-de-France · CDI · French';
        green.ratings = {...green.ratings, jd_english:{score:1}, profile_fit:{score:4}, company_english:{score:null}};
      }
      if (sample) { delete sample.location_label; sample.location = 'Marseille (13), Provence-Alpes-Côte d’Azur · CDI'; }
      const duplicate = items?.find(item => item.company === 'Demo Energy');
      if (duplicate) { duplicate.title = duplicate.company; duplicate.contract = 'Stage'; }
      if (titleContractFixture) {
        if (green) { green.contract = 'CDI'; green.title = 'Stage Coordinator'; }
        if (sample) { sample.contract = 'OTHER-verify'; sample.title = 'Freelance Sustainability Consultant'; }
        if (duplicate) { duplicate.contract = ''; duplicate.title = 'Stage - Chargé RSE'; }
      }

      await route.fulfill({response,json:body});
    });
    const page = await context.newPage();
    const pick = async company => { await page.locator('.apply-queue-item').filter({hasText:company}).first().click(); await page.locator('.apply-detail').filter({hasText:company}).waitFor(); };
    const errors = [];
    page.on('pageerror',error => errors.push(error.message));
    await page.goto(url + '/#apply');
    await page.locator('.application-status').first().waitFor();
    const roles = (await (await context.request.get(url+'/api/roles')).json()).items;
    const role = roles.find(item => item.company === 'Example Green');
    assert.ok(role.application.revision);
    const row = page.locator(`[data-application-row="${role.id}"]`);
    await pick('Example Green');
    const companyLogo = row.locator('img[data-company-logo]');
    await companyLogo.waitFor();
    await page.waitForFunction(roleId => {
      const image = document.querySelector(`[data-application-row="${CSS.escape(roleId)}"] img[data-company-logo]`);
      return image?.complete && image.naturalWidth > 0;
    }, role.id);
    assert.ok(await companyLogo.evaluate(node => node.closest('.company-avatar').classList.contains('company-avatar-dark-backdrop')),'Logo backdrop follows its saved render hint');
    const noLogoRow = page.locator('.apply-queue-item').filter({hasText:'Sample Partners'});
    assert.equal(await noLogoRow.locator('img[data-company-logo]').count(),0,'A non-matching brand keeps the initials fallback');
    assert.ok(await noLogoRow.locator('.company-avatar-fallback').isVisible());
    await page.locator('[data-apply-filter="all"]').click();
    assert.equal(await page.locator('.apply-queue-item').count(),3);
    fs.mkdirSync(path.join(__dirname,'.local/qa'),{recursive:true});
    await page.locator('.skip-link').evaluate(node => node.blur());
    await page.screenshot({path:path.join(__dirname,'.local/qa/apply-three-cards.png'),fullPage:true});
    assert.equal(await page.locator('.apply-split .apply-queue').count(),1,'Apply shows one job list beside one focused job');
    await pick('Demo Energy');
    const duplicateRow = page.locator('.apply-detail').filter({hasText:'Demo Energy'});
    assert.equal(await duplicateRow.locator('.application-role').innerText(),'Demo Energy\nRole title not recorded','Duplicate company/title is shown once with a truthful missing title');
    await pick('Example Green');
    for(const width of [1440,1920,2560,1024,390])for(const theme of ['light','dark']) {
      await page.setViewportSize({width,height:width===390?844:1000});await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.waitForTimeout(150);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,`Apply ${width} ${theme} has no overflow`);
      assert.equal(await page.locator('.apply-split').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length),width<=900?1:2,`Apply ${width} ${theme} list and job columns`);
      const logoBackdrops = await companyLogo.evaluate(node=>({image:getComputedStyle(node).backgroundColor,wrapper:getComputedStyle(node.closest('.company-avatar')).backgroundColor,filter:getComputedStyle(node).filter}));
      assert.deepEqual(logoBackdrops,{image:'rgba(0, 0, 0, 0)',wrapper:'rgb(255, 255, 255)',filter:'brightness(0)'},`Light-ink logo is visible over a white wrapper in ${theme}`);
      const normalLogoFilter = await companyLogo.evaluate(node=>{const wrapper=node.closest('.company-avatar');wrapper.classList.remove('company-avatar-dark-backdrop');const filter=getComputedStyle(node).filter;wrapper.classList.add('company-avatar-dark-backdrop');return filter;});
      assert.equal(normalLogoFilter,'none','Colour logos retain their original colours');
      const toolbar=await page.locator('.theme-toggle').evaluate(node=>getComputedStyle(node).backgroundColor);
      if(theme==='dark')assert.ok(!/^rgba?\(255, 255, 255(?:, 1)?\)$/.test(toolbar),'Dark theme toolbar has no solid white background');
      if(width===2560) {const widths=await page.evaluate(()=>({main:document.querySelector('#main').getBoundingClientRect().width,page:document.querySelector('.page-apply').getBoundingClientRect().width}));assert.ok(widths.page>=widths.main-110,'Apply uses available large-screen width');}
      await page.screenshot({path:path.join(__dirname,'.local/qa',`apply-final-${width}-${theme}.png`),fullPage:true});
      await row.screenshot({path:path.join(__dirname,'.local/qa',`apply-card-${width}-${theme}.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>document.documentElement.dataset.theme='light');
    await pick('Sample Partners');
    const longTitle = page.locator('.apply-detail').filter({hasText:'Sample Partners'}).locator('.application-role-copy > span');
    assert.equal(await longTitle.getAttribute('title'),'Senior Sustainability and Social Impact Portfolio Partnerships Programme Manager');
    assert.equal(await noLogoRow.locator('.apply-queue-copy span').evaluate(node => getComputedStyle(node).textOverflow),'ellipsis','Long titles stay on one line in the job list');
    await pick('Example Green');
    assert.deepEqual(await row.locator('.motivation-buttons button').allTextContents(),['Copy cover letter · FR']);
    await page.locator('.apply-filter-drawer > summary').click();
    await page.getByLabel('Documents',{exact:true}).selectOption('missing');
    assert.equal(await page.locator('.apply-queue-item').count(),1);
    await page.getByLabel('Documents',{exact:true}).selectOption('all');
    await page.getByRole('combobox',{name:'Profile fit',exact:true}).selectOption('4');
    assert.equal(await page.locator('.apply-queue-item').count(),3);
    await page.getByRole('combobox',{name:'Language',exact:true}).selectOption('en');
    assert.equal(await page.locator('.apply-queue-item').count(),1);
    assert.equal(await page.locator('.application-role strong').textContent(),'Sample Partners');
    await page.getByRole('combobox',{name:'Language',exact:true}).selectOption('fr');
    assert.equal(await page.locator('.apply-queue-item').count(),1);
    assert.equal(await page.locator('.application-role strong').textContent(),'Example Green');
    await page.getByRole('combobox',{name:'Language',exact:true}).selectOption('all');
    const contractFilter = page.getByLabel('Contract',{exact:true});
    assert.deepEqual(await contractFilter.locator('option').allTextContents(),['All contracts','CDD / CDI','Freelancing','Internship']);
    for (const [group,company] of [['employment','Example Green'],['freelance','Sample Partners'],['internship','Demo Energy']]) {
      await contractFilter.selectOption(group);
      assert.equal(await page.locator('.apply-queue-item').count(),1,`${group} shows only its contract group`);
      assert.equal(await page.locator('.application-role strong').textContent(),company);
    }
    await contractFilter.selectOption('all');
    assert.equal(await page.locator('.apply-queue-item').count(),3,'All contracts keeps every group');
    await page.getByRole('button',{name:'Reset filters'}).click();
    await page.locator('.apply-filter-drawer > summary').click();
    assert.equal(await page.locator('.apply-filter-drawer').evaluate(node => node.open),false,'Filters fold away again');
    await page.locator('[data-apply-filter="ready"]').click();
    await pick('Example Green');
    const optionsResponse = await context.request.get(url+'/api/apply-options');
    assert.equal(optionsResponse.status(),200);
    let motivationLaunches = 0;
    page.on('request', request => { if (request.method()==='POST' && request.url().includes('/motivation')) motivationLaunches++; });
    assert.equal(await row.locator('.application-copy-button').count(),1,'A job has one language-appropriate copy action');
    assert.equal(await row.locator('.application-copy-button').getAttribute('data-language'),'fr');
    await row.locator('[data-action="copy-application-text"]').click();
    await page.getByText('Cover letter text copied.',{exact:true}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()),'Saved cover letter & motivation.\n\nSecond paragraph.\n\nThird paragraph.\n\nFinal paragraph.');
    await pick('Sample Partners');
    const missingLetterRow = page.locator('.apply-detail').filter({hasText:'Sample Partners'});
    assert.equal(await missingLetterRow.locator('.application-copy-button').count(),1);
    assert.equal(await missingLetterRow.locator('.application-copy-button').getAttribute('data-language'),'en');
    await missingLetterRow.locator('[data-action="copy-application-text"]').click();
    await page.locator('#motivation-copy-text').waitFor();
    assert.equal(await page.locator('#motivation-copy-text').inputValue(),enText);
    await page.getByRole('button',{name:'Copy text',exact:true}).click();
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),enText);
    await page.getByRole('button',{name:'Close',exact:true}).last().click();
    assert.equal(motivationLaunches,0,'Copying a saved cover letter must not launch generation');
    const verified = {role_id:role.id,items:[{name:'Fixture Person',title:'CSR manager',email:{value:'csr@example.test',verified:true,source_url:'https://example.test/team',source_name:'Team page',source_role:'CSR manager',checked_at:'2026-10-03T12:00:00Z'},linkedin:{url:'https://www.linkedin.com/in/fixture-person',verified:true,source_url:'https://example.test/team',source_name:'Team page',source_role:'CSR manager',checked_at:'2026-10-03T12:00:00Z'}}]};
    await pick('Example Green');
    await row.locator('.apply-more > summary').click();
    await row.locator('.contact-discovery summary').click();
    assert.equal(await row.getByRole('button',{name:'Find contacts'}).count(),1);
    assert.equal(await row.getByRole('button',{name:'Find with Google'}).count(),0);
    let contactState='running',contactJobVisible=false,contactPosts=0,handoffs=0;
    const handoffPrompt='Find the best person to contact about this job opening at Example Green.';
    await context.route(url+'/api/applications/*/contacts',route => {
      if (route.request().method()==='POST') contactPosts++;
      return route.fulfill({json:contactState==='completed'?verified:{role_id:role.id,items:[],search_completed:false,job:contactJobVisible?{id:'fixture-contact',status:contactState}:null}});
    });
    await context.route(url+'/api/applications/*/contact-handoff',route => { handoffs++; return route.fulfill({json:{prompt:handoffPrompt,url:'https://example.test/advert',copied:false,opened:true,model:'Haiku 5.5',prompt_file:'Studio/prompts/find-contact-haiku.md'}}); });
    await context.route(url+'/api/contact-jobs/fixture-contact',route => route.fulfill({json:{id:'fixture-contact',kind:'application_contacts',provider:'sol',model:'gpt-6.1-sol',effort:'high',actual_model:'gpt-6.1-sol',actual_effort:'high',status:contactState}}));
    await context.route(url+'/api/jobs/fixture-contact/stop',route=>{contactState='stopping';return route.fulfill({json:{id:'fixture-contact',status:'stopping',summary:'Fixture stop requested; cleanup is in progress.'}})});
    await row.getByRole('button',{name:'Find contacts'}).click();
    await page.getByText('Prompt copied. In Chrome, open Claude, choose Haiku 5.5, then paste and send.').first().waitFor();
    assert.equal(handoffs,1,'Find contacts hands one job to Claude in Chrome');
    assert.equal(contactPosts,0,'Find contacts starts no background search');
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),handoffPrompt,'The filled Haiku prompt is copied');
    // A contact job started elsewhere (the pipeline) still shows progress and can be stopped here.
    contactJobVisible=true;
    await row.locator('.contact-discovery summary').click();
    await row.locator('.contact-discovery summary').click();
    await row.getByRole('button',{name:'Stop search',exact:true}).waitFor();
    await row.getByRole('button',{name:'Stop search',exact:true}).click();
    await row.getByRole('button',{name:'Stopping…',exact:true}).waitFor();
    assert.equal(await row.getByRole('button',{name:'Stopping…',exact:true}).isDisabled(),true,'Stop stays pending while cleanup runs');
    await page.screenshot({path:path.join(__dirname,'.local/qa/apply-contact-stopping.png'),fullPage:true});
    contactState='stopped';
    await row.getByRole('button',{name:'Find contacts',exact:true}).waitFor();
    contactState='completed';
    await row.locator('.contact-discovery summary').click();
    await row.locator('.contact-discovery summary').click();
    await row.getByRole('link',{name:'LinkedIn',exact:true}).waitFor();
    assert.equal(await row.getByText('Final review',{exact:true}).count(),0,'Single-model research has no extra review lane');
    assert.equal(await row.locator('.contact-workflow-step').count(),0,'One Sol job has no parallel workflow lanes');
    await row.getByText('csr@example.test',{exact:true}).waitFor();
    assert.equal(await row.getByRole('link',{name:'LinkedIn',exact:true}).getAttribute('href'),'https://www.linkedin.com/in/fixture-person');
    assert.ok(!(await row.textContent()).includes('[object Object]'));
    assert.equal(await row.locator('.rating-gauge').count(),3);
    assert.equal(await row.locator('.rating-gauge:nth-child(2) [role="meter"]').getAttribute('aria-valuenow'),'4');
    assert.equal(await row.locator('.rating-label').filter({hasText:'Company English'}).innerText(),'Company English');
    const gaugeLayout = await row.locator('.job-ratings').evaluate(node => ({
      labelsFit: [...node.querySelectorAll('.rating-label')].every(label => label.scrollWidth <= label.clientWidth + 1),
      ringTops: [...node.querySelectorAll('.readiness-ring')].map(ring => ring.getBoundingClientRect().top)
    }));
    assert.equal(gaugeLayout.labelsFit,true,'Full gauge labels fit their columns');
    assert.equal(await row.locator('.application-location').innerText(),'Paris 20e','City fallback keeps only Paris and arrondissement');
    assert.ok((await noLogoRow.locator('.apply-queue-copy span').innerText()).endsWith(' · Marseille'),'City fallback removes region and metadata');
    assert.equal(await row.locator('.apply-action-label').count(),0,'Actions have no duplicate headings');
    assert.equal(await row.locator('.rating-unknown .readiness-val').innerText(),'—');
    assert.equal(await row.locator('.rating-unknown summary').innerText(),'Company English\n—','Missing score shows no estimate wording');
    assert.equal(await row.locator('.rating-1 .readiness-val').evaluate(node=>getComputedStyle(node).color),'rgb(162, 64, 47)');
    assert.equal(await row.locator('.rating-4 .readiness-val').evaluate(node=>getComputedStyle(node).color),'rgb(29, 107, 71)');
    const scorePositions = await row.locator('.rating-gauge').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().left));
    assert.ok(scorePositions[1] < scorePositions[0] && scorePositions[0] < scorePositions[2],'Profile fit leads the score strip');
    assert.equal(await row.locator('.rating-static').count(),2,'JD English and Profile fit are static tiles');
    assert.equal(await row.locator('.rating-static details,.rating-static summary,.rating-static button,.rating-static [tabindex]').count(),0,'Static tiles have no expandable or focusable controls');
    assert.deepEqual(await row.locator('.rating-static').evaluateAll(nodes=>nodes.map(node=>node.tagName)),['DIV','DIV']);
    await row.locator('.rating-gauge:nth-child(3) summary').click();
    assert.equal(await row.locator('.rating-gauge:nth-child(3)').evaluate(node=>node.open),true,'Company estimate retains source details');
    await row.locator('.rating-gauge:nth-child(3) summary').click();
    await row.locator('.apply-more > summary').click();
    const response = page.waitForResponse(r => r.url().endsWith('/api/applications/'+role.id) && r.request().method()==='PUT');
    await row.locator('select').selectOption('applied');
    assert.equal((await response).status(),200);
    await page.getByText('Marked applied.',{exact:true}).waitFor();
    assert.equal(await page.locator('.filter-tab[data-apply-filter="ready"] span').textContent(),'1');
    assert.equal(await page.locator('.filter-tab[data-apply-filter="applied"] span').textContent(),'1');
    await page.locator('.filter-tab[data-apply-filter="applied"]').click();
    assert.equal(await row.locator('select').inputValue(),'applied');
    await page.reload({waitUntil:'domcontentloaded'});
    await page.locator('.filter-tab[data-apply-filter="applied"]').click();
    await row.waitFor();
    assert.equal(await row.locator('select').inputValue(),'applied');
    const stored = JSON.parse(fs.readFileSync(path.join(root,'Resources/Studio/.local/applications.json')));
    assert.equal(stored.applications[role.id].status,'applied');
    assert.ok(stored.applications[role.id].applied_at,'Applied status saves its own timestamp');
    const appliedAt = stored.applications[role.id].applied_at;
    await row.getByText(/Applied on/).waitFor();
    const rejection = page.waitForResponse(r => r.url().endsWith('/api/applications/'+role.id) && r.request().method()==='PUT');
    await row.locator('select').selectOption('rejected');
    assert.equal((await rejection).status(),200);
    await page.getByText('Marked rejected.',{exact:true}).waitFor();
    await page.locator('.filter-tab[data-apply-filter="rejected"]').click();
    assert.equal(await row.locator('select').inputValue(),'rejected');
    await row.getByText(/Rejected on/).waitFor();
    await row.getByText(/Applied on/).waitFor();
    await page.reload({waitUntil:'domcontentloaded'});
    await page.locator('.filter-tab[data-apply-filter="rejected"]').click();await row.waitFor();
    await row.getByText(/Rejected on/).waitFor();
    const dated = JSON.parse(fs.readFileSync(path.join(root,'Resources/Studio/.local/applications.json'))).applications[role.id];
    assert.equal(dated.applied_at,appliedAt,'Changing status keeps the original application timestamp');
    assert.ok(dated.rejected_at,'Rejected date survives reload');
    const inactive = page.waitForResponse(r => r.url().endsWith('/api/applications/'+role.id) && r.request().method()==='PUT');
    await row.locator('select').selectOption('inactive');
    assert.equal((await inactive).status(),200);
    await page.getByText('Marked inactive.',{exact:true}).waitFor();
    await page.locator('.filter-tab[data-apply-filter="inactive"]').click();
    assert.equal(await row.locator('select').inputValue(),'inactive');
    const overview = await (await context.request.get(url+'/api/overview')).json();
    const stale = await context.request.put(url+'/api/applications/'+role.id,{headers:{Origin:url,'X-Studio-Token':overview.token},data:{status:'ready',revision:role.application.revision}});
    assert.equal(stale.status(),409);
    assert.equal((await (await context.request.get(url+'/api/roles/'+role.id)).json()).application.status,'inactive');
    if (process.env.CV_STUDIO_SKIP_MOBILE !== '1') {
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth+2),false);
      assert.equal(await page.locator('.apply-split').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length),1);
      assert.equal(await row.locator('.rating-gauge').count(),3);
    }
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    activeStatusFixture = 'active';
    await page.reload({waitUntil:'domcontentloaded'});
    await page.locator('.active-run-bar:not(.is-stale)').waitFor();
    assert.ok((await page.locator('.active-run-bar').innerText()).includes('PIPELINE RUNNING'));
    assert.equal(await page.locator('.active-run-bar [data-action="stop-run"]').count(),1,'The active run keeps its Stop action');
    activeStatusFixture = 'stale';
    await page.reload({waitUntil:'domcontentloaded'});
    await page.locator('.application-row').first().waitFor();
    assert.equal(await page.locator('.active-run-bar.is-stale').count(),1,'A paused update remains visible as stale');
    activeStatusFixture = null;
    terminalOverlayFixture = true;
    await page.goto(url+'/#activity');await page.reload({waitUntil:'domcontentloaded'});
    await page.locator(`.run-list-item[data-select-run="${path.basename(batch)}"]`).click();
    await page.locator('.run-detail-heading').waitFor();
    assert.equal(await page.locator('.active-run-bar.is-finished').count(),1,'A finished run keeps its completion state while follow-up needs attention');
    assert.ok(!(await page.locator('.active-run-bar .eyebrow').innerText()).includes('PIPELINE RUNNING'));
    assert.ok(!(await page.locator(`.run-list-item[data-select-run="${path.basename(batch)}"]`).innerText()).includes('In progress'),'A terminal run keeps its recorded badge');
    assert.ok(!(await page.locator('.run-detail-heading').innerText()).includes('Running'),'Follow-up activity does not relabel a finished run');
    assert.ok(!(await page.locator('.run-stage').nth(3).locator('summary').innerText()).includes('Running'),'Follow-up activity does not overlay a finished stage');
    await page.getByText('345 tokens',{exact:true}).waitFor();
    await page.locator('.run-stage').nth(3).locator('summary').click();
    await page.getByText('678 tokens · thread total',{exact:true}).waitFor();
    assert.equal(await page.locator('.run-stage').count(),7);
    await page.getByText('Shortlisted',{exact:true}).waitFor();
    await page.goto(url+'/#overview');
    await page.locator('#colour-home .cd-app').waitFor();
    await page.locator('[data-metric="applied"]').waitFor();
    await page.locator('.cd-more>summary').click();
    for (const name of ['Your search funnel','Source outcomes','Automation performance','Advert language','Top locations','JD requirements & mentions']) await page.getByRole('heading',{name,exact:true}).waitFor();
    assert.equal(await page.locator('.cd-location').first().locator('span').first().textContent(),'Greater Paris');
    assert.equal(await page.locator('.cd-location').first().locator('strong').textContent(),'2');
    assert.equal(await page.locator('.cd-languages .cd-chart-row').count(),2);
    assert.equal(await page.locator('.cd-languages .cd-chart-row').first().locator('span').nth(1).textContent(),'French');
    assert.equal(await page.locator('.cd-languages .cd-chart-row').first().locator('strong').textContent(),'2');
    assert.equal(await page.locator('.cd-languages .cd-chart-row').nth(1).locator('span').nth(1).textContent(),'English');
    assert.equal(await page.locator('.cd-languages .cd-chart-row').nth(1).locator('strong').textContent(),'1');
    assert.equal(await page.locator('.cd-language-estimate').count(),1,'Unclassified adverts keep a visible estimate note');
    const languageColours = await page.locator('.cd-languages .cd-swatch').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backgroundColor));
    assert.equal(new Set(languageColours).size,2,'Language chart categories need distinguishable colours');
    assert.equal(await page.getByRole('heading',{name:'Applications',exact:true}).count(),0,'The retired application outcome pie stays absent; the requested seven-day bar chart is separate');
    if (await page.locator('.toast-dismiss').count()) await page.locator('.toast-dismiss').click();
    await page.locator('.skip-link').evaluate(node => node.blur());
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(__dirname,'.local/qa/home-new-charts.png'),fullPage:true});
    await page.goto(url+'/#pipeline');
    await page.locator('#colour-home .cd-app').waitFor();
    assert.equal(await page.locator('.nav-item[data-nav="pipeline"]').count(),0,'Retired Pipeline route returns Home and stays out of navigation');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth+2),false);
    titleContractFixture = true;
    await page.goto(url+'/#apply');
    await page.reload({waitUntil:'domcontentloaded'});
    await page.locator('.application-status').first().waitFor();
    await page.locator('[data-apply-filter="all"]').click();
    const titleContractFilter = page.getByLabel('Contract',{exact:true});
    for (const [group,company] of [['employment','Example Green'],['freelance','Sample Partners'],['internship','Demo Energy']]) {
      await titleContractFilter.selectOption(group);
      assert.equal(await page.locator('.apply-queue-item').count(),1,`${group} respects title fallback and saved contract precedence`);
      assert.equal(await page.locator('.application-role strong').textContent(),company);
    }
    await titleContractFilter.selectOption('all');
    assert.equal(await page.locator('.apply-queue-item').count(),3,'Title fallback keeps all saved roles available');
    assert.deepEqual(errors,[]);
    console.log('PASS: Apply contract groups and title fallback respect saved evidence; logos render, cards stay compact, updates persist, analytics pass, and browser makes no external requests.');
  } finally {
    if (browser) await browser.close();
    fixture.kill('SIGTERM');
    fs.rmSync(folder,{recursive:true,force:true});
  }
})().catch(error => { console.error(error); process.exitCode=1; });
