import {useEffect,useRef,useState,type RefObject} from 'react';
import {X,Search} from 'lucide-react';
import type {DesktopController} from '../state/controller.ts';
import type {AppearanceStore} from '../state/appearance.ts';
import {AppearanceDialog} from './AppearanceDialog.tsx';
import {FeaturePage} from '../features/catalog/LazyFeaturePage.tsx';
import type {LayoutStore} from '../state/layout.ts';
import type {ShortcutStore} from '../state/shortcuts.ts';
import {LayoutSettings} from './LayoutSettings.tsx';
import {KeyboardSettings} from './KeyboardSettings.tsx';
const sections=[
 ['workspace','Workspace','Workspace','layout panes editor terminal sidebar'],
 ['shortcuts','Keyboard shortcuts','Workspace','keys keybind bind shortcut keyboard'],
 ['lsp','Language servers','Workspace','completion diagnostics lsp language intelligence'],
 ['appearance','Appearance','Personalization','theme colors accent motion'],
 ['background','Wallpaper & surfaces','Personalization','background opacity glass blur image'],
 ['typography','Typography','Personalization','font text size code interface'],
 ['music','Now playing','Personalization','music playback media'],
 ['models','Models','Agent','model reasoning catalog context'],
 ['preferences','Engine preferences','Agent','config all settings parameters'],
 ['profiles','Profiles','Agent','user preferences persona'],
 ['growth','Personality & learning','Agent','persona teach growth memory policy'],
 ['mode','Permissions','Agent','execution safety approvals sandbox workspace'],
 ['reviews','Memory approvals','Agent','pending approve reject review memory'],
 ['providers','Providers','Connections','custom url endpoint'],
 ['keys','API keys','Connections','credentials secrets tokens'],
 ['auth','Accounts','Connections','chatgpt copilot grok subscription login sign in codex'],
 ['mcp','Connectors','Connections','mcp tools servers'],
 ['interop','Harness integrations','Connections','codex hermes opencode external']
] as const;
type Section=typeof sections[number][0];
export function SettingsDialog({controller,store,dialog,onWallpaper,sidebar,editor,terminal,onSidebar,onEditor,onTerminal,layout,shortcuts}:{controller:DesktopController;store:AppearanceStore;dialog:RefObject<HTMLDialogElement|null>;onWallpaper:()=>void;sidebar:boolean;editor:boolean;terminal:boolean;onSidebar:()=>void;onEditor:()=>void;onTerminal:()=>void;layout?:LayoutStore;shortcuts?:ShortcutStore}){
 useEffect(()=>{let previous=controller.snapshot().page;return controller.subscribe(()=>{const next=controller.snapshot().page;if(next!==previous&&next!=='settings'&&!(previous==='settings'&&next==='chat')&&dialog.current?.open)dialog.current.close();previous=next})},[controller,dialog]);
 const [section,setSection]=useState<Section>('appearance');const [query,setQuery]=useState('');const [preferenceQuery,setPreferenceQuery]=useState('');const content=useRef<HTMLDivElement>(null);
 const visible=sections.filter(([,label,,keywords])=>(label+' '+keywords).toLowerCase().includes(query.toLowerCase().trim()));const current=sections.find(([id])=>id===section)!;
 function select(id:Section){if(id==='preferences')setPreferenceQuery(query.trim());setSection(id);setQuery('');if(content.current)content.current.scrollTop=0;}
 return <dialog ref={dialog} className="settings-dialog appearance-dialog unified-settings" aria-label="Settings"><aside className="settings-navigation"><h2>Settings</h2><label className="settings-search"><Search size={13}/><input aria-label="Search settings" placeholder="Search settings" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="Clear settings search" onClick={()=>setQuery('')}><X size={12}/></button>}</label><nav aria-label="Settings sections">{visible.length?visible.map(([id,label,group],index)=><div key={id}>{(index===0||visible[index-1]![2]!==group)&&<span className="settings-group">{group}</span>}<button aria-current={section===id?'page':undefined} onClick={()=>select(id)}>{label}</button></div>):<p className="settings-no-results">No settings section matches “{query}”.<button onClick={()=>select('preferences')}>Search engine preferences</button><button onClick={()=>setQuery('')}>Show all settings</button></p>}</nav><p>Deus Ex Machina<br/><span>Your local workspace.</span></p></aside><div className="settings-body"><header className="settings-page-header"><span>Settings <span aria-hidden="true">/</span> <strong>{current[1]}</strong></span><button className="icon-button" aria-label="Close settings" onClick={()=>dialog.current?.close()}><X size={16}/></button></header><div className="settings-main" ref={content}>{section==='appearance'||section==='background'||section==='typography'?<AppearanceDialog embedded view={section} store={store} dialog={dialog} onWallpaper={onWallpaper}/>:section==='shortcuts'?(shortcuts?<KeyboardSettings store={shortcuts}/>:<p>Keyboard settings are unavailable in this client.</p>):section==='workspace'&&layout?<LayoutSettings store={layout}/>:section==='workspace'?<div className="workspace-settings"><h1>Workspace</h1><p>Choose what stays in view. Your files and drafts remain open.</p>{[['Show sidebar',sidebar,onSidebar],['Show code editor',editor,onEditor],['Show terminal',terminal,onTerminal]].map(([label,checked,toggle])=><label key={label as string}><span>{label as string}</span><input type="checkbox" checked={checked as boolean} onChange={toggle as ()=>void}/></label>)}<h2>While the agent works</h2><p>Enter queues steering for the next tool boundary. Escape interrupts the turn.</p><p>Commands can continue in the background. Output and stop controls stay with the command.</p><h2>Your files</h2><p>Local buffers and undo history survive pane changes. Unsaved edits and drafts are recovered after a restart.</p></div>:<FeaturePage initialQuery={section==='preferences'?preferenceQuery:undefined} key={`${section}:${preferenceQuery}`} controller={controller} pageOverride={section} onDone={()=>dialog.current?.close()}/>}</div></div></dialog>;
}
