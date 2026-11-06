import {afterEach,expect,it,vi} from 'vitest';
import {OperationStore} from '../../src/state/operations.ts';
import type {OperationSnapshot} from '@machina/contracts/rpc';
import type {DesktopBridge} from '../../src/bridge/types.ts';

afterEach(()=>vi.useRealTimers());
function fixture(){
 vi.useFakeTimers();
 const run={id:'operation-a',method:'agents.run' as const,sessionId:'session-a',status:'running' as const,cancellable:true,startedAt:'2026-11-02T12:00:00Z'};
 const request=vi.fn(async(method:string):Promise<OperationSnapshot>=>method==='operations.start'?run:method==='operations.cancel'?{...run,status:'cancelling'}:run);
 const store=new OperationStore({request} as unknown as DesktopBridge);
 return {store,request,run};
}

it('does not revive a resolved child approval from an older in-flight status read',async()=>{
 const f=fixture();const approval={requestId:'a',sessionId:'child',operation:'shell.exec',target:'test'};
 let oldReply!:(value:OperationSnapshot)=>void;let reads=0;
 f.request.mockImplementation(async(method:string)=>method==='operations.start'?{...f.run,approvals:[approval]}:method==='approvals.resolve'?f.run:++reads===1?await new Promise<OperationSnapshot>(resolve=>{oldReply=resolve}):{...f.run,approvals:[]});
 await f.store.start('agents.run',{sessionId:'session-a'});await vi.advanceTimersByTimeAsync(1000);
 await f.store.resolveApproval('a','approved');await vi.advanceTimersByTimeAsync(0);
 expect(f.store.snapshot().active?.approvals).toEqual([]);
 oldReply({...f.run,approvals:[approval]});await vi.advanceTimersByTimeAsync(0);
 expect(f.store.snapshot().active?.approvals).toEqual([]);f.store.dispose();
});

it('binds concurrent approval resolution to one child and ignores completion after stop',async()=>{
 const f=fixture();const approval={requestId:'a',sessionId:'child',operation:'shell.exec',target:'test'};
 let finish!:(value:OperationSnapshot)=>void;
 f.request.mockImplementation(async(method:string)=>method==='operations.start'?{...f.run,approvals:[approval]}:method==='approvals.resolve'?await new Promise<OperationSnapshot>(resolve=>{finish=resolve}):{...f.run,status:'cancelled',approvals:[]});
 await f.store.start('agents.run',{sessionId:'session-a'});
 const resolving=f.store.resolveApproval('a','approved');await expect(f.store.resolveApproval('a','approved')).rejects.toThrow(/already being resolved/);
 await f.store.cancel();finish(f.run);await resolving;
 expect(f.store.snapshot().active).toBeUndefined();expect(f.request.mock.calls.filter(([method])=>method==='approvals.resolve')).toHaveLength(1);f.store.dispose();
});
it('starts once, polls independently of views, and retains settled output',async()=>{
 const f=fixture();await f.store.start('agents.run',{sessionId:'session-a',steps:[{agent:'researcher',task:'Inspect parser'}]});
 expect(f.store.snapshot().active?.id).toBe('operation-a');
 await expect(f.store.start('agents.run',{})).rejects.toThrow(/already running/);
 f.request.mockImplementation(async()=>({...f.run,status:'completed',result:{results:[{response:'Inspected'}]}}));
 await vi.advanceTimersByTimeAsync(1000);
 expect(f.store.snapshot().active).toBeUndefined();
 expect(f.store.snapshot().entries[0].result).toEqual({results:[{response:'Inspected'}]});
 expect(f.store.snapshot().entries[0].params.sessionId).toBe('session-a');
 await vi.advanceTimersByTimeAsync(10000);expect(f.request).toHaveBeenCalledTimes(2);f.store.dispose();
});
it('does not treat a stop request as confirmed cancellation',async()=>{
 const f=fixture();await f.store.start('agents.run',{sessionId:'session-a'});await f.store.cancel();
 expect(f.store.snapshot().active?.status).toBe('cancelling');
 expect(f.request).toHaveBeenLastCalledWith('operations.cancel',{id:'operation-a',sessionId:'session-a'});f.store.dispose();
});
it('keeps tracking after a failed status read without repeating the operation',async()=>{
 const f=fixture();await f.store.start('agents.run',{sessionId:'session-a'});
 f.request.mockRejectedValueOnce(new Error('Temporarily unavailable'));
 await vi.advanceTimersByTimeAsync(1000);
 expect(f.store.snapshot().active?.id).toBe('operation-a');expect(f.store.snapshot().error).toMatch(/Temporarily/);
 await vi.advanceTimersByTimeAsync(3000);
 expect(f.request.mock.calls.filter(([method])=>method==='operations.start')).toHaveLength(1);f.store.dispose();
});
it('marks disconnected work as unknown and stops polling rather than claiming success',async()=>{
 const f=fixture();await f.store.start('agents.run',{sessionId:'session-a'});f.store.disconnected();
 expect(f.store.snapshot().active).toBeUndefined();expect(f.store.snapshot().entries[0].status).toBe('unknown');
 await vi.advanceTimersByTimeAsync(10000);expect(f.request).toHaveBeenCalledTimes(1);f.store.dispose();
});
it('does not advertise cancellation for an external worker without an owned stop handle',async()=>{
 const f=fixture();f.request.mockResolvedValueOnce({...f.run,cancellable:false});await f.store.start('interop.run',{});
 await expect(f.store.cancel()).rejects.toThrow(/cannot be stopped/);expect(f.request).toHaveBeenCalledTimes(1);f.store.dispose();
});

