import { describe, expect, it } from "vitest";
import { RPC_METHODS } from "@machina/contracts/rpc-methods";
import { parseCapabilities } from "../../src/bridge/capabilities.ts";

describe("engine capability handshake", () => {
  it("accepts every method name in the actual generated engine registry", () => {
    expect(parseCapabilities({ engineVersion: "1.2.14", protocolVersion: 2, storageProfile: "postgres", database: "not-initialized", methods: RPC_METHODS }).methods).toContain("skills.preview_route");
  });
  it("accepts explicit supported versions and copies the method list", () => {
    const methods = ["runtime.capabilities", "initialize"];
    const result = parseCapabilities({ engineVersion: "1.2.14", protocolVersion: 2, supportedProtocolVersions: [1, 2], database: "not-initialized", storageProfile: "postgres", methods });
    methods.push("untrusted");
    expect(result.methods).toEqual(["runtime.capabilities", "initialize"]);
    expect(result.database).toBe("not-initialized");
  });
  it.each([null, {}, { protocolVersion: 99 }, { protocolVersion: 2, methods: [1] }])("rejects invalid/incompatible replies", value => {
    expect(() => parseCapabilities(value)).toThrow(/engine capability reply/);
  });
});
