import { memo, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";

const components: Components = {
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  img: ({ alt }) => <span className="message-image-label">{alt || "Image"}</span>,
};

const Formatted = memo(function Formatted({ text }: { text: string }) {
  return <div className="formatted-message"><Markdown skipHtml components={components}>{text}</Markdown></div>;
});

/** Parse only mounted messages, at most eight times/second while streaming. */
export const MessageBody = memo(function MessageBody({ text, streaming }: { text: string; streaming: boolean }) {
  const latest = useRef(text); latest.current = text;
  const [preview, setPreview] = useState(text);
  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => setPreview(latest.current), 125);
    return () => clearInterval(timer);
  }, [streaming]);
  return <Formatted text={streaming ? preview : text}/>;
});
