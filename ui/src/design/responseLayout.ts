// Geometry shape shared with the transcript. Assistant prose is unframed now,
// so measurement and frame painting were removed; only the plan/geometry type
// survives because `AssistantTurn` and the response-layout tests use it.

export type ResponseWidthClass = "compact" | "medium" | "full";

export interface ResponseGeometry {
	/** Allocated chat-pane columns this decision was computed against. */
	paneWidth: number;
	widthClass: ResponseWidthClass;
	framed: boolean;
	/** Outside gutter before the frame. */
	indent: number;
	/** Frame span in cells (content + border + padding), never above the pane. */
	span: number;
	/** Text cells inside the frame. */
	contentWidth: number;
	/** Natural frame height once rendered at `contentWidth` (borders included). */
	frameRows: number;
}
