import { EditorState } from "@codemirror/state";
import { expect, it } from "vitest";
import { completionItems, offsetAt,completionGhost } from "../../src/features/editor/completion.ts";

it("converts UTF-16 positions including a final empty line without clamping invalid ranges", () => {
  const state = EditorState.create({ doc: "a😀\nlast\n" });
  expect(offsetAt(state, { line: 0, character: 3 })).toBe(3);
  expect(offsetAt(state, { line: 2, character: 0 })).toBe(9);
  expect(offsetAt(state, { line: 0, character: 4 })).toBeNull();
  expect(offsetAt(state, { line: 9, character: 0 })).toBeNull();
  expect(offsetAt(state, { line: 0, character: -1 })).toBeNull();
});
it('shows only the exact version-bound insertion suffix as ghost text',()=>{const state=EditorState.create({doc:'path.Pa',selection:{anchor:7}});const option=completionItems(state,7,{items:[{label:'Path'}]})!.options[0]!;expect(completionGhost(state,option)).toBe('th');expect(completionGhost(state.update({selection:{anchor:6}}).state,option)).toBe('');expect(completionGhost(state.update({changes:{from:0,insert:'#'}}).state,option)).toBe('');expect(completionGhost(state,{label:'Imagined'})).toBe('')});

it("drops completion commands, snippets and overlapping or invalid edits rather than inserting corrupt text", () => {
  const state = EditorState.create({ doc: "value.na" });
  const range = { start: { line: 0, character: 6 }, end: { line: 0, character: 8 } };
  const result = completionItems(state, 8, { items: [
    { label: "name", textEdit: { range, newText: "name" } },
    { label: "bad snippet", insertText: "func($1)", insertTextFormat: 2 },
    { label: "bad range", textEdit: { range: { start: { line: 7, character: 0 }, end: { line: 7, character: 1 } }, newText: "no" } },
    { label: "overlap", textEdit: { range, newText: "a" }, additionalTextEdits: [{ range, newText: "b" }] },
  ] });
  expect(result?.options.map(option => option.label)).toEqual(["name"]);
  expect(result?.from).toBe(6);
});
