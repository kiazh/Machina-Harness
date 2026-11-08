import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { Code2, FilePlus2, FolderOpen, MoreHorizontal, PanelLeft, Save, Settings2, Terminal, X, Search } from "lucide-react";
import type { EditorView } from "@codemirror/view";
import { Harness } from "../features/harness/Harness.tsx";
import type { DesktopController } from "../state/controller.ts";
import { Sidebar } from "./Sidebar.tsx";
import { useDesktopLifecycle } from "./useDesktopLifecycle.ts";
import { SettingsDialog } from "./SettingsDialog.tsx";
import { AppearanceStore, wallpaperStorage } from "../state/appearance.ts";
import { AppearanceContext } from "../design/AppearanceContext.ts";
import { FeaturePage } from '../features/catalog/LazyFeaturePage.tsx';
import {TerminalDock} from '../features/terminal/TerminalDock.tsx';
import {PaneResize} from './PaneResize.tsx';
import {WorkspaceDialog} from './WorkspaceDialog.tsx';
import {QuickOpen} from './QuickOpen.tsx';
import {DocumentTabs} from './DocumentTabs.tsx';
import {SaveAsDialog} from './SaveAsDialog.tsx';
import {SessionTools} from './SessionTools.tsx';
import {LayoutStore} from '../state/layout.ts';
import {ShortcutStore,SHORTCUT_COMMANDS,matchesShortcut,formatShortcut,type ShortcutAction} from '../state/shortcuts.ts';
import {CommandPalette} from '../features/commands/CommandPalette.tsx';
import {paletteCommands} from '../features/commands/catalog.ts';
import {PAGE_LABELS,type DesktopPage} from '../state/commands.ts';
import "../design/base.css";
import "../design/personal.css";
import "../design/quiet.css";
import '../design/atelier.css';
import '../design/settings.css';
import '../design/polish.css';
import '../design/workspaces.css';
import '../features/terminal/interactive-terminal.css';
const CodeEditor = lazy(() => import("../features/editor/CodeEditor.tsx").then(module => ({ default: module.CodeEditor })));
const ConversationHistory=lazy(()=>import('../features/sessions/ConversationHistory.tsx').then(module=>({default:module.ConversationHistory})));
const InteractiveTerminal=lazy(()=>import('../features/terminal/InteractiveTerminal.tsx').then(module=>({default:module.InteractiveTerminal})));

