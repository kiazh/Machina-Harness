import {EditorState} from '@codemirror/state';
import {expect,it} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';

function fixture(blocked:string){
 let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve);const calls:string[]=[];const details:{method:string;params:Record<string,unknown>}[]=[];let emit:(event:Record<string,unknown>)=>void=()=>{};
 const controller=new DesktopController({async start(){},subscribe(listener){emit=listener;return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){
  calls.push(method);details.push({method,params});if(method===blocked)await gate;
  const values:Record<string,unknown>={'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',trusted:false,capabilities:[]},'session.create':{session:{id:'s',title:'s',model:'m',provider:'p'}},'session.fork':{session:{id:'fork-s',title:'Fork',model:'m',provider:'p'}},'session.resume':{session:{id:params.sessionId,title:'Fork',model:'m',provider:'p'},history:[]},'shell.jobs':{jobs:[]},'prompt.submit':{turnId:'t'},'prompt.steering':{items:[{id:'other-q',text:'Old conversation'}]},'prompt.steer':{queued:true,items:[{id:'q',text:params.text}]}};return values[method] as T;
 }});return {controller,calls,details,release,emit:(event:Record<string,unknown>)=>emit({sessionId:'s',turnId:'t',...event})};
}
it('does not create a session when connecting or starting a new conversation',async()=>{
 const f=fixture('unused');await f.controller.connect();f.controller.newSession();
 expect(f.calls).not.toContain('session.create');
 await f.controller.send('first real message');
 expect(f.calls.filter(method=>method==='session.create')).toHaveLength(1);
});
it('retains a prompt when starting a new conversation cancels its pending session creation',async()=>{
 const f=fixture('session.create');await f.controller.connect();const sending=f.controller.send('keep me');
 f.controller.newSession();f.release();await expect(sending).rejects.toThrow('draft');
 expect(f.controller.snapshot().session).toBeUndefined();expect(f.calls).not.toContain('prompt.submit');
});
it('does not install an old steering reply in a new conversation after the turn ends',async()=>{
 const f=fixture('prompt.steer');await f.controller.connect();await f.controller.send('start');const steering=f.controller.send('steer');
 f.emit({type:'message.complete',text:'done',cancelled:false});f.controller.newSession();f.release();
 await expect(steering).rejects.toThrow('draft');expect(f.controller.snapshot().queue).toEqual([]);expect(f.controller.snapshot().session).toBeUndefined();
});
it('does not install an old queue-removal reply after starting a new conversation',async()=>{
 const f=fixture('prompt.steering');await f.controller.connect();await f.controller.send('start');const removal=f.controller.removeQueued('q');
 f.emit({type:'message.complete',text:'done',cancelled:false});f.controller.newSession();f.release();await removal;
 expect(f.controller.snapshot().queue).toEqual([]);expect(f.controller.snapshot().session).toBeUndefined();
});
it('keeps a completed fork in history without pulling the user out of a newer conversation',async()=>{
 const f=fixture('session.fork');await f.controller.connect();await f.controller.send('start');f.emit({type:'message.complete',text:'done',cancelled:false});const fork=f.controller.forkSession();
 f.controller.newSession();f.release();await fork;expect(f.controller.snapshot().session).toBeUndefined();expect(f.controller.snapshot().sessions.some(session=>session.id==='fork-s')).toBe(true);
 expect(f.calls).not.toContain('session.resume');
});

it('passes image payloads to prompt submission without retaining encodings in transcript rows',async()=>{
 const f=fixture('unused');await f.controller.connect();const images=[{name:'context.png',mediaType:'image/png',data:'iVBORw0KGgoB'}];await f.controller.send('Inspect',images);
 expect(f.details.find(call=>call.method==='prompt.submit')?.params).toMatchObject({sessionId:'s',text:'Inspect',images});
 const transcript=f.controller.transcript.order().map(id=>f.controller.transcript.get(id));expect(JSON.stringify(transcript)).not.toContain(images[0].data);expect(transcript[0]?.text).toContain('[image 1] context.png');
});
it('captures only explicit selected text from an untitled buffer without making a model request',async()=>{
 const f=fixture('unused');await f.controller.connect();const id=f.controller.snapshot().activeId;f.controller.bindEditor(id,EditorState.create({doc:'private selected remainder',selection:{anchor:8,head:16}}));
 const text=f.controller.captureSelection();expect(text).toContain('unsaved buffer');expect(text).toContain('selected');expect(text).not.toContain('private');expect(text).not.toContain('remainder');expect(f.calls).not.toContain('prompt.submit');
});
