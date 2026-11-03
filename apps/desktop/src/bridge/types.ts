export interface DesktopBridge {
  start(): Promise<void>;
  request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
  subscribe(listener: (event: Record<string, unknown>) => void): () => void;
}
