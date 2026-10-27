import assert from "node:assert/strict";
import { test } from "node:test";
import { KeysDialog, type KeysDialogChoice } from "../src/components/KeysDialog.ts";
import type { KeyRow } from "../src/features/keys.ts";

const rows: KeyRow[] = [
	{ key: "OPENAI_API_KEY", description: "OpenAI models", status: "not set", set: false, editable: true },
	{ key: "GROQ_API_KEY", description: "Groq models", status: "set", set: true, editable: true },
	{ key: "LOCAL_API_KEY", description: "Local endpoint", status: "no key", set: false, editable: false },
];

test("API key dialog searches and selects the filtered key", () => {
	const chosen: KeysDialogChoice[] = [];
	const dialog = new KeysDialog({ requestRender() {} } as never, rows, (choice) => chosen.push(choice), () => {});
	for (const char of "groq") dialog.handleInput(char);
	const display = dialog.render(68).join("\n");
	assert.match(display, /Search {2}groq/);
	assert.match(display, /GROQ_API_KEY/);
	assert.doesNotMatch(display, /OPENAI_API_KEY/);
	dialog.handleInput("\r");
	assert.deepEqual(chosen, [{ key: "GROQ_API_KEY", action: "set" }]);
});

test("keyless provider cannot be edited", () => {
	const chosen: KeysDialogChoice[] = [];
	const dialog = new KeysDialog({ requestRender() {} } as never, rows, (choice) => chosen.push(choice), () => {});
	for (const char of "local") dialog.handleInput(char);
	dialog.handleInput("\r");
	assert.deepEqual(chosen, []);
});

test("provider menu offers Add while the keys menu stays credential focused", () => {
	const chosen: KeysDialogChoice[] = [];
	const tui = { requestRender() {} } as never;
	const keys = new KeysDialog(tui, rows, (choice) => chosen.push(choice), () => {});
	assert.doesNotMatch(keys.render(68).join("\n"), /Add provider/);
	const providers = new KeysDialog(tui, rows, (choice) => chosen.push(choice), () => {}, "Providers");
	providers.handleInput("\r");
	assert.deepEqual(chosen, [{ action: "addProvider" }]);
});

test("existing custom provider opens edit and remove actions", () => {
	const chosen: KeysDialogChoice[] = [];
	const providerRows: KeyRow[] = [{ key: "acme", description: "https://api.example.com/v1", status: "custom", set: false, editable: true, clearable: true }];
	const dialog = new KeysDialog({ requestRender() {} } as never, providerRows, (choice) => chosen.push(choice), () => {}, "Providers");
	dialog.handleInput("\u001b[B");
	dialog.handleInput("\r");
	const detail = dialog.render(68).join("\n");
	assert.match(detail, /Edit URL \/ API key/);
	assert.match(detail, /Remove provider/);
	dialog.handleInput("\r");
	assert.deepEqual(chosen, [{ key: "acme", action: "editProvider" }]);
});

test("built-in OpenAI exposes credential editing without a provider switch", () => {
	const chosen: KeysDialogChoice[] = [];
	const providerRows: KeyRow[] = [{ key: "openai", description: "Built-in OpenAI", status: "built-in", set: false, editable: true, credentialKey: "OPENAI_API_KEY" }];
	const dialog = new KeysDialog({ requestRender() {} } as never, providerRows, (choice) => chosen.push(choice), () => {}, "Providers");
	dialog.handleInput("\u001b[B");
	dialog.handleInput("\r");
	const detail = dialog.render(68).join("\n");
	assert.match(detail, /Edit API key/);
	assert.match(detail, /Remove API key/);
	assert.doesNotMatch(detail, /Use provider/);
	dialog.handleInput("\r");
	assert.deepEqual(chosen, [{ key: "openai", action: "editCredential" }]);
});
