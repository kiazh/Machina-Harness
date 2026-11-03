// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {McpPage} from '../../src/features/catalog/McpPage.tsx';
import {DesktopController} from '../../src/state/controller.ts';
import {chooseSelect} from '../helpers/select.ts';

afterEach(cleanup);

const entries=[
 ...[
  ['gmail','Gmail','gmailmcp'],['google-drive','Google Drive','drivemcp'],
  ['google-calendar','Google Calendar','calendarmcp'],['github','GitHub','github'],
 ].map(([id,displayName,service])=>({
  id,displayName,kind:'connector',category:'Accounts',provider:id==='github'?'GitHub':'Google',
  description:`Use ${displayName} tools.`,docsUrl:'https://example.com/setup',
  setup:{type:'oauth',instructions:[`Register a client for ${displayName} before signing in.`]},
  config:{id,display_name:displayName,transport:'streamable_http',endpoint:id==='github'?'https://api.githubcopilot.com/mcp/':`https://${service}.googleapis.com/mcp/v1`,oauth:{client_mode:'auto',scope_allowlist:['read']}},
 })),
 {id:'firecrawl-selfhosted',displayName:'Firecrawl (self-hosted)',kind:'connector',category:'Web research',provider:'Firecrawl',description:'Use your own Firecrawl server.',docsUrl:'https://example.com/firecrawl',setup:{type:'local_endpoint',instructions:['Run the MCP bridge with FIRECRAWL_API_URL set to your own REST backend.']},config:{id:'firecrawl-selfhosted',display_name:'Firecrawl (self-hosted)',transport:'streamable_http',endpoint:'http://127.0.0.1:3000/mcp',local_access:true}},
 {id:'custom-mcp',displayName:'Custom MCP server',kind:'connector',category:'Custom',provider:'Your server',description:'Use another MCP server.',docsUrl:'https://example.com/mcp',setup:{type:'custom',instructions:['Replace the example endpoint.']},config:{id:'custom-mcp',display_name:'Custom MCP server',transport:'streamable_http',endpoint:'https://mcp.example.com/mcp'}},
 {id:'machina',displayName:'Machina MCP',kind:'harness',category:'Harnesses',provider:'Machina',description:'Make Machina available to another harness.',docsUrl:'https://example.com/machina',setup:{type:'harness',instructions:['Register Machina in another harness.']},config:null,runtime:{command:'python',argv:['-m','machina.mcp_server']}},
];

async function fixture({session=true,servers=[],inventoryError=false}:{session?:boolean;servers?:Record<string,unknown>[];inventoryError?:boolean}={}){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 const conversation={id:'session-a',model:'model',provider:'provider',title:'Conversation',status:'active',lastActivity:null};
 const responses:Record<string,unknown>={
  'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},
  initialize:{cwd:'test',model:'model',provider:'provider'},'config.get':{config:{}},
  'session.list':{sessions:[conversation]},'session.resume':{session:conversation,history:[]},
  'workspace.open':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',capabilities:[]},'shell.jobs':{jobs:[]},
  'mcp.catalog':{entries},'mcp.list':inventoryError?new Error('Inventory unavailable'):{servers},
  'mcp.configure':{server:{id:'firecrawl-selfhosted',display_name:'Firecrawl (self-hosted)',transport:'streamable_http',state:'disconnected',enabled:true}},
  'mcp.status':{server:servers[0]},
 };
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){
  calls.push({method,params});const key=method==='runtime.selectWorkspace'?'workspace.open':method;
  if(!(key in responses))throw new Error(`Unexpected ${method}`);
  const response=responses[key];if(response instanceof Error)throw response;return response as T;
 }});
 await controller.connect();if(session)await controller.resume(conversation.id);
 return {controller,calls};
}

