import {EditorState} from '@codemirror/state';
import {expect,it} from 'vitest';
import {selectionAttachment,checkedImage} from '../../src/features/harness/attachments.ts';
it('attaches only the selected unsaved text with a scoped range and an unambiguous fence',()=>{
 const state=EditorState.create({doc:'private\n```py\nselected\n```\nrest',selection:{anchor:8,head:26}});
 const text=selectionAttachment({id:'x',name:'main.py',text:'disk',languageId:'python'},state,true,'src/main.py');
 expect(text).toContain('src/main.py L2–4 (unsaved buffer)');expect(text).toContain('````python');expect(text).not.toContain('private');expect(text).not.toContain('rest');
});
it('refuses an empty or excessively large selection instead of attaching the whole file',()=>{
 expect(()=>selectionAttachment({id:'x',name:'x',text:''},EditorState.create({doc:'secret'}),false,'x')).toThrow('Select');
 const state=EditorState.create({doc:'x'.repeat(17000),selection:{anchor:0,head:17000}});expect(()=>selectionAttachment({id:'x',name:'x',text:''},state,true,'x')).toThrow('16,000');
});
it('uses actual image bytes and keeps image data inside the desktop transport bound',()=>{
 const png=new Uint8Array([137,80,78,71,13,10,26,10,1]);expect(checkedImage(png,'example.png')).toMatchObject({name:'example.png',mediaType:'image/png',bytes:9});
 expect(()=>checkedImage(new Uint8Array([60,115,118,103]),'image.png')).toThrow('PNG');
 expect(()=>checkedImage(new Uint8Array(4*1024*1024+1),'large.png')).toThrow('4 MiB');
});
