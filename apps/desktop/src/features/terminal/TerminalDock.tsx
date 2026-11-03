import {useEffect,useState,useSyncExternalStore} from 'react';
import {ArrowDownToLine,Square,Terminal,X} from 'lucide-react';
import type {DesktopController,Job} from '../../state/controller.ts';

/** Agent command output only. Interactive PTY sessions are a separate porting task. */
export function TerminalDock({controller,onClose}:{controller:DesktopController;onClose:()=>void}){
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);const [selected,setSelected]=useState('');const [output,setOutput]=useState<Job>();const [error,setError]=useState('');
 const job=state.jobs.find(j=>j.id===selected)??state.jobs.at(-1);const id=job?.id;const sessionId=state.session?.id;
 useEffect(()=>{let active=true;let timer:ReturnType<typeof setTimeout>|undefined;setOutput(undefined);setError('');if(!id||!sessionId)return;async function poll(){try{const value=await controller.job(id!,'output');if(!active)return;setOutput(value);if(value.status==='running')timer=setTimeout(()=>{void poll()},1000)}catch(reason){if(active)setError(reason instanceof Error?reason.message:String(reason))}}void poll();return()=>{active=false;if(timer)clearTimeout(timer)}},[controller,id,sessionId]);
 const current=output?.id===job?.id?output:job;
 async function act(action:'background'|'stop'){if(!job)return;try{setOutput(await controller.job(job.id,action));setError('')}catch(reason){setError(String(reason))}}
 return <section className="terminal-dock" aria-label="Terminal"><header><Terminal size={13}/><strong>Terminal</strong><div className="terminal-tabs" role="tablist" aria-label="Agent commands">{state.jobs.map(j=><button role="tab" key={j.id} aria-selected={j.id===id} onClick={()=>setSelected(j.id)} title={j.command}>{j.command.slice(0,45)}</button>)}</div><button className="icon-button" aria-label="Hide terminal" onClick={onClose}><X size={13}/></button></header>{error&&<p className="inline-error" role="alert">{error}</p>}{current?<><div className="terminal-command"><code>{current.command}</code><span>{current.status}</span>{current.status==='running'&&current.foreground&&<button onClick={()=>{void act('background')}}><ArrowDownToLine size={11}/>Background</button>}{current.status==='running'&&<button onClick={()=>{void act('stop')}}><Square size={10}/>Stop</button>}</div><pre>{current.output||'No output yet.'}</pre></>:<p className="terminal-empty">Agent-run commands appear here with live output and controls.<br/><span>Output stays with its conversation.</span></p>}</section>;
}
