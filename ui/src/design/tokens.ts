// Semantic design tokens: spacing, density, numeric bounds, palette types.
// No imports from App, features, or mutable conversation state.

/** Canonical semantic color slots (string hex values, not literal-locked). */
export type SemanticSlotName =
	| "background"
	| "surface"
	| "raisedSurface"
	| "border"
	| "text"
	| "muted"
	| "faint"
	| "accent"
	| "accentSoft"
	| "secondary"
	| "secondarySoft"
	| "success"
	| "warning"
	| "danger"
	| "selection";

export type SemanticPalette = Record<SemanticSlotName, string>;

export interface Spacing {
	blockGapRows: number;
	chromeGapRows: number;
	composerPaddingX: number;
	menuPaddingX: number;
}

export const SPACING_NORMAL: Spacing = {
	blockGapRows: 1,
	chromeGapRows: 1,
	composerPaddingX: 1,
	menuPaddingX: 0,
};

export const SPACING_COMPACT: Spacing = {
	blockGapRows: 0,
	chromeGapRows: 0,
	composerPaddingX: 1,
	menuPaddingX: 0,
};

/** One blank terminal row between major transcript blocks (normal density). */
export const BLOCK_GAP: string[] = [""];

/** Central numeric bounds (sections 6.3/6.5/6.6). */
export const LIMITS = {
	previewLinesDefault: 4,
	previewLinesCompact: 2,
	toolPreviewLinesLegacy: 6,
	outputRetainedCount: 8,
	outputMaxBytesPer: 128 * 1024,
	outputMaxBytesTotal: 1024 * 1024,
	composerMaxRows: 8,
	composerMinRows: 1,
	pickerVisibleRowsDefault: 10,
	formMaxLengthPersona: 2000,
	maxCellWidth: 48,
} as const;

/**
 * Ink & Sage: the startup palette. Warm bone text on a green-black canvas,
 * one soft sage accent, and borders that recede instead of framing every row.
 */
export const CODEX_SEMANTIC_START: SemanticPalette = {
	background: "#0f1211",
	surface: "#181d1a",
	raisedSurface: "#1b211e",
	border: "#526058",
	text: "#e8e4da",
	muted: "#9aa49c",
	faint: "#727d75",
	accent: "#a7d3b5",
	accentSoft: "#2c3a31",
	secondary: "#a5b0cb",
	secondarySoft: "#2c323d",
	success: "#9fcf9a",
	warning: "#e4b77a",
	danger: "#e08a80",
	selection: "#24312a",
};
