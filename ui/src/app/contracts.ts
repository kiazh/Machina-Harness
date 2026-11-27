// Focused host interfaces for the UI controllers.
//
// Each controller owns one concern and receives only the calls it makes on the
// composition root. This is deliberately not an `AppContext`: a controller
// cannot reach a field it did not declare, and every member here has a caller.
import type { Component, SelectItem, TUI } from "@earendil-works/pi-tui";
import type { ActivityStore } from "../activity.ts";
import type { ChangesPage } from "../components/ChangesPage.ts";
import type { Footer } from "../components/Footer.ts";
import type { StatusPage } from "../components/StatusPage.ts";
import type { AskTextOptions, FamilySession, NoticeKind } from "../features/index.ts";
import type { SessionInfo } from "../protocol.ts";
import type { TurnSessionStore } from "../turnSessionStore.ts";

/** The shell screen a command-strip tab selects. */
export type ScreenView = "chat" | "status" | "changes";

/** Structured logger methods shared by the controllers. */
export interface LogSink {
	readonly enabled: boolean;
	error(message: string, fields?: Record<string, unknown>): void;
	warn(message: string, fields?: Record<string, unknown>): void;
	info(message: string, fields?: Record<string, unknown>): void;
	debug(message: string, fields?: Record<string, unknown>): void;
	trace(message: string, fields?: Record<string, unknown>): void;
}

/**
 * Activity/status controller host.
 *
 * Invariants:
 * - `screen`/`setScreen` are the single shell view state; the controller never
 *   keeps a second copy.
 * - `request` is the App's validated/logged gateway call, not the raw client.
 * - `refreshChanges`/`ensureChangesDiff` belong to the Changes controller; the
 *   screen stack asks the composition root so it does not depend on it.
 */
export interface StatusHost {
	readonly tui: TUI;
	readonly log: LogSink;
	readonly footer: Footer;
	readonly activity: ActivityStore;
	readonly turnSession: TurnSessionStore;

	request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
	current(): SessionInfo | undefined;
	familyRows(): FamilySession[];
	screen(): ScreenView;
	setScreen(view: ScreenView): void;
	setShellPage(page: Component | undefined): void;
	statusPage(): StatusPage;
	changesPage(): ChangesPage;

	changesCount(): number;
	changesError(): string;
	setChangesError(text: string): void;
	syncChangesSelection(): void;
	ensureChangesDiff(retry?: boolean): void;
	refreshChanges(): void;

	refreshFamily(): void;
	showApprovalDock(): void;
	focusChat(): void;
	hasVisibleDialog(): boolean;
	quickOpenActive(): boolean;
	isExiting(): boolean;

	print(text: string, kind?: NoticeKind): void;
	pickCentered(title: string, items: SelectItem[]): Promise<SelectItem | undefined>;
	askText(options: AskTextOptions): Promise<string | undefined>;
	commandAction(sessionId: string, jobId: string, action: "background" | "stop" | "output"): Promise<void>;
	flushSteering(sessionId: string): Promise<void>;
	openFamily(sessionId: string): Promise<void>;
	openDialog<T>(create: (close: (value?: T) => void) => Component): Promise<T | undefined>;
}

/**
 * Changes/review/fix controller host.
 *
 * Invariants:
 * - `current` is the visible chat; every changes read is fenced by its id.
 * - `queueSteering` and `submitTurn` are the only delivery paths, so Fix in
 *   Chat keeps the running-turn queue semantics.
 * - `closeScreen` returns to Chat without resolving approvals.
 */
export interface ChangesHost {
	readonly tui: TUI;
	readonly log: LogSink;
	readonly turnSession: TurnSessionStore;

	request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
	current(): SessionInfo | undefined;
	changesPage(): ChangesPage;
	queueSteering(text: string, fromDraft?: boolean): Promise<boolean>;
	submitTurn(text: string): Promise<void>;
	print(text: string, kind?: NoticeKind): void;
	closeScreen(): void;
	transcriptNotice(text: string, kind: NoticeKind): void;
}
