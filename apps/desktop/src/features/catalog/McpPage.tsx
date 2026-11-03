import {Select} from '../../components/Select.tsx';
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {ArrowUpRight,CalendarDays,Flame,FolderOpen,GitBranch,Mail,Network,Plug} from 'lucide-react';
import type {DesktopController} from '../../state/controller.ts';
import {FeatureFrame} from '../shared/FeatureFrame.tsx';
import {useEnginePage} from '../shared/useEnginePage.ts';
import './catalog-management.css';
import './mcp-catalog.css';
import '../shared/management.css';

interface Server {id:string;display_name?:string;transport:string;state:string;enabled:boolean;endpoint?:string;last_error?:string;tool_count?:number}
interface ServerConfig {id:string;display_name:string;transport:string;command?:string;argv?:string[];cwd?:string;endpoint?:string;env_allowlist?:string[];secret_refs?:string[];enabled?:boolean;oauth?:unknown;[key:string]:unknown}
interface CatalogEntry {id:string;displayName:string;kind:'connector'|'harness';category:string;provider:string;description:string;docsUrl:string;setup:{type:'oauth'|'local_endpoint'|'custom'|'harness';instructions:string[]};config:ServerConfig|null;runtime?:{command:string;argv:string[]}}
interface LaunchReview {digest:string;proposal:Record<string,unknown>}
interface AuthReview {previewId?:string;digest?:string;issuers?:string[];review?:Record<string,unknown>;requiredScopes?:string[];authChallenge?:Record<string,unknown>}
interface AuthState {operationId?:string;state?:string;generation?:number;[key:string]:unknown}
const blank:ServerConfig={id:'',display_name:'',transport:'stdio',command:'',argv:[],cwd:'',endpoint:'',env_allowlist:[],secret_refs:[],enabled:true,trust:'untrusted'};
const starterIcons:Record<string,typeof Mail>={gmail:Mail,'google-drive':FolderOpen,'google-calendar':CalendarDays,github:GitBranch,'firecrawl-selfhosted':Flame,machina:Network};
const stateLabels:Record<string,string>={connected:'Connected',connecting:'Connecting',disconnected:'Configured, disconnected',auth_required:'Sign-in required',disabled:'Disabled',degraded:'Degraded',error:'Connection error'};
function objectFields(value:unknown):Record<string,unknown>{return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{}}

