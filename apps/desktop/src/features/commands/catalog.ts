import type {DesktopSnapshot} from '../../state/controller.ts';
import {SHORTCUT_COMMANDS,formatShortcut,type Shortcuts,type ShortcutAction} from '../../state/shortcuts.ts';
import type {DesktopPage} from '../../state/commands.ts';
import type {PaletteCommand} from './CommandPalette.tsx';

const pages:Array<[DesktopPage,string,string,string[]]>=[
 ['skills','Skills marketplace','Agent',['skills','capabilities','shop','vercel']],
 ['models','Choose a model','Agent',['models','provider','reasoning','effort']],
 ['agents','Manage agents','Agent',['workers','delegation','subagents']],
 ['context','Context and recall','Agent',['tokens','compress','memory retrieval']],
 ['memory','Search memories','Agent',['remember','forget']],
 ['jobs','Scheduled jobs','Agent',['cron','automation','schedule']],
 ['sessions','Search conversations','Conversations',['history','sessions','resume']],
 ['mcp','Connectors','Connections',['mcp','gmail','firecrawl','servers']],
 ['interop','Harness integrations','Connections',['codex','hermes','opencode','communications']],
 ['auth','Accounts','Connections',['chatgpt','subscription','oauth','sign in']],
 ['providers','Providers','Connections',['base url','custom endpoint']],
 ['keys','API keys','Connections',['secrets','credentials']],
 ['mode','Permissions','Settings',['approvals','sandbox','execution']],
 ['profiles','Profiles','Settings',['persona','preferences']],
 ['lsp','Language servers','Settings',['diagnostics','completion','python']],
 ['usage','Usage','Settings',['cost','tokens']],
 ['status','Workspace status','Workspace',['health','connection']],
 ['help','Command reference','Help',['slash','shortcuts']],
];
export function paletteCommands(state:DesktopSnapshot,bindings:Shortcuts):PaletteCommand[]{
 const workspaceReason=state.connection!=='ready'?'Connect the local engine first.':!state.workspace?'Choose a workspace first.':state.switchingWorkspace?'Wait for the workspace to finish opening.':undefined;
 const active=state.documents.find(document=>document.id===state.activeId);
 const busy=state.running||!!state.backgroundOperation||state.switchingSession||state.switchingWorkspace;
 function reason(action:ShortcutAction):string|undefined{
  if(action==='newSession'&&busy)return 'Finish or stop the active work first.';
  if(action==='interrupt'&&!state.running&&!state.backgroundOperation)return 'No agent work is running.';
  if(['quickOpen','openChanges','save','saveAs','requestCompletion'].includes(action)&&workspaceReason)return workspaceReason;
  if(action==='openChanges'&&state.methods?.size&&(!state.methods.has('workspace.changes')||!state.methods.has('workspace.diff')))return 'Update the local engine to inspect changes.';
  if(action==='save'&&active?.readOnly)return 'This file is read-only. Use Save as to make a copy.';
  if(action==='closeAgentTabs'&&!state.agentIds.length)return 'No worker inspection tabs are open.';
 }
 const shortcutActions=SHORTCUT_COMMANDS.filter(command=>command.id!=='openCommands').map(command=>({id:`shortcut:${command.id}`,label:command.name,group:command.group,shortcut:formatShortcut(bindings[command.id]),disabledReason:reason(command.id),searchTerms:command.id==='openChanges'?['git','diff','review','source control']:command.id==='quickOpen'?['open','file','search']:undefined}));
 const first=['openChanges','quickOpen','newSession','toggleEditor','toggleTerminal','openSettings'];
 shortcutActions.sort((a,b)=>{const ai=first.indexOf(a.id.slice(9)),bi=first.indexOf(b.id.slice(9));return (ai<0?99:ai)-(bi<0?99:bi)});
 return [
  ...shortcutActions.slice(0,6),
  {id:'workspace:choose',label:'Open workspace folder',group:'Workspace',description:'Choose the folder used by the agent, editor and terminal.',disabledReason:busy?'Finish or stop active work before switching folders.':undefined,searchTerms:['folder','project','directory']},
  ...pages.map(([page,label,group,searchTerms])=>({id:`page:${page}`,label,group,searchTerms})),
  {id:'conversation:history',label:'Search this conversation',group:'Conversations',searchTerms:['transcript','messages','history'],disabledReason:state.session?undefined:'Open a conversation first.'},
  ...shortcutActions.slice(6),
 ];
}
