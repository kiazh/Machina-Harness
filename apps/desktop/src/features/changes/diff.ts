export interface DiffRow {kind:'header'|'hunk'|'context'|'add'|'remove'|'note';oldLine:number|null;newLine:number|null;text:string}
export const MAX_DIFF_ROWS=6000;
export function parseDiff(patch:string):DiffRow[]{
 const rows:DiffRow[]=[];let oldLine=0,newLine=0,inHunk=false;
 const lines=patch.split('\n');if(lines.at(-1)==='')lines.pop();
 for(const text of lines){
  if(rows.length===MAX_DIFF_ROWS){rows.push({kind:'note',oldLine:null,newLine:null,text:'Display limited to 6,000 lines. Copy the diff or narrow the change to inspect more.'});break}
  const hunk=/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
  if(hunk){oldLine=Number(hunk[1]);newLine=Number(hunk[2]);inHunk=true;rows.push({kind:'hunk',oldLine:null,newLine:null,text});continue}
  if(text.startsWith('diff --git '))inHunk=false;
  if(inHunk&&text.startsWith('+'))rows.push({kind:'add',oldLine:null,newLine:newLine++,text:text.slice(1)});
  else if(inHunk&&text.startsWith('-'))rows.push({kind:'remove',oldLine:oldLine++,newLine:null,text:text.slice(1)});
  else if(inHunk&&text.startsWith(' '))rows.push({kind:'context',oldLine:oldLine++,newLine:newLine++,text:text.slice(1)});
  else rows.push({kind:text.startsWith('\\')?'note':'header',oldLine:null,newLine:null,text});
 }
 return rows;
}
export function diffAttachment(path:string,target:'working'|'staged',patch:string):string{
 if(!patch||patch.length>16000)throw new Error('Attach a diff of at most 16,000 characters. Copy a smaller selection from the diff instead.');
 const fence='`'.repeat(Math.max(2,...(patch.match(/`+/g)??[]).map(value=>value.length))+1);
 return `<attached diff: ${path.replace(/[\x00-\x1f\x7f]/g,' ')} (${target==='staged'?'HEAD to index':'index to disk'})>\n${fence}diff\n${patch}${patch.endsWith('\n')?'':'\n'}${fence}`;
}
