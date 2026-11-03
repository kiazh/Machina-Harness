// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {LayoutSettings} from '../../src/app/LayoutSettings.tsx';
import {KeyboardSettings} from '../../src/app/KeyboardSettings.tsx';
import {LayoutStore} from '../../src/state/layout.ts';
import {ShortcutStore} from '../../src/state/shortcuts.ts';
afterEach(cleanup);

it('applies a layout preset and saves subsequent real pane controls',()=>{
  let raw:string|null=null;
  const storage={getItem:()=>raw,setItem:(_key:string,value:string)=>{raw=value}};
  const store=new LayoutStore(storage);
  render(<LayoutSettings store={store}/>);
  fireEvent.click(screen.getByRole('button',{name:/Side by side/}));
  expect(store.snapshot().editor).toBe(true);
  fireEvent.change(screen.getByLabelText('Code editor position'),{target:{value:'left'}});
  fireEvent.change(screen.getByLabelText('Sidebar width'),{target:{value:'280'}});
  expect(new LayoutStore(storage).snapshot()).toMatchObject({preset:'custom',editorSide:'left',sidebarWidth:280});
  fireEvent.click(screen.getByRole('checkbox',{name:'Show code editor'}));
  expect(store.snapshot().editor).toBe(false);
});

it('captures a keyboard shortcut, explains conflicts, and restores its default',()=>{
  const store=new ShortcutStore();
  render(<KeyboardSettings store={store}/>);
  fireEvent.change(screen.getByRole('searchbox',{name:'Search shortcuts'}),{target:{value:'sidebar'}});
  fireEvent.click(screen.getByRole('button',{name:'Change Toggle sidebar shortcut'}));
  fireEvent.keyDown(screen.getByLabelText('Shortcut for Toggle sidebar'),{key:'p',ctrlKey:true});
  expect(store.snapshot().toggleSidebar).toBe('Mod+P');
  expect(screen.getByRole('status').textContent).toContain('Find a project file');
  fireEvent.click(screen.getByRole('button',{name:'Reset Toggle sidebar shortcut'}));
  expect(store.snapshot().toggleSidebar).toBe('Mod+B');
});
