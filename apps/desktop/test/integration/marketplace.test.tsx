// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {Marketplace} from '../../src/features/catalog/Marketplace.tsx';
import {DesktopController} from '../../src/state/controller.ts';
import {chooseSelect} from '../helpers/select.ts';

afterEach(cleanup);
const design={id:'one/skills/design',skillSlug:'design',name:'Design systems',source:'one/skills',repository:'https://github.com/one/skills',url:'https://skills.sh/one/skills/design',description:'Build consistent interfaces',markets:['skills.sh'],metrics:[{market:'skills.sh',label:'installs',value:1200}]};
const testing={...design,id:'two/skills/testing',skillSlug:'testing',name:'Testing changes',source:'two/skills',markets:['skillsmp'],metrics:[{market:'skillsmp',label:'GitHub stars',value:85}]};
const sources=[{id:'skills.sh',name:'skills.sh',url:'https://skills.sh',status:'ready'},{id:'skillsmp',name:'SkillsMP',url:'https://skillsmp.com',status:'ready'}];
const discovery={featured:[design],sections:[{market:'skills.sh',title:'Popular on skills.sh',description:'Ranked by installations',skills:[design]},{market:'skillsmp',title:'Development on SkillsMP',description:'Development skills, sorted by repository stars',skills:[testing]}],markets:sources};
async function fixture(extra:Record<string,unknown>={}){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 const responses:Record<string,unknown>={'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},initialize:{cwd:'/test',model:'m',provider:'p'},'config.get':{config:{}},'session.list':{sessions:[]},'runtime.selectWorkspace':{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},'skills.marketplace.discover':discovery,'skills.marketplace.search':{query:'testing',skills:[testing],markets:sources},...extra};
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){calls.push({method,params});const result=await responses[method];if(result instanceof Error)throw result;return result as T}});await controller.connect();
 return {controller,calls,responses};
}

it('loads featured and popular skills from both catalogs before a query is entered',async()=>{
 const f=await fixture();render(<Marketplace controller={f.controller} busy={false} onReview={()=>{}}/>);
 await screen.findByRole('region',{name:'Featured skills'});
 expect(screen.getByRole('region',{name:'Popular on skills.sh'}).textContent).toContain('installs');
 expect(screen.getByRole('region',{name:'Development on SkillsMP'}).textContent).toContain('GitHub stars');
 expect(f.calls.filter(call=>call.method==='skills.marketplace.discover')).toHaveLength(1);
 expect(f.calls.some(call=>call.method==='skills.marketplace.search')).toBe(false);
 expect((screen.getByRole('button',{name:'Review Testing changes'}) as HTMLButtonElement).disabled).toBe(true);
});

it('searches on submit and reruns the query when the catalog filter changes',async()=>{
 const f=await fixture();render(<Marketplace controller={f.controller} busy={false} onReview={()=>{}}/>);await screen.findByRole('region',{name:'Featured skills'});
 fireEvent.change(screen.getByRole('textbox',{name:'Search marketplace'}),{target:{value:'testing'}});
 expect(f.calls.some(call=>call.method==='skills.marketplace.search')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Search'}));await screen.findByText('1 match for “testing”');
 expect(f.calls.find(call=>call.method==='skills.marketplace.search')?.params).toEqual({query:'testing',limit:30,markets:['skills.sh','skillsmp']});
 chooseSelect(screen.getByRole('combobox',{name:'Skill catalog'}),'skillsmp');
 await waitFor(()=>expect(f.calls.filter(call=>call.method==='skills.marketplace.search').at(-1)?.params.markets).toEqual(['skillsmp']));
});

it('shows available source results alongside the unavailable catalog notice',async()=>{
 const f=await fixture({'skills.marketplace.discover':{...discovery,featured:[],sections:[{...discovery.sections[0],skills:[]},discovery.sections[1]],markets:[{...sources[0],status:'unavailable',error:'skills.sh is unavailable (HTTP 503)'},sources[1]]}});
 render(<Marketplace controller={f.controller} busy={false} onReview={()=>{}}/>);
 await screen.findByText('skills.sh is unavailable (HTTP 503)');expect(screen.getByText('Testing changes')).toBeTruthy();expect(screen.getByText('Showing the available catalog.')).toBeTruthy();
 expect(within(screen.getByRole('region',{name:'Popular on skills.sh'})).queryByText('No listings returned from this catalog.')).toBeNull();
});

it('does not install a late search reply into a new conversation scope',async()=>{
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve});const f=await fixture({'skills.marketplace.search':pending});
 render(<Marketplace controller={f.controller} busy={false} onReview={()=>{}}/>);await screen.findByRole('region',{name:'Featured skills'});
 fireEvent.change(screen.getByRole('textbox',{name:'Search marketplace'}),{target:{value:'testing'}});fireEvent.click(screen.getByRole('button',{name:'Search'}));
 f.responses['runtime.selectWorkspace']={workspaceId:'other',clientId:'c',epoch:2,resolvedRoot:'/other',rootLabel:'other',trusted:true,capabilities:[]};
 await act(()=>f.controller.switchWorkspace('/other',true));
 await waitFor(()=>expect(f.calls.filter(call=>call.method==='skills.marketplace.discover')).toHaveLength(2));
 await act(async()=>finish({query:'old',skills:[{...testing,name:'Old result'}],markets:sources}));
 expect(screen.queryByText('Old result')).toBeNull();
 expect(screen.getByRole('region',{name:'Featured skills'})).toBeTruthy();f.controller.dispose();
});
