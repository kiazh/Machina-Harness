import type {EditorState} from '@codemirror/state';
import type {OpenDocument} from '../../state/documents.ts';

export interface PromptImage {name:string;mediaType:string;data:string}
export interface DraftImage extends PromptImage {bytes:number}
export const MAX_IMAGE_TOTAL=4*1024*1024;
export function selectionAttachment(document:OpenDocument,state:EditorState,dirty:boolean,path:string):string{
 const {from,to}=state.selection.main;if(from===to)throw new Error('Select some code in the editor first.');
 if(to-from>16000)throw new Error('Attach at most 16,000 selected characters at once.');
 const selected=state.sliceDoc(from,to),start=state.doc.lineAt(from).number,end=state.doc.lineAt(to).number-(to>from&&state.doc.lineAt(to).from===to?1:0);
 const longest=Math.max(2,...(selected.match(/`+/g)??[]).map(value=>value.length)),fence='`'.repeat(longest+1);
 const language=/^[a-z0-9_+-]{1,30}$/i.test(document.languageId??'')?document.languageId:'';
 return `<attached: ${path.replace(/[\r\n\x00-\x1f]/g,' ')} L${start}${end>start?'–'+end:''} (${dirty?'unsaved buffer':'saved file'})>\n${fence}${language}\n${selected}${selected.endsWith('\n')?'':'\n'}${fence}`;
}
export function checkedImage(bytes:Uint8Array,name:string):DraftImage{
 if(bytes.length>MAX_IMAGE_TOTAL)throw new Error('Images must fit within the desktop’s 4 MiB total attachment limit.');
 const ascii=(start:number,length:number)=>String.fromCharCode(...bytes.slice(start,start+length));
 const mediaType=[137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value)?'image/png':bytes[0]===255&&bytes[1]===216&&bytes[2]===255?'image/jpeg':['GIF87a','GIF89a'].includes(ascii(0,6))?'image/gif':ascii(0,4)==='RIFF'&&ascii(8,4)==='WEBP'?'image/webp':'';
 if(!mediaType)throw new Error('Choose a PNG, JPEG, GIF or WebP image.');
 let binary='';for(let offset=0;offset<bytes.length;offset+=32768)binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
 return {name:name.replace(/[\x00-\x1f]/g,' ').slice(0,255)||'image',mediaType,data:btoa(binary),bytes:bytes.length};
}
export function readImage(file:File):Promise<DraftImage>{
 if(file.size>MAX_IMAGE_TOTAL)return Promise.reject(new Error('Images must fit within the desktop’s 4 MiB total attachment limit.'));
 return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error(`Could not read ${file.name}.`));reader.onabort=()=>reject(new Error('Image loading was cancelled.'));reader.onload=()=>{try{resolve(checkedImage(new Uint8Array(reader.result as ArrayBuffer),file.name))}catch(error){reject(error)}};reader.readAsArrayBuffer(file)});
}
