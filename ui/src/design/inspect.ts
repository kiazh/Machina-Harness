import { truncateToWidth } from "@earendil-works/pi-tui";
import { surfaceLine, theme } from "../theme.ts";

/** Keep the list and evidence readable; stack them when either would starve. */
export function inspectLayout(width: number, height: number) {
	const inset = width >= 40 ? 2 : 0;
	const bodyWidth = Math.max(1, width - inset * 2);
	const split = bodyWidth >= 86 && height >= 7;
	const listWidth = split ? Math.max(28, Math.min(44, Math.floor(bodyWidth / 3))) : bodyWidth;
	return {
		split,
		inset,
		listWidth,
		detailWidth: split ? bodyWidth - listWidth - 3 : bodyWidth,
		listRows: split ? height : Math.min(height <= 8 ? 1 : 3, Math.max(1, height - 4)),
		combine(list = "", detail = ""): string {
			const prefix = " ".repeat(inset);
			if (!split) return prefix + (list || detail);
			return `${prefix}${surfaceLine(truncateToWidth(list, listWidth, "…", true), listWidth)} ${theme.border("│")} ${detail}`;
		},
	};
}

export function selectionWindow(index: number, size: number, length: number): number {
	return Math.max(0, Math.min(length - size, index - Math.floor(size / 2)));
}
