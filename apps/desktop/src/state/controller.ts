import type { ViewUpdate } from "@codemirror/view";
import { Text, type EditorState } from "@codemirror/state";
import { WorkspaceGatewayAdapter, isWorkspaceRefusal,type WorkspaceIntelligenceOperation } from "@machina/contracts/workspace-gateway";
import type { ConfigGetResult, HistoryEntry, InitializeResult, OperationSnapshot, SessionInfo, SessionListResult, SessionResult } from "@machina/contracts/rpc";
import type { WorkspaceIntelligenceResult, WorkspaceOpenResult, WorkspaceReadResult } from "@machina/contracts/workspace";
import type { DesktopBridge } from "../bridge/types.ts";
import { parseCapabilities } from "../bridge/capabilities.ts";
import { TranscriptStore } from "./transcript.ts";
import { DocumentSync, type OpenDocument } from "./documents.ts";
import { configureDocumentState } from '../features/editor/documentState.ts';
import {rememberWorkspace,relativeWorkspacePath,sameWorkspace,workspaceContains,workspacePreference} from './workspacePreference.ts';
import type { RecoveryStore } from "./recovery.ts";
import {MAX_IMAGE_TOTAL,selectionAttachment,type PromptImage} from '../features/harness/attachments.ts';
import { FEATURE_COMMANDS, parseDesktopCommand, type DesktopPage } from './commands.ts';
import {OperationStore,type TrackedOperation} from './operations.ts';

export interface QueueItem { id: string; text: string }
export interface Job { id: string; command: string; status: string; foreground?: boolean; output?: string; deadline?: number; returncode?: number }
export interface Approval { requestId: string; operation: string; target: string; argv?: string[]; cwd?: string }
export interface DesktopSnapshot {
  backgroundOperation?:TrackedOperation;
  pendingAttachments?:number;
  composerInsertion?:{id:number;text:string};
  editorNavigation?:{documentId:string;line:number;character:number;token:number};
  methods?:ReadonlySet<string>;
  switchingSession?:boolean;
  switchingWorkspace?:boolean;
  uiAction?:{id:number;name:'workspace'|'editor'|'quickOpen'|'terminal'|'sessionTools'|'history';query?:string};
  profile?:string;
  page: DesktopPage;
  connection: "offline" | "connecting" | "ready" | "failed";
  workspace?: WorkspaceOpenResult;
  model: string;
  provider: string;
  session?: SessionInfo;
  sessions: SessionInfo[];
  documents: OpenDocument[];
  activeId: string;
  editorFocus: number;
  dirty: ReadonlySet<string>;
  running: boolean;
  queue: QueueItem[];
  jobs: Job[];
  approvals: Approval[];
  agentIds: string[];
  hiddenAgentIds?:ReadonlySet<string>;
  focusedAgent?:string;
  agentFocus?:number;
  error: string;
}

export class DesktopController {
  readonly operations:OperationStore;
  private unsubscribeOperations:()=>void;
  readonly transcript = new TranscriptStore();
  readonly agents = new TranscriptStore({ maxRows: 32 });
  readonly workspace: WorkspaceGatewayAdapter;
  readonly documents: DocumentSync;
  private listeners = new Set<() => void>();
  private value: DesktopSnapshot = { page:'chat', connection: "offline", model: "", provider: "", sessions: [], documents: [{ id: "untitled:1", name: "Untitled.py", text: "" }], activeId: "untitled:1", editorFocus: 0, dirty: new Set(), running: false, queue: [], jobs: [], approvals: [], agentIds: [], error: "" };
  private connecting?: Promise<void>;
  private next = 1;
  private turnId?: string;
  private currentAssistant?: string;
  private displayed = "";
  private retired = new Set<string>();
  private unsubscribe: () => void;
  private editorStates = new Map<string, EditorState>();
  private sessionRevision=0;
  private fileReads=new Map<string,number>();
  private focusRevision=0;
  private inspections=new WeakMap<object,{workspace:WorkspaceOpenResult;id:string;doc:Text;version:number|null}>();
  private savingFiles=new Set<string>();

  constructor(readonly bridge: DesktopBridge, readonly recovery?: RecoveryStore) {
    this.operations=new OperationStore(bridge);
    this.unsubscribeOperations=this.operations.subscribe(()=>{this.set({backgroundOperation:this.operations.snapshot().active})});
    try{const profile=localStorage.getItem('machina.desktop.profile');if(profile&&/^[a-z0-9][a-z0-9-]{0,39}$/.test(profile))this.value={...this.value,profile}}catch{}
    this.workspace = new WorkspaceGatewayAdapter((method, params) => bridge.request(method, params));
    this.documents = new DocumentSync(this.workspace, error => this.fail(error));
    this.unsubscribe = bridge.subscribe(event => this.event(event));
  }
  snapshot = (): DesktopSnapshot => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private set(patch: Partial<DesktopSnapshot>): void { this.value = { ...this.value, ...patch }; for (const listener of this.listeners) listener(); }
  fail(error: unknown): void { this.set({ error: error instanceof Error ? error.message : String(error) }); }
  clearError(): void { this.set({ error: "" }); }
  private guardOperation():void{if(this.operations.snapshot().active)throw new Error('Wait for the background run to settle, or stop it first.');}
  async runOperation(method:OperationSnapshot['method'],params:Record<string,unknown>):Promise<TrackedOperation>{
    if(this.value.connection!=='ready')throw new Error('Connect the local engine first.');
    if(!this.value.methods?.has('operations.start'))throw new Error('Update the engine to run tracked background tasks.');
    if(this.value.running||this.value.switchingSession||this.value.switchingWorkspace)throw new Error('Wait for the active conversation work to finish first.');
    this.guardOperation();return this.operations.start(method,{...(this.value.session?{sessionId:this.value.session.id}:{}),...params});
  }
  setPendingAttachments(count:number):void{if(this.value.pendingAttachments!==count)this.set({pendingAttachments:count})}
  captureSelection():string{
    const document=this.value.documents.find(value=>value.id===this.value.activeId),state=this.editorStates.get(this.value.activeId);
    if(!document||!state)throw new Error('Open the code editor and select some text first.');
    const root=document.workspaceRoot??this.value.workspace?.resolvedRoot;
    const path=root&&document.uri&&workspaceContains(root,document.uri)?relativeWorkspacePath(root,document.uri):document.name;
    return selectionAttachment(document,state,this.value.dirty.has(document.id)||!document.uri,path);
  }
  requestSelectionAttachment():void{this.set({composerInsertion:{id:++this.next,text:this.captureSelection()}})}
  requestDraftInsertion(text:string):void{if(!text||text.length>20000)throw new Error('Attach at most 20,000 characters to the draft.');this.set({composerInsertion:{id:++this.next,text}})}
  consumeInsertion(id:number):void{if(this.value.composerInsertion?.id===id)this.set({composerInsertion:undefined})}

