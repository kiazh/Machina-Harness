// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';
import {OperationStatus} from '../../src/features/shared/OperationStatus.tsx';

const controllers:DesktopController[]=[];
afterEach(()=>{cleanup();for(const controller of controllers)controller.dispose();controllers.length=0});
async function fixture(){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 const approval={requestId:'approval-a',sessionId:'child-session',agentId:'worker',operation:'shell.exec',target:'Run tests',argv:['python','-m','pytest'],cwd:'/test',proposal:{backend:'host',timeout:60}};
 const session={id:'parent-session',title:'Parent',model:'m',provider:'p',status:'active',lastActivity:null};
 const operation={id:'operation-a',method:'agents.run',sessionId:session.id,status:'running',cancellable:true,startedAt:'2026-11-03T00:00:00Z',approvals:[approval]};
 const responses:Record<string,unknown>={'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:['operations.start','operations.get','operations.cancel','approvals.resolve']},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[session]},'session.resume':{session,history:[]},'shell.jobs':{jobs:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},'operations.start':operation,'operations.get':operation,'operations.cancel':{...operation,status:'cancelled',approvals:[]},'approvals.resolve':{resolved:true}};
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){calls.push({method,params});const raw=responses[method],value=await raw;if(value instanceof Error)throw value;return value as T}});controllers.push(controller);await controller.connect();await controller.resume(session.id);await controller.runOperation('agents.run',{steps:[{agent:'worker',task:'test'}]});return {controller,calls,responses,operation};
}
it('recovers child approval details on remount and resolves once with child ownership',async()=>{
 const f=await fixture();const first=render(<OperationStatus controller={f.controller} method="agents.run"/>);
 await screen.findByText(/Waiting for approval/);first.unmount();render(<OperationStatus controller={f.controller} method="agents.run"/>);
 expect(screen.getByText('Run tests')).toBeTruthy();fireEvent.click(screen.getByText('Reviewed action'));expect(screen.getByText(/"backend": "host"/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Allow once'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='approvals.resolve')?.params).toEqual({sessionId:'child-session',requestId:'approval-a',verdict:'approved',grant:'once'}));
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Allow once'})).toBeNull());expect(f.calls.filter(call=>call.method==='operations.start')).toHaveLength(1);
});
it('denies through the child scope and retains an actionable card when resolution fails',async()=>{
 const f=await fixture();f.responses['approvals.resolve']=new Error('Approval expired');render(<OperationStatus controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Deny'}));await screen.findByRole('alert');expect(screen.getByRole('alert').textContent).toContain('Approval expired');
 expect(f.calls.find(call=>call.method==='approvals.resolve')?.params).toMatchObject({sessionId:'child-session',verdict:'denied'});expect(screen.getByRole('button',{name:'Deny'})).toBeTruthy();
});
