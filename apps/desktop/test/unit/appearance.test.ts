import { expect, it } from "vitest";
import { AppearanceStore, normalizeAppearance } from "../../src/state/appearance.ts";

it("restores only supported appearance values and clamps rendering budgets", () => {
  expect(normalizeAppearance({ preset: "remote-code", glass: -1, blur: 900, motion: "unknown", uiSize: 90, background: "https://untrusted", accent: "url(evil)" })).toMatchObject({ preset: "porcelain", glass: .2, blur: 32, motion: "subtle", uiSize: 18, background: "art", accent: "#2452ff" });
});
it("persists a complete preset while keeping custom wallpaper and code size", () => {
  const storage = new Map<string, string>();
  const store = new AppearanceStore({ getItem: key => storage.get(key) ?? null, setItem: (key, value) => { storage.set(key, value); } });
  store.update({ background: "custom", codeSize: 17, motion: "off" });
  store.select("volt");
  expect(store.snapshot()).toMatchObject({ preset: "volt", background: "custom", codeSize: 17, motion: "off", accent: "#d5f900" });
  const restored = new AppearanceStore({ getItem: key => storage.get(key) ?? null, setItem() {} });
  expect(restored.snapshot()).toEqual(store.snapshot());
});

it("refreshes the old factory accent once while retaining custom choices", () => {
  let raw = JSON.stringify({ preset: "afterimage", accent: "#ff784a", background: "custom", codeSize: 18 });
  const storage = { getItem: () => raw, setItem: (_key: string, value: string) => { raw = value; } };
  const store = new AppearanceStore(storage);
  expect(store.snapshot()).toMatchObject({ accent: "#8eb8ff", background: "custom", codeSize: 18 });
  store.update({ accent: "#ff784a" });
  expect(new AppearanceStore(storage).snapshot().accent).toBe("#ff784a");
});
