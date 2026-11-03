// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react';
import { EditorState, Text } from '@codemirror/state';
import { undo } from '@codemirror/commands';
import type { EditorView } from '@codemirror/view';
import { afterEach, expect, it } from 'vitest';
import { CodeEditor } from '../../src/features/editor/CodeEditor.tsx';

afterEach(cleanup);

it.each([
  { from: 'CRLF', before: 'old\r\nsecond', to: 'LF', after: 'new\nsecond', undone: 'old\nsecond' },
  { from: 'LF', before: 'old\nsecond', to: 'CRLF', after: 'new\r\nsecond', undone: 'old\r\nsecond' },
])('reconfigures $from to $to on reload while retaining the view and undo history', ({ from, before, to, after, undone }) => {
  let view!: EditorView;
  const ready = (next: EditorView) => { view = next; };
  const document = { id: 'a', name: 'a.py', text: before, lineEnding: from };
  const rendered = render(<CodeEditor document={document} onReady={ready} />);
  const original = view;
  const state = view.state.update({ changes: { from: 0, to: view.state.doc.length, insert: Text.of(['new', 'second']) } }).state;
  rendered.rerender(<CodeEditor document={{ ...document, text: after, lineEnding: to, initialState: state, reloadToken: 1 }} onReady={ready} />);
  expect(view).toBe(original);
  expect(view.state.sliceDoc()).toBe(after);
  expect(view.state.doc.lines).toBe(2);
  act(() => { undo(view); });
  expect(view.state.sliceDoc()).toBe(undone);
});

it('keeps line endings per tab and updates reused state metadata after Save As', () => {
  let view!: EditorView;
  const ready = (next: EditorView) => { view = next; };
  const a = { id: 'a', name: 'a.py', text: 'alpha\r\nsecond', lineEnding: 'CRLF' };
  const b = { id: 'b', name: 'b.py', text: 'beta\nsecond', lineEnding: 'LF' };
  const rendered = render(<CodeEditor document={a} onReady={ready} />);
  act(() => view.dispatch({ changes: { from: 5, insert: ' edit' } }));
  const savedState = view.state;
  rendered.rerender(<CodeEditor document={b} onReady={ready} />);
  expect(view.state.sliceDoc()).toBe('beta\nsecond');
  rendered.rerender(<CodeEditor document={a} onReady={ready} />);
  expect(view.state.sliceDoc()).toBe('alpha edit\r\nsecond');
  rendered.rerender(<CodeEditor document={{ id: 'saved', name: 'saved.py', text: 'alpha edit\nsecond', lineEnding: 'LF', initialState: savedState }} onReady={ready} />);
  expect(view.state.sliceDoc()).toBe('alpha edit\nsecond');
  act(() => { undo(view); });
  expect(view.state.sliceDoc()).toBe('alpha\nsecond');
});

it('updates read-only metadata on the same active document', () => {
  let view!: EditorView;
  const ready = (next: EditorView) => { view = next; };
  const document = { id: 'a', name: 'a.py', text: 'alpha' };
  const rendered = render(<CodeEditor document={document} onReady={ready} />);
  rendered.rerender(<CodeEditor document={{ ...document, readOnly: true }} onReady={ready} />);
  expect(view.state.readOnly).toBe(true);
  rendered.rerender(<CodeEditor document={document} onReady={ready} />);
  expect(view.state.readOnly).toBe(false);
});
