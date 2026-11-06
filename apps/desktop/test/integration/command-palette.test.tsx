// @vitest-environment jsdom
import {createRef} from 'react';
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {CommandPalette,type PaletteCommand} from '../../src/features/commands/CommandPalette.tsx';

// jsdom has no native modal implementation. Keep focus behavior, but deliberately
// delay the close event as browsers do, to exercise safe dialog handoff.
const originalShowModal=Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype,'showModal');
const originalClose=Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype,'close');
beforeEach(()=>{
 Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(this:HTMLDialogElement){
  this.setAttribute('open','');
  (this.querySelector('[autofocus]') as HTMLElement|null)?.focus();
 }});
 Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(this:HTMLDialogElement){
  this.removeAttribute('open');
  setTimeout(()=>this.dispatchEvent(new Event('close')),0);
 }});
});
afterEach(()=>{
 cleanup();vi.restoreAllMocks();
 if(originalShowModal)Object.defineProperty(HTMLDialogElement.prototype,'showModal',originalShowModal);else Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');
 if(originalClose)Object.defineProperty(HTMLDialogElement.prototype,'close',originalClose);else Reflect.deleteProperty(HTMLDialogElement.prototype,'close');
});

const commands:PaletteCommand[]=[
 {id:'new',label:'New conversation',group:'Conversation',shortcut:'Ctrl+N'},
 {id:'export',label:'Export conversation',group:'Conversation',disabledReason:'Open a conversation first.'},
 {id:'changes',label:'Workspace changes',group:'Workspace',description:'Review staged and working tree changes',searchTerms:['git','diff']},
 {id:'settings',label:'Open settings',group:'Workspace',shortcut:'Ctrl+,'},
];
async function fixture(entries=commands,onRun=vi.fn()){
 const dialog=createRef<HTMLDialogElement>();
 render(<><button>Launch commands</button><CommandPalette dialog={dialog} commands={entries} onRun={onRun}/></>);
 const launcher=screen.getByRole('button',{name:'Launch commands'});
 launcher.focus();
 await act(async()=>dialog.current!.showModal());
 const input=screen.getByRole('combobox',{name:'Search commands'}) as HTMLInputElement;
 return {dialog,input,launcher,onRun};
}
function selected(){return screen.getAllByRole('option').find(option=>option.getAttribute('aria-selected')==='true')}

it('searches aliases and descriptions without submitting unknown input',async()=>{
 const {input,onRun}=await fixture();
 fireEvent.change(input,{target:{value:'git'}});
 expect(screen.getAllByRole('option').map(row=>row.textContent)).toEqual(['Workspace changesReview staged and working tree changes']);
 fireEvent.change(input,{target:{value:'working tree'}});
 expect(selected()?.textContent).toContain('Workspace changes');
 fireEvent.change(input,{target:{value:'please write some code'}});
 expect(screen.queryByRole('option')).toBeNull();
 expect(screen.getByRole('status').textContent).toContain('No commands');
 fireEvent.keyDown(input,{key:'Enter'});
 expect(onRun).not.toHaveBeenCalled();
});

it('keeps focus in search while arrows and Home/End change the active descendant',async()=>{
 const {input}=await fixture();
 expect(document.activeElement).toBe(input);
 expect(selected()?.textContent).toContain('New conversation');
 fireEvent.keyDown(input,{key:'ArrowUp'});
 expect(selected()?.textContent).toContain('Open settings');
 fireEvent.keyDown(input,{key:'Home'});
 expect(selected()?.textContent).toContain('New conversation');
 fireEvent.keyDown(input,{key:'End'});
 expect(selected()?.textContent).toContain('Open settings');
 fireEvent.keyDown(input,{key:'ArrowDown'});
 expect(selected()?.textContent).toContain('New conversation');
 expect(input.getAttribute('aria-activedescendant')).toBe(selected()?.id);
 expect(document.activeElement).toBe(input);
 fireEvent.change(input,{target:{value:'settings'}});
 expect(selected()?.textContent).toContain('Open settings');
 fireEvent.change(input,{target:{value:''}});
 expect(selected()?.textContent).toContain('New conversation');
});

