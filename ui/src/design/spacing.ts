// One transcript gutter; every block shares the same left edge, body column,
// and right clearance. Panels use the same edge, with their body one step in.
export const transcriptInset = (width: number): number =>
	width >= 40 ? 2 : width >= 20 ? 1 : 0;

export const bodyIndent = (width: number): number => transcriptInset(width) + 2;

export const panelInset = (width: number): number => transcriptInset(width);

/** Prose column inside the transcript gutter. The chat uses the pane width. */
export const chatColumnWidth = (width: number): number =>
	Math.max(1, width - transcriptInset(width) * 2);
