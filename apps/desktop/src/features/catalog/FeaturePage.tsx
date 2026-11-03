import {useSyncExternalStore} from 'react';
import type {DesktopController} from '../../state/controller.ts';
import {FEATURE_COMMANDS,PAGE_LABELS,type DesktopPage} from '../../state/commands.ts';
import {ConnectionControls} from './ConnectionControls.tsx';
import {MemoryPage} from '../memory/MemoryPage.tsx';
import {JobsPage} from '../jobs/JobsPage.tsx';
import {AgentsPage} from '../agents/AgentsPage.tsx';
import {ContextPage} from '../context/ContextPage.tsx';
import {SkillsPage} from './SkillsPage.tsx';
import {McpPage} from './McpPage.tsx';
import {ModelsPage} from './ModelsPage.tsx';
import {AccountsPage} from './AccountsPage.tsx';
import {EnginePreferences} from '../settings/EnginePreferences.tsx';
import {PermissionsPage} from '../settings/PermissionsPage.tsx';
import {ProfilesPage} from '../settings/ProfilesPage.tsx';
import {InteropPage,MusicPage,GrowthPage} from '../settings/CompanionPages.tsx';
import {LanguageServersPage} from '../settings/LanguageServersPage.tsx';
import {MemoryReviews} from '../memory/MemoryReviews.tsx';
import {SessionsPage} from '../sessions/SessionsPage.tsx';
import {StatusPage,UsagePage} from '../shared/OverviewPages.tsx';
import {FeatureFrame} from '../shared/FeatureFrame.tsx';
import {useEnginePage} from '../shared/useEnginePage.ts';

export function FeaturePage({controller,pageOverride,onDone,initialQuery}:{controller:DesktopController;pageOverride?:DesktopPage;onDone?:()=>void;initialQuery?:string}) {
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);
 const page=pageOverride??state.page;const key=state.session?.id??'none';
 switch(page){
 case 'memory':return <MemoryPage controller={controller}/>;
 case 'jobs':return <JobsPage key={key} controller={controller}/>;
 case 'agents':return <AgentsPage key={key} controller={controller}/>;
 case 'context':return <ContextPage key={key} controller={controller}/>;
 case 'skills':return <SkillsPage key={key} controller={controller}/>;
 case 'mcp':return <McpPage key={key} controller={controller}/>;
 case 'models':return <ModelsPage key={key} controller={controller} onDone={onDone}/>;
 case 'auth':return <AccountsPage controller={controller}/>;
 case 'preferences':return <EnginePreferences controller={controller} initialQuery={initialQuery}/>;
 case 'profiles':return <ProfilesPage controller={controller}/>;
 case 'mode':return <PermissionsPage key={key} controller={controller}/>;
 case 'growth':return <GrowthPage key={key} controller={controller}/>;
 case 'interop':return <InteropPage controller={controller}/>;
 case 'music':return <MusicPage controller={controller}/>;
 case 'lsp':return <LanguageServersPage key={state.workspace?.epoch} controller={controller}/>;
 case 'reviews':return <MemoryReviews controller={controller}/>;
 case 'sessions':return <SessionsPage controller={controller}/>;
 case 'status':return <StatusPage controller={controller}/>;
 case 'usage':return <UsagePage key={key} controller={controller}/>;
 case 'providers':case 'keys':return <ConnectionsPage key={page} page={page} controller={controller}/>;
 case 'help':return <FeatureFrame controller={controller} title="Commands" description="Commands run locally in the client. Unknown commands keep your draft and never reach the model."><div className="feature-command-list">{Object.entries(FEATURE_COMMANDS).filter(([name])=>!['?','h','skill','model','providers','mem','config','persona','harnesses','effort','approvals','problems','profiles','nowplaying','compact','compress','ls'].includes(name)).map(([name,target])=><button key={name} onClick={()=>controller.navigate(target)}><code>/{name}</code><span>{PAGE_LABELS[target]}</span></button>)}</div><p>/history searches recorded messages in the current conversation. /attach adds the selected code to your draft for review. /new starts a conversation. /resume &lt;id&gt; opens one. /review asks the agent to inspect your changes. Feature pages provide the controls for their operations.</p></FeatureFrame>;
 default:return <FeatureFrame controller={controller} title={PAGE_LABELS[page]}><button onClick={()=>controller.navigate('chat')}>Return to conversation</button></FeatureFrame>;
 }
}
function ConnectionsPage({controller,page}:{controller:DesktopController;page:'providers'|'keys'}) {
 const resource=useEnginePage<unknown>(controller,page==='providers'?'providers.list':'secrets.list');
 return <FeatureFrame controller={controller} title={PAGE_LABELS[page]} description={page==='providers'?'Add, edit or remove a connection. The model determines which provider is used.':'Credential values are stored by the local engine and never shown here.'} {...resource} onRefresh={resource.refresh}><ConnectionControls controller={controller} page={page} data={resource.data} onChanged={resource.refresh}/></FeatureFrame>;
}
