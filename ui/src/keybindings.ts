import { KeybindingsManager, matchesKey, parseKey, setKeybindings, TUI_KEYBINDINGS } from "@earendil-works/pi-tui";
import { SHORTCUT_ROWS, type ShortcutRow } from "./design/shortcuts.ts";

export const APP_BINDINGS = {
	shortcuts: { label: "Show shortcuts", key: "ctrl+n h" },
	stop: { label: "Stop / clear / exit", key: "ctrl+c" },
	cancelTurn: { label: "Stop active reply", key: "escape" },
	pasteImage: { label: "Paste image", key: "ctrl+v" },
	pasteImageMac: { label: "Paste image (macOS)", key: "super+v" },
	agentMenu: { label: "Agent menu", key: "down" },
	exit: { label: "Exit empty chat", key: "ctrl+d" },
	clear: { label: "Clear chat", key: "ctrl+l" },
	agentNext: { label: "Next agent", key: "ctrl+n j" },
	agentPrevious: { label: "Previous agent", key: "ctrl+n k" },
	agentClose: { label: "Close idle agent tab", key: "ctrl+n d" },
	quickOpen: { label: "Quick open", key: "ctrl+p" },
	explorer: { label: "Toggle file tree", key: "ctrl+t" },
	closeTab: { label: "Close file tab", key: "ctrl+w" },
	focusNext: { label: "Next pane", key: "ctrl+n n" },
	focusPrevious: { label: "Previous pane", key: "ctrl+n p" },
	chatMaximize: { label: "Maximize chat", key: "ctrl+n c" },
	editorMaximize: { label: "Maximize editor", key: "ctrl+n e" },
	restoreSplit: { label: "Restore split", key: "ctrl+n s" },
	attach: { label: "Attach selection", key: "ctrl+shift+a" },
	complete: { label: "Inline completion", key: "ctrl+space" },
	references: { label: "Find references", key: "ctrl+n r" },
	definition: { label: "Go to definition", key: "ctrl+n g" },
	rename: { label: "Rename symbol", key: "ctrl+n m" },
	save: { label: "Save file", key: "ctrl+s" },
} as const;

export type AppBindingId = keyof typeof APP_BINDINGS;
export type BindingOverrides = Record<string, string>;

export function bindingRows(overrides: BindingOverrides): Array<{ id: string; label: string; key: string }> {
	return [
		...Object.entries(APP_BINDINGS).map(([id, item]) => ({ id, label: item.label, key: overrides[id] ?? item.key })),
		...Object.entries(TUI_KEYBINDINGS).map(([id, item]) => ({
			id,
			label: item.description ?? id,
			key: overrides[id] ?? (Array.isArray(item.defaultKeys) ? item.defaultKeys.join(" / ") : item.defaultKeys),
		})),
	];
}

export function parseBindingOverrides(raw: unknown): BindingOverrides {
	if (typeof raw !== "string" || !raw) return {};
	try {
		const parsed: unknown = JSON.parse(raw);
		if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
		const allowed = new Set(bindingRows({}).map((row) => row.id));
		return Object.fromEntries(Object.entries(parsed).filter(([id, key]) =>
			allowed.has(id) && typeof key === "string" && key.length <= 64 && /^[a-z0-9+?_-]+(?: [a-z0-9+?_-]+)?$/i.test(key),
		)) as BindingOverrides;
	} catch {
		return {};
	}
}

export class AppKeybindings {
	private overrides: BindingOverrides = {};

	set(overrides: BindingOverrides): void {
		this.overrides = { ...overrides };
		const tuiOverrides = Object.fromEntries(Object.entries(overrides).filter(([id]) => id.startsWith("tui.")));
		const definitions = Object.fromEntries(Object.entries(TUI_KEYBINDINGS).map(([id, item]) => [id, item]));
		setKeybindings(new KeybindingsManager(definitions, tuiOverrides as ConstructorParameters<typeof KeybindingsManager>[1]));
	}

	get(): BindingOverrides { return { ...this.overrides }; }

	matches(data: string, id: AppBindingId): boolean {
		const key = this.overrides[id] ?? APP_BINDINGS[id].key;
		return key.includes(" ") ? data === key : matchesKey(data, key as Parameters<typeof matchesKey>[1]);
	}

	leaders(): string[] {
		const sequences = Object.keys(APP_BINDINGS)
			.map((id) => this.overrides[id] ?? APP_BINDINGS[id as AppBindingId].key)
			.filter((key) => key.includes(" "));
		return [...new Set(sequences.map((key) => key.split(" ")[0]!))];
	}
}

export function capturedKey(data: string): string | undefined {
	const parsed = parseKey(data);
	if (!parsed || parsed === "escape") return undefined;
	return parsed;
}

export function bindingConflicts(overrides: BindingOverrides, id: string, key: string): string[] {
	return bindingRows(overrides).filter((row) => row.id !== id && row.key.split(" / ").includes(key)).map((row) => row.label);
}

const SHORTCUT_BINDINGS: Record<string, string[]> = {
	send: ["tui.input.submit"], newline: ["tui.input.newLine"], image: ["pasteImage", "pasteImageMac"],
	stop: ["cancelTurn"], cancel: ["stop"], exit: ["exit"], clear: ["clear"],
	complete: ["tui.input.tab"], subagents: ["agentMenu"], tabs: ["agentNext", "agentPrevious", "agentClose"],
	quickopen: ["quickOpen"], save: ["save"], closetab: ["closeTab"], explorer: ["explorer"],
	intel: ["complete"], focus: ["focusNext", "focusPrevious"], chatmax: ["chatMaximize", "editorMaximize", "restoreSplit"],
	help: ["shortcuts"],
};

export function effectiveShortcutRows(overrides: BindingOverrides): ShortcutRow[] {
	const effective = new Map(bindingRows(overrides).map((row) => [row.id, row.key]));
	return SHORTCUT_ROWS.map((row) => {
		const ids = SHORTCUT_BINDINGS[row.value] ?? [];
		if (!ids.some((id) => id in overrides)) return { ...row };
		const keys = ids.map((id) => effective.get(id)).filter(Boolean).join(" / ");
		return { ...row, description: `Current: ${keys}${row.description ? ` · ${row.description}` : ""}` };
	});
}
