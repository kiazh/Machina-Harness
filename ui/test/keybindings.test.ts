import assert from "node:assert/strict";
import { test } from "node:test";
import { APP_BINDINGS, AppKeybindings, bindingConflicts, parseBindingOverrides, platformKeyLabel } from "../src/keybindings.ts";
import { KeybindingsDialog } from "../src/components/KeybindingsDialog.ts";
import { TUI_KEYBINDINGS, visibleWidth } from "@earendil-works/pi-tui";

test("saved bindings replace defaults and conflicts remain user selectable", () => {
	const bindings = new AppKeybindings();
	bindings.set(parseBindingOverrides('{"restoreSplit":"ctrl+k u","chatMaximize":"ctrl+k u"}'));
	assert.equal(bindings.matches("ctrl+k u", "restoreSplit"), true);
	assert.deepEqual(bindingConflicts(bindings.get(), "chatMaximize", "ctrl+k u"), ["Restore split"]);
	bindings.set({});
});

test("daily defaults dispatch one keypress without any leader or toolkit collisions", () => {
	const defaults: string[] = Object.values(APP_BINDINGS).map((binding) => binding.key);
	assert.ok(defaults.every((key) => !/^f\d+$|\s/.test(key) && key !== "ctrl+n"));
	assert.equal(new Set(defaults).size, defaults.length);
	const bindings = new AppKeybindings();
	assert.deepEqual(bindings.leaders(), []);
	assert.equal(bindings.matches("\x1b[D", "statusLeft"), false);
	assert.equal(bindings.matches("\x1b[C", "statusRight"), false);
	assert.equal(bindings.matches("\x1b[1;3D", "statusLeft"), true, "Alt+Left works in legacy VT");
	assert.equal(bindings.matches("\x1b[1;3:1D", "statusLeft"), true, "Alt+Left works in Kitty CSI-u");
	const toolkitKeys = new Set<string>(Object.values(TUI_KEYBINDINGS).flatMap(binding => [binding.defaultKeys].flat()));
	assert.ok(!toolkitKeys.has("ctrl+n"));
	// Alt+Left/Right belong to the screen strip. Word motion keeps its
	// Ctrl+Arrow and Alt+B/F fallbacks, so nothing is lost.
	const intentional = new Set(["alt+left", "alt+right"]);
	assert.ok(defaults.filter(key => key.startsWith("alt+")).every(key => intentional.has(key) || !toolkitKeys.has(key)));
	assert.ok(toolkitKeys.has("ctrl+left"), "word motion still has a fallback");
	assert.ok(toolkitKeys.has("alt+b"));
});

test("single-key fallbacks cover terminal and input-method reserved keys", () => {
	const bindings = new AppKeybindings();
	assert.equal(bindings.matches("\x1bi", "complete"), true);
	assert.equal(bindings.matches("\x1bx", "closeTab"), true);
	assert.equal(bindings.matches("\x1bo", "quickOpen"), true);
	assert.equal(bindings.matches("\x1bt", "attach"), true);
	assert.equal(platformKeyLabel("ctrl+shift+left", "macos"), "⌃⇧←");
	assert.equal(platformKeyLabel("ctrl+shift+left", "windows"), "Ctrl+Shift+←");
	assert.equal(platformKeyLabel("alt+h", "macos"), "⌥H");
	assert.equal(platformKeyLabel("up / ctrl+b", "windows"), "↑ / Ctrl+B");
});

test("a user override removes fallback aliases for that action", () => {
	const bindings = new AppKeybindings();
	bindings.set({ complete: "alt+z" });
	assert.equal(bindings.matches("\x1bi", "complete"), false);
	assert.equal(bindings.matches("\x1bz", "complete"), true);
	assert.deepEqual(bindingConflicts({}, "save", "alt+i"), ["Inline completion"]);
	bindings.set({});
});

test("copied leader defaults migrate but deliberately customized sequences survive", () => {
	const overrides = parseBindingOverrides('{"statusLeft":"ctrl+n 2","shortcuts":"ctrl+n h","save":"ctrl+k u"}');
	assert.deepEqual(overrides, { save: "ctrl+k u" });
	const bindings = new AppKeybindings();
	bindings.set(overrides);
	assert.deepEqual(bindings.leaders(), ["ctrl+k"]);
	assert.equal(bindings.matches("\x1bh", "shortcuts"), true);
});

test("keybinding dialog captures a key and commits on Escape", () => {
	let saved: Record<string, string> = {};
	let closed = false;
	const dialog = new KeybindingsDialog({ requestRender() {}, terminal: { columns: 100, rows: 40 } } as never, {}, (value) => { saved = value; }, () => { closed = true; });
	dialog.handleInput("\r");
	dialog.handleInput("\x1bh"); // Alt+H
	assert.deepEqual(saved, {});
	dialog.handleInput("\x1b");
	assert.equal(saved["shortcuts"], "alt+h");
	dialog.handleInput("\x1b");
	assert.equal(closed, true);
});

test("overlapping keys warn and remain saved; Backspace restores a default", () => {
	const saves: Array<Record<string, string>> = [];
	const dialog = new KeybindingsDialog({ requestRender() {}, terminal: { columns: 100, rows: 40 } } as never, {}, (value) => { saves.push(value); }, () => {});
	dialog.handleInput("\r");
	dialog.handleInput("\x13"); // Ctrl+S already saves the active file
	dialog.handleInput("\x1b");
	assert.equal(saves.at(-1)?.shortcuts, "ctrl+s");
	assert.match(dialog.render(68).join("\n"), /Also used by: Save file/);
	assert.ok(dialog.render(68).every((line) => visibleWidth(line) === 68));
	dialog.handleInput("\x7f");
	assert.equal(saves.at(-1)?.shortcuts, undefined);
});
