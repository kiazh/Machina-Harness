import {useEffect,useRef,useState} from 'react';
import type {OperationApproval} from '@machina/contracts/rpc';
import type {DesktopController} from '../../state/controller.ts';
import './operation-approvals.css';

function Approval({approval,controller,stopping}:{approval:OperationApproval;controller:DesktopController;stopping:boolean}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 async function resolve(verdict:'approved'|'denied'){
  if(busy||stopping)return;setBusy(true);setError('');
  try{await controller.operations.resolveApproval(approval.requestId,verdict)}catch(reason){if(alive.current)setError(reason instanceof Error?reason.message:String(reason))}finally{if(alive.current)setBusy(false)}
 }
 return <article className="operation-approval" aria-label={`Worker approval: ${approval.operation}`}>
  <div className="operation-approval-heading"><span>Worker permission</span><strong>{approval.operation}</strong></div><p>{approval.target}</p>
  {!!approval.argv?.length&&<pre className="operation-approval-command">{approval.argv.join(' ')}</pre>}
  {approval.cwd&&<p className="operation-approval-cwd">Folder: <code>{approval.cwd}</code></p>}
  {approval.proposal&&<details><summary>Reviewed action</summary><pre>{JSON.stringify(approval.proposal,null,2)}</pre></details>}
  {approval.expiresAt&&<p className="operation-approval-expiry">Expires {new Date(approval.expiresAt).toLocaleTimeString()}</p>}
  {error&&<p role="alert" className="inline-error">{error}</p>}
  <div className="operation-approval-actions"><p>Your decision applies to this action. Earlier workflow steps are kept.</p><button disabled={busy||stopping} onClick={()=>{void resolve('denied')}}>Deny</button><button className="primary-button" disabled={busy||stopping} onClick={()=>{void resolve('approved')}}>{busy?'Resolving…':'Allow once'}</button></div>
 </article>;
}
export function OperationApprovals({approvals,controller,stopping}:{approvals:OperationApproval[];controller:DesktopController;stopping:boolean}){
 return <section className="operation-approvals" aria-label="Workflow approvals">{approvals.map(approval=><Approval key={approval.requestId} approval={approval} controller={controller} stopping={stopping}/>)}</section>;
}
