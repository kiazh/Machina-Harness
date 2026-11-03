import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {ArrowDownLeft,ArrowUpRight,RefreshCw,Search,X} from 'lucide-react';
import type {DesktopController} from '../../state/controller.ts';
import {Select} from '../../components/Select.tsx';
import './harness-communications.css';

export interface HarnessEvent {
 id:string;correlationId:string;direction:'incoming'|'outgoing';phase:'request'|'response';
 transport:'mcp'|'cli';method:string;harness:string|null;sessionId:string|null;
 status:'running'|'completed'|'failed'|'cancelled'|'waiting_approval';summary:string;payload:string;
 truncated:boolean;createdAt:string;elapsedMs:number|null;
}
interface Traffic {events:HarnessEvent[];nextCursor:string;hasMore:boolean;retention:{maxEvents:number;maxBytes:number;payloadMaxChars:number}}
export function mergeHarnessEvents(previous:HarnessEvent[],incoming:HarnessEvent[]):HarnessEvent[]{
 const byId=new Map(previous.map(event=>[event.id,event]));
 for(const event of incoming)byId.set(event.id,event);
 return [...byId.values()].sort((a,b)=>Number(a.id)-Number(b.id)).slice(-200);
}
const statusLabel=(status:string)=>status==='waiting_approval'?'Waiting for approval':status[0]!.toUpperCase()+status.slice(1);

