import { memo, useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import type { SlotItemMapArray } from 'swapy';
import { SwapyLayout,SwapySlot,SwapyItem,DragHandle } from '@/components/ui/swapy-draggable-card';
import { MetricDonut } from '@/components/MetricDonut';
import { Icon } from '@/components/Icon';
import { ParisClock } from '@/components/ParisClock';
import { ApplicationWeek } from '@/components/ApplicationWeek';
import type { Model,Period } from './types';

const nf=new Intl.NumberFormat('en-GB');
const num=(v:number|null)=>v==null?'—':nf.format(v);
const keys=['applied','replies','ready'] as const;
type KpiId=typeof keys[number];
const initialMap=keys.map(id=>({slot:`slot-${id}`,item:id}));
type KpiMetric={id:KpiId;label:string;value:number|null;icon:string;caption:string;tone:string};
type SavedLayout={period?:string;map?:SlotItemMapArray};
const colourMap:Record<string,string>={linkedin:'#7760c8',hellowork:'#268c68',unknown:'#dfa842',apec:'#d16f73',france_travail:'#4d98b5',francetravail:'#4d98b5',welcometothejungle:'#ab7035'};
const sites:Record<string,string>={hellowork:'HelloWork',france_travail:'France Travail',linkedin:'LinkedIn',apec:'APEC',wttj:'Welcome to the Jungle',indeed:'Indeed'};
function validMap(input:unknown):input is SlotItemMapArray {if(!Array.isArray(input)||input.length!==keys.length||!input.every(x=>x&&typeof x==='object'&&typeof x.slot==='string'&&typeof x.item==='string'))return false;return new Set(input.map(x=>x.slot)).size===keys.length&&new Set(input.map(x=>x.item)).size===keys.length&&input.every(x=>initialMap.some(y=>y.slot===x.slot)&&keys.includes(x.item));}
const layoutStorage='cv-studio.home-layout.v1';
function savedState():SavedLayout|null{try{return JSON.parse(localStorage.getItem(layoutStorage)||'null');}catch{return null;}}
function persistLayout(patch:Partial<SavedLayout>){try{localStorage.setItem(layoutStorage,JSON.stringify({...savedState(),...patch}));}catch{/* Storage is optional. */}}
function restoredMap(input:unknown):SlotItemMapArray{
 if(validMap(input))return initialMap.map(({slot})=>({...input.find(x=>x.slot===slot)!}));
 const legacy=['applied','replies','rejected','ready'];
 if(!Array.isArray(input)||input.length!==legacy.length||!input.every(x=>x&&typeof x==='object'&&legacy.includes(x.item)&&legacy.some(id=>x.slot===`slot-${id}`))||new Set(input.map(x=>x.slot)).size!==legacy.length||new Set(input.map(x=>x.item)).size!==legacy.length)return initialMap;
 const order=legacy.map(id=>input.find(x=>x.slot===`slot-${id}`)!.item).filter(id=>id!=='rejected');
 return initialMap.map(({slot},index)=>({slot,item:order[index]}));
}

export function App({model,onPeriod}:{model:Model;onPeriod:(id:string)=>void}){
 const [period,setPeriod]=useState(model.periods[model.selectedPeriod]?model.selectedPeriod:'all');
 const [message,setMessage]=useState('');
 useEffect(()=>{if(model.periods[model.selectedPeriod])setPeriod(model.selectedPeriod);},[model.selectedPeriod]);
 const p=model.periods[period]||model.periods.all;
 const parsedDate=model.capturedAt?new Date(model.capturedAt):null;
 const date=parsedDate&&!Number.isNaN(parsedDate.getTime())?new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Paris',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(parsedDate):'Not recorded';
 const metrics=useMemo<KpiMetric[]>(()=>[{id:'applied',label:'Already applied',value:p.applied,icon:'send',caption:p.outcomeNotes[0]||'Recorded applications',tone:'mint'},{id:'replies',label:'Replies',value:p.replies,icon:'message-circle',caption:p.outcomeNotes[1]||`${p.replyRate} of recorded applications`,tone:'lilac'},{id:'ready',label:'To apply',value:p.ready,icon:'arrow-up-right',caption:model.availability.roles==='available'?'Current opportunities · all time':'Role records unavailable',tone:'butter'}],[p,model.availability.roles]);
 const sourceChart=useMemo(()=>{const grouped=new Map<string,{id:string;label:string;value:number;color:string}>();for(const row of p.sources){if(row.applied===null)continue;const old=grouped.get(row.label);if(old)old.value+=row.applied;else grouped.set(row.label,{id:row.id,label:row.label,value:row.applied,color:colourMap[row.id]||'#87877a'});}return [...grouped.values()].filter(x=>x.value>0).sort((a,b)=>b.value-a.value);},[p]);
 const inv=model.inventory;
 return <div className="cd-app">
  <main className="cd-main">
   <header className="cd-hero"><ParisClock/></header>
   <div className="cd-toolbar"><nav className="cd-periods" aria-label="KPI time period">{Object.values(model.periods).map(x=><button key={x.id} data-period={x.id} aria-pressed={x.id===period} className={`cursor-interaction ${x.id===period?'cd-selected':''}`} onClick={()=>{setPeriod(x.id);onPeriod(x.id);persistLayout({period:x.id});setMessage(`Showing ${x.label.toLowerCase()} statistics.`);}}>{x.label}</button>)}</nav><a className="cd-apply-link" href="#apply" data-nav="apply">Open Apply <Icon name="arrow-up-right"/></a></div>
   {model.notices.map((notice,i)=><p key={i} className="cd-data-notice" role="status">{notice}</p>)}
   {model.live.running&&<p className="cd-data-notice" role="status">Automation is running. New saved evidence will appear after it is recorded. <a href="#activity" data-nav="activity">View history</a></p>}
   <div className="cd-sectionline"><h2>Your overview</h2><span><Icon name="grip-vertical"/>Drag KPI cards to rearrange</span></div>
   <KpiBoard metrics={metrics}/>
   <ApplicationWeek days={model.applicationWeek} note={model.applicationWeekNote}/>
   <div className="cd-insights-grid">
    <section className="cd-panel cd-sources-chart"><PanelHeading eyebrow="APPLICATIONS" title="Where you applied" meta={p.label}/>{model.availability.roles==='available'?<MetricDonut data={sourceChart} label="Applications by saved source" totalLabel="applied"/>:<p className="cd-note">Application source counts are unavailable. Refresh to try again.</p>}<p className="cd-note">Saved application sources. Matching source names are combined in this chart.</p></section>
    <section className="cd-panel cd-funnel"><PanelHeading eyebrow="DISCOVERY" title="Your search funnel" meta="Recorded minima"/><div className="cd-funnel-step" data-funnel="read"><span>Jobs collected</span><strong>{p.read}</strong><div className="cd-track"><i style={{width:p.readValue?'100%':'0%'}}/></div></div><div className="cd-funnel-step" data-funnel="chosen"><span>Shortlisted Jobs</span><strong>{p.chosen}</strong><div className="cd-track"><i style={{width:p.chosenValue!==null&&p.readValue!==null?`${Math.min(100,p.chosenValue/Math.max(p.readValue,1)*100)}%`:'0%'}}/></div></div><p className="cd-note">{p.funnelNote}</p>{p.readNote&&<p className="cd-note">{p.readNote}</p>}{p.chosenNote&&<p className="cd-note">{p.chosenNote}</p>}</section>
    <section className="cd-panel cd-automation"><PanelHeading eyebrow="BEHIND THE SCENES" title="Automation performance" meta={p.label}/><div className="cd-automation-grid">{p.performanceItems.map((x,i)=><div key={x.label} data-performance={x.label} className={`cd-auto-metric cd-auto-${i}`}><strong><PerformanceValue value={x.value}/></strong><span>{x.label}</span></div>)}</div>{model.availability.runs!=='available'&&<p className="cd-note">Run history unavailable. Refresh to try again.</p>}<details className="cd-details"><summary className="cursor-interaction">How these figures are counted</summary>{p.performanceItems.filter(x=>x.note).map(x=><p key={x.label} data-performance-note={x.label}><strong>{x.label}.</strong> {x.note}</p>)}</details></section>
   </div>
   <details className="cd-more"><summary className="cursor-interaction"><span>More Home insights</span><small>Source outcomes, saved adverts, locations & requirements</small></summary><div className="cd-expanded">
    <section className="cd-panel cd-source-outcomes"><PanelHeading title="Source outcomes" meta="Saved evidence only"/><DataTable headings={['Source','Shortlist','Applied','Replied']} rows={p.sources.map(x=>[x.label,num(x.shortlisted),num(x.applied),num(x.replies)])}/><p className="cd-note">{p.sourceNote} Historical estimates have no source allocation.</p></section>
    <section className="cd-panel cd-library"><PanelHeading title="Saved advert library" meta="All time · independent of date filter"/><div className="cd-library-numbers">{[['Unique saved adverts',inv.unique_postings],['New in latest Hunt',inv.latest_run.new_postings],['Seen again in latest Hunt',inv.latest_run.repeated_postings],['Repeat captures',inv.repeat_captures],['Shortlisted saved adverts',inv.shortlisted_postings]].map(([label,value])=><div key={String(label)}><span>{label}</span><strong>{num(value as number|null)}</strong></div>)}</div><DataTable headings={['Website','Unique adverts','Shortlisted']} rows={inv.by_website.map(x=>[sites[x.website]||x.website,num(x.unique_postings),num(x.shortlisted_postings)])}/><details className="cd-details"><summary className="cursor-interaction">Coverage and unknown values</summary><p>{inv.coverage_note}</p><p>— means unknown. There are {num(inv.saved_captures)} saved captures.</p></details></section>
    <section className="cd-panel cd-locations"><PanelHeading title="Top locations" meta={p.label}/>{p.locations.length?p.locations.map(x=><div className="cd-location" key={x.label}><span>{x.label}</span><div className="cd-track"><i style={{width:`${x.count/Math.max(...p.locations.map(t=>t.count))*100}%`}}/></div><strong>{num(x.count)}</strong></div>):<p className="cd-note">{model.availability.roles==='available'?'No locations with saved dates in this period.':'Location counts are unavailable until role records load.'}</p>}</section>
    {model.availability.requirements==='available'?<Requirements key={period} p={p}/>:<section className="cd-panel cd-requirements"><PanelHeading title="JD requirements & mentions" meta="Saved text only"/><p className="cd-note">{model.availability.requirements==='loading'?'Saved advert analysis is loading.':model.errors.jdInsights||'Saved advert analysis is unavailable. Refresh to try again.'}</p></section>}
   </div></details>
   <footer className="cd-footnote">{p.footer} Updated {date} · Paris.</footer>
   <p className="cd-sr" role="status" aria-live="polite">{message}</p>
  </main>
 </div>;
}
const KpiBoard=memo(function KpiBoard({metrics}:{metrics:KpiMetric[]}){
 const [map,setMap]=useState(()=>restoredMap(savedState()?.map));
 const [announcement,setAnnouncement]=useState('');
 const [reducedMotion,setReducedMotion]=useState(()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches);
 useEffect(()=>{const query=window.matchMedia('(prefers-reduced-motion: reduce)');const update=()=>setReducedMotion(query.matches);query.addEventListener('change',update);return()=>query.removeEventListener('change',update);},[]);
 useEffect(()=>persistLayout({map}),[map]);
 function moveCard(event:KeyboardEvent<HTMLButtonElement>,id:KpiId){
  if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
  event.preventDefault();
  const index=map.findIndex(x=>x.item===id),board=event.currentTarget.closest('.cd-kpi-grid');
  const columns=board?getComputedStyle(board).gridTemplateColumns.split(' ').length:1;
  const next=event.key==='Home'?0:event.key==='End'?map.length-1:index+(event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:event.key==='ArrowUp'?-columns:columns);
  if(next<0||next>=map.length||next===index)return;
  const updated=map.map(x=>({...x}));
  [updated[index].item,updated[next].item]=[updated[next].item,updated[index].item];
  setMap(updated);setAnnouncement(`${metrics.find(x=>x.id===id)?.label} moved to position ${next+1} of ${map.length}.`);
  requestAnimationFrame(()=>document.querySelector<HTMLButtonElement>(`#colour-home [data-swapy-item="${id}"] [data-swapy-handle]`)?.focus());
 }
 return <><p className="cd-sr" id="cd-kpi-reorder-help">Use arrow keys to move a card. Home moves it first; End moves it last.</p><SwapyLayout id="home-kpi-board" className="cd-kpi-grid" map={map} reducedMotion={reducedMotion} onSwap={next=>{if(validMap(next))setMap(next);}}>{map.map(({slot,item})=>{const metric=metrics.find(x=>x.id===item)!;return <SwapySlot id={slot} key={slot} className="cd-kpi-slot"><SwapyItem id={item} key={item} className={`cd-kpi cd-${metric.tone}`}><div className="cd-kpi-heading"><span className="cd-kpi-icon"><Icon name={metric.icon}/></span><DragHandle label={`Move ${metric.label} card`} aria-describedby="cd-kpi-reorder-help" onKeyDown={event=>moveCard(event,metric.id)}/></div><p className="cd-kpi-label">{metric.label}</p><strong className="cd-kpi-value" data-metric={metric.id}>{num(metric.value)}</strong><p className="cd-kpi-caption">{metric.caption}</p></SwapyItem></SwapySlot>;})}</SwapyLayout><p className="cd-sr" role="status" aria-live="polite">{announcement}</p></>;
},(previous,next)=>previous.metrics.length===next.metrics.length&&previous.metrics.every((x,i)=>{const y=next.metrics[i];return x.id===y.id&&x.label===y.label&&x.value===y.value&&x.icon===y.icon&&x.caption===y.caption&&x.tone===y.tone;}));
function PanelHeading({title,eyebrow,meta}:{title:string;eyebrow?:string;meta?:string}){return <header className="cd-panel-heading"><div>{eyebrow&&<p className="cd-eyebrow">{eyebrow}</p>}<h2>{title}</h2></div>{meta&&<span>{meta}</span>}</header>;}
function PerformanceValue({value}:{value:string}){return <>{value.split(/( tokens| min| h|%)/g).map((part,i)=>/^( tokens| min| h|%)$/.test(part)?<small key={i}>{part}</small>:part)}</>;}
function DataTable({headings,rows}:{headings:string[];rows:string[][]}){return <table className="cd-table"><thead><tr>{headings.map(x=><th key={x} scope="col">{x}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i}>{r.map((x,j)=><td key={j}>{x}</td>)}</tr>)}</tbody></table>;}
function Requirements({p}:{p:Period}){
 const [category,setCategory]=useState(0),[term,setTerm]=useState(0);const c=p.categories[category],t=c?.terms[term];
 return <section className="cd-panel cd-requirements"><PanelHeading title="JD requirements & mentions" meta="Saved text only"/><div className="cd-category-tabs">{p.categories.map((x,i)=><button key={x.id} className={`cursor-interaction ${i===category?'cd-selected':''}`} aria-pressed={i===category} onClick={()=>{setCategory(i);setTerm(0);}}>{x.label} · {num(x.count)}</button>)}</div><div className="cd-requirement-grid"><div className="cd-term-list">{c?.terms.map((x,i)=><button key={x.term} className={`cursor-interaction ${i===term?'cd-selected':''}`} aria-pressed={i===term} onClick={()=>setTerm(i)}><span>{x.term}</span><strong>{num(x.count)}</strong></button>)}</div><div className="cd-excerpt" aria-live="polite">{t?<><h3>{t.term} · {num(t.count)} mentions</h3><p>Example from a saved description</p>{t.hits[0]&&<><blockquote>…{t.hits[0].quote}…</blockquote><p>{t.hits[0].company} · {t.hits[0].title}</p><details className="cd-details"><summary className="cursor-interaction">Saved sources & sample excerpts</summary>{t.hits.map((h,i)=><div key={i}><p><strong>Example {i+1}.</strong> {h.company} · {h.title}</p>{i>0&&<blockquote>…{h.quote}…</blockquote>}<p>{h.url||h.source||'Source not recorded'}</p>{h.source&&h.url&&<p>Saved record: {h.source}</p>}<p>Captured: {h.captured||'date unknown'}</p></div>)}</details></>}</>:<p>No dated mentions in this period.</p>}</div></div><p className="cd-note">Top 12 terms per category; up to 3 saved sample excerpts per term. Counts are literal mentions, not unique jobs or proficiency scores.</p><details className="cd-details"><summary className="cursor-interaction">Requirement data coverage</summary><p>This bounded scan covers {num(p.coverage.documents)} documents. {num(p.coverage.undated_documents)} have no capture date and are included in All time only. {num(p.coverage.omitted_documents)} documents were omitted.</p></details></section>;
}
