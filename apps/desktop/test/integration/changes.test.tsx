// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';
import {ChangesPage} from '../../src/features/changes/ChangesPage.tsx';

afterEach(cleanup);
const main={path:'src/main.py',status:'MM',staged:true,unstaged:true,untracked:false,exists:true};
const added={path:'notes.txt',status:'??',staged:false,unstaged:false,untracked:true,exists:true};
const changes={workspaceId:'w',workspaceEpoch:1,state:'ready',branch:'work',entries:[main,added],truncated:false};
const diff={workspaceId:'w',workspaceEpoch:1,path:main.path,target:'working',patch:'--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new\n',binary:false,truncated:false};
async function fixture(extra:Record<string,unknown>={}){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 const responses:Record<string,unknown>={'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},'workspace.changes':changes,'workspace.diff':(params:Record<string,unknown>)=>({...diff,path:params.path,target:params.target}),...extra};
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){calls.push({method,params});const raw=responses[method],result=await(typeof raw==='function'?raw(params):raw);if(result instanceof Error)throw result;return result as T}});await controller.connect();
 return {controller,calls,responses};
}
it('loads a workspace-bound disk diff, distinguishes staged work and attaches only to the draft',async()=>{
 const f=await fixture();render(<ChangesPage controller={f.controller}/>);
 await screen.findByRole('button',{name:'Inspect src/main.py'});
 await screen.findByRole('region',{name:'Diff for src/main.py'});
 expect(f.calls.find(call=>call.method==='workspace.changes')?.params).toMatchObject({workspaceId:'w',epoch:1,clientId:'c'});
 fireEvent.click(screen.getByRole('button',{name:'Staged'}));
 await waitFor(()=>expect((screen.getByRole('button',{name:'Attach diff to draft'}) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(screen.getByRole('button',{name:'Attach diff to draft'}));
 expect(f.controller.snapshot().composerInsertion?.text).toContain('<attached diff: src/main.py (HEAD to index)>');
 expect(f.calls.some(call=>call.method==='prompt.submit')).toBe(false);
});
it('does not replace a newly selected diff with an old in-flight reply',async()=>{
 let finish!:(value:unknown)=>void;
 const f=await fixture({'workspace.diff':(params:Record<string,unknown>)=>params.path===main.path?new Promise(resolve=>{finish=resolve}):{...diff,path:added.path,patch:'@@ -0,0 +1 @@\n+note\n'}});
 render(<ChangesPage controller={f.controller}/>);await screen.findByRole('button',{name:'Inspect notes.txt'});
 fireEvent.click(screen.getByRole('button',{name:'Inspect notes.txt'}));await screen.findByRole('region',{name:'Diff for notes.txt'});
 await act(async()=>finish(diff));expect(screen.queryByRole('region',{name:'Diff for src/main.py'})).toBeNull();
});

it('refreshes inventory before requesting one fresh diff without a redundant old read',async()=>{
 const f=await fixture();render(<ChangesPage controller={f.controller}/>);await screen.findByRole('region',{name:'Diff for src/main.py'});
 let finish!:(value:unknown)=>void;f.responses['workspace.changes']=new Promise(resolve=>{finish=resolve});
 fireEvent.click(screen.getByRole('button',{name:'Refresh'}));await act(async()=>{});
 expect(f.calls.filter(call=>call.method==='workspace.diff')).toHaveLength(1);
 await act(async()=>finish({...changes}));await screen.findByRole('region',{name:'Diff for src/main.py'});
 expect(f.calls.filter(call=>call.method==='workspace.diff')).toHaveLength(2);
});
it('distinguishes no repository, clean state and failed inventory from each other',async()=>{
 const f=await fixture({'workspace.changes':{...changes,state:'not_repository',entries:[]}});
 const rendered=render(<ChangesPage controller={f.controller}/>);await screen.findByText('This folder is not in a Git repository.');rendered.unmount();
 f.responses['workspace.changes']={...changes,entries:[]};const clean=render(<ChangesPage controller={f.controller}/>);await screen.findByText('Your working tree is clean.');clean.unmount();
 f.responses['workspace.changes']=new Error('Git read failed');render(<ChangesPage controller={f.controller}/>);await screen.findByRole('alert');expect(screen.queryByText('Your working tree is clean.')).toBeNull();
});
it('explains a binary diff without pretending it has no changes',async()=>{
 const f=await fixture({'workspace.diff':{...diff,binary:true,patch:''}});render(<ChangesPage controller={f.controller}/>);
 await screen.findByText('Binary file changed. A text diff is unavailable.');expect(screen.queryByText('No textual differences for this version.')).toBeNull();
});

it('never calls an incomplete empty scan clean and keeps file rendering bounded',async()=>{
 const f=await fixture({'workspace.changes':{...changes,entries:[],truncated:true}});
 const rendered=render(<ChangesPage controller={f.controller}/>);await screen.findByText('Inspection was incomplete.');expect(screen.queryByText('Your working tree is clean.')).toBeNull();rendered.unmount();
 f.responses['workspace.changes']={...changes,entries:Array.from({length:350},(_,index)=>({...main,path:`file-${index}.py`}))};
 render(<ChangesPage controller={f.controller}/>);await screen.findByRole('button',{name:'Inspect file-0.py'});expect(screen.getAllByRole('button',{name:/^Inspect /})).toHaveLength(100);
 fireEvent.click(screen.getByRole('button',{name:'Next'}));await screen.findByRole('button',{name:'Inspect file-100.py'});
 fireEvent.change(screen.getByRole('textbox',{name:'Filter changed files'}),{target:{value:'file-349'}});expect(screen.getAllByRole('button',{name:/^Inspect /})).toHaveLength(1);
});
