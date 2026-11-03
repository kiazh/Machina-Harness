import { useCallback, useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { DesktopController } from "../state/controller.ts";

export function useDesktopLifecycle(controller: DesktopController,hasShells=false) {
  const [confirmClose, setConfirmClose] = useState(false);
  const [closing, setClosing] = useState(false);
  const release = useRef<() => void>(() => {});
  const close = useCallback(async () => {
    setClosing(true);
    try {
      await controller.recovery?.flush();
      release.current();
      await getCurrentWindow().close();
    } catch (error) { controller.fail(error); setClosing(false); }
  }, [controller]);
  useEffect(() => {
    const visibility = () => { if (document.visibilityState === "hidden") void controller.recovery?.flush().catch(error => controller.fail(error)); };
    document.addEventListener("visibilitychange", visibility);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!isTauri() && (controller.snapshot().dirty.size||controller.snapshot().pendingAttachments||controller.snapshot().backgroundOperation)) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    let disposed = false;
    if (isTauri()) void getCurrentWindow().onCloseRequested(event => {
      event.preventDefault();
      const state = controller.snapshot();
      if (state.pendingAttachments||hasShells||state.running||state.backgroundOperation || state.jobs.some(job => job.status === "running")) setConfirmClose(true);
      else void close();
    }).then(unlisten => { if (disposed) unlisten(); else release.current = unlisten; }).catch(error => controller.fail(error));
    return () => { disposed = true; release.current(); document.removeEventListener("visibilitychange", visibility); window.removeEventListener("beforeunload", beforeUnload); };
  }, [controller, close,hasShells]);
  return { confirmClose, closing, cancelClose: () => setConfirmClose(false), close };
}
