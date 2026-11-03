// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {MemoryReviews} from '../../src/features/memory/MemoryReviews.tsx';
import {ModelsPage} from '../../src/features/catalog/ModelsPage.tsx';
import {DesktopController} from '../../src/state/controller.ts';

afterEach(cleanup);
async function fixture(extra:Record<string,unknown>={}){
 const responses:Record<string,unknown>={
  'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},
  initialize:{cwd:'test',model:'swift',provider:'fixture'},'config.get':{config:{}},'session.list':{sessions:[]},
  'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',capabilities:[]},
  'memory.pending':{pending:[]},'providers.list':{providers:[]},
  'models.list':{provider:'fixture',source:'cache',models:[{id:'swift',displayName:'Swift model',description:'Quick tasks'}]},...extra,
 };
 const calls:string[]=[];
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string){calls.push(method);const value=responses[method];if(value instanceof Error)throw value;return value as T}});
 await controller.connect();return {controller,responses,calls};
}

it('does not claim that memory approvals are empty when the initial queue read fails',async()=>{
 const f=await fixture({'memory.pending':new Error('Memory queue unavailable')});render(<MemoryReviews controller={f.controller}/>);
 await screen.findByText('Memory queue unavailable');
 expect(screen.queryByText('No pending memories.')).toBeNull();
});

it('withdraws the empty memory state when refreshing the queue fails',async()=>{
 const f=await fixture();render(<MemoryReviews controller={f.controller}/>);
 await screen.findByText('No pending memories.');
 f.responses['memory.pending']=new Error('Memory queue unavailable');
 fireEvent.click(screen.getByRole('button',{name:'Refresh'}));
 await screen.findByText('Memory queue unavailable');
 expect(screen.queryByText('No pending memories.')).toBeNull();
});

it('explains a model search with no matches and clears it without fetching the catalog again',async()=>{
 const f=await fixture();render(<ModelsPage controller={f.controller}/>);
 await screen.findByRole('button',{name:/Swift model/});
 fireEvent.change(screen.getByRole('textbox',{name:'Search models'}),{target:{value:'missing-model'}});
 expect(screen.getByRole('status').textContent).toContain('No models match');
 expect(screen.getByRole('status').textContent).toContain('missing-model');
 expect(screen.queryByRole('button',{name:/Swift model/})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Clear model search'}));
 await screen.findByRole('button',{name:/Swift model/});
 await waitFor(()=>expect((screen.getByRole('textbox',{name:'Search models'}) as HTMLInputElement).value).toBe(''));
 expect(f.calls.filter(method=>method==='models.list')).toHaveLength(1);
});

it('distinguishes a provider with no catalog entries from a filtered search',async()=>{
 const f=await fixture({'models.list':{provider:'fixture',source:'cache',models:[]}});render(<ModelsPage controller={f.controller}/>);
 await screen.findByText('No models were returned. Check the connection or enter a model ID below.');
 expect(screen.queryByText(/No models match/)).toBeNull();
});
