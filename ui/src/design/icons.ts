// Central icon/marker registry. No color here; theme.ts applies styling.
// Every state keeps a text marker so meaning survives monochrome skins.

export const ICONS = {
	userPrefix: "›",
	assistantPrefix: "●",
	assistantIdle: "●",
	toolRunning: "●",
	toolRunningDouble: "●●",
	toolActiveFrames: ["·", "•", "●", "•"] as const,
	/**
	 * Working pulse: the snowflake cycles its four six-spoked forms
	 * (`✶` pointed → `✼` open centre → `✽` heavy → `✻` solid), so it reads
	 * as a flake turning rather than a shape swapping in place.
	 */
	workingFrames: ["✶", "✼", "✽", "✻"] as const,
	success: "✔",
	successAlt: "✓",
	error: "✖",
	errorAlt: "✗",
	interrupted: "○",
	unknown: "?",
	noticeSuccess: "✓",
	noticeWarning: "!",
	noticeError: "✗",
	idleSquare: "□",
	growthFrames: ["▪"] as const,
	growthStatic: "▪",
	composerBar: "│",
	selectedMarker: "❯",
	selectedMarkerAscii: ">",
	separatorDot: " · ",
	ellipsisMore: "…",
	diffHunk: "@@",
	secretMask: "•",
} as const;

/** Tool header markers by outcome (section 6.5). */
export const TOOL_MARKERS = {
	queued: "…",
	running: ICONS.toolRunning,
	success: ICONS.success,
	error: ICONS.error,
	interrupted: ICONS.interrupted,
	unknown: ICONS.unknown,
} as const;

