const KEY='machina.desktop.workspace.v1';
export function workspacePreference():{root:string;trust:boolean}|undefined{
  try{const value=JSON.parse(localStorage.getItem(KEY)??'null');if(value&&typeof value.root==='string'&&value.root.length<=4096&&typeof value.trust==='boolean')return value}catch{}
}
export function rememberWorkspace(root:string,trust:boolean):void{
  try{localStorage.setItem(KEY,JSON.stringify({root,trust}))}catch{}
}
export function sameWorkspace(first:string,second:string):boolean{
  const normalize=(path:string)=>{const value=path.replaceAll('\\','/').replace(/\/$/,'');return /^[a-z]:\//i.test(value)||value.startsWith('//')?value.toLowerCase():value};
  return normalize(first)===normalize(second);
}
export function workspaceContains(root:string,uri:string):boolean{
  try{const url=new URL(uri);if(url.protocol!=='file:')return false;const path=decodeURIComponent((url.hostname?'//'+url.hostname:'')+url.pathname).replace(/^\/(?=[a-z]:\/)/i,'');return sameWorkspace(root,path)||sameWorkspace(root,path.slice(0,root.replaceAll('\\','/').replace(/\/$/,'').length))&&path.replaceAll('\\','/').charAt(root.replaceAll('\\','/').replace(/\/$/,'').length)==='/'}catch{return false}
}
export function relativeWorkspacePath(root:string,uri:string):string{
  if(!workspaceContains(root,uri))throw new Error('This location is outside the current workspace. Open that folder explicitly to inspect it.');
  const url=new URL(uri),path=decodeURIComponent((url.hostname?'//'+url.hostname:'')+url.pathname).replace(/^\/(?=[a-z]:\/)/i,'');
  return path.slice(root.replaceAll('\\','/').replace(/\/$/,'').length).replace(/^\//,'');
}
