import type { EditorState } from "@codemirror/state";
import type { ViewUpdate } from "@codemirror/view";
import type { WorkspaceGatewayAdapter } from "@machina/contracts/workspace-gateway";
import type { LspPosition, LspTextEdit, WorkspaceReadResult } from "@machina/contracts/workspace";
import type { CodeDocument } from "../features/editor/CodeEditor.tsx";

export interface OpenDocument extends CodeDocument {
  workspaceRoot?:string;
  externalRevision?:string;
  uri?: string;
  revision?: string;
  languageId?: string;
  readOnly?: boolean;
  lineEnding?: string;
}
interface Buffer {
  remoteVersion: number;
  pending: LspTextEdit[];
  state?: EditorState;
  flushing?: Promise<void>;
  timer?: ReturnType<typeof setTimeout>;
  error?: Error;
  opening?: Promise<void>;
}

export class DocumentSync {
  private buffers = new Map<string, Buffer>();
  constructor(private gateway: WorkspaceGatewayAdapter, private report: (error: unknown) => void) {}
  reset():void{for(const buffer of this.buffers.values())clearTimeout(buffer.timer);this.buffers.clear()}
  async open(read: WorkspaceReadResult, state?: EditorState): Promise<OpenDocument> {
    // Install the new mirror before awaiting the engine so concurrent typing is
    // queued against the exact text being opened, never an orphaned old buffer.
    const buffer: Buffer = { remoteVersion: 0, pending: [], state };
    this.buffers.set(read.uri, buffer);
    buffer.opening = (async () => {
      const result = await this.gateway.documentOpen(read.uri, read.languageId, 0, read.text);
      if ("refused" in result) throw new Error(result.refused.message);
      if (result.acceptedVersion !== 0) throw new Error("Document synchronization version mismatch while reopening.");
    })();
    try { await buffer.opening; }
    catch (error) { buffer.error = error instanceof Error ? error : new Error("Could not reopen the document."); throw buffer.error; }
    finally { buffer.opening = undefined; }
    return { ...read, id: read.uri, name: decodeURIComponent(read.uri.split("/").at(-1) ?? "File") };
  }
  bind(id: string, state: EditorState): void {
    const buffer = this.buffers.get(id); if (buffer) buffer.state = state;
  }
  changed(id: string, update: ViewUpdate): void {
    const buffer = this.buffers.get(id);
    if (!buffer) return;
    buffer.state = update.state;
    const edits: LspTextEdit[] = [];
    update.changes.iterChanges((from, to, _newFrom, _newTo, inserted) => {
      const first = update.startState.doc.lineAt(from); const last = update.startState.doc.lineAt(to);
      edits.push({ range: { start: { line: first.number - 1, character: from - first.from }, end: { line: last.number - 1, character: to - last.from } }, newText: inserted.toString() });
    });
    // CodeMirror offsets share the old transaction document. Apply in descending
    // order so earlier edits do not shift later LSP positions in this batch.
    buffer.pending.push(...edits.reverse());
    clearTimeout(buffer.timer);
    buffer.timer = setTimeout(() => { void this.flush(id).catch(this.report); }, 180);
  }
  async flush(id: string): Promise<void> {
    const buffer = this.buffers.get(id);
    if (!buffer) return;
    clearTimeout(buffer.timer);
    if (buffer.opening) await buffer.opening;
    if (buffer.flushing) { await buffer.flushing; return this.flush(id); }
    if (buffer.error) throw buffer.error;
    if (!buffer.pending.length) return;
    const edits = buffer.pending.splice(0);
    const version = buffer.remoteVersion + 1;
    buffer.flushing = (async () => {
      const result = await this.gateway.documentChange(id, { baseVersion: buffer.remoteVersion, version, changes: edits.map(edit => ({ range: edit.range, text: edit.newText })) });
      if ("refused" in result) throw new Error(result.refused.message);
      if (result.resync || result.acceptedVersion !== version) throw new Error("Document synchronization version mismatch. Your local changes are retained; saving is paused.");
      buffer.remoteVersion = result.acceptedVersion;
    })();
    try { await buffer.flushing; }
    catch (error) { buffer.error = error instanceof Error ? error : new Error("Document synchronization failed"); throw buffer.error; }
    finally { buffer.flushing = undefined; }
    if (buffer.pending.length) await this.flush(id);
  }
  async save(document: OpenDocument): Promise<{ revision: string; unchanged: boolean }> {
    if (!document.uri || !document.revision) throw new Error("Choose a project file before saving. Untitled file saving is not connected yet.");
    if (document.readOnly) throw new Error("This file is read-only.");
    if (document.lineEnding === "mixed") throw new Error("This file has mixed line endings. Save is paused to avoid silently normalizing the file.");
    await this.flush(document.id);
    const buffer = this.buffers.get(document.id);
    const captured = buffer?.state;
    if (!buffer || !captured) throw new Error("The editor buffer is unavailable; local edits are retained.");
    const result = await this.gateway.save(document.uri, document.revision, captured.sliceDoc(), buffer.remoteVersion);
    if (!result.ok) throw new Error(result.conflict ? "The file changed on disk. Your local edits are retained; reload or review before overwriting." : result.refused?.message ?? "Save failed; your local edits are retained.");
    return { revision: result.revision!, unchanged: buffer.state === captured };
  }
  version(id: string): number | null { return this.buffers.get(id)?.remoteVersion ?? null; }
  async close(id:string):Promise<void>{const buffer=this.buffers.get(id);if(!buffer)return;clearTimeout(buffer.timer);if(buffer.flushing)await buffer.flushing;const result=await this.gateway.documentClose(id);if('refused' in result)throw new Error(result.refused.message);this.buffers.delete(id)}
  async completion(id: string, state: EditorState, position: LspPosition): Promise<unknown | null> {
    return this.intelligence('completion',id,state,position);
  }
  async diagnostics(id:string,state:EditorState,signal?:AbortSignal):Promise<unknown|null>{
    return this.intelligence('diagnostics',id,state,undefined,signal);
  }
  private async intelligence(operation:'completion'|'diagnostics',id:string,state:EditorState,position?:LspPosition,signal?:AbortSignal):Promise<unknown|null>{
    const buffer = this.buffers.get(id);
    const workspace = this.gateway.identity();
    if (signal?.aborted || !buffer || !workspace || buffer.state?.doc !== state.doc) return null;
    await this.flush(id);
    if (signal?.aborted || buffer.state?.doc !== state.doc || this.gateway.identity() !== workspace) return null;
    const version = buffer.remoteVersion;
    const result = await this.gateway.intelligence(operation, id, version, position);
    if (signal?.aborted || "refused" in result || this.buffers.get(id)!==buffer || buffer.state?.doc !== state.doc || this.gateway.identity() !== workspace || result.workspaceId !== workspace.workspaceId || result.workspaceEpoch !== workspace.epoch || result.documentVersion !== version || result.state !== "ready" || result.freshness !== "versioned") return null;
    return result.result;
  }
}
