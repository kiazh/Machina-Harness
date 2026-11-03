import {expect, it} from 'vitest';
import {LayoutStore, normalizeLayout} from '../../src/state/layout.ts';

it('restores safe layout bounds instead of hiding the agent or injecting CSS', () => {
  expect(normalizeLayout({preset:'bad',sidebar:'false',editorWidth:900,sidebarWidth:-10,editorSide:'url(evil)',terminalHeight:Infinity,density:'tiny'})).toMatchObject({preset:'custom',sidebar:true,editorWidth:72,sidebarWidth:168,editorSide:'right',terminalHeight:200,density:'comfortable'});
});

it('persists a selected workspace then records manual pane changes as custom', () => {
  const values = new Map<string,string>();
  const storage = {getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value)}};
  const store = new LayoutStore(storage);
  store.select('terminal-desk');
  expect(store.snapshot()).toMatchObject({editor:true,terminal:true,sidebar:false,terminalHeight:320});
  store.update({editorSide:'left',sidebarWidth:280});
  expect(new LayoutStore(storage).snapshot()).toMatchObject({preset:'custom',editorSide:'left',sidebarWidth:280,terminalHeight:320});
  store.reset();
  expect(store.snapshot()).toMatchObject({preset:'agent-first',sidebar:true,editor:false,terminal:true});
});

it('migrates the existing editor width and survives unavailable persistence', () => {
  const store = new LayoutStore({getItem:key=>key==='machina.desktop.editorWidth'?'64':null,setItem(){throw new Error('Full')}});
  expect(store.snapshot().editorWidth).toBe(64);
  store.update({editor:true});
  expect(store.snapshot().editor).toBe(true);
});