it('does not revive a settled run when an older cancellation reply arrives',async()=>{
 const f=fixture();await f.store.start('agents.run',{sessionId:'session-a'});
 let finish!:(value:OperationSnapshot)=>void;
 f.request.mockImplementation(async(method:string)=>method==='operations.cancel'?new Promise(resolve=>{finish=resolve}):{...f.run,status:'completed',result:{results:[]}});
 const stopping=f.store.cancel();await vi.advanceTimersByTimeAsync(1000);
 expect(f.store.snapshot().active).toBeUndefined();
 finish({...f.run,status:'cancelling'});await stopping;
 expect(f.store.snapshot().active).toBeUndefined();expect(f.store.snapshot().entries[0].status).toBe('completed');f.store.dispose();
});

it('does not revive a cancelled run when an earlier status read returns late',async()=>{
 const f=fixture();await f.store.start('agents.run',{sessionId:'session-a'});
 let finish!:(value:OperationSnapshot)=>void;
 f.request.mockImplementation(async(method:string)=>method==='operations.get'?new Promise(resolve=>{finish=resolve}):{...f.run,status:'cancelled'});
 await vi.advanceTimersByTimeAsync(1000);await f.store.cancel();
 expect(f.store.snapshot().active).toBeUndefined();finish(f.run);await vi.advanceTimersByTimeAsync(1);
 expect(f.store.snapshot().active).toBeUndefined();expect(f.store.snapshot().entries[0].status).toBe('cancelled');f.store.dispose();
});

it('recovers a lost start reply with the caller UUID and never repeats the task',async()=>{
 const f=fixture();f.request.mockRejectedValueOnce(new Error('Start acknowledgement lost'));
 await expect(f.store.start('agents.run',{sessionId:'session-a'})).rejects.toThrow(/lost/);
 const id=f.store.snapshot().active?.id;expect(id).toMatch(/^[\da-f-]{36}$/);expect(f.store.snapshot().active?.status).toBe('unknown');
 f.request.mockResolvedValueOnce({...f.run,id:id!,status:'completed',result:{results:[]}});
 await vi.advanceTimersByTimeAsync(1000);
 expect(f.request).toHaveBeenLastCalledWith('operations.get',{id,sessionId:'session-a',missingOk:true});
 expect(f.store.snapshot().active).toBeUndefined();expect(f.store.snapshot().entries[0].status).toBe('completed');
 expect(f.request.mock.calls.filter(([method])=>method==='operations.start')).toHaveLength(1);f.store.dispose();
});

it('releases an unconfirmed start only when the engine confirms it was not admitted',async()=>{
 const f=fixture();f.request.mockRejectedValueOnce(new Error('Invalid task'));await expect(f.store.start('agents.run',{})).rejects.toThrow();
 f.request.mockResolvedValueOnce(null as unknown as OperationSnapshot);await vi.advanceTimersByTimeAsync(1000);
 expect(f.store.snapshot().active).toBeUndefined();expect(f.store.snapshot().entries[0].status).toBe('failed');f.store.dispose();
});
