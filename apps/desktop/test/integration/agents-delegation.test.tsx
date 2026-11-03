// @vitest-environment jsdom
import {chooseSelect} from '../helpers/select.ts';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {AgentsPage} from '../../src/features/agents/AgentsPage.tsx';
import {DesktopController} from '../../src/state/controller.ts';

afterEach(()=>{cleanup();localStorage.clear()});
const researcher={name:'researcher',description:'Inspect the code',systemPrompt:'Read carefully',tools:[],model:null,provider:null,source:'builtin'};
const reviewer={...researcher,name:'reviewer',description:'Review the findings'};
const completed={agent:'researcher',task:'Inspect parser',response:'Parser inspected',sessionId:'child-a',status:'complete',tokens:42,iterations:1,needsApproval:[]};
async function fixture(extra:Record<string,unknown>={},methods:string[]=[]){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 const session={id:'session-a',model:'model',provider:'provider',title:'Conversation A',status:'active',lastActivity:null};
 const responses:Record<string,unknown>={
  'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods},
  initialize:{cwd:'/test',model:'model',provider:'provider'},'config.get':{config:{}},'session.list':{sessions:[session]},
  'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',trusted:true,capabilities:[]},
  'session.resume':{session,history:[]},'shell.jobs':{jobs:[]},'agents.list':{agents:[researcher,reviewer]},
  'agents.run':{results:[completed]},'agents.history':{messages:[]},...extra,
 };
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){
  calls.push({method,params});if(!(method in responses))throw new Error(`Unexpected ${method}`);
  const value=await responses[method];if(value instanceof Error)throw value;return value as T;
 }});
 await controller.connect();await controller.resume(session.id);
 return {controller,calls,responses,async switchSession(){responses['session.resume']={session:{...session,id:'session-b',title:'Conversation B'},history:[]};await act(()=>controller.resume('session-b'))}};
}
async function openTask(){fireEvent.click((await screen.findAllByRole('button',{name:'Delegate task'}))[0]);fireEvent.change(screen.getByRole('textbox',{name:'Task'}),{target:{value:'Inspect parser'}})}
function addReview(){fireEvent.click(screen.getByRole('button',{name:'Add step'}));chooseSelect(screen.getByRole('combobox',{name:'Agent 2'}),'reviewer');fireEvent.change(screen.getByRole('textbox',{name:'Task 2'}),{target:{value:'Review findings'}})}

it('keeps the single-task flow and submits its explicit sequential mode and parent scope only once',async()=>{
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve});
 const f=await fixture({'agents.run':pending});render(<AgentsPage controller={f.controller}/>);await openTask();
 const form=screen.getByRole('button',{name:'Run task'}).closest('form')!;
 fireEvent.submit(form);fireEvent.submit(form);
 expect(f.calls.filter(call=>call.method==='agents.run')).toEqual([{method:'agents.run',params:{sessionId:'session-a',mode:'sequential',steps:[{agent:'researcher',task:'Inspect parser'}]}}]);
 expect(screen.getByRole('status').textContent).toMatch(/working/i);
 await act(async()=>finish({results:[completed]}));
 expect((await screen.findByRole('region',{name:'Delegation results'})).textContent).toMatch(/Parser inspected/);
 expect(screen.getByText(/42 tokens/)).toBeTruthy();
 expect(f.calls.some(call=>['prompt.submit','providers.models','models.list'].includes(call.method))).toBe(false);
});