  navigate(page:DesktopPage):void { this.set({page}); }
  inspectAgent(id:string):void{if(!this.value.agentIds.includes(id))return;const hidden=new Set(this.value.hiddenAgentIds);hidden.delete(id);this.set({page:'chat',hiddenAgentIds:hidden,focusedAgent:id,agentFocus:(this.value.agentFocus??0)+1})}
  dismissAgent(id?:string):void{const hidden=new Set(this.value.hiddenAgentIds);for(const current of id?[id]:this.value.agentIds)hidden.add(current);this.set({hiddenAgentIds:hidden,focusedAgent:undefined,agentFocus:(this.value.agentFocus??0)+1})}
  requestUI(name:NonNullable<DesktopSnapshot['uiAction']>['name'],query?:string):void{this.set({uiAction:{id:++this.next,name,...(query?{query}:{})}})}
  consumeUI(id:number):void{if(this.value.uiAction?.id===id)this.set({uiAction:undefined})}
  setProfile(profile:string):void{if(profile&&!/^[a-z0-9][a-z0-9-]{0,39}$/.test(profile))throw new Error('Use lowercase letters, digits and dashes, up to 40 characters.');this.set({profile:profile||undefined});try{localStorage.setItem('machina.desktop.profile',profile)}catch{}}
  private async reconnectDocuments():Promise<void>{
    const workspace=this.workspace.identity();if(!workspace)return;
    this.documents.reset();
    for(const document of this.value.documents){
      if(!document.uri||!workspaceContains(workspace.resolvedRoot,document.uri))continue;
      const read=await this.workspace.read(document.uri);
      if(this.workspace.identity()!==workspace)return;
      if(isWorkspaceRefusal(read)){this.fail(`Could not reconnect ${document.name}. Its local text is retained.`);continue;}
      const state=this.editorStates.get(document.id);
      await this.documents.open({...read,text:state?.sliceDoc()??document.text,revision:document.revision??read.revision},state);
      if(this.workspace.identity()!==workspace)return;
      this.set({documents:this.value.documents.map(current=>current.id===document.id?{...current,workspaceRoot:workspace.resolvedRoot,languageId:read.languageId,lineEnding:current.lineEnding??read.lineEnding,readOnly:read.readOnly,revision:current.revision??read.revision}:current)});
    }
  }
  private async selectWorkspace(root:string,trust:boolean,sessionId?:string):Promise<WorkspaceOpenResult>{
    const previous=this.workspace.identity();
    const workspace=await this.workspace.select(root,trust,sessionId);
    rememberWorkspace(workspace.resolvedRoot,workspace.trusted);
    const keep=this.value.dirty.size>0||!!previous&&sameWorkspace(previous.resolvedRoot,workspace.resolvedRoot);
    this.set({workspace});
    if(keep){if(!previous||previous.workspaceId!==workspace.workspaceId||previous.epoch!==workspace.epoch)await this.reconnectDocuments();}
    else {this.documents.reset();this.editorStates.clear();const id=`untitled:${++this.next}`;this.set({documents:[{id,name:'Untitled.py',text:''}],activeId:id,dirty:new Set(),editorFocus:this.value.editorFocus+1});}
    return workspace;
  }
  async switchWorkspace(root:string,trust:boolean):Promise<void>{
    this.guardOperation();
    if(this.value.connection==='connecting')throw new Error('Wait for the local engine to finish connecting before choosing a workspace.');
    if(this.value.switchingSession||this.value.switchingWorkspace)throw new Error('Wait for the workspace or conversation to finish opening.');
    if(this.value.running||this.value.jobs.some(job=>job.status==='running'))throw new Error('Stop active agent work and commands before switching workspaces.');
    if(this.savingFiles.size)throw new Error('Wait for files to finish saving.');
    const restoring=this.value.documents.every(document=>!document.uri||workspaceContains(root,document.uri));
    if(this.value.dirty.size&&!restoring)throw new Error('Save or close unsaved code tabs before switching workspaces.');
    this.set({switchingWorkspace:true});
    try{const session=this.value.session;const workspace=await this.selectWorkspace(root,trust,session?.id);if(session)this.set({session:{...session,workspaceRoot:workspace.resolvedRoot}});}
    finally{this.set({switchingWorkspace:false});}
  }
  async setModel(model:string,provider:string):Promise<void>{
    this.guardOperation();
    if(this.value.switchingSession)throw new Error('The conversation is still opening. Try again when it is ready.');
    if(this.value.running)throw new Error('Stop the current turn before changing models.');
    if(this.value.connection!=='ready')throw new Error('Connect the local engine first.');
    if(!model.trim()||!provider.trim())throw new Error('Choose a model and provider.');
    const session=this.value.session;
    const revision=this.sessionRevision;
    if(session){
      const result=await this.bridge.request<{model:string;provider:string}>('session.setModel',{sessionId:session.id,model,provider});
      if(this.value.session?.id!==session.id||revision!==this.sessionRevision)return;
      this.set({model:result.model,provider:result.provider,session:{...this.value.session,model:result.model,provider:result.provider}});
    }else{
      await this.bridge.request('config.set',{key:'provider',value:provider});
      try{await this.bridge.request('config.set',{key:'model',value:model});}
      catch(error){const config=await this.bridge.request<ConfigGetResult>('config.get');if(revision===this.sessionRevision&&!this.value.session)this.set({model:String(config.config.model??this.value.model),provider:String(config.config.provider??provider)});throw error;}
      if(revision===this.sessionRevision&&!this.value.session)this.set({model,provider});
    }
  }
  async renameSession(title:string):Promise<void>{
    this.guardOperation();
    if(this.value.switchingSession)throw new Error('The conversation is still opening.');
    const session=this.value.session;if(!session)throw new Error('Open a conversation first.');if(this.value.running)throw new Error('Stop the current turn first.');
    const result=await this.bridge.request<SessionResult>('session.rename',{sessionId:session.id,title});
    this.set({sessions:this.value.sessions.map(s=>s.id===session.id?result.session:s),...(this.value.session?.id===session.id?{session:result.session}:{})});
  }
  async forkSession(title=''):Promise<void>{
    this.guardOperation();
    if(this.value.switchingSession)throw new Error('The conversation is still opening.');
    const session=this.value.session;if(!session)throw new Error('Open a conversation first.');if(this.value.running)throw new Error('Stop the current turn first.');
    const revision=this.sessionRevision;
    const result=await this.bridge.request<SessionResult>('session.fork',{sessionId:session.id,title});this.set({sessions:[result.session,...this.value.sessions]});if(revision===this.sessionRevision&&this.value.session?.id===session.id)await this.resume(result.session.id);
  }
  async deleteSession():Promise<void>{
    this.guardOperation();
    if(this.value.switchingSession)throw new Error('The conversation is still opening.');
    const session=this.value.session;if(!session)throw new Error('Open a conversation first.');if(this.value.running)throw new Error('Stop the current turn first.');
    const result=await this.bridge.request<{deleted:boolean}>('session.delete',{sessionId:session.id});if(!result.deleted)throw new Error('The conversation was not deleted.');if(this.value.session?.id===session.id)this.newSession();this.set({sessions:this.value.sessions.filter(s=>s.id!==session.id)});
  }
  async restoreRecovery(): Promise<void> {
    if (!this.recovery) return;
    try {
      const records = await this.recovery.load();
      if (records.length) {
        this.next = Math.max(this.next, ...records.map(record => Number(record.id.replace(/^untitled:/, "")) || 0));
        this.set({ documents: records, activeId: records[0]!.id, dirty: new Set(records.map(record => record.id)), error: `Recovered ${records.length} unsaved buffer${records.length === 1 ? "" : "s"}. Files on disk are unchanged.` });
      }
    } catch (error) { this.fail(error); }
  }

