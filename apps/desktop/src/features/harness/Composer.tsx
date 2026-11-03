import { useEffect, useRef, useState } from "react";
import {Code2,Paperclip,X} from 'lucide-react';
import {matchesShortcut,formatShortcut} from '../../state/shortcuts.ts';
import {MAX_IMAGE_TOTAL,readImage,type DraftImage,type PromptImage} from './attachments.ts';

interface Props {
  running:boolean;disabled:boolean;onSend:(text:string,images?:PromptImage[])=>Promise<unknown>;onInterrupt:()=>void;
  interruptShortcut?:string|null;initialDraft?:string;onDraft?:(text:string)=>void;
  onAttachSelection?:()=>string;insertion?:{id:number;text:string};onConsumeInsertion?:(id:number)=>void;onPendingAttachments?:(count:number)=>void;
}
export function Composer({running,disabled,onSend,onInterrupt,initialDraft='',onDraft,interruptShortcut='Escape',onAttachSelection,insertion,onConsumeInsertion,onPendingAttachments}:Props){
 const [draft,setDraft]=useState(initialDraft),[images,setImages]=useState<DraftImage[]>([]),[error,setError]=useState(''),[sending,setSending]=useState(false),[loading,setLoading]=useState(false);
 const input=useRef<HTMLInputElement>(null),textarea=useRef<HTMLTextAreaElement>(null),latest=useRef(draft),latestImages=useRef(images),reading=useRef(false),alive=useRef(true);latest.current=draft;latestImages.current=images;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 useEffect(()=>{onPendingAttachments?.(images.length+(loading?1:0))},[images.length,loading,onPendingAttachments]);
 function update(text:string){latest.current=text;setDraft(text);onDraft?.(text)}
 function insert(text:string){const prior=latest.current.trim()==='/attach'?'':latest.current;update(prior+(prior&&!prior.endsWith('\n')?'\n\n':'')+text+'\n');setError('');textarea.current?.focus()}
 useEffect(()=>{if(insertion){insert(insertion.text);onConsumeInsertion?.(insertion.id)}},[insertion?.id]);
 function attachSelection(){try{if(!onAttachSelection)throw new Error('Open code in the editor first.');insert(onAttachSelection())}catch(reason){setError(reason instanceof Error?reason.message:String(reason))}}
 async function attachFiles(files:File[]){
  if(!files.length||reading.current||sending)return;
  const existing=latestImages.current;
  if(existing.length+files.length>5){setError('Attach up to five images per message.');return;}
  if(existing.reduce((sum,image)=>sum+image.bytes,0)+files.reduce((sum,file)=>sum+file.size,0)>MAX_IMAGE_TOTAL){setError('Images must fit within the desktop’s 4 MiB total attachment limit.');return;}
  reading.current=true;setLoading(true);setError('');
  try{const loaded=await Promise.all(files.map(readImage));if(alive.current)setImages(previous=>[...previous,...loaded])}catch(reason){if(alive.current)setError(reason instanceof Error?reason.message:String(reason))}finally{reading.current=false;if(alive.current)setLoading(false)}
 }
 async function submit(){
  if(!draft.trim()||sending||disabled||loading)return;
  if(draft.trim()==='/attach'){attachSelection();return;}
  if(images.length&&draft.trim().startsWith('/')){setError('Send image attachments with a message, rather than a slash command.');return;}
  if(images.length&&running){setError('Images stay here for the next turn. Wait for the agent to finish before sending them.');return;}
  if(draft.length>20000){setError('Messages can contain up to 20,000 characters. Your draft is retained.');return;}
  const text=draft,captured=images;setSending(true);setError('');
  try{if(captured.length)await onSend(text,captured.map(({name,mediaType,data})=>({name,mediaType,data})));else await onSend(text);
   if(latest.current===text)update('');if(latestImages.current===captured)setImages([]);
  }catch(reason){setError(reason instanceof Error?reason.message:'Could not send. Your draft is retained.')}finally{if(alive.current)setSending(false)}
 }
 return <form className="composer" onSubmit={event=>{event.preventDefault();void submit()}} onDragOver={event=>{if(event.dataTransfer.types.includes('Files'))event.preventDefault()}} onDrop={event=>{if(event.dataTransfer.files.length){event.preventDefault();void attachFiles(Array.from(event.dataTransfer.files))}}}>
  {error&&<p role="alert" className="inline-error">{error}</p>}
  {!!images.length&&<ul className="composer-attachments" aria-label="Image attachments">{images.map((image,index)=><li key={index}><Paperclip size={12}/><span title={`${image.mediaType}, ${Math.ceil(image.bytes/1024)} KiB`}>{image.name}</span><button type="button" aria-label={`Remove image ${index+1}: ${image.name}`} disabled={sending||loading} onClick={()=>setImages(previous=>previous.filter((_,position)=>position!==index))}><X size={12}/></button></li>)}</ul>}
  {images.length>0&&running&&<p className="composer-attachment-note" role="status">Images can be sent after this turn. Remove them to queue text steering.</p>}
  {loading&&<p className="composer-attachment-note" role="status">Reading image attachments…</p>}
  <textarea ref={textarea} aria-label="Message Machina" rows={3} value={draft} onChange={event=>update(event.target.value)} placeholder={running?'Steer the next step…':"Ask Machina, or describe what you’re building…"}
   onPaste={event=>{const files=Array.from(event.clipboardData.files).filter(file=>file.type.startsWith('image/'));if(files.length){event.preventDefault();void attachFiles(files)}}}
   onKeyDown={event=>{if(event.nativeEvent.isComposing)return;if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void submit()}if(running&&matchesShortcut(event.nativeEvent,interruptShortcut)){event.preventDefault();onInterrupt()}}}/>
  <div className="composer-bottom"><div className="composer-tools"><input ref={input} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden aria-label="Choose image attachments" disabled={sending||loading} onChange={event=>{void attachFiles(Array.from(event.target.files??[]));event.target.value=''}}/>
   <button type="button" aria-label="Attach images" title="Attach images (up to 5, 4 MiB total)" disabled={sending||loading} onClick={()=>input.current?.click()}><Paperclip size={14}/></button>
   {onAttachSelection&&<button type="button" aria-label="Attach selected code" title="Attach only the selected code, including unsaved edits" disabled={sending||loading} onClick={attachSelection}><Code2 size={14}/></button>}
   <span>{running&&images.length?'Images ready for the next turn':running?`Enter queues${interruptShortcut?` · ${formatShortcut(interruptShortcut)} interrupts`:''}`:'Enter sends · Shift+Enter for a new line'}</span></div>
   <button className="send-button" type="submit" aria-label={running&&images.length?'Send images after current turn':running?'Queue message':'Send message'} disabled={disabled||sending||loading||!draft.trim()||!!(running&&images.length)}><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M5 12h14m-6-6 6 6-6 6"/></svg></button></div>
 </form>;
}
