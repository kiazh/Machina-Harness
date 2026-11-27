// Activity/status controller: the live status snapshot, its background
// refresh, Activity inspection, the command-strip activity count, and the
// shell screen stack (Activity is the left tab of that strip).
//
// Interface invariants:
// - The shell screen still lives in App; this controller reads and writes it
//   through StatusHost, so there is exactly one view state.
// - refresh() keeps the original 3s screen timer cadence, the in-flight guard,
//   the 15s slow-source window, and the bounded lists (24 jobs/tools, 40
//   sessions, 20 progress lines).
// - inspectActivity() keeps the same queue-version fence and the same dialog
//   choices; it never sends a fix or resolves an approval.
// - dispose() is the teardown both exit and gateway-loss call: timer first,
//   then both pages.
import { truncateToWidth } from "@earendil-works/pi-tui";
import { activityEmptyGuidance, type ShellJob, type SteeringItem } from "../activity.ts";
import type { NavSnapshot, NavView } from "../components/NavBar.ts";
import { OutputViewer, type OutputRecord } from "../components/OutputViewer.ts";
import type { StatusRow, StatusSnapshot } from "../components/StatusPage.ts";
import { sanitizeDisplay } from "../design/text.ts";
import { toolCallDisplayName } from "../mcpNames.ts";
import type { InteropStatusResult, MCPServerView, SessionListResult } from "../protocol.ts";
import type { ScreenView, StatusHost } from "./contracts.ts";

/** Gateway view of one background harness run (interop.jobs / harness.job). */
export interface HarnessJobView {
	id: string;
	owner_session_id: string;
	session_id: string;
	platform: string;
	task: string;
	status: string;
	needs_input: boolean;
	started_at: string | null;
	ended_at: string | null;
	exit_code: number | null;
	command: string[];
	stdout: string;
	stderr: string;
	error: string;
	progress: string[];
}

export class StatusController {
	private readonly host: StatusHost;
	private refreshing = false;
	private harnesses: StatusRow[] = [];
	private readonly harnessJobs = new Map<string, HarnessJobView>();
	private mcp: StatusRow[] = [];
	private sessions: StatusRow[] = [];
	private defaultPending = false;
	private slowRefreshAt = 0;
	private updatedAt = "";
	private error = "";
	private timer: ReturnType<typeof setInterval> | undefined;

	constructor(host: StatusHost) {
		this.host = host;
	}

	/** Open the full-page live status tab (also `/activity`). */
	openStatus(): void {
		this.openScreen("status");
	}

	/**
	 * Open the per-prompt Changes screen. Changes always belong to the open
	 * chat: with no chat there is nothing to review, and browsing another
	 * chat's work from here would misattribute it.
	 */
	openChanges(): void {
		this.openScreen("changes");
	}

	/** Command-strip click: switch screens without changing the app's key map. */
	selectNav(view: NavView): void {
		if (this.host.quickOpenActive()) return;
		// A visible dialog owns the screen; one hidden by the dock does not.
		if (this.host.hasVisibleDialog()) return;
		if (view === "chat") {
			if (this.host.screen() !== "chat") this.closeScreen();
			if (this.host.turnSession.approvalDialogCount()) this.host.showApprovalDock();
			else this.host.focusChat();
			return;
		}
		if (view === "activity") {
			this.openStatus();
			return;
		}
		this.openChanges();
	}

	private navActivityCount(): number {
		const runningJobs = this.host.activity.allJobs().filter(({ job }) =>
			job.status === "running" || job.status === "stopping",
		).length;
		const agents = this.host.familyRows().filter(row => !row.main && row.running).length;
		return runningJobs + agents + this.host.activity.queuedCount();
	}

	navSnapshot(): NavSnapshot {
		const project = (this.host.footer.cwd || "")
			.replace(/[\\/]+$/, "")
			.split(/[\\/]/)
			.pop() ?? "";
		const screen = this.host.screen();
		return {
			view: screen === "status" ? "activity" : screen,
			title: this.host.current()?.title ?? "",
			project,
			branch: this.host.footer.branch,
			model: this.host.footer.model,
			status: this.host.footer.status,
			activityCount: this.navActivityCount(),
			changeCount: this.host.changesCount(),
		};
	}

