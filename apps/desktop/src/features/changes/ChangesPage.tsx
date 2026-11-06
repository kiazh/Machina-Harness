import {useCallback,useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {ArrowLeft,ChevronRight,FileCode2,GitBranch,PanelLeft,RefreshCw,Search} from 'lucide-react';
import type {WorkspaceChange,WorkspaceChangesResult,WorkspaceDiffResult} from '@machina/contracts/workspace';
import {isWorkspaceRefusal} from '@machina/contracts/workspace-gateway';
import type {DesktopController} from '../../state/controller.ts';
import {DiffView} from './DiffView.tsx';
import {diffAttachment} from './diff.ts';
import './changes.css';

const message=(error:unknown)=>error instanceof Error?error.message:String(error);
function changeLabel(entry:WorkspaceChange){return entry.untracked?'New file':entry.status.includes('U')||entry.status==='AA'?'Conflict':entry.status.includes('R')?'Renamed':entry.status.includes('D')?'Deleted':entry.status.includes('A')?'Added':'Modified'}
export function ChangesPage({controller}:{controller:DesktopController}){
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);
 const [inventory,setInventory]=useState<WorkspaceChangesResult>(),[diff,setDiff]=useState<WorkspaceDiffResult>();
 const [loading,setLoading]=useState(false),[reading,setReading]=useState(false),[error,setError]=useState(''),[diffError,setDiffError]=useState('');
 const [path,setPath]=useState(''),[target,setTarget]=useState<'working'|'staged'>('working'),[query,setQuery]=useState(''),[filesOpen,setFilesOpen]=useState(false),[revision,setRevision]=useState(0),[opening,setOpening]=useState(false);
 const selected=inventory?.entries.find(entry=>entry.path===path);
 const pathRef=useRef(path);pathRef.current=path;
 const fileFilter=useRef<HTMLInputElement>(null),fileToggle=useRef<HTMLButtonElement>(null),lastFilesOpen=useRef(false);
 const [filePage,setFilePage]=useState(0);
 useEffect(()=>{if(filesOpen)fileFilter.current?.focus({preventScroll:true});else if(lastFilesOpen.current)fileToggle.current?.focus({preventScroll:true});lastFilesOpen.current=filesOpen},[filesOpen]);
 useEffect(()=>setFilePage(0),[query]);
 const generation=useRef(0),diffGeneration=useRef(0);
 const workspace=state.workspace,identity=`${state.connection}:${workspace?.workspaceId??''}:${workspace?.epoch??''}:${workspace?.clientId??''}`;
 const ready=state.connection==='ready'&&!!workspace;
 const supported=!state.methods?.size||state.methods.has('workspace.changes')&&state.methods.has('workspace.diff');
 const refresh=useCallback(()=>setRevision(value=>value+1),[]);
 useEffect(()=>{setInventory(undefined);setDiff(undefined);setPath('');setQuery('');setFilesOpen(false);setError('');setDiffError('')},[identity]);
 useEffect(()=>{
  if(!ready||!supported){setLoading(false);return}
  const captured=controller.workspace.identity(),current=++generation.current;let active=true;
  setLoading(true);setError('');setDiff(undefined);diffGeneration.current++;
  void controller.workspace.changes().then(result=>{
   if(!active||generation.current!==current||controller.workspace.identity()!==captured)return;
   if(isWorkspaceRefusal(result))throw new Error(result.refused.message);
   if(result.workspaceId!==captured?.workspaceId||result.workspaceEpoch!==captured?.epoch)throw new Error('Workspace changed during inspection. Refresh to try again.');
   setInventory(result);
   const next=result.entries.find(entry=>entry.path===pathRef.current)??result.entries[0];
   setPath(next?.path??'');setTarget(previous=>next?.staged&&(!next.unstaged&&!next.untracked||previous==='staged')?'staged':'working');
  }).catch(reason=>{if(active&&generation.current===current){setError(message(reason));setInventory(undefined)}}).finally(()=>{if(active&&generation.current===current)setLoading(false)});
  return()=>{active=false};
 },[controller,identity,ready,supported,revision]);
 useEffect(()=>{
  if(loading||!ready||!supported||!selected||inventory?.state!=='ready'){setReading(false);return}
  let active=true;const current=++diffGeneration.current,captured=controller.workspace.identity();setReading(true);setDiff(undefined);setDiffError('');
  void controller.workspace.diff(path,target).then(result=>{
   if(!active||current!==diffGeneration.current||controller.workspace.identity()!==captured)return;
   if(isWorkspaceRefusal(result))throw new Error(result.refused.message);
   if(result.path!==path||result.target!==target||result.workspaceId!==captured?.workspaceId||result.workspaceEpoch!==captured?.epoch)throw new Error('The inspected file changed. Refresh to read this diff.');
   setDiff(result);
  }).catch(reason=>{if(active&&current===diffGeneration.current)setDiffError(message(reason))}).finally(()=>{if(active&&current===diffGeneration.current)setReading(false)});
  return()=>{active=false};
 },[controller,path,target,inventory,loading,ready,supported]);
 useEffect(()=>{
  let timer:ReturnType<typeof setTimeout>;
  const update=()=>{if(document.hidden)return;clearTimeout(timer);timer=setTimeout(refresh,250)};
  const unsubscribe=controller.bridge.subscribe(event=>{if(event.type==='message.complete'||event.type==='workspace.fileChanged')update()});
  window.addEventListener('focus',update);document.addEventListener('visibilitychange',update);
  return()=>{clearTimeout(timer);unsubscribe();window.removeEventListener('focus',update);document.removeEventListener('visibilitychange',update)};
 },[controller,refresh]);
 async function openFile(){
  if(!selected||opening)return;const captured=controller.workspace.identity();setOpening(true);setDiffError('');
  try{await controller.openFile(selected.path);if(controller.workspace.identity()===captured){controller.navigate('chat');controller.requestUI('editor')}}catch(reason){if(controller.workspace.identity()===captured)setDiffError(message(reason))}finally{setOpening(false)}
 }
 function attach(){if(!diff||diff.truncated)return;try{controller.requestDraftInsertion(diffAttachment(diff.path,diff.target,diff.patch));controller.navigate('chat')}catch(reason){setDiffError(message(reason))}}
 const matches=inventory?.entries.filter(entry=>entry.path.toLowerCase().includes(query.trim().toLowerCase()))??[];
 const pageCount=Math.max(1,Math.ceil(matches.length/100)),pageIndex=Math.min(filePage,pageCount-1),shownFiles=matches.slice(pageIndex*100,(pageIndex+1)*100);
 function fileKeys(event:React.KeyboardEvent<HTMLElement>){if(!filesOpen)return;if(event.key==='Escape'&&!event.nativeEvent.isComposing){event.preventDefault();event.stopPropagation();setFilesOpen(false)}else if(event.key==='Tab'){const items=[...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input')],index=items.indexOf(document.activeElement as HTMLElement);if(event.shiftKey&&index===0||!event.shiftKey&&index===items.length-1){event.preventDefault();setFilesOpen(false)}}}
 const attachUnavailable=!diff?.patch||diff.binary||diff.truncated||diff.patch.length>16000;
 return <section className="feature-page changes-page" aria-label="Workspace changes">
  <div className="feature-toolbar"><button onClick={()=>controller.navigate('chat')}><ArrowLeft size={13}/>Conversation</button><span className="feature-spacer"/>{inventory?.branch&&<span className="changes-branch"><GitBranch size={13}/>{inventory.branch}</span>}<button disabled={!ready||!supported||loading} onClick={refresh}><RefreshCw size={13}/>Refresh</button></div>
  <header className="changes-heading"><div><h1>Workspace changes</h1><p>Review existing changes in this folder, including edits made before the current task.</p></div>{!!inventory?.entries.length&&<span>{inventory.entries.length}{inventory.truncated?'+':''} changed {inventory.entries.length===1?'file':'files'}</span>}</header>
  {!!state.dirty.size&&<p className="changes-unsaved">{state.dirty.size} unsaved {state.dirty.size===1?'editor buffer is':'editor buffers are'} separate from these disk changes. Save a buffer to include it here.</p>}
  {!ready?<div className="changes-empty"><h2>Open a connected workspace</h2><p>Choose a folder to inspect its code changes.</p><button onClick={()=>controller.requestUI('workspace')}>Choose workspace</button></div>:!supported?<p className="changes-empty">Update the local engine to inspect workspace changes.</p>:error?<div className="changes-empty"><p role="alert">{error}</p><button onClick={refresh}>Retry inspection</button></div>:loading&&!inventory?<p className="changes-empty" role="status">Reading workspace changes…</p>:inventory?.state==='not_repository'?<div className="changes-empty"><h2>This folder is not in a Git repository.</h2><p>Files remain editable. Open a repository folder to review its changes.</p><button onClick={()=>controller.requestUI('workspace')}>Choose workspace</button></div>:inventory?.state==='unavailable'?<div className="changes-empty"><h2>Git inspection is unavailable.</h2><p>{inventory.reason||'Install Git and refresh this workspace.'}</p><button onClick={refresh}>Retry inspection</button></div>:inventory?.state==='ready'&&!inventory.entries.length&&inventory.truncated?<div className="changes-empty"><h2>Inspection was incomplete.</h2><p>Some files could not be inspected within the review limits. Refresh or choose a smaller workspace to check them.</p><button onClick={refresh}>Retry inspection</button></div>:inventory?.state==='ready'&&!inventory.entries.length?<div className="changes-empty"><GitBranch size={24}/><h2>Your working tree is clean.</h2><p>No staged, unstaged or new files were found in this workspace.</p></div>:inventory&&<div className="changes-body" data-show-files={filesOpen} aria-busy={loading}>
   <aside className="changes-files" aria-label="Changed files" onKeyDown={fileKeys}><button className="changes-close-files" aria-label="Close changed files" onClick={()=>setFilesOpen(false)}>Close files</button><label className="changes-search"><Search size={13}/><input ref={fileFilter} aria-label="Filter changed files" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Filter files…"/></label><div className="changes-file-list">{shownFiles.map(entry=><button key={entry.path} aria-label={`Inspect ${entry.path}`} aria-pressed={entry.path===path} disabled={loading} onClick={()=>{setPath(entry.path);setTarget(entry.unstaged||entry.untracked?'working':'staged');setFilesOpen(false)}}><FileCode2 size={14}/><span><strong title={entry.path}>{entry.path}</strong><small>{changeLabel(entry)}{entry.staged?' · staged':''}{entry.unstaged?' · on disk':''}</small>{entry.oldPath&&<small title={entry.oldPath}>from {entry.oldPath}</small>}</span><ChevronRight size={12}/></button>)}{!matches.length&&<p>No changed files match this search.<button onClick={()=>setQuery('')}>Clear filter</button></p>}</div>{pageCount>1&&<div className="changes-file-pages"><button disabled={pageIndex===0} onClick={()=>setFilePage(value=>value-1)}>Previous</button><span>{pageIndex+1} / {pageCount}</span><button disabled={pageIndex===pageCount-1} onClick={()=>setFilePage(value=>value+1)}>Next</button></div>}{inventory.truncated&&<p className="changes-truncated">Inspection is incomplete. Some files were outside the review limits.</p>}</aside>
   <section className="changes-inspection" aria-label="Selected change"><header className="change-heading"><button ref={fileToggle} className="changes-toggle-files" aria-label="Choose changed file" aria-expanded={filesOpen} onClick={()=>setFilesOpen(value=>!value)}><PanelLeft size={14}/>Files</button><div><h2 title={path}>{path}</h2><span>{target==='working'?'Index → disk':'HEAD → index'}</span></div>{selected?.exists&&<button disabled={opening||loading} onClick={()=>{void openFile()}}>{opening?'Opening…':'Open file'}</button>}</header><div className="change-versions" aria-label="Diff version"><button aria-pressed={target==='working'} disabled={loading||!selected?.unstaged&&!selected?.untracked} onClick={()=>setTarget('working')}>Working tree</button><button aria-pressed={target==='staged'} disabled={loading||!selected?.staged} onClick={()=>setTarget('staged')}>Staged</button><span/><button title={attachUnavailable?'Attach a complete text diff of at most 16,000 characters. Use plain text to copy a smaller selection for larger diffs.':'Add this diff to your draft without sending it'} disabled={attachUnavailable||reading||loading} onClick={attach}>Attach diff to draft</button></div>
    {reading||loading?<p className="diff-placeholder" role="status">Reading diff…</p>:diffError?<div className="diff-placeholder"><p role="alert">{diffError}</p><button onClick={refresh}>Retry diff</button></div>:diff?.binary?<p className="diff-placeholder">Binary file changed. A text diff is unavailable.</p>:diff&&!diff.patch?<p className="diff-placeholder">No textual differences for this version.</p>:diff&&<>{diff.truncated&&<p className="changes-truncated">This diff reached its size limit. Only the available excerpt is shown.</p>}<DiffView patch={diff.patch} path={diff.path}/></>}
   </section>
  </div>}
 </section>;
}
