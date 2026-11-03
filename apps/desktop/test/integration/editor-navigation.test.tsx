// @vitest-environment jsdom
import {cleanup,render} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import type {EditorView} from '@codemirror/view';
import {CodeEditor} from '../../src/features/editor/CodeEditor.tsx';
afterEach(cleanup);
it('navigates to a result location without editing the buffer and clamps outdated positions',()=>{
 let editor!:EditorView;const change=vi.fn();const document={id:'main',name:'main.py',text:'one\ntwo\nthree'};
 const {rerender}=render(<CodeEditor document={document} navigation={{documentId:'main',line:1,character:2,token:1}} onReady={view=>{editor=view}} onChange={change}/>);
 expect(editor.state.selection.main.head).toBe(6);expect(change).not.toHaveBeenCalled();
 rerender(<CodeEditor document={document} navigation={{documentId:'main',line:999,character:999,token:2}}/>);
 expect(editor.state.selection.main.head).toBe(editor.state.doc.length);expect(editor.state.doc.toString()).toBe(document.text);
});