it.each(['sequential','parallel'])('runs an edited %s workflow with only its remaining steps',async(mode)=>{
 const f=await fixture({'agents.run':{results:[completed,{...completed,agent:'reviewer',task:'Review findings',response:'Reviewed'}]}});
 render(<AgentsPage controller={f.controller}/>);await openTask();addReview();
 chooseSelect(screen.getByRole('combobox',{name:'Execution'}),mode);
 expect(screen.getByText(mode==='parallel'?/do not receive each other’s results/:/receives earlier results/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Add step'}));
 fireEvent.change(screen.getByRole('textbox',{name:'Task 3'}),{target:{value:'Do not run this'}});
 fireEvent.click(screen.getByRole('button',{name:'Remove step 3'}));
 fireEvent.click(screen.getByRole('button',{name:'Run workflow'}));
 await screen.findByText('Reviewed');
 expect(f.calls.find(call=>call.method==='agents.run')?.params).toEqual({sessionId:'session-a',mode,steps:[{agent:'researcher',task:'Inspect parser'},{agent:'reviewer',task:'Review findings'}]});
});

it.each(['rpc','result'])('retains every task and execution choice after a %s failure',async(kind)=>{
 const f=await fixture({'agents.run':kind==='rpc'?new Error('Delegation unavailable'):{results:[{...completed,status:'error',response:'Worker failed'}]}});
 render(<AgentsPage controller={f.controller}/>);await openTask();addReview();
 chooseSelect(screen.getByRole('combobox',{name:'Execution'}),'parallel');
 fireEvent.click(screen.getByRole('button',{name:'Run workflow'}));
 await screen.findByText(kind==='rpc'?'Delegation unavailable':'Worker failed');
 expect((screen.getByRole('textbox',{name:'Task'}) as HTMLTextAreaElement).value).toBe('Inspect parser');
 expect((screen.getByRole('textbox',{name:'Task 2'}) as HTMLTextAreaElement).value).toBe('Review findings');
 expect(screen.getByRole('combobox',{name:'Execution'}).textContent).toContain('Parallel');
 expect((screen.getByRole('button',{name:'Run workflow'}) as HTMLButtonElement).disabled).toBe(false);
});

it('shows approval details and the sequential steps that have not started',async()=>{
 const f=await fixture({'agents.run':{results:[{...completed,status:'waiting_approval',response:'Permission needed',needsApproval:[{requestId:'approval-a',operation:'shell.execute',target:'npm test',status:'pending',sessionId:'child-a',agentId:'worker-a'}]}]}});
 render(<AgentsPage controller={f.controller}/>);await openTask();addReview();fireEvent.click(screen.getByRole('button',{name:'Run workflow'}));
 const results=await screen.findByRole('region',{name:'Delegation results'});
 expect(within(results).getByText(/Waiting for approval/)).toBeTruthy();
 expect(within(results).getByText('shell.execute')).toBeTruthy();expect(within(results).getByText('npm test')).toBeTruthy();
 expect(within(results).getByText(/1 step has not started/)).toBeTruthy();
 expect(within(results).getByText(/cannot resume this paused workflow/)).toBeTruthy();
 fireEvent.click(within(results).getByText('Request details'));
 expect(within(results).getByText('child-a')).toBeTruthy();
 fireEvent.click(within(results).getByRole('button',{name:'Configure conversation permissions'}));
 expect(f.controller.snapshot().page).toBe('mode');expect(f.controller.snapshot().session?.id).toBe('session-a');
 expect(f.calls.filter(call=>call.method==='session.resume')).toHaveLength(1);
 expect(f.calls.some(call=>call.method==='approvals.resolve')).toBe(false);
});

it('does not show a previous conversation’s late results or clear the new conversation’s draft',async()=>{
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve});
 const f=await fixture({'agents.run':pending});render(<AgentsPage controller={f.controller}/>);await openTask();fireEvent.click(screen.getByRole('button',{name:'Run task'}));
 await f.switchSession();await act(async()=>finish({results:[{...completed,response:'Old conversation result'}]}));
 await openTask();fireEvent.change(screen.getByRole('textbox',{name:'Task'}),{target:{value:'New conversation draft'}});
 expect(screen.queryByText('Old conversation result')).toBeNull();
 expect((screen.getByRole('textbox',{name:'Task'}) as HTMLTextAreaElement).value).toBe('New conversation draft');
 expect(f.calls.filter(call=>call.method==='agents.run')).toHaveLength(1);
});

it('removes settled delegation results when another conversation is opened',async()=>{
 const f=await fixture();render(<AgentsPage controller={f.controller}/>);await openTask();fireEvent.click(screen.getByRole('button',{name:'Run task'}));
 await screen.findByText('Parser inspected');await f.switchSession();
 expect(screen.queryByRole('region',{name:'Delegation results'})).toBeNull();
 expect(screen.queryByRole('textbox',{name:'Task'})).toBeNull();
});

