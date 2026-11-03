import {useRef} from 'react';
import {X} from 'lucide-react';
import type {OpenDocument} from '../state/documents.ts';
export function DocumentTabs({documents,activeId,dirty,onSelect,onClose}:{documents:OpenDocument[];activeId:string;dirty:ReadonlySet<string>;onSelect:(id:string)=>void;onClose:(id:string)=>void}){
 const buttons=useRef(new Map<string,HTMLButtonElement>());
 return <div className="document-tabs" role="tablist" aria-label="Open files" onKeyDown={event=>{
  if((event.target as HTMLElement).getAttribute('role')!=='tab'||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();const index=documents.findIndex(d=>d.id===activeId);const next=event.key==='Home'?0:event.key==='End'?documents.length-1:(index+(event.key==='ArrowRight'?1:-1)+documents.length)%documents.length;
  const id=documents[next]?.id;if(id){onSelect(id);buttons.current.get(id)?.focus()}
 }}>{documents.map(document=><div className="document-tab" key={document.id}><button ref={node=>{if(node)buttons.current.set(document.id,node);else buttons.current.delete(document.id)}} role="tab" aria-selected={document.id===activeId} tabIndex={document.id===activeId?0:-1} className={document.id===activeId?'active':''} onClick={()=>onSelect(document.id)}><span>{document.name}</span>{dirty.has(document.id)&&<span className="dirty-dot" aria-label="Unsaved changes"/>}</button><button className="file-close" aria-label={`Close ${document.name}`} onClick={()=>onClose(document.id)}><X size={11}/></button></div>)}</div>;
}