	snapshot(): StatusSnapshot {
		const family = this.host.familyRows();
		const titles = new Map(family.map((row) => [row.session.id, row.session.title || row.session.id]));
		const current = this.host.current();
		if (current && !titles.has(current.id)) titles.set(current.id, current.title || current.id);
		const agentIds = new Set(family.map((row) => row.session.id));
		const agents: StatusRow[] = family.map((row) => {
			const activeTool = this.host.turnSession.activeToolLabel(row.session.id);
			const queued = this.host.activity.queue(row.session.id).length + this.host.turnSession.queuedSteering(row.session.id).length;
			const detail = [
				row.session.model || "",
				activeTool ? `tool ${activeTool}` : "",
				queued ? `${queued} queued` : "",
			].filter(Boolean).join(" · ");
			return {
				id: `agent:${row.session.id}`,
				label: row.main ? `Main · ${row.session.title || "untitled"}` : (row.session.title || "Subagent"),
				detail,
				state: row.running ? "running" : "idle",
				logs: [
					`Session: ${row.session.id}`,
					`Title: ${row.session.title || "untitled"}`,
					`Route: ${row.session.provider || "?"} / ${row.session.model || "?"}`,
					`State: ${row.running ? "running" : "idle"}`,
					`Active tool: ${activeTool || "none"}`,
					`Queued: ${queued}`,
				],
			};
		});
		// Turns in other sessions still count as live agents even when they are
		// outside the visible family (for example a background child session).
		for (const sessionId of this.host.turnSession.inFlightSessions()) {
			if (agentIds.has(sessionId)) continue;
			agentIds.add(sessionId);
			agents.push({
				id: `agent:${sessionId}`,
				label: `Session ${sessionId.slice(0, 8)}`,
				detail: this.host.turnSession.activeToolLabel(sessionId) ? `tool ${this.host.turnSession.activeToolLabel(sessionId)}` : "turn in flight",
				state: "running",
				logs: [
					`Session: ${sessionId}`,
					"State: running (outside the visible family)",
					`Active tool: ${this.host.turnSession.activeToolLabel(sessionId) || "none"}`,
				],
			});
		}
		const shells: StatusRow[] = this.host.activity.allJobs().slice(0, 24).map(({ sessionId, job }) => {
			const title = titles.get(sessionId) ?? sessionId.slice(0, 8);
			const output = job.output ? job.output.trimEnd().split(/\r?\n/).slice(-12) : [];
			return {
				id: `shell:${sessionId}:${job.id}`,
				label: job.command || "Shell command",
				detail: `${job.foreground ? "Foreground shell" : "Background shell"} · ${title}`,
				state: job.status,
				preview: output.slice(-3),
				logs: [
					`Session: ${title}`,
					`Command: ${job.command}`,
					`Status: ${job.status}`,
					`Return code: ${job.returncode ?? "pending"}`,
					...(output.length ? ["", "Output:", ...output] : []),
				],
			};
		});
		const tools: StatusRow[] = this.host.activity.allTools(24).map((tool) => {
			const kind = tool.name.startsWith("mcp_")
				? "MCP"
				: tool.name.startsWith("harness_")
					? "HARNESS"
					: "TOOL";
			const display = toolCallDisplayName(tool.name, tool.args) || tool.name;
			const duration = tool.completedAt ? `${Math.max(0, tool.completedAt - tool.startedAt)}ms` : "running";
			const result = tool.result ? tool.result.split(/\r?\n/).slice(0, 12) : [];
			return {
				id: `tool:${tool.sessionId}:${tool.id}`,
				label: `${kind} · ${display}`,
				detail: titles.get(tool.sessionId) ?? tool.sessionId.slice(0, 8),
				state: tool.status,
				preview: result,
				logs: [
					`Tool: ${tool.name}`,
					`Session: ${titles.get(tool.sessionId) ?? tool.sessionId}`,
					`Started: ${new Date(tool.startedAt).toLocaleTimeString()} · ${duration}`,
					`Status: ${tool.status}`,
					...(tool.outcome ? [`Outcome: ${tool.outcome}`] : []),
					...(tool.args && Object.keys(tool.args).length ? [`Arguments: ${JSON.stringify(tool.args)}`] : []),
					...(result.length ? ["", "Result:", ...result] : []),
				],
			};
		});
		const queued = this.host.activity.queuedCount() + family.reduce(
			(total, row) => total + this.host.turnSession.queuedSteering(row.session.id).length,
			0,
		);
		return {
			agents,
			sessions: this.sessions,
			harnesses: [...this.harnesses, ...this.harnessJobRows()],
			shells,
			tools,
			mcp: this.mcp,
			queued,
			approvals: this.host.turnSession.approvalDialogCount(),
			updatedAt: this.updatedAt,
			error: this.error,
		};
	}

