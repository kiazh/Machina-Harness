// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {HarnessCommunications,mergeHarnessEvents,type HarnessEvent} from '../../src/features/settings/HarnessCommunications.tsx';
import {DesktopController} from '../../src/state/controller.ts';
import {chooseSelect} from '../helpers/select.ts';

afterEach(cleanup);
const event:HarnessEvent={id:'1',correlationId:'exchange',direction:'incoming',phase:'request',transport:'mcp',method:'machina_status',harness:'Test harness',sessionId:null,status:'running',summary:'Inspect workspace',payload:'{"request":"status"}',truncated:false,createdAt:'2026-11-02T20:00:00Z',elapsedMs:null};
const response={...event,id:'2',direction:'outgoing' as const,phase:'response' as const,status:'completed' as const,payload:'{"ok":true}',elapsedMs:42};
async function fixture(traffic:unknown={events:[event,response],nextCursor:'2',hasMore:false}){
 const calls:string[]=[];const results:Record<string,unknown>={'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},'interop.communications':traffic};
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string){calls.push(method);const result=results[method];if(result instanceof Error)throw result;return result as T}});await controller.connect();return {controller,calls};
}
it('shows real directions and paired request/response details with no model call',async()=>{
 const f=await fixture();render(<HarnessCommunications controller={f.controller}/>);
 await screen.findByRole('button',{name:/From Machina.*machina_status/});
 fireEvent.click(screen.getByRole('button',{name:/From Machina.*machina_status/}));
 const details=screen.getByRole('region',{name:'Exchange details'});expect(details.textContent).toContain('To Machina · request');expect(details.textContent).toContain('From Machina · response');expect(details.textContent).toContain('"ok":true');
 chooseSelect(screen.getByRole('combobox',{name:'Communication status'}),'running');expect(screen.getByText('No exchanges match these filters.')).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:'Clear filters'}));
 expect(f.calls.some(method=>method==='prompt.submit'||method==='session.create')).toBe(false);
});
it('shows a failed load honestly and does not pretend the recording is empty',async()=>{
 const f=await fixture(new Error('Recording unavailable'));render(<HarnessCommunications controller={f.controller}/>);
 await screen.findByRole('alert');expect(screen.getByRole('alert').textContent).toContain('Recording unavailable');expect(screen.queryByText('No communications recorded yet')).toBeNull();
});
it('bounds and deduplicates live event batches',()=>{
 const many=Array.from({length:400},(_,index)=>({...event,id:String(index+1)}));
 const merged=mergeHarnessEvents(many.slice(0,250),many.slice(200));expect(merged).toHaveLength(200);expect(merged[0]!.id).toBe('201');expect(merged.at(-1)!.id).toBe('400');
});
