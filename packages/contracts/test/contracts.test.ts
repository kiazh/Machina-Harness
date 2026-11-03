import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { RpcError } from "../src/rpc.ts";
import { WorkspaceGatewayAdapter } from "../src/workspace-gateway.ts";
import {RPC_METHODS} from '../src/rpc-methods.ts';

test('renderer and native RPC allowlists are identical, including marketplace and MCP editing',()=>{
 const native=JSON.parse(readFileSync(new URL('../src/rpc-methods.json',import.meta.url),'utf8'));
 assert.deepEqual([...RPC_METHODS],native);
 for(const method of ['mcp.config','skills.marketplace.search','skills.marketplace.preview','skills.marketplace.install'])assert.ok((RPC_METHODS as readonly string[]).includes(method));
});

test("shared errors and workspace bindings remain browser-independent", async () => {
  assert.equal(new RpcError(123, "refused").code, 123);
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  const adapter = new WorkspaceGatewayAdapter(async <T>(method: string, params: Record<string, unknown>) => {
    calls.push({ method, params });
    return (method === "workspace.open" ? { workspaceId: "workspace-a", clientId: "client-a", epoch: 3 } : {}) as T;
  });
  await adapter.open("/workspace", false);
  await adapter.read("file:///workspace/a.py");
  assert.equal(calls[1]!.params.workspaceId, "workspace-a");
  assert.equal(calls[1]!.params.epoch, 3);
});

test("shared source contains no renderer, Node or terminal runtime dependencies", () => {
  for (const file of ["rpc.ts", "events.ts", "workspace.ts", "workspace-gateway.ts"]) {
    const source = readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /from ["'](?:node:|react|@tauri|@earendil)/);
  }
});

test("event copies used by both clients have byte-identical declarations", () => {
  assert.equal(readFileSync(new URL("../src/events.ts", import.meta.url), "utf8"), readFileSync(new URL("../../../ui/src/events.ts", import.meta.url), "utf8"));
});
