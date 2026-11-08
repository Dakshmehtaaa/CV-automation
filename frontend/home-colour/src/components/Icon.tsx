import { useLayoutEffect, useRef } from 'react';
import {icon as studioIcon} from '../../../../static/ui.js';
const names:Record<string,string>={'send':'upRight','message-circle':'mail','circle-minus':'close','arrow-up-right':'upRight','clock-3':'clock','grip-vertical':'menu','pause':'stop','sparkles':'sun','layers-2':'grid'};
export function Icon({name,className=''}:{name:string;className?:string}){
 const ref=useRef<HTMLSpanElement>(null);
 useLayoutEffect(()=>{if(ref.current)ref.current.innerHTML=studioIcon(names[name]||name);},[name]);
 return <span ref={ref} className={`cd-icon ${className}`} aria-hidden="true"/>;
}
