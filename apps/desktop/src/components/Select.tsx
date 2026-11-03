import {Children,isValidElement,useRef,useState,type CSSProperties,type ReactNode} from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import {Check,ChevronDown,ChevronUp} from 'lucide-react';
import './select.css';

interface Option {value:string;label:ReactNode;disabled?:boolean}
interface Props {
 value?:string|number;defaultValue?:string|number;children:ReactNode;
 onChange?:(change:{target:{value:string}})=>void;
 disabled?:boolean;required?:boolean;name?:string;id?:string;title?:string;
 'aria-label'?:string;'aria-describedby'?:string;className?:string;style?:CSSProperties;
}
function options(children:ReactNode):Option[]{
 return Children.toArray(children).flatMap(child=>{
  if(!isValidElement<{value?:string|number;children?:ReactNode;disabled?:boolean}>(child))return [];
  if(child.type==='option')return [{value:String(child.props.value??child.props.children??''),label:child.props.children,disabled:child.props.disabled}];
  return options(child.props.children);
 });
}
const EMPTY='__machina_empty_option__';

/** Shared select behavior, with the popup inside the current native dialog layer. */
export function Select({value,defaultValue,onChange,children,disabled,required,name,...props}:Props){
 const items=options(children),trigger=useRef<HTMLButtonElement>(null);
 const [host,setHost]=useState<HTMLElement|null>(null);
 const encode=(value:string|number|undefined)=>value===undefined?undefined:String(value)===''?EMPTY:String(value);
 return <SelectPrimitive.Root value={encode(value)} defaultValue={encode(defaultValue)} disabled={disabled} required={required} name={name}
  onOpenChange={open=>{if(open)setHost(trigger.current?.closest('dialog')??trigger.current?.closest<HTMLElement>('.desktop-app')??document.body)}}
  onValueChange={value=>onChange?.({target:{value:value===EMPTY?'':value}})}>
  <SelectPrimitive.Trigger {...props} ref={trigger} className={`select-trigger ${props.className??''}`}><SelectPrimitive.Value/><SelectPrimitive.Icon asChild><ChevronDown size={14}/></SelectPrimitive.Icon></SelectPrimitive.Trigger>
  <SelectPrimitive.Portal container={host}>
   <SelectPrimitive.Content className="select-content" data-select-popup="true" position="popper" sideOffset={5} collisionPadding={12} collisionBoundary={host} onEscapeKeyDown={event=>event.stopPropagation()}>
    <SelectPrimitive.ScrollUpButton className="select-scroll"><ChevronUp size={13}/></SelectPrimitive.ScrollUpButton>
    <SelectPrimitive.Viewport className="select-viewport">{items.map(item=><SelectPrimitive.Item className="select-option" data-select-value={item.value} key={item.value} value={encode(item.value)!} disabled={item.disabled}>
     <SelectPrimitive.ItemText>{item.label}</SelectPrimitive.ItemText><SelectPrimitive.ItemIndicator className="select-check"><Check size={13}/></SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>)}</SelectPrimitive.Viewport>
    <SelectPrimitive.ScrollDownButton className="select-scroll"><ChevronDown size={13}/></SelectPrimitive.ScrollDownButton>
   </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
 </SelectPrimitive.Root>;
}
