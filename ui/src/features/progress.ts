// Delayed progress notice for slow local RPCs. Fast responses stay quiet so
// command output does not get noisier in tests or normal use; once an RPC is
// genuinely slow, the user sees what is happening while it runs.

import type { FeatureHost } from "./types.ts";

export async function withDelayedNotice<T>(
	host: FeatureHost,
	message: string,
	operation: () => Promise<T>,
	delayMs = 250,
): Promise<T> {
	const timer = setTimeout(() => host.print(message, "plain"), delayMs);
	try {
		return await operation();
	} finally {
		clearTimeout(timer);
	}
}
