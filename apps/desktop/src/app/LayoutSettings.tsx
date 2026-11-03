import {useSyncExternalStore} from 'react';
import {Check, RotateCcw} from 'lucide-react';
import {LAYOUT_PRESETS, type LayoutPreset, type LayoutStore} from '../state/layout.ts';

export function LayoutSettings({store}:{store:LayoutStore}) {
  const layout=useSyncExternalStore(store.subscribe,store.snapshot);
  return <div className="workspace-settings layout-settings">
    <div className="settings-title-row"><h1>Workspace</h1><button className="settings-reset" onClick={()=>store.reset()}><RotateCcw size={13}/>Reset layout</button></div>
    <p>Arrange the agent, code and command output around your work. Files and drafts stay open when you hide a pane.</p>
    <div className="layout-presets" role="group" aria-label="Workspace layouts">{(Object.keys(LAYOUT_PRESETS) as LayoutPreset[]).map(id=>{
      const preset=LAYOUT_PRESETS[id];
      return <button key={id} aria-pressed={layout.preset===id} onClick={()=>store.select(id)}>
        <span className={`layout-preview layout-preview-${id}`} aria-hidden="true"><i className="layout-preview-sidebar"/><i className="layout-preview-agent"/>{preset.values.editor&&<i className="layout-preview-code">{preset.values.terminal&&<b/>}</i>}</span>
        <span><strong>{preset.name}</strong>{layout.preset===id&&<Check size={13}/>}</span><small>{preset.description}</small>
      </button>;
    })}</div>
    <div className="settings-section-heading"><h2>Panes</h2>{layout.preset==='custom'&&<span>Custom layout</span>}</div>
    <label className="setting-row"><span>Show sidebar<small>Sessions, navigation and project files.</small></span><input aria-label="Show sidebar" type="checkbox" checked={layout.sidebar} onChange={e=>store.update({sidebar:e.target.checked})}/></label>
    <label className="setting-row"><span>Show code editor<small>Buffers and undo history stay intact when hidden.</small></span><input aria-label="Show code editor" type="checkbox" checked={layout.editor} onChange={e=>store.update({editor:e.target.checked})}/></label>
    <label className="setting-row"><span>Show terminal<small>Shells and agent output in the bottom dock.</small></span><input aria-label="Show terminal" type="checkbox" checked={layout.terminal} onChange={e=>store.update({terminal:e.target.checked})}/></label>
    {!layout.editor&&layout.terminal&&<p className="settings-inline-note">The terminal appears with the editor. Use the Terminal button to open it on its own.</p>}
    <h2>Placement & size</h2>
    <label className="setting-row"><span>Code editor position</span><select aria-label="Code editor position" value={layout.editorSide} onChange={e=>store.update({editorSide:e.target.value as 'left'|'right'})}><option value="right">Right of the agent</option><option value="left">Left of the agent</option></select></label>
    <label className="setting-row range-setting"><span>Sidebar width<small>{Math.round(layout.sidebarWidth)} px</small></span><input aria-label="Sidebar width" type="range" min={168} max={360} step={4} value={layout.sidebarWidth} onChange={e=>store.update({sidebarWidth:Number(e.target.value)})}/></label>
    <label className="setting-row range-setting"><span>Code editor width<small>{Math.round(layout.editorWidth)}% of the window. Drag its divider to adjust.</small></span><input aria-label="Code editor width" type="range" min={25} max={72} value={layout.editorWidth} onChange={e=>store.update({editorWidth:Number(e.target.value)})}/></label>
    <label className="setting-row range-setting"><span>Terminal height<small>{Math.round(layout.terminalHeight)} px, within the space available.</small></span><input aria-label="Terminal height" type="range" min={120} max={480} step={10} value={layout.terminalHeight} onChange={e=>store.update({terminalHeight:Number(e.target.value)})}/></label>
    <label className="setting-row"><span>Navigation density<small>Spacing in the sidebar and file tabs.</small></span><select aria-label="Navigation density" value={layout.density} onChange={e=>store.update({density:e.target.value as 'compact'|'comfortable'})}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
    <p className="settings-footnote">Saved on this device. The agent keeps a minimum reading width as the window gets smaller.</p>
  </div>;
}
