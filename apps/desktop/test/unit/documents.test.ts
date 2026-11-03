import { EditorState, type TransactionSpec } from "@codemirror/state";
import type { ViewUpdate } from "@codemirror/view";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceGatewayAdapter } from "@machina/contracts/workspace-gateway";
import { DocumentSync } from "../../src/state/documents.ts";

afterEach(() => vi.useRealTimers());

async function setup(text = "one 😀\nlast\n") {
  vi.useFakeTimers();
  const request = vi.fn(async <T>(method: string, params: Record<string, unknown>): Promise<T> => {
    const results: Record<string, unknown> = {
      "workspace.open": { workspaceId: "w", clientId: "c", epoch: 1 },
      "workspace.documentOpen": { acceptedVersion: 0 },
      "workspace.documentChange": { acceptedVersion: params.version },
      "workspace.save": { ok: true, revision: "saved" },
    };
    return results[method==='runtime.selectWorkspace'?'workspace.open':method] as T;
  });
  const gateway = new WorkspaceGatewayAdapter(async <T>(method: string, params: Record<string, unknown>) => await request(method, params) as T);
  await gateway.open("/fixture", true);
  const sync = new DocumentSync(gateway, vi.fn());
  const document = await sync.open({ uri: "file:///fixture/test.py", text, revision: "base", languageId: "python", lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: text.length });
  let state = EditorState.create({ doc: text }); sync.bind(document.id, state);
  const edit = (changes: TransactionSpec) => {
    const transaction = state.update(changes);
    sync.changed(document.id, { state: transaction.state, startState: state, changes: transaction.changes } as ViewUpdate);
    state = transaction.state;
  };
  return { sync, document, request, edit };
}

it("flushes descending UTF-16 edits before saving the captured buffer", async () => {
  const f = await setup();
  f.edit({ changes: [{ from: 6, insert: "!" }, { from: 7, to: 11, insert: "tail" }] });
  await f.sync.save(f.document);
  const change = f.request.mock.calls.find(([method]) => method === "workspace.documentChange")![1];
  expect(change.changes).toEqual([
    { range: { start: { line: 1, character: 0 }, end: { line: 1, character: 4 } }, text: "tail" },
    { range: { start: { line: 0, character: 6 }, end: { line: 0, character: 6 } }, text: "!" },
  ]);
  const save = f.request.mock.calls.find(([method]) => method === "workspace.save")![1];
  expect(save.expectedRevision).toBe("base"); expect(save.text).toBe("one 😀!\ntail\n");
});

it("rejects a mismatched mirror acknowledgement and never writes the file", async () => {
  const f = await setup(); f.edit({ changes: { from: 0, insert: "#" } });
  f.request.mockImplementation(async <T>() => ({ acceptedVersion: 900 } as T));
  await expect(f.sync.save(f.document)).rejects.toThrow(/version|synchroniz/i);
  expect(f.request.mock.calls.filter(([method]) => method === "workspace.save")).toHaveLength(0);
});

it("retains dirty state when another edit arrives while disk save is in flight", async () => {
  const f = await setup(); f.edit({ changes: { from: 0, insert: "a" } });
  await f.sync.flush(f.document.id);
  let finish!: (value: unknown) => void;
  f.request.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const saving = f.sync.save(f.document); await Promise.resolve();
  f.edit({ changes: { from: 0, insert: "b" } });
  finish({ ok: true, revision: "new" });
  expect(await saving).toEqual({ revision: "new", unchanged: false });
});

it("reports an external disk conflict without retrying or overwriting", async () => {
  const f = await setup();
  f.request.mockImplementation(async <T>() => ({ ok: false, conflict: { reason: "changed", diskRevision: "other", baseRevision: "base" } } as T));
  await expect(f.sync.save(f.document)).rejects.toThrow(/changed on disk/);
  expect(f.request.mock.calls.filter(([method]) => method === "workspace.save")).toHaveLength(1);
});

it("preserves edits made while the engine reopens a document mirror", async () => {
  const f = await setup("old");
  let finish!: (value: unknown) => void;
  const realRequest = f.request.getMockImplementation()!;
  f.request.mockImplementation((method, params) => method === "workspace.documentOpen" ? new Promise(resolve => { finish = resolve; }) : realRequest(method, params));
  const initial = EditorState.create({ doc: "old" });
  const opening = f.sync.open({ uri: f.document.id, text: "old", revision: "base", languageId: "python", lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 3 }, initial);
  f.edit({ changes: { from: 3, insert: " new edit" } });
  finish({ acceptedVersion: 0 }); await opening;
  await f.sync.save(f.document);
  const save = f.request.mock.calls.find(([method]) => method === "workspace.save")![1];
  expect(save.text).toBe("old new edit");
  expect(f.request.mock.calls.find(([method]) => method === "workspace.documentChange")).toBeDefined();
});
