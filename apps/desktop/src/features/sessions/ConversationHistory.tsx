import {useCallback,useEffect,useMemo,useRef,useState,useSyncExternalStore,type RefObject} from 'react';
import {useVirtualizer} from '@tanstack/react-virtual';
import {X} from 'lucide-react';
import type {ConversationHistoryResult} from '@machina/contracts/rpc';
import type {DesktopController,DesktopSnapshot} from '../../state/controller.ts';
import './conversation-history.css';

interface Props {
 controller:DesktopController;
 dialog:RefObject<HTMLDialogElement|null>;
 openToken?:number;
 initialQuery?:string;
}
interface Scope {identity:string;revision:number}
interface Navigation {query:string;cursors:(string|null)[];page:number;revision:number}
const identity=(state:DesktopSnapshot)=>JSON.stringify([state.session?.id,state.workspace?.workspaceId,!!state.switchingSession,!!state.switchingWorkspace,state.connection]);

export function ConversationHistory({controller,dialog,openToken=0,initialQuery=''}:Props){
 const scope=useRef<Scope>({identity:identity(controller.snapshot()),revision:0});
 // Invalidate synchronously on every scope transition, including batched A → B → A.
 const subscribe=useCallback((listener:()=>void)=>controller.subscribe(()=>{
  const next=identity(controller.snapshot());
  if(next!==scope.current.identity)scope.current={identity:next,revision:scope.current.revision+1};
  listener();
 }),[controller]);
 const state=useSyncExternalStore(subscribe,controller.snapshot);
 const consumed=useRef(0);
 const [opened,setOpened]=useState(0);
 useEffect(()=>{
  if(openToken<=0||openToken===consumed.current||!dialog.current)return;
  consumed.current=openToken;
  if(!dialog.current.open)dialog.current.showModal();
  setOpened(openToken);
 },[openToken,dialog]);
 const closed=()=>{scope.current.revision++;setOpened(0)};
 return <dialog ref={dialog} className="settings-dialog conversation-history" aria-label="Conversation history" onClose={closed} onCancel={closed}>
  <div className="dialog-heading"><div><h2>Conversation history</h2><p className="history-conversation-title">{state.session?.title||'No conversation open'}</p></div><button className="icon-button" aria-label="Close conversation history" onClick={()=>dialog.current?.close()}><X size={16}/></button></div>
  {opened>0&&<HistoryPage key={`${opened}:${scope.current.revision}`} controller={controller} dialog={dialog} state={state} scope={scope} revision={scope.current.revision} initialQuery={initialQuery.slice(0,256)}/>}
 </dialog>;
}

