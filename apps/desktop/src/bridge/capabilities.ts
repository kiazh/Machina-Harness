export interface EngineCapabilities {
  engineVersion: string;
  protocolVersion: 2;
  storageProfile: "postgres";
  database: "not-initialized" | "ready";
  methods: string[];
}

export function parseCapabilities(value: unknown): EngineCapabilities {
  if (!value || typeof value !== "object") throw new Error("Invalid engine capability reply");
  const data = value as Record<string, unknown>;
  if (data.protocolVersion !== 2 || typeof data.engineVersion !== "string" || !data.engineVersion ||
    data.storageProfile !== "postgres" || !["not-initialized", "ready"].includes(String(data.database)) ||
    !Array.isArray(data.methods) || data.methods.length > 512 ||
    !data.methods.every(item => typeof item === "string" && /^[a-zA-Z][a-zA-Z0-9._]{0,127}$/.test(item))) {
    throw new Error("Invalid or unsupported engine capability reply");
  }
  return { engineVersion: data.engineVersion, protocolVersion: 2, storageProfile: "postgres", database: data.database as EngineCapabilities["database"], methods: [...data.methods] };
}
