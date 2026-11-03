import { expect, it } from 'vitest';
import { DesktopController } from '../../src/state/controller.ts';
import type { DesktopBridge } from '../../src/bridge/types.ts';

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

async function fixture(blockedMethod: 'workspace.read' | 'workspace.documentOpen') {
  const entered = deferred(), release = deferred();
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  let epoch = 0;
  const bridge: DesktopBridge = {
    async start() {},
    subscribe() { return () => {}; },
    async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      calls.push({ method, params });
      if (method === blockedMethod) { entered.resolve(); await release.promise; }
      const values: Record<string, unknown> = {
        'runtime.capabilities': { engineVersion: 'test', protocolVersion: 2, storageProfile: 'postgres', database: 'ready', methods: [] },
        initialize: { cwd: 'first', cwdFull: '/first', provider: 'p', model: 'm' },
        'config.get': { config: {} },
        'session.list': { sessions: [] },
        'workspace.read': { uri: params.uri, text: 'old workspace', revision: 'one', languageId: 'python', encoding: 'utf-8', lineEnding: 'LF', readOnly: false, sizeBytes: 13 },
        'workspace.documentOpen': { acceptedVersion: 0, serverGeneration: 1, state: 'ready' },
      };
      if (method === 'workspace.open'||method === 'runtime.selectWorkspace') return { workspaceId: 'w', clientId: 'c', epoch: ++epoch, rootLabel: params.root, resolvedRoot: params.root, trusted: true, capabilities: [] } as T;
      return values[method==='runtime.selectWorkspace'?'workspace.open':method] as T;
    },
  };
  const controller = new DesktopController(bridge);
  await controller.connect();
  return { controller, calls, entered, release };
}

it('drops an old file read before opening a mirror in a different workspace', async () => {
  const f = await fixture('workspace.read');
  const opening = f.controller.openFile('hello.py');
  await f.entered.promise;
  await f.controller.switchWorkspace('/second', true);
  const current = f.controller.snapshot().activeId;
  f.release.resolve();
  await opening;
  expect(f.controller.snapshot().workspace?.resolvedRoot).toBe('/second');
  expect(f.controller.snapshot().activeId).toBe(current);
  expect(f.controller.snapshot().documents.map(document => document.uri)).toEqual([undefined]);
  expect(f.calls.filter(call => call.method === 'workspace.documentOpen')).toHaveLength(0);
});

it('does not reinsert an old tab when its mirror acknowledgement arrives after switching workspaces', async () => {
  const f = await fixture('workspace.documentOpen');
  const opening = f.controller.openFile('hello.py');
  await f.entered.promise;
  await f.controller.switchWorkspace('/second', true);
  const current = f.controller.snapshot().activeId;
  f.release.resolve();
  await opening;
  expect(f.controller.snapshot().activeId).toBe(current);
  expect(f.controller.snapshot().documents.map(document => document.uri)).toEqual([undefined]);
});

it('opens one tab when a project file is requested twice before its read completes', async () => {
  const f = await fixture('workspace.read');
  const first = f.controller.openFile('hello.py');
  const second = f.controller.openFile('hello.py');
  f.release.resolve();
  await Promise.all([first, second]);
  expect(f.controller.snapshot().documents.map(document => document.id)).toEqual(['file:///first/hello.py']);
  expect(f.calls.filter(call => call.method === 'workspace.documentOpen')).toHaveLength(1);
});
