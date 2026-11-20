// Single source for the keyboard-shortcut reference. The `?`/Ctrl+N,H picker
// (Picker rows, flat list with a value) and `/help` (context-grouped HelpKey
// rows) both render these rows, so a new binding is documented once.
//
// Order is the picker's: composer bindings, then workspace, then global; the
// help menu groups by `context` and preserves source order inside a group.

export type ShortcutContext = "Composer" | "Workspace" | "Global";

export interface ShortcutRow {
	/** Stable picker value (also the row identity in tests). */
	value: string;
	context: ShortcutContext;
	label: string;
	description?: string;
}

export const SHORTCUT_ROWS: readonly ShortcutRow[] = [
	{ value: "send", context: "Composer", label: "Enter — send / queue guidance", description: "While working, queues guidance for the next safe tool boundary; the current task stays in context; Escape interrupts" },
	{ value: "activity", context: "Composer", label: "Ctrl+N then A — activity", description: "Live status tab for agents, shells, tools, queues and harnesses · /activity; /activity list for actions" },
	{ value: "newline", context: "Composer", label: "Shift+Enter — new line" },
	{ value: "image", context: "Composer", label: "Drop an image / Ctrl+V — paste image", description: "PNG, JPEG, GIF or WebP · ⌘V where forwarded by your terminal" },
	{ value: "stop", context: "Composer", label: "Esc ×2 - stop reply", description: "First press arms the abort; second within 900ms stops. Ctrl+C stops immediately. Twice on empty input edits the last prompt" },
	{ value: "cancel", context: "Composer", label: "Ctrl+C — stop / clear / exit", description: "Stop a reply, clear a draft, or exit when empty" },
	{ value: "exit", context: "Composer", label: "Ctrl+D — exit", description: "When input is empty and idle" },
	{ value: "clear", context: "Composer", label: "Ctrl+L — clear chat", description: "When idle" },
	{ value: "complete", context: "Composer", label: "Tab — complete", description: "Commands and arguments" },
	{ value: "words", context: "Composer", label: "Ctrl+←/→ — move by word", description: "Alt+B / Alt+F also move by word when a terminal does not forward Ctrl+Arrow" },
	{ value: "subagents", context: "Composer", label: "↓ — subagent menu", description: "On empty input: switch between the main session and its subagents" },
	{ value: "tabs", context: "Composer", label: "Ctrl+N then J / K — next / previous agent", description: "With composer focus; the file editor keeps Ctrl+Shift+Arrow word selection" },
	{ value: "status", context: "Composer", label: "Ctrl+N then ← / → — status tab", description: "Full-page live view of agents, shells, tools/MCP calls, queues and external harnesses · /activity" },
	{ value: "quickopen", context: "Workspace", label: "Ctrl+P / Ctrl+N then O - quick open", description: "Fuzzy file search in the workspace; the prefixed form survives terminals that capture Ctrl+P" },
	{ value: "save", context: "Workspace", label: "Ctrl+S — save file", description: "Save the active editor buffer" },
	{ value: "closetab", context: "Workspace", label: "Ctrl+W / Ctrl+N then X - close tab", description: "Close the active editor tab (dirty: save/discard/cancel); the prefixed form survives terminal tab/window capture" },
	{ value: "explorer", context: "Workspace", label: "Ctrl+T / Ctrl+Shift+E / Ctrl+N then T - file tree", description: "Toggle the right-edge explorer; Ctrl+N then T works when a terminal captures Ctrl+T; → or enter expands folders (/explorer)" },
	{ value: "intel", context: "Workspace", label: "Ctrl+Space / Ctrl+N then I - inline completion", description: "Tab accepts · Esc dismisses · macOS input-source and Windows IME may steal Ctrl+Space · fallback /editor complete · Ctrl+N then G definition · R references · M rename · /problems lists diagnostics" },
	{ value: "focus", context: "Workspace", label: "Ctrl+N then N / P — next / previous pane", description: "Composer → editor → explorer → problems" },
	{ value: "chatmax", context: "Workspace", label: "Ctrl+N then C chat · E editor · S split", description: "Ctrl+←/→ remains word movement in text fields; edit shortcuts in Settings" },
	{ value: "wsroot", context: "Workspace", label: "/workspace open <path> — open a folder", description: "Also /workspace split|chat|editor|ratio|side|status|close" },
	{ value: "help", context: "Global", label: "? / Ctrl+N then H — shortcuts", description: "? on empty input; Ctrl+N then H with a draft" },
	{ value: "commands", context: "Global", label: "/help — all commands", description: "This menu; /growth shows learning details" },
	{ value: "learning", context: "Global", label: "□ / ▪ — activity and learning", description: "Pulses while working and briefly when learning is saved" },
];
