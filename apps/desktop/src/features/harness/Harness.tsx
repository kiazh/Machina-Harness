import {Select} from '../../components/Select.tsx';
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { ArrowDownToLine, Check, ChevronDown, Circle, ExternalLink, Square, Terminal, X } from "lucide-react";
import type { DesktopController, Job } from "../../state/controller.ts";
import { Composer } from "./Composer.tsx";
import { Transcript } from "./Transcript.tsx";

function AgentText({ controller, id }: { controller: DesktopController; id: string }) {
  const subscribe = useCallback((listener: () => void) => controller.agents.subscribeMessage(id, listener), [controller, id]);
  const agent = useSyncExternalStore(subscribe, () => controller.agents.get(id));
  return <section className="agent-inspection"><h3>{agent?.title ?? "Subagent"}</h3><small>{agent?.status}</small><pre>{agent?.text || "Waiting for activity from this worker…"}</pre></section>;
}

export function Harness({ controller,commandsInTerminal=false,interruptShortcut }: { controller: DesktopController;commandsInTerminal?:boolean;interruptShortcut?:string|null }) {
  const state = useSyncExternalStore(controller.subscribe, controller.snapshot);
  const pendingAttachments=useCallback((count:number)=>controller.setPendingAttachments(count),[controller]);
  const [output, setOutput] = useState<Job>();
  const [inspected, setInspected] = useState<string>();
  const liveInspection = inspected && state.agentIds.includes(inspected) ? inspected : undefined;
  useEffect(()=>{setInspected(state.focusedAgent)},[state.agentFocus]);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [editingQueue,setEditingQueue]=useState('');const [queueDraft,setQueueDraft]=useState('');const [queueBusy,setQueueBusy]=useState(false);
  const outputId = output?.id;
  useEffect(()=>{setOutput(undefined);setInspected(undefined)},[state.session?.id,commandsInTerminal]);
  useEffect(() => {
    if (!outputId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        if (document.visibilityState !== "hidden") {
          const job = await controller.job(outputId, "output");
          if (!active) return;
          setOutput(job);
          if (job.status !== "running") return;
        }
        if (active) timer = setTimeout(poll, 750);
      } catch (error) { if (active) controller.fail(error); }
    };
    timer = setTimeout(poll, 750);
    return () => { active = false; clearTimeout(timer); };
  }, [controller, outputId]);
  const perform = (action: Promise<unknown>) => { void action.catch(error => controller.fail(error)); };
  const currentJob = state.jobs.find(job => job.status === "running" && job.foreground) ?? state.jobs.find(job => job.status === "running") ?? state.jobs.at(-1);
  const approval = state.approvals[0];
  return <>
    {state.session && <header className="harness-header"><strong>{state.session.title || "Conversation"}</strong></header>}
    <div className="harness-tabs"><button className={!liveInspection ? "selected" : ""} onClick={() => setInspected(undefined)}>Conversation</button>{state.agentIds.filter(id=>!state.hiddenAgentIds?.has(id)).map(id => <div className="worker-tab" key={id}><button className={liveInspection === id ? "selected" : ""} onClick={() => setInspected(id)}>Worker <span className="working-dot"/></button><button title="Hide this inspection; the worker keeps running" aria-label="Close worker inspection" onClick={()=>controller.dismissAgent(id)}><X size={11}/></button></div>)}</div>
    <div className="session-line"><Select aria-label="Conversation" value={state.session?.id ?? ""} disabled={state.running||!!state.backgroundOperation} onChange={event => { if (event.target.value) perform(controller.resume(event.target.value)); else controller.newSession(); }}><option value="">New conversation</option>{state.sessions.map(session => <option key={session.id} value={session.id}>{session.title}</option>)}</Select></div>
    {liveInspection ? <AgentText controller={controller} id={liveInspection}/> : <Transcript store={controller.transcript}/>}
    {approval && <section className="approval-dock" aria-label="Approval required"><div className="eyebrow">PERMISSION REQUIRED</div><h3>{approval.operation}</h3><p>{approval.target}</p>{approval.argv?.length ? <pre>{approval.argv.join(" ")}</pre> : null}<div className="approval-actions"><button disabled={approvalBusy} onClick={async () => { setApprovalBusy(true); try { await controller.approve(approval.requestId, "denied"); } catch (error) { controller.fail(error); } finally { setApprovalBusy(false); } }}>Deny</button><button className="primary-button" disabled={approvalBusy} onClick={async () => { setApprovalBusy(true); try { await controller.approve(approval.requestId, "approved"); } catch (error) { controller.fail(error); } finally { setApprovalBusy(false); } }}>Allow once</button></div></section>}
    {!commandsInTerminal&&currentJob && <div className="job-strip"><div className="job-title"><Terminal size={13}/><span title={currentJob.command}>{currentJob.command}</span><small>{currentJob.status}</small></div><div className="job-actions">
      {currentJob.status === "running" && currentJob.foreground && <button title="Continue this process in background; its original deadline remains" onClick={() => perform(controller.job(currentJob.id, "background"))}><ArrowDownToLine size={12}/> Background</button>}
      <button onClick={async () => { try { setOutput(await controller.job(currentJob.id, "output")); } catch (error) { controller.fail(error); } }}>Output</button>
      {currentJob.status === "running" && <button className="stop-button" onClick={() => perform(controller.job(currentJob.id, "stop"))}><Square size={10}/> Stop</button>}
    </div></div>}
    {output && <section className="output-view" aria-label="Command output"><div><strong>{output.status} · {output.command}</strong><button className="icon-button" aria-label="Close command output" onClick={() => setOutput(undefined)}><X size={14}/></button></div><pre>{output.output || "No output yet."}</pre><button onClick={async () => { try { setOutput(await controller.job(output.id, "output")); } catch (error) { controller.fail(error); } }}>Refresh output</button></section>}
    {state.queue.length > 0 && <section className="steering-queue" aria-label="Queued messages"><div className="eyebrow">QUEUED · {state.queue.length}</div>{state.queue.map((item, index) => <div key={item.id}><span>{index + 1}</span>{editingQueue===item.id?<form className="queue-edit" onSubmit={async event=>{event.preventDefault();setQueueBusy(true);try{await controller.replaceQueued(item.id,queueDraft);setEditingQueue('')}catch(error){controller.fail(error)}finally{setQueueBusy(false)}}}><input aria-label="Edit queued message" value={queueDraft} onChange={event=>setQueueDraft(event.target.value)} disabled={queueBusy}/><button disabled={queueBusy||!queueDraft.trim()}>Save</button><button type="button" disabled={queueBusy} onClick={()=>setEditingQueue('')}>Cancel</button></form>:<><p>{item.text}</p><button aria-label={`Edit queued message ${index+1}`} onClick={()=>{setEditingQueue(item.id);setQueueDraft(item.text)}}>Edit</button></>}<button className="icon-button" disabled={queueBusy} aria-label={`Remove queued message ${index + 1}`} onClick={() => perform(controller.removeQueued(item.id))}><X size={12}/></button></div>)}</section>}
    <div className="composer-wrap">{(state.switchingSession||state.switchingWorkspace)&&<p className="feature-description" role="status">{state.switchingWorkspace?'Opening workspace…':'Opening conversation…'} Your draft is kept.</p>}
      {state.backgroundOperation&&<div className="run-control" role="status"><span>Background run active · your draft is kept</span><button onClick={()=>controller.navigate(state.backgroundOperation?.method==='agents.run'?'agents':'interop')}>View run</button></div>}{state.running && <div className="run-control" role="status"><span><span className="working-dot"/>Working{state.queue.length ? ` · ${state.queue.length} queued` : ""}</span><button aria-label="Stop agent" onClick={() => perform(controller.interrupt())}><Square size={10}/>Stop</button></div>}
      {state.connection !== "ready" && <div className="connection-recovery"><span>{state.connection === "connecting" ? "Connecting local engine…" : "Connect your local engine to send a message."}</span><button disabled={state.connection === "connecting"} onClick={() => { void controller.connect(); }}>{state.connection === "connecting" ? "Connecting…" : "Connect"}</button></div>}
      <Composer onPendingAttachments={pendingAttachments} insertion={state.composerInsertion} onConsumeInsertion={id=>controller.consumeInsertion(id)} onAttachSelection={()=>controller.captureSelection()} interruptShortcut={interruptShortcut} running={state.running} disabled={state.connection !== "ready"||!!state.switchingSession||!!state.switchingWorkspace||!!state.backgroundOperation} initialDraft={controller.recovery?.initialDraft} onDraft={text => controller.recovery?.draft(text)} onSend={(text,images) => controller.send(text,images)} onInterrupt={() => perform(controller.interrupt())}/><div className="model-caption"><button onClick={()=>controller.navigate('models')}>{state.model || (state.connection === "ready" ? "Choose model" : "Local engine offline")} <ChevronDown size={11}/></button>{state.connection === "ready" && <span><Check size={10}/>{state.provider}</span>}</div></div>
  </>;
}