	/** Background harness runs, rendered beside detected harnesses. */
	private harnessJobRows(): StatusRow[] {
		const current = this.host.current();
		const visible = new Set(this.host.familyRows().map((row) => row.session.id));
		if (current) visible.add(current.id);
		return [...this.harnessJobs.values()]
			.filter(
				job =>
					visible.size === 0
					|| visible.has(job.owner_session_id)
					|| job.session_id === current?.id,
			)
			.sort((left, right) => (right.started_at ?? "").localeCompare(left.started_at ?? ""))
			.slice(0, 24)
			.map(job => ({
				id: `harness-job:${job.id}`,
				label: `${job.platform} · ${job.task.slice(0, 48)}`,
				detail:
					job.status === "running"
						? (job.progress?.at(-1)?.slice(0, 80) || "background subagent")
						: job.needs_input
							? "needs direction"
							: (job.command[0] ?? `${job.platform} run`),
				state: job.needs_input && job.status !== "running" ? "needs input" : job.status,
				updatedAt: job.ended_at ?? job.started_at ?? undefined,
				logs: [
					`Job: ${job.id}`,
					`Harness: ${job.platform}`,
					`Session: ${job.session_id}`,
					`Owner: ${job.owner_session_id}`,
					`Task: ${job.task}`,
					`Status: ${job.status}`,
					...(job.needs_input ? ["Needs input: the harness asked a question."] : []),
					`Exit: ${job.exit_code ?? "pending"}`,
					...(job.error ? [`Error: ${job.error}`] : []),
					...(job.progress?.length
						? ["", "Live output:", ...job.progress.slice(-20)]
						: []),
					...(job.stdout ? ["", "Output:", ...job.stdout.split(/\r?\n/).slice(0, 12)] : []),
					...(job.stderr ? ["", "Stderr:", ...job.stderr.split(/\r?\n/).slice(0, 8)] : []),
				],
			}));
	}

