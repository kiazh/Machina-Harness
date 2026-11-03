import {EditorState} from '@codemirror/state';
// @vitest-environment jsdom
import {afterEach, expect, it} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';
import {RecoveryStore} from '../../src/state/recovery.ts';

afterEach(()=>localStorage.clear());
function fixture(recovery?:RecoveryStore,blocked?:string){
  let release!:()=>void,enter!:()=>void;const gate=new Promise<void>(resolve=>release=resolve),entered=new Promise<void>(resolve=>enter=resolve);
  const calls:{method:string;params:Record<string,unknown>}[]=[];
  let epoch=0;
  const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){
    calls.push({method,params});if(method===blocked||(blocked==='target.read'&&method==='workspace.read'&&String(params.uri).endsWith('/target.py'))){enter();await gate;}
    if(method==='workspace.open'||method==='runtime.selectWorkspace')return {workspaceId:'w',clientId:'c',epoch:++epoch,resolvedRoot:params.root,rootLabel:params.root,trusted:params.trust,capabilities:[]} as T;
    const values:Record<string,unknown>={
      'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},
      initialize:{cwd:'original',cwdFull:'/original',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},
      'session.resume':{session:{id:params.sessionId,title:'Other project',model:'m',provider:'p',workspaceRoot:params.sessionId==='original'?'/original':'/other'},history:[]},
      'shell.jobs':{jobs:[]},'workspace.read':{uri:params.uri,text:'disk',revision:'disk-rev',languageId:'python',encoding:'utf-8',lineEnding:'LF',readOnly:false,sizeBytes:4},
      'workspace.documentOpen':{acceptedVersion:0},'workspace.intelligence':{workspaceId:'w',workspaceEpoch:epoch,uri:params.uri,documentVersion:params.version,freshness:'versioned',state:'ready',source:'editor-buffer',serverGeneration:1,operation:params.operation,result:[],truncated:false},
    };return values[method] as T;
  }},recovery);
  return {controller,calls,release,entered};
}
it('binds an explicit folder choice to the current conversation and retains it for restart',async()=>{
  const f=fixture();await f.controller.connect();await f.controller.resume('original');
  await f.controller.switchWorkspace('/project',false);
  expect(f.calls.at(-1)).toMatchObject({method:'runtime.selectWorkspace',params:{root:'/project',trust:false,sessionId:'original'}});
  expect(f.controller.snapshot().session?.workspaceRoot).toBe('/project');
  const next=fixture();await next.controller.connect();expect(next.controller.snapshot().workspace?.resolvedRoot).toBe('/project');
});
it('reopens recovered buffers in their recorded workspace using recovered text and the old disk revision',async()=>{
  const recovery=new RecoveryStore({async load(){return [{id:'file:///project/main.py',uri:'file:///project/main.py',workspaceRoot:'/project',name:'main.py',text:'unsaved',revision:'before',updatedAt:1}]},async write(){},async remove(){}});
  const f=fixture(recovery);await f.controller.restoreRecovery();await f.controller.connect();
  expect(f.controller.snapshot().workspace?.resolvedRoot).toBe('/project');
  expect(f.controller.snapshot().dirty.has('file:///project/main.py')).toBe(true);
  expect(f.calls.find(call=>call.method==='workspace.documentOpen')?.params.text).toBe('unsaved');
  expect(f.controller.snapshot().documents[0]?.revision).toBe('before');
});
it('opens the pinned workspace when resuming a conversation from another project',async()=>{
  const f=fixture();await f.controller.connect();await f.controller.resume('other');
  expect(f.controller.snapshot()).toMatchObject({session:{id:'other'},workspace:{resolvedRoot:'/other',trusted:false}});
  expect(f.calls.some(call=>call.method==='runtime.selectWorkspace'&&call.params.sessionId==='other')).toBe(true);
});
it('retains recovered edits and the old conversation if another conversation needs a different folder',async()=>{
  const recovery=new RecoveryStore({async load(){return [{id:'file:///original/main.py',uri:'file:///original/main.py',workspaceRoot:'/original',name:'main.py',text:'unsaved',revision:'before',updatedAt:1}]},async write(){},async remove(){}});
  const f=fixture(recovery);await f.controller.restoreRecovery();await f.controller.connect();await f.controller.resume('original');
  await expect(f.controller.resume('other')).rejects.toThrow('unsaved');
  expect(f.controller.snapshot()).toMatchObject({session:{id:'original'},workspace:{resolvedRoot:'/original'},switchingSession:false});
  expect(f.controller.snapshot().documents[0]?.text).toBe('unsaved');
});

it('asks the user to wait before selecting a workspace during engine startup',async()=>{
 const f=fixture(undefined,'config.get');const connecting=f.controller.connect();await f.entered;
 await expect(f.controller.switchWorkspace('/chosen',true)).rejects.toThrow('connecting');
 expect(f.calls.some(call=>call.method==='runtime.selectWorkspace'&&call.params.root==='/chosen')).toBe(false);
 f.release();await connecting;expect(f.controller.snapshot().workspace?.resolvedRoot).toBe('/original');
});
it('rebinds the current conversation to its recorded folder after an engine reconnect',async()=>{
 const f=fixture();await f.controller.connect();await f.controller.resume('original');const before=f.calls.length;
 await f.controller.connect();expect(f.calls.slice(before).find(call=>call.method==='runtime.selectWorkspace')?.params).toMatchObject({root:'/original',sessionId:'original'});
 expect(f.controller.snapshot().connection).toBe('ready');
});
it('does not fetch a code-navigation location outside the selected workspace',async()=>{
 const f=fixture();await f.controller.connect();await expect(f.controller.openLocation('file:///outside/main.py',2)).rejects.toThrow('outside');
 expect(f.calls.some(call=>call.method==='workspace.read')).toBe(false);
});

it('does not navigate an inspected result after its source buffer has changed',async()=>{
 const f=fixture();await f.controller.connect();await f.controller.openFile('main.py');const id=f.controller.snapshot().activeId,state=EditorState.create({doc:'disk'});f.controller.bindEditor(id,state);
 const result=await f.controller.inspectDocument('definition');expect(f.controller.isInspectionCurrent(result)).toBe(true);
 f.controller.bindEditor(id,state.update({changes:{from:0,insert:'new '}}).state);expect(f.controller.isInspectionCurrent(result)).toBe(false);
 await expect(f.controller.openLocation('file:///original/target.py',0,0,result)).rejects.toThrow('fresh');expect(f.calls.filter(call=>call.method==='workspace.read')).toHaveLength(1);
});
it('does not focus a target if the inspected source changes while that target is loading',async()=>{
 const f=fixture(undefined,'target.read');await f.controller.connect();await f.controller.openFile('main.py');const id=f.controller.snapshot().activeId,state=EditorState.create({doc:'disk'});f.controller.bindEditor(id,state);
 const result=await f.controller.inspectDocument('definition');const opening=f.controller.openLocation('file:///original/target.py',0,0,result);await f.entered;
 f.controller.bindEditor(id,state.update({changes:{from:0,insert:'new '}}).state);f.release();await expect(opening).rejects.toThrow('fresh');expect(f.controller.snapshot().activeId).toBe(id);
});
it('lets older navigation loads finish without stealing focus from a newer file choice',async()=>{
 const f=fixture(undefined,'target.read');await f.controller.connect();const old=f.controller.openLocation('file:///original/target.py',0);await f.entered;await f.controller.openLocation('file:///original/newest.py',2);
 f.release();await old;expect(f.controller.snapshot().activeId).toBe('file:///original/newest.py');expect(f.controller.snapshot().editorNavigation?.line).toBe(2);
});
