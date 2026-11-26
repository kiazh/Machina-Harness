// One card primitive for evidence and decisions. Copilot-style: a thin
// rounded frame, a title embedded in the top rule, a padded interior, and an
// optional raised surface. Every caller shares the same geometry so boxes
// never drift into hand-drawn ASCII.
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import { repairFill, theme } from "../theme.ts";

type FillName = "toolBg" | "assistantBg" | "userBg";

export interface CardOptions {
	/** Full card width including both border columns. */
	width: number;
	/** Plain title shown in the top rule. */
	title?: string;
	paddingX?: number;
	paddingY?: boolean;
	fill?: FillName;
	titleTone?: (text: string) => string;
	borderTone?: (text: string) => string;
}

const FILL = {
	toolBg: theme.toolBg,
	assistantBg: theme.assistantBg,
	userBg: theme.userBg,
} as const;

function pad(line: string, width: number): string {
	const clipped = truncateToWidth(line, width, "…");
	return clipped + " ".repeat(Math.max(0, width - visibleWidth(clipped)));
}

/**
 * Render a bounded card. Lines may carry ANSI; only visible width is measured.
 * Selected bars inside the card keep their own background because the fill is
 * repaired around nested resets.
 */
export function renderCard(lines: string[], options: CardOptions): string[] {
	const total = Math.max(4, Math.floor(options.width));
	const border = options.borderTone ?? theme.border;
	const tone = options.titleTone ?? theme.muted;
	const padX = Math.max(0, Math.min(Math.floor(options.paddingX ?? 2), Math.floor((total - 4) / 2)));
	const inner = Math.max(1, total - 2 - padX * 2);
	const fill = options.fill ? FILL[options.fill] : undefined;
	const paint = (body: string): string => fill ? repairFill(options.fill!, fill(body)) : body;
	const frame = (body: string): string => `${border("│")}${paint(body)}${border("│")}`;
	const blank = frame(`${" ".repeat(padX)}${" ".repeat(inner)}${" ".repeat(padX)}`);

	const title = options.title ? ` ${options.title} ` : "";
	const titleWidth = visibleWidth(title);
	const clippedTitle = titleWidth > total - 4 ? truncateToWidth(title, total - 4, "…") : title;
	const top =
		clippedTitle.length > 0
			? `${border("╭─")}${tone(clippedTitle)}${border(`${"─".repeat(Math.max(0, total - 3 - visibleWidth(clippedTitle)))}╮`)}`
			: `${border("╭")}${border(`${"─".repeat(total - 2)}╮`)}`;

	const out = [top];
	if (options.paddingY) out.push(blank);
	for (const line of lines) {
		const padded = `${" ".repeat(padX)}${pad(line, inner)}${" ".repeat(padX)}`;
		out.push(frame(padded));
	}
	if (options.paddingY) out.push(blank);
	out.push(`${border("╰")}${border(`${"─".repeat(total - 2)}╯`)}`);
	return out;
}
