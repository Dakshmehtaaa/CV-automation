import {createRoot, type Root} from 'react-dom/client';
import {App} from './App';
import type {Model} from './types';

let root:Root|null=null;
let element:HTMLElement|null=null;
export function mountColourHome(target:HTMLElement,model:Model,onPeriod:(id:string)=>void){
 if(element!==target){unmountColourHome();element=target;root=createRoot(target);}
 root!.render(<App model={model} onPeriod={onPeriod}/>);
}
export function unmountColourHome(){root?.unmount();root=null;element=null;}
export function savedColourHomePeriod(){return 'today';}
