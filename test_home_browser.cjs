/* Paris event-date dashboard acceptance. Temporary fixture; no research or live records. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'));
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'cv-home-'));
const ready=path.join(temp,'ready.json');
const output=path.resolve(__dirname,'.local/qa/redesign-sol');fs.mkdirSync(output,{recursive:true});
const fixture=spawn('python3',[path.join(__dirname,'browser_fixture.py'),'--ready',ready],{cwd:__dirname,stdio:['ignore','pipe','pipe']});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let logs='',browser,page;fixture.stderr.on('data',d=>logs+=d);
(async()=>{
 try{
  for(let i=0;i<100&&!fs.existsSync(ready);i++){if(fixture.exitCode!==null)throw Error(logs);await pause(100)}
  const {url}=JSON.parse(fs.readFileSync(ready));
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',timezoneId:'America/Los_Angeles'});
  await context.addInitScript(()=>{
   if(!localStorage.getItem('cv-studio.home-layout.v1'))localStorage.setItem('cv-studio.home-layout.v1',JSON.stringify({period:'all',map:[{slot:'slot-applied',item:'ready'},{slot:'slot-replies',item:'rejected'},{slot:'slot-rejected',item:'applied'},{slot:'slot-ready',item:'replies'}]}));
  });
  const external=[],errors=[];let mode='full';
  await context.route('**/*',route=>{if(!route.request().url().startsWith(url)){external.push(route.request().url());return route.abort()}return route.continue()});
  const base=(await(await context.request.get(url+'/api/roles')).json()).items[0];
  const today='2026-10-05T22:30:00Z',yesterday='2026-10-04T22:30:00Z',lastWeek='2026-09-28T12:00:00Z';
  const specs=[
   {status:'rejected',applied_at:lastWeek,rejected_at:today,found_at:lastWeek,source:'linkedin',location:'Paris (75)',language:'fr'},
   {status:'offer',applied_at:yesterday,offer_at:today,found_at:yesterday,source:'indeed',location:'Paris 75020',language:'en'},
   {status:'applied',applied_at:today,found_at:today,source:'hellowork',location:'Lyon',language:'fr'},
   {status:'interviewing',applied_at:lastWeek,interviewing_at:yesterday,found_at:lastWeek,source:'linkedin',location:'Nanterre',language:'en'},
   {status:'saved',canonical_applied:true,found_at:null,source:'unknown',location:'',language:'unknown'},
   {status:'ready',found_at:yesterday,source:'linkedin',location:'Paris 20e',language:'en'},
  ];
  const urls={linkedin:'https://www.linkedin.com/jobs/view/',indeed:'https://www.indeed.com/viewjob?jk=',hellowork:'https://www.hellowork.com/fr-fr/emplois/'};
  const roles=specs.map((spec,i)=>({...base,id:`home-${i}`,folder:`Tailored CVs/Home ${i}`,company:`Home ${i}`,title:`Role ${i}`,found_at:spec.found_at,location:spec.location,language:spec.language,source_url:spec.source==='unknown'?'':urls[spec.source]+i,cv_available:i<4,canonical_cv_available:i<4,cv_built_at:i<4?spec.applied_at:null,application:{...base.application,...spec,status_history:Object.entries(spec).filter(([key])=>key.endsWith('_at')&&key!=='found_at').map(([key,at])=>({status:key.replace('_at',''),at}))}}));
  const accepted=(id,date,jobs)=>({id,kind:'batch',started_at:date,duration_seconds:600,status:'done',jobs_authoritative:true,acceptance_basis:'sealed_hunt_audit',jobs,summary_metrics:{jobs_shortlisted:jobs.length,cvs_built:1},stage_metrics:[{id:'hunt_audit',finished_at:date,jobs_shortlisted:jobs.length,status:'done'}],usage:{exact_tokens:1000,token_scope:'job'}});
  const sealedJobs=[{role_path:'Tailored CVs/Home 0',status:'shortlisted',source:'linkedin'},{role_path:'Tailored CVs/Home 1',status:'shortlisted',source:'indeed'}];
  const runs=[accepted('today',today,[sealedJobs[0]]),accepted('duplicate-today',today,[sealedJobs[0]]),accepted('yesterday',yesterday,[sealedJobs[1]]),{id:'proposal-only',kind:'batch',started_at:today,jobs_authoritative:false,jobs:[{role_path:'Tailored CVs/Proposal only',status:'proposed'}],summary_metrics:{jobs_proposed:1,jobs_shortlisted:0,cvs_built:0},stage_metrics:[{id:'hunt_audit',status:'done',finished_at:today,jobs_shortlisted:0}]}];
  runs[1].usage={exact_tokens:100000,token_scope:'thread_cumulative'};runs[2].usage={recorded_tokens:2000,token_scope:'recorded_request'};
  const workerCounter=(id,value,extra={})=>({usage_scope:'worker_thread_cumulative',usage_counter_basis:'codex_appserver_thread_token_usage',usage_thread_id:id,usage_observed_at:today,usage_binding_verified:true,recorded_tokens:{total_tokens:value},...extra});
  const rolloutCounter=(id,value)=>workerCounter(id,value,{usage_counter_basis:'codex_rollout_token_count',usage_source_integrity_verified:true,cache_included:true,whole_task_exact_tokens:null});
  runs[0].stage_metrics.push({id:'build',usage_counters:[workerCounter('covered-by-run',900000)]});
  runs.push({...accepted('worker-snapshots',today,[]),usage:{},stage_metrics:[{id:'hunt',usage_counters:[workerCounter('worker-a',1000),rolloutCounter('worker-b',2000),workerCounter('invalid-unbound',700000,{usage_binding_verified:false}),workerCounter('invalid-timestamp',800000,{usage_observed_at:'bad date'}),workerCounter('',500000)]},{id:'build',usage_counters:[workerCounter('worker-a',1000)]}]});
  runs.push({...accepted('same-worker-again',today,[]),usage:{},stage_metrics:[{id:'hunt',usage_counters:[workerCounter('worker-a',1000),workerCounter('worker-b',2000)]}]});
  const quotaUsage=(delta,window='codex:10080:1790415284',provider='codex')=>({status:'account_observed',account_delta_pp:delta,window,provider,approximate:true,windows:[{id:'primary',status:'observed',account_delta_pp:delta,window,provider,approximate:true}]});
  runs.push({...accepted('quota-use-a',today,[]),usage:quotaUsage(8)});
  runs.push({...accepted('quota-use-b',today,[]),usage:quotaUsage(2)});
  runs.push({...accepted('quota-unknown-provider',today,[]),usage:quotaUsage(11,'codex:primary:10080:12345',null)});
  runs.push({...accepted('quota-snapshot-only',today,[]),usage:{status:'partial',provider:'codex',window:'codex:primary:10080:12345',approximate:true,account_observations:[{used_percent:53}]}});
  const hits=[today,yesterday,lastWeek,null].map((captured_at,i)=>({source:`fixture${i}/JD.md`,captured_at,company:`JD ${i}`,title:'Analyst',quote:'Excellent Excel reporting skills are required.'}));
  const docs=[{...hits[0],full_capture:true,source_url:'https://example.com/jobs/today'},{...hits[0],source:'copy/JD.md',full_capture:true,source_url:'https://example.com/jobs/today?utm_source=duplicate'},{...hits[1],full_capture:true,source_url:'https://example.com/jobs/yesterday'},{...hits[2],full_capture:false,kind:'summary',source_url:'https://example.com/jobs/summary'},{...hits[3],full_capture:true,source_url:'https://example.com/jobs/unknown'}];
  const jd={documents:docs,categories:[{id:'it',label:'IT skills',terms:[{term:'Excel',count:4,hits}]}],coverage:{undated_documents:1,truncated:false,omitted_documents:0}};
  const languageRoles=()=>mode==='language-ratio'
   ?Array.from({length:403},(_,i)=>({...roles[0],id:`language-${i}`,folder:`Tailored CVs/Language ${i}`,language:i<118?'fr':i<130?'en':'unknown'}))
   :roles.map(role=>({...role,...(mode==='language-no-ratio'?{language:'unknown'}:{found_at:role.language==='unknown'?today:lastWeek})}));
  await context.route('**/api/roles',route=>route.fulfill(mode==='role-error'?{status:503,json:{error:'Fixture records unavailable'}}:{json:{items:mode==='empty'?[]:mode.startsWith('language-')?languageRoles():mode==='chart-scale'?[...roles,{...roles[2],id:'chart-scale-extra',folder:'Tailored CVs/Chart extra',source_url:'https://www.hellowork.com/fr-fr/emplois/chart-extra'}]:mode==='location'?['Paris (75)','Paris-Saclay','Île-de-France','Nanterre (92)','Toulouse 31000','LYON, France','Paris, Texas, USA','Lyon (69)'].map((location,i)=>({...roles[0],id:`location-${i}`,location})):mode==='floor'?Array.from({length:100},(_,i)=>({...roles[0],id:`floor-${i}`,folder:`Tailored CVs/Floor ${i}`,canonical_cv_available:false,application:{status:'applied',applied_at:today}})):roles}}));
  await context.route('**/api/runs',route=>route.fulfill(mode==='run-error'?{status:503,json:{error:'Fixture history unavailable'}}:{json:{items:mode==='empty'?[]:mode==='no-usage'?runs.filter(run=>run.started_at===today).map(run=>({...run,usage:{},stage_metrics:(run.stage_metrics||[]).map(stage=>({...stage,usage:{},usage_counters:[]}))})):runs}}));
  await context.route('**/api/jd-insights',route=>route.fulfill(mode==='jd-error'?{status:503,json:{error:'Fixture advert analysis unavailable'}}:{json:mode==='empty'?{documents:[],categories:[],coverage:{}}:mode==='jd-loading'?{loading:true}:jd}));
  await context.route('**/api/overview',async route=>{const response=await route.fetch({headers:{...route.request().headers(),'x-studio-catalogs':'0'}});const body=await response.json();delete body.catalogs;const readEvents=['indeed-1','indeed-2','wttj-1','wttj-2','wttj-3','wttj-4','wttj-5'].map((id,i)=>({canonical_url:'https://example.com/jobs/'+id,captured_at:'2026-10-05T19:2'+i+':00Z'}));const manual=[{canonical_url:'https://www.hellowork.com/fr-fr/emplois/82868402.html',accepted_at:'2026-10-05T18:08:51.566470+00:00',company:'Panzani',title:'Charge de Projets RSE',source:'hellowork',queue_id:'9ac1c56d806e4e77'},{canonical_url:'https://fr.linkedin.com/jobs/view/charg%C3%A9-e-de-missions-rse-at-hexaom-4465735732',accepted_at:'2026-10-05T18:08:51.566470+00:00',company:'HEXAOM',title:'Chargé de missions RSE',source:'linkedin',queue_id:'94ab440e76f048fa'},{url:'https://hellowork.com/fr-fr/emplois/82868402.html?utm_source=duplicate',accepted_at:'2026-10-05T18:08:51.566470+00:00',company:'Panzani',source:'hellowork',queue_id:'duplicate'}];body.dashboard_analytics={saved_adverts:{available:true,unique_postings:96,saved_captures:120,repeat_captures:24,shortlisted_postings:2,latest_run:{new_postings:44,repeated_postings:52},by_website:[{website:'linkedin',unique_postings:19,saved_captures:20,shortlisted_postings:1},{website:'indeed',unique_postings:16,saved_captures:20,shortlisted_postings:1}],coverage_note:'Indexed saved history only.'},unique_jobs_read:mode==='empty'?0:7,read_events:mode==='empty'?[]:readEvents,read_event_partial_dates:mode==='empty'?[]:['2026-10-05'],read_events_partial:mode!=='empty',manual_shortlists:mode==='empty'?[]:manual,missing_evidence:{read_count_without_indexed_urls:0},coverage_note:'Verified capture and screening receipts; Oct 5 has unsupported capture gaps.',historical_adjustments:mode==='empty'?{available:false}:{available:true,source:'user supplied historical estimate',verified:false,event_date:null,source_allocation:null,recorded_counts:{unique_full_ads_read:246,validated_jds_chosen_for_cv:80}}};
  if(mode==='collection')Object.assign(body.dashboard_analytics,{collection_events:[{identity:'posting:linkedin:101',captured_at:yesterday},{identity:'posting:linkedin:101',captured_at:yesterday},{identity:'job:HARK-accepted-1',captured_at:today},{identity:'job:HARK-undated-1',captured_at:null}],unique_jobs_collected:3,collection_events_partial:false,hark_feed:{available:true,captured_at_utc:'2026-10-06T00:00:00Z',screening_markers:{YES:360}},sheet_shortlists:[{identity:'job:HARK-accepted-1',accepted_at:today,source:'hark',acceptance_basis:'independent_screening_receipt'},{canonical_url:'https://hellowork.com/fr-fr/emplois/82868402.html?utm_source=sheet',accepted_at:yesterday,source:'hellowork',acceptance_basis:'independent_screening_receipt'}]});
  if(mode==='analytics-loading')body.dashboard_analytics={loading:true};if(mode==='analytics-error')body.dashboard_analytics={unavailable:true};return route.fulfill({response,json:body})});
  page=await context.newPage();await page.clock.install({time:new Date('2026-10-06T00:30:00Z')});page.on('pageerror',e=>errors.push(e.message));
  const home=async()=>{if(page.url()===url+'/#overview')await page.reload();else await page.goto(url+'/#overview');await page.locator('#colour-home .cd-app').waitFor();await page.locator('.cd-more>summary').click();await page.locator('.cd-requirements h2').waitFor();await page.locator('.cd-automation .cd-details>summary').click()};
  const applied=()=>page.locator('[data-metric="applied"]').innerText();
  const outcome=label=>page.locator(`[data-metric="${label.toLowerCase()}"]`).innerText();
  const performance=label=>page.locator(`[data-performance="${label}"] strong`).innerText();
  const performanceNote=label=>page.locator(`[data-performance-note="${label}"]`).evaluate((node,name)=>node.textContent.replace(`${name}.`,'').trim(),label);
  const select=async value=>{await page.locator(`[data-period="${value}"]`).click();await page.locator(`[data-period="${value}"][aria-pressed="true"]`).waitFor()};
  const sourceRow=label=>page.locator('.cd-source-outcomes tbody tr').filter({has:page.locator('td',{hasText:new RegExp('^'+label+'$')})});
  const inventoryCount=label=>page.locator('.cd-library-numbers>div').filter({has:page.locator('span',{hasText:new RegExp('^'+label+'$')})}).locator('strong').innerText();
  const inventorySource=label=>page.locator('.cd-library tbody tr').filter({has:page.locator('td',{hasText:new RegExp('^'+label+'$')})});
  const funnel=kind=>page.locator(`[data-funnel="${kind}"] strong`).innerText();
  const termCount=()=>page.locator('.cd-term-list button strong').innerText();
  await home();
  const weekCounts=()=>page.locator('[data-application-date]').evaluateAll(nodes=>nodes.map(node=>({date:node.dataset.applicationDate,count:node.dataset.applicationCount})));
  const initialWeek=await weekCounts();
  assert.deepEqual(initialWeek.map(day=>day.date),['2026-09-30','2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-10-05','2026-10-06'],'Seven Paris calendar days include today in a Los Angeles browser');
  assert.deepEqual(initialWeek.map(day=>day.count),['0','0','0','0','0','1','1'],'Only original applied events enter bars; recent rejection/offer dates cannot inflate applications');
  assert.ok((await page.locator('.cd-week-note').innerText()).includes('1 recorded applications have no saved applied date'),'The chart excludes undated evidence with a visible note');
  const weekGeometry=()=>page.locator('[data-application-date]').evaluateAll(nodes=>nodes.map(node=>{
   const track=node.querySelector('.cd-week-track'),bar=track.querySelector('i'),count=track.querySelector('.cd-week-count');
   return {count:node.dataset.applicationCount,barHeight:bar.getBoundingClientRect().height,trackHeight:track.getBoundingClientRect().height,baseline:track.getBoundingClientRect().bottom,labelBottom:count.getBoundingClientRect().bottom,background:getComputedStyle(track).backgroundColor};
  }));
  const initialGeometry=await weekGeometry();
  for(const day of initialGeometry.filter(day=>day.count==='0')){
   assert.equal(day.barHeight,0,'Zero applications have no raised bar');
   assert.ok(Math.abs(day.baseline-day.labelBottom-7)<=2,'Zero labels sit just above the baseline');
   assert.equal(day.background,'rgba(0, 0, 0, 0)','No full-height background can impersonate a zero-value bar');
  }
  assert.equal(await page.locator('.cd-clock-day').innerText(),'Tuesday');
  assert.equal(await page.locator('.cd-clock-date').innerText(),'6 October 2026');
  assert.match(await page.locator('.cd-clock-time').innerText(),/^02:30:\d{2}$/,'Clock uses Paris independently from browser timezone');
  assert.equal(await page.locator('.cd-stars,.cd-star-toggle,[data-action="pause-stars"]').count(),0,'Star effect and pause control are gone');
  assert.ok(!(await page.locator('.cd-hero').innerText()).includes('Where I help'));
  assert.deepEqual(await page.locator('.cd-kpi-slot').evaluateAll(nodes=>nodes.map(node=>node.querySelector('[data-swapy-item]')?.dataset.swapyItem)),['ready','applied','replies'],'Saved four-card order migrates to three cards without losing retained order');
  const moveApplied=page.getByRole('button',{name:'Move Already applied card',exact:true});
  await moveApplied.focus();await moveApplied.press('Home');
  await page.clock.runFor(20);
  assert.deepEqual(await page.locator('.cd-kpi-slot').evaluateAll(nodes=>nodes.map(node=>node.querySelector('[data-swapy-item]')?.dataset.swapyItem)),['applied','ready','replies'],'Keyboard card reordering remains available');
  assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')),'Move Already applied card','Moved card retains keyboard focus');
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('cv-studio.home-layout.v1')).map.some(row=>row.item==='rejected')),false,'Migrated saved layout removes the rejected card');
  await page.evaluate(()=>{window.__homeChartNode=document.querySelector('.cd-sources-chart .recharts-surface');window.__homeWeekNode=document.querySelector('.cd-week-chart');window.__homeCardNode=document.querySelector('[data-metric="applied"]');});
  const oldClock=await page.locator('.cd-clock-time').innerText();await page.clock.runFor(1100);
  assert.notEqual(await page.locator('.cd-clock-time').innerText(),oldClock,'Live clock advances');
  assert.equal(await page.evaluate(()=>window.__homeChartNode===document.querySelector('.cd-sources-chart .recharts-surface')&&window.__homeWeekNode===document.querySelector('.cd-week-chart')&&window.__homeCardNode===document.querySelector('[data-metric="applied"]')),true,'Clock ticks preserve chart and draggable card DOM');
  for(const selector of ['.cd-sources-chart .cd-chart-breakdown']){
   assert.equal(await page.locator(selector).evaluate(node=>getComputedStyle(node).position==='absolute'&&node.getBoundingClientRect().width<=1),true,'Legend list is removed from visible chart layout');
  }
  const firstBar=page.locator('[data-application-date]').first();await firstBar.focus();
  assert.equal(await firstBar.locator('.cd-week-tooltip').isVisible(),true,'Bar counts and full dates are available on keyboard focus');
  await moveApplied.focus();
  for(const label of ['Runs','Average duration','Average usage','Quota used','CVs built']){
   const typography=await page.locator(`[data-performance="${label}"]`).evaluate(node=>({number:Number.parseFloat(getComputedStyle(node.querySelector('strong')).fontSize),label:Number.parseFloat(getComputedStyle(node.querySelector(':scope>span')).fontSize),first:node.firstElementChild.tagName}));
   assert.equal(typography.first,'STRONG','Automation metric puts number before label');assert.ok(typography.number>=30&&typography.number>=typography.label*2,'Automation number is much larger than its label');
  }
  assert.ok(!(await page.locator('.cd-automation').textContent()).includes('Usage recorded for'),'Removed coverage boilerplate stays absent even in expanded details');
  assert.ok(!(await page.locator('.cd-automation').textContent()).includes('Quota readings are account-wide'));
  for(const title of ['Your search funnel','Automation performance','Source outcomes','Top locations'])assert.equal(await page.getByRole('heading',{name:title,exact:true}).count(),1);
  assert.equal(await page.getByRole('heading',{name:'Advert language',exact:true}).count(),0,'Advert language pie chart removed (Daksh, 12 Oct)');assert.equal(await page.locator('.cd-languages').count(),0);
  assert.equal(await page.locator('[data-period]').count(),6);
  assert.equal(await page.getByRole('heading',{name:'Saved advert library',exact:true}).count(),1);
  assert.equal(await inventoryCount('Unique saved adverts'),'96');
  assert.equal(await inventorySource('Indeed').locator('td').nth(1).innerText(),'16');
  assert.equal(await inventorySource('Indeed').locator('td').nth(2).innerText(),'1');

  assert.equal(await applied(),'5','All-time retains undated historical applied evidence with an explicit missing-date note');
  assert.equal(await page.locator('[data-swapy-item="applied"] .cd-kpi-caption').innerText(),'1 application records have no saved event date','Undated application evidence remains explicit');
  assert.equal(await outcome('Replies'),'3');assert.equal(await page.locator('[data-metric="rejected"]').count(),0);assert.equal(await performance('Average usage'),'2,000 tokens','Average covered run sums each bound worker once, excludes invalid bindings, and prefers run-scoped counter');
  assert.equal(await performance('Quota used'),'~5%','Only nonnegative account deltas from the same saved provider/window are averaged');
  assert.equal(await performanceNote('Average usage'),'Recorded for 3 of 10 runs; average uses runs with counters. Selected worker-thread totals only; cached input is included and whole-task usage is unknown.','Token average states its partial coverage and cumulative source');
  assert.equal(await performanceNote('Quota used'),'Average saved account-wide change from 2 readings','Quota remains separate from token counts');
  assert.ok(!(await page.locator('.cd-automation').innerText()).includes('saved counters summed'));
  assert.equal(await page.getByText('Employer offers',{exact:true}).count(),0,'Offers are absent from Home');
  assert.equal(await funnel('read'),'253+','Verified read events dedupe before the explicitly historical estimate is added');
  assert.equal(await funnel('chosen'),'84+','Manual audit acceptances merge by canonical URL; duplicate alias and proposal are excluded');
  assert.ok(!(await page.locator('.cd-funnel').innerText()).includes('user-supplied historical ad reads'));
  assert.ok(!(await page.locator('.cd-funnel').innerText()).includes('estimates have no assigned date or source'));
  assert.ok((await page.locator('.cd-funnel').innerText()).includes('Includes unverified historical estimates: 246 ads read and 80 JDs chosen, with no saved date or source.'),'Historical minima keep their explicit evidence limits');
  assert.equal(await performance('CVs built'),'4','CV count uses canonical PDFs in role catalog');
  assert.equal(await page.locator('.cd-funnel-step').count(),2,'Funnel does not repeat application outcome KPIs');
  assert.equal(await page.locator('[data-performance]').count(),5,'Performance adds quota usage without repeating funnel KPIs');
  assert.equal(await sourceRow('LinkedIn').locator('td').nth(1).innerText(),'2','Verified manual HEXAOM acceptance joins the sealed LinkedIn acceptance; historical estimates stay excluded');
  await select('today');
  assert.deepEqual(await weekCounts(),initialWeek,'Last-seven-days chart stays independent of main period filter');
  assert.equal(await inventoryCount('Unique saved adverts'),'96','Library stays explicitly all-time while period changes');
  assert.equal(await applied(),'1','Rejection/offer dated today must not become an application today');
  assert.equal(await page.locator('[data-metric="rejected"]').count(),0);assert.equal(await outcome('Replies'),'2');
  assert.equal(await performance('Average usage'),'2,000 tokens','Today uses only Paris-dated token counters');
  assert.equal(await performanceNote('Average usage'),'Recorded for 2 of 9 runs; average uses runs with counters. Selected worker-thread totals only; cached input is included and whole-task usage is unknown.');
  assert.equal(await performance('Quota used'),'~5%','Today averages only compatible saved quota-delta receipts');
  assert.equal(await funnel('read'),'0','Paris Oct 6 has no capture events; Oct 5 gaps do not make today unknown');
  
  assert.ok((await page.locator('.cd-locations').innerText()).includes('Lyon'));
  assert.equal(await termCount(),'1');
  const linkedinToday=sourceRow('LinkedIn');
  assert.equal(await linkedinToday.locator('td').nth(2).innerText(),'0','Source Applied uses application date, not rejection date');
  assert.equal(await linkedinToday.locator('td').nth(3).innerText(),'1','Source Replies uses the saved response date independently from the application date');
  assert.equal(await funnel('chosen'),'1');
  await select('yesterday');assert.equal(await applied(),'1');assert.equal(await page.locator('[data-metric="rejected"]').count(),0);assert.equal(await outcome('Replies'),'1');assert.equal(await performance('Average usage'),'2,000 tokens');assert.equal(await performanceNote('Average usage'),'Recorded for 1 of 1 runs; average uses runs with counters');assert.equal(await performance('Quota used'),'—','No saved delta means no quota percentage');assert.equal(await performanceNote('Quota used'),'No saved before-and-after quota change for this period');
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.period),'yesterday','Changing the period preserves keyboard focus');
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('cv-studio.home-layout.v1')).period),'yesterday','The selected period is saved with the Home layout');
  assert.equal(await page.locator('.page.is-entering').count(),0,'Filtering and background renders do not replay the page entrance');
  assert.equal(await funnel('read'),'7+','Paris Oct 5 reads use actual capture dates and remain a known minimum');
  assert.equal(await funnel('chosen'),'3','Two manual approvals plus one distinct sealed acceptance dated Oct 5');
  assert.equal(await performance('Average usage'),'2,000 tokens','Scoped recorded usage stays distinct when exact is absent');
  assert.equal(await page.locator('.cd-location').filter({has:page.locator('span',{hasText:'Greater Paris'})}).locator('strong').innerText(),'2','Paris office spellings group once into Greater Paris');
  assert.equal(await termCount(),'1');
  await select('this_week');assert.equal(await applied(),'2','Paris week includes Oct 5 and Oct 6 in a Los Angeles browser');assert.equal(await performance('Average usage'),'2,000 tokens','Weekly token average includes Paris-dated counters');assert.equal(await performanceNote('Average usage'),'Recorded for 3 of 10 runs; average uses runs with counters. Selected worker-thread totals only; cached input is included and whole-task usage is unknown.');
  await select('last_week');assert.equal(await applied(),'2');assert.equal(await termCount(),'1');
  await select('this_month');assert.equal(await applied(),'2');assert.equal(await termCount(),'2');
  await select('all');assert.equal(await termCount(),'4');
  assert.ok((await page.locator('.cd-requirements').innerText()).includes('Counts are literal mentions, not unique jobs or proficiency scores.'));
  assert.equal(await page.locator('.cd-excerpt>blockquote').innerText(),'…Excellent Excel reporting skills are required.…','The visible excerpt preserves the literal saved source quote');
  await page.locator('.cd-excerpt .cd-details>summary').click();
  assert.ok((await page.locator('.cd-excerpt').innerText()).includes('https://example.com/jobs/today'),'The captured full advert retains its saved source URL');
  assert.ok((await page.locator('.cd-excerpt').innerText()).includes('Captured: 2026-10-05T22:30:00Z'),'The quote retains its capture date');
  runs.push({...accepted('quota-new-window',today,[]),usage:quotaUsage(4,'codex:10080:next-window')});
  await home();assert.equal(await performance('Quota used'),'—','Different saved quota windows are never averaged together');runs.pop();
  const sameWindowQuotaRuns=runs.filter(run=>run.id==='quota-use-a'||run.id==='quota-use-b');
  for(const run of sameWindowQuotaRuns){run.usage.approximate=false;run.usage.windows[0].approximate=false;}
  await home();assert.equal(await performance('Quota used'),'5%','The approximation marker follows the saved receipt basis');
  for(const run of sameWindowQuotaRuns){run.usage.approximate=true;run.usage.windows[0].approximate=true;}
  await home();assert.equal(await performance('Quota used'),'~5%');
  mode='location';await home();assert.equal(await page.locator('.cd-location').filter({has:page.locator('span',{hasText:/^Greater Paris$/})}).locator('strong').innerText(),'4');assert.equal(await page.locator('.cd-location').filter({has:page.locator('span',{hasText:/^Lyon, France$/})}).locator('strong').innerText(),'2');assert.equal(await page.locator('.cd-location').filter({has:page.locator('span',{hasText:/^Toulouse, France$/})}).locator('strong').innerText(),'1');assert.equal(await page.locator('.cd-location').filter({has:page.locator('span',{hasText:/^Paris, Texas, USA$/})}).locator('strong').innerText(),'1','Foreign Paris stays separate');
  mode='collection';await home();await select('all');
  assert.equal(await funnel('read'),'3','Known Jobs collected excludes unallocated historical read estimate');
  assert.equal(await funnel('chosen'),'85+','One independent Hark acceptance joins saved acceptances; a cross-source URL alias counts once and YES marker counts add nothing');
  assert.ok(!(await page.locator('.cd-funnel').innerText()).includes('246 ads read'),'Historical read estimate is not advertised as part of the new collection total');
  await select('today');assert.equal(await funnel('read'),'1+','Dated collection excludes undated jobs, with a minimum marker');assert.equal(await funnel('chosen'),'2');
  await select('yesterday');assert.equal(await funnel('read'),'1+','Duplicate source events count one collected job in a dated view');
  mode='full';await home();await select('all');
  mode='floor';await home();assert.equal(await applied(),'100');assert.equal(await funnel('chosen'),'100+','Authorized historical floor remains a known minimum');assert.ok(!(await page.locator('.cd-funnel').innerText()).includes('estimated historical shortlist floor'));assert.equal(await sourceRow('LinkedIn').locator('td').nth(1).innerText(),'2','Historical floor cannot inflate source shortlist counts');mode='full';await home();
  mode='full';await home();await select('all');
  mode='chart-scale';await home();
  const scaledGeometry=await weekGeometry();
  const one=scaledGeometry.find(day=>day.count==='1'),two=scaledGeometry.find(day=>day.count==='2');
  assert.ok(one&&two&&Math.abs(two.barHeight/one.barHeight-2)<.05,'Two applications produce twice the visible height of one');
  assert.ok(two.labelBottom<one.labelBottom,'Positive value labels follow their own bar heights');
  mode='full';await home();
  for(const width of [1440,1920,2560,1024,390])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:width===390?844:1000});await page.evaluate(t=>{document.documentElement.dataset.theme=t;document.querySelector('.skip-link')?.blur();scrollTo(0,0)},theme);await page.waitForTimeout(150);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,`${width} ${theme} no page overflow`);
   for(const selector of ['.cd-sources-chart']){
    const geometry=await page.locator(selector).evaluate(node=>{
     const plot=node.querySelector('.cd-donut-plot'),surface=plot.querySelector('.recharts-surface'),style=getComputedStyle(node),card=node.getBoundingClientRect();
     return {plotWidth:plot.getBoundingClientRect().width,plotHeight:plot.getBoundingClientRect().height,contentWidth:card.width-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight)-2,surfaceHeight:surface?.getBoundingClientRect().height||0,bottomGap:card.bottom-node.lastElementChild.getBoundingClientRect().bottom};
    });
    assert.ok(geometry.plotWidth/geometry.contentWidth>.95,`${width} ${theme} donut fills the card content width`);
    assert.ok(Math.abs(geometry.plotWidth-geometry.plotHeight)<2&&geometry.surfaceHeight>50,`${width} ${theme} donut stays square and visible`);
    assert.ok(geometry.bottomGap<=27,`${width} ${theme} chart card has no stretched blank bottom`);
   }
   await page.screenshot({path:path.join(output,`home-functional-${width}-${theme}.png`),fullPage:true});
  }
  mode='empty';await home();assert.equal(await page.locator('#apply-count').innerText(),'0','Apply badge explicitly shows zero ready jobs');assert.equal(await applied(),'0');assert.equal(await outcome('Replies'),'0');assert.equal(await funnel('read'),'0','An explicit zero read receipt remains known zero');assert.equal(await funnel('chosen'),'—','An empty run list has no acceptance receipt proving a zero shortlist');assert.ok((await weekCounts()).every(day=>day.count==='0'),'A returned empty application catalog gives seven known zeros');
  mode='role-error';await home();assert.equal(await applied(),'—');assert.equal(await page.locator('[data-metric="rejected"]').count(),0);assert.equal(await performance('CVs built'),'—','Missing role catalog must not become zero available CVs');assert.ok((await weekCounts()).every(day=>day.count==='unknown'),'Failed role read gives seven unknown counts, not invented zeros');
  mode='run-error';await home();assert.ok((await page.locator('.cd-automation').innerText()).includes('Run history unavailable'));assert.equal(await performance('Runs'),'—','Missing run history must not look like an empty run list');
  mode='analytics-loading';await home();assert.equal(await funnel('read'),'—');assert.equal(await funnel('chosen'),'—');assert.equal(await inventoryCount('Unique saved adverts'),'—');assert.ok((await page.locator('.cd-data-notice').innerText()).includes('Checking saved advert evidence.'));
  mode='analytics-error';await home();assert.equal(await funnel('read'),'—');assert.equal(await funnel('chosen'),'—');assert.equal(await inventoryCount('Unique saved adverts'),'—');assert.ok((await page.locator('.cd-data-notice').innerText()).includes('Saved advert evidence is unavailable.'));
  mode='jd-loading';await home();assert.ok((await page.locator('.cd-requirements').innerText()).includes('Saved advert analysis is loading.'));assert.equal(await page.locator('.cd-term-list').count(),0,'Loading analysis must not show zero literal mentions');
  mode='jd-error';await home();await page.getByText('Fixture advert analysis unavailable',{exact:true}).waitFor();assert.equal(await page.locator('.cd-term-list').count(),0,'Failed analysis must not show zero literal mentions');
  mode='no-usage';await home();await select('today');assert.equal(await performance('Average usage'),'—','Missing telemetry stays unknown instead of becoming zero');assert.equal(await performanceNote('Average usage'),'No token counters recorded for 9 runs');assert.equal(await performance('Quota used'),'—');assert.equal(await performanceNote('Quota used'),'No saved before-and-after quota change for this period');
  const savedRuns=runs.splice(0,runs.length);
  runs.push(accepted('compact-tokens',today,[]));
  for(const [tokenCount,expected] of [[3500000,'3.5M tokens'],[35000000,'35M tokens'],[39025228,'39M tokens']]){
   runs[0].usage={exact_tokens:tokenCount,token_scope:'job'};mode='full';await home();assert.equal(await performance('Average usage'),expected,`Compact ${tokenCount} tokens`);
  }
  runs.splice(0,runs.length,...savedRuns);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'home-functional-results.json'),JSON.stringify({status:'passed',checks:['Zero bars and labels at baseline; positive heights proportional','Donuts fill card width with no stretched empty bottoms across responsive sizes','Six Paris calendar periods change all dashboard sections','Applied uses application events independently from rejection/offer events','Unknown dates kept all-time and excluded scoped','Source applied/replied counts','Sealed and manual accepted identities merge by canonical URL and Paris acceptance date; duplicate alias and proposal excluded','Full captured adverts never become read proof; verified screened reads are date-scoped and gaps remain minimums','Offers absent and application outcomes not repeated in funnel/performance','Historical minima explicitly disclose unverified estimates without inventing dates or source allocation','Canonical role catalog PDF counts','Estimated French/English allocation preserves exact total, labels estimates, handles date fallback and absent ratio','Token values compact to M notation without changing units','Today, Yesterday and This week token totals follow Paris dates and expose run coverage','Missing token/quota telemetry stays unknown with a clear explanation','Quota delta shown only for matching, saved provider/window receipts; snapshot percentages and incompatible windows excluded','Bound worker snapshots dedupe per thread and sum per run; invalid bindings omitted','Requirements source quotes and date scope','Greater Paris, French cities, and foreign Paris normalization','Authorized estimated shortlist floor stays undated and source unallocated','Loading, unavailable and empty evidence stay distinct; unknown values never become zero','No external requests or browser errors']},null,2));
  console.log('PASS: dashboard Paris event dates, funnel evidence, source outcomes, date-scoped requirements, responsive variants and empty/error states.');
 }catch(error){if(page)await page.screenshot({path:path.join(output,'home-functional-failure.png'),fullPage:true});throw error}
 finally{if(browser)await browser.close();fixture.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true})}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
