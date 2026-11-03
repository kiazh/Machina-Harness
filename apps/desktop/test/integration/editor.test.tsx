// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { EditorView } from "@codemirror/view";
import { undo } from "@codemirror/commands";
import { CodeEditor } from "../../src/features/editor/CodeEditor.tsx";

afterEach(cleanup);
it('refreshes a changed clean file without replacing the editor and can undo the refresh',()=>{let view:EditorView|undefined;const ready=(value:EditorView)=>view=value;const doc={id:'a',name:'a.py',text:'before'};const mounted=render(<CodeEditor document={doc} onReady={ready}/>);const original=view;const state=view!.state.update({changes:{from:0,to:6,insert:'on disk'}}).state;mounted.rerender(<CodeEditor document={{...doc,text:'on disk',initialState:state,reloadToken:1}} onReady={ready}/>);expect(view).toBe(original);expect(view!.state.doc.toString()).toBe('on disk');act(()=>{undo(view!)});expect(view!.state.doc.toString()).toBe('before')});
it('does not resurrect discarded edits after a closed file is reopened',()=>{
 let view:EditorView|undefined;const ready=(value:EditorView)=>{view=value};
 const a={id:'a',name:'a.py',text:'disk contents'},b={id:'b',name:'b.py',text:'other'};
 const rendered=render(<CodeEditor document={a} openIds={['a','b']} onReady={ready}/>);
 act(()=>view!.dispatch({changes:{from:0,to:view!.state.doc.length,insert:'discarded edit'}}));
 rendered.rerender(<CodeEditor document={b} openIds={['b']} onReady={ready}/>);
 rendered.rerender(<CodeEditor document={a} openIds={['a','b']} onReady={ready}/>);
 expect(view!.state.doc.toString()).toBe('disk contents');
});

it("retains the actual editor, local edits and undo through parent rerenders", () => {
  let view: EditorView | undefined;
  const ready = vi.fn((next: EditorView) => { view = next; });
  const change = vi.fn();
  const document = { id: "untitled:1", name: "Untitled.py", text: "before" };
  const rendered = render(<CodeEditor document={document} onReady={ready} onChange={change} />);
  const original = view;
  act(() => view!.dispatch({ changes: { from: 6, insert: " after" } }));
  rendered.rerender(<CodeEditor document={{ ...document }} onReady={ready} onChange={change} />);
  expect(view).toBe(original);
  expect(ready).toHaveBeenCalledTimes(1);
  expect(view!.state.doc.toString()).toBe("before after");
  expect(change).toHaveBeenCalledTimes(1);
  act(() => { undo(view!); });
  expect(view!.state.doc.toString()).toBe("before");
});

it("switches documents without mixing their edits or undo stacks", () => {
  let view: EditorView | undefined;
  const ready = (next: EditorView) => { view = next; };
  const a = { id: "a", name: "a.py", text: "alpha" };
  const b = { id: "b", name: "b.py", text: "beta" };
  const rendered = render(<CodeEditor document={a} onReady={ready} />);
  act(() => view!.dispatch({ changes: { from: 5, insert: " edit" } }));
  rendered.rerender(<CodeEditor document={b} onReady={ready} />);
  expect(view!.state.doc.toString()).toBe("beta");
  rendered.rerender(<CodeEditor document={a} onReady={ready} />);
  expect(view!.state.doc.toString()).toBe("alpha edit");
});
