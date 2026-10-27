import { Key, matchesKey, truncateToWidth, visibleWidth, type Component, type TUI, type TuiMouseEvent } from "@earendil-works/pi-tui";
import type { KeyRow } from "../features/keys.ts";
import { theme } from "../theme.ts";

export type KeysDialogChoice = { key: string; action: "set" | "clear" | "editProvider" | "removeProvider" | "editCredential" | "removeCredential" } | { action: "addProvider" };

/** Centered, searchable key list. Secret values are never held by this component. */
export class KeysDialog implements Component {
	private readonly tui: TUI;
	private readonly rows: KeyRow[];
	private readonly onChoose: (choice: KeysDialogChoice) => void;
	private readonly onClose: () => void;
	private readonly title: string;
	private query = "";
	private selected = 0;
	private offset = 0;
	private readonly visibleRows = 12;
	private detail: KeyRow | undefined;
	private detailSelection = 0;
	constructor(tui: TUI, rows: KeyRow[], onChoose: (choice: KeysDialogChoice) => void, onClose: () => void, title = "API keys", initialQuery = "") {
		this.tui = tui;
		this.rows = rows;
		this.onChoose = onChoose;
		this.onClose = onClose;
		this.title = title;
		this.query = initialQuery;
	}

	invalidate(): void {}
	private filtered(): KeyRow[] {
		const query = this.query.toLowerCase().trim();
		const rows = this.title === "Providers"
			? [{ key: "+ Add provider", description: "Add an OpenAI-compatible URL and optional API key", status: "setup", set: false, editable: true }, ...this.rows]
			: this.rows;
		return query ? rows.filter((row) => `${row.key} ${row.description} ${row.status}`.toLowerCase().includes(query)) : rows;
	}
	private move(index: number): void {
		const count = this.filtered().length;
		this.selected = Math.max(0, Math.min(index, count - 1));
		if (this.selected < this.offset) this.offset = this.selected;
		if (this.selected >= this.offset + this.visibleRows) this.offset = this.selected - this.visibleRows + 1;
		this.tui.requestRender();
	}
	private choose(action: KeysDialogChoice["action"]): void {
		const row = this.filtered()[this.selected];
		if (this.title === "Providers" && row?.key !== "+ Add provider") {
			if (!row) return;
			this.detail = row;
			this.detailSelection = 0;
			this.tui.requestRender();
			return;
		}
		if (row?.key === "+ Add provider") {
			if (action === "set") this.onChoose({ action: "addProvider" });
		} else if (row?.editable && (action !== "clear" || row.clearable !== false)) this.onChoose({ key: row.key, action });
	}
	handleInput(data: string): void {
		if (this.detail) {
			const actions = this.detailActions();
			if (matchesKey(data, Key.escape)) { this.detail = undefined; this.tui.requestRender(); return; }
			if (matchesKey(data, Key.up)) { this.detailSelection = Math.max(0, this.detailSelection - 1); this.tui.requestRender(); return; }
			if (matchesKey(data, Key.down)) { this.detailSelection = Math.min(actions.length - 1, this.detailSelection + 1); this.tui.requestRender(); return; }
			if (matchesKey(data, Key.enter)) {
				const action = actions[this.detailSelection]?.action;
				if (action === "back") { this.detail = undefined; this.tui.requestRender(); }
				else if (action) this.onChoose({ key: this.detail.key, action });
			}
			return;
		}
		if (matchesKey(data, Key.escape)) { this.onClose(); return; }
		if (matchesKey(data, Key.up)) { this.move(this.selected - 1); return; }
		if (matchesKey(data, Key.down)) { this.move(this.selected + 1); return; }
		if (matchesKey(data, Key.pageUp)) { this.move(this.selected - this.visibleRows); return; }
		if (matchesKey(data, Key.pageDown)) { this.move(this.selected + this.visibleRows); return; }
		if (matchesKey(data, Key.enter)) { this.choose("set"); return; }
		if (matchesKey(data, Key.delete)) { this.choose("clear"); return; }
		if (matchesKey(data, Key.backspace)) { this.query = this.query.slice(0, -1); this.move(0); return; }
		if (data.length === 1 && data >= " " && data !== "\u007f") {
			this.query += data;
			this.move(0);
		}
	}
	handleMouse(event: TuiMouseEvent): ReturnType<NonNullable<Component["handleMouse"]>> {
		if (event.type !== "click" || event.button !== "left") return undefined;
		if (this.detail) {
			const action = this.detailActions()[event.y - 4]?.action;
			if (action === "back") { this.detail = undefined; this.tui.requestRender(); }
			else if (action) this.onChoose({ key: this.detail.key, action });
			return { handled: true, focus: true, render: true };
		}
		const index = this.offset + event.y - 4;
		if (index >= 0 && index < this.filtered().length && index < this.offset + this.visibleRows) {
			this.move(index);
			this.choose("set");
			return { handled: true, focus: true, render: true };
		}
		return undefined;
	}
	render(width: number): string[] {
		const size = Math.max(10, Math.min(width, 68));
		const inside = size - 4;
		const line = (value: string) => {
			const content = truncateToWidth(value, inside);
			return `│ ${content}${" ".repeat(Math.max(0, inside - visibleWidth(content)))} │`;
		};
		if (this.detail) {
			const out = [
				`╭${"─".repeat(size - 2)}╮`,
				line(theme.accent(this.detail.key)),
				line(theme.muted(this.detail.description)),
				line(""),
			];
			for (const [index, action] of this.detailActions().entries()) {
				const value = `${index === this.detailSelection ? "›" : " "} ${action.label}`;
				out.push(line(index === this.detailSelection ? theme.accent(value) : value));
			}
			while (out.length < this.visibleRows + 4) out.push(line(""));
			out.push(line(theme.muted("↑↓ choose · Enter select · Esc back")));
			out.push(`╰${"─".repeat(size - 2)}╯`);
			return out;
		}
		const rows = this.filtered();
		const out = [
			`╭${"─".repeat(size - 2)}╮`,
			line(theme.accent(this.title)),
			line(`Search  ${this.query || theme.muted("Type a provider or key name…")}`),
			line(theme.muted("Key".padEnd(Math.max(12, inside - 19)) + "Status")),
		];
		for (let i = this.offset; i < Math.min(rows.length, this.offset + this.visibleRows); i++) {
			const row = rows[i]!;
			const labelWidth = Math.max(12, inside - 19);
			const value = `${i === this.selected ? "›" : " "} ${truncateToWidth(row.key, labelWidth - 2).padEnd(labelWidth - 2)} ${row.status}`;
			out.push(line(i === this.selected ? theme.accent(value) : row.editable ? value : theme.muted(value)));
		}
		if (!rows.length) out.push(line(theme.muted("No matching keys")));
		while (out.length < this.visibleRows + 4) out.push(line(""));
		out.push(line(theme.muted(this.title === "Providers"
			? "Type to search · ↑↓ choose · Enter options · Esc close"
			: "Type to search · ↑↓ choose · Enter set · Delete clear · Esc close")));
		out.push(`╰${"─".repeat(size - 2)}╯`);
		return out;
	}
	private detailActions(): Array<{ label: string; action: "editProvider" | "removeProvider" | "editCredential" | "removeCredential" | "back" }> {
		return [
			...(this.detail?.clearable ? [
				{ label: "Edit URL / API key", action: "editProvider" as const },
				{ label: "Remove provider", action: "removeProvider" as const },
			] : []),
			...(this.detail?.credentialKey ? [
				{ label: "Edit API key", action: "editCredential" as const },
				{ label: "Remove API key", action: "removeCredential" as const },
			] : []),
			{ label: "Back", action: "back" },
		];
	}
}
