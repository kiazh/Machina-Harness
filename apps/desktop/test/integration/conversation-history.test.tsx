// @vitest-environment jsdom
import {createRef} from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {ConversationHistory} from '../../src/features/sessions/ConversationHistory.tsx';
import {DesktopController} from '../../src/state/controller.ts';

const nativeShowModal=Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype,'showModal');
const nativeClose=Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype,'close');
beforeEach(()=>{
 Object.defineProperties(HTMLDialogElement.prototype,{
  showModal:{configurable:true,value:function(this:HTMLDialogElement){this.open=true}},
  close:{configurable:true,value:function(this:HTMLDialogElement){this.open=false;this.dispatchEvent(new Event('close'))}},
 });
 vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(520);
 vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800);
});
afterEach(()=>{
 cleanup();localStorage.clear();vi.restoreAllMocks();
 if(nativeShowModal)Object.defineProperty(HTMLDialogElement.prototype,'showModal',nativeShowModal);else Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');
 if(nativeClose)Object.defineProperty(HTMLDialogElement.prototype,'close',nativeClose);else Reflect.deleteProperty(HTMLDialogElement.prototype,'close');
});

const entry=(id:string,content:string,role:'user'|'assistant'|'tool'|'system'='assistant')=>({id,role,content,createdAt:'2026-11-02T12:00:00Z'});
const result=(content:string,nextCursor:string|null=null)=>({entries:[entry(content,content)],nextCursor});
function deferred(){let resolve!:(value:unknown)=>void;const promise=new Promise<unknown>(finish=>{resolve=finish});return {promise,resolve}}
async function fixture(options:{history?:(params:Record<string,unknown>)=>unknown;methods?:string[];session?:boolean}={}){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 let history=options.history??(()=>result('Newest recorded message'));
 const session=(id:string)=>({id,title:`Conversation ${id.slice(-1).toUpperCase()}`,model:'model',provider:'provider',status:'active',lastActivity:null,workspaceRoot:'/test'});
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){
  calls.push({method,params});
  if(method==='session.history')return await history(params) as T;
  const responses:Record<string,unknown>={
   'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:options.methods??['session.history']},
   initialize:{cwd:'/test',model:'model',provider:'provider'},'config.get':{config:{}},'session.list':{sessions:[session('session-a'),session('session-b')]},
   'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',trusted:true,capabilities:[]},
   'session.resume':{session:session(String(params.sessionId)),history:[]},'shell.jobs':{jobs:[]},
  };
  if(!(method in responses))throw new Error(`Unexpected ${method}`);
  return responses[method] as T;
 }});
 await controller.connect();if(options.session!==false)await controller.resume('session-a');
 const dialog=createRef<HTMLDialogElement>();
 return {controller,dialog,calls,historyCalls:()=>calls.filter(call=>call.method==='session.history'),setHistory:(next:typeof history)=>{history=next}};
}

it('waits until opened, requests the explicit conversation, and presents its newest page chronologically',async()=>{
 const f=await fixture({history:()=>({entries:[entry('new','Newer recorded message'),entry('old','Earlier recorded message','user')],nextCursor:null})});
 const view=render(<ConversationHistory controller={f.controller} dialog={f.dialog}/>);
 expect(f.dialog.current?.open).toBe(false);expect(f.historyCalls()).toEqual([]);
 view.rerender(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 await screen.findByText('Newer recorded message');
 expect(screen.getByRole('dialog',{name:'Conversation history'}).textContent).toContain('Conversation A');
 expect(screen.getAllByRole('article').map(row=>row.textContent)).toEqual([expect.stringContaining('Earlier recorded message'),expect.stringContaining('Newer recorded message')]);
 expect(f.historyCalls()).toEqual([{method:'session.history',params:{sessionId:'session-a',limit:100}}]);
 expect(f.calls.filter(call=>call.method==='session.resume')).toHaveLength(1);
 expect(f.calls.some(call=>['prompt.submit','models.list','providers.models'].includes(call.method))).toBe(false);
});

it('searches only on submission and keeps literal text and the page cursor scoped to that search',async()=>{
 const f=await fixture({history:params=>result(params.cursor?'Older matching message':params.query?'Literal match':'Recent message','opaque+/older=')});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);await screen.findByText('Recent message');
 fireEvent.change(screen.getByRole('searchbox',{name:'Search conversation history'}),{target:{value:'100%_literal[pattern]'}});
 expect(f.historyCalls()).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'Search'}));await screen.findByText('Literal match');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));await screen.findByText('Older matching message');
 expect(f.historyCalls().map(call=>call.params)).toEqual([
  {sessionId:'session-a',limit:100},
  {sessionId:'session-a',limit:100,query:'100%_literal[pattern]'},
  {sessionId:'session-a',limit:100,query:'100%_literal[pattern]',cursor:'opaque+/older='},
 ]);
});

