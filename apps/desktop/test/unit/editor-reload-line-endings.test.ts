import { EditorState } from '@codemirror/state';
import { history, undo } from '@codemirror/commands';
import { expect, it } from 'vitest';
import { DesktopController } from '../../src/state/controller.ts';

async function fixture(text: string, lineEnding: 'LF' | 'CRLF' | 'CR') {
  let disk = { text, lineEnding, revision: 'one' };
  let mirror = '';
  let saved = '';
  const controller = new DesktopController({
    async start() {},
    subscribe() { return () => {}; },
    async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      if (method === 'workspace.documentOpen') mirror = String(params.text);
      if (method === 'workspace.save') saved = String(params.text);
      const responses: Record<string, unknown> = {
        'runtime.capabilities': { engineVersion: 'test', protocolVersion: 2, storageProfile: 'postgres', database: 'ready', methods: [] },
        initialize: { cwd: 'test', cwdFull: '/test', provider: 'p', model: 'm' },
        'config.get': { config: {} },
        'session.list': { sessions: [] },
        'workspace.open': { workspaceId: 'w', clientId: 'c', epoch: 1, rootLabel: 'test', resolvedRoot: '/test', trusted: true, capabilities: [] },
        'workspace.read': { uri: params.uri, ...disk, languageId: 'python', encoding: 'utf-8', readOnly: false, sizeBytes: new TextEncoder().encode(disk.text).length },
        'workspace.documentOpen': { acceptedVersion: 0, serverGeneration: 1, state: 'ready' },
        'workspace.save': { ok: true, revision: 'saved' },
      };
      return responses[method==='runtime.selectWorkspace'?'workspace.open':method] as T;
    },
  });
  await controller.connect();
  await controller.openFile('hello.py');
  const id = controller.snapshot().activeId;
  controller.bindEditor(id, EditorState.create({
    doc: text,
    extensions: [history(), EditorState.lineSeparator.of(lineEnding === 'CRLF' ? '\r\n' : lineEnding === 'CR' ? '\r' : '\n')],
  }));
  return {
    controller, id,
    replace(text: string, lineEnding: 'LF' | 'CRLF' | 'CR') { disk = { text, lineEnding, revision: 'two' }; },
    mirror: () => mirror,
    saved: () => saved,
  };
}

it.each([
  { from: 'CRLF', before: 'old\r\nsecond\r\n', to: 'LF', after: 'new\nsecond\n', undone: 'old\nsecond\n' },
  { from: 'LF', before: 'old\nsecond\n', to: 'CRLF', after: 'new\r\nsecond\r\n', undone: 'old\r\nsecond\r\n' },
  { from: 'LF', before: 'old\nsecond\n', to: 'CR', after: 'new\rsecond\r', undone: 'old\rsecond\r' },
] as const)('reloads $from as $to with real lines, matching mirror/save text and undo', async ({ from, before, to, after, undone }) => {
  const f = await fixture(before, from);
  f.replace(after, to);
  await f.controller.reloadDocument(f.id);
  const document = f.controller.snapshot().documents.find(document => document.id === f.id)!;
  let state = document.initialState!;
  expect(state.doc.lines).toBe(3);
  expect(state.doc.line(1).text).toBe('new');
  expect(state.sliceDoc()).toBe(after);
  expect(f.mirror()).toBe(after);
  await f.controller.save();
  expect(f.saved()).toBe(after);
  expect(undo({ state, dispatch(transaction) { state = transaction.state; } })).toBe(true);
  expect(state.sliceDoc()).toBe(undone);
});
