import type { EditorState } from "@codemirror/state";
import type { Completion, CompletionResult, CompletionSource } from "@codemirror/autocomplete";
import type { LspPosition } from "@machina/contracts/workspace";

export type IntelligenceRequest = (id: string, state: EditorState, position: LspPosition) => Promise<unknown | null>;
export interface GhostCompletion extends Completion {machinaGhost?:{document:EditorState['doc'];from:number;to:number;insert:string}}
export function completionGhost(state:EditorState,completion:Completion|null):string{
  const value=(completion as GhostCompletion|null)?.machinaGhost;const cursor=state.selection.main;
  if(!value||state.doc!==value.document||!cursor.empty||cursor.head!==value.to||value.insert.includes('\n')||value.insert.length>512)return '';
  const typed=state.sliceDoc(value.from,value.to);return value.insert.startsWith(typed)?value.insert.slice(typed.length):'';
}

export function offsetAt(state: EditorState, position: LspPosition): number | null {
  if (!position || !Number.isInteger(position.line) || !Number.isInteger(position.character) || position.line < 0 || position.character < 0 || position.line >= state.doc.lines) return null;
  const line = state.doc.line(position.line + 1);
  return position.character <= line.length ? line.from + position.character : null;
}

interface Edit { from: number; to: number; insert: string }
function textEdit(state: EditorState, raw: any): Edit | null {
  if (!raw || typeof raw.newText !== "string" || raw.newText.length > 65_536) return null;
  const from = offsetAt(state, raw.range?.start); const to = offsetAt(state, raw.range?.end);
  return from !== null && to !== null && from <= to ? { from, to, insert: raw.newText } : null;
}

/** Only plain, version-bound edits are applied; unsupported snippets stay unavailable. */
export function completionItems(state: EditorState, position: number, raw: any): CompletionResult | null {
  if (!raw || !Array.isArray(raw.items)) return null;
  const before = state.sliceDoc(state.doc.lineAt(position).from, position);
  const word = /[\p{L}\p{N}_$]*$/u.exec(before)![0];
  const from = position - word.length;
  const options: GhostCompletion[] = [];
  for (const item of raw.items.slice(0, 100)) {
    if (!item || typeof item.label !== "string" || item.insertTextFormat === 2) continue;
    const primary = item.textEdit ? textEdit(state, item.textEdit) : { from, to: position, insert: typeof item.insertText === "string" ? item.insertText : item.label };
    if (!primary || primary.from > position || primary.to < position) continue;
    const extra: Edit[] = [];
    let invalid = false;
    if (item.additionalTextEdits !== undefined && !Array.isArray(item.additionalTextEdits)) continue;
    for (const rawEdit of (item.additionalTextEdits ?? []).slice(0, 32)) {
      const edit = textEdit(state, rawEdit); if (!edit) { invalid = true; break; } extra.push(edit);
    }
    const edits = [primary, ...extra].sort((a, b) => a.from - b.from || a.to - b.to);
    if (invalid || edits.some((edit, index) => index > 0 && edit.from <= edits[index - 1]!.to)) continue;
    options.push({
      label: item.label.slice(0, 512), detail: typeof item.detail === "string" ? item.detail.slice(0, 512) : undefined,
      info: typeof item.documentation === "string" ? item.documentation.slice(0, 4000) : undefined,
      machinaGhost:{document:state.doc,from:primary.from,to:primary.to,insert:primary.insert},
      apply(view) {
        // Selection movement is harmless, document movement is not. Never map an
        // old server range onto a modified buffer by guessing.
        if (view.state.doc !== state.doc || view.state.readOnly || view.composing) return;
        const shift = extra.filter(edit => edit.to <= primary.from).reduce((sum, edit) => sum + edit.insert.length - (edit.to - edit.from), 0);
        view.dispatch({ changes: edits, selection: { anchor: primary.from + primary.insert.length + shift }, userEvent: "input.complete" });
      },
    });
  }
  return options.length ? { from, options, filter: true } : null;
}

export function completionSource(id: string, request: IntelligenceRequest): CompletionSource {
  return async context => {
    if (context.state.readOnly || context.state.selection.ranges.length !== 1 || !context.state.selection.main.empty) return null;
    const line = context.state.doc.lineAt(context.pos);
    if (!context.explicit && !/[\p{L}\p{N}_$.]$/u.test(context.state.sliceDoc(Math.max(line.from, context.pos - 1), context.pos))) return null;
    const result = await request(id, context.state, { line: line.number - 1, character: context.pos - line.from });
    return context.aborted ? null : completionItems(context.state, context.pos, result);
  };
}