it('does not submit empty tasks or a delegation without a parent conversation',async()=>{
 const f=await fixture();render(<AgentsPage controller={f.controller}/>);await openTask();
 fireEvent.change(screen.getByRole('textbox',{name:'Task'}),{target:{value:'   '}});
 fireEvent.submit(screen.getByRole('form',{name:'Delegation workflow'}));
 expect(f.calls.some(call=>call.method==='agents.run')).toBe(false);
 act(()=>f.controller.newSession());await openTask();
 expect((screen.getByRole('button',{name:'Run task'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.submit(screen.getByRole('form',{name:'Delegation workflow'}));
 expect(f.calls.some(call=>call.method==='agents.run')).toBe(false);
});

it('caps editable workflows at the engine’s sixteen-step limit',async()=>{
 const f=await fixture();render(<AgentsPage controller={f.controller}/>);await openTask();
 for(let step=2;step<=16;step++)fireEvent.click(screen.getByRole('button',{name:'Add step'}));
 expect((screen.getByRole('button',{name:'Add step'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.getAllByRole('combobox',{name:/^Agent/})).toHaveLength(16);
 fireEvent.click(screen.getByRole('button',{name:'Remove step 16'}));
 expect((screen.getByRole('button',{name:'Add step'}) as HTMLButtonElement).disabled).toBe(false);
});

it('does not label definition changes as running delegation work',async()=>{
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve});
 const f=await fixture({'agents.save':pending});render(<AgentsPage controller={f.controller}/>);await openTask();
 fireEvent.click(screen.getByRole('button',{name:'Create agent'}));
 fireEvent.change(screen.getByRole('textbox',{name:'Name'}),{target:{value:'new-agent'}});
 fireEvent.click(screen.getByRole('button',{name:'Save agent'}));
 await waitFor(()=>expect((screen.getByRole('button',{name:'Saving…'}) as HTMLButtonElement).disabled).toBe(true));
 expect(screen.queryByText(/Agents working/)).toBeNull();
 await act(async()=>finish({agent:{...researcher,name:'new-agent',source:'db'}}));
});

it('loads delegation history on demand with the current session and preserves recorded statuses',async()=>{
 const f=await fixture({'agents.history':{messages:[{id:'message-a',fromAgent:'orchestrator',toAgent:'reviewer',task:'Inspect changes',response:null,status:'running',tokens:0,createdAt:'2026-11-02T12:00:00Z'},{id:'message-b',fromAgent:'orchestrator',toAgent:'researcher',task:'Find tests',response:'Tests located',status:'complete',tokens:15,createdAt:null}]}});
 render(<AgentsPage controller={f.controller}/>);await screen.findAllByRole('button',{name:'Delegate task'});
 expect(f.calls.some(call=>call.method==='agents.history')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'View delegation history'}));
 const history=await screen.findByRole('region',{name:'Delegation history'});await within(history).findByText('Tests located');
 expect(within(history).getByText(/Running/)).toBeTruthy();expect(within(history).getByText('Inspect changes')).toBeTruthy();
 expect(f.calls.find(call=>call.method==='agents.history')?.params).toEqual({sessionId:'session-a',limit:100});
});

it('ignores late history from a previously opened conversation',async()=>{
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve});
 const f=await fixture({'agents.history':pending});render(<AgentsPage controller={f.controller}/>);
 fireEvent.click(screen.getByRole('button',{name:'View delegation history'}));
 await waitFor(()=>expect(f.calls.filter(call=>call.method==='agents.history')).toHaveLength(1));
 await f.switchSession();f.responses['agents.history']={messages:[]};
 fireEvent.click(screen.getByRole('button',{name:'View delegation history'}));
 await screen.findByText('No delegations recorded in this conversation.');
 await act(async()=>finish({messages:[{id:'old',fromAgent:'orchestrator',toAgent:'reviewer',task:'Old task',response:'Old history',status:'complete',tokens:0,createdAt:null}]}));
 expect(screen.queryByText('Old history')).toBeNull();
 expect(f.calls.filter(call=>call.method==='agents.history').map(call=>call.params.sessionId)).toEqual(['session-a','session-b']);
});

it('explains missing history support without requesting it or claiming the history is empty',async()=>{
 const f=await fixture({},['agents.list','agents.run']);render(<AgentsPage controller={f.controller}/>);
 fireEvent.click(screen.getByRole('button',{name:'View delegation history'}));
 expect((await screen.findByRole('region',{name:'Delegation history'})).textContent).toMatch(/does not support/);
 expect(f.calls.some(call=>call.method==='agents.history')).toBe(false);
 expect(screen.queryByText('No delegations recorded in this conversation.')).toBeNull();
});

it('keeps history failures distinct from an empty history and allows a scoped retry',async()=>{
 const f=await fixture({'agents.history':new Error('History could not be read')});render(<AgentsPage controller={f.controller}/>);
 fireEvent.click(screen.getByRole('button',{name:'View delegation history'}));
 expect((await screen.findByRole('alert')).textContent).toBe('History could not be read');
 expect(screen.queryByText('No delegations recorded in this conversation.')).toBeNull();
 f.responses['agents.history']={messages:[]};fireEvent.click(screen.getByRole('button',{name:'Refresh delegation history'}));
 await screen.findByText('No delegations recorded in this conversation.');
 expect(f.calls.filter(call=>call.method==='agents.history')).toHaveLength(2);
});

it('keeps a tracked workflow and its result when the user leaves and reopens Agents',async()=>{
 const operation={id:'run-a',method:'agents.run',sessionId:'session-a',status:'running',cancellable:true,startedAt:'2026-11-02T00:00:00Z'};
 const f=await fixture({'operations.start':operation,'operations.get':{...operation,status:'completed',result:{results:[completed]}}},['agents.list','agents.run','operations.start','operations.get','operations.cancel']);
 const view=render(<AgentsPage controller={f.controller}/>);await openTask();fireEvent.click(screen.getByRole('button',{name:'Run task'}));
 await waitFor(()=>expect(f.controller.snapshot().backgroundOperation?.id).toBe('run-a'));
 view.unmount();render(<AgentsPage controller={f.controller}/>);
 expect((await screen.findByRole('region',{name:'Background run'})).textContent).toMatch(/continue browsing/);
 await screen.findByText('Parser inspected',{}, {timeout:3000});
 expect(f.calls.filter(call=>call.method==='operations.start')).toHaveLength(1);
 expect(f.calls.some(call=>call.method==='agents.run')).toBe(false);f.controller.dispose();
});
