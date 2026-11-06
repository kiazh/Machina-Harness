import type {OperationSnapshot} from '@machina/contracts/rpc';
import type {DesktopBridge} from '../bridge/types.ts';

export interface TrackedOperation extends Omit<OperationSnapshot,'status'> {
 status:OperationSnapshot['status']|'starting'|'unknown';
 params:Record<string,unknown>;
 unconfirmed?:boolean;
}
interface Snapshot {entries:readonly TrackedOperation[];active?:TrackedOperation;error:string}
export class OperationStore {
 private value:Snapshot={entries:[],error:''};
 private listeners=new Set<()=>void>();
 private timer?:ReturnType<typeof setTimeout>;
 private generation=0;
 private readSequence=0;
 private disposed=false;
 private resolving=new Set<string>();
 private resolvedApprovals=new Set<string>();
 constructor(private bridge:DesktopBridge){}
 snapshot=()=>this.value;
 subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener)}};
 private put(entry:TrackedOperation,error=''){
  entry={...entry,approvals:(entry.approvals??[]).filter(approval=>!this.resolvedApprovals.has(approval.requestId))};
  const entries=[entry,...this.value.entries.filter(row=>row.id!==entry.id)].slice(0,32);
  const active=['starting','running','cancelling'].includes(entry.status)||entry.status==='unknown'&&entry.unconfirmed?entry:undefined;
  this.value={entries,active,error};for(const listener of this.listeners)listener();
 }
 async start(method:OperationSnapshot['method'],params:Record<string,unknown>):Promise<TrackedOperation>{
  if(this.disposed)throw new Error('This workspace has closed.');
  if(this.value.active)throw new Error('An operation is already running. Wait for it to finish first.');
  const generation=++this.generation;
  this.resolvedApprovals.clear();
  const entry:TrackedOperation={id:crypto.randomUUID(),method,params:structuredClone(params),sessionId:typeof params.sessionId==='string'?params.sessionId:undefined,status:'starting',cancellable:false,startedAt:new Date().toISOString()};
  this.put(entry);
  try{
   const response=await this.bridge.request<OperationSnapshot>('operations.start',{id:entry.id,method,params:entry.params});
   if(generation!==this.generation||this.disposed)return {...entry,status:'unknown'};
   this.value={...this.value,entries:this.value.entries.filter(row=>row.id!==entry.id)};
   const current={...entry,...response,approvals:response.approvals??[]};this.put(current);this.schedule(generation);return current;
  }catch(reason){
   if(generation===this.generation&&!this.disposed){this.put({...entry,status:'unknown',unconfirmed:true,error:reason instanceof Error?reason.message:String(reason)},'Checking whether the engine started this run. The task has not been repeated.');this.schedule(generation)}
   throw reason;
  }
 }
 private schedule(generation:number,delay=1000){
  if(this.timer)clearTimeout(this.timer);
  if(!this.value.active||this.disposed)return;
  this.timer=setTimeout(()=>{this.timer=undefined;void this.poll(generation)},delay);
 }
 private async poll(generation:number){
  const entry=this.value.active;if(!entry||entry.status==='starting')return;
  const read=++this.readSequence;
  try{
   const response=await this.bridge.request<OperationSnapshot|null>('operations.get',{id:entry.id,...(entry.sessionId?{sessionId:entry.sessionId}:{}),...(entry.unconfirmed?{missingOk:true}:{})});
   if(generation!==this.generation||read!==this.readSequence||this.disposed||this.value.active?.id!==entry.id)return;
   if(response===null){this.put({...entry,status:'failed',unconfirmed:false,error:'The engine did not start this run. Your task draft is retained.'});return}
   // A status read issued before Stop must not overwrite its acknowledgement.
   if(this.value.active?.status==='cancelling'&&response.status==='running')return;
   const pending=new Set((response.approvals??[]).map(approval=>approval.requestId));
   for(const id of this.resolvedApprovals)if(!pending.has(id))this.resolvedApprovals.delete(id);
   this.put({...entry,...response,approvals:response.approvals??[],unconfirmed:false,error:response.error});
  }catch(reason){
   if(generation!==this.generation||read!==this.readSequence||this.disposed)return;
   this.value={...this.value,error:reason instanceof Error?reason.message:String(reason)};for(const listener of this.listeners)listener();
  }finally{if(generation===this.generation&&read===this.readSequence)this.schedule(generation,this.value.error?3000:1000)}
 }
 async cancel():Promise<void>{
  const entry=this.value.active;if(!entry)return;
  if(entry.status==='starting'||entry.unconfirmed)throw new Error('Wait for the engine to confirm this run before stopping it.');
  if(!entry.cancellable)throw new Error('This external run cannot be stopped from Machina. Its engine deadline still applies.');
  if(entry.status==='cancelling')return;
  const generation=this.generation;
  const response=await this.bridge.request<OperationSnapshot>('operations.cancel',{id:entry.id,...(entry.sessionId?{sessionId:entry.sessionId}:{})});
  if(generation===this.generation&&!this.disposed&&this.value.active?.id===entry.id){++this.readSequence;this.put({...entry,...response,approvals:response.approvals??[]});this.schedule(generation)}
 }
 async resolveApproval(requestId:string,verdict:'approved'|'denied'):Promise<void>{
  const entry=this.value.active;
  const approval=entry?.approvals?.find(approval=>approval.requestId===requestId);
  if(!entry||entry.status!=='running'||!approval)throw new Error('This workflow is no longer waiting for that approval.');
  if(this.resolving.has(requestId))throw new Error('This approval is already being resolved.');
  const generation=this.generation;this.resolving.add(requestId);
  try{
   await this.bridge.request('approvals.resolve',{sessionId:approval.sessionId,requestId,verdict,grant:'once'});
   if(this.disposed||this.generation!==generation||this.value.active?.id!==entry.id)return;
   ++this.readSequence;
   this.resolvedApprovals.add(requestId);
   this.put({...this.value.active,approvals:this.value.active.approvals?.filter(approval=>approval.requestId!==requestId)});
   this.schedule(generation,0);
  }finally{this.resolving.delete(requestId)}
 }
 disconnected(){
  ++this.generation;if(this.timer)clearTimeout(this.timer);this.timer=undefined;
  if(this.value.active)this.put({...this.value.active,status:'unknown',unconfirmed:false,error:'Engine disconnected; the outcome of this work is unknown.'});
 }
 dispose(){this.disposed=true;++this.generation;if(this.timer)clearTimeout(this.timer);this.listeners.clear()}
}
