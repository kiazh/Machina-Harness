import {useSyncExternalStore} from 'react';
import type {DesktopController} from '../../state/controller.ts';

export function OperationStatus({controller,method}:{controller:DesktopController;method?:'agents.run'|'interop.run'}){
 const state=useSyncExternalStore(controller.operations.subscribe,controller.operations.snapshot);
 const operation=state.active;if(!operation||method&&operation.method!==method)return null;
 return <section className="operation-status" aria-label="Background run">
  <div><strong>{operation.method==='agents.run'?'Delegation workflow':'External harness'} · {operation.status==='cancelling'?'Stopping…':operation.status==='unknown'?'Checking start…':operation.status==='starting'?'Starting…':'Running'}</strong><p>You can continue browsing. Results will stay available on this page.</p></div>
  {operation.cancellable&&<button disabled={operation.status==='cancelling'} onClick={()=>{void controller.operations.cancel().catch(error=>controller.fail(error))}}>Stop background run</button>}
  {!operation.cancellable&&!['starting','unknown'].includes(operation.status)&&<p>This external run cannot be stopped from Machina. Its engine deadline still applies.</p>}
  {state.error&&<p role="alert">Status unavailable: {state.error}. The run has not been repeated.</p>}
 </section>;
}
