import { KeybindingsManager, matchesKey, parseKey, setKeybindings, TUI_KEYBINDINGS } from "@earendil-works/pi-tui";
import { SHORTCUT_ROWS, type ShortcutRow } from "./design/shortcuts.ts";

export const APP_BINDINGS = {
	shortcuts: { label: "Show shortcuts", key: "alt+h" },
	stop: { label: "Stop / clear / exit", key: "ctrl+c" },
	cancelTurn: { label: "Stop active reply (press Escape twice)", key: "escape" },
	pasteImage: { label: "Paste image", key: "ctrl+v" },
	pasteImageMac: { label: "Paste image (macOS)", key: "super+v" },
	pasteImageDirect: { label: "Read image from system clipboard", key: "alt+v" },
	agentMenu: { label: "Agent menu", key: "down" },
	exit: { label: "Exit empty chat", key: "ctrl+d" },
	clear: { label: "Clear chat", key: "ctrl+l" },
	agentNext: { label: "Next agent", key: "alt+j" },
	agentPrevious: { label: "Previous agent", key: "alt+k" },
	agentClose: { label: "Close idle agent tab", key: "alt+0" },
	activity: { label: "View activity and queued prompts", key: "alt+a" },
	showChat: { label: "Show Chat", key: "alt+down" },
	statusLeft: { label: "Show Activity (left of Chat)", key: "alt+left" },
	statusRight: { label: "Show Changes (right of Chat)", key: "alt+right" },
	quickOpen: { label: "Quick open", key: "ctrl+p" },
	explorer: { label: "Toggle file tree", key: "ctrl+t" },
	explorerAlt: { label: "Toggle file tree (fallback)", key: "alt+e" },
	explorerSafe: { label: "Toggle file tree (browser-safe)", key: "ctrl+shift+e" },
	closeTab: { label: "Close file tab", key: "ctrl+w" },
	focusNext: { label: "Next pane", key: "alt+q" },
	focusPrevious: { label: "Previous pane", key: "alt+w" },
	chatMaximize: { label: "Maximize chat", key: "alt+1" },
	editorMaximize: { label: "Maximize editor", key: "alt+2" },
	restoreSplit: { label: "Restore split", key: "alt+3" },
	attach: { label: "Attach selection", key: "ctrl+shift+a" },
	complete: { label: "Inline completion", key: "ctrl+space" },
	references: { label: "Find references", key: "alt+r" },
	definition: { label: "Go to definition", key: "alt+g" },
	rename: { label: "Rename symbol", key: "alt+m" },
	save: { label: "Save file", key: "ctrl+s" },
} as const;

export type AppBindingId = keyof typeof APP_BINDINGS;
export type BindingOverrides = Record<string, string>;
export type KeyPlatform = "macos" | "windows" | "linux";

/** Single-chord fallbacks when the terminal reserves a primary shortcut. */
const ALIASES: Partial<Record<AppBindingId, string[]>> = {
	complete: ["alt+i"],
	closeTab: ["alt+x"],
	quickOpen: ["alt+o"],
	attach: ["alt+t"],
};

// Old settings (including copied defaults) should not keep the old leader
// armed. Other custom sequences are kept.
const OLD_LEADER_KEYS: Partial<Record<AppBindingId, string>> = {
	shortcuts: "h", pasteImageDirect: "b", agentNext: "j", agentPrevious: "k", agentClose: "d",
	activity: "a", showChat: "1", statusLeft: "2", statusRight: "3", explorerAlt: "t",
	focusNext: "n", focusPrevious: "p", chatMaximize: "c", editorMaximize: "e",
	restoreSplit: "s", references: "r", definition: "g", rename: "m",
};

export function keyPlatform(platform: NodeJS.Platform = process.platform): KeyPlatform {
	if (platform === "darwin") return "macos";
	if (platform === "win32") return "windows";
	return "linux";
}