export function App({ controller }: { controller: DesktopController }) {
  const state = useSyncExternalStore(controller.subscribe, controller.snapshot);
  const [layout]=useState(()=>{try{return new LayoutStore(localStorage)}catch{return new LayoutStore()}});
  const arrangement=useSyncExternalStore(layout.subscribe,layout.snapshot);
  const {sidebar:storedSidebar,editor,terminal,editorWidth}=arrangement;
  const [compact,setCompact]=useState(()=>window.matchMedia("(max-width: 1100px)").matches);
  const sidebar=storedSidebar&&!(editor&&compact);
  const setSidebar=(value:boolean|((previous:boolean)=>boolean))=>layout.update({sidebar:typeof value==='function'?value(layout.snapshot().sidebar):value});
  const setEditor=(value:boolean|((previous:boolean)=>boolean))=>layout.update({editor:typeof value==='function'?value(layout.snapshot().editor):value});
  const setTerminal=(value:boolean|((previous:boolean)=>boolean))=>layout.update({terminal:typeof value==='function'?value(layout.snapshot().terminal):value});
  const toggleSidebar=()=>{const show=!sidebar;if(show&&editor&&compact)setEditor(false);setSidebar(show)};
  const resizeEditor=useCallback((width:number)=>layout.update({editorWidth:width}),[layout]);
  const [shortcuts]=useState(()=>{try{return new ShortcutStore(localStorage)}catch{return new ShortcutStore()}});
  const bindings=useSyncExternalStore(shortcuts.subscribe,shortcuts.snapshot);
  const [terminalEngaged,setTerminalEngaged]=useState(false),[terminalTab,setTerminalTab]=useState<'commands'|'shell'>('commands'),[shellMounted,setShellMounted]=useState(false);
  const terminalShown=terminal&&(editor||terminalEngaged)&&state.page==='chat';
  const toggleTerminal=()=>{controller.navigate('chat');setTerminal(!terminalShown);setTerminalEngaged(true);if(!terminalShown){setTerminalTab('shell');setShellMounted(true)}};
  const previousFocus = useRef(state.editorFocus);
  const editorView=useRef<EditorView>(null);
  const [editorMounted, setEditorMounted] = useState(editor);
  const workspaceDialog=useRef<HTMLDialogElement>(null);
  const [liveShells,setLiveShells]=useState(0);
  const quickOpen=useRef<HTMLDialogElement>(null);
  const reloadFile=useRef<HTMLDialogElement>(null);
  const [reloading,setReloading]=useState(false);
  const saveAs=useRef<HTMLDialogElement>(null);
  const closeTab=useRef<HTMLDialogElement>(null);
  const [closingTab,setClosingTab]=useState('');
  const settings = useRef<HTMLDialogElement>(null);
  const commandPalette=useRef<HTMLDialogElement>(null);
  const sessionTools=useRef<HTMLDialogElement>(null);
  const [sessionToolsAction,setSessionToolsAction]=useState('rename'),[sessionToolsToken,setSessionToolsToken]=useState(0);
  const openSessionTools=(action='rename')=>{setSessionToolsAction(action);setSessionToolsToken(value=>value+1);sessionTools.current?.showModal()};
  const historyDialog=useRef<HTMLDialogElement>(null);
  const [historyMounted,setHistoryMounted]=useState(false),[historyToken,setHistoryToken]=useState(0),[historyQuery,setHistoryQuery]=useState('');
  const [appearance] = useState(() => { try { return new AppearanceStore(localStorage); } catch { return new AppearanceStore(); } });
  const preferences = useSyncExternalStore(appearance.subscribe, appearance.snapshot);
  const [wallpaper, setWallpaper] = useState("");
  const [wallpaperRevision, setWallpaperRevision] = useState(0);
  const lifecycle = useDesktopLifecycle(controller,liveShells>0);
  useEffect(()=>{if(state.page==='settings'){settings.current?.showModal();controller.navigate('chat')}},[state.page,controller]);
  useEffect(()=>{const action=state.uiAction;if(!action)return;if(action.name==='editor'){settings.current?.close();controller.navigate('chat');setEditor(true)}else if(action.name==='quickOpen')quickOpen.current?.showModal();else if(action.name==='workspace')workspaceDialog.current?.showModal();else if(action.name==='terminal')toggleTerminal();else if(action.name==='sessionTools')openSessionTools(action.query);else if(action.name==='history'){setHistoryMounted(true);setHistoryQuery(action.query??'');setHistoryToken(value=>value+1)}controller.consumeUI(action.id)},[state.uiAction]);
  const active = state.documents.find(doc => doc.id === state.activeId) ?? state.documents[0]!;
  const openIds=useMemo(()=>state.documents.map(document=>document.id),[state.documents]);
  const ready = useCallback((view: EditorView) => {editorView.current=view;controller.bindEditor(controller.snapshot().activeId, view.state)}, [controller]);
  const change = useCallback((id: string, update: Parameters<DesktopController["changed"]>[1]) => controller.changed(id, update), [controller]);
  const selection=useCallback((id:string,state:import("@codemirror/state").EditorState)=>controller.bindEditor(id,state),[controller]);
  const complete = useCallback<import("../features/editor/completion.ts").IntelligenceRequest>((id, state, position) => controller.documents.completion(id, state, position).catch(() => null), [controller]);
  const diagnostics=useCallback<import('../features/editor/diagnostics.ts').DiagnosticsRequest>((id,state,signal)=>controller.documents.diagnostics(id,state,signal),[controller]);
  useEffect(()=>{
    if(!editorMounted||!editor)return;
    let active=true;
    const unsubscribe=controller.bridge.subscribe(event=>{
      if(event.type!=='workspace.diagnostics')return;
      const workspace=controller.workspace.identity();
      if(!workspace||event.workspaceId!==workspace.workspaceId||event.epoch!==workspace.epoch||event.uri!==controller.snapshot().activeId)return;
      void import('../features/editor/diagnostics.ts').then(module=>{if(active&&editorView.current)editorView.current.dispatch({effects:module.refreshDiagnostics.of(null)})});
    });
    return()=>{active=false;unsubscribe()};
  },[controller,editorMounted,editor]);
  useEffect(() => {
    if (previousFocus.current !== state.editorFocus) { previousFocus.current = state.editorFocus; controller.navigate('chat');setEditor(true); }
  }, [state.editorFocus]);
  useEffect(() => { if (editor) setEditorMounted(true); }, [editor]);
  useEffect(()=>{if(state.dirty.size>0)setEditor(true)},[]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1100px)");
    const adapt=()=>setCompact(media.matches);
    adapt();media.addEventListener("change",adapt);return()=>media.removeEventListener("change",adapt);
  }, []);
  useEffect(() => {
    if (preferences.background !== "custom") { setWallpaper(""); return; }
    let mounted = true; let url = "";
    void wallpaperStorage().then(blob => {
      if (!mounted) return;
      if (blob) { url = URL.createObjectURL(blob); setWallpaper(url); } else setWallpaper("");
    }).catch(error => { if (mounted) controller.fail(error); });
    return () => { mounted = false; if (url) URL.revokeObjectURL(url); };
  }, [preferences.background, wallpaperRevision, controller]);
  function runShortcut(action:ShortcutAction){
    const current=controller.snapshot();
    if(action==='openCommands')commandPalette.current?.showModal();
    else if(action==='openChanges')controller.navigate('changes');
    else if(action==='quickOpen')quickOpen.current?.showModal();
    else if(action==='saveAs'){setEditor(true);saveAs.current?.showModal()}
    else if(action==='save'){if(!current.documents.find(d=>d.id===current.activeId)?.uri){setEditor(true);saveAs.current?.showModal()}else if(current.dirty.has(current.activeId))void controller.save().catch(error=>controller.fail(error))}
    else if(action==='newDocument')controller.newDocument();
    else if(action==='requestCompletion'){controller.navigate('chat');setEditor(true);requestAnimationFrame(()=>{const view=editorView.current;if(view)void import('@codemirror/autocomplete').then(module=>{view.focus();module.startCompletion(view)})})}
    else if(action==='newSession'){if(current.running)controller.fail('Stop the active turn before starting a new conversation.');else controller.newSession()}
    else if(action==='attachSelection'){try{controller.requestSelectionAttachment()}catch(error){controller.fail(error)}}
    else if(action==='openSettings')settings.current?.showModal();
    else if(action==='toggleSidebar')toggleSidebar();
    else if(action==='toggleEditor'){controller.navigate('chat');setEditor(value=>current.page!=='chat'||!value)}
    else if(action==='toggleTerminal')toggleTerminal();
    else if(action==='closeAgentTabs')controller.dismissAgent();
    else if(action==='interrupt')void controller.interrupt().catch(error=>controller.fail(error));
  }
  const availableCommands=useMemo(()=>paletteCommands(state,bindings),[state.connection,state.running,state.workspace,state.backgroundOperation,state.switchingSession,state.switchingWorkspace,state.activeId,state.documents,state.agentIds.length,state.methods,state.session,bindings]);
  function runPaletteCommand(id:string){
    const command=paletteCommands(controller.snapshot(),bindings).find(command=>command.id===id);
    if(!command||command.disabledReason)return;
    if(id.startsWith('shortcut:')){const action=SHORTCUT_COMMANDS.find(command=>command.id===id.slice(9));if(action)runShortcut(action.id)}
    else if(id.startsWith('page:')&&Object.hasOwn(PAGE_LABELS,id.slice(5)))controller.navigate(id.slice(5) as DesktopPage);
    else if(id==='workspace:choose')workspaceDialog.current?.showModal();
    else if(id==='conversation:history'){setHistoryMounted(true);setHistoryQuery('');setHistoryToken(value=>value+1)}
  }
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if(event.isComposing||event.defaultPrevented||document.querySelector('dialog[open], [role=dialog], [role=alertdialog], [data-select-popup]'))return;
      for(const command of SHORTCUT_COMMANDS){if(!matchesShortcut(event,bindings[command.id]))continue;
       if(command.id==='interrupt'&&((!controller.snapshot().running&&!controller.snapshot().backgroundOperation)||document.querySelector('.cm-tooltip')))continue;
       event.preventDefault();runShortcut(command.id);return;
      }
    };
    window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener);
  }, [controller,bindings,terminalShown,sidebar,compact,editor]);
  const style = {
    "--editor-width":editor?`min(${editorWidth}vw, calc(100vw - var(--sidebar-width) - 260px))`:'0px',
    '--sidebar-width':sidebar?arrangement.sidebarWidth+'px':'0px','--terminal-height':arrangement.terminalHeight+'px',
    "--code-size": preferences.codeSize + "px", "--ui-size": preferences.uiSize + "px",
    "--ui-font": preferences.uiFont + ', "Segoe UI", -apple-system, system-ui, sans-serif',
    "--code-font": preferences.codeFont + ', "SFMono-Regular", Consolas, monospace',
    "--accent": preferences.accent, "--glass": preferences.glass, "--glass-blur": preferences.blur + "px",
    ...(preferences.background === "custom" ? { "--wallpaper": wallpaper ? 'url("' + wallpaper + '")' : "none" } : {}),
  } as CSSProperties;
  return <AppearanceContext value={preferences}><div className="desktop-app" data-sidebar={sidebar} data-editor={editor} data-editor-side={arrangement.editorSide} data-density={arrangement.density} data-preset={preferences.preset} data-background={preferences.background} data-motion={preferences.motion} style={style}>
    <div className="wallpaper-layer" aria-hidden="true"/><div className="appearance-scrim" aria-hidden="true"/>
    <header className="app-header"><button className="icon-button" aria-label="Toggle sidebar" aria-pressed={sidebar} onClick={toggleSidebar}><PanelLeft size={15}/></button><button className="icon-button" aria-label="Open workspace" disabled={state.connection==='connecting'} title="Choose a workspace folder" onClick={()=>workspaceDialog.current?.showModal()}><FolderOpen size={14}/></button><div className="workspace-breadcrumb"><span>{state.page==='chat'?(state.session?.title||'New session'):PAGE_LABELS[state.page]}</span></div>{state.session&&<button className="session-tools-toggle" aria-label="Conversation tools" onClick={()=>openSessionTools()}><MoreHorizontal size={15}/></button>}<div className="header-actions"><button className="icon-button" aria-label="Search commands" title={`Search commands · ${formatShortcut(bindings.openCommands)}`} onClick={()=>commandPalette.current?.showModal()}><Search size={15}/></button>{(state.page!=='chat'||state.backgroundOperation)&&(state.running||state.backgroundOperation||state.approvals.length>0)&&<div className="global-run-status"><button onClick={()=>controller.navigate(state.backgroundOperation?.method==='agents.run'?'agents':state.backgroundOperation?'interop':'chat')}>{state.approvals.length||state.backgroundOperation?.approvals?.length?'Approval needed':state.backgroundOperation?'Background run':'Agent working'}</button>{(state.running||state.backgroundOperation?.cancellable)&&<button onClick={()=>{void controller.interrupt().catch(error=>controller.fail(error))}}>Stop</button>}</div>}<button className="editor-toggle" aria-label="Toggle code editor" aria-pressed={editor} onClick={() => {controller.navigate('chat');setEditor(value => state.page!=='chat'||!value)}}><Code2 size={14}/><span>Code</span></button><button className="editor-toggle" aria-label="Toggle terminal" aria-pressed={terminalShown} onClick={toggleTerminal}><Terminal size={14}/><span>Terminal</span></button><button className="icon-button" aria-label="Open settings" onClick={() => settings.current?.showModal()}><Settings2 size={15}/></button></div></header>
    {state.error && <div className="error-banner" role="alert"><span>{state.error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => controller.clearError()}><X size={14}/></button></div>}
    <main className="workspace-grid">
      <aside className="sidebar-panel" aria-label="Workspace sidebar" aria-hidden={!sidebar} inert={!sidebar}><Sidebar controller={controller} onSettings={() => settings.current?.showModal()} onCode={()=>{controller.navigate('chat');setEditor(true)}} appearance={appearance}/></aside>
      <section className="harness-panel" aria-label="Agent harness" hidden={state.page!=='chat'}><Harness interruptShortcut={bindings.interrupt} controller={controller} commandsInTerminal={terminalShown&&terminalTab==='commands'}/></section>
      {state.page!=='chat'&&<FeaturePage controller={controller}/>}
      <section className="editor-panel" aria-label="Code workspace" aria-hidden={!editor} inert={!editor}>{editor&&<PaneResize side={arrangement.editorSide} width={editorWidth} onChange={resizeEditor}/>}<div className="editor-toolbar"><DocumentTabs documents={state.documents} activeId={state.activeId} dirty={state.dirty} onSelect={id=>controller.selectDocument(id)} onClose={id=>{if(state.dirty.has(id)){setClosingTab(id);closeTab.current?.showModal()}else void controller.closeDocument(id).catch(error=>controller.fail(error))}}/><div className="editor-actions"><button className="icon-button" aria-label="Find a project file" title={`Find a file · ${formatShortcut(bindings.quickOpen)}`} onClick={()=>quickOpen.current?.showModal()}><Search size={15}/></button><button className="icon-button" title="New untitled file" aria-label="New untitled file" onClick={() => controller.newDocument()}><FilePlus2 size={16}/></button><button className="icon-button" title={`Save file · ${formatShortcut(bindings.save)}`} aria-label="Save file" disabled={!!active.uri&&!state.dirty.has(active.id)} onClick={() => {if(!active.uri)saveAs.current?.showModal();else void controller.save().catch(error => controller.fail(error));}}><Save size={15}/></button><button className="icon-button" aria-label="Hide code editor" onClick={() => setEditor(false)}><X size={15}/></button></div></div>
        <div className="file-breadcrumb"><span>{active.uri ? decodeURIComponent(new URL(active.uri).pathname).replace(state.workspace?.resolvedRoot?.replaceAll("\\", "/") ?? "", "").replace(/^\//, "") : "Untitled buffer"}</span><span>{active.externalRevision?'Changed on disk · ':''}{active.languageId ?? "Python"}{active.uri&&<button disabled={reloading} onClick={()=>{if(state.dirty.has(active.id))reloadFile.current?.showModal();else void controller.reloadDocument(active.id).catch(reason=>controller.fail(reason))}}>Reload</button>}</span></div>
        {editorMounted && <Suspense fallback={<div className="code-editor" role="status">Opening editor…</div>}><CodeEditor navigation={state.editorNavigation} openIds={openIds} document={active} onReady={ready} onChange={change} onSelection={selection} onCompletion={complete} onDiagnostics={diagnostics}/></Suspense>}
        <footer className="editor-status"><span>{state.dirty.has(active.id) ? "Unsaved changes" : active.uri ? "Saved" : "Untitled"}</span><span>UTF-8 <i>·</i> {active.lineEnding ?? "LF"}</span></footer>

      </section>
      <section className="terminal-workspace" aria-label="Terminal workspace" hidden={!terminalShown}><div className="terminal-workspace-tabs" role="tablist" aria-label="Terminal views"><button role="tab" aria-selected={terminalTab==='commands'} onClick={()=>setTerminalTab('commands')}>Agent output{state.jobs.some(job=>job.status==='running')?' · running':''}</button><button role="tab" aria-selected={terminalTab==='shell'} onClick={()=>{setTerminalTab('shell');setShellMounted(true)}}>Shell</button><button aria-label="Hide terminal workspace" onClick={()=>setTerminal(false)}><X size={12}/></button></div>{terminalShown&&terminalTab==='commands'&&<TerminalDock controller={controller} onClose={()=>setTerminal(false)}/>} {shellMounted&&<Suspense fallback={<p>Loading shell…</p>}><InteractiveTerminal onSessionCount={setLiveShells} cwd={state.workspace?.resolvedRoot} visible={terminalShown&&terminalTab==='shell'} onClose={()=>setTerminal(false)}/></Suspense>}</section>
    </main>
    {state.connection !== "ready" && <footer className="app-status" role="status" aria-live="polite"><div><span className="connection-dot"/><span>{state.connection === "connecting" ? "Connecting local engine…" : "Local engine offline"}</span><button aria-busy={state.connection === "connecting"} disabled={state.connection === "connecting"} onClick={() => { void controller.connect(); }}>{state.connection === "connecting" ? "Connecting…" : "Connect"}</button></div></footer>}
    <SettingsDialog layout={layout} shortcuts={shortcuts} controller={controller} store={appearance} dialog={settings} onWallpaper={() => setWallpaperRevision(value => value + 1)} sidebar={sidebar} editor={editor} terminal={terminal} onSidebar={toggleSidebar} onEditor={()=>{controller.navigate('chat');setEditor(v=>!v)}} onTerminal={()=>setTerminal(v=>!v)}/>
    <WorkspaceDialog controller={controller} dialog={workspaceDialog} hasShells={liveShells>0}/>
    <QuickOpen controller={controller} dialog={quickOpen}/>
    <dialog ref={reloadFile} className="settings-dialog save-as-dialog" aria-label="Reload file" onCancel={event=>{if(reloading)event.preventDefault()}}><h2>Reload from disk?</h2><p>This replaces the current buffer with the file on disk. Your local edits remain available through editor undo.</p><div className="approval-actions"><button disabled={reloading} onClick={()=>reloadFile.current?.close()}>Keep local edits</button><button disabled={reloading} onClick={()=>{setReloading(true);void controller.reloadDocument(active.id,true).then(()=>reloadFile.current?.close()).catch(reason=>controller.fail(reason)).finally(()=>setReloading(false))}}>{reloading?'Reloading…':'Replace buffer with disk'}</button></div></dialog>
    <SaveAsDialog controller={controller} dialog={saveAs}/>
    <CommandPalette dialog={commandPalette} commands={availableCommands} onRun={runPaletteCommand}/>
    <dialog ref={closeTab} className="settings-dialog save-as-dialog" aria-label="Unsaved changes"><h2>Keep your changes?</h2><p>{state.documents.find(d=>d.id===closingTab)?.name} has unsaved edits.</p><div className="approval-actions"><button onClick={()=>closeTab.current?.close()}>Keep editing</button><button onClick={()=>{void controller.closeDocument(closingTab,true).then(()=>closeTab.current?.close()).catch(error=>controller.fail(error))}}>Discard and close</button></div></dialog>
    <SessionTools controller={controller} dialog={sessionTools} initialAction={sessionToolsAction} openToken={sessionToolsToken}/>
    {historyMounted&&<Suspense fallback={null}><ConversationHistory controller={controller} dialog={historyDialog} openToken={historyToken} initialQuery={historyQuery}/></Suspense>}
    {lifecycle.confirmClose && <div className="modal-overlay"><section role="alertdialog" aria-modal="true" aria-label="Close running workspace" className="settings-dialog"><h2>{state.pendingAttachments?'Keep your image attachments?':'Work is still running.'}</h2>{!!state.pendingAttachments&&<p>Unsent images are only held in this window and will be discarded if you close it.</p>}<p>Closing stops the local engine, commands and open shells. Unsaved code and your message draft will be recovered next time.</p><div className="approval-actions"><button autoFocus disabled={lifecycle.closing} onClick={lifecycle.cancelClose}>Keep working</button><button disabled={lifecycle.closing} className="primary-button" onClick={() => { void lifecycle.close(); }}>{lifecycle.closing ? "Saving recovery…" : "Close workspace"}</button></div></section></div>}
  </div></AppearanceContext>;
}