	async refresh(): Promise<void> {
		if (this.host.screen() !== "status" || this.refreshing) return;
		this.refreshing = true;
		const sessionId = this.host.current()?.id;
		const slow = Date.now() - this.slowRefreshAt >= 15_000;
		if (slow) this.slowRefreshAt = Date.now();
		try {
			const [interop, mcp, jobs, harnessJobs, steering, sessionsResult] = await Promise.allSettled([
				slow ? this.host.request<InteropStatusResult>("interop.status") : Promise.resolve(null),
				slow ? this.host.request<{ servers?: MCPServerView[] }>("mcp.list") : Promise.resolve(null),
				sessionId
					? this.host.request<{ jobs?: ShellJob[] }>("shell.jobs", { sessionId })
					: Promise.resolve({ jobs: [] }),
				sessionId
					? this.host.request<{ jobs?: HarnessJobView[] }>("interop.jobs", { sessionId })
					: Promise.resolve({ jobs: [] }),
				sessionId
					? this.host.request<{ items?: SteeringItem[] }>("prompt.steering", { sessionId })
					: Promise.resolve({ items: [] }),
				slow ? this.host.request<SessionListResult>("session.list", { limit: 40 }) : Promise.resolve(null),
			]);
			if (this.host.screen() !== "status") return;
			if (sessionId) {
				if (jobs.status === "fulfilled") {
					for (const job of jobs.value.jobs ?? []) this.host.activity.setJob(sessionId, job);
				}
				if (steering.status === "fulfilled") {
					this.host.activity.setQueue(sessionId, steering.value.items ?? []);
				}
				if (harnessJobs.status === "fulfilled") {
					for (const job of harnessJobs.value.jobs ?? []) this.harnessJobs.set(job.id, job);
				}
			}
			if (sessionsResult.status === "fulfilled" && sessionsResult.value) {
				const live = new Set(this.host.turnSession.inFlightSessions());
				const currentId = this.host.current()?.id;
				const defaultProject = (this.host.footer.cwd || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "global";
				this.sessions = (sessionsResult.value.sessions ?? [])
					.slice(0, 40)
					.map((session) => {
						const project = session.workspaceRoot
							? (session.workspaceRoot.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || defaultProject)
							: defaultProject;
						// A persisted session's "active" status means "not archived";
						// it is not proof that a turn is running right now. Only the
						// in-flight turn registry can make the live dot green.
						const running = live.has(session.id);
						const state = running
							? "active"
							: session.status === "active"
								? "inactive"
								: session.status || "inactive";
						return {
							id: `session:${session.id}`,
							label: session.title || "untitled",
							detail: `${session.provider || "?"} / ${session.model || "?"}`,
							state,
							running,
							updatedAt: session.lastActivity ?? undefined,
							logs: [
								`Session: ${session.id}`,
								`Title: ${session.title || "untitled"}`,
								`Project: ${project}`,
								`Route: ${session.provider || "?"} / ${session.model || "?"}`,
								`Status: ${state}`,
								`Last activity: ${session.lastActivity || "unknown"}`,
								...(session.parentSessionId ? [`Parent: ${session.parentSessionId}`] : []),
							],
						};
					})
					.sort((left, right) => {
						if (left.id === `session:${currentId}`) return -1;
						if (right.id === `session:${currentId}`) return 1;
						return (right.updatedAt ?? "").localeCompare(left.updatedAt ?? "");
					});
				// The first refresh starts before session.list returns, so the
				// queue summary (or a stale session list) can be selected for one
				// frame. Once the real session list arrives after opening the tab,
				// move the default selection to the current chat.
				if (this.defaultPending && this.host.screen() === "status") {
					// One attempt only: a failed or empty list must not move the
					// user's selection 15s later on the next retry.
					this.defaultPending = false;
					if (sessionsResult.status === "fulfilled" && this.sessions.length) {
						this.host.statusPage().selectDefault();
					}
				}
			}
			if (interop.status === "fulfilled" && interop.value) {
				this.harnesses = (interop.value.platforms ?? []).map((row) => ({
					id: `harness:${row.platform}`,
					label: row.label || row.platform,
					detail: [row.version, row.scope, row.notes?.[0]].filter(Boolean).join(" · "),
					state: row.running ? "running" : row.installed ? "installed" : "not installed",
					logs: [
						`Platform: ${row.platform}`,
						`Installed: ${row.installed ? "yes" : "no"}`,
						`Running: ${row.running ? "yes" : "no"}`,
						`Version: ${row.version || "unknown"}`,
						`Scope: ${row.scope || "none"}`,
						...(row.notes?.length ? ["", "Notes:", ...row.notes] : []),
					],
				}));
			}
			if (mcp.status === "fulfilled" && mcp.value) {
				this.mcp = (mcp.value.servers ?? []).map((server) => ({
					id: `mcp:${server.id}`,
					label: server.display_name || server.id,
					detail: [server.transport, server.server_version, server.error].filter(Boolean).join(" · "),
					state: server.error ? "error" : server.state,
					logs: [
						`ID: ${server.id}`,
						`Transport: ${server.transport}`,
						`State: ${server.state}`,
						`Server: ${server.server_name || "unknown"}${server.server_version ? ` ${server.server_version}` : ""}`,
						`Protocol: ${server.protocol_version || "unknown"}`,
						...(server.error ? [`Error: ${server.error}`] : []),
					],
				}));
			}
			const failures = [interop, mcp].filter((result) => result.status === "rejected").length;
			this.error = failures ? `Live status refresh incomplete (${failures} source${failures === 1 ? "" : "s"} unavailable).` : "";
			this.host.log.debug("status.refresh", {
				sessions: this.sessions.length,
				harnesses: this.harnesses.length,
				mcp: this.mcp.length,
				failures,
			});
			this.updatedAt = new Date().toLocaleTimeString("en-US", {
				hour12: false,
				hour: "2-digit",
				minute: "2-digit",
				second: "2-digit",
			});
		} finally {
			this.refreshing = false;
			this.host.tui.requestRender();
		}
	}

	openScreen(screen: Exclude<ScreenView, "chat">): void {
		if (this.host.isExiting()) return;
		this.host.log.info("screen.open", { screen, session: this.host.current()?.id ?? null, current: this.host.screen() });
		if (this.host.screen() === screen) {
			if (screen === "status") void this.refresh();
			else this.host.refreshChanges();
			this.host.tui.requestRender();
			return;
		}
		this.clearScreenTimer();
		this.disposeActivePage();
		this.host.setScreen(screen);
		const page = screen === "status" ? this.host.statusPage() : this.host.changesPage();
		this.host.setShellPage(page);
		this.host.tui.setFocus(page);
		this.host.footer.keyHint = "";
		if (screen === "status") {
			this.defaultPending = true;
			this.updatedAt = "";
			this.error = "";
			this.slowRefreshAt = 0;
			this.host.refreshFamily();
			void this.refresh();
		} else {
			this.host.setChangesError("");
			this.host.syncChangesSelection();
			this.host.ensureChangesDiff();
			this.host.refreshChanges();
		}
		this.timer = setInterval(() => {
			if (this.host.screen() === "status") void this.refresh();
			else if (this.host.screen() === "changes") this.host.refreshChanges();
		}, 3_000);
		this.host.tui.requestRender();
	}

	closeScreen(): void {
		if (this.host.screen() === "chat") return;
		const previous = this.host.screen();
		this.host.log.info("screen.close", { screen: previous });
		this.host.setScreen("chat");
		this.clearScreenTimer();
		if (previous === "status") this.host.statusPage().dispose();
		else this.host.changesPage().dispose();
		this.host.setShellPage(undefined);
		this.host.footer.keyHint = "";
		// A pending decision takes focus back on return; otherwise the composer.
		if (this.host.turnSession.approvalDialogCount()) this.host.showApprovalDock();
		else this.host.focusChat();
	}

	private clearScreenTimer(): void {
		if (this.timer !== undefined) {
			clearInterval(this.timer);
			this.timer = undefined;
		}
	}

	private disposeActivePage(): void {
		if (this.host.screen() === "status") this.host.statusPage().dispose();
		else if (this.host.screen() === "changes") this.host.changesPage().dispose();
	}

	/** App teardown: retire the refresh timer and both retained pages. */
	dispose(): void {
		this.clearScreenTimer();
		this.host.statusPage().dispose();
		this.host.changesPage().dispose();
	}

	async inspectActivity(): Promise<void> {
		const session = this.host.current();
		if (!session) {
			this.host.print(activityEmptyGuidance(false), "guidance");
			return;
		}
		const queueVersion = this.host.activity.queueVersion(session.id);
		try {
			const [queued, jobs] = await Promise.all([
				this.host.request<{ items: SteeringItem[] }>("prompt.steering", { sessionId: session.id }).catch(() => ({ items: this.host.activity.queue(session.id) })),
				this.host.request<{ jobs: ShellJob[] }>("shell.jobs", { sessionId: session.id }).catch(() => ({ jobs: this.host.activity.listJobs(session.id) })),
			]);
			if (this.host.activity.queueVersion(session.id) === queueVersion) this.host.activity.setQueue(session.id, queued.items);
			for (const job of jobs.jobs) this.host.activity.setJob(session.id, job);
			if (this.host.current()?.id !== session.id) return;
			const items = [
				...queued.items.map((item) => ({ label: `Queued · ${truncateToWidth(sanitizeDisplay(item.text), 80, "…")}`, value: `q:${item.id}` })),
				...jobs.jobs.map((job) => ({ label: `${job.status} · ${truncateToWidth(sanitizeDisplay(job.command), 75, "…")}`, value: `j:${job.id}` })),
				...this.host.familyRows().filter((row) => !row.main).map((row) => ({ label: `Agent ${row.running ? "working" : "idle"} · ${row.session.title}`, value: `a:${row.session.id}` })),
			];
			if (this.host.turnSession.queuedSteering(session.id).length > 0) items.unshift({ label: "Retry retained steering", value: "retry" });
			if (!items.length) { this.host.print(activityEmptyGuidance(true), "guidance"); return; }
			const selected = await this.host.pickCentered("Activity", items);
			if (!selected || this.host.current()?.id !== session.id) return;
			if (selected.value === "retry") { this.host.turnSession.setInterrupted(session.id, false); await this.host.flushSteering(session.id); return; }
			if (selected.value.startsWith("a:")) { await this.host.openFamily(selected.value.slice(2)); return; }
			if (selected.value.startsWith("q:")) {
				const item = queued.items.find((value) => value.id === selected.value.slice(2));
				if (!item) return;
				const choice = await this.host.pickCentered("Queued steering", [
					{ label: "Edit", value: "edit" }, { label: "Remove", value: "remove" },
					...(!this.host.turnSession.isInFlight(session.id) ? [{ label: "Send queued prompts now", value: "send" }] : []),
				]);
				if (choice?.value === "send") { this.host.turnSession.setInterrupted(session.id, false); await this.host.flushSteering(session.id); return; }
				if (choice?.value === "edit") {
					const text = await this.host.askText({ title: "Edit queued steering", initial: item.text, maxLength: 20_000 });
					if (!text) return;
					await this.host.request("prompt.steering", { sessionId: session.id, replace: item.id, text });
				} else if (choice?.value === "remove") await this.host.request("prompt.steering", { sessionId: session.id, remove: item.id });
				const queueVersion = this.host.activity.queueVersion(session.id);
				const updated = await this.host.request<{ items: SteeringItem[] }>("prompt.steering", { sessionId: session.id });
				if (this.host.activity.queueVersion(session.id) === queueVersion) this.host.activity.setQueue(session.id, updated.items);
			} else {
				const id = selected.value.slice(2);
				const job = jobs.jobs.find((value) => value.id === id);
				const choice = await this.host.pickCentered("Command", [
					...(job?.foreground && job.status === "running" ? [{ label: "Background", value: "background", description: "Release the agent; original deadline remains" }] : []),
					{ label: "View live output", value: "view" },
					...(job?.status === "running" ? [{ label: "Stop command", value: "stop" }] : []),
				]);
				if (choice?.value === "background") await this.host.commandAction(session.id, id, "background");
				else if (choice?.value === "stop") {
					const result = await this.host.request<{ job: ShellJob }>("shell.jobs", { sessionId: session.id, jobId: id, stop: true });
					this.host.activity.setJob(session.id, result.job);
				} else if (choice?.value === "view") await this.inspectShellJob(session.id, id);
			}
		} catch (error) { this.host.print(`Could not load activity: ${message(error)}`, "error"); }
		finally { this.host.tui.requestRender(); }
	}

	async inspectShellJob(sessionId: string, id: string): Promise<void> {
		const initial = await this.host.request<{ job: ShellJob }>("shell.jobs", { sessionId, jobId: id });
		const record = (job: ShellJob): OutputRecord => ({ sessionId, toolId: id, name: `${job.status} · ${job.command}`, text: job.output ?? "", byteLength: Buffer.byteLength(job.output ?? ""), backendTruncated: job.truncated ?? false, locallyTruncated: false });
		let viewer: OutputViewer | undefined;
		let active = true, pending = false;
		const timer = setInterval(() => {
			if (!active || pending) return;
			pending = true;
			void this.host.request<{ job: ShellJob }>("shell.jobs", { sessionId, jobId: id }).then(({ job }) => {
				if (!active) return;
				viewer?.setRecord(record(job));
				this.host.activity.setJob(sessionId, job);
				if (job.status !== "running") clearInterval(timer);
				this.host.tui.requestRender();
			}).catch(() => { clearInterval(timer); }).finally(() => { pending = false; });
		}, 750);
		try { await this.host.openDialog<void>((close) => viewer = new OutputViewer(record(initial.job), { tui: this.host.tui, onClose: () => close() })); }
		finally { active = false; clearInterval(timer); }
	}

	/** Route a `harness.job` frame into the retained rows. */
	setHarnessJob(job: HarnessJobView): void {
		this.harnessJobs.set(job.id, job);
	}

	/** The child session a harness-job row opens, if the row is still live. */
	harnessSessionId(rowId: string): string | undefined {
		return this.harnessJobs.get(rowId)?.session_id;
	}
}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
