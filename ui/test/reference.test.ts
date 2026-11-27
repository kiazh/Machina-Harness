import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, visibleWidth, TuiAltScreen, type Terminal } from "@earendil-works/pi-tui";
import { ComposerBox, Footer, header, ToolTurn } from "../src/components.ts";
import { ApprovalCard } from "../src/components/ApprovalCard.ts";
import { AnimationCoordinator } from "../src/design/motion.ts";
import { editorTheme } from "../src/theme.ts";
const plain = (rows: string[]) => rows.map(stripTerminalSequences);

test("reference header keeps brand and workspace on one row", () => {
 const rows = plain(header("1.0", {model:"actual", provider:"test", cwd:"/repo", branch:"main"}).render(120));
 assert.equal(rows.length, 1);
 assert.match(rows[0]!, /MACHINA.*repo$/);
});

test("completed tools use quiet indented log rows across the pane", () => {
 const tool = new ToolTurn("read_file", {path:"install.sh"}, {frame: f => f[0] ?? ""});
 tool.settle({text:"Homebrew fallback configuration", backendTruncated:false, locallyTruncated:false}, "success");
 const rows = tool.render(160);
 assert.match(plain(rows)[1]!, /^ {2}✔/);
 assert.doesNotMatch(plain(rows).join("\n"), /┌|└/);
 assert.ok(rows.every(row => visibleWidth(row) <= 160));
 assert.match(plain(rows).join("\n"), /Read file.*install.sh/);
 assert.match(plain(rows).join("\n"), /Homebrew fallback/);
});

test("reference footer is one quiet row with actual metadata and right aligned help", () => {
 const coordinator = new AnimationCoordinator(() => {}, true);
 try {
  const footer = new Footer(coordinator);
  Object.assign(footer, {model:"actual-model", branch:"main", tokens:17});
  const rows = plain(footer.render(120));
  assert.equal(rows.length, 1);
  assert.match(rows[0]!, /actual-model.*main.*ask.*ready.*17 tokens/);
  assert.match(rows[0]!, /\? shortcuts · \/ commands\s+□$/);
 } finally {coordinator.dispose();}
});

test("one-row empty composer retains its actionable placeholder", () => {
 const tui = new TuiAltScreen({columns:80, rows:24} as Terminal);
 const composer = new ComposerBox(tui, editorTheme, {paddingX:1});
 composer.setMaxRows(1);
 assert.match(plain(composer.render(80)).join("\n"), /Ask anything/);
});

test("reference approval dock separates vertical choices from transcript evidence", () => {
 const card = new ApprovalCard({requestId:"r", sessionId:"s", operation:"write_file", target:"install.sh", argv:[], cwd:"/repo", backend:"local", capabilities:[]}, {docked:true} as ConstructorParameters<typeof ApprovalCard>[1]);
 const rows = plain(card.render(120));
 assert.match(rows.join("\n"), /Allow write_file · install.sh\?/);
 const choices = ["Allow once", "Allow for this session", "Deny"].map(label => rows.findIndex(row => row.includes(label)));
 assert.ok(choices.every(i => i >= 0));
 assert.equal(new Set(choices).size, 3);
  assert.match(rows[choices[0]!]!, /›.*Allow once/);
 assert.doesNotMatch(rows.join("\n"), /argv:|Capabilities:|┌/);
});

test("approval evidence never exceeds a narrow terminal", () => {
	const card = new ApprovalCard(
		{ requestId: "r", sessionId: "s", operation: "write_file", target: "install.sh", argv: [], cwd: "/repo", backend: "local", capabilities: [] },
		{ docked: true } as ConstructorParameters<typeof ApprovalCard>[1],
	);
	for (const width of [8, 12, 20, 24, 40]) {
		const rows = card.evidence.render(width);
		assert.ok(rows.every(row => visibleWidth(row) <= width), `width ${width}: ${rows.map(visibleWidth).join(",")}`);
	}
});

