import { expect, it, vi } from "vitest";
import { TranscriptStore } from "../../src/state/transcript.ts";

it("a streaming delta changes only its message and retains the order snapshot", () => {
  const store = new TranscriptStore();
  store.put({ id: "a", role: "assistant", text: "one" });
  store.put({ id: "b", role: "assistant", text: "two" });
  const order = store.order();
  const a = vi.fn(); const b = vi.fn(); const list = vi.fn();
  store.subscribeMessage("a", a); store.subscribeMessage("b", b); store.subscribe(list);
  store.append("a", " more");
  store.flush();
  expect(store.get("a")?.text).toBe("one more");
  expect(store.order()).toBe(order);
  expect(a).toHaveBeenCalledTimes(1);
  expect(b).not.toHaveBeenCalled();
  expect(list).not.toHaveBeenCalled();
});

it("bounds retained rows and message content without losing explicit truncation state", () => {
  const store = new TranscriptStore({ maxRows: 3, maxText: 32 });
  for (let i = 0; i < 10; i++) store.put({ id: String(i), role: "assistant", text: "x".repeat(100) });
  store.flush();
  expect(store.order()).toEqual(["7", "8", "9"]);
  expect(store.get("0")).toBeUndefined();
  expect(store.get("9")?.text.length).toBeLessThanOrEqual(32);
  expect(store.get("9")?.truncated).toBe(true);
});
