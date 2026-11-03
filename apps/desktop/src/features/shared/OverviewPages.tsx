import type {StatusResult,UsageResult} from '@machina/contracts/rpc';
import type {DesktopController} from '../../state/controller.ts';
import {FeatureFrame} from './FeatureFrame.tsx';
import {useEnginePage} from './useEnginePage.ts';
export function StatusPage({controller}:{controller:DesktopController}) {
 const page=useEnginePage<StatusResult>(controller,'status');
 return <FeatureFrame controller={controller} title="Workspace status" {...page} onRefresh={page.refresh}>{page.data&&<><dl className="feature-data">{[['Database',page.data.postgres],['Provider',page.data.provider],['Model',page.data.model],['Saved conversations',page.data.sessions],['Context chunks',page.data.contextChunks],['Memories',page.data.memories],['Pending memories',page.data.pendingMemories]].map(([label,value])=><div key={String(label)}><dt>{label}</dt><dd>{String(value??'Not reported')}</dd></div>)}</dl><h2>Registered tools</h2><p className="memory-content management-muted">{page.data.tools.join(', ')||'No tools registered.'}</p><div className="management-actions"><button onClick={()=>controller.navigate('reviews')}>Review pending memories</button><button onClick={()=>controller.navigate('lsp')}>Language servers</button><button onClick={()=>controller.navigate('mcp')}>MCP connectors</button></div></>}</FeatureFrame>;
}
export function UsagePage({controller}:{controller:DesktopController}) {
 const page=useEnginePage<UsageResult>(controller,'usage.get',true);
 return <FeatureFrame controller={controller} title="Usage" description="Engine accounting for this conversation and its agent. Unknown provider usage is shown separately." {...page} onRefresh={page.refresh}>{!page.sessionId&&<p>Open a conversation to inspect usage.</p>}{page.data&&<div className="usage-tables">{(['session','agent'] as const).map(scope=><section key={scope}><h2>{scope==='session'?'This conversation':'This agent'}</h2><dl className="feature-data">{Object.entries(page.data![scope]).map(([key,value])=><div key={key}><dt>{key.replace(/([a-z])([A-Z])/g,'$1 $2')}</dt><dd>{value===null?'Not reported / unlimited':typeof value==='number'?value.toLocaleString():String(value)}</dd></div>)}</dl></section>)}</div>}</FeatureFrame>;
}
