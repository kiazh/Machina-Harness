// Chrome labels are single-line: hostile titles, models, hints or completion
// labels must never add a row, leak a control byte, or split an escape.
import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { NavBar } from "../src/components/NavBar.ts";
import { Footer } from "../src/components/Footer.ts";
import { AnimationCoordinator } from "../src/design/motion.ts";
import { CompletionPopup } from "../src/components/IntelligencePopups.ts";
import { formatProblemRow } from "../src/components/ProblemsPane.ts";
import { StatusPage } from "../src/components/StatusPage.ts";
import { ChangesPage } from "../src/components/ChangesPage.ts";

const DIRTY = "\x1b]0;owned\x07\nsecond line\r\x1b[2J\x00tail";

function assertOneLine(row: string, width: number): void {
	const plain = stripTerminalSequences(row);
	// eslint-disable-next-line no-control-regex -- checking for leaked control bytes is the point.
	assert.doesNotMatch(plain, /[\r\n\x00-\x1f\x7f\x9b]/, `control byte in ${JSON.stringify(plain)}`);
	assert.ok(visibleWidth(row) <= width, `${visibleWidth(row)} > ${width}`);
}

test("chrome labels never add rows, leak controls, or split escapes", () => {
	const nav = new NavBar(
		() => ({
			view: "chat",
			title: DIRTY,
			project: DIRTY,
			branch: DIRTY,
			model: DIRTY,
			status: "ready",
		}),
		() => {},
		() => 24,
	);
	for (const width of [1, 20, 80, 160]) {
		const rows = nav.render(width);
		assert.equal(rows.length, 1);
		for (const row of rows) assertOneLine(row, width);
	}

	const coordinator = new AnimationCoordinator(() => {}, true);
	try {
		const footer = new Footer(coordinator);
		Object.assign(footer, { model: DIRTY, branch: DIRTY, mode: DIRTY, keyHint: DIRTY, workspaceHints: DIRTY });
		const rows = footer.render(120);
		assert.equal(rows.length, 1);
		assertOneLine(rows[0]!, 120);
	} finally {
		coordinator.dispose();
	}

	const popup = new CompletionPopup({ maxVisibleRows: 4 });
	popup.setModel({ items: [{ label: DIRTY, detail: DIRTY, kind: 3 }], selectedIndex: 0, omitted: 0 });
	const popupRows = popup.render(80);
	assert.equal(popupRows.length, 1);
	assertOneLine(popupRows[0]!, 80);

	const problem = formatProblemRow(
		{
			uri: "file:///w/a.ts",
			producer: "ts",
			version: null,
			severity: 1,
			message: DIRTY,
			source: DIRTY,
			line: 0,
			character: 0,
			range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } },
		},
		{ width: 80 },
	);
	assertOneLine(problem, 80);

	const tui = { terminal: { columns: 80, rows: 20 }, requestRender() {} };
	const status = new StatusPage(
		{ terminal: { columns: 80, rows: 20 }, requestRender() {} } as never,
		() => ({
			agents: [],
			shells: [],
			tools: [],
			mcp: [],
			harnesses: [],
			queued: 0,
			approvals: 0,
			updatedAt: "12:00",
			error: DIRTY,
		}),
	);
	status.setViewportRows(20);
	for (const row of status.render(80)) assertOneLine(row, 80);

	const changes = new ChangesPage(
		tui as never,
		() => ({ turns: [], hasChat: true, error: DIRTY }),
		() => ({ loading: false, files: [], truncated: false }),
	);
	changes.setViewportRows(20);
	for (const row of changes.render(80)) assertOneLine(row, 80);
});
