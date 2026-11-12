import {Select} from '../../components/Select.tsx';
import {Fragment,useEffect,useRef,useState,useSyncExternalStore,type FormEvent} from 'react';
import type {AgentHistoryResult,AgentInfo,DelegationResult} from '@machina/contracts/rpc';
import type {DesktopController} from '../../state/controller.ts';
import {useEnginePage} from '../shared/useEnginePage.ts';
import {OperationStatus} from '../shared/OperationStatus.tsx';

export interface DelegationSelection {agent:string;scope:string;id:number}
interface Step {id:number;agent:string;task:string}
interface RunResults {rows:DelegationResult[];steps:Step[];mode:'sequential'|'parallel'}
interface Props {
 controller:DesktopController;
 agents:AgentInfo[];
 selection?:DelegationSelection;
 scope:string;
 sessionId?:string;
 available:boolean;
 working:boolean;
 running:boolean;
 run:(params:Record<string,unknown>)=>Promise<{results:DelegationResult[]}|undefined>;
}
const statusLabel=(status:string)=>({complete:'Complete',completed:'Complete',error:'Failed',failed:'Failed',waiting_approval:'Waiting for approval',running:'Running',pending:'Pending',interrupted:'Interrupted'}[status]??status.replaceAll('_',' '));

function DelegationHistory({controller}:{controller:DesktopController}) {
 const page=useEnginePage<AgentHistoryResult>(controller,'agents.history',true);
 return <section aria-label="Delegation history" className="management-list">
  <div className="management-row-heading"><h2>Delegation history</h2><div className="management-actions"><button disabled={!page.available||page.busy} onClick={page.refresh}>Refresh delegation history</button></div></div>
  <p className="management-muted">Up to 100 recorded delegations in this conversation, in the order they started.</p>
  {page.unavailableReason&&<p role="status">{page.unavailableReason}</p>}
  {page.busy&&<p role="status">Loading delegation history…</p>}
  {page.error&&<p className="inline-error" role="alert">{page.error}</p>}
  {!page.error&&page.data?.messages.map(row=><article key={row.id}>
   <div className="management-row-heading"><h2>{row.fromAgent} → {row.toAgent}</h2><small>{statusLabel(row.status)}{row.tokens?` · ${row.tokens.toLocaleString()} tokens`:''}</small></div>
   {row.createdAt&&<p className="management-muted"><time dateTime={row.createdAt}>{new Date(row.createdAt).toLocaleString()}</time></p>}
   <p className="memory-content">{row.task}</p>{row.response&&<details><summary>Response</summary><pre>{row.response}</pre></details>}
  </article>)}
  {page.available&&!page.busy&&!page.error&&page.data?.messages.length===0&&<p>No delegations recorded in this conversation.</p>}
 </section>;
}

