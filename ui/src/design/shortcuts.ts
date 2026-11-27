// Single source for the keyboard-shortcut reference. The `?`/Alt+H picker
// (Picker rows, flat list with a value) and `/help` (context-grouped HelpKey
// rows) both render these rows, so a new binding is documented once.
//
// Order is the picker's: composer bindings, then workspace, then global; the
// help menu groups by `context` and preserves source order inside a group.

export type ShortcutContext = "Composer" | "Workspace" | "Review & activity" | "Global";

export interface ShortcutRow {
	/** Stable picker value (also the row identity in tests). */
	value: string;
	context: ShortcutContext;
	label: string;
	description?: string;
}

export const SHORTCUT_ROWS: readonly ShortcutRow[] = [
	{ value: "send", context: "Composer", label: "Enter · send or queue guidance", description: "During a reply, guidance is delivered at the next safe tool boundary in the current chat" },
	{ value: "newline", context: "Composer", label: "Shift+Enter — new line" },
	{ value: "image", context: "Composer", label: "Alt+V · paste clipboard image", description: "Works without the terminal's Ctrl+V paste action. Ctrl+V / ⌘V also work when forwarded. Or drop an image file." },
	{ value: "stop", context: "Composer", label: "Esc ×2 - stop reply", description: "First press arms the abort; second within 900ms stops. Ctrl+C stops immediately. Twice on empty input edits the last prompt" },
	{ value: "cancel", context: "Composer", label: "Ctrl+C — stop / clear / exit", description: "Stop a reply, clear a draft, or exit when empty" },
	{ value: "exit", context: "Composer", label: "Ctrl+D — exit", description: "When input is empty and idle" },
	{ value: "clear", context: "Composer", label: "Ctrl+L — clear chat", description: "When idle" },
	{ value: "complete", context: "Composer", label: "Tab — complete", description: "Commands and arguments" },
	{ value: "words", context: "Composer", label: "Ctrl+←/→ — move by word", description: "Alt+B / Alt+F also move by word when a terminal does not forward Ctrl+Arrow" },
	{ value: "subagents", context: "Composer", label: "↓ — subagent menu", description: "On empty input: switch between the main session and its subagents" },
	{ value: "tabs", context: "Composer", label: "Alt+J / Alt+K · next / previous agent", description: "Alt+0 closes the current idle agent tab." },
	{ value: "status", context: "Global", label: "Alt+← / Alt+→ · Activity ← Chat → Changes", description: "← from Chat opens Activity; → opens Changes. Either arrow steps back toward Chat, and Esc always returns." },
	{ value: "quickopen", context: "Workspace", label: "Ctrl+P / Alt+O · quick open", description: "Fuzzy file search in the workspace. Also /quickopen." },
	{ value: "save", context: "Workspace", label: "Ctrl+S — save file", description: "Save the active editor buffer" },
	{ value: "closetab", context: "Workspace", label: "Ctrl+W / Alt+X · close file tab", description: "Close the editor tab. Unsaved changes still prompt to save, discard or cancel." },
	{ value: "explorer", context: "Workspace", label: "Alt+E / Ctrl+T / Ctrl+Shift+E · file tree", description: "Enter or Right expands folders. Also /explorer." },
	{ value: "intel", context: "Workspace", label: "Ctrl+Space / Alt+I · inline completion", description: "If your input method captures Ctrl+Space, use Alt+I or /editor complete. Alt+G definition, Alt+R references, Alt+M rename." },
	{ value: "focus", context: "Workspace", label: "Alt+Q / Alt+W · next / previous pane", description: "Composer → editor → explorer → problems" },
	{ value: "chatmax", context: "Workspace", label: "Alt+1 chat · Alt+2 editor · Alt+3 split", description: "Change the workspace layout without changing text-editing keys." },
	{ value: "attach", context: "Workspace", label: "Alt+T / Ctrl+Shift+A · attach selection", description: "Copy the active file selection into the chat draft. Does not send it." },
	{ value: "wsroot", context: "Workspace", label: "/workspace open <path> — open a folder", description: "Also /workspace split|chat|editor|ratio|side|status|close" },
	{ value: "activity", context: "Review & activity", label: "Activity · 1–7 filter, ↑↓ move, Space details", description: "Enter opens the selection. PgUp/PgDn pages expanded logs. Alt+← also opens Activity. Ctrl+C stops a running reply." },
	{ value: "reviewViews", context: "Review & activity", label: "Changes · ↑↓ prompts, 1/2/3 views, [ ] files", description: "Tab switches Files/Diff/Review; Space shows the prompt." },
	{ value: "reviewActions", context: "Review & activity", label: "Review · R rules, M model, N/P finding, Enter fix", description: "PgUp/PgDn reads more detail. X cancels a review. Enter sends the fix to this chat. C clears handled findings." },
	{ value: "approval", context: "Review & activity", label: "Approval · Enter confirm, Esc deny", description: "Allow once is selected first. ↑↓ changes the choice. PgUp/PgDn pages evidence." },
	{ value: "help", context: "Global", label: "? / Alt+H · shortcuts", description: "? on empty chat or in Activity and Changes. Alt+H also works with a draft." },
	{ value: "commands", context: "Global", label: "/help — all commands", description: "This menu; /growth shows learning details" },
	{ value: "learning", context: "Global", label: "□ / ▪ — activity and learning", description: "Pulses while working and briefly when learning is saved" },
];
