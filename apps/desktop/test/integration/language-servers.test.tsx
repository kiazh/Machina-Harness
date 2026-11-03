// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {EditorState} from '@codemirror/state';
import type {WorkspaceIntelligenceResult,WorkspaceLspStatusResult} from '@machina/contracts/workspace';
import type {WorkspaceIntelligenceOperation} from '@machina/contracts/workspace-gateway';
import type {DesktopController,DesktopSnapshot} from '../../src/state/controller.ts';
import {LanguageServersPage} from '../../src/features/settings/LanguageServersPage.tsx';

afterEach(cleanup);
const uri='file:///test/main.py';
const range={start:{line:7,character:4},end:{line:7,character:12}};
function result(operation='diagnostics',payload:unknown={diagnostics:[{range,severity:2,message:'Unknown value',source:'pyright',code:'reportUnknown'}]}):WorkspaceIntelligenceResult {
 return {workspaceId:'w',workspaceEpoch:1,uri,source:'editor-buffer',documentVersion:2,serverGeneration:1,operation,state:'ready',freshness:'versioned',truncated:false,result:payload};
}
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(reason:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject};}
function fixture(patch:Partial<DesktopSnapshot>={}){
 let state:DesktopSnapshot={page:'lsp',connection:'ready',workspace:{workspaceId:'w',clientId:'c',epoch:1,resolvedRoot:'/test',rootLabel:'test',trusted:true,capabilities:[]},model:'',provider:'',sessions:[],documents:[{id:uri,uri,name:'main.py',text:''}],activeId:uri,editorFocus:0,dirty:new Set(),running:false,queue:[],jobs:[],approvals:[],agentIds:[],error:'',...patch};
 const listeners=new Set<()=>void>();
 const lspStatus=vi.fn(async ():Promise<WorkspaceLspStatusResult>=>({servers:[{profile:'pyright',languageId:'python',state:'ready',generation:1}]}));
 const lspControl=vi.fn(async (_profile:string,_action:string)=>({profile:'pyright',action:'restart',state:'ready'}));
 const inspectDocument=vi.fn(async (operation:WorkspaceIntelligenceOperation)=>result(operation));
 const openLocation=vi.fn(async (_uri:string,_line:number,_character?:number,_expected?:WorkspaceIntelligenceResult)=>{});
 const isInspectionCurrent=vi.fn((_result:WorkspaceIntelligenceResult)=>true);
 const controller={snapshot:()=>state,subscribe:(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener)}},workspace:{lspStatus,lspControl},inspectDocument,openLocation,isInspectionCurrent,navigate:vi.fn(),connect:vi.fn()} as unknown as DesktopController;
 return {controller,lspStatus,lspControl,inspectDocument,openLocation,isInspectionCurrent,update(patch:Partial<DesktopSnapshot>){act(()=>{state={...state,...patch};listeners.forEach(listener=>listener())})}};
}
async function inspect(name:string){await waitFor(()=>expect((screen.getByRole('button',{name}) as HTMLButtonElement).disabled).toBe(false));fireEvent.click(screen.getByRole('button',{name}));}

it('shows diagnostic severity and source and opens its exact zero-based LSP position',async()=>{
 const f=fixture(),expected=result();f.inspectDocument.mockResolvedValueOnce(expected);render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');
 await screen.findByText('Unknown value');expect(screen.getByText(/Warning/)).toBeTruthy();expect(screen.getByText(/pyright.*reportUnknown/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:/Open main.py:8:5/}));
 await waitFor(()=>expect(f.openLocation).toHaveBeenCalledWith(uri,7,4,expected));expect(f.isInspectionCurrent).toHaveBeenCalledWith(expected);
});

it('opens nested symbols using the selection range and renders hover contents as inert text',async()=>{
 const f=fixture();f.inspectDocument.mockResolvedValueOnce(result('symbols',{hierarchical:[{name:'Container',kind:5,range,children:[{name:'run',kind:6,range,selectionRange:{start:{line:9,character:2},end:{line:9,character:5}}}]}],flat:[],omitted:0}));
 render(<LanguageServersPage controller={f.controller}/>);await inspect('File symbols');await screen.findByText('run');fireEvent.click(screen.getByRole('button',{name:/Open main.py:10:3/}));expect(f.openLocation).toHaveBeenCalledWith(uri,9,2,expect.objectContaining({operation:'symbols'}));
 f.inspectDocument.mockResolvedValueOnce(result('hover',{contents:[{language:'python',value:'def run() -> None'},'<script>unsafe()</script>']}));
 await inspect('Symbol information');await screen.findByText(/def run\(\) -> None/);expect(screen.getByText(/<script>unsafe\(\)<\/script>/)).toBeTruthy();expect(document.querySelector('script')).toBeNull();
});

