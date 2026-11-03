// Synthetic integration fixture. This entry is outside the production build.
import { createRoot } from "react-dom/client";
import { App } from "../../src/app/App.tsx";
import { DesktopController } from "../../src/state/controller.ts";
import type { DesktopBridge } from "../../src/bridge/types.ts";
import type {AgentMessage} from '@machina/contracts/rpc';
import {RPC_METHODS} from '@machina/contracts/rpc-methods';

let emit: (event: Record<string, unknown>) => void = () => {};
const source = `from dataclasses import dataclass\nfrom pathlib import Path\n\n\n@dataclass(frozen=True)\nclass Workspace:\n    root: Path\n    trusted: bool = False\n\n    def files(self) -> list[Path]:\n        \"\"\"Return the Python files in this workspace.\"\"\"\n        return sorted(self.root.rglob("*.py"))\n\n\ndef open_workspace(path: str) -> Workspace:\n    root = Path(path).resolve()\n    if not root.is_dir():\n        raise ValueError("Choose an existing directory")\n    return Workspace(root=root)\n`;
let jobStatus = "running";
let foreground = true;
let memories=[{id:'fixture-memory',content:'Prefer small focused changes',category:'preference',importance:.8,accessCount:1,createdAt:'2026-11-01T00:00:00Z',sessionId:'fixture-session'}];
let scheduled:Record<string,unknown>[]=[];
let definitions:Record<string,unknown>[]=[];
let delegationSequence=0;
const delegationMessages:(AgentMessage&{sessionId:string})[]=[];
const trackedFixture=new URLSearchParams(location.search).has('tracked');
const catalogSkills=Array.from({length:8},(_,index)=>({id:`fixture/skills/${index?'workflow-'+index:'design-basics'}`,skillSlug:index?'workflow-'+index:'design-basics',name:index?'fixture-workflow-'+index:'fixture-design-basics',source:'fixture/skills',description:'Synthetic catalog entry for browser verification.',repository:'https://github.com/fixture/skills',url:'https://skills.sh/fixture/skills/design-basics',markets:[index%2?'skillsmp':'skills.sh'],metrics:[{market:index%2?'skillsmp':'skills.sh',label:index%2?'GitHub stars':'installs',value:4200-index*310}]}));
const catalogSources=[{id:'skills.sh',name:'skills.sh',url:'https://skills.sh',status:'ready'},{id:'skillsmp',name:'SkillsMP',url:'https://skillsmp.com',status:'ready'}];
let trackedOperation:Record<string,unknown>|undefined;
let trackedCompletion=0;
const bridge: DesktopBridge = {
  async start() {},
  subscribe(listener) { emit = listener; return () => {}; },
  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if(method.startsWith('skills.marketplace.')){
      const target=window as unknown as {fixtureMarketplaceCalls?:{method:string;params:Record<string,unknown>}[]};(target.fixtureMarketplaceCalls??=[]).push({method,params});
      if(method==='skills.marketplace.discover')return {featured:catalogSkills.slice(0,3),sections:[{market:'skills.sh',title:'Popular on skills.sh',description:'Ranked by installations',skills:catalogSkills.filter(row=>row.markets[0]==='skills.sh')},{market:'skillsmp',title:'Development on SkillsMP',description:'Development skills, sorted by repository stars',skills:catalogSkills.filter(row=>row.markets[0]==='skillsmp')}],markets:catalogSources} as T;
      if(method==='skills.marketplace.search')return {query:params.query,skills:catalogSkills.filter(row=>(!params.markets||(params.markets as string[]).includes(row.markets[0]))&&row.name.toLowerCase().includes(String(params.query).toLowerCase())),markets:catalogSources} as T;
      if(method==='skills.marketplace.preview')return {reviewId:'fixture-review',digest:'fixture-digest',source:'https://github.com/fixture/skills',revision:'fixture-revision',scope:'installation',skills:[{name:params.skill,description:'Synthetic reviewed skill',content:'Read these synthetic instructions before installing.',files:[]}]} as T;
    }
    if(method==='operations.start'){
      const target=window as unknown as {fixtureOperationStarts?:number};target.fixtureOperationStarts=(target.fixtureOperationStarts??0)+1;
      const result=await bridge.request(String(params.method),params.params as Record<string,unknown>);
      trackedOperation={id:'fixture-operation',method:params.method,sessionId:(params.params as Record<string,unknown>).sessionId,status:'running',cancellable:true,startedAt:new Date().toISOString(),result};trackedCompletion=Infinity;
      Object.assign(window,{fixtureFinishOperation:()=>{trackedCompletion=0}});
      const {result:_,...pending}=trackedOperation;return pending as T;
    }
    if(method==='operations.get'||method==='operations.cancel'){
      if(!trackedOperation||params.id!==trackedOperation.id)throw new Error('Unknown fixture operation');
      if(method==='operations.cancel')trackedOperation={...trackedOperation,status:'cancelled',result:undefined};
      else if(trackedOperation.status==='running'&&Date.now()>=trackedCompletion)trackedOperation={...trackedOperation,status:'completed'};
      if(trackedOperation.status==='running'){const {result:_,...pending}=trackedOperation;return pending as T}return trackedOperation as T;
    }
    if(method==='session.history'){
      const target=window as unknown as {fixtureHistoryCalls?:Record<string,unknown>[]};(target.fixtureHistoryCalls??=[]).push(params);
      const all=Array.from({length:125},(_,index)=>({id:`history-${125-index}`,role:index%3===0?'user':'assistant',content:`Recorded history message ${125-index}. Synthetic fixture only.`,createdAt:new Date(Date.UTC(2026,10,2,12,0)-index*60000).toISOString()}));
      const matching=all.filter(row=>!params.query||row.content.toLowerCase().includes(String(params.query).toLowerCase()));const offset=params.cursor==='fixture-older'?100:0,limit=Math.min(100,Number(params.limit)||100),entries=matching.slice(offset,offset+limit);
      return {entries,nextCursor:offset+entries.length<matching.length?'fixture-older':null} as T;
    }
    if(method==='session.export')return {markdown:'# Synthetic fixture transcript\n\nRecorded messages.'} as T;
    if(method==='prompt.submit')Object.assign(window,{fixtureLastPrompt:params});
    if(method==='workspace.lspStatus')return {workspaceId:'fixture',epoch:1,servers:[{languageId:'python',profile:'python',state:'ready',generation:1,detail:'Synthetic language server fixture'}]} as T;
    if(method==='workspace.lspControl')return {profile:params.profile,state:'ready',generation:2} as T;
    if(method==='workspace.intelligence'){
      const range={start:{line:5,character:4},end:{line:5,character:9}};
      const result=params.operation==='completion'?{items:[{label:'Path',insertText:'Path'}]}:params.operation==='definition'?[{uri:'file:///sample-project/src/settings.py',range}]:params.operation==='references'?[{uri:params.uri,range}]:params.operation==='diagnostics'?{diagnostics:[{range,severity:2,message:'Synthetic warning for navigation acceptance',source:'fixture'}]}:params.operation==='symbols'?{hierarchical:[{name:'Workspace',kind:5,range,selectionRange:range}]}:params.operation==='hover'?{contents:{kind:'plaintext',value:'class Workspace: fixture symbol'}}:[];
      return {workspaceId:'fixture',workspaceEpoch:1,uri:params.uri,source:'editor-buffer',documentVersion:params.version,serverGeneration:1,operation:params.operation,state:'ready',freshness:'versioned',truncated:false,result} as T;
    }
    if(method==='jobs.list')return {jobs:scheduled} as T;
    if(method==='jobs.create'){const job={...params,id:'fixture-schedule',enabled:true,status:'idle',runCount:0};scheduled.push(job);return {job} as T;}
    if(method==='jobs.setEnabled'){scheduled=scheduled.map(row=>row.id===params.id?{...row,enabled:params.enabled}:row);return {job:scheduled.find(row=>row.id===params.id)} as T;}
    if(method==='jobs.delete'){scheduled=scheduled.filter(row=>row.id!==params.id);return {deleted:true} as T;}
    if(method==='agents.list')return {agents:definitions} as T;
    if(method==='agents.save'){const agent={...params,source:'db'};definitions=[...definitions.filter(row=>row.name!==params.name),agent];return {agent} as T;}
    if(method==='agents.delete'){definitions=definitions.filter(row=>row.name!==params.name);return {deleted:true} as T;}
    if(method==='agents.run'){
      if(!params.sessionId||!Array.isArray(params.steps)||!params.steps.length||params.steps.length>16||!['sequential','parallel'].includes(String(params.mode)))throw new Error('Invalid fixture delegation');
      const results=params.steps.map((step:{agent:string;task:string},index:number)=>{
        if(!step.agent||!step.task||!definitions.some(agent=>agent.name===step.agent))throw new Error('Unknown fixture delegation agent or task');
        const id=++delegationSequence,response=`Synthetic ${params.mode} delegation result ${index+1}`;
        delegationMessages.push({id:`fixture-delegation-${id}`,sessionId:String(params.sessionId),fromAgent:'orchestrator',toAgent:step.agent,task:step.task,response,status:'complete',tokens:0,createdAt:'2026-11-02T12:00:00Z'});
        return {...step,response,sessionId:`fixture-child-${id}`,status:'complete',tokens:0,iterations:1,needsApproval:[]};
      });
      return {results} as T;
    }
    if(method==='agents.history')return {messages:delegationMessages.filter(row=>row.sessionId===params.sessionId).slice(0,Number(params.limit??50)).map(({sessionId,...row})=>row)} as T;
    if(method==='context.get')return {totalTokens:50000,budget:200000,goal:'Keep the workspace reliable',chunks:[{type:'user',agent:'harness',tokens:200,createdAt:'2026-11-02T00:00:00Z',preview:'Check file lifecycle'}]} as T;
    if(method==='memory.list'||method==='memory.search')return {memories:memories.filter(row=>(!params.category||row.category===params.category)&&(!params.query||row.content.toLowerCase().includes(String(params.query).toLowerCase()))),total:memories.length} as T;
    if(method==='memory.add'){const memory={...memories[0]!,id:'fixture-memory-new',content:String(params.content),category:String(params.category),importance:Number(params.importance)};memories.push(memory);return {status:'stored',memory} as T;}
    if(method==='memory.forget'){memories=memories.filter(row=>row.id!==params.id);return {deleted:true} as T;}
    if(method==='memory.share')return {status:'delivered'} as T;
    const job = () => ({ id: "fixture-job", command: "python -m pytest tests/workspace", status: jobStatus, foreground, output: "Synthetic command output · fixture only\nCollecting workspace checks…\n" });
    const responses: Record<string, unknown> = {
      "runtime.capabilities": { engineVersion: "fixture", protocolVersion: 2, storageProfile: "postgres", database: "ready", methods: trackedFixture?RPC_METHODS:[] },
      initialize: { cwd: "sample-project", cwdFull: "/sample-project", model: "Test model", provider: "fixture" },
      "config.get": { config: { model: "Test model", provider: "Synthetic fixture", temperature: .7 } },
      "session.list": { sessions: [] },
      "session.create": { session: { id: "fixture-session", title: "Synthetic workspace check", model: "Test model", provider: "fixture" } },
      "workspace.open": { workspaceId: "fixture", clientId: "fixture", epoch: 1, rootLabel: "sample-project · test fixture", resolvedRoot: "/sample-project", trusted: true, capabilities: [] },
      "workspace.list": { entries: (params.path ? ["__init__.py", "workspace.py", "settings.py"] : ["src/", "tests/", "README.md", "pyproject.toml"]).map(name => ({ name: name.replace(/\/$/, ""), path: `${params.path ? `${params.path}/` : ""}${name.replace(/\/$/, "")}`, kind: name.endsWith("/") ? "directory" : "file" })), nextCursor: null, truncated: false, exclusions: [] },
      "workspace.read": { uri: params.uri, text: source, revision: "fixture-revision", languageId: "python", lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: source.length },
      "workspace.documentOpen": { acceptedVersion: 0, serverGeneration: 1, state: "ready" },
      "workspace.documentChange": { acceptedVersion: params.version },
      "workspace.save": { ok: true, revision: "fixture-saved" },
      "prompt.submit": { turnId: "fixture-turn" },
      "prompt.steer": { queued: true, items: [{ id: "fixture-queue", text: params.text }] },
      "prompt.cancel": {},
      "prompt.steering": { items: params.replace?[{id:params.replace,text:params.text}]:[] },
      'workspace.quickOpen':{entries:[{name:'workspace.py',path:'src/workspace.py',directory:'src'}],total:1,truncated:false,indexing:false,scanned:1,stale:false,workspaceEpoch:1,reasons:[],exclusions:[]},
      'workspace.documentClose':{closed:true},
      'skills.list':{skills:[{name:'Fixture skill',description:'Synthetic catalog entry for browser verification',sourceType:'file',enabled:true,usageCount:3,triggers:[],version:'1'}]},
      'skills.show':{skill:{name:'Fixture skill',description:'Synthetic catalog entry',content:'Fixture skill instructions',enabled:true}},
      'providers.list':{providers:[{name:'fixture',kind:'builtin',keySet:false}]},
      'models.list':{provider:'fixture',source:'fixture-cache',fetchError:'Synthetic live catalog warning',models:[{id:'fixture-new-model',displayName:'Alternate fixture model',contextWindow:200000,efforts:['low','high']}]},
      'session.setModel':{sessionId:'fixture-session',model:params.model,provider:params.provider},
      'config.set':{key:params.key,value:params.value},
    };
    if (method === "shell.jobs") {
      if (params.background) foreground = false;
      if (params.stop) jobStatus = "stopped";
      return { job: job() } as T;
    }
    if (!((method==='runtime.selectWorkspace'?'workspace.open':method) in responses)) throw new Error(`Fixture does not implement ${method}`);
    return responses[method==='runtime.selectWorkspace'?'workspace.open':method] as T;
  },
};
const controller = new DesktopController(bridge);
await controller.connect();
await controller.openFile("src/workspace.py");
await controller.send("Review how the workspace opens files and retains local changes.");
emit({ type: "message.start", sessionId: "fixture-session", turnId: "fixture-turn" });
emit({ type: "message.delta", sessionId: "fixture-session", turnId: "fixture-turn", text: "I’ll trace the file lifecycle first, then check how local edits are retained when the agent updates the workspace." });
emit({ type: "tool.start", sessionId: "fixture-session", turnId: "fixture-turn", id: "read", name: "Read src/workspace.py", args: { path: "src/workspace.py" } });
emit({ type: "tool.complete", sessionId: "fixture-session", turnId: "fixture-turn", id: "read", name: "Read src/workspace.py", result: source });
emit({ type: "message.delta", sessionId: "fixture-session", turnId: "fixture-turn", text: "The path is resolved before opening. Next I’m checking the save boundary so a changed file on disk cannot silently replace your local buffer." });
emit({ type: "shell.job", sessionId: "fixture-session", job: { id: "fixture-job", command: "python -m pytest tests/workspace", status: "running", foreground: true } });
createRoot(document.getElementById("root")!).render(<App controller={controller}/>);
Object.assign(window, { fixtureController: controller, fixtureEmit: emit });