test("native approval frame docks controls above composer at 140x52, 80x24, and 40x9", async (t) => {
 const {referenceFixture} = await import("./support/reference.ts");
 for (const [columns, rows] of [[140,52], [80,24], [40,9]]) {
  const fixture = await referenceFixture(columns!, rows!);
  try {
   const screen = fixture.plain();
   assert.equal(screen.length, rows);
   assert.ok(fixture.tui.getScreenLines().every(line => visibleWidth(line) <= columns!));
   const heading = screen.findIndex(line => line.includes("Allow write_file"));
   const once = screen.findIndex(line => line.includes("Allow once"));
   const deny = screen.findIndex(line => line.includes("Deny"));
   const composer = screen.findIndex(line => line.includes("Ask anything") || line.includes("Queue a message"));
   assert.ok(heading >= 0 && once > heading && deny > once && composer > deny);
   assert.match(screen.at(-1)!, /awaiting approval/);
   if (rows! >= 24) assert.match(screen.join("\n"), /needs approval/);
   if (rows === 9) {
    assert.match(screen.join("\n"), /Allow blocked/);
     fixture.type("\r");
    assert.equal(fixture.requests.filter(r => r.method === "approvals.resolve").length, 0, "unreviewable Allow stays blocked");
   }
   fixture.type("\x1b");
   await new Promise(resolve => setImmediate(resolve));
   fixture.tui.renderNow(true);
   assert.equal(fixture.requests.filter(r => r.method === "approvals.resolve").length, 1);
   assert.equal(fixture.requests.at(-1)!.params.verdict, "denied");
   assert.doesNotMatch(fixture.plain().join("\n"), /Allow write_file/);
  } finally {await fixture.app.exit();}
 }
 t.diagnostic("Native pi-tui frames: reference layout plus clipped-terminal approval safety");
});

test("a fresh Enter allows only the active request, once", async () => {
 const {referenceFixture} = await import("./support/reference.ts");
 const fixture = await referenceFixture(80,24);
 try {
  fixture.emit(fixture.approval("second-request"));
  fixture.type("\r");
  await new Promise(resolve => setImmediate(resolve));
  fixture.type("\x1b[13;1:2u");
  const decisions = fixture.requests.filter(r => r.method === "approvals.resolve");
  assert.deepEqual(decisions.map(r => r.params), [
   {requestId:"demo-7f2a", sessionId:"reference-session", verdict:"approved", grant:"once"},
  ]);
 } finally {await fixture.app.exit();}
});

test("command strip tabs switch screens without leaving the shell", async () => {
	const { referenceFixture } = await import("./support/reference.ts");
	const fixture = await referenceFixture(120, 40);
	try {
		const clickTab = (label: string): void => {
			const x = fixture.plain()[0]!.indexOf(label) + 1;
			fixture.type(`\x1b[<0;${x};1M`);
			fixture.type(`\x1b[<0;${x};1m`);
		};
		clickTab("Activity");
		await new Promise(resolve => setImmediate(resolve));
		fixture.tui.renderNow(true);
		assert.equal((fixture.app as unknown as { view: string }).view, "status", "a pending approval never blocks navigation");
		fixture.type("\x1b");
		await new Promise(resolve => setImmediate(resolve));
		fixture.tui.renderNow(true);
		assert.match(fixture.plain().join("\n"), /Allow write_file/, "returning to Chat restores the decision");
		clickTab("Chat");
		await new Promise(resolve => setImmediate(resolve));
		fixture.tui.renderNow(true);
		assert.equal((fixture.app as unknown as { view: string }).view, "chat");
		assert.match(fixture.plain().join("\n"), /Allow write_file/, "the decision is still waiting");
		clickTab("Activity");
		await new Promise(resolve => setImmediate(resolve));
		fixture.tui.renderNow(true);
		assert.equal((fixture.app as unknown as { view: string }).view, "status");
		assert.match(fixture.plain().join("\n"), /Recent work/);
		clickTab("Chat");
		await new Promise(resolve => setImmediate(resolve));
		fixture.tui.renderNow(true);
		assert.equal((fixture.app as unknown as { view: string }).view, "chat");
		assert.match(fixture.plain().join("\n"), /Queue a message/);
	} finally {
		await fixture.app.exit();
	}
});