it('navigates definition links and normalized reference locations but disables outside-workspace files',async()=>{
 const f=fixture();f.inspectDocument.mockResolvedValueOnce(result('definition',[{targetUri:'file:///test/lib.py',targetRange:range,targetSelectionRange:{start:{line:2,character:1},end:{line:2,character:5}}}]));
 render(<LanguageServersPage controller={f.controller}/>);await inspect('Definition');const definition=await screen.findByRole('button',{name:/Open lib.py:3:2/});fireEvent.click(definition);expect(f.openLocation).toHaveBeenCalledWith('file:///test/lib.py',2,1,expect.objectContaining({operation:'definition'}));
 f.inspectDocument.mockResolvedValueOnce(result('references',{locations:[{uri,range},{uri:'file:///other/private.py',range}],omitted:3}));
 await inspect('References');const external=await screen.findByRole('button',{name:/Open file:\/\/\/other\/private.py:8:5/});expect((external as HTMLButtonElement).disabled).toBe(true);expect(screen.getByText(/Outside current workspace/)).toBeTruthy();expect(screen.getByText(/3.*omitted/)).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:/Open main.py:8:5/}));expect(f.openLocation).toHaveBeenLastCalledWith(uri,7,4,expect.objectContaining({operation:'references'}));
});

it('keeps unavailable, stale and truncated results explicit and preserves unsupported payloads',async()=>{
 const f=fixture();f.inspectDocument.mockResolvedValueOnce({...result('diagnostics',null),state:'missing',freshness:'unavailable'});
 render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');await screen.findByText(/Results unavailable/);expect(screen.queryByText(/No problems/)).toBeNull();
 f.inspectDocument.mockResolvedValueOnce({...result('diagnostics'),freshness:'stale',truncated:true});await inspect('Problems');await screen.findByText(/Results are stale/);expect(screen.getByText(/Results truncated/)).toBeTruthy();
 f.inspectDocument.mockResolvedValueOnce(result('symbols',{unexpected:{server:'shape'}}));await inspect('File symbols');await screen.findByText(/Unsupported result format/);expect(screen.getByText(/"unexpected"/)).toBeTruthy();
});

it('explains offline and switching availability and never requests status or inspection while unavailable',async()=>{
 const f=fixture({connection:'offline'});render(<LanguageServersPage controller={f.controller}/>);expect(screen.getByRole('button',{name:'Connect engine'})).toBeTruthy();expect((screen.getByRole('button',{name:'Problems'}) as HTMLButtonElement).disabled).toBe(true);expect(f.lspStatus).not.toHaveBeenCalled();
 f.update({connection:'ready',switchingWorkspace:true});expect(screen.getByText(/finish opening/)).toBeTruthy();expect(f.lspStatus).not.toHaveBeenCalled();
 f.update({switchingWorkspace:false});await screen.findByText('python');await inspect('Problems');await screen.findByText('Unknown value');f.update({switchingWorkspace:true});expect(screen.queryByText('Unknown value')).toBeNull();expect((screen.queryByRole('button',{name:'start'}) as HTMLButtonElement| null)?.disabled??true).toBe(true);
});

it('drops inspection replies and errors from a file that is no longer active',async()=>{
 const f=fixture();const pending=deferred<WorkspaceIntelligenceResult>();f.inspectDocument.mockReturnValueOnce(pending.promise);
 render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');f.update({activeId:'file:///test/next.py',documents:[{id:'file:///test/next.py',uri:'file:///test/next.py',name:'next.py',text:''}]});
 await act(async()=>pending.resolve(result()));expect(screen.queryByText('Unknown value')).toBeNull();
 f.inspectDocument.mockResolvedValueOnce({...result('diagnostics',{diagnostics:[]}),uri:'file:///test/next.py'});await inspect('Problems');await screen.findByText(/No problems reported/);
 const failed=deferred<WorkspaceIntelligenceResult>();f.inspectDocument.mockReturnValueOnce(failed.promise);await inspect('Problems');f.update({activeId:uri,documents:[{id:uri,uri,name:'main.py',text:''}]});await act(async()=>failed.reject(new Error('Old file error')));expect(screen.queryByText('Old file error')).toBeNull();
});

it('drops old workspace status and control completions without refreshing or blocking the new workspace',async()=>{
 const f=fixture();const oldStatus=deferred<WorkspaceLspStatusResult>();f.lspStatus.mockReturnValueOnce(oldStatus.promise);render(<LanguageServersPage controller={f.controller}/>);
 f.update({workspace:{...f.controller.snapshot().workspace!,workspaceId:'new',epoch:2}});await screen.findByText('python');await act(async()=>oldStatus.resolve({servers:[{profile:'obsolete',languageId:'obsolete',state:'ready',generation:1}]}));expect(screen.queryByText('obsolete')).toBeNull();
 const control=deferred<{profile:string;action:string;state:string}>();f.lspControl.mockReturnValueOnce(control.promise);fireEvent.click(screen.getByRole('button',{name:'restart'}));
 f.update({workspace:{...f.controller.snapshot().workspace!,workspaceId:'latest',epoch:3}});await waitFor(()=>expect(f.lspStatus).toHaveBeenCalledTimes(3));await act(async()=>control.resolve({profile:'pyright',action:'restart',state:'ready'}));expect(f.lspStatus).toHaveBeenCalledTimes(3);await inspect('Problems');expect(f.inspectDocument).toHaveBeenCalledTimes(1);
});

