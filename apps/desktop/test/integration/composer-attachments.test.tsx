// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {Composer} from '../../src/features/harness/Composer.tsx';
afterEach(cleanup);
const png=()=>new File([new Uint8Array([137,80,78,71,13,10,26,10,1])],'context.png',{type:'image/png'});
async function attach(){fireEvent.change(screen.getByLabelText('Choose image attachments'),{target:{files:[png()]}});await screen.findByRole('button',{name:'Remove image 1: context.png'})}
it('adds an explicit selection to the draft without sending and replaces /attach rather than sending it',()=>{
 const send=vi.fn(),selected=vi.fn(()=>'<attached: src/main.py L2 (unsaved buffer)>\n```python\nselected\n```');render(<Composer running={false} disabled={false} onSend={send} onInterrupt={()=>{}} onAttachSelection={selected}/>);
 const draft=screen.getByRole('textbox',{name:'Message Machina'});fireEvent.change(draft,{target:{value:'/attach'}});fireEvent.keyDown(draft,{key:'Enter'});
 expect((draft as HTMLTextAreaElement).value).toContain('src/main.py L2');expect((draft as HTMLTextAreaElement).value).not.toContain('/attach');expect(send).not.toHaveBeenCalled();
});
it('retains failed image submissions and sends only the gateway image fields on retry',async()=>{
 const send=vi.fn().mockRejectedValueOnce(new Error('Image provider unavailable')).mockResolvedValueOnce(undefined),count=vi.fn();
 render(<Composer running={false} disabled={false} onSend={send} onInterrupt={()=>{}} onPendingAttachments={count}/>);await attach();
 const draft=screen.getByRole('textbox',{name:'Message Machina'});fireEvent.change(draft,{target:{value:'Inspect this image'}});
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Send message'})));expect(screen.getByRole('alert').textContent).toContain('provider unavailable');expect((draft as HTMLTextAreaElement).value).toBe('Inspect this image');expect(screen.getByRole('button',{name:'Remove image 1: context.png'})).toBeTruthy();
 expect(send.mock.calls[0][1]).toEqual([{name:'context.png',mediaType:'image/png',data:'iVBORw0KGgoB'}]);
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Send message'})));expect((draft as HTMLTextAreaElement).value).toBe('');expect(screen.queryByRole('button',{name:'Remove image 1: context.png'})).toBeNull();expect(count).toHaveBeenLastCalledWith(0);
});
it('keeps image attachments for a later turn instead of silently dropping them from steering',async()=>{
 const send=vi.fn();render(<Composer running disabled={false} onSend={send} onInterrupt={()=>{}}/>);await attach();fireEvent.change(screen.getByRole('textbox',{name:'Message Machina'}),{target:{value:'Steer with image'}});
 await act(async()=>fireEvent.keyDown(screen.getByRole('textbox',{name:'Message Machina'}),{key:'Enter'}));expect(send).not.toHaveBeenCalled();expect(screen.getByRole('alert').textContent).toContain('next turn');expect(screen.getByRole('button',{name:'Remove image 1: context.png'})).toBeTruthy();
});
it('rejects non-image content and oversized batches before sending',async()=>{
 const send=vi.fn();render(<Composer running={false} disabled={false} onSend={send} onInterrupt={()=>{}}/>);
 fireEvent.change(screen.getByLabelText('Choose image attachments'),{target:{files:[new File(['<svg/>'],'pretend.png',{type:'image/png'})]}});await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('PNG'));
 expect(screen.queryByRole('list',{name:'Image attachments'})).toBeNull();expect(send).not.toHaveBeenCalled();
});
