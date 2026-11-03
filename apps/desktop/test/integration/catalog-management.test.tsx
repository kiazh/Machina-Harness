// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {SkillsPage} from '../../src/features/catalog/SkillsPage.tsx';
import {McpPage} from '../../src/features/catalog/McpPage.tsx';
import {DesktopController} from '../../src/state/controller.ts';

afterEach(cleanup);
async function fixture(extra:Record<string,unknown>={}){
 const calls:{method:string;params:Record<string,unknown>}[]=[];
 const session={id:'session-a',model:'model',provider:'provider',title:'Conversation',status:'active',lastActivity:null};
 const server={id:'docs',display_name:'My docs',transport:'stdio',state:'disconnected',enabled:true};
 const responses:Record<string,unknown>={
  'runtime.capabilities':{engineVersion:'test',protocolVersion:2,storageProfile:'postgres',database:'ready',methods:[]},initialize:{cwd:'test',model:'model',provider:'provider'},'config.get':{config:{}},'session.list':{sessions:[session]},'workspace.open':{workspaceId:'w',clientId:'c',epoch:1,rootLabel:'test',resolvedRoot:'/test',capabilities:[]},'session.resume':{session,history:[]},'shell.jobs':{jobs:[]},
  'skills.list':{skills:[]},'skills.marketplace.search':{provider:'skills.sh',skills:[{id:'owner/repo/demo',name:'demo',source:'owner/repo',installs:20,repository:'https://github.com/owner/repo',url:'https://skills.sh/owner/repo/demo'}]},
  'skills.marketplace.preview':{reviewId:'review-a',digest:'exact-digest',source:'https://github.com/owner/repo',revision:'revision-a',scope:'installation',skills:[{name:'demo',description:'Example skill',content:'Reviewed instructions',files:[]}]},'skills.marketplace.install':{installed:['demo'],usable:true,outcomes:[]},
  'mcp.list':{servers:[server]},'mcp.status':{server},'mcp.tools':{tools:[],truncated:false},'mcp.preview':{digest:'connect-digest',proposal:{argv:['node','server.js'],cwd:'/project',backend:'host'}},'mcp.connect':{server:{...server,state:'connected'}},
  'mcp.config':{config:{id:'docs',display_name:'My docs',transport:'stdio',command:'node',argv:['server.js','--port','1234'],cwd:'/project',trust:'untrusted',secret_refs:['DOCS_TOKEN']}},'mcp.configure':{server},...extra};
 const controller=new DesktopController({async start(){},subscribe(){return()=>{}},async request<T>(method:string,params:Record<string,unknown>={}){calls.push({method,params});if(!((method==='runtime.selectWorkspace'?'workspace.open':method) in responses))throw new Error(`Unexpected ${method}`);const response=responses[method==='runtime.selectWorkspace'?'workspace.open':method];if(response instanceof Error)throw response;return response as T}});
 await controller.connect();await controller.resume(session.id);return {controller,calls,responses};
}

