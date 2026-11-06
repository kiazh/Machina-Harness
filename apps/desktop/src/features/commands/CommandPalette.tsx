import {useEffect,useId,useMemo,useRef,useState,type KeyboardEvent,type RefObject} from 'react';
import {Search,X} from 'lucide-react';
import './command-palette.css';

export interface PaletteCommand {
 id:string;
 label:string;
 group:string;
 description?:string;
 shortcut?:string|null;
 disabledReason?:string;
 searchTerms?:readonly string[];
}
interface CommandPaletteProps {
 dialog:RefObject<HTMLDialogElement|null>;
 commands:PaletteCommand[];
 onRun:(id:string)=>void;
}

const RESULT_LIMIT=20;
function normalize(text:string){return text.normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().trim().replace(/\s+/g,' ')}
function rank(command:PaletteCommand,query:string){
 const label=normalize(command.label);
 if(label===query)return 0;
 if(label.startsWith(query))return 1;
 if(label.includes(query))return 2;
 const aliases=(command.searchTerms??[]).map(normalize);
 if(aliases.some(alias=>alias===query||alias.startsWith(query)))return 3;
 const words=query.split(' ');
 if(words.every(word=>label.includes(word)))return 4;
 const searchable=normalize([command.label,command.group,command.description??'',...aliases].join(' '));
 return words.every(word=>searchable.includes(word))?5:undefined;
}

