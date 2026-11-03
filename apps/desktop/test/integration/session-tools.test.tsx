// @vitest-environment jsdom
import {createRef} from 'react';
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';
import {SessionTools} from '../../src/app/SessionTools.tsx';
afterEach(()=>{cleanup();vi.restoreAllMocks();localStorage.clear()});
async function fixture(){
 let emit:(event:Record<string,unknown>)=>void=()=>{};
 const controller=new DesktopController({async start(){},subscribe(listener){emit=listener;return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){
  const values:Record<string,unknown>={'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},'session.resume':{session:{id:params.sessionId,title:'A',model:'m',provider:'p',workspaceRoot:'/test'},history:[]},'shell.jobs':{jobs:[]},'prompt.submit':{turnId:'t'}};return values[method] as T;
 }});await controller.connect();await controller.resume('a');return {controller,emit};
}
it('opens the requested export action and allows this read-only action while the main turn runs',async()=>{
 const f=await fixture();await f.controller.send('Working');const dialog=createRef<HTMLDialogElement>();render(<SessionTools controller={f.controller} dialog={dialog} initialAction="export" openToken={1}/>);dialog.current!.setAttribute('open','');
 expect((screen.getByRole('combobox',{name:'Conversation action'}) as HTMLSelectElement).value).toBe('export');expect((screen.getByRole('button',{name:'Apply'}) as HTMLButtonElement).disabled).toBe(false);
});
it('explains disconnected controls and disables export until the engine reconnects',async()=>{
 const f=await fixture();f.emit({type:'desktop.disconnected'});const dialog=createRef<HTMLDialogElement>();render(<SessionTools controller={f.controller} dialog={dialog} initialAction="export" openToken={1}/>);dialog.current!.setAttribute('open','');
 expect((screen.getByRole('button',{name:'Apply'}) as HTMLButtonElement).disabled).toBe(true);expect(screen.getByRole('button',{name:'Connect engine'})).toBeTruthy();
});
it.each([true,false])('does not let an old operation close or replace a newly opened tool (success=%s)',async(success)=>{
 const f=await fixture();let settle!:()=>void;const pending=new Promise<void>((resolve,reject)=>{settle=()=>success?resolve():reject(new Error('Old operation failed'))});vi.spyOn(f.controller,'renameSession').mockImplementation(()=>pending);
 const dialog=createRef<HTMLDialogElement>(),close=vi.fn();const view=render(<SessionTools controller={f.controller} dialog={dialog} initialAction="rename" openToken={1}/>);dialog.current!.setAttribute('open','');Object.defineProperty(dialog.current,'close',{configurable:true,value:close});
 fireEvent.change(screen.getByRole('textbox',{name:'Conversation action text'}),{target:{value:'Rename A'}});fireEvent.click(screen.getByRole('button',{name:'Apply'}));
 view.rerender(<SessionTools controller={f.controller} dialog={dialog} initialAction="export" openToken={2}/>);await act(async()=>settle());
 expect(close).not.toHaveBeenCalled();expect((screen.getByRole('combobox',{name:'Conversation action'}) as HTMLSelectElement).value).toBe('export');expect(screen.queryByRole('alert')).toBeNull();
});
