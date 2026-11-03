import {expect, it} from 'vitest';
import {ShortcutStore, captureShortcut, matchesShortcut} from '../../src/state/shortcuts.ts';

const event = (key:string,options:Partial<KeyboardEvent>={}) => ({key,ctrlKey:false,metaKey:false,altKey:false,shiftKey:false,isComposing:false,repeat:false,...options}) as KeyboardEvent;

it('matches primary modifiers on either desktop platform and rejects extra modifiers', () => {
  expect(matchesShortcut(event('p',{ctrlKey:true}),'Mod+P')).toBe(true);
  expect(matchesShortcut(event('p',{metaKey:true}),'Mod+P')).toBe(true);
  expect(matchesShortcut(event('p',{ctrlKey:true,shiftKey:true}),'Mod+P')).toBe(false);
  expect(matchesShortcut(event('p',{ctrlKey:true,isComposing:true}),'Mod+P')).toBe(false);
  expect(matchesShortcut(event('Escape'),'Escape')).toBe(true);
});

it('captures desktop chords without committing incomplete modifiers', () => {
  expect(captureShortcut(event('Shift',{shiftKey:true}))).toBe(null);
  expect(captureShortcut(event('B',{ctrlKey:true,shiftKey:true}))).toBe('Mod+Shift+B');
  expect(captureShortcut(event('`',{ctrlKey:true}))).toBe('Mod+`');
});

it('keeps deliberate overlaps, restores changes and resets one command independently', () => {
  let raw:string|null=null;
  const storage={getItem:()=>raw,setItem:(_key:string,value:string)=>{raw=value}};
  const store=new ShortcutStore(storage);
  store.set('toggleSidebar','Mod+P');
  store.set('newSession',null);
  expect(store.conflicts('toggleSidebar')).toEqual(['quickOpen']);
  const restored=new ShortcutStore(storage);
  expect(restored.snapshot()).toMatchObject({toggleSidebar:'Mod+P',newSession:null});
  restored.reset('toggleSidebar');
  expect(restored.snapshot().toggleSidebar).toBe('Mod+B');
  expect(restored.snapshot().newSession).toBe(null);
});

it('discards malformed stored chords while retaining unassigned commands', () => {
  const store=new ShortcutStore({getItem:()=>JSON.stringify({save:'javascript:alert(1)',quickOpen:null,openSettings:'Mod+Shift+,'}),setItem(){}});
  expect(store.snapshot()).toMatchObject({save:'Mod+S',quickOpen:null,openSettings:'Mod+Shift+,'});
});
it('warns when an explicit platform modifier overlaps a portable default',()=>{const store=new ShortcutStore();store.set('save','Ctrl+P');expect(store.conflicts('save')).toContain('quickOpen');store.set('save','Meta+P');expect(store.conflicts('save')).toContain('quickOpen');store.set('save','Ctrl+Shift+P');expect(store.conflicts('save')).not.toContain('quickOpen')});
it('captures punctuation, plus and international keys without losing them on restore',()=>{
 let raw:string|null=null;const storage={getItem:()=>raw,setItem:(_key:string,value:string)=>{raw=value}};
 for(const key of ['?','+','ß']){const chord=captureShortcut(event(key,{ctrlKey:true,shiftKey:key==='?'}));expect(chord).not.toBeNull();const store=new ShortcutStore(storage);store.set('quickOpen',chord!);const restored=new ShortcutStore(storage);expect(matchesShortcut(event(key,{ctrlKey:true,shiftKey:key==='?'}),restored.snapshot().quickOpen)).toBe(true)}
});
it('preserves combined Control and Command modifiers instead of treating them as an ordinary primary shortcut',()=>{
 const combined=event('p',{ctrlKey:true,metaKey:true});expect(matchesShortcut(combined,'Mod+P')).toBe(false);expect(captureShortcut(combined)).toBe('Ctrl+Meta+P');expect(matchesShortcut(combined,'Ctrl+Meta+P')).toBe(true);
});