export function CommandPalette({dialog,commands,onRun}:CommandPaletteProps){
 const [query,setQuery]=useState(''),[selected,setSelected]=useState(0),[open,setOpen]=useState(false);
 const input=useRef<HTMLInputElement>(null),previousFocus=useRef<HTMLElement|null>(null),composing=useRef(false);
 const id=useId(),listId=`${id}-commands`,hintId=`${id}-hint`;
 const matches=useMemo(()=>{
  const search=normalize(query);
  if(!search)return commands;
  return commands.map((command,index)=>({command,index,rank:rank(command,search)}))
   .filter((entry):entry is {command:PaletteCommand;index:number;rank:number}=>entry.rank!==undefined)
   .sort((left,right)=>left.rank-right.rank||left.index-right.index).map(entry=>entry.command);
 },[commands,query]);
 const visible=useMemo(()=>matches.slice(0,RESULT_LIMIT),[matches]);
 const activeIndex=visible.length?Math.min(selected,visible.length-1):-1;
 const optionId=(index:number)=>`${id}-command-${index}`;
 const groups:{name:string;start:number;entries:PaletteCommand[]}[]=[];
 visible.forEach((command,index)=>{
  const last=groups.at(-1);
  if(last?.name===command.group)last.entries.push(command);
  else groups.push({name:command.group,start:index,entries:[command]});
 });

 useEffect(()=>{
  const node=dialog.current;if(!node)return;
  // Remember outside focus before showModal moves it into the native dialog.
  // Observing `open` alone is too late to capture that element.
  const rememberFocus=(event?:FocusEvent)=>{
   const target=event?.target??document.activeElement;
   if(target instanceof HTMLElement&&!node.contains(target))previousFocus.current=target;
  };
  rememberFocus();document.addEventListener('focusin',rememberFocus,true);
  const opened=()=>{
   setOpen(node.open);
   if(node.open){setQuery('');setSelected(0);composing.current=false;input.current?.focus({preventScroll:true})}
  };
  const observer=new MutationObserver(opened);
  observer.observe(node,{attributes:true,attributeFilter:['open']});
  if(node.open)opened();
  return()=>{observer.disconnect();document.removeEventListener('focusin',rememberFocus,true)};
 },[dialog]);
 useEffect(()=>{
  if(open&&activeIndex>=0)document.getElementById(optionId(activeIndex))?.scrollIntoView?.({block:'nearest'});
 },[open,activeIndex,visible,id]);

 function clear(){setQuery('');setSelected(0);input.current?.focus({preventScroll:true})}
 function close(){
  const node=dialog.current;if(!node?.open)return;
  // Native `close` events can arrive after the action opens another dialog.
  // Restore now, and never move focus from an onClose handler.
  node.close();setOpen(false);setQuery('');setSelected(0);composing.current=false;
  if(previousFocus.current?.isConnected)previousFocus.current.focus({preventScroll:true});
 }
 function run(command:PaletteCommand|undefined){
  if(!command||command.disabledReason||!dialog.current?.open)return;
  close();queueMicrotask(()=>onRun(command.id));
 }
 function searchKeys(event:KeyboardEvent<HTMLInputElement>){
  if(composing.current||event.nativeEvent.isComposing||event.keyCode===229||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return;
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
   event.preventDefault();event.stopPropagation();
   if(!visible.length)return;
   if(event.key==='Home')setSelected(0);
   else if(event.key==='End')setSelected(visible.length-1);
   else setSelected((activeIndex+(event.key==='ArrowDown'?1:-1)+visible.length)%visible.length);
  }else if(event.key==='Enter'){
   event.preventDefault();event.stopPropagation();run(visible[activeIndex]);
  }
 }
 const status=!matches.length?'No commands match. Try a different search.':matches.length>RESULT_LIMIT?`Showing ${visible.length} of ${matches.length} commands. Search to narrow the results.`:`${matches.length} command${matches.length===1?'':'s'}`;

 return <dialog ref={dialog} className="command-palette" aria-label="Commands"
  onKeyDown={event=>{
   if(event.key!=='Escape')return;
   event.stopPropagation();
   if(composing.current||event.nativeEvent.isComposing||event.keyCode===229)return;
   event.preventDefault();if(query)clear();else close();
  }}
  onCancel={event=>{event.preventDefault();if(composing.current)return;if(query)clear();else close()}}>
  <div className="command-palette-search">
   <Search size={16} aria-hidden="true"/>
   <input ref={input} autoFocus role="combobox" aria-label="Search commands" aria-autocomplete="list"
    aria-expanded={open} aria-controls={listId} aria-describedby={hintId}
    aria-activedescendant={activeIndex>=0?optionId(activeIndex):undefined}
    placeholder="Search commands…" value={query} maxLength={200} spellCheck={false} autoComplete="off"
    onCompositionStart={()=>{composing.current=true}} onCompositionEnd={()=>{composing.current=false}}
    onChange={event=>{setQuery(event.target.value);setSelected(0)}} onKeyDown={searchKeys}/>
   {query&&<button type="button" className="command-palette-clear" aria-label="Clear command search" onClick={clear}>Clear</button>}
   <button type="button" className="icon-button" aria-label="Close commands" onClick={close}><X size={16}/></button>
  </div>
  <div role="listbox" id={listId} aria-label="Commands" className="command-palette-results">
   {groups.map(group=><div role="group" aria-labelledby={`${id}-group-${group.start}`} key={group.start}>
    <div className="command-palette-group" id={`${id}-group-${group.start}`}>{group.name}</div>
    {group.entries.map((command,offset)=>{
     const index=group.start+offset,entryId=optionId(index);
     const description=[command.description&&`${entryId}-description`,command.disabledReason&&`${entryId}-reason`].filter(Boolean).join(' ')||undefined;
     return <div role="option" id={entryId} key={command.id} aria-selected={activeIndex===index}
      aria-disabled={!!command.disabledReason} aria-labelledby={`${entryId}-label`} aria-describedby={description}
      className="command-palette-option" onMouseDown={event=>event.preventDefault()}
      onMouseEnter={()=>setSelected(index)} onClick={()=>{setSelected(index);run(command)}}>
      <div className="command-palette-label">
       <span id={`${entryId}-label`}>{command.label}</span>
       {command.description&&<small id={`${entryId}-description`}>{command.description}</small>}
       {command.disabledReason&&<small id={`${entryId}-reason`} className="command-palette-reason">{command.disabledReason}</small>}
      </div>
      {command.shortcut&&<kbd>{command.shortcut}</kbd>}
     </div>;
    })}
   </div>)}
  </div>
  <div role="status" className={`command-palette-status${!matches.length?' command-palette-empty':''}`}>{status}</div>
  <footer id={hintId}><span>↑ ↓ select <span aria-hidden="true">·</span> Enter run</span><span>Esc {query?'clear search':'close'}</span></footer>
 </dialog>;
}
