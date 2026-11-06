import {useLayoutEffect,useMemo,useRef,useState,type CSSProperties} from 'react';
import {useVirtualizer} from '@tanstack/react-virtual';
import {parseDiff,type DiffRow} from './diff.ts';

const screenReaderOnly:CSSProperties={position:'absolute',width:1,height:1,margin:-1,padding:0,overflow:'hidden',clipPath:'inset(50%)',whiteSpace:'nowrap',border:0,userSelect:'none'};
function sourceLine(row:DiffRow){
 if(row.kind==='add')return `Added, new line ${row.newLine}. `;
 if(row.kind==='remove')return `Removed, old line ${row.oldLine}. `;
 if(row.kind==='context')return `Context, old line ${row.oldLine}, new line ${row.newLine}. `;
}

export function DiffView({patch,path}:{patch:string;path:string}){
 const scroll=useRef<HTMLDivElement>(null),[plain,setPlain]=useState(false),[notice,setNotice]=useState('');
 const clipboardGeneration=useRef(0);
 const rows=useMemo(()=>parseDiff(patch),[patch]);
 const virtual=useVirtualizer({count:rows.length,getScrollElement:()=>scroll.current,estimateSize:()=>23,overscan:12});
 useLayoutEffect(()=>{
  clipboardGeneration.current++;setNotice('');setPlain(false);if(scroll.current)scroll.current.scrollTop=0;
  return()=>{clipboardGeneration.current++};
 },[patch,path]);
 async function copy(){
  const current=++clipboardGeneration.current;setNotice('');
  try{
   await navigator.clipboard.writeText(patch);
   if(current===clipboardGeneration.current)setNotice('Diff copied.');
  }catch{
   if(current!==clipboardGeneration.current)return;
   setNotice('Clipboard unavailable. Use plain text to select and copy the diff.');setPlain(true);
  }
 }
 return <>
  <div className="diff-tools"><span>Read-only diff</span><div><button aria-pressed={plain} onClick={()=>setPlain(value=>!value)}>{plain?'Line view':'Plain text'}</button><button onClick={()=>{void copy()}}>Copy diff</button></div></div>
  {notice&&<p className="diff-copy-notice" role="status">{notice}</p>}
  {plain?<pre className="diff-plain" tabIndex={0} aria-label={`Plain diff for ${path}`}>{patch}</pre>:<div className="diff-scroll" ref={scroll} tabIndex={0} role="region" aria-label={`Diff for ${path}`}>
   <div className="diff-lines" style={{height:virtual.getTotalSize(),minWidth:'100%'}}>{virtual.getVirtualItems().map(item=>{
    const row=rows[item.index]!,description=sourceLine(row);
    return <div className={`diff-line diff-${row.kind}`} key={item.key} style={{height:item.size,transform:`translateY(${item.start}px)`}}>
     {description&&<span style={screenReaderOnly}>{description}</span>}
     <span className="diff-numbers" aria-hidden="true"><span>{row.oldLine}</span><span>{row.newLine}</span><b>{row.kind==='add'?'+':row.kind==='remove'?'−':''}</b></span><code>{row.text||' '}</code>
    </div>;
   })}</div>
  </div>}
 </>;
}