export function HarnessCommunications({controller}:{controller:DesktopController}){
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);
 const [events,setEvents]=useState<HarnessEvent[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const [query,setQuery]=useState(''),[source,setSource]=useState('all'),[status,setStatus]=useState('all'),[selected,setSelected]=useState<string>();
 const [paused,setPaused]=useState(false),[visible,setVisible]=useState(!document.hidden),[revision,setRevision]=useState(0);
 const cursor=useRef(''),initialized=useRef(false),details=useRef<HTMLElement>(null);
 const supported=!state.methods?.size||state.methods.has('interop.communications');
 useEffect(()=>{const change=()=>setVisible(!document.hidden);document.addEventListener('visibilitychange',change);return()=>document.removeEventListener('visibilitychange',change)},[]);
 useEffect(()=>{cursor.current='';initialized.current=false;setEvents([]);setSelected(undefined)},[state.connection]);
 useEffect(()=>{
  if(state.connection!=='ready'||!supported||!visible||paused){setLoading(false);return}
  let active=true;let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   if(!initialized.current)setLoading(true);
   try{
    const result=await controller.bridge.request<Traffic>('interop.communications',{limit:100,...(cursor.current?{cursor:cursor.current}:{})});
    if(!active)return;
    cursor.current=result.nextCursor;initialized.current=true;setError('');
    if(result.events.length)setEvents(previous=>mergeHarnessEvents(previous,result.events));
    timer=setTimeout(()=>void poll(),result.hasMore?100:2000);
   }catch(reason){if(active){setError(reason instanceof Error?reason.message:String(reason));timer=setTimeout(()=>void poll(),5000)}}
   finally{if(active)setLoading(false)}
  }
  void poll();return()=>{active=false;clearTimeout(timer)};
 },[controller,state.connection,supported,visible,paused,revision]);
 useEffect(()=>{if(selected)details.current?.focus()},[selected]);
 const sources=[...new Set(events.map(event=>event.harness||'Unidentified client'))].sort();
 const settlements=new Map(events.filter(event=>event.phase==='response').map(event=>[event.correlationId,event.status]));
 const currentStatus=(event:HarnessEvent)=>settlements.get(event.correlationId)??event.status;
 const filtered=events.filter(event=>(source==='all'||(event.harness||'Unidentified client')===source)&&(status==='all'||currentStatus(event)===status)&&`${event.method} ${event.summary} ${event.harness??''}`.toLowerCase().includes(query.trim().toLowerCase()));
 const chosen=events.find(event=>event.id===selected),related=chosen?events.filter(event=>event.correlationId===chosen.correlationId):[];
 return <section className="harness-communications" aria-label="Harness communications">
  <header><div><h2>Communications</h2><p>Actual exchanges through Machina’s MCP server and external CLI runs.</p></div><div className="management-actions"><button disabled={!supported||state.connection!=='ready'} onClick={()=>setPaused(value=>!value)}>{paused?'Resume updates':'Pause updates'}</button><button aria-label="Refresh communications" disabled={loading||!visible||state.connection!=='ready'||!supported} onClick={()=>{setPaused(false);setRevision(value=>value+1)}}><RefreshCw size={14}/></button></div></header>
  <div className="traffic-filter"><label className="traffic-search"><Search size={14}/><input aria-label="Search communications" value={query} placeholder="Search methods, clients or summaries" onChange={event=>setQuery(event.target.value)}/>{query&&<button aria-label="Clear communications search" onClick={()=>setQuery('')}><X size={12}/></button>}</label><Select aria-label="Communication source" value={source} onChange={event=>setSource(event.target.value)}><option value="all">All clients</option>{sources.map(name=><option key={name}>{name}</option>)}</Select><Select aria-label="Communication status" value={status} onChange={event=>setStatus(event.target.value)}><option value="all">All states</option>{['running','completed','failed','cancelled','waiting_approval'].map(value=><option key={value} value={value}>{statusLabel(value)}</option>)}</Select></div>
  {state.connection!=='ready'?<p role="status">Connect the local engine to inspect communications.</p>:!supported?<p role="status">Update the local engine to inspect communications.</p>:<>
   <div className="traffic-meta"><span>{paused?'Updates paused':!visible?'Updates paused while hidden':error?'Updates unavailable':loading?'Loading exchanges…':'Updating every 2 seconds'}</span><span>{events.length} recent events · up to 200 displayed</span></div>
   {error&&<p role="alert" className="inline-error">{error}{events.length?' · Showing previously received events.':''}</p>}
   {!loading&&!error&&!events.length&&<div className="catalog-empty"><h3>No communications recorded yet</h3><p>Connect another harness to Machina or run an external CLI task from Integrations. Exchanges appear here as they happen.</p></div>}
   {!!events.length&&!filtered.length&&<p className="catalog-empty">No exchanges match these filters.<button onClick={()=>{setQuery('');setSource('all');setStatus('all')}}>Clear filters</button></p>}
   <div className="traffic-list" aria-label="Recent harness events">{[...filtered].reverse().map(event=><button className="traffic-event" key={event.id} aria-pressed={selected===event.id} onClick={()=>setSelected(event.id)}><span className="traffic-direction">{event.direction==='incoming'?<ArrowDownLeft size={15}/>:<ArrowUpRight size={15}/>}<span>{event.direction==='incoming'?'To Machina':'From Machina'}</span></span><span className="traffic-event-copy"><strong>{event.method}</strong><small>{event.harness||'Unidentified client'} · {event.transport.toUpperCase()} · {event.phase}</small><span>{event.summary}</span></span><span className="traffic-event-state" data-state={currentStatus(event)}>{statusLabel(currentStatus(event))}<small><time dateTime={event.createdAt}>{new Date(event.createdAt).toLocaleTimeString()}</time>{event.elapsedMs!==null?` · ${event.elapsedMs} ms`:''}</small></span></button>)}</div>
   {chosen&&<section className="traffic-detail" ref={details} tabIndex={-1} aria-label="Exchange details"><header><div><h3>{chosen.method}</h3><small>Correlation {chosen.correlationId}</small></div><button onClick={()=>setSelected(undefined)}>Close details</button></header>{related.map(event=><div key={event.id}><h4>{event.direction==='incoming'?'To Machina':'From Machina'} · {event.phase} · {statusLabel(currentStatus(event))}</h4><pre>{event.payload}</pre>{event.truncated&&<p>Payload shortened to the local recording limit.</p>}</div>)}</section>}
   <p className="traffic-footnote">Stored locally with credential redaction, bounded to 1,000 events and 4 MiB of payload. Older events expire. Unidentified MCP clients keep their actual unknown identity.</p>
  </>}
 </section>;
}
