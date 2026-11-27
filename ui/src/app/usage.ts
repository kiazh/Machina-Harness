// `/usage` dashboard controller: one full-screen page, one RPC, and refresh
// in place. The page owns rendering; the controller owns the request and the
// dialog lifetime.

import type { Component, TUI } from "@earendil-works/pi-tui";
import type { UsageReportResult } from "../protocol.ts";
import { UsagePage } from "../components/UsagePage.ts";

export interface UsageHost {
	tui: Pick<TUI, "terminal" | "requestRender">;
	request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
	openDialog<T>(create: (close: (value?: T) => void) => Component): Promise<T | undefined>;
}

function localTimezone(): string {
	try {
		return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
	} catch {
		return "UTC";
	}
}

export class UsageController {
	private readonly host: UsageHost;
	private page: UsagePage | undefined;
	private loading = false;

	constructor(host: UsageHost) {
		this.host = host;
	}

	/** Open the dashboard, or refresh it when it is already on screen. */
	open(): void {
		if (this.page) {
			void this.load(this.page);
			return;
		}
		let page!: UsagePage;
		void this.host.openDialog<void>((close) => {
			page = new UsagePage({
				tui: this.host.tui,
				onClose: () => {
					this.page = undefined;
					close();
				},
				onRefresh: () => void this.load(page),
			});
			this.page = page;
			void this.load(page);
			return page;
		});
	}

	private async load(page: UsagePage): Promise<void> {
		if (this.loading) return;
		this.loading = true;
		try {
			const report = await this.host.request<UsageReportResult>("usage.report", {
				timezone: localTimezone(),
				days: 365,
			});
			if (this.page === page) page.setReport(report);
		} catch (error) {
			if (this.page === page) {
				page.setError(error instanceof Error ? error.message : String(error));
			}
		} finally {
			this.loading = false;
		}
	}
}
