// Command controller: submit-time routing (plain prompt vs slash command),
// steering while a turn runs, blank-chat materialization, shell-job actions,
// and the `/skills` directory menu.
//
// Interface invariants:
// - submit() is the only entry point: it parses first, guards only conflicting
//   operations, records history, then either dispatches a command or submits a
//   turn. Read-only commands stay usable during a turn (AH-031).
// - A running turn keeps its steering semantics: plain text goes to
//   queueSteering instead of submitTurn, and image attachments are refused
//   with the same notice instead of being dropped.
// - A blank chat is a client-side draft. It is materialized only when an
//   ordinary message is actually submitted; slash commands never create one.
// - The image pipeline stays owned by the host composer (paste/drop/Esc also
//   use it). The controller only reads its epoch and takes/restores the draft,
//   so a late load or a reset can never submit a stale draft.
// - Secret-bearing command forms (/keys set ..., /provider add ... with an
//   inline key) never enter input history. See commandCarriesSecret().
// - Errors are reported through the same print/transcript notice paths as the
//   composition root; no error is swallowed.
import type { Component, Editor, TUI } from "@earendil-works/pi-tui";
import type { ActivityStore, ShellJob } from "../activity.ts";
import { guardSubmit } from "../commandDispatch.ts";
import { parseCommand, splitSub, type ParsedCommand } from "../commands.ts";
import { DEFAULT_MENU_VISIBLE_ROWS } from "../components/Menu.ts";
import {
	SkillsMenu,
	type SkillsCandidate,
	type SkillsDetailsSkill,
	type SkillsFilters,
	type SkillsPreviewResult,
	type SkillsSearchParams,
	type SkillsSearchResult,
	type SkillsSectionParams,
	type SkillsSectionResult,
} from "../components/SkillsMenu.ts";
import type { NoticeKind } from "../features/index.ts";
import { ImageDraft, type ImageAttachment } from "../images.ts";
import type { SessionInfo } from "../protocol.ts";

/** One reading of the composer's image pipeline, captured before awaiting loads. */
export interface CommandImageState {
	/** Image drops/pastes still loading; while nonzero the editor text wins. */
	readonly pending: number;
	/** Draft epoch; any reset invalidates an in-flight submit. */
	readonly epoch: number;
	/** The current load chain to await before reading editor text. */
	readonly settled: Promise<void>;
}

/** The last accepted prompt, replayed so its original image bytes can be reused. */
export interface LastPrompt {
	readonly text: string;
	readonly sessionId: string | undefined;
	readonly images: ImageAttachment[];
}

/**
 * Composition-root operations the command controller calls.
 *
 * Invariants:
 * - `current` is the single visible-session state; the controller keeps no copy.
 * - `submitTurn` is the App's turn path (echo, in-flight tracking, guard), not a
 *   raw `prompt.submit`; `queueSteering` is the in-flight delivery path.
 * - `ensureSession` materializes a blank chat exactly once for concurrent
 *   first submits.
 * - `setCommandRunning` stamps/clears the input-suppression window while a
 *   command does pre-dialog work, together with its timestamp.
 * - `imageState`/`imageDraft`/`bumpImageEpoch`/`lastPrompt` are the composer's
 *   existing image state, not a second copy owned by this controller.
 */
export interface CommandHost {
	readonly tui: TUI;
	readonly editor: Editor;
	readonly activity: ActivityStore;

	current(): SessionInfo | undefined;
	request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
	print(text: string, kind?: NoticeKind): void;
	transcriptNotice(text: string, kind: NoticeKind): void;
	isExiting(): boolean;

	isTurnRunning(sessionId: string): boolean;
	isInFlight(sessionId: string): boolean;
	visibleInFlight(): boolean;

	ensureSession(text: string): Promise<void>;
	submitTurn(text: string, images?: ImageAttachment[]): Promise<void>;
	queueSteering(text: string, fromDraft?: boolean): Promise<boolean>;
	setRunning(running: boolean): void;
	setCommandRunning(active: boolean): void;

	inspectShellJob(sessionId: string, jobId: string): Promise<void>;
	syncWorkspaceShell(): void;