export function McpPage({controller,embedded=false}:{controller:DesktopController;embedded?:boolean}) {
 const formPanel=useRef<HTMLFormElement>(null);
 const page=useEnginePage<{servers:Server[]}>(controller,'mcp.list');
 const catalog=useEnginePage<{entries:CatalogEntry[]}>(controller,'mcp.catalog');
 const [starter,setStarter]=useState<CatalogEntry>();
 const setupHeading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{if(starter)setupHeading.current?.focus({preventScroll:true})},[starter]);
 const [selected,setSelected]=useState<Server>(),[form,setForm]=useState<ServerConfig>(),[editing,setEditing]=useState(false),[args,setArgs]=useState(''),[env,setEnv]=useState(''),[secrets,setSecrets]=useState(''),[advanced,setAdvanced]=useState(''),[localError,setLocalError]=useState(''),[review,setReview]=useState<LaunchReview>(),[remove,setRemove]=useState(false),[tools,setTools]=useState<{name:string;description?:string}[]>(),[authReview,setAuthReview]=useState<AuthReview>(),[auth,setAuth]=useState<AuthState>(),[forgetAuth,setForgetAuth]=useState(false);
 const currentId=selected?.id;const current=()=>controller.snapshot().session?.id===page.sessionId;
 useEffect(()=>{if(!form)return;const panel=formPanel.current;if(!panel)return;for(let parent=panel.parentElement;parent;parent=parent.parentElement){if(/auto|scroll/.test(getComputedStyle(parent).overflowY)){parent.scrollTop+=panel.getBoundingClientRect().top-parent.getBoundingClientRect().top-16;break}}panel.querySelector<HTMLInputElement>('input:not([readonly])')?.focus({preventScroll:true})},[form?.id,editing]);
 function update(key:string,value:unknown){setForm(previous=>previous?{...previous,[key]:value}:previous)}
 function startForm(config:ServerConfig,edit:boolean,entry?:CatalogEntry){setForm(config);setStarter(entry);setEditing(edit);setArgs(JSON.stringify(config.argv??[],null,2));setEnv((config.env_allowlist??[]).join('\n'));setSecrets((config.secret_refs??[]).join('\n'));setAdvanced(JSON.stringify({oauth:config.oauth??null,trust:config.trust??'untrusted',credential_profile:config.credential_profile??'',local_access:config.local_access??false},null,2));setReview(undefined);setLocalError('')}
 function chooseStarter(entry:CatalogEntry){
  if(!entry.config)return;
  const config=structuredClone(entry.config);
  if(entry.setup.type==='oauth'&&(entry.provider==='Google'||entry.provider==='GitHub'))config.oauth={...objectFields(config.oauth),client_mode:'preregistered',client_id:'',...(entry.provider==='Google'?{token_endpoint_auth_method:'client_secret_basic',client_secret_ref:''}:{})};
  setSelected(undefined);setAuth(undefined);setAuthReview(undefined);setTools(undefined);startForm(config,false,entry);
 }
 let extraFields:Record<string,unknown>={};try{extraFields=objectFields(JSON.parse(advanced))}catch{/* Keep an incomplete JSON draft editable. */}
 const oauth=extraFields.oauth?objectFields(extraFields.oauth):undefined;
 function updateAdvanced(key:string,value:unknown){setAdvanced(JSON.stringify({...extraFields,[key]:value},null,2))}
 function updateOauth(key:string,value:unknown){updateAdvanced('oauth',{...oauth,[key]:value})}
 function configuredServer(entry:CatalogEntry){return page.data?.servers.find(server=>server.id===entry.config?.id||(entry.config?.endpoint&&server.endpoint?.replace(/\/$/,'')===entry.config.endpoint.replace(/\/$/,'')))}
 function refresh(){page.refresh();catalog.refresh()}
 async function inspect(server:Server){setSelected(server);setForm(undefined);setReview(undefined);setTools(undefined);setAuth(undefined);setAuthReview(undefined);setRemove(false);const response=await page.act<{server:Server}>('mcp.status',{id:server.id});if(response&&current())setSelected(response.server)}
 async function edit(){if(!currentId)return;const result=await page.act<{config:ServerConfig}>('mcp.config',{id:currentId});if(result&&current())startForm(result.config,true)}
 async function save(event:FormEvent){
  event.preventDefault();if(!form||page.working)return;setLocalError('');
  if(!page.sessionId){setLocalError('Open a conversation to save this server and review its connection.');return}
  try{
   const stdio=form.transport==='stdio';
   const argv:unknown=stdio?JSON.parse(args):[],extra:unknown=JSON.parse(advanced);
   if(!Array.isArray(argv)||!argv.every(item=>typeof item==='string'))throw new Error('Arguments must be a JSON array of strings.');
   if(!extra||typeof extra!=='object'||Array.isArray(extra))throw new Error('Advanced configuration must be a JSON object.');
   // Normalize only the payload so switching transports keeps the unsaved draft.
   const transportFields=stdio?{endpoint:'',oauth:null,credential_profile:'',local_access:false}:{command:'',argv:[],cwd:'',env_allowlist:[],secret_refs:[]};
   const config={...form,...extra,id:form.id,display_name:form.display_name,transport:form.transport,argv,env_allowlist:env.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),secret_refs:secrets.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),...transportFields};
   const result=await page.act<{server:Server}>('mcp.configure',{config},'Server saved. Review its connection before launching.');
   if(result&&current()){setForm(undefined);setSelected(result.server)}
  }catch(reason){setLocalError(reason instanceof Error?reason.message:String(reason))}
 }
 async function connectionReview(){if(!currentId)return;setAuthReview(undefined);const value=await page.act<LaunchReview>('mcp.preview',{id:currentId});if(value&&current())setReview(value)}
 async function connect(){if(!currentId||!review)return;const value=await page.act<{server:Server}>('mcp.connect',{id:currentId,digest:review.digest},'Server connected.');if(value&&current()){setReview(undefined);setSelected(value.server)}}
 async function oauthPreview(issuer?:string){if(!currentId)return;const value=await page.act<AuthReview>('mcp.oauth.preview',{id:currentId,...(issuer?{issuer}:{})});if(value&&current())setAuthReview(value)}
 async function oauthStart(){if(!currentId||!authReview?.previewId||!authReview.digest)return;const value=await page.act<AuthState>('mcp.oauth.start',{id:currentId,previewId:authReview.previewId,digest:authReview.digest});if(value&&current()){setAuth(value);setAuthReview(undefined)}}
 const authConsent=authReview?.review?{...authReview.review,...(authReview.requiredScopes?{requiredScopes:authReview.requiredScopes}:{}),...(authReview.authChallenge?{authChallenge:authReview.authChallenge}:{})}:authReview;
 const controls=<div className="mcp-catalog">
 <section aria-label="Available connectors" className="mcp-starters">
  <div className="mcp-section-title"><h2>Available connectors</h2><p>Choose a service to review its setup.</p></div>
  {catalog.busy&&!catalog.data&&<p className="feature-description" role="status">Loading connector starters…</p>}
  {catalog.error&&<p className="inline-error" role="alert">Connector starters could not be loaded: {catalog.error}</p>}
  {catalog.unavailableReason&&<p className="feature-description">{catalog.unavailableReason}</p>}
  <div className="mcp-starter-list">{catalog.data?.entries.map(entry=>{
   const server=entry.kind==='connector'?configuredServer(entry):undefined;
   const status=entry.kind==='harness'?'For other agent harnesses':page.error?'Status unavailable':!page.data?'Checking configuration…':server?(stateLabels[server.state]??server.state):entry.setup.type==='local_endpoint'?'Requires local server':'Not configured';
   const Icon=starterIcons[entry.id]??Plug;
   return <article className="mcp-starter-row" key={entry.id}>
    <span className="mcp-starter-icon"><Icon size={18} aria-hidden="true"/></span>
    <div className="mcp-starter-copy"><h3>{entry.displayName}</h3><p>{entry.description}</p><small>{entry.provider} · {entry.category}</small></div>
    <span className={`mcp-starter-state${server?.state==='connected'&&!page.error?' is-connected':''}`}>{status}</span>
    {entry.kind==='harness'?<button className="mcp-starter-action" onClick={()=>controller.navigate('interop')}>Manage agent harnesses<ArrowUpRight size={13} aria-hidden="true"/></button>:server?<button className="mcp-starter-action" aria-label={`Manage ${entry.displayName}`} disabled={page.working||!page.available} onClick={()=>{void inspect(server)}}>Manage</button>:<button className="mcp-starter-action" aria-label={`Set up ${entry.displayName}`} disabled={page.working||!catalog.available} onClick={()=>chooseStarter(entry)}>Set up</button>}
   </article>
  })}</div>
 </section>
 <div className="mcp-section-title mcp-configured-title"><h2>Configured servers</h2><p>Connection state comes from this local engine.</p></div>
 {!page.sessionId&&!form&&<p className="feature-description">You can browse setup without a conversation. Open one to save a server or review its connection.</p>}
 <div className="catalog-results">{page.data?.servers.map(server=><div className="catalog-result" key={server.id}><button className="catalog-result-main" aria-label={`Inspect ${server.display_name||server.id}`} disabled={page.working} onClick={()=>{void inspect(server)}}><strong>{server.display_name||server.id}</strong><p>{server.transport} · {server.state}</p>{server.last_error&&<small className="catalog-error">{server.last_error}</small>}</button></div>)}</div>
 {page.available&&!page.busy&&!page.error&&page.data&&page.data.servers.length===0&&<p className="catalog-empty">No MCP servers configured. Add a local stdio server or an HTTP endpoint.</p>}
 {localError&&<p role="alert" className="inline-error">{localError}</p>}
 {form&&<form ref={formPanel} className="catalog-form" onSubmit={event=>{void save(event)}}>
  <h2 ref={setupHeading} tabIndex={-1}>{editing?'Edit server':starter?`Set up ${starter.displayName}`:'Add MCP server'}</h2>
  {starter&&<div className="mcp-setup-guide"><ol>{starter.setup.instructions.map(instruction=><li key={instruction}>{instruction}</li>)}</ol><a href={starter.docsUrl} target="_blank" rel="noreferrer">Setup documentation<ArrowUpRight size={12} aria-hidden="true"/></a></div>}
  <div className="catalog-columns"><label>Server ID<input required readOnly={editing} maxLength={64} pattern="[a-z0-9][a-z0-9_-]*" value={form.id} onChange={event=>update('id',event.target.value)}/></label><label>Display name<input required value={form.display_name} onChange={event=>update('display_name',event.target.value)}/></label></div>
  <label>Transport<Select value={form.transport} onChange={event=>update('transport',event.target.value)}><option value="stdio">Local executable (stdio)</option><option value="streamable_http">Streamable HTTP</option></Select></label>
  {form.transport==='stdio'?<>
   <label>Executable<input required placeholder="node, python, or an absolute executable path" value={form.command??''} onChange={event=>update('command',event.target.value)}/></label>
   <label>Arguments (JSON array)<textarea aria-label="Arguments" value={args} onChange={event=>setArgs(event.target.value)}/></label>
   <label>Working directory<input value={form.cwd??''} onChange={event=>update('cwd',event.target.value)}/></label>
   <div className="catalog-columns"><label>Environment variable names<textarea value={env} onChange={event=>setEnv(event.target.value)} placeholder="One allowed name per line"/></label><label>Credential reference names<textarea value={secrets} onChange={event=>setSecrets(event.target.value)} placeholder="One configured credential name per line"/></label></div>
  </>:<>
   <label>Endpoint URL<input aria-label="Endpoint URL" required type="url" value={form.endpoint??''} onChange={event=>update('endpoint',event.target.value)}/>{starter?.setup.type==='local_endpoint'&&<small>Use the MCP bridge endpoint here. Configure the self-hosted REST backend on the bridge.</small>}</label>
   <label className="catalog-checkbox"><input type="checkbox" checked={extraFields.local_access===true} onChange={event=>updateAdvanced('local_access',event.target.checked)}/>Allow local HTTP access</label>
   <label className="catalog-checkbox"><input type="checkbox" checked={!!oauth} onChange={event=>{setAdvanced(JSON.stringify({...extraFields,oauth:event.target.checked?{client_mode:'auto'}:null,...(event.target.checked?{credential_profile:''}:{})},null,2))}}/>Use OAuth sign in</label>
   {oauth&&<fieldset className="mcp-oauth-fields"><legend>Account setup</legend>
    <label>OAuth client mode<Select value={String(oauth.client_mode??'auto')} onChange={event=>updateAdvanced('oauth',{...oauth,client_mode:event.target.value,...(event.target.value!=='preregistered'?{client_secret_ref:'',token_endpoint_auth_method:'none'}:{}),...(event.target.value!=='metadata_document'?{client_metadata_url:''}:{})})}><option value="auto">Automatic discovery</option><option value="preregistered">Registered OAuth client</option><option value="metadata_document">Client metadata document</option></Select></label>
    {oauth.client_mode==='preregistered'&&<>
     <label>OAuth client ID<input required value={String(oauth.client_id??'')} onChange={event=>updateOauth('client_id',event.target.value)} autoComplete="off"/></label>
     <label>Token authentication<Select value={String(oauth.token_endpoint_auth_method??'none')} onChange={event=>updateOauth('token_endpoint_auth_method',event.target.value)}><option value="none">Public client, no secret</option><option value="client_secret_basic">Client secret (HTTP Basic)</option></Select></label>
     <label>Client secret reference<input aria-label="Client secret reference" required={oauth.token_endpoint_auth_method==='client_secret_basic'} pattern="[A-Z_][A-Z0-9_]*" value={String(oauth.client_secret_ref??'')} onChange={event=>updateOauth('client_secret_ref',event.target.value)} placeholder="GOOGLE_MCP_CLIENT_SECRET" autoComplete="off"/><small>Enter a saved secret name. Manage the secret value in API keys.</small></label>
    </>}
    {oauth.client_mode==='metadata_document'&&<label>Client metadata URL<input required type="url" value={String(oauth.client_metadata_url??'')} onChange={event=>updateOauth('client_metadata_url',event.target.value)}/></label>}
    <div className="catalog-columns"><label>Callback port<input aria-label="Callback port" type="number" min={0} max={65535} value={Number(oauth.callback_port??0)} onChange={event=>updateOauth('callback_port',Number(event.target.value))}/><small>Use a fixed port for a registered callback. Zero lets the engine choose.</small></label><label>Callback path<input value={String(oauth.callback_path??'/oauth/callback')} onChange={event=>updateOauth('callback_path',event.target.value)}/></label></div>
    <label>Requested scopes<textarea aria-label="Requested scopes" value={Array.isArray(oauth.scope_allowlist)?oauth.scope_allowlist.join('\n'):''} onChange={event=>updateOauth('scope_allowlist',event.target.value.split(/\r?\n/).map(scope=>scope.trim()).filter(Boolean))}/></label>
    <p>Saving keeps these settings. You will review the issuer, callback, and requested scopes before signing in.</p>
    <button type="button" className="mcp-secret-link" onClick={()=>controller.navigate('keys')}>Manage API keys and secrets<ArrowUpRight size={12} aria-hidden="true"/></button>
   </fieldset>}
  </>}
  <label className="catalog-checkbox"><input type="checkbox" checked={form.enabled!==false} onChange={event=>update('enabled',event.target.checked)}/> Enabled</label>
  <details><summary>Authentication & advanced configuration</summary><p>Credential values belong in API keys. These settings contain references only.</p><label>Advanced configuration (JSON)<textarea value={advanced} onChange={event=>setAdvanced(event.target.value)}/></label></details>
  {!page.sessionId&&<p className="mcp-session-hint">Open a conversation to save this server and review its connection.</p>}
  <div className="catalog-actions"><button disabled={page.working||!page.sessionId}>Save server</button><button type="button" disabled={page.working} onClick={()=>{setForm(undefined);setStarter(undefined)}}>Cancel</button></div>
 </form>}
 {selected&&!form&&<section className="catalog-server-detail" aria-label="Server details"><div className="catalog-section-heading"><h2>{selected.display_name||selected.id}</h2><button onClick={()=>{setSelected(undefined);setReview(undefined)}}>Close details</button></div><p>{selected.transport} · {selected.state}</p><div className="catalog-actions"><button disabled={page.working||!page.sessionId} onClick={()=>{void edit()}}>Edit server</button>{selected.state==='connected'?<button disabled={page.working} onClick={()=>{void page.act<{server:Server}>('mcp.disconnect',{id:currentId},'Server disconnected.').then(result=>{if(result&&current())setSelected(result.server)})}}>Disconnect</button>:<button disabled={page.working||!page.sessionId} onClick={()=>{void connectionReview()}}>Review connection</button>}<button disabled={page.working||selected.state!=='connected'} onClick={()=>{void page.act<{tools:{name:string;description?:string}[]}>('mcp.tools',{id:currentId}).then(value=>{if(value&&current())setTools(value.tools)})}}>Inspect tools</button><button disabled={page.working} onClick={()=>setRemove(true)}>Remove server</button></div>
 {remove&&<div className="inline-confirm"><p>Remove {selected.display_name||selected.id} from this installation?</p><button disabled={page.working} onClick={()=>{void page.act<{removed:boolean}>('mcp.remove',{id:currentId}).then(result=>{if(result?.removed&&current()){setSelected(undefined);setRemove(false)}})}}>Confirm remove server</button><button disabled={page.working} onClick={()=>setRemove(false)}>Keep server</button></div>}
 {review&&<section className="catalog-review" aria-label="Review server connection"><h3>Exact connection to launch</h3><pre>{JSON.stringify(review.proposal,null,2)}</pre><p className="catalog-code">Digest: {review.digest}</p><div className="catalog-actions"><button disabled={page.working} onClick={()=>{void connect()}}>Connect reviewed server</button><button disabled={page.working} onClick={()=>setReview(undefined)}>Cancel connection</button></div></section>}
 {tools&&<div className="management-list">{tools.map(tool=><article key={tool.name}><strong>{tool.name}</strong><p>{tool.description}</p></article>)}{!tools.length&&<p>No tools advertised by this server.</p>}</div>}
 {selected.transport==='streamable_http'&&<details><summary>Account authorization</summary><div className="catalog-actions"><button disabled={page.working||!page.sessionId} onClick={()=>{void oauthPreview()}}>Review sign in</button><button disabled={page.working||!page.sessionId} onClick={()=>{void page.act<AuthState>('mcp.oauth.status',{id:currentId,...(auth?.operationId?{operationId:auth.operationId}:{})}).then(value=>{if(value&&current())setAuth(value)})}}>Refresh account status</button></div>{authReview?.issuers?.map(issuer=><button key={issuer} disabled={page.working} onClick={()=>{void oauthPreview(issuer)}}>Use {issuer}</button>)}{authReview?.previewId&&<div className="catalog-review"><pre>{JSON.stringify(authConsent,null,2)}</pre><p className="catalog-code">Digest: {authReview.digest}</p><button disabled={page.working} onClick={()=>{void oauthStart()}}>Start reviewed sign in</button></div>}{auth&&<><p className="catalog-auth-status">Status: {auth.state??'Authorization started'}</p>{auth.operationId&&<div className="catalog-actions"><button disabled={page.working} onClick={()=>{void page.act('mcp.oauth.open',{id:currentId,operationId:auth.operationId},'Sign-in opened in your browser.')}}>Open sign-in browser</button><button disabled={page.working} onClick={()=>{void page.act('mcp.oauth.cancel',{id:currentId,operationId:auth.operationId},'Sign-in cancelled.').then(value=>{if(value)setAuth(undefined)})}}>Cancel sign in</button></div>}{typeof auth.generation==='number'&&auth.generation>0&&<button disabled={page.working} onClick={()=>setForgetAuth(true)}>Forget account credentials</button>}{forgetAuth&&<div className="inline-confirm"><p>Forget the saved credentials for this server?</p><button disabled={page.working} onClick={()=>{void page.act('mcp.oauth.forget',{id:currentId,expectedGeneration:auth.generation},'Credentials forgotten.').then(value=>{if(value){setAuth(undefined);setForgetAuth(false)}})}}>Confirm forget credentials</button><button onClick={()=>setForgetAuth(false)}>Keep credentials</button></div>}</>}</details>}
 </section>}
 </div>;
 const add=<button disabled={!page.available||page.working} onClick={()=>startForm({...blank},false)}>Add MCP server</button>;
 if(embedded)return <div className="catalog-embedded"><div className="catalog-actions">{add}<button disabled={page.busy||page.working} onClick={refresh}>Refresh servers</button></div>{page.error&&<p role="alert" className="inline-error">{page.error}</p>}{page.notice&&<p role="status">{page.notice}</p>}{controls}</div>;
 return <FeatureFrame controller={controller} title="Connectors" description="Choose an account or local server to extend your agent’s tools. Every connection starts with a review." {...page} actions={add} onRefresh={refresh}>{controls}</FeatureFrame>;
}
