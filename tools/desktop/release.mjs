import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, delimiter, join, resolve } from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(import.meta.url);
const cli = join(dirname(require.resolve("@tauri-apps/cli")), "tauri.js");
const python = process.env.MACHINA_PYTHON || (process.platform === "win32" ? "python" : "python3");

// This builds only the native desktop package. The terminal UI is neither
// invoked nor bundled; the released app starts the Machina Python gateway
// directly from the user's installed engine.
const child = spawn(process.execPath, [cli, "build", "--features", "dev-engine"], {
  cwd: join(root, "apps/desktop"),
  env: {
    ...process.env,
    MACHINA_PYTHON: python,
    MACHINA_REPO: root,
    PATH: join(homedir(), ".cargo", "bin") + delimiter + (process.env.PATH ?? ""),
  },
  stdio: "inherit",
  shell: false,
  windowsHide: true,
});
child.on("error", error => { console.error(error.message); process.exitCode = 1; });
child.on("close", (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0); });
