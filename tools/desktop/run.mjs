import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import {previewExecutable,publishLockedPreview,rememberPreview} from './artifacts.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const windows = process.platform === "win32";
const mode = process.argv[2] ?? "dev";
if (!["dev", "build", "preview"].includes(mode)) throw new Error("Use dev, build, or preview.");
const python = process.env.MACHINA_PYTHON || join(root, ".venv", windows ? "Scripts/python.exe" : "bin/python");
if (!existsSync(python)) throw new Error("Create the repository .venv first, or set MACHINA_PYTHON to the engine interpreter.");
const env = { ...process.env, MACHINA_PYTHON: python, MACHINA_REPO: root, PATH: join(homedir(), ".cargo", "bin") + delimiter + (process.env.PATH ?? "") };
let executable; let args;
if (mode === "preview") {
  executable = previewExecutable(root,windows);
  if (!existsSync(executable)) throw new Error("Build the engineering preview first with npm run desktop:native.");
  args = [];
} else {
  const require = createRequire(import.meta.url);
  const cli = join(dirname(require.resolve("@tauri-apps/cli")), "tauri.js");
  executable = process.execPath;
  args = [cli, mode === "build" ? "build" : "dev", ...(mode === "build" ? ["--no-bundle"] : []), "--features", "dev-engine"];
}
const startedAt=Date.now();let diagnostic='';
const child = spawn(executable, args, { cwd: join(root, "apps/desktop"), env, stdio: mode==='build'?['inherit','inherit','pipe']:"inherit", shell: false, windowsHide: true });
if(child.stderr)child.stderr.on('data',chunk=>{process.stderr.write(chunk);diagnostic=(diagnostic+String(chunk)).slice(-65_536)});
child.on("error", error => { console.error(error.message); process.exitCode = 1; });
child.on("close", (code, signal) => {
  if(mode==='build'&&windows&&code===1){
    const preview=publishLockedPreview(root,startedAt,diagnostic);
    if(preview){console.log(`Native preview built: ${preview}\nThe older preview remains running. Launch this build with npm run desktop:preview.`);process.exitCode=0;return;}
  }
  if(mode==='build'&&code===0){
    const path=join(root,'apps/desktop/src-tauri/target/release',windows?'machina-desktop.exe':'machina-desktop');
    if(existsSync(path))rememberPreview(root,path);
  }
  process.exitCode = code ?? (signal ? 1 : 0);
});