it('disables methods an engine does not advertise and reports navigation errors inline',async()=>{
 const unsupported=fixture({methods:new Set(['workspace.lspStatus'])});const view=render(<LanguageServersPage controller={unsupported.controller}/>);await screen.findByText('python');expect((screen.getByRole('button',{name:'Problems'}) as HTMLButtonElement).disabled).toBe(true);expect((screen.getByRole('button',{name:'restart'}) as HTMLButtonElement).disabled).toBe(true);expect(unsupported.inspectDocument).not.toHaveBeenCalled();view.unmount();
 const f=fixture();f.openLocation.mockRejectedValueOnce(new Error('File is outside the authorized workspace root'));render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');fireEvent.click(await screen.findByRole('button',{name:/Open main.py:8:5/}));await screen.findByRole('alert');expect(screen.getByRole('alert').textContent).toContain('outside the authorized workspace');
});

it('does not revive a prior file result after switching away and back or show no-problems before a report',async()=>{
 const f=fixture();const pending=deferred<WorkspaceIntelligenceResult>();f.inspectDocument.mockReturnValueOnce(pending.promise);render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');
 f.update({activeId:'other'});f.update({activeId:uri});await act(async()=>pending.resolve(result()));expect(screen.queryByText('Unknown value')).toBeNull();
 f.inspectDocument.mockResolvedValueOnce({...result('diagnostics',null),freshness:'unversioned'});await inspect('Problems');await screen.findByText(/No diagnostic report has been published/);expect(screen.queryByText(/No problems reported/)).toBeNull();expect(screen.getByText(/did not confirm a document version/)).toBeTruthy();
});

it('explains workspace trust and blocks inspection of a retained file from a different workspace',async()=>{
 const f=fixture();f.update({workspace:{...f.controller.snapshot().workspace!,trusted:false}});render(<LanguageServersPage controller={f.controller}/>);expect(screen.getByText(/review trust for this folder/)).toBeTruthy();expect(f.lspStatus).not.toHaveBeenCalled();
 f.update({workspace:{...f.controller.snapshot().workspace!,trusted:true,resolvedRoot:'/other'}});await screen.findByText('python');expect(screen.getByText(/active file is outside the current workspace/)).toBeTruthy();expect((screen.getByRole('button',{name:'Problems'}) as HTMLButtonElement).disabled).toBe(true);
});

it('disables stale location buttons and cannot navigate when a stale row is clicked',async()=>{
 const f=fixture();f.inspectDocument.mockResolvedValueOnce({...result('diagnostics'),freshness:'stale'});render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');
 const button=await screen.findByRole('button',{name:/Open main.py:8:5/});fireEvent.click(button);expect(f.openLocation).not.toHaveBeenCalled();expect((button as HTMLButtonElement).disabled).toBe(true);expect(screen.getByText(/Results are stale/)).toBeTruthy();
});

it('rechecks the source document before navigation and asks for fresh results if it changed',async()=>{
 const f=fixture(),expected=result();f.inspectDocument.mockResolvedValueOnce(expected);render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');const button=await screen.findByRole('button',{name:/Open main.py:8:5/});
 f.isInspectionCurrent.mockReturnValue(false);fireEvent.click(button);expect(f.isInspectionCurrent).toHaveBeenCalledWith(expected);expect(f.openLocation).not.toHaveBeenCalled();expect((await screen.findByRole('alert')).textContent).toMatch(/changed.*fresh results/i);
});

it.each([{revision:'new-revision'},{reloadToken:1}])('removes old rows and drops pending replies when the active file changes on disk: %j',async change=>{
 const f=fixture();render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');await screen.findByText('Unknown value');
 f.update({documents:f.controller.snapshot().documents.map(document=>({...document,...change}))});expect(screen.queryByText('Unknown value')).toBeNull();
 const pending=deferred<WorkspaceIntelligenceResult>();f.inspectDocument.mockReturnValueOnce(pending.promise);await inspect('Problems');f.update({documents:f.controller.snapshot().documents.map(document=>({...document,...('revision' in change?{revision:'latest-revision'}:{reloadToken:2})}))});await act(async()=>pending.resolve(result()));expect(screen.queryByText('Unknown value')).toBeNull();
});

it('retains a same-code result after the editor selection changes',async()=>{
 const initialState=EditorState.create({doc:'print(value)'}),f=fixture({documents:[{id:uri,uri,name:'main.py',text:'print(value)',initialState,revision:'same',reloadToken:0}]});render(<LanguageServersPage controller={f.controller}/>);await inspect('Problems');await screen.findByText('Unknown value');
 const moved=initialState.update({selection:{anchor:5}}).state;expect(moved.doc).toBe(initialState.doc);f.update({documents:f.controller.snapshot().documents.map(document=>({...document,initialState:moved}))});expect(screen.getByText('Unknown value')).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:/Open main.py:8:5/}));expect(f.openLocation).toHaveBeenCalledWith(uri,7,4,expect.objectContaining({operation:'diagnostics'}));
});
