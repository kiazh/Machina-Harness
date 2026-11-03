import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FileCode2, FileText, Folder, Search } from "lucide-react";
import type { WorkspaceListEntry } from "@machina/contracts/workspace";
import type { DesktopController } from "../../state/controller.ts";

function Directory({ path, controller, filter, depth = 0 }: { path: string; controller: DesktopController; filter: string; depth?: number }) {
  const [entries, setEntries] = useState<WorkspaceListEntry[]>([]);
  const [expanded, setExpanded] = useState(new Set<string>());
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    void controller.list(path).then(result => { if (active) { setEntries(result.entries); setCursor(result.nextCursor); } }).catch(error => { if (active) setError(String(error)); });
    return () => { active = false; };
  }, [controller, path]);
  if(error&&!entries.length)return <p className="tree-error">{error}</p>;
  const sorted = [...entries].sort((a, b) => Number(b.kind === "directory") - Number(a.kind === "directory") || a.name.localeCompare(b.name));
  return <ul className="file-tree">{sorted.filter(entry => !filter || entry.kind === "directory" || entry.name.toLowerCase().includes(filter.toLowerCase())).map(entry => <li key={entry.path}>
    <button className="tree-row" style={{ paddingLeft: `${14 + depth * 14}px` }} title={entry.path} aria-expanded={entry.kind==='directory'?expanded.has(entry.path):undefined} onClick={() => {
      if (entry.kind === "directory") setExpanded(prior => { const next = new Set(prior); if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path); return next; });
      else void controller.openFile(entry.path).catch(error => controller.fail(error));
    }}>
      {entry.kind === "directory" ? <>{expanded.has(entry.path) ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}<Folder size={15}/></> : <><span className="tree-spacer"/>{/\.(py|[jt]sx?|rs|json)$/.test(entry.name) ? <FileCode2 size={15}/> : <FileText size={15}/>}</>}
      <span>{entry.name}</span>
    </button>
    {entry.kind === "directory" && expanded.has(entry.path) && <Directory path={entry.path} controller={controller} filter={filter} depth={depth + 1}/>}
  </li>)}{error&&<li className="tree-error">{error}</li>}{cursor && <li><button className="tree-row" disabled={loading} onClick={async () => {
    setLoading(true);setError('');
    try { const result = await controller.list(path, cursor); setEntries(prior => [...new Map([...prior, ...result.entries].map(entry => [entry.path, entry])).values()]); setCursor(result.nextCursor); }
    catch (error) { setError(String(error)); }
    finally { setLoading(false); }
  }}>{loading ? "Loading…" : "Load more files"}</button></li>}</ul>;
}

export function Explorer({ controller, ready }: { controller: DesktopController; ready: boolean }) {
  const [filter, setFilter] = useState("");
  return <><div className="explorer-heading"><span>PROJECT</span><Folder size={14}/></div>
    <label className="file-filter"><Search size={14}/><input aria-label="Filter visible files" placeholder="Filter files…" value={filter} onChange={event => setFilter(event.target.value)}/></label>
    <div className="explorer-content">{ready ? <Directory path="" controller={controller} filter={filter}/> : <p className="explorer-empty">Connect the local engine to browse your project.</p>}</div>
  </>;
}