	openDialog<T>(create: (close: (value?: T) => void) => Component): Promise<T | undefined>;
	runCommand(command: ParsedCommand): Promise<void>;

	imageState(): CommandImageState;
	imageDraft(): ImageDraft;
	setImageDraft(draft: ImageDraft): void;
	bumpImageEpoch(): number;
	lastPrompt(): LastPrompt;
	rememberPrompt(text: string, images: ImageAttachment[], sessionId: string | undefined): void;
}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

/**
 * True when a slash command embeds a raw secret that must never reach the
 * editor's input history.
 *
 * `/keys set KEY VALUE` is the documented secret form. `/provider add <name>
 * <url> <key>` is NOT: `addProvider` (features/settings.ts) reads only the
 * name and URL and always prompts for the key through a masked editor, so a
 * third token is ignored — but a user may still type it, and it would
 * otherwise be stashed in the session's recalled history. Both subcommands are
 * matched case-insensitively (splitSub lowercases the head).
 */
export function commandCarriesSecret(command: ParsedCommand | undefined): boolean {
	if (!command) return false;
	const [sub, rest] = splitSub(command.args);
	if (command.name === "keys") return sub === "set";
	return command.name === "provider" && sub === "add" && rest.split(/\s+/).filter(Boolean).length > 2;
}

export class CommandController {
	private readonly host: CommandHost;
	/** `/skills` Menu dialog (A5, spec §12.3): mounted component for dedup. */
	private skillsMenu: SkillsMenu | undefined;

	constructor(host: CommandHost) {
		this.host = host;
	}

	/** Read-only compatibility view; the mounted menu remains controller-owned. */
	get mountedSkillsMenu(): SkillsMenu | undefined {
		return this.skillsMenu;
	}

	async submit(raw: string): Promise<void> {
		const images = this.host.imageState();
		const waitingForImages = images.pending > 0;
		const epoch = images.epoch;
		await images.settled;
		if (epoch !== this.host.imageState().epoch || this.host.isExiting()) return;
		const text = (waitingForImages ? this.host.editor.getExpandedText() : raw).trim();
		if (!text) return;

		// AH-031: parse FIRST, then guard only conflicting operations.
		// Read-only / navigation commands must work during a turn; only
		// new prompts and session-mutating commands on the in-flight
		// session are rejected.
		const command = parseCommand(text);
		if (command && /\[image \d+\]/.test(text)) {
			this.host.print("Send image attachments with a message, rather than a slash command.", "warning");
			return;
		}
		const current = this.host.current();
		const inFlight = current !== undefined && this.host.isTurnRunning(current.id);
		if (!command && current?.parentSessionId && !current.isFork && !current.harnessPlatform) {
			this.host.transcriptNotice("Subagent sessions are for inspection. Return to the main session to send a message.", "info");
			this.host.tui.requestRender();
			return;
		}
		if (!command && inFlight && current) {
			if (this.host.imageState().pending || /\[image \d+\]/.test(text)) {
				this.host.print("Queued steering currently accepts text. Keep the image for the next turn.", "info");
				return;
			}
			await this.host.queueSteering(text);
			return;
		}
		switch (guardSubmit(command, Boolean(inFlight))) {
			case "turn-busy":
				this.host.transcriptNotice("The previous turn is still finishing. Try again shortly.", "warning");
				this.host.tui.requestRender();
				return;
			case "stop-first":
				this.host.transcriptNotice("Stop the running reply before changing this session.", "warning");
				this.host.tui.requestRender();
				return;
			case "allow":
				break;
		}

		let draft: ReturnType<ImageDraft["prepare"]>;
		try {
			const last = this.host.lastPrompt();
			if (text === last.text && this.host.current()?.id === last.sessionId && last.images.length) {
				// Editor history contains markers only. Re-edit the original
				// inline bytes instead of reading a possibly changed file.
				const replay = new ImageDraft();
				replay.addMany(last.images);
				this.host.setImageDraft(replay);
			}
			draft = this.host.imageDraft().prepare(text);
		}
		catch (error) {
			this.host.print(message(error), "error");
			return;
		}
		if (!command && !this.host.current()) {
			try {
				// The chat is still a client-side draft. Materialize it only
				// now that there is an actual message to save.
				await this.host.ensureSession(draft.text);
			} catch (error) {
				this.host.print(message(error), "error");
				return;
			}
		}
		const submittedImages = this.host.imageDraft();
		this.host.setImageDraft(new ImageDraft());
		const submittedEpoch = this.host.bumpImageEpoch();
		const submittedSessionId = this.host.current()?.id;

		this.host.editor.setText("");

		// Never keep secrets in input history: `/keys set KEY VALUE` carries a
		// raw value, and the unsupported `/provider add <name> <url> <key>`
		// form carries one in its ignored third token (see commandCarriesSecret).
		if (!commandCarriesSecret(command)) this.host.editor.addToHistory(draft.text);
		if (command) {
			this.host.setCommandRunning(true);
			try {
				await this.host.runCommand(command);
			} finally {
				this.host.setCommandRunning(false);
			}
		} else {
			this.host.rememberPrompt(draft.text, draft.images, submittedSessionId);
			try {
				await this.host.submitTurn(draft.text, draft.images);
			} catch (error) {
				// Restore the draft on failure. When the guard was released
				// the failure preceded acceptance and retry is meaningful;
				// when it is retained the dispatch outcome is unknown, so say
				// so instead of implying a retry is safe.
				const unknownDispatch = submittedSessionId ? this.host.isInFlight(submittedSessionId) : false;
				if (!this.host.editor.getText() && !unknownDispatch
					&& this.host.current()?.id === submittedSessionId
					&& this.host.imageState().epoch === submittedEpoch) {
					this.host.setImageDraft(submittedImages);
					this.host.editor.setText(text);
				}
				this.host.setRunning(this.host.visibleInFlight());
				this.host.transcriptNotice(
					unknownDispatch
						? `${message(error)} (dispatch state unknown; retry may duplicate work)`
						: message(error),
					unknownDispatch ? "warning" : "error",
				);
			}
		}
		this.host.tui.requestRender();
	}