  connect(): Promise<void> {
    if(this.value.switchingWorkspace){this.fail('Wait for the workspace to finish opening before reconnecting.');return Promise.resolve();}
    if (this.connecting) return this.connecting;
    this.connecting = this.start().finally(() => { this.connecting = undefined; });
    return this.connecting;
  }
  private async start(): Promise<void> {
    this.set({ connection: "connecting", error: "" });
    try {
      await this.bridge.start();
      const capabilities=parseCapabilities(await this.bridge.request("runtime.capabilities"));
      this.set({methods:new Set(capabilities.methods)});
      const info = await this.bridge.request<InitializeResult>("initialize", { protocolVersion: 2 });
      const [config, sessions] = await Promise.all([
        this.bridge.request<ConfigGetResult>("config.get"),
        this.bridge.request<SessionListResult>("session.list", { limit: 50 }),
      ]);
      const preference=workspacePreference();
      const recoveredRoots=[...new Set(this.value.documents.map(document=>document.workspaceRoot).filter((root):root is string=>!!root))];
      if(recoveredRoots.length>1)throw new Error('Recovered files belong to different workspaces. Your buffers are retained; choose their workspace folder to reconnect them.');
      const root=this.value.session?.workspaceRoot??recoveredRoots[0]??preference?.root??info.cwdFull??info.cwd;
      const trust=preference&&sameWorkspace(root,preference.root)?preference.trust:!recoveredRoots.length&&!preference;
      const workspace=await this.workspace.select(root,trust,this.value.session?.id);
      this.set({workspace});rememberWorkspace(workspace.resolvedRoot,workspace.trusted);
      await this.reconnectDocuments();
      this.set({ connection: "ready", workspace, model: String(config.config.model ?? info.model), provider: String(config.config.provider ?? info.provider), sessions: (sessions.sessions ?? []).filter(session => !session.parentSessionId || session.isFork) });
    } catch (error) { this.set({ connection: "failed" }); this.fail(error); }
  }
  async list(path = "", cursor?: string) { const result = await this.workspace.list(path, cursor); if (isWorkspaceRefusal(result)) throw new Error(result.refused.message); return result; }
  private inspectionMatches(result:WorkspaceIntelligenceResult,active:boolean):boolean{
    const source=this.inspections.get(result);
    return !!source&&!['stale','pending','unavailable'].includes(result.freshness)&&this.workspace.identity()===source.workspace&&(!active||this.value.activeId===source.id)&&this.editorStates.get(source.id)?.doc===source.doc&&this.documents.version(source.id)===source.version;
  }
  isInspectionCurrent(result:WorkspaceIntelligenceResult):boolean{return this.inspectionMatches(result,true)}
  async openLocation(uri:string,line:number,character=0,expected?:WorkspaceIntelligenceResult):Promise<void>{
    if(!Number.isSafeInteger(line)||line<0||!Number.isSafeInteger(character)||character<0)throw new Error('Invalid source position.');
    const workspace=this.workspace.identity();if(!workspace)throw new Error('Open a workspace first.');
    const path=relativeWorkspacePath(workspace.resolvedRoot,uri);
    if(expected&&!this.isInspectionCurrent(expected))throw new Error('The inspected source changed. Request fresh results before navigating.');
    const focus=this.focusRevision+1;
    await this.openFile(path,()=>!expected||this.inspectionMatches(expected,false));
    if(this.workspace.identity()!==workspace||this.focusRevision!==focus)return;
    if(expected&&!this.inspectionMatches(expected,false))throw new Error('The inspected source changed. Request fresh results before navigating.');
    const document=this.value.documents.find(value=>value.uri&&workspaceContains(workspace.resolvedRoot,value.uri)&&relativeWorkspacePath(workspace.resolvedRoot,value.uri)===path);
    if(document){this.set({page:'chat',activeId:document.id,editorFocus:this.value.editorFocus+1,editorNavigation:{documentId:document.id,line,character,token:++this.next}});this.requestUI('editor');}
  }
  async openFile(path: string,focusGuard?:()=>boolean): Promise<void> {
    if(this.value.switchingWorkspace)throw new Error('Wait for the workspace to finish opening.');
    const focus=++this.focusRevision;
    const workspace = this.workspace.identity();
    const root = workspace?.resolvedRoot;
    if (!root) throw new Error("Connect a workspace first.");
    const base = new URL("file:///"); const normalized = root.replaceAll("\\", "/");
    if (normalized.startsWith("//")) { const parts = normalized.slice(2).split("/"); base.hostname = parts.shift()!; base.pathname = `/${parts.join("/")}/`; }
    else base.pathname = `${normalized.startsWith("/") ? "" : "/"}${normalized}/`;
    const uri = new URL(path.split("/").map(encodeURIComponent).join("/"), base).href;
    const existing = this.value.documents.find(doc => doc.uri === uri);
    if (existing) { this.set({ activeId: existing.id, editorFocus: this.value.editorFocus + 1 }); return; }
    const request = (this.fileReads.get(uri) ?? 0) + 1;
    this.fileReads.set(uri, request);
    const read = await this.workspace.read(uri);
    if (this.workspace.identity() !== workspace || this.fileReads.get(uri) !== request) return;
    if (isWorkspaceRefusal(read)) throw new Error(read.refused.message);
    const document = await this.documents.open(read as WorkspaceReadResult);
    if (this.workspace.identity() !== workspace || this.fileReads.get(uri) !== request) return;
    const focused=focus===this.focusRevision&&(!focusGuard||focusGuard());
    const docs = this.value.documents.filter(doc => !focused||doc.uri || this.value.dirty.has(doc.id));
    this.set({ documents: [...docs, {...document,workspaceRoot:root}], ...(focused?{activeId: document.id, editorFocus: this.value.editorFocus + 1}:{}) });
  }
  selectDocument(id: string): void { if (this.value.documents.some(document => document.id === id)){this.focusRevision++;this.set({ activeId: id, editorFocus: this.value.editorFocus + 1 });} }
  async inspectDocument(operation:WorkspaceIntelligenceOperation){
    const id=this.value.activeId;const state=this.editorStates.get(id);const workspace=this.workspace.identity();if(!state||!workspace||!this.value.documents.find(document=>document.id===id)?.uri)throw new Error('Open a saved project file in the editor first.');
    await this.documents.flush(id);const version=this.documents.version(id);const line=state.doc.lineAt(state.selection.main.head);const result=await this.workspace.intelligence(operation,id,version,{line:line.number-1,character:state.selection.main.head-line.from});
    if(isWorkspaceRefusal(result))throw new Error(result.refused.message);
    if(this.workspace.identity()!==workspace||this.value.activeId!==id||this.editorStates.get(id)?.doc!==state.doc||result.workspaceEpoch!==workspace.epoch||result.documentVersion!==version)throw new Error('The file changed during inspection. Request fresh results.');
    this.inspections.set(result,{workspace,id,doc:state.doc,version});
    return result;
  }
  newDocument(): void { this.focusRevision++;const id = `untitled:${++this.next}`; this.set({ documents: [...this.value.documents, { id, name: `Untitled ${this.next}.py`, text: "" }], activeId: id, editorFocus: this.value.editorFocus + 1 }); }
  async saveAs(path:string):Promise<void>{
    if(this.value.switchingWorkspace)throw new Error('Wait for the workspace to finish opening.');
    const original=this.value.documents.find(d=>d.id===this.value.activeId);const root=this.value.workspace?.resolvedRoot;
    if(!original||!root)throw new Error('Connect a workspace before saving.');
    const parts=path.trim().replaceAll('\\','/').split('/');
    if(!path.trim()||parts.some(p=>!p||p==='.'||p==='..'||p.includes(':'))||path.startsWith('/'))throw new Error('Choose a relative path inside the workspace.');
    const base=new URL('file:///');const normalized=root.replaceAll('\\','/');
    if(normalized.startsWith('//')){const pieces=normalized.slice(2).split('/');base.hostname=pieces.shift()!;base.pathname='/'+pieces.join('/')+'/'}else base.pathname=(normalized.startsWith('/')?'':'/')+normalized+'/';
    const uri=new URL(parts.map(encodeURIComponent).join('/'),base).href;
    if(this.value.documents.some(d=>d.uri===uri))throw new Error('This file is already open. Choose another path.');
    const captured=this.editorStates.get(original.id);if(!captured)throw new Error('The editor is still opening. Try again.');
    const result=await this.workspace.save(uri,null,captured.sliceDoc(),0);
    if(!result.ok)throw new Error(result.conflict?'A file already exists at this path. Choose another name; it was not overwritten.':result.refused?.message??'Save failed. Your buffer is retained.');
    const latest=this.editorStates.get(original.id)??captured;
    const document=await this.documents.open({uri,text:latest.sliceDoc(),revision:result.revision!,languageId:original.languageId??'python',encoding:'utf-8',lineEnding:latest.lineBreak==='\r\n'?'CRLF':latest.lineBreak==='\r'?'CR':'LF',readOnly:false,sizeBytes:new TextEncoder().encode(latest.sliceDoc()).length},latest);
    this.editorStates.set(document.id,latest);this.editorStates.delete(original.id);
    const dirty=new Set(this.value.dirty);dirty.delete(original.id);if(latest!==captured)dirty.add(document.id);
    this.set({documents:this.value.documents.map(d=>d.id===original.id?{...document,initialState:latest,workspaceRoot:root}:d),activeId:document.id,dirty});
    if(original.uri)await this.documents.close(original.id).catch(reason=>this.fail(reason));
    await this.recovery?.forget(original.id);if(latest!==captured)this.recovery?.document(document,latest);
  }
  async closeDocument(id:string,discard=false):Promise<void>{
    const original=this.value.documents.find(d=>d.id===id);if(!original)return;
    const captured=this.editorStates.get(id);
    if(id===this.value.activeId)this.focusRevision++;
    if(this.value.dirty.has(id)&&!discard)throw new Error('Save or discard your changes before closing.');
    await this.documents.close(id);
    await this.recovery?.forget(id);
    const latest=this.editorStates.get(id);
    if(latest&&latest.doc!==captured?.doc){
      const dirty=new Set(this.value.dirty);dirty.add(id);this.set({dirty});
      this.recovery?.document(original,latest);
      if(original.uri)await this.documents.open({uri:original.uri,text:latest.sliceDoc(),revision:original.revision!,languageId:original.languageId??'plaintext',encoding:'utf-8',lineEnding:original.lineEnding==='CRLF'?'CRLF':original.lineEnding==='CR'?'CR':original.lineEnding==='mixed'?'mixed':'LF',readOnly:original.readOnly??false,sizeBytes:new TextEncoder().encode(latest.sliceDoc()).length},latest);
      throw new Error('New edits arrived while the tab was closing. The tab and changes were kept.');
    }
    const remaining=this.value.documents.filter(d=>d.id!==id);const dirty=new Set(this.value.dirty);dirty.delete(id);this.editorStates.delete(id);
    if(!remaining.length){const next=`untitled:${++this.next}`;remaining.push({id:next,name:'Untitled.py',text:''})}
    this.set({documents:remaining,dirty,activeId:this.value.activeId===id?remaining[0]!.id:this.value.activeId});
  }
  bindEditor(id: string, state: EditorState): void { this.editorStates.set(id, state); this.documents.bind(id, state); }
  async reloadDocument(id:string,discard=false):Promise<void>{
    const original=this.value.documents.find(document=>document.id===id);const workspace=this.workspace.identity();if(!original?.uri||!workspace)return;
    if(this.value.dirty.has(id)&&!discard)throw new Error('This file has unsaved edits. Confirm replacing them before reloading.');
    const captured=this.editorStates.get(id);const request=(this.fileReads.get(id)??0)+1;this.fileReads.set(id,request);
    const read=await this.workspace.read(original.uri);if(isWorkspaceRefusal(read))throw new Error(read.refused.message);
    if(this.workspace.identity()!==workspace||this.fileReads.get(id)!==request||!this.value.documents.some(document=>document.id===id))return;
    const current=this.editorStates.get(id);
    if(current?.doc!==captured?.doc||(!discard&&this.value.dirty.has(id))){this.set({documents:this.value.documents.map(document=>document.id===id?{...document,externalRevision:read.revision}:document)});return;}
    // Text stores logical lines independently of the previous file's separator.
    // Configure serialization before the reloaded state reaches sync or save.
    const next=current&&configureDocumentState(current.update({changes:{from:0,to:current.doc.length,insert:Text.of(read.text.split(/\r\n?|\n/))}}).state,read);
    if(next)this.editorStates.set(id,next);
    const opening=this.documents.open(read,next);
    const dirty=new Set(this.value.dirty);dirty.delete(id);
    this.set({documents:this.value.documents.map(document=>document.id===id?{...read,id,name:document.name,workspaceRoot:original.workspaceRoot??workspace.resolvedRoot,initialState:next,reloadToken:(document.reloadToken??0)+1,externalRevision:undefined}:document),dirty});
    await opening;
    if(!this.value.dirty.has(id))await this.recovery?.forget(id);
  }
  changed(id: string, update: ViewUpdate): void {
    this.editorStates.set(id, update.state);
    this.documents.changed(id, update);
    const document = this.value.documents.find(document => document.id === id);
    if (document) this.recovery?.document(document, update.state);
    if (!this.value.dirty.has(id)) this.set({ dirty: new Set([...this.value.dirty, id]) });
  }
  async save(): Promise<void> {
    if(this.value.switchingWorkspace)throw new Error('Wait for the workspace to finish opening.');
    const document = this.value.documents.find(doc => doc.id === this.value.activeId);
    if (!document) return;
    this.savingFiles.add(document.id);
    let result:{revision:string;unchanged:boolean};try{result=await this.documents.save(document)}finally{this.savingFiles.delete(document.id)}
    const dirty = new Set(this.value.dirty); if (result.unchanged) dirty.delete(document.id);
    this.set({ documents: this.value.documents.map(doc => doc.id === document.id ? { ...doc, revision: result.revision,externalRevision:undefined } : doc), dirty });
    if (result.unchanged) await this.recovery?.forget(document.id);
    else {
      const current = this.editorStates.get(document.id);
      if (current) this.recovery?.document({ ...document, revision: result.revision }, current);
    }
  }
  async resume(id: string): Promise<void> {
    this.guardOperation();
    if(this.value.switchingWorkspace)throw new Error('Wait for the workspace to finish opening.');
    if (this.value.running) throw new Error("Stop the current turn before switching conversations.");
    const revision=++this.sessionRevision;this.set({switchingSession:true});
    try{
    const result = await this.bridge.request<{ session: SessionInfo; history: HistoryEntry[] }>("session.resume", { sessionId: id });
    if(revision!==this.sessionRevision)return;if(this.value.running)throw new Error('A turn started while opening the conversation. Stop the current turn and try again.');
    let jobs:Job[]=[];let jobError='';
    try{jobs=(await this.bridge.request<{jobs:Job[]}>('shell.jobs',{sessionId:id})).jobs??[]}catch(reason){jobError=`Could not reload background commands: ${reason instanceof Error?reason.message:String(reason)}`}
    if(revision!==this.sessionRevision)return;if(this.value.running)throw new Error('A turn started while opening the conversation. Stop the current turn and try again.');
    const root=result.session.workspaceRoot??this.value.workspace?.resolvedRoot;
    if(root){
      const same=!!this.value.workspace&&sameWorkspace(root,this.value.workspace.resolvedRoot);
      if(!same&&(this.value.dirty.size||this.value.jobs.some(job=>job.status==='running')))throw new Error('Save or close unsaved code tabs and stop active commands before opening a conversation from a different workspace.');
      if(!same||!result.session.workspaceRoot){
        this.set({switchingWorkspace:true});
        try{const selected=await this.selectWorkspace(root,same?this.value.workspace!.trusted:false,result.session.id);result.session={...result.session,workspaceRoot:selected.resolvedRoot};}
        finally{this.set({switchingWorkspace:false});}
      }
    }
    if(revision!==this.sessionRevision)return;
    this.transcript.clear();
    for (const [index, entry] of result.history.entries()) this.transcript.put({ id: `history:${id}:${index}`, role: entry.role === "system" ? "notice" : entry.role, title: entry.tool, text: entry.content, status: entry.role === "tool" ? "recorded" : undefined });
    this.turnId = undefined; this.currentAssistant = undefined;
    this.agents.clear();
    this.set({ page:'chat', session: result.session, model: result.session.model, provider: result.session.provider, queue: [], jobs, approvals: [],agentIds:[],hiddenAgentIds:new Set(),focusedAgent:undefined,error:jobError });
    }finally{if(revision===this.sessionRevision)this.set({switchingSession:false})}
  }
  newSession(): void {
    if(this.operations.snapshot().active){this.fail('Wait for the background run to settle, or stop it first.');return;}
    if (this.value.running||this.value.switchingWorkspace) return;
    const revision=++this.sessionRevision;this.agents.clear();
    this.transcript.clear(); this.turnId = undefined; this.currentAssistant = undefined;
    this.set({ page:'chat',switchingSession:false, session: undefined, queue: [], jobs: [], approvals: [],agentIds:[],hiddenAgentIds:new Set(),focusedAgent:undefined, error: "" });
    if(this.value.connection==='ready')void this.bridge.request<ConfigGetResult>('config.get').then(config=>{if(revision===this.sessionRevision&&!this.value.session)this.set({model:String(config.config.model??this.value.model),provider:String(config.config.provider??this.value.provider)})}).catch(error=>{if(revision===this.sessionRevision)this.fail(error)});
  }
  async send(text: string,images:PromptImage[]=[]): Promise<void> {
    if(this.value.switchingWorkspace)throw new Error('The workspace is still opening. Your draft is retained.');
    if(this.value.switchingSession)throw new Error('The conversation is still opening. Your draft is retained.');
    const command=parseDesktopCommand(text);
    if(images.length&&command)throw new Error('Send image attachments with a message, rather than a slash command.');
    if(images.length&&this.value.running)throw new Error('Image attachments can be sent after the current turn finishes. Your draft is retained.');
    if(images.length>5||images.reduce((sum,image)=>sum+image.data.length,0)>Math.ceil(MAX_IMAGE_TOTAL/3)*4+images.length*4)throw new Error('Images exceed the desktop attachment limit. Your draft is retained.');
    if(command){
      const ui:Record<string,NonNullable<DesktopSnapshot['uiAction']>['name']>={workspace:'workspace',editor:'editor',quickopen:'quickOpen',terminal:'terminal',output:'terminal',rename:'sessionTools',fork:'sessionTools',delete:'sessionTools',export:'sessionTools',goal:'sessionTools'};
      if(command.name==='history'||command.name==='transcript'){if(command.args.length>256)throw new Error('History searches can contain up to 256 characters. Your draft is retained.');this.requestUI('history',command.args);return;}
      if(command.name==='attach'){if(command.args)throw new Error('/attach uses the active editor selection and takes no arguments.');this.requestSelectionAttachment();return;}
      if(command.name==='review'){await this.send(`Review my current changes and find issues.${command.args?` Focus on: ${command.args}`:''} Use the available tools to inspect the working tree, then report findings ordered by severity.`);return;}
      if(command.name==='init'){await this.initializeInstructions();return;}
      if(command.name==='rename'&&command.args){await this.renameSession(command.args);return;}
      if(command.name==='fork'&&command.args){await this.forkSession(command.args);return;}
      if(ui[command.name]){if(command.args)throw new Error(`/${command.name} opens its desktop controls. Choose the operation there.`);this.requestUI(ui[command.name]!,ui[command.name]==='sessionTools'?command.name:undefined);return;}
      if(command.name==='resume'){if(command.args)await this.resume(command.args);else this.navigate('sessions');return;}
      const page=FEATURE_COMMANDS[command.name];
      if(page){if(command.args)throw new Error(`/${command.name} opens the ${page} page. Use its controls for arguments.`);this.navigate(page);return;}
      if(command.name==='new'||command.name==='clear'){if(this.value.running)throw new Error('Stop the current turn before starting a new session.');this.newSession();return;}
      if(command.name==='sessions'||command.name==='resume'){if(command.args)await this.resume(command.args);else this.navigate('chat');return;}
      throw new Error(`Unknown command /${command.name}. Open /help for desktop commands. It was not sent to the model.`);
    }
    this.guardOperation();
    if (this.value.connection !== "ready") throw new Error("Connect the local engine first. Your draft is retained.");
    const revision=this.sessionRevision;
    if (!this.value.session) {
      this.set({switchingSession:true});
      try{
        const created = await this.bridge.request<SessionResult>("session.create", { title: text.slice(0, 80),...(this.value.profile?{agent:`harness-${this.value.profile}`}:{}) });
        if(revision!==this.sessionRevision)throw new Error('The conversation changed before sending. Your draft is retained.');
        this.set({ session: created.session, sessions: [created.session, ...this.value.sessions] });
      }finally{if(revision===this.sessionRevision)this.set({switchingSession:false});}
    }
    const sessionId = this.value.session!.id;
    if (this.value.running) {
      const queue = await this.bridge.request<{ queued: boolean; items?: QueueItem[] }>("prompt.steer", { sessionId, text });
      if(revision!==this.sessionRevision||this.value.session?.id!==sessionId)throw new Error('The conversation changed while queuing. Your draft is retained.');
      if (queue.queued) { this.set({ queue: queue.items ?? this.value.queue }); return; }
      this.set({ running: false });
    }
    const userId = `user:${++this.next}`;
    const displayText=text+(images.length?'\n'+images.map((image,index)=>`[image ${index+1}] ${image.name}`).join('\n'):'');
    this.transcript.put({ id: userId, role: "user", text:displayText, status: "sending" });
    this.turnId = undefined; this.currentAssistant = undefined; this.displayed = "";
    this.set({ running: true, error: "" });
    try {
      const result = await this.bridge.request<{ turnId: string }>("prompt.submit", { sessionId, text,...(images.length?{images}:{}) });
      if(revision!==this.sessionRevision||this.value.session?.id!==sessionId)return;
      if (!this.retired.has(result.turnId)) this.turnId = result.turnId;
      this.transcript.put({ id: userId, role: "user", text:displayText });
    } catch (error) { if(revision===this.sessionRevision&&this.value.session?.id===sessionId){this.set({ running: false }); this.transcript.put({ id: userId, role: "user", text:displayText, status: "not sent" });} throw error; }
  }
  async initializeInstructions():Promise<void>{
    if(this.value.connection!=='ready'||!this.value.workspace)throw new Error('Open a workspace first.');
    const root=this.value.workspace.resolvedRoot.replaceAll('\\','/');const base=new URL('file:///');if(root.startsWith('//')){const parts=root.slice(2).split('/');base.hostname=parts.shift()!;base.pathname='/'+parts.join('/')+'/'}else base.pathname=(root.startsWith('/')?'':'/')+root+'/';
    const uri=new URL('AGENTS.md',base).href;
    const text='# Project instructions\n\n## Workflow\n\n- Read the relevant code before making changes.\n- Keep changes focused and consistent with this project.\n- Run the checks appropriate to the change.\n- Report concrete findings and remaining limitations.\n- Do not commit or publish unless asked.\n\n## Local knowledge\n\nAdd this project’s architecture, conventions and useful commands here.\n';
    const result=await this.workspace.save(uri,null,text,0);if(!result.ok)throw new Error(result.conflict?'AGENTS.md already exists and was kept. Open it from Files to edit it.':result.refused?.message??'Could not create project instructions.');await this.openFile('AGENTS.md');
  }
  async interrupt(): Promise<void> { if(this.operations.snapshot().active){await this.operations.cancel();return}if (this.value.session) await this.bridge.request("prompt.cancel", { sessionId: this.value.session.id }); }
  async removeQueued(id: string): Promise<void> {
    const sessionId=this.value.session?.id,revision=this.sessionRevision;if(!sessionId)return;
    const result = await this.bridge.request<{ items: QueueItem[] }>("prompt.steering", { sessionId, remove: id });if(revision===this.sessionRevision&&this.value.session?.id===sessionId)this.set({ queue: result.items });
  }
  async replaceQueued(id:string,text:string):Promise<void>{const sessionId=this.value.session?.id;if(!text.trim())throw new Error('Write a message before saving.');const result=await this.bridge.request<{items:QueueItem[]}>('prompt.steering',{sessionId,replace:id,text});if(this.value.session?.id===sessionId)this.set({queue:result.items});}
  async job(id: string, action: "background" | "stop" | "output"): Promise<Job> {
    const sessionId=this.value.session?.id;
    const result = await this.bridge.request<{ job: Job }>("shell.jobs", { sessionId, jobId: id, ...(action !== "output" ? { [action]: true } : {}) });
    if(this.value.session?.id===sessionId)this.set({ jobs: [...this.value.jobs.filter(job => job.id !== id), result.job] }); return result.job;
  }
  async approve(requestId: string, verdict: "approved" | "denied"): Promise<void> {
    await this.bridge.request("approvals.resolve", { sessionId: this.value.session?.id, requestId, verdict, grant: "once" });
    this.set({ approvals: this.value.approvals.filter(approval => approval.requestId !== requestId) });
  }
  dispose(): void { this.unsubscribe();this.unsubscribeOperations();this.operations.dispose(); }

