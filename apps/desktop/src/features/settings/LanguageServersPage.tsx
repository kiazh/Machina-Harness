import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import type {WorkspaceLspStatusResult,WorkspaceIntelligenceResult} from '@machina/contracts/workspace';
import {isWorkspaceRefusal,type WorkspaceIntelligenceOperation} from '@machina/contracts/workspace-gateway';
import type {DesktopController} from '../../state/controller.ts';
import {workspaceContains} from '../../state/workspacePreference.ts';
import {FeatureFrame} from '../shared/FeatureFrame.tsx';
import {LspInspection} from './LspInspection.tsx';
import type {InspectionLocation} from './lspResults.ts';
import '../shared/management.css';

interface Scope {key:string}
interface Scoped<T> {scope:Scope;value:T}
const errorMessage=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
const operations=[['diagnostics','Problems'],['symbols','File symbols'],['hover','Symbol information'],['definition','Definition'],['references','References']] as const;

export function LanguageServersPage({controller}:{controller:DesktopController}) {
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);
 const document=state.documents.find(value=>value.id===state.activeId);
 const supported=(method:string)=>!state.methods?.size||state.methods.has(method);
 const unavailableReason=state.connection!=='ready'?(state.connection==='connecting'?'Connecting the local engine…':'Connect the local engine to use language servers.'):state.switchingWorkspace||state.switchingSession?'Wait for the conversation or workspace to finish opening.':!state.workspace?'Open a workspace to use language servers.':!state.workspace.trusted?'Use Open workspace to review trust for this folder before starting language servers.':'';
 const statusReason=unavailableReason||(!supported('workspace.lspStatus')?'This engine version does not support language server status. Update the local engine.':'');
 const inspectionReason=unavailableReason||(!supported('workspace.intelligence')?'This engine version does not support code inspection. Update the local engine.':!document?.uri?'Open a saved project file in the editor to inspect code.':!workspaceContains(state.workspace!.resolvedRoot,document.uri)?'The active file is outside the current workspace. Open a file from this workspace.':'');
 const workspaceKey=JSON.stringify([state.workspace?.workspaceId,state.workspace?.epoch,state.workspace?.resolvedRoot,statusReason,supported('workspace.lspControl'),supported('workspace.intelligence')]);
 const workspaceScope=useRef<Scope>({key:workspaceKey});if(workspaceScope.current.key!==workspaceKey)workspaceScope.current={key:workspaceKey};
 const fileKey=JSON.stringify([workspaceKey,state.activeId,document?.uri,document?.revision,document?.reloadToken??0,inspectionReason]);
 const fileScope=useRef<Scope>({key:fileKey});if(fileScope.current.key!==fileKey)fileScope.current={key:fileKey};
 const currentWorkspace=workspaceScope.current,currentFile=fileScope.current;
 const [data,setData]=useState<Scoped<WorkspaceLspStatusResult>>(),[result,setResult]=useState<Scoped<WorkspaceIntelligenceResult>>(),[error,setError]=useState<Scoped<string>>(),[loading,setLoading]=useState<Scope>(),[working,setWorking]=useState<Scope>(),[inspecting,setInspecting]=useState<Scope>(),[revision,setRevision]=useState(0);
 const controlRequest=useRef<object|undefined>(undefined),inspectionRequest=useRef<object|undefined>(undefined);
 const alive=useRef(false);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 useEffect(()=>{
  let active=true;setData(undefined);setError(undefined);
  if(statusReason){setLoading(undefined);return;}
  const scope=currentWorkspace;setLoading(scope);
  void controller.workspace.lspStatus().then(value=>{if(!active||workspaceScope.current!==scope)return;if(isWorkspaceRefusal(value))throw new Error(value.refused.message);setData({scope,value});}).catch(reason=>{if(active&&workspaceScope.current===scope)setError({scope,value:errorMessage(reason)});}).finally(()=>{if(active&&workspaceScope.current===scope)setLoading(undefined);});
  return()=>{active=false};
 },[controller,currentWorkspace,revision,statusReason]);
 const busy=loading===currentWorkspace||working===currentWorkspace||inspecting===currentFile;
 const servers=data?.scope===currentWorkspace?data.value:undefined;
 const inspection=result?.scope===currentFile?result.value:undefined;
 const visibleError=error&&(error.scope===currentWorkspace||error.scope===currentFile)?error.value:'';
 async function control(profile:string,action:'start'|'stop'|'restart'){
  if(unavailableReason||!supported('workspace.lspControl')||busy)return;
  const scope=currentWorkspace,request={};controlRequest.current=request;setWorking(scope);setError(undefined);setResult(undefined);
  try{const response=await controller.workspace.lspControl(profile,action);if(!alive.current||workspaceScope.current!==scope||controlRequest.current!==request)return;if(isWorkspaceRefusal(response))throw new Error(response.refused.message);setRevision(value=>value+1);}
  catch(reason){if(alive.current&&workspaceScope.current===scope&&controlRequest.current===request)setError({scope,value:errorMessage(reason)});}
  finally{if(alive.current&&workspaceScope.current===scope&&controlRequest.current===request)setWorking(undefined);}
 }
 async function inspect(operation:WorkspaceIntelligenceOperation){
  if(inspectionReason||busy)return;
  const scope=currentFile,request={};inspectionRequest.current=request;setInspecting(scope);setError(undefined);setResult(undefined);
  try{const response=await controller.inspectDocument(operation);if(!alive.current||fileScope.current!==scope||inspectionRequest.current!==request)return;
   if(response.workspaceId!==state.workspace?.workspaceId||response.workspaceEpoch!==state.workspace.epoch||response.uri!==document?.uri)throw new Error('The file or workspace changed during inspection. Request fresh results.');
   setResult({scope,value:response});
  }catch(reason){if(alive.current&&fileScope.current===scope&&inspectionRequest.current===request)setError({scope,value:errorMessage(reason)});}
  finally{if(alive.current&&fileScope.current===scope&&inspectionRequest.current===request)setInspecting(undefined);}
 }
 async function open(location:InspectionLocation){
  if(unavailableReason||busy||!inspection||inspection.freshness==='stale'||inspection.freshness==='pending'||inspection.freshness==='unavailable')return;
  const scope=currentFile;setError(undefined);
  try{if(!controller.isInspectionCurrent(inspection))throw new Error('The source file changed after inspection. Request fresh results before opening a location.');await controller.openLocation(location.uri,location.position.line,location.position.character,inspection);}
  catch(reason){if(alive.current&&fileScope.current===scope)setError({scope,value:errorMessage(reason)});}
 }
 return <FeatureFrame controller={controller} title="Language servers" description="Inspect the active file using its current editor buffer and retained cursor position." busy={busy} error={visibleError} unavailableReason={statusReason} onRefresh={!statusReason?()=>setRevision(value=>value+1):undefined}>
  <div className="management-list">{servers?.servers.map(server=><article key={server.profile}><div className="management-row-heading"><h2>{server.languageId||server.profile}</h2><small>{server.state}</small></div><p className="lsp-server-meta">{server.profile} · Generation {server.generation}</p>{server.detail&&<p>{server.detail}</p>}<div className="management-actions">{(['start','restart','stop'] as const).map(action=><button key={action} disabled={busy||!!unavailableReason||!supported('workspace.lspControl')} onClick={()=>{void control(server.profile,action);}}>{action}</button>)}</div></article>)}</div>
  {servers&&!servers.servers.length&&<p>No language-server profiles were reported. Open a supported file and check the engine’s configured language-server executables.</p>}
  {!unavailableReason&&!supported('workspace.lspControl')&&<p className="feature-description">This engine version does not support language server controls.</p>}
  <h2>Inspect active code</h2><p className="feature-description">{document?.name??'No open file'}. Symbol information, definitions and references use the cursor position retained from the editor.</p>
  {inspectionReason&&inspectionReason!==statusReason&&<p className="feature-description">{inspectionReason}</p>}
  <div className="management-actions">{operations.map(([operation,label])=><button disabled={busy||!!inspectionReason} key={operation} onClick={()=>{void inspect(operation);}}>{label}</button>)}</div>
  {inspection&&<LspInspection result={inspection} root={state.workspace!.resolvedRoot} disabled={busy||!!unavailableReason} onOpen={location=>{void open(location);}}/>}
  <p className="feature-description">Tab accepts a visible completion in the editor. Change the suggestion shortcut in Keyboard shortcuts.</p>
 </FeatureFrame>;
}
