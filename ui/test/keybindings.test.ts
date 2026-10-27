import assert from "node:assert/strict";
import { test } from "node:test";
import { APP_BINDINGS, AppKeybindings, bindingConflicts, parseBindingOverrides } from "../src/keybindings.ts";
import { KeybindingsDialog } from "../src/components/KeybindingsDialog.ts";
import { TUI_KEYBINDINGS, matchesKey, visibleWidth } from "@earendil-works/pi-tui";

test("saved bindings replace defaults and conflicts remain user selectable", () => {
	const bindings = new AppKeybindings();
	bindings.set(parseBindingOverrides('{"restoreSplit":"ctrl+n s","chatMaximize":"ctrl+n s"}'));
	assert.equal(bindings.matches("ctrl+n s", "restoreSplit"), true);
	assert.deepEqual(bindingConflicts(bindings.get(), "chatMaximize", "ctrl+n s"), ["Restore split"]);
	bindings.set({});
});

test("daily defaults use a plain control prefix and no function keys", () => {
	const defaults = Object.values(APP_BINDINGS).map((binding) => binding.key);
	assert.ok(defaults.every((key) => !/^f\d+$/.test(key)));
	assert.ok(defaults.includes("ctrl+n c"));
	assert.ok(defaults.includes("ctrl+n e"));
	assert.ok(defaults.includes("ctrl+n s"));
	assert.deepEqual(new AppKeybindings().leaders(), ["ctrl+n"]);
	assert.equal(matchesKey("\x0e", "ctrl+n"), true, "plain control byte works without enhanced keyboard protocols");
	assert.ok(Object.values(TUI_KEYBINDINGS).every((binding) =>
		!([binding.defaultKeys].flat() as string[]).includes("ctrl+n"),
	));
});

test("keybinding dialog captures a key and commits on Escape", () => {
	let saved: Record<string, string> = {};
	let closed = false;
	const dialog = new KeybindingsDialog({ requestRender() {}, terminal: { columns: 100, rows: 40 } } as never, {}, (value) => { saved = value; }, () => { closed = true; });
	dialog.handleInput("\r");
	dialog.handleInput("\x0e"); // Ctrl+N
	dialog.handleInput("h");
	assert.deepEqual(saved, {});
	dialog.handleInput("\x1b");
	assert.equal(saved["shortcuts"], "ctrl+n h");
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
