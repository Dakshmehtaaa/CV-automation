import { memo, useEffect, useState } from 'react';

const timeFormat=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Paris',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
const dayFormat=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Paris',weekday:'long'});
const dateFormat=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Paris',day:'numeric',month:'long',year:'numeric'});

// Clock ticks belong to this component. Evidence charts stay outside its updates.
export const ParisClock=memo(function ParisClock(){
 const [now,setNow]=useState(()=>new Date());
 useEffect(()=>{
  let timer:number|undefined;
  function stop(){if(timer!==undefined){window.clearTimeout(timer);timer=undefined;}}
  function tick(){
   stop();
   if(document.hidden)return;
   setNow(new Date());
   timer=window.setTimeout(tick,1000-Date.now()%1000);
  }
  function visibility(){if(document.hidden)stop();else tick();}
  document.addEventListener('visibilitychange',visibility);tick();
  return()=>{stop();document.removeEventListener('visibilitychange',visibility);};
 },[]);
 return <div className="cd-clock" aria-live="off"><p className="cd-eyebrow">PARIS</p><time className="cd-clock-time" dateTime={now.toISOString()} aria-label={`${timeFormat.format(now)}, Paris time`}>{timeFormat.format(now)}</time><h1 className="cd-clock-day">{dayFormat.format(now)}</h1><p className="cd-clock-date">{dateFormat.format(now)}</p></div>;
});
