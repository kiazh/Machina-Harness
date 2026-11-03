export const SHORTCUT_COMMANDS = [
  {id:'quickOpen',name:'Find a project file',group:'Files',shortcut:'Mod+P'},
  {id:'save',name:'Save file',group:'Files',shortcut:'Mod+S'},
  {id:'saveAs',name:'Save file as…',group:'Files',shortcut:'Mod+Shift+S'},
  {id:'newDocument',name:'New untitled file',group:'Files',shortcut:'Mod+N'},
  {id:'attachSelection',name:'Attach selected code',group:'Files',shortcut:'Mod+Shift+A'},
  {id:'requestCompletion',name:'Request code completion',group:'Files',shortcut:'Mod+Shift+Space'},
  {id:'newSession',name:'New conversation',group:'Workspace',shortcut:'Mod+Shift+N'},
  {id:'openSettings',name:'Open settings',group:'Workspace',shortcut:'Mod+,'},
  {id:'toggleSidebar',name:'Toggle sidebar',group:'Workspace',shortcut:'Mod+B'},
  {id:'toggleEditor',name:'Toggle code editor',group:'Workspace',shortcut:'Mod+Shift+E'},
  {id:'toggleTerminal',name:'Toggle terminal',group:'Workspace',shortcut:'Mod+`'},
  {id:'closeAgentTabs',name:'Close worker inspection tabs',group:'Agent',shortcut:'Mod+Shift+D'},
  {id:'interrupt',name:'Interrupt agent',group:'Agent',shortcut:'Escape'},
] as const;
export type ShortcutAction=typeof SHORTCUT_COMMANDS[number]['id'];
export type Shortcuts=Record<ShortcutAction,string|null>;
type Storage=Pick<globalThis.Storage,'getItem'|'setItem'>;
const storageKey='machina.desktop.shortcuts.v1';
const modifiers=['Mod','Ctrl','Meta','Alt','Shift'];
const namedKeys=['Escape','Enter','Tab','Space','Plus','Backspace','Delete','Home','End','PageUp','PageDown','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'];
function normalizeChord(value:unknown):string|null|undefined {
  if(value===null)return null;
  if(typeof value!=='string')return undefined;
  const parts=value.split('+');
  const key=parts.pop();
  if(!key||!((Array.from(key).length===1&&!/[\s\x00-\x1f\x7f+]/u.test(key))||namedKeys.includes(key)||/^F(?:[1-9]|1\d|2[0-4])$/.test(key)))return undefined;
  if(parts.some(part=>!modifiers.includes(part))||new Set(parts).size!==parts.length)return undefined;
  if(parts.includes('Mod')&&(parts.includes('Ctrl')||parts.includes('Meta')))return undefined;
  return [...modifiers.filter(modifier=>parts.includes(modifier)),/^[a-z]$/i.test(key)?key.toUpperCase():key].join('+');
}
export function captureShortcut(event:Pick<KeyboardEvent,'key'|'ctrlKey'|'metaKey'|'altKey'|'shiftKey'|'isComposing'>):string|null {
  if(event.isComposing||['Control','Meta','Alt','Shift','AltGraph','Dead','Unidentified'].includes(event.key))return null;
  const key=event.key===' '?'Space':event.key==='+'?'Plus':event.key;
  const primary=event.ctrlKey&&event.metaKey?['Ctrl','Meta']:event.ctrlKey||event.metaKey?['Mod']:[];
  const chord=[...primary,...(event.altKey?['Alt']:[]),...(event.shiftKey?['Shift']:[]),key].join('+');
  return normalizeChord(chord)??null;
}
export function matchesShortcut(event:Pick<KeyboardEvent,'key'|'ctrlKey'|'metaKey'|'altKey'|'shiftKey'|'isComposing'>,chord:string|null):boolean {
  if(!chord||event.isComposing)return false;
  const parts=chord.split('+');const key=parts.pop()!;
  const mod=parts.includes('Mod');
  if(mod?event.ctrlKey===event.metaKey:event.ctrlKey!==parts.includes('Ctrl')||event.metaKey!==parts.includes('Meta'))return false;
  if(event.altKey!==parts.includes('Alt')||event.shiftKey!==parts.includes('Shift'))return false;
  return (event.key===' '?'Space':event.key==='+'?'Plus':event.key).toLowerCase()===key.toLowerCase();
}
export function formatShortcut(chord:string|null):string{return chord?chord.replaceAll('Mod','Ctrl / ⌘').replaceAll('Meta','⌘').replaceAll('+',' + '):'Unassigned'}
export class ShortcutStore {
  private value:Shortcuts;
  private listeners=new Set<()=>void>();
  constructor(private storage?:Storage){
    let raw:Partial<Shortcuts>={};
    try{const value=JSON.parse(storage?.getItem(storageKey)??'null');if(value&&typeof value==='object')raw=value;}catch{}
    this.value=Object.fromEntries(SHORTCUT_COMMANDS.map(command=>[command.id,normalizeChord(raw[command.id])??(raw[command.id]===null?null:command.shortcut)])) as Shortcuts;
  }
  snapshot=():Shortcuts=>this.value;
  subscribe=(listener:()=>void):(()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener)}};
  set(action:ShortcutAction,chord:string|null):void {
    const normalized=normalizeChord(chord);
    if(normalized===undefined||this.value[action]===normalized)return;
    this.value={...this.value,[action]:normalized};
    this.persist();
  }
  reset(action?:ShortcutAction):void {
    if(action){this.set(action,SHORTCUT_COMMANDS.find(command=>command.id===action)!.shortcut);return;}
    this.value=Object.fromEntries(SHORTCUT_COMMANDS.map(command=>[command.id,command.shortcut])) as Shortcuts;
    this.persist();
  }
  conflicts(action:ShortcutAction):ShortcutAction[]{const chord=this.value[action];return chord?SHORTCUT_COMMANDS.filter(command=>command.id!==action&&chordsOverlap(chord,this.value[command.id])).map(command=>command.id):[]}
  private persist(){try{this.storage?.setItem(storageKey,JSON.stringify(this.value));}catch{}for(const listener of this.listeners)listener()}
}
export function chordsOverlap(first:string|null,second:string|null):boolean{
 if(!first||!second)return false;const key=first.split('+').at(-1)!;
 return [false,true].some(ctrlKey=>[false,true].some(metaKey=>[false,true].some(altKey=>[false,true].some(shiftKey=>{const event={key:key==='Space'?' ':key,ctrlKey,metaKey,altKey,shiftKey,isComposing:false};return matchesShortcut(event,first)&&matchesShortcut(event,second)}))));
}
