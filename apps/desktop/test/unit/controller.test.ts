import { expect, it, vi } from "vitest";
import { DesktopController } from "../../src/state/controller.ts";
import type { DesktopBridge } from "../../src/bridge/types.ts";

function fixture() {
  let listener: (event: Record<string, unknown>) => void = () => {};
  const requests: { method: string; params: Record<string, unknown> }[] = [];
  const bridge: DesktopBridge = {
    async start() {}, subscribe(fn) { listener = fn; return () => {}; },
    async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      requests.push({ method, params });
      const responses: Record<string, unknown> = {
        "runtime.capabilities": { engineVersion: "1.2.14", protocolVersion: 2, storageProfile: "postgres", database: "not-initialized", methods: [] },
        initialize: { cwd: "fixture", cwdFull: "/fixture", model: "fixture-model", provider: "fixture" },
        "config.get": { config: { model: "fixture-model", provider: "fixture" } },
        "session.list": { sessions: [] },
        "workspace.open": { workspaceId: "w", clientId: "c", epoch: 1, rootLabel: "fixture", resolvedRoot: "/fixture", trusted: true, capabilities: [] },
        "session.create": { session: { id: "s", title: "Fixture", model: "fixture-model", provider: "fixture", status: "active", lastActivity: null } },
        "prompt.submit": { turnId: "t" },
        "prompt.steer": { queued: true, items: [{ id: "q", text: params.text }] },
      };
      return (responses[method==='runtime.selectWorkspace'?'workspace.open':method] ?? {}) as T;
    },
  };
  const controller = new DesktopController(bridge);
  return { controller, requests, emit: (value: Record<string, unknown>) => listener({ sessionId: "s", turnId: "t", ...value }) };
}

it("keeps streaming deltas out of the shell state subscription and reconciles the canonical answer once", async () => {
  const f = fixture(); await f.controller.connect(); await f.controller.send("Check it");
  const shell = vi.fn(); f.controller.subscribe(shell);
  f.emit({ type: "message.delta", text: "Answer" });
  f.emit({ type: "message.delta", text: " text" });
  expect(shell).not.toHaveBeenCalled();
  f.emit({ type: "message.complete", text: "Answer text", cancelled: false });
  const messages = f.controller.transcript.order().map(id => f.controller.transcript.get(id)!);
  expect(messages.filter(row => row.role === "assistant").map(row => row.text)).toEqual(["Answer text"]);
  expect(f.controller.snapshot().running).toBe(false);
});

it("queues busy prompts and ignores retired or other-session deltas", async () => {
  const f = fixture(); await f.controller.connect(); await f.controller.send("Start");
  await f.controller.send("Steer");
  expect(f.requests.filter(r => r.method === "prompt.submit")).toHaveLength(1);
  expect(f.requests.some(r => r.method === "prompt.steer" && r.params.text === "Steer")).toBe(true);
  f.emit({ type: "message.complete", text: "Done", cancelled: false });
  f.emit({ type: "message.delta", text: "stale" });
  f.emit({ type: "message.delta", sessionId: "another", turnId: "another", text: "wrong session" });
  expect(f.controller.transcript.order().map(id => f.controller.transcript.get(id)?.text).join(" ")).not.toMatch(/stale|wrong session/);
});