function HistoryPage({controller,dialog,state,scope,revision,initialQuery}:{controller:DesktopController;dialog:Props['dialog'];state:DesktopSnapshot;scope:RefObject<Scope>;revision:number;initialQuery:string}){
 const [draft,setDraft]=useState(initialQuery);
 const [navigation,setNavigation]=useState<Navigation>({query:initialQuery,cursors:[null],page:0,revision:0});
 const [unsearchedPages,setUnsearchedPages]=useState<Map<string|null,number>>(()=>new Map());
 const [data,setData]=useState<ConversationHistoryResult>();
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const scroll=useRef<HTMLDivElement>(null);
 const sessionId=state.session?.id;
 const unavailable=!sessionId?'Open a conversation to browse its recorded history.':state.switchingSession||state.switchingWorkspace?'The conversation is opening. History will be available when it is ready.':state.connection!=='ready'?'Connect the local engine to read conversation history.':state.methods?.size&&!state.methods.has('session.history')?'This engine does not support conversation history. Update the local engine to browse recorded messages.':'';
 useEffect(()=>{
  if(unavailable||!sessionId||!dialog.current?.open)return;
  let stale=false;
  const current=()=>!stale&&scope.current.revision===revision&&!!dialog.current?.open;
  setLoading(true);setData(undefined);setError('');
  if(scroll.current)scroll.current.scrollTop=0;
  const cursor=navigation.cursors[navigation.page];
  void controller.bridge.request<ConversationHistoryResult>('session.history',{
   sessionId,limit:100,...(cursor?{cursor}:{}),...(navigation.query?{query:navigation.query}:{}),
  }).then(result=>{if(current()){
   setData(result);
   setUnsearchedPages(previous=>new Map(previous).set(cursor??null,result.unsearchedCount??0));
  }}).catch(reason=>{if(current())setError(reason instanceof Error?reason.message:String(reason))}).finally(()=>{if(current())setLoading(false)});
  return()=>{stale=true};
 },[controller,dialog,sessionId,unavailable,navigation,scope,revision]);
 const entries=useMemo(()=>data?.entries.slice(0,100).reverse()??[],[data]);
 const unsearchedCount=useMemo(()=>[...unsearchedPages.values()].reduce((total,count)=>total+count,0),[unsearchedPages]);
 const emptyMessage=unsearchedCount>0?'No matches in the searchable previews on this page. Some messages need full export.':data?.nextCursor?(navigation.query?'No matching messages in this page. Choose Older to continue searching.':'No recorded messages in this page. Choose Older to continue.'):navigation.page>0?(navigation.query?'No matching messages in this page.':'No recorded messages in this page.'):navigation.query?'No recorded messages match this search.':'No messages recorded in this conversation.';
 const virtualizer=useVirtualizer({count:entries.length,getScrollElement:()=>scroll.current,estimateSize:()=>160,overscan:4,getItemKey:index=>`${sessionId}:${entries[index]!.id}`,useAnimationFrameWithResizeObserver:true});
 const refresh=()=>setNavigation(previous=>({...previous,revision:previous.revision+1}));
 const startSearch=(query:string)=>{setUnsearchedPages(new Map());setNavigation(previous=>({query,cursors:[null],page:0,revision:previous.revision+1}))};
 return <>
  <form className="history-search" role="search" onSubmit={event=>{event.preventDefault();if(!unavailable)startSearch(draft)}}>
   <label htmlFor="conversation-history-search">Search recorded messages</label>
   <div><input id="conversation-history-search" type="search" aria-label="Search conversation history" placeholder="Search this conversation" maxLength={256} value={draft} disabled={!!unavailable} onChange={event=>setDraft(event.target.value.slice(0,256))}/><button type="submit" disabled={!!unavailable}>Search</button></div>
  </form>
  <div className="history-page-toolbar"><p>{data?`${entries.length} message${entries.length===1?'':'s'}${navigation.query?' matching your search':''} · Page ${navigation.page+1}`:'Recorded messages'}</p><nav aria-label="History pages"><button disabled={!!unavailable||loading||!data?.nextCursor} onClick={()=>{if(data?.nextCursor)setNavigation(previous=>({...previous,cursors:[...previous.cursors.slice(0,previous.page+1),data.nextCursor],page:previous.page+1}))}}>Older</button><button disabled={!!unavailable||loading||navigation.page===0} onClick={()=>setNavigation(previous=>({...previous,page:previous.page-1}))}>Newer</button><button disabled={!!unavailable||loading} onClick={()=>startSearch(navigation.query)}>Latest</button></nav></div>
  {unsearchedCount>0&&<p className="history-search-notice" role="note">{unsearchedCount} {unsearchedCount===1?'message could':'messages could'} not be searched in full across the pages visited in this search. Export the full conversation to inspect {unsearchedCount===1?'it':'them'}.</p>}
  {unavailable?<p className="history-state" role="status">{unavailable}</p>:<>
   {loading&&<p className="history-state" role="status">Loading history…</p>}
   {error&&<div className="history-state"><p className="inline-error" role="alert">{error}</p><button onClick={refresh}>Retry</button></div>}
   {!loading&&!error&&data&&entries.length===0&&<p className="history-state" role="status">{emptyMessage}</p>}
   <div className="history-entries" ref={scroll} role="region" aria-label="Recorded conversation messages" aria-busy={loading} tabIndex={0} hidden={loading||!!error||!entries.length}>
    <div style={{height:`${virtualizer.getTotalSize()}px`,position:'relative',width:'100%'}}>
     {virtualizer.getVirtualItems().map(item=>{const entry=entries[item.index]!;const preview=messagePreview(entry.content);return <div key={item.key} data-index={item.index} ref={virtualizer.measureElement} style={{position:'absolute',top:0,left:0,width:'100%',transform:`translateY(${item.start}px)`}}><article className="history-entry"><div className="history-entry-metadata"><strong>{entry.role==='user'?'You':entry.role==='assistant'?'Machina':entry.role==='tool'?'Tool':'System'}</strong>{entry.tool&&<span>{entry.tool}</span>}{entry.createdAt&&<time dateTime={entry.createdAt}>{displayTime(entry.createdAt)}</time>}</div><pre>{preview.text||'(No text content)'}</pre>{(entry.truncated||preview.truncated)&&<p className="history-truncated">Preview truncated. Export the full conversation for complete text.</p>}</article></div>})}
    </div>
   </div>
  </>}
  <footer className="history-footer"><p>Up to 100 messages per page, shown oldest first. Long messages show previews.</p><button disabled={!sessionId} onClick={()=>{dialog.current?.close();controller.requestUI('sessionTools','export')}}>Export full conversation</button></footer>
 </>;
}

function messagePreview(content:string):{text:string;truncated:boolean}{
 const bounded=content.slice(0,4000);
 const text=bounded.split('\n',31).slice(0,30).join('\n');
 return {text,truncated:text.length<content.length};
}
function displayTime(value:string):string{
 const date=new Date(value);
 return Number.isNaN(date.getTime())?'Time unavailable':date.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});
}