  private appendAssistant(text: string): void {
    if (!this.currentAssistant) {
      this.currentAssistant = `assistant:${this.turnId}:${++this.next}`;
      this.transcript.put({ id: this.currentAssistant, role: "assistant", text: "", streaming: true });
    }
    this.transcript.append(this.currentAssistant, text);
    this.displayed += text;
  }
  private event(event: Record<string, unknown>): void {
    if (event.type === "desktop.disconnected") { this.operations.disconnected();this.set({ connection: "offline", running: false, error: "Engine disconnected. Unfinished work has an unknown outcome; your drafts and code are retained." }); return; }
    if(event.type==='workspace.fileChanged'){
      const workspace=this.workspace.identity();if(!workspace||event.workspaceId!==workspace.workspaceId||event.epoch!==workspace.epoch)return;
      const document=this.value.documents.find(value=>value.uri===event.uri);if(!document||event.revision===document.revision||this.savingFiles.has(document.id))return;
      if(this.value.dirty.has(document.id)){this.set({documents:this.value.documents.map(value=>value.id===document.id?{...value,externalRevision:String(event.revision)}:value)});return;}
      void this.reloadDocument(document.id).catch(reason=>this.fail(reason));return;
    }
    if (event.sessionId !== this.value.session?.id) return;
    if (event.type === "shell.job" && event.job && typeof event.job === "object") {
      const job = event.job as Job; const prior = this.value.jobs.find(value => value.id === job.id);
      if (prior && prior.status !== "running" && job.status === "running") return;
      this.set({ jobs: [...this.value.jobs.filter(value => value.id !== job.id), job] }); return;
    }
    const turnId = String(event.turnId ?? "");
    if (!turnId || this.retired.has(turnId) || (this.turnId && this.turnId !== turnId)) return;
    this.turnId ??= turnId;
    switch (event.type) {
      case "message.start": this.displayed = ""; this.currentAssistant = undefined; this.set({ running: true }); break;
      case "message.delta": this.appendAssistant(String(event.text ?? "")); break;
      case "tool.start":
        if (this.currentAssistant) { const old = this.transcript.get(this.currentAssistant)!; this.transcript.put({ ...old, streaming: false }); }
        this.currentAssistant = undefined;
        this.transcript.put({ id: `tool:${turnId}:${event.id}`, role: "tool", title: String(event.name), text: JSON.stringify(event.args ?? {}), status: "running" }); break;
      case "tool.complete":
        this.transcript.put({ id: `tool:${turnId}:${event.id}`, role: "tool", title: String(event.name), text: String(event.result ?? ""), status: event.isError ? "failed" : event.outcome === "outcome_unknown" ? "unknown" : "complete", truncated: Boolean(event.truncated) }); break;
      case "steering.update":
        for (const item of (event.delivered ?? []) as QueueItem[]) this.transcript.put({ id: `steer:${item.id}`, role: "user", text: item.text });
        this.set({ queue: (event.items ?? []) as QueueItem[] }); break;
      case "needs_approval": case "permission.required": this.set({ approvals: [...this.value.approvals.filter(item => item.requestId !== event.requestId), event as unknown as Approval] }); break;
      case "approval.resolved": case "permission.resolved": this.set({ approvals: this.value.approvals.filter(item => item.requestId !== event.requestId) }); break;
      case "agent.child": {
        const id = String(event.childSessionId); const current = this.agents.get(id);
        if (event.phase === "start") { this.agents.put({ id, role: "assistant", title: String(event.value), text: "", status: "working" }); this.set({ agentIds: [...this.value.agentIds.filter(item => item !== id), id] }); }
        else if (event.phase === "text" || event.phase === "tool") this.agents.append(id, String(event.value ?? ""));
        else if (event.phase === "paused") { if (current) this.agents.put({ ...current, status: "Waiting for approval" }); }
        else { this.agents.remove(id); this.set({ agentIds: this.value.agentIds.filter(item => item !== id) }); }
        break;
      }
      case "error": this.fail(String(event.message ?? "Agent request failed")); break;
      case "message.complete": {
        const text = String(event.text ?? "");
        if (text && text !== this.displayed) {
          if (text.startsWith(this.displayed)) this.appendAssistant(text.slice(this.displayed.length));
          else if (this.currentAssistant) this.transcript.put({ ...this.transcript.get(this.currentAssistant)!, text, streaming: false });
          else this.appendAssistant(text);
        }
        if (this.currentAssistant) this.transcript.put({ ...this.transcript.get(this.currentAssistant)!, streaming: false });
        for (const id of this.transcript.order()) { const row = this.transcript.get(id)!; if (row.role === "tool" && row.status === "running") this.transcript.put({ ...row, status: event.cancelled ? "interrupted" : "unknown" }); }
        if (event.cancelled) this.transcript.put({ id: `stop:${turnId}`, role: "notice", text: "Interrupted. Queued prompts remain pending." });
        this.retired.add(turnId); if (this.retired.size > 256) this.retired.delete(this.retired.values().next().value!);
        this.set({ running: false }); break;
      }
    }
  }
}
