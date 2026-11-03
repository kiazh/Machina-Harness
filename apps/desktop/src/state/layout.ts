export type LayoutPreset = 'agent-first' | 'split-code' | 'code-focus' | 'terminal-desk';
export interface Layout {
  preset: LayoutPreset | 'custom';
  sidebar: boolean;
  editor: boolean;
  terminal: boolean;
  sidebarWidth: number;
  editorWidth: number;
  editorSide: 'left' | 'right';
  terminalHeight: number;
  density: 'comfortable' | 'compact';
}
export const LAYOUT_PRESETS: Record<LayoutPreset, {name:string; description:string; values:Omit<Layout,'preset'>}> = {
  'agent-first': {name:'Agent first',description:'Conversation with room to think. Open code when needed.',values:{sidebar:true,editor:false,terminal:true,sidebarWidth:224,editorWidth:51,editorSide:'right',terminalHeight:200,density:'comfortable'}},
  'split-code': {name:'Side by side',description:'Keep the conversation and code in view together.',values:{sidebar:true,editor:true,terminal:true,sidebarWidth:224,editorWidth:48,editorSide:'right',terminalHeight:200,density:'comfortable'}},
  'code-focus': {name:'Code focus',description:'A wide editor beside a compact conversation.',values:{sidebar:false,editor:true,terminal:false,sidebarWidth:224,editorWidth:68,editorSide:'right',terminalHeight:200,density:'compact'}},
  'terminal-desk': {name:'Terminal desk',description:'Code and command output with more vertical room.',values:{sidebar:false,editor:true,terminal:true,sidebarWidth:224,editorWidth:60,editorSide:'right',terminalHeight:320,density:'compact'}},
};
type Storage = Pick<globalThis.Storage,'getItem'|'setItem'>;
const key='machina.desktop.layout.v1';
const clamp=(value:unknown,fallback:number,min:number,max:number)=>typeof value==='number'&&Number.isFinite(value)?Math.max(min,Math.min(max,value)):fallback;
export function normalizeLayout(value:unknown):Layout {
  const raw=(value&&typeof value==='object'?value:{}) as Partial<Layout>;
  const preset=raw.preset&&Object.hasOwn(LAYOUT_PRESETS,raw.preset)?raw.preset as LayoutPreset:raw.preset?'custom':'agent-first';
  const defaults=LAYOUT_PRESETS[preset==='custom'?'agent-first':preset].values;
  return {preset,sidebar:typeof raw.sidebar==='boolean'?raw.sidebar:defaults.sidebar,editor:typeof raw.editor==='boolean'?raw.editor:defaults.editor,terminal:typeof raw.terminal==='boolean'?raw.terminal:defaults.terminal,sidebarWidth:clamp(raw.sidebarWidth,defaults.sidebarWidth,168,360),editorWidth:clamp(raw.editorWidth,defaults.editorWidth,25,72),editorSide:raw.editorSide==='left'?'left':'right',terminalHeight:clamp(raw.terminalHeight,defaults.terminalHeight,120,480),density:raw.density==='compact'?'compact':'comfortable'};
}
export class LayoutStore {
  private value:Layout;
  private listeners=new Set<()=>void>();
  constructor(private storage?:Storage){
    let stored:unknown;
    try {stored=JSON.parse(storage?.getItem(key)??'null');}catch{}
    if(!stored){try{const width=Number(storage?.getItem('machina.desktop.editorWidth'));if(width>=25&&width<=72)stored={editorWidth:width,preset:'custom'};}catch{}}
    this.value=normalizeLayout(stored);
  }
  snapshot=():Layout=>this.value;
  subscribe=(listener:()=>void):(()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener)}};
  update(patch:Partial<Layout>):void {
    const next=normalizeLayout({...this.value,...patch,preset:patch.preset??'custom'});
    if(Object.keys(next).every(field=>next[field as keyof Layout]===this.value[field as keyof Layout]))return;
    this.value=next;
    try{this.storage?.setItem(key,JSON.stringify(next));}catch{}
    for(const listener of this.listeners)listener();
  }
  select(preset:LayoutPreset):void{this.update({...LAYOUT_PRESETS[preset].values,preset})}
  reset():void{this.select('agent-first')}
}
