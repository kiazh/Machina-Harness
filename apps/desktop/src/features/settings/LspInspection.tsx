import {useMemo} from 'react';
import type {WorkspaceIntelligenceResult} from '@machina/contracts/workspace';
import {workspaceContains} from '../../state/workspacePreference.ts';
import {inspectionResult,type InspectionLocation} from './lspResults.ts';
import './lspInspection.css';

const titles:Record<string,string>={diagnostics:'Problems',symbols:'File symbols',hover:'Symbol information',definition:'Definition',references:'References'};
function pathLabel(root:string,uri:string):string {
 if(!workspaceContains(root,uri))return uri;
 try{const url=new URL(uri);const path=decodeURIComponent((url.hostname?'//'+url.hostname:'')+url.pathname).replace(/^\/(?=[a-z]:\/)/i,'');return path.slice(root.replaceAll('\\','/').replace(/\/$/,'').length+1)||path;}catch{return uri;}
}
const freshness:Record<string,string>={versioned:'Matches the inspected document version.',unversioned:'The server did not confirm a document version. These results may not match the current buffer.',pending:'Results are pending while the language server changes state.',stale:'Results are stale. Request fresh results to open these locations.',unavailable:'Results unavailable. Check the language server state and try again.'};
function emptyMessage(result:WorkspaceIntelligenceResult):string {
 if(result.freshness==='unavailable'||result.freshness==='pending'||result.freshness==='stale')return 'No results to display.';
 if(result.operation==='diagnostics')return result.result===null?'No diagnostic report has been published for this file.':result.freshness==='versioned'?'No problems reported for this file.':'The server returned no problems; the document version is unconfirmed.';
 return result.operation==='symbols'?'No file symbols returned.':result.operation==='hover'?'No symbol information at this position.':result.operation==='definition'?'No definition at this position.':'No references returned.';
}
export function LspInspection({result,root,disabled,onOpen}:{result:WorkspaceIntelligenceResult;root:string;disabled:boolean;onOpen:(location:InspectionLocation)=>void}) {
 const parsed=useMemo(()=>inspectionResult(result),[result]);
 const fallback=parsed.unsupported.length?JSON.stringify(parsed.unsupported.length===1?parsed.unsupported[0]:parsed.unsupported,null,2):'';
 const fallbackLimit=20000;
 const locationUnavailable=result.freshness==='stale'||result.freshness==='pending'||result.freshness==='unavailable';
 return <section className="lsp-inspection" aria-label={`${titles[result.operation]??'Inspection'} results`}>
  <div className="management-row-heading"><h2>{titles[result.operation]??'Inspection results'}</h2><small>Server {result.state}</small></div>
  <p className="feature-description">{freshness[result.freshness]??result.freshness}</p>
  <p className="lsp-result-meta">{result.source==='editor-buffer'?'Editor buffer':'Disk'}{result.documentVersion!==null?` · Document version ${result.documentVersion}`:''}{result.serverGeneration!==null?` · Server generation ${result.serverGeneration}`:''}</p>
  {(result.truncated||parsed.omitted>0||parsed.displayTruncated)&&<p className="feature-description" role="status">Results truncated.{parsed.omitted>0?` ${parsed.omitted} additional results omitted by the server.`:''}{parsed.displayTruncated?' Display limit reached.':''}</p>}
  {parsed.hover!==undefined&&<pre className="lsp-hover">{parsed.hover.slice(0,20000)}</pre>}{parsed.hover!==undefined&&parsed.hover.length>20000&&<p className="feature-description">Symbol information shortened for display.</p>}
  {parsed.rows.length>0&&<ul className="lsp-result-list">{parsed.rows.map((row,index)=>{
   const target=row.location;const outside=target&&!workspaceContains(root,target.uri);const label=target?`${pathLabel(root,target.uri)}:${target.position.line+1}:${target.position.character+1}`:'';
   return <li key={index} style={{paddingInlineStart:`${Math.min(row.depth,12)*12}px`}}><div className="lsp-result-content">{row.kind&&<span className="lsp-result-kind">{row.kind}</span>}<strong>{row.label}</strong>{row.detail&&<small>{row.detail}</small>}</div><div className="lsp-result-location">{target?<><button aria-label={`Open ${label}`} disabled={disabled||!!outside||locationUnavailable} onClick={()=>onOpen(target)} title={outside?'This location is outside the current workspace.':locationUnavailable?'Request fresh results before opening this location.':`Open ${label}`}>{label}</button>{outside&&<small>Outside current workspace</small>}</>:<small>Location unavailable</small>}</div></li>;
  })}</ul>}
  {!parsed.rows.length&&parsed.hover===undefined&&!parsed.unsupported.length&&<p className="feature-description">{emptyMessage(result)}</p>}
  {parsed.unsupported.length>0&&<details className="lsp-fallback" open><summary>Unsupported result format. Server data</summary><pre className="management-output">{fallback.slice(0,fallbackLimit)}</pre>{fallback.length>fallbackLimit&&<p className="feature-description">Server data shortened for display.</p>}</details>}
 </section>;
}
