import { Compartment, EditorState, Prec, StateEffect } from '@codemirror/state';

interface DocumentMetadata { lineEnding?: string; readOnly?: boolean }

const metadata = new Compartment();
const lineSeparator = (document: DocumentMetadata) => document.lineEnding === 'CRLF' ? '\r\n' : document.lineEnding === 'CR' ? '\r' : '\n';
const extensions = (document: DocumentMetadata) => [
  EditorState.lineSeparator.of(lineSeparator(document)),
  EditorState.readOnly.of(document.readOnly ?? false),
];

export function documentMetadataExtension(document: DocumentMetadata) {
  return Prec.highest(metadata.of(extensions(document)));
}

export function configureDocumentState(state: EditorState, document: DocumentMetadata): EditorState {
  const installed = metadata.get(state) !== undefined;
  if (installed && state.lineBreak === lineSeparator(document) && state.readOnly === (document.readOnly ?? false)) return state;
  return state.update({ effects: installed
    ? metadata.reconfigure(extensions(document))
    : StateEffect.appendConfig.of(documentMetadataExtension(document)),
  }).state;
}