	/** Shell-job actions for the activity bar: newest running job, else last. */
	commandActions(): { label: string; run: () => void }[] {
		const sessionId = this.host.current()?.id;
		if (!sessionId) return [];
		const jobs = this.host.activity.listJobs(sessionId);
		const job = jobs.find(item => item.status === "running" && item.foreground)
			?? jobs.find(item => item.status === "running")
			?? jobs.at(-1);
		if (!job) return [];
		const run = (action: "background" | "stop" | "output") => {
			void this.commandAction(sessionId, job.id, action);
		};
		return [
			...(job.status === "running" && job.foreground ? [{ label: "Background", run: () => run("background") }] : []),
			{ label: "Output", run: () => run("output") },
			...(job.status === "running" ? [{ label: "Stop", run: () => run("stop") }] : []),
		];
	}

	async commandAction(sessionId: string, jobId: string, action: "background" | "stop" | "output"): Promise<void> {
		try {
			if (action === "output") {
				await this.host.inspectShellJob(sessionId, jobId);
				return;
			}
			const { job } = await this.host.request<{ job: ShellJob }>("shell.jobs", {
				sessionId,
				jobId,
				[action]: true,
			});
			this.host.activity.setJob(sessionId, job);
			if (action === "background" && job.deadline) {
				this.host.print("Command moved to background; its original execution deadline still applies.", "info");
			}
		} catch (error) {
			this.host.print(`Command control failed: ${message(error)}`, "error");
		} finally {
			this.host.syncWorkspaceShell();
			this.host.tui.requestRender();
		}
	}