test("approval queue and externally resolved requests advance the dock without issuing decisions", async () => {
 const {referenceFixture} = await import("./support/reference.ts");
 const fixture = await referenceFixture();
 try {
  fixture.emit(fixture.approval("second-request"));
  fixture.tui.renderNow(true);
  assert.match(fixture.plain().join("\n"), /request: demo-7f2a/);
  fixture.emit({type:"approval.resolved", requestId:"demo-7f2a", status:"approved"});
  fixture.tui.renderNow(true);
  assert.match(fixture.plain().join("\n"), /request: second-request/);
  assert.equal(fixture.requests.filter(r => r.method === "approvals.resolve").length, 0);
  fixture.type("\x1b");
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(fixture.requests.at(-1)!.params, {requestId:"second-request", sessionId:"reference-session", verdict:"denied", grant:"once"});
 } finally {await fixture.app.exit();}
});

test("an approval arriving while on Activity does not steal its focus or hide Keys", async () => {
	const { referenceFixture } = await import("./support/reference.ts");
	const fixture = await referenceFixture();
	try {
		fixture.app.openStatus();
		fixture.tui.renderNow(true);
		const page = fixture.tui.getFocusedComponent();
		fixture.emit(fixture.approval("late-approval"));
		fixture.tui.renderNow(true);
		assert.equal(fixture.tui.getFocusedComponent()?.constructor.name, page?.constructor.name);
		fixture.type("?");
		fixture.tui.renderNow(true);
		assert.match(fixture.plain().join("\n"), /Keyboard shortcuts/);
		fixture.type("\x1b");
		await new Promise(resolve => setImmediate(resolve));
		fixture.tui.renderNow(true);
		assert.equal(fixture.tui.getFocusedComponent()?.constructor.name, page?.constructor.name);
	} finally {
		await fixture.app.exit();
	}
});

test("a hostile approval payload stays one row per row", async () => {
	const { referenceFixture } = await import("./support/reference.ts");
	const fixture = await referenceFixture();
	try {
		fixture.type("\x1b"); // deny the demo request, then deliver a hostile one
		await new Promise(resolve => setImmediate(resolve));
		fixture.emit({
			...fixture.approval("hostile-approval"),
			tool: "write_file\nnext",
			target: "install.sh\n\x1b[2Jowned",
			argv: ["a\nb", "\x1b]0;title\x07"],
		});
		fixture.tui.renderNow(true);
		const screen = fixture.plain();
		assert.equal(screen.length, (fixture.tui.terminal as { rows: number }).rows, "the dock never changes the frame height");
		// eslint-disable-next-line no-control-regex -- leaked control bytes are exactly what this asserts.
		for (const line of screen) assert.doesNotMatch(line, /[\x00-\x1f\x7f]/, `control byte in ${JSON.stringify(line)}`);
		assert.match(screen.join("\n"), /Allow write_file/);
	} finally {
		await fixture.app.exit();
	}
});

test("malformed gateway frames never crash or open an unbound approval", async () => {
	const { referenceFixture } = await import("./support/reference.ts");
	const fixture = await referenceFixture();
	try {
		fixture.type("\x1b"); // deny the demo request first
		await new Promise(resolve => setImmediate(resolve));
		const frames: Array<Record<string, unknown>> = [
			{ type: "tool.start" },
			{ type: "tool.start", id: 7, name: null, args: 5 },
			{ type: "tool.complete", id: 7, result: null },
			{ type: "message.delta", text: null },
			{ type: "message.complete", text: 42 },
			{ type: "needs_approval" },
			{ type: "permission.required", requestId: 42, sessionId: null },
			{ type: "permission.resolved", requestId: 42 },
			{ type: "error", message: null },
		];
		for (const frame of frames) {
			assert.doesNotThrow(() => fixture.emit(frame), `frame ${String(frame.type)} must not crash the UI`);
		}
		fixture.tui.renderNow(true);
		assert.equal(fixture.plain().length, (fixture.tui.terminal as { rows: number }).rows);
		assert.doesNotMatch(fixture.plain().join("\n"), /Approval required/, "an unidentifiable frame never opens a card");
	} finally {
		await fixture.app.exit();
	}
});

