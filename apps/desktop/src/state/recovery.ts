import type { EditorState } from "@codemirror/state";
import type { OpenDocument } from "./documents.ts";

export interface RecoveryRecord {
  workspaceRoot?:string;
  id: string; name: string; text: string; uri?: string; revision?: string;
  languageId?: string; lineEnding?: string; updatedAt: number;
}
export interface RecoveryRepository {
  load(): Promise<RecoveryRecord[]>;
  write(record: RecoveryRecord): Promise<void>;
  remove(id: string): Promise<void>;
}

/** Retain immutable editor states cheaply; serialize at most once per 750ms. */
export class RecoveryStore {
  private pending = new Map<string, () => RecoveryRecord>();
  private generations = new Map<string, number>();
  private timer?: ReturnType<typeof setTimeout>;
  private writing: Promise<void> = Promise.resolve();
  initialDraft = "";
  constructor(private repository: RecoveryRepository, private report: (error: unknown) => void = () => {}) {}
  document(document: OpenDocument, state: EditorState): void {
    this.generations.set(document.id, (this.generations.get(document.id) ?? 0) + 1);
    this.pending.set(document.id, () => ({ id: document.id, name: document.name, workspaceRoot:document.workspaceRoot, uri: document.uri, revision: document.revision, languageId: document.languageId, lineEnding: document.lineEnding, text: state.sliceDoc(), updatedAt: Date.now() }));
    this.schedule();
  }
  draft(text: string): void {
    this.pending.set("$composer", () => ({ id: "$composer", name: "Message draft", text, updatedAt: Date.now() })); this.schedule();
  }
  private schedule(): void { this.timer ??= setTimeout(() => { this.timer = undefined; void this.flush().catch(this.report); }, 750); }
  async load(): Promise<RecoveryRecord[]> {
    const records = await this.repository.load();
    this.initialDraft = records.find(record => record.id === "$composer")?.text ?? "";
    return records.filter(record => record.id !== "$composer");
  }
  flush(): Promise<void> {
    clearTimeout(this.timer); this.timer = undefined;
    this.writing = this.writing.catch(() => {}).then(async () => {
      while (this.pending.size) {
        const [id, build] = this.pending.entries().next().value!;
        this.pending.delete(id);
        try {
          const record = build();
          if (record.text.length > 4 * 1024 * 1024) throw new Error(`Recovery buffer for ${record.name} exceeds 4 MiB. Keep the window open and save your work.`);
          await this.repository.write(record);
        } catch (error) { if (!this.pending.has(id)) this.pending.set(id, build); throw error; }
      }
    });
    return this.writing;
  }
  forget(id: string): Promise<void> {
    this.pending.delete(id);
    const generation = this.generations.get(id) ?? 0;
    this.writing = this.writing.catch(() => {}).then(async () => {
      if ((this.generations.get(id) ?? 0) === generation) await this.repository.remove(id);
    });
    return this.writing;
  }
}

export class IndexedDbRecovery implements RecoveryRepository {
  private database?: Promise<IDBDatabase>;
  private open(): Promise<IDBDatabase> {
    return this.database ??= new Promise((resolve, reject) => {
      const request = indexedDB.open("machina-desktop-recovery", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("buffers", { keyPath: "id" });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new Error("Local recovery storage is unavailable. Keep the window open and save your work."));
    });
  }
  private async transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const database = await this.open();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction("buffers", mode);
      const request = action(transaction.objectStore("buffers"));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = transaction.onabort = () => reject(new Error("Could not persist local recovery data. Keep the window open and save your work."));
    });
  }
  async load(): Promise<RecoveryRecord[]> {
    const records = await this.transaction("readonly", store => store.getAll());
    return records.filter((record): record is RecoveryRecord => record && typeof record.id === "string" && typeof record.name === "string" && typeof record.text === "string");
  }
  async write(record: RecoveryRecord): Promise<void> { await this.transaction("readwrite", store => store.put(record)); }
  async remove(id: string): Promise<void> { await this.transaction("readwrite", store => store.delete(id)); }
}