it('applies an initial command query only on open and bounds submitted searches to 256 characters',async()=>{
 const f=await fixture();const view=render(<ConversationHistory controller={f.controller} dialog={f.dialog} initialQuery="literal %_needle"/>);
 expect(f.historyCalls()).toEqual([]);
 view.rerender(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1} initialQuery="literal %_needle"/>);
 await screen.findByText('Newest recorded message');expect(f.historyCalls()[0]?.params.query).toBe('literal %_needle');
 fireEvent.change(screen.getByRole('searchbox',{name:'Search conversation history'}),{target:{value:'a'.repeat(300)}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));await waitFor(()=>expect(f.historyCalls()).toHaveLength(2));
 expect(f.historyCalls()[1]?.params.query).toBe('a'.repeat(256));
 fireEvent.click(screen.getByRole('button',{name:'Close conversation history'}));
 view.rerender(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={2} initialQuery="new command query"/>);
 await waitFor(()=>expect(f.historyCalls()).toHaveLength(3));expect(f.historyCalls()[2]?.params.query).toBe('new command query');
});

it('navigates newer pages and resets the cursor stack when returning to latest',async()=>{
 const f=await fixture({history:params=>result(params.cursor?String(params.cursor):'Latest page',params.cursor==='page-two'?'page-three':'page-two')});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);await screen.findByText('Latest page');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));await screen.findByText('page-two');
 fireEvent.click(screen.getByRole('button',{name:'Newer'}));await screen.findByText('Latest page');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));await screen.findByText('page-two');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));await screen.findByText('page-three');
 fireEvent.click(screen.getByRole('button',{name:'Latest'}));await screen.findByText('Latest page');
 expect(f.historyCalls().map(call=>call.params.cursor)).toEqual([undefined,'page-two',undefined,'page-two','page-three',undefined]);
 expect((screen.getByRole('button',{name:'Newer'}) as HTMLButtonElement).disabled).toBe(true);
});

it('discards an older query reply after a newer search has completed',async()=>{
 const old=deferred();const f=await fixture({history:params=>params.query?result('Current search result'):old.promise});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 await waitFor(()=>expect(f.historyCalls()).toHaveLength(1));expect(screen.getByRole('status').textContent).toContain('Loading');
 fireEvent.change(screen.getByRole('searchbox',{name:'Search conversation history'}),{target:{value:'current'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));await screen.findByText('Current search result');
 await act(async()=>old.resolve(result('Stale search result')));
 expect(screen.queryByText('Stale search result')).toBeNull();expect(screen.getByText('Current search result')).toBeTruthy();
});

it('discards stale results even when the conversation changes from A to B and back to A',async()=>{
 const old=deferred();let requests=0;const f=await fixture({history:()=>++requests===1?old.promise:result('Current A history')});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 await waitFor(()=>expect(f.historyCalls()).toHaveLength(1));
 await act(async()=>{await f.controller.resume('session-b');await f.controller.resume('session-a')});
 await screen.findByText('Current A history');await act(async()=>old.resolve(result('Stale A history')));
 expect(screen.queryByText('Stale A history')).toBeNull();expect(screen.getByText('Current A history')).toBeTruthy();
});

it('invalidates a pending request on close and does not read history again until reopened',async()=>{
 const old=deferred();const f=await fixture({history:()=>old.promise});
 const view=render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 await waitFor(()=>expect(f.historyCalls()).toHaveLength(1));fireEvent.click(screen.getByRole('button',{name:'Close conversation history'}));
 await act(()=>f.controller.resume('session-b'));await act(async()=>old.resolve(result('Closed dialog history')));
 expect(f.historyCalls()).toHaveLength(1);expect(screen.queryByText('Closed dialog history')).toBeNull();
 f.setHistory(()=>result('Reopened B history'));
 view.rerender(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={2}/>);await screen.findByText('Reopened B history');
 expect(f.historyCalls().at(-1)?.params).toEqual({sessionId:'session-b',limit:100});
});

it('distinguishes failed history from empty history and retries the same conversation',async()=>{
 const f=await fixture({history:()=>{throw new Error('Stored history is unavailable')}});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 expect((await screen.findByRole('alert')).textContent).toContain('Stored history is unavailable');
 expect(screen.queryByText('No messages recorded in this conversation.')).toBeNull();
 f.setHistory(()=>({entries:[],nextCursor:null}));fireEvent.click(screen.getByRole('button',{name:'Retry'}));
 await screen.findByText('No messages recorded in this conversation.');expect(f.historyCalls()).toHaveLength(2);
});

it('keeps searching older pages when a bounded search page is empty but has a cursor',async()=>{
 const f=await fixture({history:params=>params.cursor?result('Older match'):{entries:[],nextCursor:'continue-search'}});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1} initialQuery="needle"/>);
 await screen.findByText('No matching messages in this page. Choose Older to continue searching.');
 expect(screen.queryByText('No recorded messages match this search.')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Older'}));await screen.findByText('Older match');
 expect(f.historyCalls().at(-1)?.params).toEqual({sessionId:'session-a',limit:100,query:'needle',cursor:'continue-search'});
});

