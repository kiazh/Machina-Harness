export type Preset = "porcelain" | "ink" | "volt" | "afterimage" | "chrome" | "vellum" | "glacier" | "oxide" | "nocturne";
export type Motion = "full" | "subtle" | "off";
export interface Appearance {
  preset: Preset; glass: number; blur: number; motion: Motion;
  background: "art" | "custom" | "none"; accent: string;
  uiSize: number; codeSize: number; uiFont: string; codeFont: string;
}
export const PRESETS = {
  porcelain:{surface:'oklch(98% .005 250)',name:'Porcelain',description:'Blue ink. An open white workspace.',accent:'#2452ff',glass:.98,blur:0},
  ink:{surface:'oklch(20% .008 260)',name:'Ink',description:'A quiet workspace after dark.',accent:'#a0bbf4',glass:.98,blur:0},
  afterimage: {surface:'oklch(21% .025 260)', name: "Afterimage", description: "Blue ink. White light.", accent: "#8eb8ff", glass: .64, blur: 14 },
  volt: {surface:'oklch(20.5% .02 120)', name: "Volt", description: "High contrast. Pure signal.", accent: "#d5f900", glass: .88, blur: 4 },
  chrome: {surface:'oklch(96.5% .01 260)', name: "Chrome", description: "Silver. Precision. Light.", accent: "#183df2", glass: .76, blur: 20 },
  vellum:{surface:'oklch(96.5% .016 82)',name:'Vellum',description:'Warm paper and oxblood ink.',accent:'#8a3545',glass:.98,blur:0},
  glacier:{surface:'oklch(97% .014 218)',name:'Glacier',description:'Cool daylight and deep ocean blue.',accent:'#07628c',glass:.95,blur:4},
  oxide:{surface:'oklch(20.5% .012 50)',name:'Oxide',description:'Charcoal and a trace of copper.',accent:'#e4a683',glass:.96,blur:0},
  nocturne:{surface:'oklch(21% .025 295)',name:'Nocturne',description:'Smoked violet and pale silver.',accent:'#c0afe9',glass:.92,blur:8},
} as const;
type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;
const key = "machina.desktop.appearance.v1";
const clamp = (value: unknown, fallback: number, min: number, max: number) => typeof value === "number" && Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;
const font = (value: unknown, fallback: string) => typeof value === "string" && /^[\w ,"'.-]{1,150}$/.test(value) ? value : fallback;
export function normalizeAppearance(value: unknown): Appearance {
  const raw = (value && typeof value === "object" ? value : {}) as Partial<Appearance>;
  const preset: Preset = raw.preset && Object.hasOwn(PRESETS, raw.preset) ? raw.preset : "porcelain";
  const defaults = PRESETS[preset];
  return {
    preset, glass: clamp(raw.glass, defaults.glass, .2, 1), blur: clamp(raw.blur, defaults.blur, 0, 32),
    motion: ["full", "subtle", "off"].includes(String(raw.motion)) ? raw.motion as Motion : "subtle",
    background: ["art", "custom", "none"].includes(String(raw.background)) ? raw.background as Appearance["background"] : "none",
    accent: typeof raw.accent === "string" && /^#[\da-f]{6}$/i.test(raw.accent) ? raw.accent : defaults.accent,
    uiSize: clamp(raw.uiSize, 14, 12, 18), codeSize: clamp(raw.codeSize, 14, 11, 24),
    uiFont: font(raw.uiFont, "Segoe UI"), codeFont: font(raw.codeFont, "Cascadia Code"),
  };
}

export class AppearanceStore {
  private value: Appearance;
  private listeners = new Set<() => void>();
  constructor(private storage?: Storage) {
    let stored: unknown;
    try { stored = JSON.parse(storage?.getItem(key) ?? "null"); } catch {}
    if (stored && typeof stored === "object") {
      const prior = stored as Record<string, unknown>;
      if (prior.designVersion !== 2 && prior.preset === "afterimage" && prior.accent === "#ff784a") stored = { ...prior, accent: PRESETS.afterimage.accent };
    }
    this.value = normalizeAppearance(stored);
  }
  snapshot = (): Appearance => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  update(patch: Partial<Appearance>): void {
    this.value = normalizeAppearance({ ...this.value, ...patch });
    try { this.storage?.setItem(key, JSON.stringify({ ...this.value, designVersion: 2 })); } catch { /* The live choice remains usable if local persistence is unavailable. */ }
    for (const listener of this.listeners) listener();
  }
  select(preset: Preset): void { this.update({ ...PRESETS[preset], preset }); }
}

export async function wallpaperStorage(blob?: Blob | null): Promise<Blob | undefined> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("machina-desktop-appearance", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("wallpaper");
    open.onerror = () => reject(new Error("Wallpaper storage is unavailable."));
    open.onsuccess = () => {
      const database = open.result;
      const transaction = database.transaction("wallpaper", blob === undefined ? "readonly" : "readwrite");
      const store = transaction.objectStore("wallpaper");
      const request = blob === undefined ? store.get("custom") : blob === null ? store.delete("custom") : store.put(blob, "custom");
      transaction.oncomplete = () => { database.close(); resolve(blob === undefined ? request.result as Blob | undefined : blob ?? undefined); };
      transaction.onerror = transaction.onabort = () => { database.close(); reject(new Error("Could not save the custom wallpaper.")); };
    };
  });
}