	/**
	 * `/skills`: a bounded, bottom-anchored Menu dialog over `skills.search`,
	 * with `skills.show` details and a call-free `skills.preview_route` view
	 * (the same `openDialog` + `Menu` pattern as the theme menu). The menu
	 * holds no authority: delete is routed back through the legacy
	 * `/skills delete` command (same confirmation/authorization), and preview
	 * cannot start a turn, call a provider, run tools, install skills or
	 * change approvals.
	 */
	openSkillsPicker(initialQuery = ""): void {
		if (this.skillsMenu) return;
		void this.host.openDialog<{ delete: string }>((close) => {
			const menu = new SkillsMenu(
				{
					search: (params) => this.skillsSearch(params),
					catalog: (filters) => this.skillsCatalog(filters),
					details: (skillId) => this.skillsDetails(skillId),
					preview: (request) => this.skillsPreview(request),
					section: (params) => this.skillsSection(params),
				},
				{
					initialQuery,
					title: "Skills",
					tui: this.host.tui,
					visibleRows: DEFAULT_MENU_VISIBLE_ROWS,
					onChange: () => this.host.tui.requestRender(),
					onClose: () => close(),
					onDelete: (skillName) => close({ delete: skillName }),
				},
			);
			this.skillsMenu = menu;
			menu.activate();
			return menu;
		}).then((result) => {
			this.skillsMenu = undefined;
			// Keep the legacy command as the only delete path: same
			// confirmation, same authorization, same RPC as `/skills delete`.
			if (result?.delete === undefined) return;
			const command = parseCommand(`/skills delete ${result.delete}`);
			if (command) void this.host.runCommand(command);
		});
	}

	/** `skills.search` binding: the pane's closed filter object goes as-is. */
	private skillsSearch(params: SkillsSearchParams): Promise<SkillsSearchResult> {
		return this.host.request<SkillsSearchResult>("skills.search", {
			query: params.query,
			filters: params.filters,
			limit: params.limit,
			...(params.cursor ? { cursor: params.cursor } : {}),
		});
	}

	/**
	 * Empty-query browse for the skills menu: the full catalog (`skills.list`,
	 * the legacy authorization-guarded handler) shaped like a search result so
	 * opening `/skills` lists every skill immediately. Disabled skills are
	 * shown honestly (applicability `excluded`, reason `disabled`) — browsing
	 * intentionally surfaces them; the automatic selector never does.
	 */
	private async skillsCatalog(filters: SkillsFilters = {
		task_family: "",
		capability: "",
		source: [],
		enabled: null,
		include_excluded: true,
	}): Promise<SkillsSearchResult> {
		const { skills } = await this.host.request<{
			skills: Array<{ name: string; description: string; sourceType?: string; enabled?: boolean }>;
		}>("skills.list", { filters: { ...filters, source: [...filters.source] } });
		const candidates: SkillsCandidate[] = (Array.isArray(skills) ? skills : []).map((skill) => {
			const enabled = skill.enabled !== false;
			return {
				skillId: skill.name,
				name: skill.name,
				contentRevision: "",
				score: 0,
				scoreComponents: {},
				matchedPhrases: [],
				matchedCapabilities: [],
				applicability: enabled ? "applicable" : "excluded",
				reasons: enabled ? [] : ["disabled"],
				workflowStages: [],
				explanation: "catalog browse",
				sourceType: skill.sourceType ?? "",
				trust: skill.sourceType === "builtin-ecc" ? "bundled" : "local",
				enabled,
			};
		});
		return {
			query: "",
			filters: { ...filters, source: [...filters.source] },
			limit: candidates.length,
			browse: true,
			candidates,
			catalogRevision: "",
			queryDescriptorRevision: "",
			policyRevision: "",
			nextCursor: null,
			diagnostics: {},
		};
	}

	/** Details read: the legacy `skills.show` contract (case-insensitive id). */
	private async skillsDetails(skillId: string): Promise<SkillsDetailsSkill> {
		const { skill } = await this.host.request<{ skill: SkillsDetailsSkill }>("skills.show", { name: skillId });
		return skill;
	}

	/** Call-free route preview: no turn, provider, tool, install or approval. */
	private skillsPreview(request: string): Promise<SkillsPreviewResult> {
		return this.host.request<SkillsPreviewResult>("skills.preview_route", { request, context: {} });
	}

	/** Version-bound section read: exact passage for the shown revision (§12.2). */
	private skillsSection(params: SkillsSectionParams): Promise<SkillsSectionResult> {
		return this.host.request<SkillsSectionResult>("skills.section", {
			skillId: params.skillId,
			...(params.contentRevision ? { contentRevision: params.contentRevision } : {}),
			...(params.section ? { section: params.section } : {}),
			...(params.offset !== undefined ? { offset: params.offset } : {}),
			budget: params.budget ?? 800,
		});
	}
}
