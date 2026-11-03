import { useState, useSyncExternalStore } from "react";
import { Activity, ArrowUpRight, Files, MessageSquare, Plus, Search, Settings2, Sparkles, Code2, MoreHorizontal } from "lucide-react";
import type { DesktopController } from "../state/controller.ts";
import { Explorer } from "../features/explorer/Explorer.tsx";
import { AppearanceStore, PRESETS, type Preset } from "../state/appearance.ts";

export function Sidebar({ controller, onSettings, onCode, appearance }: { controller: DesktopController; onSettings: () => void; onCode:()=>void; appearance: AppearanceStore }) {
  const state = useSyncExternalStore(controller.subscribe, controller.snapshot);
  const [section, setSection] = useState<"sessions" | "files" | "activity">("sessions");
  const [query, setQuery] = useState("");
  const [more,setMore]=useState(false);
  return <>
    <div className="sidebar-top"><button className="new-session-button" disabled={state.running||!!state.backgroundOperation} onClick={() => { controller.newSession(); setSection("sessions"); }}><Plus size={16}/><span>New session</span></button></div>
    <nav className="sidebar-nav" aria-label="Workspace navigation">
      <button aria-current={section === "sessions"&&state.page==='chat' ? "page" : undefined} onClick={() => {controller.navigate('chat');setSection("sessions")}}><MessageSquare size={16}/><span>Conversations</span></button>
      <button aria-current={state.page==='skills'?'page':undefined} onClick={()=>controller.navigate('skills')}><Sparkles size={16}/><span>Capabilities</span></button>
      <button onClick={onCode}><Code2 size={16}/><span>Code workspace</span></button>
      <button aria-current={section === "files"&&state.page==='chat' ? "page" : undefined} onClick={() => {controller.navigate('chat');setSection("files")}}><Files size={16}/><span>Files</span></button>
      <button aria-current={section === "activity"&&state.page==='chat' ? "page" : undefined} onClick={() => {controller.navigate('chat');setSection("activity")}}><Activity size={16}/><span>Activity</span>{state.running && <span className="working-dot"/>}</button>
      <button aria-expanded={more} onClick={()=>setMore(v=>!v)}><MoreHorizontal size={16}/><span>More</span></button>
      {more&&<div className="more-features">{(['memory','context','jobs','agents','usage','status','help'] as const).map(page=><button key={page} onClick={()=>controller.navigate(page)}>{({models:'Models',memory:'Memory',context:'Context',jobs:'Scheduled jobs',agents:'Agents',mcp:'Connectors',providers:'Providers',keys:'API keys',auth:'Accounts',usage:'Usage',status:'Status',help:'Commands'})[page]}</button>)}</div>}
    </nav>
    <div className="sidebar-body" hidden={section !== "sessions"}>
      <div className="sidebar-section-heading">RECENT CONVERSATIONS</div>
      {!!state.sessions.length && <label className="file-filter"><Search size={13}/><input aria-label="Search conversations" placeholder="Search…" value={query} onChange={event => setQuery(event.target.value)}/></label>}
      <button className="sidebar-browse" onClick={()=>controller.navigate("sessions")}>Search all conversations</button><div className="session-list">{state.sessions.filter(session => session.title.toLowerCase().includes(query.toLowerCase())).map(session => <button key={session.id} className={state.session?.id === session.id ? "selected" : ""} disabled={state.running||!!state.backgroundOperation} title={session.title} onClick={() => { void controller.resume(session.id).catch(error => controller.fail(error)); }}><span>{session.title || "Untitled conversation"}</span></button>)}</div>
      {!!state.sessions.length&&!state.sessions.some(session=>session.title.toLowerCase().includes(query.toLowerCase()))&&<p className="sidebar-empty">No conversations match this search.</p>}
      {!state.sessions.length && <p className="sidebar-empty">Your conversations will appear here.</p>}
    </div>
    <div className="sidebar-body files-body" hidden={section !== "files"}><Explorer key={`${state.workspace?.workspaceId}:${state.workspace?.epoch}`} controller={controller} ready={state.connection === "ready"}/></div>
    <div className="sidebar-body" hidden={section !== "activity"}><div className="sidebar-section-heading">LIVE WORK</div>
      {!state.running && !state.backgroundOperation && !state.jobs.some(job => job.status === "running") && !state.agentIds.length && <p className="sidebar-empty">Nothing running. Agent work and commands appear here as they start.</p>}
      {state.backgroundOperation&&<div className="sidebar-activity"><span className="working-dot"/><button onClick={()=>controller.navigate(state.backgroundOperation?.method==='agents.run'?'agents':'interop')}>Background run</button></div>}{state.running && <div className="sidebar-activity"><span className="working-dot"/><span>Main agent working</span></div>}
      {state.agentIds.map(id => <button key={id} className="sidebar-activity" onClick={()=>controller.inspectAgent(id)}><span className="working-dot"/><span>Inspect worker</span></button>)}
      {state.jobs.map(job => <div key={job.id} className="sidebar-job"><span title={job.command}>{job.command}</span><small>{job.status}</small></div>)}
    </div>
    <div className="sidebar-bottom"><div className="sidebar-section-heading">ATMOSPHERE</div><div className="quick-presets">{(Object.keys(PRESETS) as Preset[]).map(preset => <button key={preset} className={`quick-preset quick-${preset}`} title={PRESETS[preset].name} aria-label={`Use ${PRESETS[preset].name} style`} onClick={() => appearance.select(preset)}/>)}<button className="personalize-button" onClick={onSettings}>Personalize <ArrowUpRight size={12}/></button></div><button onClick={onSettings}><Settings2 size={15}/><span>Settings</span></button><div><span className={`connection-dot ${state.connection === "ready" ? "connected" : ""}`}/><span>{state.workspace?.rootLabel ?? "Local workspace"}</span></div></div>
  </>;
}