test("terminal completion retires unanswered approval controls without sending a decision", async () => {
 const {referenceFixture} = await import("./support/reference.ts");
 const fixture = await referenceFixture();
 try {
  fixture.emit({type:"message.complete", text:"Turn ended", tokens:3, iterations:1, toolCalls:1, cancelled:true});
  fixture.tui.renderNow(true);
  assert.doesNotMatch(fixture.plain().join("\n"), /Allow write_file/);
  fixture.type("\x1b");
  assert.equal(fixture.requests.filter(r => r.method === "approvals.resolve").length, 0);
 } finally {await fixture.app.exit();}
});

test("queued approvals keep the active action's evidence visible at 80x24", async () => {
 const {referenceFixture} = await import("./support/reference.ts");
 const fixture = await referenceFixture(80,24);
 try {
  fixture.emit({...fixture.approval("queued-r"), target:"danger.sh", argv:["erase", "danger"]});
  fixture.tui.renderNow(true);
  let screen = fixture.plain().join("\n");
  assert.match(screen, /Approval required · needs approval/);
  assert.match(screen, /Allow write_file · install.sh\?/);
  assert.doesNotMatch(screen, /danger.sh|erase/);
  fixture.emit({type:"approval.resolved", requestId:"demo-7f2a", status:"denied"});
  fixture.tui.renderNow(true);
  screen = fixture.plain().join("\n");
  assert.match(screen, /Allow write_file · danger.sh\?/);
  assert.match(screen, /argv: "erase" "danger"/);
 } finally {await fixture.app.exit();}
});

test("long approval targets remain reviewable through evidence paging", () => {
 const target = "/safe/" + "a".repeat(100) + "/delete-everything";
 const card = new ApprovalCard({requestId:"long", sessionId:"s", operation:"write_file", target, argv:[], cwd:"/repo", backend:"local", capabilities:[]}, {docked:true, tui:{terminal:{columns:80,rows:24}} as unknown as import("@earendil-works/pi-tui").TUI});
 const viewed: string[] = [];
 for(let i=0;i<6;i++) {
  viewed.push(...plain(card.evidence.render(80)));
  card.handleInput("\x1b[6~");
 }
 assert.match(viewed.join("\n"), /delete-everything/);
});

test("ordinary dialogs arriving after approval wait and restore their draft after denial", async () => {
 const {referenceFixture} = await import("./support/reference.ts");
 const fixture = await referenceFixture(80,24);
 try {
  const pending = fixture.app.askText({title:"Late persona response", initial:"saved draft"});
  fixture.tui.renderNow(true);
  assert.match(fixture.plain().join("\n"), /Allow write_file/);
  assert.doesNotMatch(fixture.plain().join("\n"), /Late persona response/);
  fixture.type("\x1b");
  await new Promise(resolve => setImmediate(resolve));
  fixture.tui.renderNow(true);
  assert.match(fixture.plain().join("\n"), /Late persona response/);
  assert.match(fixture.plain().join("\n"), /saved draft/);
  fixture.type("\x13");
  assert.equal(await pending, "saved draft");
  assert.equal(fixture.requests.filter(r => r.method === "approvals.resolve").length, 1);
 } finally {await fixture.app.exit();}
});

test("fresh bound approval continuation renders and releases the turn guard on completion", async () => {
 const {referenceFixture} = await import("./support/reference.ts");
 const fixture = await referenceFixture();
 try {
  fixture.emit({type:"approval.resolved", requestId:"demo-7f2a", status:"approved"});
  fixture.emit({type:"approval.resumed", requestId:"demo-7f2a", originTurnId:"reference-turn", turnId:"resumed-turn"});
  fixture.emit({type:"message.delta", turnId:"resumed-turn", text:"The change was applied."});
  fixture.emit({type:"message.complete", turnId:"resumed-turn", text:"The change was applied.", tokens:7, iterations:1, toolCalls:1, cancelled:false});
  fixture.tui.renderNow(true);
  assert.match(fixture.plain().join("\n"), /The change was applied\./);
  const count = fixture.requests.filter(r => r.method === "prompt.submit").length;
  await fixture.app.submitTurn("next task");
  assert.equal(fixture.requests.filter(r => r.method === "prompt.submit").length, count+1);
 } finally {await fixture.app.exit();}
});
