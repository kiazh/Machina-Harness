import type {LspPosition,LspRange,WorkspaceIntelligenceResult} from '@machina/contracts/workspace';

export interface InspectionLocation {uri:string;position:LspPosition}
export interface InspectionRow {label:string;detail?:string;kind?:string;depth:number;location?:InspectionLocation}
export interface InspectionResult {rows:InspectionRow[];hover?:string;unsupported:unknown[];omitted:number;displayTruncated:boolean}
const MAX_ROWS=1000;
const symbolKinds=['File','Module','Namespace','Package','Class','Method','Property','Field','Constructor','Enum','Interface','Function','Variable','Constant','String','Number','Boolean','Array','Object','Key','Null','Enum member','Struct','Event','Operator','Type parameter'];
const record=(value:unknown):Record<string,unknown>|undefined=>typeof value==='object'&&value!==null&&!Array.isArray(value)?value as Record<string,unknown>:undefined;
const text=(value:unknown):string|undefined=>typeof value==='string'?value:undefined;
const count=(value:unknown):number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?value:0;
function position(value:unknown):LspPosition|undefined {
 const item=record(value);if(!item)return;
 if(typeof item.line!=='number'||typeof item.character!=='number'||!Number.isInteger(item.line)||!Number.isInteger(item.character)||item.line<0||item.character<0||item.line>2147483647||item.character>2147483647)return;
 return {line:item.line,character:item.character};
}
function range(value:unknown):LspRange|undefined {
 const item=record(value);const start=position(item?.start),end=position(item?.end);
 if(!start||!end||end.line<start.line||end.line===start.line&&end.character<start.character)return;
 return {start,end};
}
function location(value:unknown):InspectionLocation|undefined {
 const item=record(value);if(!item)return;
 const uri=text(item.targetUri)??text(item.uri);
 const target=typeof item.targetUri==='string'?range(item.targetSelectionRange)??range(item.targetRange):range(item.range);
 if(uri&&target)return {uri,position:target.start};
}
function hoverText(value:unknown):string|undefined {
 if(typeof value==='string')return value;
 const item=record(value);if(item&&typeof item.value==='string')return item.value;
 if(Array.isArray(value)){const parts=value.map(part=>typeof part==='string'?part:text(record(part)?.value));if(parts.every(part=>part!==undefined))return parts.join('\n\n');}
}

/** Parse both bounded engine payloads and standard LSP replies without guessing locations. */
export function inspectionResult(result:WorkspaceIntelligenceResult):InspectionResult {
 const parsed:InspectionResult={rows:[],unsupported:[],omitted:0,displayTruncated:false};
 const payload=result.result,item=record(payload);parsed.omitted=count(item?.omitted);
 if(payload===null||payload===undefined)return parsed;
 const unsupported=(value:unknown)=>{if(parsed.unsupported.length<20)parsed.unsupported.push(value);else parsed.displayTruncated=true};
 if(result.operation==='hover'){
  parsed.hover=hoverText(item?.contents??payload);
  if(parsed.hover===undefined)unsupported(payload);
  return parsed;
 }
 if(result.operation==='symbols'){
  // Prefer the hierarchy for DocumentSymbol detail and selection ranges.
  const hierarchical=Array.isArray(item?.hierarchical)?item.hierarchical:undefined;
  const flat=Array.isArray(item?.flat)?item.flat:undefined;
  const entries=Array.isArray(payload)?payload:hierarchical?.length?hierarchical:flat??hierarchical;
  if(!entries){unsupported(payload);return parsed;}
  const stack=entries.map(value=>({value,depth:0})).reverse();
  let visited=0;
  while(stack.length&&visited<MAX_ROWS){
   const {value,depth}=stack.pop()!;visited++;
   const node=record(value);if(!node||typeof node.name!=='string'){unsupported(value);continue;}
   const selection=range(node.selectionRange)??range(node.range);
   const target=location(node.location)??(selection?{uri:text(node.uri)??result.uri,position:selection.start}:undefined);
   parsed.rows.push({label:node.name,detail:[text(node.detail),text(node.containerName)].filter(Boolean).join(' · ')||undefined,kind:typeof node.kind==='number'?symbolKinds[node.kind-1]:undefined,depth:Math.min(12,depth||count(node.depth)),location:target});
   if(Array.isArray(node.children))for(let index=node.children.length-1;index>=0;index--)stack.push({value:node.children[index],depth:depth+1});
  }
  parsed.displayTruncated=parsed.displayTruncated||stack.length>0;
  return parsed;
 }
 const diagnostics=result.operation==='diagnostics';
 if(!diagnostics&&result.operation!=='definition'&&result.operation!=='references'){unsupported(payload);return parsed;}
 const entries=Array.isArray(payload)?payload:diagnostics?item?.diagnostics:Array.isArray(item?.locations)?item.locations:item?[item]:undefined;
 if(!Array.isArray(entries)){unsupported(payload);return parsed;}
 parsed.displayTruncated=entries.length>MAX_ROWS;
 for(const value of entries.slice(0,MAX_ROWS)){
  const node=record(value);
  if(diagnostics){
   if(!node||typeof node.message!=='string'){unsupported(value);continue;}
   const target=range(node.range);
   parsed.rows.push({label:node.message,detail:[text(node.source)??text(node.producer),typeof node.code==='string'||typeof node.code==='number'?String(node.code):undefined].filter(Boolean).join(' · ')||undefined,kind:typeof node.severity==='number'?['Error','Warning','Information','Hint'][node.severity-1]??'Problem':'Problem',depth:0,location:target?{uri:text(node.uri)??result.uri,position:target.start}:undefined});
  }else{
   const target=location(value);if(!target){unsupported(value);continue;}
   parsed.rows.push({label:result.operation==='definition'?'Definition':'Reference',depth:0,location:target});
  }
 }
 return parsed;
}