it('lets people browse and prepare account setup without an active conversation',async()=>{
 const f=await fixture({session:false});render(<McpPage controller={f.controller}/>);
 for(const name of ['Gmail','Google Drive','Google Calendar','GitHub','Firecrawl (self-hosted)','Custom MCP server']){
  expect((await screen.findByRole('button',{name:`Set up ${name}`})).hasAttribute('disabled')).toBe(false);
 }
 fireEvent.click(screen.getByRole('button',{name:'Set up Gmail'}));
 await screen.findByText('Register a client for Gmail before signing in.');
 expect((screen.getByRole('textbox',{name:'OAuth client ID'}) as HTMLInputElement).value).toBe('');
 expect((screen.getByRole('button',{name:'Save server'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.getByText('Open a conversation to save this server and review its connection.')).toBeTruthy();
 expect(f.calls.some(call=>['mcp.configure','mcp.connect','mcp.oauth.start'].includes(call.method))).toBe(false);
});

it('saves an edited self-hosted MCP bridge URL with local access and waits for connection review',async()=>{
 const f=await fixture();render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Set up Firecrawl (self-hosted)'}));
 expect(screen.getByText(/FIRECRAWL_API_URL/)).toBeTruthy();
 const endpoint=screen.getByRole('textbox',{name:'Endpoint URL'});
 expect((endpoint as HTMLInputElement).value).toBe('http://127.0.0.1:3000/mcp');
 fireEvent.change(endpoint,{target:{value:'http://localhost:4300/mcp'}});
 expect((screen.getByRole('checkbox',{name:'Allow local HTTP access'}) as HTMLInputElement).checked).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params).toMatchObject({sessionId:'session-a',config:{id:'firecrawl-selfhosted',endpoint:'http://localhost:4300/mcp',local_access:true,command:'',argv:[]}}));
 expect(f.calls.some(call=>call.method==='mcp.connect')).toBe(false);
});

it('uses the reported inventory state and manages an existing connector matched by endpoint',async()=>{
 const f=await fixture({servers:[{id:'my-github',display_name:'Work repositories',transport:'streamable_http',endpoint:'https://api.githubcopilot.com/mcp/',state:'connected',enabled:true}]});
 render(<McpPage controller={f.controller}/>);
 const manage=await screen.findByRole('button',{name:'Manage GitHub'});
 expect(within(manage.closest('article')!).getByText('Connected')).toBeTruthy();
 expect(screen.queryByRole('button',{name:'Set up GitHub'})).toBeNull();
 fireEvent.click(manage);
 await screen.findByRole('region',{name:'Server details'});
 expect(f.calls.find(call=>call.method==='mcp.status')?.params.id).toBe('my-github');
 expect(f.calls.some(call=>call.method==='mcp.configure')).toBe(false);
});

it('shows unknown configuration status when inventory fails while keeping starters browsable',async()=>{
 const f=await fixture({session:false,inventoryError:true});render(<McpPage controller={f.controller}/>);
 const setup=await screen.findByRole('button',{name:'Set up Gmail'});
 await screen.findByText('Inventory unavailable');
 expect(within(setup.closest('article')!).getByText('Status unavailable')).toBeTruthy();
 expect(screen.queryByText('Not configured')).toBeNull();
 fireEvent.click(setup);
 await screen.findByRole('textbox',{name:'OAuth client ID'});
});

it('routes Machina MCP to harness management without creating a self connector',async()=>{
 const f=await fixture({session:false});render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Manage agent harnesses'}));
 expect(f.controller.snapshot().page).toBe('interop');
 expect(f.calls.some(call=>['mcp.configure','mcp.connect','interop.enable'].includes(call.method))).toBe(false);
});

it('offers named OAuth setup fields that save secret references and retain the scope review',async()=>{
 const f=await fixture();render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Set up Gmail'}));
 fireEvent.change(screen.getByRole('textbox',{name:'OAuth client ID'}),{target:{value:'registered-client'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Client secret reference'}),{target:{value:'GOOGLE_MCP_SECRET'}});
 chooseSelect(screen.getByRole('combobox',{name:'OAuth client mode'}),'preregistered');
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params.config).toMatchObject({id:'gmail',oauth:{client_mode:'preregistered',client_id:'registered-client',client_secret_ref:'GOOGLE_MCP_SECRET',scope_allowlist:['read']}}));
 expect(f.calls.some(call=>call.method==='mcp.oauth.start'||call.method==='mcp.connect')).toBe(false);
});
