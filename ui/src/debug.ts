// Opt-in structured UI log for debugging and tests.
//
// Off by default and free when off: no strings are built unless a scoped
// logger is enabled. Enable with `MACHINA_UI_LOG=<path>` or
// `MACHINA_UI_DEBUG=1` (defaults to `<MACHINA_HOME>/logs/ui.log`), and set
// `MACHINA_UI_LOG_LEVEL=error|warn|info|debug|trace` (default `debug`).
//
// Records are one JSON object per line. Secrets are redacted by field name,
// long strings are clipped, and the file rotates at 5 MB.

import { closeSync, mkdirSync, openSync, renameSync, rmSync, statSync, writeSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export type LogLevel = "error" | "warn" | "info" | "debug" | "trace";

const LEVELS: Record<LogLevel, number> = { error: 0, warn: 1, info: 2, debug: 3, trace: 4 };
const MAX_FIELD_CHARS = 800;
const MAX_ARRAY_ITEMS = 20;
const MAX_OBJECT_KEYS = 30;
const MAX_DEPTH = 4;
const MAX_BYTES = 5 * 1024 * 1024;
const SECRET_KEY = /(api[-_]?key|token|secret|password|passwd|authorization|cookie|credential|private[-_]?key)$/i;
const SECRET_TEXT = /(bearer\s+|(?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|password|passwd)\s*[:=]\s*["']?)([^\s"',;}]+)/gi;
const PRIVATE_METHOD = /(^|\.)(ask|question|prompt|chat|feedback|memory|growth|keys?|secret|auth|credential|oauth|provider\.(add|key))/i;
const PRIVATE_KEYS = new Set(["value", "text", "code", "answer", "prompt", "message", "content", "password", "token", "key", "secret"]);

function scrubSecrets(text: string): string {
	return text.replace(SECRET_TEXT, "$1[redacted]");
}

/** Log parameters with request-shaping context; user content and secrets stay out. */
export function redactParamsForLog(method: string, params: Record<string, unknown>): Record<string, unknown> {
	if (!PRIVATE_METHOD.test(method)) return params;
	const out: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(params)) {
		out[key] = PRIVATE_KEYS.has(key) || SECRET_KEY.test(key) ? "[redacted]" : value;
	}
	return out;
}

/** Redact by key, clip long values, and bound depth/width for JSON output. */
export function redactLogValue(value: unknown, depth = 0): unknown {
	if (typeof value === "string") {
		const scrubbed = scrubSecrets(value);
		return scrubbed.length > MAX_FIELD_CHARS ? `${scrubbed.slice(0, MAX_FIELD_CHARS)}…(${scrubbed.length} chars)` : scrubbed;
	}
	if (value === null || typeof value !== "object") return value;
	if (depth >= MAX_DEPTH) return "[depth]";
	if (Array.isArray(value)) {
		return value.slice(0, MAX_ARRAY_ITEMS).map((item) => redactLogValue(item, depth + 1));
	}
	const record = value as Record<string, unknown>;
	const out: Record<string, unknown> = {};
	for (const [key, entry] of Object.entries(record).slice(0, MAX_OBJECT_KEYS)) {
		out[key] = SECRET_KEY.test(key) ? "[redacted]" : redactLogValue(entry, depth + 1);
	}
	return out;
}

export function describeError(error: unknown): { name: string; message: string; stack?: string } {
	if (error instanceof Error) {
		return {
			name: error.name,
			message: error.message,
			...(error.stack ? { stack: error.stack } : {}),
		};
	}
	return { name: typeof error, message: String(error) };
}

function defaultLogPath(env: NodeJS.ProcessEnv): string {
	const home = (env.MACHINA_HOME ?? "").trim() || join(homedir(), ".machina");
	return join(home, "logs", "ui.log");
}

function expandHome(path: string): string {
	return path.startsWith("~") ? join(homedir(), path.slice(1)) : path;
}

export interface DebugLogOptions {
	path: string;
	level?: LogLevel;
	maxBytes?: number;
}

export class DebugLog {
	private fd: number | undefined;
	private path: string | undefined;
	private level: LogLevel = "info";
	private maxBytes = MAX_BYTES;
	private written = 0;
	private readonly pid = process.pid;

	get enabled(): boolean {
		return this.fd !== undefined;
	}

	get target(): string | undefined {
		return this.path;
	}

	configure(options: DebugLogOptions): void {
		this.close();
		const path = expandHome(options.path);
		this.level = options.level && Object.hasOwn(LEVELS, options.level) ? options.level : "debug";
		this.maxBytes = Math.max(1024, options.maxBytes ?? MAX_BYTES);
		try {
			mkdirSync(dirname(path), { recursive: true });
			this.rotateIfNeeded(path);
			this.fd = openSync(path, "a", 0o600);
			this.path = path;
			this.written = 0;
		} catch {
			this.fd = undefined;
			this.path = undefined;
		}
	}

	/** Configure from the environment. Returns true when logging was enabled. */
	configureFromEnv(env: NodeJS.ProcessEnv = process.env): boolean {
		const requested = (env.MACHINA_UI_LOG ?? "").trim();
		const debug = /^(1|true|yes)$/i.test((env.MACHINA_UI_DEBUG ?? "").trim());
		if (!requested && !debug) return false;
		const levelText = (env.MACHINA_UI_LOG_LEVEL ?? "").trim().toLowerCase();
		const level = Object.hasOwn(LEVELS, levelText) ? (levelText as LogLevel) : "debug";
		const path = !requested || /^(1|true|yes)$/i.test(requested) ? defaultLogPath(env) : requested;
		this.configure({ path, level });
		return this.enabled;
	}

	scope(scope: string): ScopedLog {
		return new ScopedLog(this, scope);
	}

	write(scope: string, level: LogLevel, message: string, fields?: Record<string, unknown>): void {
		const fd = this.fd;
		if (fd === undefined || LEVELS[level] > LEVELS[this.level]) return;
		const record: Record<string, unknown> = {
			ts: new Date().toISOString(),
			pid: this.pid,
			level,
			scope,
			msg: message,
		};
		if (fields !== undefined) record.data = redactLogValue(fields);
		let line: string;
		try {
			line = `${JSON.stringify(record)}\n`;
		} catch {
			line = `${JSON.stringify({ ts: record.ts, pid: this.pid, level, scope, msg: message, data: "[unserializable]" })}\n`;
		}
		try {
			writeSync(fd, line);
			this.written += Buffer.byteLength(line);
			if (this.written >= this.maxBytes) this.rotate();
		} catch {
			// A broken log file must never break the UI. Disable until reconfigured.
			this.close();
		}
	}

	close(): void {
		if (this.fd !== undefined) {
			try { closeSync(this.fd); } catch { /* already gone */ }
		}
		this.fd = undefined;
		this.path = undefined;
		this.written = 0;
	}

	/** Test seam: close and forget configuration. */
	reset(): void {
		this.close();
		this.level = "info";
		this.maxBytes = MAX_BYTES;
	}

	private rotateIfNeeded(path: string): void {
		try {
			if (statSize(path) >= this.maxBytes) this.rotateFile(path);
		} catch {
			// absent file: nothing to rotate
		}
	}

	private rotate(): void {
		const path = this.path;
		if (path === undefined) return;
		try { if (this.fd !== undefined) closeSync(this.fd); } catch { /* ignore */ }
		this.fd = undefined;
		this.rotateFile(path);
		try {
			this.fd = openSync(path, "a", 0o600);
			this.written = 0;
		} catch {
			this.path = undefined;
		}
	}

	private rotateFile(path: string): void {
		try { rmSync(`${path}.1`, { force: true }); } catch { /* ignore */ }
		try { renameSync(path, `${path}.1`); } catch { /* ignore */ }
	}
}

function statSize(path: string): number {
	return statSync(path).size;
}

export class ScopedLog {
	private readonly owner: DebugLog;
	readonly scope: string;

	constructor(owner: DebugLog, scope: string) {
		this.owner = owner;
		this.scope = scope;
	}

	get enabled(): boolean {
		return this.owner.enabled;
	}

	error(message: string, fields?: Record<string, unknown>): void {
		this.owner.write(this.scope, "error", message, fields);
	}

	warn(message: string, fields?: Record<string, unknown>): void {
		this.owner.write(this.scope, "warn", message, fields);
	}

	info(message: string, fields?: Record<string, unknown>): void {
		this.owner.write(this.scope, "info", message, fields);
	}

	debug(message: string, fields?: Record<string, unknown>): void {
		this.owner.write(this.scope, "debug", message, fields);
	}

	trace(message: string, fields?: Record<string, unknown>): void {
		this.owner.write(this.scope, "trace", message, fields);
	}

	child(suffix: string): ScopedLog {
		return new ScopedLog(this.owner, `${this.scope}.${suffix}`);
	}
}

/** Process-wide UI log; no-op until configured. */
export const uiLog = new DebugLog();