export function ManualDelegation({controller,agents,selection,scope,sessionId,available,working,running,run:request}:Props) {
 const operations=useSyncExternalStore(controller.operations.subscribe,controller.operations.snapshot);
 const tracked=operations.entries.find(row=>row.method==='agents.run'&&row.sessionId===sessionId);
 const trackedSupported=controller.snapshot().methods?.has('operations.start');
 const [steps,setSteps]=useState<Step[]>(),[mode,setMode]=useState<'sequential'|'parallel'>('sequential'),[result,setResult]=useState<RunResults>(),[pending,setPending]=useState(false),[historyOpen,setHistoryOpen]=useState(false),[historyRevision,setHistoryRevision]=useState(0);
 const nextStep=useRef(1),inFlight=useRef(false),alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 useEffect(()=>{
  if(!tracked||['starting','running','cancelling'].includes(tracked.status))return;
  const submitted=Array.isArray(tracked.params.steps)?tracked.params.steps.map((row,index)=>({...row as {agent:string;task:string},id:index+1})):[];
  const response=tracked.result as {results?:DelegationResult[]}|undefined;
  if(tracked.status==='completed'&&Array.isArray(response?.results)){
   setResult({rows:response.results,steps:submitted,mode:tracked.params.mode==='parallel'?'parallel':'sequential'});
   if(response.results.length===submitted.length&&response.results.every(row=>row.status==='complete'||row.status==='completed'))setSteps(undefined);
   else setSteps(previous=>previous??submitted);
   setHistoryRevision(value=>value+1);
  }else if(submitted.length)setSteps(previous=>previous??submitted);
 },[tracked]);
 useEffect(()=>{
  if(!selection||selection.scope!==scope)return;
  setSteps([{id:nextStep.current++,agent:selection.agent,task:''}]);setMode('sequential');setResult(undefined);
 },[selection,scope]);
 const blocked=!available||working||running||!sessionId;
 function update(id:number,key:'agent'|'task',value:string){setSteps(previous=>previous?.map(step=>step.id===id?{...step,[key]:value}:step))}
 async function run(event:FormEvent){
  event.preventDefault();
  if(inFlight.current||blocked||!steps?.length||steps.some(step=>!step.agent||!step.task.trim()))return;
  const submitted=steps.map(step=>({...step})),execution=steps.length===1?'sequential':mode;
  inFlight.current=true;setPending(true);setResult(undefined);
  try{
   if(trackedSupported){await controller.runOperation('agents.run',{mode:execution,steps:submitted.map(({agent,task})=>({agent,task}))});return}
   const response=await request({mode:execution,steps:submitted.map(({agent,task})=>({agent,task}))});
   if(!alive.current||!response)return;
   setResult({rows:response.results,steps:submitted,mode:execution});setHistoryRevision(value=>value+1);
   if(response.results.length===submitted.length&&response.results.every(row=>row.status==='complete'||row.status==='completed'))setSteps(undefined);
  }catch(error){controller.fail(error)}finally{inFlight.current=false;if(alive.current)setPending(false)}
 }
 const waiting=result?.rows.some(row=>row.status==='waiting_approval'||row.needsApproval?.length);
 const notStarted=result?.mode==='sequential'&&waiting?Math.max(0,result.steps.length-result.rows.length):0;
 return <>
  <OperationStatus controller={controller} method="agents.run"/>
  {tracked?.error&&<p className="inline-error" role="alert">{tracked.error}</p>}
  {steps&&<form className="management-form" onSubmit={event=>{void run(event)}} aria-label="Delegation workflow">
   <h2>{steps.length===1?`Delegate to ${steps[0].agent}`:'Delegate a workflow'}</h2>
   {steps.map((step,index)=><Fragment key={step.id}>
    <div className="management-fields"><label>{index===0?'Agent':`Agent ${index+1}`}<Select disabled={working} value={step.agent} onChange={event=>update(step.id,'agent',event.target.value)}>{agents.map(agent=><option key={agent.name} value={agent.name}>{agent.name}</option>)}</Select></label>{steps.length>1&&<div className="management-actions"><button type="button" disabled={working} aria-label={`Remove step ${index+1}`} onClick={()=>setSteps(previous=>previous?.filter(row=>row.id!==step.id))}>Remove step {index+1}</button></div>}</div>
    <label>{index===0?'Task':`Task ${index+1}`}<textarea autoFocus={index===0} required disabled={working} maxLength={10000} value={step.task} onChange={event=>update(step.id,'task',event.target.value)}/></label>
   </Fragment>)}
   {steps.length>1&&<><label>Execution<Select disabled={working} value={mode} onChange={event=>setMode(event.target.value as typeof mode)}><option value="sequential">Sequential</option><option value="parallel">Parallel</option></Select></label><p className="management-muted">{mode==='sequential'?'Each step receives earlier results. The workflow pauses if a step needs approval.':'Steps run independently and do not receive each other’s results. Use this for tasks without dependencies.'}</p></>}
   <div className="management-actions"><button disabled={blocked||steps.some(step=>!step.agent||!step.task.trim())}>{pending?'Agent working…':steps.length===1?'Run task':'Run workflow'}</button><button type="button" disabled={working||steps.length>=16} onClick={()=>setSteps(previous=>previous&&previous.length<16?[...previous,{id:nextStep.current++,agent:agents[0]?.name??'',task:''}]:previous)}>Add step</button><button type="button" disabled={working} onClick={()=>setSteps(undefined)}>Cancel</button></div>
   {steps.length===16&&<p className="management-muted">A workflow can contain up to 16 steps.</p>}
   {pending&&<p role="status">Agents working… Results appear when the workflow finishes or pauses.</p>}
   {!sessionId&&<p>Open a conversation first so this work has a parent session.</p>}
   {running&&<p>Let the main turn finish before starting a manual delegation.</p>}
  </form>}
  {result&&<section aria-label="Delegation results" className="management-list">
   {result.rows.map((row,index)=><article key={index}>
    <div className="management-row-heading"><h2>{result.steps.length>1?`${index+1}. `:''}{row.agent}</h2><small>{statusLabel(row.status)}{row.tokens?` · ${row.tokens.toLocaleString()} tokens`:''}{row.iterations?` · ${row.iterations} ${row.iterations===1?'iteration':'iterations'}`:''}</small></div>
    <p className="memory-content">{result.steps[index]?.task??row.task}</p><pre>{row.response}</pre>
    {!!row.needsApproval?.length&&<div aria-label={`Approval requests for ${row.agent}`}><h3>Approval required</h3>{row.needsApproval.map((approval,approvalIndex)=><div key={approval.requestId||approvalIndex}><p><strong>{approval.operation||'Requested action'}</strong><br/><span>{approval.target}</span></p><details><summary>Request details</summary>{approval.requestId&&<p>Request: <code>{approval.requestId}</code></p>}{(approval.sessionId||row.sessionId)&&<p>Agent session: <code>{approval.sessionId||row.sessionId}</code></p>}{approval.agentId&&<p>Agent: <code>{approval.agentId}</code></p>}{approval.status&&<p>Status: {statusLabel(approval.status)}</p>}</details></div>)}</div>}
   </article>)}
   {notStarted>0&&<p>{notStarted} {notStarted===1?'step has':'steps have'} not started.</p>}
   {waiting&&<p className="management-muted">The engine cannot resume this paused workflow from this page. Configure conversation permissions before running it again. Rerunning repeats every submitted step.</p>}
   <div className="management-actions">{waiting&&<button onClick={()=>controller.navigate('mode')}>Configure conversation permissions</button>}<button onClick={()=>setResult(undefined)}>Close results</button></div>
  </section>}
  <div className="management-actions"><button aria-expanded={historyOpen} onClick={()=>setHistoryOpen(value=>!value)}>{historyOpen?'Hide delegation history':'View delegation history'}</button></div>
  {historyOpen&&<DelegationHistory key={historyRevision} controller={controller}/>}
 </>;
}
