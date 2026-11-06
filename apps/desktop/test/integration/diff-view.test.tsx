// @vitest-environment jsdom
import {act,cleanup,fireEvent,isInaccessible,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {DiffView} from '../../src/features/changes/DiffView.tsx';

const patch='@@ -3,2 +3,2 @@\n same\n-old\n+new\n';
const originalClipboard=Object.getOwnPropertyDescriptor(navigator,'clipboard');
beforeEach(()=>{
 // Give the real virtualizer a viewport. jsdom does not measure layout.
 vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(400);
 vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(700);
});
afterEach(()=>{
 cleanup();vi.restoreAllMocks();
 if(originalClipboard)Object.defineProperty(navigator,'clipboard',originalClipboard);else Reflect.deleteProperty(navigator,'clipboard');
});
function clipboard(writeText:(text:string)=>Promise<void>){Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}})}

it('exposes added and removed source lines to assistive technology in line view',()=>{
 render(<DiffView path="src/main.py" patch={patch}/>);
 expect(isInaccessible(screen.getByText('Removed, old line 4.'))).toBe(false);
 expect(isInaccessible(screen.getByText('Added, new line 4.'))).toBe(false);
 expect(isInaccessible(screen.getByText('Context, old line 3, new line 3.'))).toBe(false);
});

it.each([
 {name:'path',path:'src/other.py',patch},
 {name:'patch',path:'src/main.py',patch:'@@ -1 +1 @@\n-before\n+after\n'},
])('ignores old clipboard success after the $name changes',async next=>{
 let finish!:()=>void;
 clipboard(()=>new Promise(resolve=>{finish=resolve}));
 const view=render(<DiffView path="src/main.py" patch={patch}/>);
 fireEvent.click(screen.getByRole('button',{name:'Copy diff'}));
 view.rerender(<DiffView path={next.path} patch={next.patch}/>);
 await act(async()=>finish());
 expect(screen.queryByRole('status')).toBeNull();
 expect(screen.getByRole('button',{name:'Plain text'}).getAttribute('aria-pressed')).toBe('false');
});

it.each([
 {name:'path',path:'src/other.py',patch},
 {name:'patch',path:'src/main.py',patch:'@@ -1 +1 @@\n-before\n+after\n'},
])('ignores old clipboard failure after the $name changes',async next=>{
 let fail!:(reason:Error)=>void;
 clipboard(()=>new Promise((_resolve,reject)=>{fail=reject}));
 const view=render(<DiffView path="src/main.py" patch={patch}/>);
 fireEvent.click(screen.getByRole('button',{name:'Copy diff'}));
 view.rerender(<DiffView path={next.path} patch={next.patch}/>);
 await act(async()=>fail(new Error('Permission denied')));
 expect(screen.queryByRole('status')).toBeNull();
 expect(screen.getByRole('region',{name:`Diff for ${next.path}`})).toBeTruthy();
});

it('copies the inspected patch and reports success for the current selection',async()=>{
 let copied='';clipboard(async text=>{copied=text});
 render(<DiffView path="src/main.py" patch={patch}/>);
 fireEvent.click(screen.getByRole('button',{name:'Copy diff'}));
 await act(async()=>{});
 expect(copied).toBe('@@ -3,2 +3,2 @@\n same\n-old\n+new\n');
 expect(screen.getByRole('status').textContent).toBe('Diff copied.');
});

it('opens selectable plain text when copying the current selection fails',async()=>{
 clipboard(async()=>{throw new Error('Permission denied')});
 render(<DiffView path="src/main.py" patch={patch}/>);
 fireEvent.click(screen.getByRole('button',{name:'Copy diff'}));
 await act(async()=>{});
 expect(screen.getByLabelText('Plain diff for src/main.py').textContent).toBe(patch);
 expect(screen.getByRole('status').textContent).toContain('Clipboard unavailable');
});