const MODIFIER_LABELS: Record<KeyPlatform, Record<string, string>> = {
	macos: { ctrl: "⌃", shift: "⇧", alt: "⌥", super: "⌘" },
	windows: { ctrl: "Ctrl", shift: "Shift", alt: "Alt", super: "Win" },
	linux: { ctrl: "Ctrl", shift: "Shift", alt: "Alt", super: "Super" },
};
const KEY_LABELS: Record<string, string> = {
	escape: "Esc",
	left: "←",
	right: "→",
	up: "↑",
	down: "↓",
	space: "Space",
	pageup: "PgUp",
	pagedown: "PgDn",
	home: "Home",
	end: "End",
	tab: "Tab",
	enter: "Enter",
	backspace: "Backspace",
	delete: "Delete",
};

/** Human label for a binding sequence; the stored key remains platform-neutral. */
export function platformKeyLabel(key: string, platform: KeyPlatform = keyPlatform()): string {
	const labels = MODIFIER_LABELS[platform];
	const formatChord = (chord: string): string => {
		const parts = chord.split("+");
		const modifiers = parts.slice(0, -1).map((part) => labels[part.toLowerCase()] ?? part);
		const base = parts.at(-1) ?? chord;
		const baseLabel = KEY_LABELS[base.toLowerCase()] ?? base.toUpperCase();
		return `${modifiers.join(platform === "macos" ? "" : "+")}${modifiers.length && platform !== "macos" ? "+" : ""}${baseLabel}`;
	};
	return key
		.split(" / ")
		.map((sequence) => sequence.split(/\s+/).filter(Boolean).map(formatChord).join(" then "))
		.join(" / ");
}

export function bindingAliases(id: AppBindingId): string[] {
	return ALIASES[id] ?? [];
}

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
		return Object.fromEntries(Object.entries(parsed).filter(([id, key]) => {
			if (!allowed.has(id) || typeof key !== "string" || !/^[a-z0-9+?_-]+(?: [a-z0-9+?_-]+)?$/i.test(key) || key.length > 64) return false;
			const old = OLD_LEADER_KEYS[id as AppBindingId];
			return !old || key.toLowerCase() !== `ctrl+n ${old}`;
		})) as BindingOverrides;
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
		if (key.includes(" ") ? data === key : matchesKey(data, key as Parameters<typeof matchesKey>[1])) return true;
		return this.overrides[id] === undefined
			&& bindingAliases(id).some((alias) => matchesKey(data, alias as Parameters<typeof matchesKey>[1]));
	}

	leaders(): string[] {
		const sequences = Object.entries(this.overrides)
			.filter(([id, key]) => Object.hasOwn(APP_BINDINGS, id) && key.includes(" "))
			.map(([, key]) => key);
		return [...new Set(sequences.map((key) => key.split(" ")[0]!))];
	}
}

export function capturedKey(data: string): string | undefined {
	const parsed = parseKey(data);
	if (!parsed || parsed === "escape") return undefined;
	return parsed;
}

export function bindingConflicts(
	overrides: BindingOverrides,
	id: string,
	key: string,
): string[] {
	return bindingRows(overrides)
		.filter((row) => {
			if (row.id === id) return false;
			if (row.key.split(" / ").includes(key)) return true;
			return overrides[row.id] === undefined
				&& bindingAliases(row.id as AppBindingId).includes(key);
		})
		.map((row) => row.label);
}

const SHORTCUT_BINDINGS: Record<string, string[]> = {
	send: ["tui.input.submit"], newline: ["tui.input.newLine"], image: ["pasteImageDirect", "pasteImage", "pasteImageMac"],
	stop: ["cancelTurn"], cancel: ["stop"], exit: ["exit"], clear: ["clear"],
	complete: ["tui.input.tab"], subagents: ["agentMenu"], tabs: ["agentNext", "agentPrevious", "agentClose"],
	quickopen: ["quickOpen"], save: ["save"], closetab: ["closeTab"], explorer: ["explorer", "explorerAlt", "explorerSafe"],
	intel: ["complete"], focus: ["focusNext", "focusPrevious"], chatmax: ["chatMaximize", "editorMaximize", "restoreSplit"],
	attach: ["attach"],
	help: ["shortcuts"],
	activity: ["activity"],
	status: ["showChat", "statusLeft", "statusRight"],
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
