import {expect,it} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';

it('keeps an operation visible and guards conflicting conversation actions while it runs',async()=>{
 const calls:string[]=[];
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string){
  calls.push(method);
  return ({'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:['operations.start','operations.get','operations.cancel']},initialize:{cwd:'/test',model:'test',provider:'test'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},'operations.start':{id:'op',method:'interop.run',status:'running',cancellable:false,startedAt:'2026-11-02T00:00:00Z'}}[method]??{}) as T;
 }});
 await controller.connect();await controller.runOperation('interop.run',{platform:'codex',prompt:'Inspect'});
 controller.navigate('models');expect(controller.snapshot().backgroundOperation?.id).toBe('op');
 await expect(controller.send('Another task')).rejects.toThrow(/background run/);
 await expect(controller.resume('another')).rejects.toThrow(/background run/);
 await expect(controller.setModel('other','openai')).rejects.toThrow(/background run/);
 await expect(controller.switchWorkspace('/other',true)).rejects.toThrow(/background run/);
 expect(calls).not.toContain('prompt.submit');expect(calls).not.toContain('session.resume');
 controller.dispose();
});
