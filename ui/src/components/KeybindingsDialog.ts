import { Key, matchesKey, truncateToWidth, visibleWidth, type Component, type TuiMouseEvent, type TUI } from "@earendil-works/pi-tui";
import { bindingConflicts, bindingRows, capturedKey, type BindingOverrides } from "../keybindings.ts";
import { theme } from "../theme.ts";

const VISIBLE_ROWS = 12;

export class KeybindingsDialog implements Component {
	invalidate(): void {}
	private selected = 0;
	private offset = 0;
	private capture = false;
	private pending: string | undefined;
	private warning = "";
	private overrides: BindingOverrides;
	readonly onClose: () => void;
	readonly onSave: (overrides: BindingOverrides) => void;
	private readonly tui: TUI;

	constructor(tui: TUI, overrides: BindingOverrides, onSave: (overrides: BindingOverrides) => void, onClose: () => void) {
		this.tui = tui;
		this.overrides = { ...overrides };
		this.onSave = onSave;
		this.onClose = onClose;
	}

	private rows() { return bindingRows(this.overrides); }

	private choose(index: number): void {
		this.selected = Math.max(0, Math.min(index, this.rows().length - 1));
		if (this.selected < this.offset) this.offset = this.selected;
		if (this.selected >= this.offset + VISIBLE_ROWS) this.offset = this.selected - VISIBLE_ROWS + 1;
		this.tui.requestRender();
	}

	handleInput(data: string): void {
		if (this.capture) {
			if (matchesKey(data, Key.escape)) {
				if (this.pending) {
					const id = this.rows()[this.selected]!.id;
					this.warning = bindingConflicts(this.overrides, id, this.pending).join(", ");
					this.overrides[id] = this.pending;
					this.onSave({ ...this.overrides });
				}
				this.capture = false;
				this.pending = undefined;
			} else {
				const key = capturedKey(data);
				if (key) this.pending = this.pending?.startsWith("ctrl+") && !this.pending.includes(" ") && /^[a-z]$/.test(key)
					? `${this.pending} ${key}` : key;
			}
			this.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.escape)) { this.onClose(); return; }
		if (matchesKey(data, Key.up)) this.choose(this.selected - 1);
		else if (matchesKey(data, Key.down)) this.choose(this.selected + 1);
		else if (matchesKey(data, Key.pageUp)) this.choose(this.selected - VISIBLE_ROWS);
		else if (matchesKey(data, Key.pageDown)) this.choose(this.selected + VISIBLE_ROWS);
		else if (matchesKey(data, Key.enter)) { this.capture = true; this.pending = undefined; this.warning = ""; this.tui.requestRender(); }
		else if (matchesKey(data, Key.backspace) || matchesKey(data, Key.delete)) {
			delete this.overrides[this.rows()[this.selected]!.id];
			this.warning = "Default restored";
			this.onSave({ ...this.overrides });
			this.tui.requestRender();
		}
	}

	handleMouse(event: TuiMouseEvent): ReturnType<NonNullable<Component["handleMouse"]>> {
		if (event.type !== "click" || event.button !== "left") return undefined;
		const index = this.offset + event.y - 3;
		if (index >= 0 && index < this.rows().length && index < this.offset + VISIBLE_ROWS) {
			this.choose(index);
			this.capture = true;
			this.pending = undefined;
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
		const rows = this.rows();
		const out = [
			`╭${"─".repeat(size - 2)}╮`,
			line(theme.accent("Keyboard shortcuts")),
			line(theme.muted("Action".padEnd(Math.max(12, inside - 23)) + "Key")),
		];
		for (let i = this.offset; i < Math.min(rows.length, this.offset + VISIBLE_ROWS); i++) {
			const row = rows[i]!;
			const labelWidth = Math.max(12, inside - 23);
			const key = i === this.selected && this.capture ? `[ ${this.pending ?? "press a key"} ]` : `[ ${row.key} ]`;
			const value = `${i === this.selected ? "›" : " "} ${truncateToWidth(row.label, labelWidth - 2).padEnd(labelWidth - 2)} ${key}`;
			out.push(line(i === this.selected ? theme.accent(value) : value));
		}
		while (out.length < VISIBLE_ROWS + 3) out.push(line(""));
		out.push(line(this.warning ? theme.warning(`Also used by: ${this.warning}`) : this.capture ? "Key or Ctrl+key then letter · Esc saves" : "↑↓ choose · click/Enter edit · Backspace reset · Esc close"));
		out.push(`╰${"─".repeat(size - 2)}╯`);
		return out;
	}
}
