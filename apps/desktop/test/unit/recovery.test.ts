import { EditorState } from "@codemirror/state";
import { afterEach, expect, it, vi } from "vitest";
import { RecoveryStore, type RecoveryRecord, type RecoveryRepository } from "../../src/state/recovery.ts";

afterEach(() => vi.useRealTimers());
function fixture() {
  const records = new Map<string, RecoveryRecord>();
  const write = vi.fn(async (record: RecoveryRecord) => { records.set(record.id, record); });
  const repository: RecoveryRepository = { load: async () => [...records.values()], write, remove: async id => { records.delete(id); } };
  return { store: new RecoveryStore(repository), records, write };
}
it("coalesces keystrokes and retains only the latest buffer and draft", async () => {
  vi.useFakeTimers(); const f = fixture();
  for (let index = 0; index < 100; index++) f.store.document({ id: "a", name: "a.py", text: "" }, EditorState.create({ doc: `latest ${index}` }));
  f.store.draft("old"); f.store.draft("new");
  expect(f.write).not.toHaveBeenCalled();
  await f.store.flush();
  expect(f.write).toHaveBeenCalledTimes(2);
  expect(f.records.get("a")?.text).toBe("latest 99");
  expect(f.records.get("$composer")?.text).toBe("new");
});
it("does not resurrect a saved document from a pending recovery write", async () => {
  vi.useFakeTimers(); const f = fixture();
  f.store.document({ id: "a", name: "a.py", text: "" }, EditorState.create({ doc: "edit" }));
  await f.store.forget("a"); await f.store.flush();
  expect(await f.store.load()).toEqual([]);
});
it("retains failed writes for retry and discloses recovery failures", async () => {
  vi.useFakeTimers(); const f = fixture();
  f.store.draft("keep me"); f.write.mockRejectedValueOnce(new Error("storage full"));
  await expect(f.store.flush()).rejects.toThrow("storage full");
  await f.store.flush();
  expect(f.records.get("$composer")?.text).toBe("keep me");
});

it("a queued saved-buffer removal cannot delete a newer edit drained by an in-flight write", async () => {
  vi.useFakeTimers(); const f = fixture();
  let finish!: () => void;
  f.write.mockImplementationOnce(async record => { await new Promise<void>(resolve => { finish = resolve; }); f.records.set(record.id, record); });
  f.store.document({ id: "a", name: "a.py", text: "" }, EditorState.create({ doc: "saved version" }));
  const writing = f.store.flush(); await Promise.resolve(); await Promise.resolve();
  const forgetting = f.store.forget("a");
  f.store.document({ id: "a", name: "a.py", text: "" }, EditorState.create({ doc: "new unsaved edit" }));
  finish(); await writing; await forgetting; await f.store.flush();
  expect(f.records.get("a")?.text).toBe("new unsaved edit");
});