it.each([null,'continue-search'])('does not claim a total no-match when some messages could not be fully searched: %s',async nextCursor=>{
 const f=await fixture({history:()=>({entries:[],nextCursor,unsearchedCount:2})});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1} initialQuery="needle"/>);
 await screen.findByText('No matches in the searchable previews on this page. Some messages need full export.');
 expect(screen.getByRole('note').textContent).toContain('2 messages could not be searched in full');
 expect(screen.queryByText('No recorded messages match this search.')).toBeNull();
 expect((screen.getByRole('button',{name:'Older'}) as HTMLButtonElement).disabled).toBe(nextCursor===null);
 fireEvent.click(screen.getByRole('button',{name:'Export full conversation'}));
 expect(f.controller.snapshot().uiAction).toMatchObject({name:'sessionTools',query:'export'});
});

it('shows search limitations alongside matching messages',async()=>{
 const f=await fixture({history:()=>({...result('Searchable matching preview'),unsearchedCount:1})});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1} initialQuery="needle"/>);
 await screen.findByText('Searchable matching preview');
 expect(screen.getByRole('note').textContent).toContain('1 message could not be searched in full');
});

it('retains search uncertainty across visited pages and replaces counts when revisiting a cursor',async()=>{
 const f=await fixture({history:params=>({entries:[],nextCursor:params.cursor?null:'second-page',unsearchedCount:params.cursor?0:2})});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1} initialQuery="needle"/>);
 await screen.findByRole('note');expect(screen.getByRole('note').textContent).toContain('2 messages could not be searched in full');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Older'}).hasAttribute('disabled')).toBe(true));
 expect(screen.getByRole('note').textContent).toContain('2 messages could not be searched in full across the pages visited in this search');
 expect(screen.queryByText('No recorded messages match this search.')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Newer'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Older'}).hasAttribute('disabled')).toBe(false));
 expect(screen.getByRole('note').textContent).toContain('2 messages could not be searched in full');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Older'}).hasAttribute('disabled')).toBe(true));
 f.setHistory(params=>({entries:[],nextCursor:params.cursor?null:'second-page',unsearchedCount:params.cursor?3:1}));
 fireEvent.click(screen.getByRole('button',{name:'Newer'}));
 await waitFor(()=>expect(screen.getByRole('note').textContent).toContain('1 message could not be searched in full'));
 fireEvent.click(screen.getByRole('button',{name:'Older'}));
 await waitFor(()=>expect(screen.getByRole('note').textContent).toContain('4 messages could not be searched in full'));
 f.setHistory(()=>({entries:[],nextCursor:null,unsearchedCount:0}));
 fireEvent.change(screen.getByRole('searchbox',{name:'Search conversation history'}),{target:{value:'another search'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByText('No recorded messages match this search.');expect(screen.queryByRole('note')).toBeNull();
});

it('clears visited-page uncertainty when Latest starts a fresh search run',async()=>{
 const f=await fixture({history:params=>({entries:[],nextCursor:params.cursor?null:'second-page',unsearchedCount:params.cursor?3:0})});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1} initialQuery="needle"/>);
 await screen.findByText('No matching messages in this page. Choose Older to continue searching.');
 fireEvent.click(screen.getByRole('button',{name:'Older'}));await screen.findByRole('note');
 fireEvent.click(screen.getByRole('button',{name:'Latest'}));
 await screen.findByText('No matching messages in this page. Choose Older to continue searching.');
 expect(screen.queryByRole('note')).toBeNull();
});

it.each([{session:false,methods:['session.history'],message:/Open a conversation/},{session:true,methods:['session.list'],message:/does not support conversation history/}])('explains unavailable history without making a request: $session / $methods',async options=>{
 const f=await fixture(options);render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 await screen.findByText(options.message);expect(f.historyCalls()).toEqual([]);
});

it('bounds long readonly previews, preserves tool metadata, and points to full export',async()=>{
 const f=await fixture({history:()=>({entries:[{...entry('tool','<script>literal text</script>\n'+'Long tool output. '.repeat(1000),'tool'),tool:'Read',truncated:true}],nextCursor:null})});
 render(<ConversationHistory controller={f.controller} dialog={f.dialog} openToken={1}/>);
 const row=await screen.findByRole('article');expect(within(row).getByText('Read')).toBeTruthy();
 expect(row.querySelector('pre')!.textContent!.length).toBeLessThan(5000);expect(row.querySelector('script')).toBeNull();
 expect(row.textContent).toContain('Preview truncated');
 fireEvent.click(screen.getByRole('button',{name:'Export full conversation'}));
 expect(f.controller.snapshot().uiAction).toMatchObject({name:'sessionTools',query:'export'});expect(f.dialog.current?.open).toBe(false);
});
