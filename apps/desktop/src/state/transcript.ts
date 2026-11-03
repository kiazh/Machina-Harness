export interface Message {
  id: string;
  role: "user" | "assistant" | "tool" | "notice";
  text: string;
  title?: string;
  status?: string;
  truncated?: boolean;
  streaming?: boolean;
}
type Listener = () => void;

/** Per-message subscriptions keep token traffic out of the editor/app shell. */
export class TranscriptStore {
  private rows = new Map<string, Message>();
  private ids: readonly string[] = [];
  private listeners = new Set<Listener>();
  private rowListeners = new Map<string, Set<Listener>>();
  private pending = new Set<string>();
  private scheduled = false;
  private maxRows: number;
  private maxText: number;
  constructor({ maxRows = 1000, maxText = 65_536 } = {}) { this.maxRows = maxRows; this.maxText = maxText; }
  order = (): readonly string[] => this.ids;
  get = (id: string): Message | undefined => this.rows.get(id);
  subscribe = (listener: Listener): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  subscribeMessage(id: string, listener: Listener): () => void {
    this.flush();
    const listeners = this.rowListeners.get(id) ?? new Set<Listener>();
    listeners.add(listener); this.rowListeners.set(id, listeners);
    return () => { listeners.delete(listener); if (!listeners.size) this.rowListeners.delete(id); };
  }
  put(message: Message): void {
    const isNew = !this.rows.has(message.id);
    this.rows.set(message.id, { ...message, text: message.text.slice(0, this.maxText), truncated: message.truncated || message.text.length > this.maxText });
    if (isNew) {
      this.ids = [...this.ids, message.id];
      while (this.ids.length > this.maxRows) { this.rows.delete(this.ids[0]!); this.ids = this.ids.slice(1); }
      for (const listener of this.listeners) listener();
    }
    this.pending.add(message.id);
    if (!this.scheduled) {
      this.scheduled = true;
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => this.flush());
      else queueMicrotask(() => this.flush());
    }
  }
  append(id: string, text: string): void {
    const prior = this.rows.get(id);
    if (prior) this.put({ ...prior, text: prior.text + text });
  }
  flush(): void {
    this.scheduled = false;
    const changed = [...this.pending]; this.pending.clear();
    for (const id of changed) for (const listener of this.rowListeners.get(id) ?? []) listener();
  }
  remove(id: string): void {
    if (!this.rows.delete(id)) return;
    this.ids = this.ids.filter(value => value !== id); this.pending.delete(id);
    for (const listener of this.rowListeners.get(id) ?? []) listener();
    for (const listener of this.listeners) listener();
  }
  clear(): void { this.rows.clear(); this.ids = []; this.pending.clear(); for (const listener of this.listeners) listener(); }
}
