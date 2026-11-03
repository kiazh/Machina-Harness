import {expect,it} from 'vitest';
import {EditorState} from '@codemirror/state';
import {python} from '@codemirror/lang-python';
import {javascript} from '@codemirror/lang-javascript';
import {localDiagnostics,lspDiagnostics} from '../../src/features/editor/diagnostics.ts';

it('finds Python and TypeScript parser errors without a language server',()=>{
 for(const extensions of [python(),javascript({typescript:true})]){
  const state=EditorState.create({doc:'def (\nconst value = ;',extensions});
  const diagnostics=localDiagnostics(state);
  expect(diagnostics.length).toBeGreaterThan(0);
  expect(diagnostics.every(row=>row.severity==='error'&&row.from>=0&&row.to<=state.doc.length)).toBe(true);
 }
 expect(localDiagnostics(EditorState.create({doc:'value = 1\n',extensions:python()}))).toEqual([]);
});
it('maps versioned LSP UTF-16 ranges, severity and source while ignoring malformed ranges',()=>{
 const state=EditorState.create({doc:'😀wrong\nnext'});
 const range={start:{line:0,character:2},end:{line:0,character:7}};
 expect(lspDiagnostics(state,{diagnostics:[{range,severity:1,message:'Unknown name',source:'pyright'},{range:{start:{line:99,character:0},end:{line:99,character:1}},message:'bad'}]})).toEqual([{from:2,to:7,severity:'error',message:'Unknown name',source:'pyright'}]);
});
