import { lazy, memo, Suspense, useCallback, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Check, ChevronRight, Circle, OctagonAlert } from "lucide-react";
import type { TranscriptStore } from "../../state/transcript.ts";
const MessageBody=lazy(()=>import('./MessageBody.tsx').then(module=>({default:module.MessageBody})));

const MessageRow = memo(function MessageRow({ id, store }: { id: string; store: TranscriptStore }) {
  const subscribe = useCallback((listener: () => void) => store.subscribeMessage(id, listener), [store, id]);
  const message = useSyncExternalStore(subscribe, () => store.get(id));
  if (!message) return null;
  if (message.role === "tool") return <details className={`tool-row tool-${message.status}`}>
    <summary>{message.status === "complete" ? <Check size={14}/> : message.status === "failed" ? <OctagonAlert size={14}/> : <Circle size={12}/>}<span>{message.title}</span><small>{message.status}</small><ChevronRight className="tool-chevron" size={14}/></summary>
    <pre>{message.text}</pre>{message.truncated && <p>Output preview truncated. Open the command output for more.</p>}
  </details>;
  return <article className={`message message-${message.role}`}>
    <div className="message-label">{message.role === "user" ? "You" : message.role === "assistant" ? "Machina" : "Progress"}{message.status && <small>{message.status}</small>}{message.streaming && <span className="working-dot"/>}</div>
    <div className="message-body">{message.role === "assistant" ? <Suspense fallback={message.text}><MessageBody text={message.text} streaming={Boolean(message.streaming)}/></Suspense> : message.text}</div>
    {message.truncated && <small>Long message preview truncated.</small>}
  </article>;
});

export function Transcript({ store }: { store: TranscriptStore }) {
  const ids = useSyncExternalStore(store.subscribe, store.order);
  const parent = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [detached,setDetached]=useState(false);
  const virtualizer = useVirtualizer({ count: ids.length, getScrollElement: () => parent.current, estimateSize: () => 110, overscan: 8, getItemKey: index => ids[index]!, useAnimationFrameWithResizeObserver:true });
  useLayoutEffect(() => {
    if (!inner.current || typeof ResizeObserver === "undefined") return;
    let frame=0;
    const observer = new ResizeObserver(() => {
      if(frame||!follow.current||!parent.current?.clientHeight)return;
      frame=requestAnimationFrame(()=>{frame=0;if(follow.current&&parent.current?.clientHeight)parent.current.scrollTop=parent.current.scrollHeight});
    });
    observer.observe(inner.current); return () => {observer.disconnect();cancelAnimationFrame(frame)};
  }, [ids.length>0]);
  useLayoutEffect(() => { if (follow.current && ids.length) virtualizer.scrollToIndex(ids.length - 1, { align: "end" }); }, [ids.length]);
  return <><div className="transcript" ref={parent} onScroll={event => { const element = event.currentTarget; const distance=element.scrollHeight-element.scrollTop-element.clientHeight;follow.current=distance<70;if(distance>200)setDetached(true);else if(distance<70)setDetached(false); }}>
    {!ids.length ? <div className="harness-empty"><div className="hero-identity"><h2>DEUS EX<br/>MACHINA</h2></div><p>Bring an idea. Leave with something working.</p></div> :
      <div ref={inner} style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative", width: "100%" }}>
        {virtualizer.getVirtualItems().map(item => <div key={item.key} data-index={item.index} ref={virtualizer.measureElement} style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${item.start}px)` }}><MessageRow id={ids[item.index]!} store={store}/></div>)}
      </div>}
  </div>{detached&&<button className="jump-latest" onClick={()=>{follow.current=true;setDetached(false);virtualizer.scrollToIndex(ids.length-1,{align:'end'})}}>↓ Jump to latest</button>}</>;
}
