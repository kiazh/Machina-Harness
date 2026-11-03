import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { DesktopBridge } from "./types.ts";
import { RPC_METHODS } from "@machina/contracts/rpc-methods";

export class NativeBridge implements DesktopBridge {
  private generation = 0;
  private listening?: Promise<() => void>;
  private listeners = new Set<(event: Record<string, unknown>) => void>();

  async start(): Promise<void> {
    if (!isTauri()) throw new Error("Open the native desktop application to connect the local engine.");
    this.listening ??= listen<{ generation: number; payload: Record<string, unknown> }>("machina:engine", event => {
      if (event.payload.generation !== this.generation) return;
      const payload = event.payload.payload;
      if (payload.type === "desktop.batch" && Array.isArray(payload.events)) {
        for (const item of payload.events) if (item && typeof item === "object") for (const listener of this.listeners) listener(item);
      } else for (const listener of this.listeners) listener(payload);
    });
    await this.listening;
    const result = await invoke<{ generation: number }>("engine_start");
    this.generation = result.generation;
  }
  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.generation) throw new Error("The engine is not connected.");
    if (!(RPC_METHODS as readonly string[]).includes(method)) throw new Error("Unsupported engine method.");
    try { return await invoke<T>("engine_request", { generation: this.generation, method, params }); }
    catch (error) { throw new Error(typeof error === "string" ? error : "Engine request failed."); }
  }
  subscribe(listener: (event: Record<string, unknown>) => void): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }
}
