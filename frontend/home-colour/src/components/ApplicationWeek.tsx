import { memo, useId } from 'react';
import type { ApplicationDay } from '../types';

const countFormat=new Intl.NumberFormat('en-GB');
const dateFormat=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Paris',weekday:'long',day:'numeric',month:'long',year:'numeric'});

export const ApplicationWeek=memo(function ApplicationWeek({days,note}:{days:ApplicationDay[];note?:string}){
 const headingId=useId();
 if(!Array.isArray(days)||days.length!==7)return <section className="cd-panel cd-application-week"><h2>Applications · last 7 days</h2><p className="cd-note">Application dates are unavailable.</p></section>;
 const max=Math.max(1,...days.map(day=>day.count??0));
 return <section className="cd-panel cd-application-week"><header className="cd-week-heading"><h2 id={headingId}>Applications · last 7 days</h2><span>Paris · calendar days</span></header><div className="cd-week-chart" role="figure" aria-labelledby={headingId}>{days.map(day=>{
  const date=new Date(`${day.date}T12:00:00Z`);
  const fullDate=Number.isNaN(date.getTime())?day.date:dateFormat.format(date);
  const count=day.count===null?'—':countFormat.format(day.count);
  const height=day.count===null?0:day.count/max*100;
  const description=`${fullDate}: ${day.count===null?'application count unavailable':`${count} ${day.count===1?'application':'applications'}`}`;
  return <div key={day.date} className={`cd-week-day ${day.count===null?'cd-week-unknown':''}`} data-application-date={day.date} data-application-count={day.count??'unknown'} tabIndex={0} role="img" aria-label={description}><span className="cd-week-tooltip" aria-hidden="true">{fullDate}<br/>{day.count===null?'Count unavailable':`${count} ${day.count===1?'application':'applications'}`}</span><div className="cd-week-track" aria-hidden="true"><strong className="cd-week-count" style={{bottom:`calc(${height}% + 6px)`}}>{count}</strong><i style={{height:`${height}%`}}/></div><span className="cd-week-label" aria-hidden="true">{day.label}</span></div>;
 })}</div>{note&&<p className="cd-week-note">{note}</p>}</section>;
},(previous,next)=>previous.note===next.note&&previous.days===next.days||previous.note===next.note&&previous.days?.length===next.days?.length&&!!previous.days&&!!next.days&&previous.days.every((day,i)=>{const other=next.days[i];return day.date===other.date&&day.label===other.label&&day.count===other.count;}));
