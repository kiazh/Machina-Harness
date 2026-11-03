import {StateEffect,type EditorState} from '@codemirror/state';
import {ViewPlugin,type ViewUpdate,type EditorView} from '@codemirror/view';
import {syntaxTree} from '@codemirror/language';
import {lintGutter,setDiagnostics,type Diagnostic} from '@codemirror/lint';
import {offsetAt} from './completion.ts';

export type DiagnosticsRequest=(id:string,state:EditorState,signal:AbortSignal)=>Promise<unknown|null>;
export const refreshDiagnostics=StateEffect.define<null>();
export function localDiagnostics(state:EditorState):Diagnostic[]{
 const diagnostics:Diagnostic[]=[];
 syntaxTree(state).iterate({enter(node){
  if(!node.type.isError||diagnostics.length>=100)return;
  const from=Math.min(node.from,Math.max(0,state.doc.length-1)),to=Math.min(state.doc.length,Math.max(from+1,node.to));
  if(!diagnostics.some(row=>row.from===from&&row.to===to))diagnostics.push({from,to,severity:'error',message:'Syntax error',source:'Syntax parser'});
 }});
 return diagnostics;
}
export function lspDiagnostics(state:EditorState,raw:unknown):Diagnostic[]{
 if(!raw||typeof raw!=='object'||!Array.isArray((raw as {diagnostics?:unknown}).diagnostics))return [];
 const diagnostics:Diagnostic[]=[];
 for(const row of (raw as {diagnostics:unknown[]}).diagnostics.slice(0,200)){
  if(!row||typeof row!=='object')continue;
  const value=row as {range?:{start:any;end:any};severity?:number;message?:string;source?:string;producer?:string};
  const from=offsetAt(state,value.range?.start),end=offsetAt(state,value.range?.end);
  if(from===null||end===null||end<from||typeof value.message!=='string')continue;
  diagnostics.push({from,to:Math.min(state.doc.length,Math.max(from+1,end)),severity:value.severity===2?'warning':value.severity===3?'info':value.severity===4?'hint':'error',message:value.message.slice(0,8000),source:typeof value.source==='string'?value.source:typeof value.producer==='string'?value.producer:'Language server'});
 }
 return diagnostics;
}

/** No polling: edits and actual server notifications schedule one debounced read. */
export function liveDiagnostics(id:string,request:()=>DiagnosticsRequest|undefined){
 const lifecycle=ViewPlugin.fromClass(class{
  pending?:AbortController;
  timer?:ReturnType<typeof setTimeout>;
  generation=0;
  destroyed=false;
  constructor(readonly view:EditorView){this.schedule()}
  schedule(){
   const generation=++this.generation;
   this.pending?.abort();clearTimeout(this.timer);
   this.timer=setTimeout(()=>void this.run(generation),500);
  }
  update(update:ViewUpdate){
   if(update.docChanged){
    this.schedule();const generation=this.generation;
    // Decorations describe an exact document version, never a guessed mapping.
    queueMicrotask(()=>{if(!this.destroyed&&generation===this.generation)this.view.dispatch(setDiagnostics(this.view.state,[]))});
   }else if(update.transactions.some(transaction=>transaction.effects.some(effect=>effect.is(refreshDiagnostics))))this.schedule();
  }
  async run(generation:number){
   const state=this.view.state,local=localDiagnostics(state);
   this.view.dispatch(setDiagnostics(this.view.state,local));
   const fetch=request();if(!fetch)return;
   const abort=new AbortController();this.pending=abort;
   try{
    const raw=await fetch(id,state,abort.signal);
    if(this.destroyed||abort.signal.aborted||this.generation!==generation||this.view.state.doc!==state.doc)return;
    const remote=lspDiagnostics(state,raw);
    this.view.dispatch(setDiagnostics(this.view.state,[...remote,...local.filter(row=>!remote.some(other=>other.severity==='error'&&other.from<=row.to&&other.to>=row.from))]));
   }catch{/* Local syntax feedback remains available when LSP is unavailable. */}
  }
  destroy(){this.destroyed=true;this.generation++;clearTimeout(this.timer);this.pending?.abort()}
 });
 return [lifecycle,lintGutter()];
}