it('lets keyboard users inspect unavailable reasons but never runs those commands',async()=>{
 const {input,onRun,dialog}=await fixture();
 fireEvent.keyDown(input,{key:'ArrowDown'});
 const unavailable=selected()!;
 expect(unavailable.getAttribute('aria-disabled')).toBe('true');
 expect(unavailable.textContent).toContain('Open a conversation first.');
 fireEvent.keyDown(input,{key:'Enter'});
 fireEvent.click(unavailable);
 await act(async()=>{});
 expect(onRun).not.toHaveBeenCalled();
 expect(dialog.current!.open).toBe(true);
});

it('closes and restores focus before running an action that opens another dialog',async()=>{
 const handoff=document.createElement('dialog');
 const nextInput=document.createElement('input');
 nextInput.setAttribute('autofocus','');
 handoff.append(nextInput);document.body.append(handoff);
 let wasClosed=false,hadFocus=false;
 const onRun=vi.fn(()=>{
  wasClosed=!f.dialog.current!.open;hadFocus=document.activeElement===f.launcher;
  handoff.showModal();
 });
 const f=await fixture(commands,onRun);
 fireEvent.keyDown(f.input,{key:'Enter'});
 await act(async()=>{});
 expect(onRun).toHaveBeenCalledExactlyOnceWith('new');
 expect(wasClosed).toBe(true);expect(hadFocus).toBe(true);
 await act(async()=>new Promise(resolve=>setTimeout(resolve,0)));
 expect(document.activeElement).toBe(nextInput);
 handoff.remove();
});

it('clears search on Escape, then closes without sending Escape to the workspace',async()=>{
 const {input,dialog,launcher}=await fixture();
 const escaped=vi.fn();window.addEventListener('keydown',escaped);
 try{
  fireEvent.change(input,{target:{value:'settings'}});
  fireEvent.keyDown(input,{key:'Escape'});
  expect(input.value).toBe('');expect(dialog.current!.open).toBe(true);
  fireEvent.keyDown(input,{key:'Escape'});
  expect(dialog.current!.open).toBe(false);expect(document.activeElement).toBe(launcher);
  expect(escaped).not.toHaveBeenCalled();
 }finally{window.removeEventListener('keydown',escaped)}
});

it('preserves IME composition until text is committed',async()=>{
 const {input,dialog,onRun}=await fixture();
 fireEvent.change(input,{target:{value:'settings'}});
 fireEvent.compositionStart(input);
 fireEvent.keyDown(input,{key:'Enter',isComposing:true});
 fireEvent.keyDown(input,{key:'Escape',isComposing:true});
 fireEvent.keyDown(input,{key:'ArrowDown',isComposing:true});
 expect(input.value).toBe('settings');expect(dialog.current!.open).toBe(true);expect(onRun).not.toHaveBeenCalled();
 fireEvent.compositionEnd(input);
 fireEvent.keyDown(input,{key:'Enter'});
 await act(async()=>{});
 expect(onRun).toHaveBeenCalledExactlyOnceWith('settings');
});

it('clears query and selection when reopened and supports a clear-search button',async()=>{
 const {input,dialog}=await fixture();
 fireEvent.change(input,{target:{value:'settings'}});
 fireEvent.click(screen.getByRole('button',{name:'Clear command search'}));
 expect(input.value).toBe('');expect(document.activeElement).toBe(input);
 fireEvent.keyDown(input,{key:'End'});
 fireEvent.change(input,{target:{value:'changes'}});
 await act(async()=>dialog.current!.close());
 await act(async()=>dialog.current!.showModal());
 expect(input.value).toBe('');expect(selected()?.textContent).toContain('New conversation');
});

it('bounds a blank search and explains how to reach remaining commands',async()=>{
 await fixture(Array.from({length:35},(_,index)=>({id:String(index),label:`Action ${index}`,group:'Workspace'})));
 expect(screen.getAllByRole('option')).toHaveLength(20);
 expect(screen.getByRole('status').textContent).toContain('Search to narrow');
});

it('ranks exact labels ahead of prefix and alias matches, retaining source order on ties',async()=>{
 const {input}=await fixture([
  {id:'alias',label:'Review files',group:'Workspace',searchTerms:['changes']},
  {id:'prefix',label:'Changes settings',group:'Workspace'},
  {id:'exact',label:'Changes',group:'Workspace'},
  {id:'tie',label:'Changes view',group:'Workspace'},
 ]);
 fireEvent.change(input,{target:{value:'changes'}});
 expect(screen.getAllByRole('option').map(row=>row.textContent)).toEqual(['Changes','Changes settings','Changes view','Review files']);
});
