import {Select} from '../components/Select.tsx';
import { useRef, useState, useSyncExternalStore, type RefObject } from "react";
import { ImagePlus, RotateCcw, X } from "lucide-react";
import { AppearanceStore, PRESETS, wallpaperStorage, type Motion, type Preset } from "../state/appearance.ts";

export function AppearanceDialog({ store, dialog, onWallpaper, embedded=false,view='all' }: { view?:'all'|'appearance'|'background'|'typography';embedded?:boolean; store: AppearanceStore; dialog: RefObject<HTMLDialogElement | null>; onWallpaper: () => void }) {
  const preferences = useSyncExternalStore(store.subscribe, store.snapshot);
  const upload = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const content=<><div className="dialog-heading"><div><div className="eyebrow">MAKE IT YOURS</div><h2 id="appearance-title">{view==='background'?'Wallpaper & surfaces':view==='typography'?'Typography':'Appearance'}</h2></div><button className="icon-button" aria-label="Close appearance settings" onClick={() => dialog.current?.close()}><X size={18}/></button></div>
    <p>{view==='background'?'Choose your backdrop and keep the reading surface clear.':view==='typography'?'Set comfortable type for conversations and code.':'Choose the colors and motion that suit your workspace.'}</p>
    <div hidden={view!=='all'&&view!=='appearance'}>
    <div className="preset-choices" role="group" aria-label="Visual style">{(Object.keys(PRESETS) as Preset[]).map(preset => <button key={preset} className={`preset-choice preset-${preset}`} aria-pressed={preferences.preset === preset} onClick={() => store.select(preset)}><span className="preset-preview" style={{backgroundColor:PRESETS[preset].surface,color:PRESETS[preset].accent}}>M<span>+ · +</span></span><strong>{PRESETS[preset].name}</strong><small>{PRESETS[preset].description}</small></button>)}</div>
    </div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div hidden={view!=='all'&&view!=='background'}><div className="appearance-section"><div className="setting-section-label"><span>BACKGROUND</span><Select aria-label="Background source" value={preferences.background} onChange={event => store.update({ background: event.target.value as "art" | "custom" | "none" })}><option value="art">Machina artwork</option><option value="custom">Your wallpaper</option><option value="none">Solid color</option></Select></div><div className="wallpaper-actions"><button disabled={busy} onClick={() => upload.current?.click()}><ImagePlus size={15}/>{busy ? "Saving wallpaper…" : "Choose your wallpaper"}</button><button title="Remove custom wallpaper" aria-label="Remove custom wallpaper" onClick={async () => { try { await wallpaperStorage(null); store.update({ background: "art" }); onWallpaper(); } catch (error) { setError(String(error)); } }}><RotateCcw size={14}/></button></div>
      <input ref={upload} type="file" hidden accept="image/png,image/jpeg,image/webp,image/avif" aria-label="Upload wallpaper" onChange={async event => {
        const file = event.target.files?.[0]; event.target.value = ""; if (!file) return;
        setBusy(true); setError("");
        try {
          if (file.size > 20 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp", "image/avif"].includes(file.type)) throw new Error("Choose a PNG, JPEG, WebP or AVIF image under 20 MiB.");
          const bitmap = await createImageBitmap(file); const pixels = bitmap.width * bitmap.height; bitmap.close();
          if (pixels > 40_000_000) throw new Error("Choose a wallpaper smaller than 40 megapixels.");
          await wallpaperStorage(file); store.update({ background: "custom" }); onWallpaper();
        } catch (error) { setError(error instanceof Error ? error.message : "Could not open this wallpaper."); }
        finally { setBusy(false); }
      }}/>
    </div>
    <label className="range-setting"><span>Surface opacity <small>{Math.round(preferences.glass * 100)}%</small></span><input aria-label="Surface opacity" type="range" min=".2" max="1" step=".01" value={preferences.glass} onChange={event => store.update({ glass: Number(event.target.value) })}/></label>
    <label className="range-setting"><span>Glass blur <small>{preferences.blur} px</small></span><input aria-label="Glass blur" type="range" min="0" max="32" step="1" value={preferences.blur} onChange={event => store.update({ blur: Number(event.target.value) })}/></label>
    </div><div hidden={view!=='all'&&view!=='appearance'}><label>Accent <input aria-label="Accent color" type="color" value={preferences.accent} onChange={event => store.update({ accent: event.target.value })}/></label>
    <label>Motion <Select aria-label="Motion" value={preferences.motion} onChange={event => store.update({ motion: event.target.value as Motion })}><option value="full">Expressive</option><option value="subtle">Subtle</option><option value="off">Off</option></Select></label>
    </div><div hidden={view!=='all'&&view!=='typography'}><div className="setting-section-label typography-label">TYPOGRAPHY</div>
    <label>Interface font <Select aria-label="Interface font" value={preferences.uiFont} onChange={event => store.update({ uiFont: event.target.value })}>{["Manrope Variable", "Segoe UI", "SF Pro Text", "Arial"].map(font => <option key={font}>{font}</option>)}</Select></label>
    <label>Interface size <Select aria-label="Interface size" value={preferences.uiSize} onChange={event => store.update({ uiSize: Number(event.target.value) })}>{[12, 13, 14, 15, 16, 17, 18].map(value => <option key={value} value={value}>{value} px</option>)}</Select></label>
    <label>Code font <Select aria-label="Code font" value={preferences.codeFont} onChange={event => store.update({ codeFont: event.target.value })}>{["Cascadia Code", "SFMono-Regular", "Consolas", "Menlo", "monospace"].map(font => <option key={font}>{font}</option>)}</Select></label>
    <label>Code size <Select aria-label="Code size" value={preferences.codeSize} onChange={event => store.update({ codeSize: Number(event.target.value) })}>{[11, 12, 13, 14, 15, 16, 17, 18, 20, 22, 24].map(value => <option key={value} value={value}>{value} px</option>)}</Select></label>
    </div><p className="settings-footnote">Saved on this device. System reduced-motion preferences take priority.</p>
  </>;
  return embedded?<div className="appearance-content">{content}</div>:<dialog ref={dialog} className="settings-dialog appearance-dialog" aria-labelledby="appearance-title">{content}</dialog>;
}
