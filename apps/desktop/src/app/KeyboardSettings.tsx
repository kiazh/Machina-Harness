import {useState,useSyncExternalStore} from 'react';
import {RotateCcw,Search,X} from 'lucide-react';
import {SHORTCUT_COMMANDS,captureShortcut,formatShortcut,type ShortcutAction,type ShortcutStore} from '../state/shortcuts.ts';

export function KeyboardSettings({store}:{store:ShortcutStore}) {
  const shortcuts=useSyncExternalStore(store.subscribe,store.snapshot);
  const [query,setQuery]=useState('');
  const [recording,setRecording]=useState<ShortcutAction|null>(null);
  const [notice,setNotice]=useState('');
  const visible=SHORTCUT_COMMANDS.filter(command=>(command.name+' '+command.group+' '+formatShortcut(shortcuts[command.id])).toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="keyboard-settings workspace-settings">
    <div className="settings-title-row"><h1>Keyboard shortcuts</h1><button className="settings-reset" onClick={()=>{store.reset();setNotice('Default shortcuts restored.')}}><RotateCcw size={13}/>Reset all</button></div>
    <p>Choose a shortcut, then press the keys you want to use. Ctrl on Windows and Command on macOS share the same primary modifier.</p>
    <label className="shortcut-search"><Search size={14}/><input type="search" aria-label="Search shortcuts" placeholder="Search commands or keys" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="Clear shortcut search" onClick={()=>setQuery('')}><X size={13}/></button>}</label>
    <div className="shortcut-list">{visible.map((command,index)=>{
      const conflicts=store.conflicts(command.id);
      const changed=shortcuts[command.id]!==command.shortcut;
      return <div key={command.id}>
        {(index===0||visible[index-1]?.group!==command.group)&&<h2>{command.group}</h2>}
        <div className="shortcut-row"><span>{command.name}{changed&&<small>Customized</small>}</span><div className="shortcut-controls">{recording===command.id?<input className="shortcut-capture" autoFocus readOnly aria-label={`Shortcut for ${command.name}`} placeholder="Press shortcut…" onBlur={()=>setRecording(null)} onKeyDown={event=>{
          event.preventDefault();event.stopPropagation();
          const chord=captureShortcut(event.nativeEvent);
          if(!chord)return;
          store.set(command.id,chord);setRecording(null);
          const overlaps=store.conflicts(command.id).map(id=>SHORTCUT_COMMANDS.find(item=>item.id===id)!.name);
          setNotice(overlaps.length?`${command.name} shares this shortcut with ${overlaps.join(', ')}. The first available command runs.`:`${command.name}: ${formatShortcut(chord)}.`);
        }}/>:<button className="shortcut-key" aria-label={`Change ${command.name} shortcut`} onClick={()=>{setRecording(command.id);setNotice('')}}><kbd>{formatShortcut(shortcuts[command.id])}</kbd></button>}
        <button className="icon-button" aria-label={`Unassign ${command.name} shortcut`} title="Unassign shortcut" disabled={shortcuts[command.id]===null} onClick={()=>{store.set(command.id,null);setNotice(`${command.name} is unassigned.`)}}><X size={13}/></button>
        <button className="icon-button" aria-label={`Reset ${command.name} shortcut`} title="Restore default" disabled={!changed} onClick={()=>{store.reset(command.id);setNotice(`${command.name} reset to ${formatShortcut(command.shortcut)}.`)}}><RotateCcw size={13}/></button></div></div>
        {conflicts.length>0&&<p className="shortcut-conflict">Also assigned to {conflicts.map(id=>SHORTCUT_COMMANDS.find(item=>item.id===id)!.name).join(', ')}.</p>}
      </div>;
    })}</div>
    {!visible.length&&<p className="settings-inline-note">No commands match “{query}”.</p>}
    <p className="shortcut-notice" role="status" aria-live="polite">{notice}</p>
    <p className="settings-footnote">Overlapping shortcuts are allowed. Text editing and open dialogs keep their own keys. Enter sends or queues your message; Shift + Enter starts a new line.</p>
  </div>;
}
