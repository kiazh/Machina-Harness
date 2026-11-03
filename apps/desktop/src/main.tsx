import { createRoot } from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { App } from "./app/App.tsx";
import { NativeBridge } from "./bridge/tauri.ts";
import { DesktopController } from "./state/controller.ts";
import { IndexedDbRecovery, RecoveryStore } from "./state/recovery.ts";

const recovery = new RecoveryStore(new IndexedDbRecovery(), error => controller.fail(error));
const controller = new DesktopController(new NativeBridge(), recovery);
await controller.restoreRecovery();
createRoot(document.getElementById("root")!).render(<App controller={controller}/>);
if (isTauri()) void controller.connect();
