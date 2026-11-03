import { memo, useLayoutEffect, useRef } from "react";
import { Compartment, EditorState, Text } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, lineNumbers, type ViewUpdate } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { keymap } from "@codemirror/view";
import { acceptCompletion, autocompletion, completionKeymap, startCompletion } from "@codemirror/autocomplete";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { javascript } from "@codemirror/lang-javascript";
import { bracketMatching, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { python } from "@codemirror/lang-python";
import { completionSource, type IntelligenceRequest } from "./completion.ts";
import {inlineCompletionGhost} from './ghost.ts';
import { configureDocumentState, documentMetadataExtension } from './documentState.ts';
import {liveDiagnostics,type DiagnosticsRequest} from './diagnostics.ts';
const highlights = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.bool, tags.null], color: "var(--syntax-keyword)" },
  { tag: [tags.string, tags.regexp], color: "var(--syntax-string)" },
  { tag: [tags.function(tags.variableName), tags.definition(tags.variableName), tags.number], color: "var(--syntax-function)" },
  { tag: [tags.comment, tags.meta], color: "var(--syntax-comment)", fontStyle: "italic" },
]);

export interface CodeDocument { id: string; name: string; text: string; readOnly?: boolean; lineEnding?: string; initialState?:EditorState;reloadToken?:number }
interface Props {
  navigation?:{documentId:string;line:number;character:number;token:number};
  openIds?:readonly string[];
  document: CodeDocument;
  onReady?: (view: EditorView) => void;
  onChange?: (id: string, update: ViewUpdate) => void;
  onSelection?: (id:string,state:EditorState)=>void;
  onCompletion?: IntelligenceRequest;
  onDiagnostics?: DiagnosticsRequest;
}

export const CodeEditor = memo(function CodeEditor({ document, onReady, onChange, onCompletion,onDiagnostics,openIds,onSelection,navigation }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const states = useRef(new Map<string, EditorState>());
  const loaded=useRef(new Map<string,number>());
  const active = useRef(document.id);
  const language=useRef(new Compartment());
  const languageFor=(name:string)=>name.endsWith('.py')?[python()]:/\.[cm]?[jt]sx?$/.test(name)?[javascript({typescript:/\.tsx?$/.test(name),jsx:/\.[jt]sx$/.test(name)})]:[];
  const callbacks = useRef({ onReady, onChange, onCompletion,onDiagnostics,onSelection });
  callbacks.current = { onReady, onChange, onCompletion,onDiagnostics,onSelection };
  function stateFor(doc: CodeDocument) {
    const changed=loaded.current.has(doc.id)&&loaded.current.get(doc.id)!==(doc.reloadToken??0);loaded.current.set(doc.id,doc.reloadToken??0);
    const existing = (changed?undefined:states.current.get(doc.id)) ?? doc.initialState;
    if(existing)return configureDocumentState(existing,doc);
    return EditorState.create({
      doc: Text.of(doc.text.split(/\r\n?|\n/)),
      extensions: [
        lineNumbers(), highlightActiveLineGutter(), history(), drawSelection(),
        indentOnInput(), bracketMatching(), autocompletion({ activateOnTypingDelay: 180, override: [completionSource(doc.id, (_id, state, position) => callbacks.current.onCompletion?.(active.current, state, position) ?? Promise.resolve(null))] }), highlightActiveLine(),
        highlightSelectionMatches(), documentMetadataExtension(doc),
        inlineCompletionGhost,
        liveDiagnostics(doc.id,()=>callbacks.current.onDiagnostics),
        syntaxHighlighting(highlights),
        language.current.of(languageFor(doc.name)),
        keymap.of([{ key: "Tab", run: view => !view.composing && acceptCompletion(view) }, ...completionKeymap.filter(binding=>binding.run!==startCompletion), ...searchKeymap, ...defaultKeymap, ...historyKeymap, indentWithTab]),
        EditorView.contentAttributes.of({ "aria-label": "Code editor", spellcheck: "false" }),
        EditorView.theme({
          "&": { height: "100%", backgroundColor: "transparent", color: "var(--ink)", fontSize: "var(--code-size, 13px)" },
          ".cm-scroller": { overflow: "auto", fontFamily: "var(--code-font)" },
          ".cm-content": { padding: "20px 0 60px", lineHeight: "1.75" },
          ".cm-gutters": { backgroundColor: "transparent", color: "var(--muted)", border: "none", paddingRight: "16px" },
          ".cm-lineNumbers .cm-gutterElement": { minWidth: "42px" },
          ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "#b3a58f0b" },
          ".cm-cursor": { borderLeftColor: "var(--accent)" },
          '.cm-inline-ghost':{color:'var(--muted)',opacity:'.7',pointerEvents:'none'},
          '.cm-tooltip':{backgroundColor:'var(--canvas)',color:'var(--ink)',border:'1px solid var(--line)',borderRadius:'4px'},
          '.cm-tooltip-lint':{maxWidth:'min(60ch, 70vw)',fontFamily:'var(--ui-font)'},
          '.cm-diagnostic':{padding:'8px 12px',lineHeight:'1.6',whiteSpace:'pre-wrap'},
          "&.cm-focused": { outline: "none" },
          "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "var(--hover)" },
        }),
        EditorView.updateListener.of(update => { if (update.docChanged) callbacks.current.onChange?.(active.current, update);else if(update.selectionSet)callbacks.current.onSelection?.(active.current,update.state); }),
      ],
    });
  }
  useLayoutEffect(() => {
    const editor = new EditorView({ state: stateFor(document), parent: host.current! });
    view.current = editor;
    callbacks.current.onReady?.(editor);
    return () => { editor.destroy(); view.current = null; };
  }, []);
  useLayoutEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const changed=active.current!==document.id||loaded.current.get(document.id)!==(document.reloadToken??0);
    if(changed){
      if(active.current!==document.id)states.current.set(active.current, editor.state);
      active.current = document.id;
      editor.setState(stateFor(document));
    }
    const configured=configureDocumentState(editor.state,document);
    const metadataChanged=configured!==editor.state;
    if(metadataChanged)editor.setState(configured);
    editor.dispatch({effects:language.current.reconfigure(languageFor(document.name))});
    if(changed||metadataChanged)callbacks.current.onReady?.(editor);
  }, [document.id,document.reloadToken,document.name,document.lineEnding,document.readOnly]);
  useLayoutEffect(()=>{
    const editor=view.current;if(!editor||!navigation||navigation.documentId!==active.current)return;
    const line=editor.state.doc.line(Math.min(editor.state.doc.lines,Math.max(1,navigation.line+1)));
    const position=line.from+Math.min(line.length,Math.max(0,navigation.character));
    editor.dispatch({selection:{anchor:position},effects:EditorView.scrollIntoView(position,{y:'center'})});editor.focus();
  },[navigation?.token]);
  useLayoutEffect(()=>{if(openIds)for(const id of states.current.keys())if(!openIds.includes(id))states.current.delete(id)},[openIds,document.id]);
  return <div className="code-editor" ref={host} />;
});
