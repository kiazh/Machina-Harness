import {selectedCompletion} from '@codemirror/autocomplete';
import {Decoration,ViewPlugin,WidgetType,type DecorationSet,type EditorView,type ViewUpdate} from '@codemirror/view';
import {completionGhost} from './completion.ts';
class GhostText extends WidgetType {
 constructor(private text:string){super()}
 eq(other:GhostText){return this.text===other.text}
 toDOM(){const node=document.createElement('span');node.className='cm-inline-ghost';node.textContent=this.text;node.setAttribute('aria-hidden','true');return node}
}
function decorations(view:EditorView):DecorationSet{const text=completionGhost(view.state,selectedCompletion(view.state));return text?Decoration.set([Decoration.widget({widget:new GhostText(text),side:1}).range(view.state.selection.main.head)]):Decoration.none}
export const inlineCompletionGhost=ViewPlugin.fromClass(class{decorations:DecorationSet;constructor(view:EditorView){this.decorations=decorations(view)}update(update:ViewUpdate){if(update.startState!==update.state)this.decorations=decorations(update.view)}},{decorations:plugin=>plugin.decorations});
