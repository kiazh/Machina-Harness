// @vitest-environment jsdom
import {act,cleanup,render} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import type {EditorView} from '@codemirror/view';
import {diagnosticCount,forEachDiagnostic} from '@codemirror/lint';
import {CodeEditor} from '../../src/features/editor/CodeEditor.tsx';
import {refreshDiagnostics} from '../../src/features/editor/diagnostics.ts';

afterEach(()=>{cleanup();vi.useRealTimers()});
const value={diagnostics:[{range:{start:{line:0,character:0},end:{line:0,character:4}},message:'Unknown name',severity:1}]};
it('debounces typing and rejects an older same-document response after a refresh',async()=>{
 vi.useFakeTimers({toFake:['setTimeout','clearTimeout']});let view!:EditorView;
 const replies:Array<(value:unknown)=>void>=[],signals:AbortSignal[]=[];
 const request=vi.fn((_id,_state,signal:AbortSignal)=>{signals.push(signal);return new Promise(resolve=>replies.push(resolve))});
 render(<CodeEditor document={{id:'a',name:'a.py',text:'name'}} onReady={next=>view=next} onDiagnostics={request}/>);
 await act(()=>vi.advanceTimersByTimeAsync(510));expect(request).toHaveBeenCalledTimes(1);
 act(()=>view.dispatch({effects:refreshDiagnostics.of(null)}));
 await act(()=>vi.advanceTimersByTimeAsync(510));expect(signals[0]!.aborted).toBe(true);
 await act(async()=>replies[1]!(value));expect(diagnosticCount(view.state)).toBe(1);
 await act(async()=>replies[0]!({diagnostics:[]}));expect(diagnosticCount(view.state)).toBe(1);
 act(()=>view.dispatch({changes:{from:0,to:4,insert:'other'}}));
 await act(()=>vi.advanceTimersByTimeAsync(300));
 act(()=>view.dispatch({changes:{from:5,insert:'_name'}}));
 await act(()=>vi.advanceTimersByTimeAsync(300));expect(request).toHaveBeenCalledTimes(2);
 await act(()=>vi.advanceTimersByTimeAsync(210));expect(request).toHaveBeenCalledTimes(3);
});
it('shows local syntax errors with no server and clears them when corrected',async()=>{
 vi.useFakeTimers({toFake:['setTimeout','clearTimeout']});let view!:EditorView;
 render(<CodeEditor document={{id:'local',name:'local.py',text:'def ('}} onReady={next=>view=next}/>);
 await act(()=>vi.advanceTimersByTimeAsync(510));expect(diagnosticCount(view.state)).toBeGreaterThan(0);
 const messages:string[]=[];forEachDiagnostic(view.state,row=>messages.push(row.message));expect(messages).toContain('Syntax error');
 act(()=>view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'value = 1\n'}}));
 await act(()=>vi.advanceTimersByTimeAsync(510));expect(diagnosticCount(view.state)).toBe(0);
});
it('cancels outstanding reads on document switch and unmount',async()=>{
 vi.useFakeTimers({toFake:['setTimeout','clearTimeout']});const signals:AbortSignal[]=[];
 const request=vi.fn((_id,_state,signal:AbortSignal)=>{signals.push(signal);return new Promise(()=>{})});
 const props={onDiagnostics:request,onReady:()=>{}};
 const rendered=render(<CodeEditor {...props} document={{id:'a',name:'a.py',text:'name'}}/>);
 await act(()=>vi.advanceTimersByTimeAsync(510));
 rendered.rerender(<CodeEditor {...props} document={{id:'b',name:'b.py',text:'other'}}/>);
 expect(signals[0]!.aborted).toBe(true);
 await act(()=>vi.advanceTimersByTimeAsync(510));rendered.unmount();expect(signals[1]!.aborted).toBe(true);
});
