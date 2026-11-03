// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {DesktopController} from '../../src/state/controller.ts';
import {useEnginePage} from '../../src/features/shared/useEnginePage.ts';
import {FeatureFrame} from '../../src/features/shared/FeatureFrame.tsx';

afterEach(cleanup);
function fixture(){const calls:string[]=[];const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string){calls.push(method);const values:Record<string,unknown>={'runtime.capabilities':{engineVersion:'old',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:['config.get']},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',trusted:false,capabilities:[]}};return values[method] as T}});return {controller,calls}}
function Page({controller,scoped=false}:{controller:DesktopController;scoped?:boolean}){const page=useEnginePage(controller,'jobs.list',scoped);return <FeatureFrame controller={controller} title="Scheduled jobs" {...page}><button disabled={!page.available}>Create job</button></FeatureFrame>}
it('explains offline features and offers a direct connection action',()=>{const {controller}=fixture();render(<Page controller={controller}/>);expect(screen.getByRole('status').textContent).toMatch(/connect.*engine/i);expect(screen.getByRole('button',{name:'Connect engine'})).toBeTruthy();expect((screen.getByRole('button',{name:'Create job'}) as HTMLButtonElement).disabled).toBe(true)});
it('uses the advertised engine capabilities instead of exposing a broken action',async()=>{const {controller,calls}=fixture();await controller.connect();render(<Page controller={controller}/>);await waitFor(()=>expect(screen.getByRole('status').textContent).toMatch(/does not support/));expect(calls).not.toContain('jobs.list');expect((screen.getByRole('button',{name:'Create job'}) as HTMLButtonElement).disabled).toBe(true)});