it('reviews marketplace instructions before submitting the exact confirmed digest',async()=>{
 const f=await fixture();render(<SkillsPage controller={f.controller}/>);
 await waitFor(()=>expect((screen.getByRole('button',{name:'Marketplace'}) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(screen.getByRole('button',{name:'Marketplace'}));
 fireEvent.change(screen.getByRole('textbox',{name:'Search marketplace'}),{target:{value:'demo'}});
 fireEvent.click(screen.getByRole('button',{name:'Search skills.sh'}));
 fireEvent.click(await screen.findByRole('button',{name:'Review demo'}));
 await screen.findByText('Reviewed instructions');
 expect(f.calls.some(call=>call.method==='skills.marketplace.install')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Install reviewed skill'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='skills.marketplace.install')?.params).toEqual({sessionId:'session-a',reviewId:'review-a',digest:'exact-digest',confirmed:true}));
});

it('never connects a server until the exact launch review is confirmed',async()=>{
 const f=await fixture();render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Review connection'}));
 await screen.findByText(/server.js/);
 expect(f.calls.some(call=>call.method==='mcp.connect')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Connect reviewed server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.connect')?.params).toEqual({id:'docs',sessionId:'session-a',digest:'connect-digest'}));
});

it('preserves executable arguments and credential names when editing an existing server',async()=>{
 const f=await fixture();render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Edit server'}));
 const name=await screen.findByRole('textbox',{name:'Display name'});
 fireEvent.change(name,{target:{value:'Edited docs'}});
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params.config).toMatchObject({id:'docs',display_name:'Edited docs',argv:['server.js','--port','1234'],cwd:'/project',secret_refs:['DOCS_TOKEN']}));
 expect(f.calls.some(call=>call.method==='mcp.connect')).toBe(false);
});

it('clears local launch fields when saving an HTTP transport without discarding the form draft',async()=>{
 const f=await fixture({'mcp.config':{config:{id:'docs',display_name:'My docs',transport:'stdio',command:'node',argv:['server.js'],cwd:'/project',env_allowlist:['PATH'],secret_refs:['DOCS_TOKEN'],connect_timeout:25,tool_allowlist:['search'],trust:'trusted_read'}}});
 render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Edit server'}));
 const transport=await screen.findByRole('combobox',{name:'Transport'});
 fireEvent.change(transport,{target:{value:'streamable_http'}});
 fireEvent.change(transport,{target:{value:'stdio'}});
 expect((screen.getByRole('textbox',{name:'Executable'}) as HTMLInputElement).value).toBe('node');
 expect(JSON.parse((screen.getByRole('textbox',{name:'Arguments'}) as HTMLTextAreaElement).value)).toEqual(['server.js']);
 fireEvent.change(transport,{target:{value:'streamable_http'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Endpoint URL'}),{target:{value:'https://docs.example/mcp'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Advanced configuration (JSON)'}),{target:{value:JSON.stringify({oauth:null,credential_profile:'docs-account',local_access:false,trust:'trusted_read'})}});
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params.config).toMatchObject({transport:'streamable_http',endpoint:'https://docs.example/mcp',command:'',argv:[],cwd:'',env_allowlist:[],secret_refs:[],credential_profile:'docs-account',connect_timeout:25,tool_allowlist:['search'],trust:'trusted_read'}));
});

it.each([
 {oauth:{client_mode:'auto',scope_allowlist:['read']},credential_profile:''},
 {oauth:null,credential_profile:'docs-account'},
])('clears HTTP fields when switching to stdio from $credential_profile $oauth',async(auth)=>{
 const f=await fixture({'mcp.config':{config:{id:'docs',display_name:'My docs',transport:'streamable_http',endpoint:'https://docs.example/mcp',local_access:true,trust:'trusted_read',connect_timeout:25,...auth}}});
 render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Edit server'}));
 fireEvent.change(await screen.findByRole('combobox',{name:'Transport'}),{target:{value:'stdio'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Executable'}),{target:{value:'python'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Arguments'}),{target:{value:'["server.py"]'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Credential reference names'}),{target:{value:'LOCAL_TOKEN'}});
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params.config).toMatchObject({transport:'stdio',command:'python',argv:['server.py'],secret_refs:['LOCAL_TOKEN'],endpoint:'',oauth:null,credential_profile:'',local_access:false,trust:'trusted_read',connect_timeout:25}));
});

it.each([
 {oauth:{client_mode:'auto',scope_allowlist:['read']},credential_profile:''},
 {oauth:null,credential_profile:'docs-account'},
])('preserves HTTP authentication and advanced settings on ordinary edits: $credential_profile $oauth',async(auth)=>{
 const f=await fixture({'mcp.config':{config:{id:'docs',display_name:'My docs',transport:'streamable_http',endpoint:'https://docs.example/mcp',local_access:true,trust:'trusted_read',connect_timeout:25,tool_allowlist:['search'],...auth}}});
 render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Edit server'}));
 fireEvent.change(await screen.findByRole('textbox',{name:'Display name'}),{target:{value:'Edited HTTP docs'}});
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params.config).toMatchObject({display_name:'Edited HTTP docs',transport:'streamable_http',endpoint:'https://docs.example/mcp',local_access:true,trust:'trusted_read',connect_timeout:25,tool_allowlist:['search'],...auth}));
});

it('ignores an invalid hidden argv draft after switching to HTTP',async()=>{
 const f=await fixture();render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Edit server'}));
 fireEvent.change(await screen.findByRole('textbox',{name:'Arguments'}),{target:{value:'unfinished JSON'}});
 fireEvent.change(screen.getByRole('combobox',{name:'Transport'}),{target:{value:'streamable_http'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Endpoint URL'}),{target:{value:'https://docs.example/mcp'}});
 fireEvent.click(screen.getByRole('button',{name:'Save server'}));
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.configure')?.params.config).toMatchObject({transport:'streamable_http',argv:[],command:''}));
});

it('submits server configuration only once while a save is pending',async()=>{
 let finish!:(value:unknown)=>void;
 const pending=new Promise(resolve=>{finish=resolve});
 const f=await fixture({'mcp.configure':pending});render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByRole('button',{name:'Edit server'}));
 const button=await screen.findByRole('button',{name:'Save server'});
 const form=button.closest('form')!;
 fireEvent.submit(form);fireEvent.submit(form);
 expect(f.calls.filter(call=>call.method==='mcp.configure')).toHaveLength(1);
 expect((button as HTMLButtonElement).disabled).toBe(true);
 finish({server:{id:'docs',display_name:'My docs',transport:'stdio',state:'disconnected',enabled:true}});
 await screen.findByText('Server saved. Review its connection before launching.');
});

it('shows the OAuth review and scope challenge before submitting its exact consent',async()=>{
 const server={id:'docs',display_name:'My docs',transport:'streamable_http',state:'disconnected',enabled:true};
 const f=await fixture({'mcp.list':{servers:[server]},'mcp.status':{server},'mcp.oauth.preview':{previewId:'auth-preview',digest:'oauth-digest',expiresAt:'2030-01-01T00:00:00Z',review:{connectorId:'docs',revision:3,endpoint:'https://docs.example/mcp',resource:'https://docs.example',issuer:'https://account.example',requestedScopes:['read'],registrationMode:'auto',endpoints:{authorization:'https://account.example/authorize',token:'https://account.example/token',registration:null,revocation:null},redirectUri:'http://127.0.0.1:12345/oauth/callback'},requiredScopes:['write'],authChallenge:{requiredScopes:['write'],error:'insufficient_scope'}},'mcp.oauth.start':{operationId:'auth-op',state:'awaiting_user'}});
 render(<McpPage controller={f.controller}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Inspect My docs'}));
 fireEvent.click(await screen.findByText('Account authorization'));
 fireEvent.click(screen.getByRole('button',{name:'Review sign in'}));
 const confirm=await screen.findByRole('button',{name:'Start reviewed sign in'});
 const consent=JSON.parse(confirm.parentElement!.querySelector('pre')!.textContent!);
 expect(consent).toMatchObject({issuer:'https://account.example',requestedScopes:['read'],requiredScopes:['write'],authChallenge:{requiredScopes:['write'],error:'insufficient_scope'}});
 expect(f.calls.some(call=>call.method==='mcp.oauth.start')).toBe(false);
 fireEvent.click(confirm);
 await waitFor(()=>expect(f.calls.find(call=>call.method==='mcp.oauth.start')?.params).toEqual({sessionId:'session-a',id:'docs',previewId:'auth-preview',digest:'oauth-digest'}));
});

it.each(['initial','refresh'])('does not claim the MCP inventory is empty when its %s request fails',async(phase)=>{
 const failure=new Error('Server inventory could not be read');
 const f=await fixture({'mcp.list':phase==='initial'?failure:{servers:[]}});
 render(<McpPage controller={f.controller}/>);
 if(phase==='refresh'){
  await screen.findByText('No MCP servers configured. Add a local stdio server or an HTTP endpoint.');
  f.responses['mcp.list']=failure;
  fireEvent.click(screen.getByRole('button',{name:'Refresh'}));
 }
 expect((await screen.findByRole('alert')).textContent).toBe('Server inventory could not be read');
 expect(screen.queryByText('No MCP servers configured. Add a local stdio server or an HTTP endpoint.')).toBeNull();
});
