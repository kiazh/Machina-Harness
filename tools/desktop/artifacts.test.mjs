import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,utimesSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename,dirname,join,resolve} from 'node:path';
import {previewExecutable,publishLockedPreview} from './artifacts.mjs';

function fixture(t){
 const temporary=resolve(tmpdir()),root=mkdtempSync(join(temporary,'machina-artifacts-test-'));
 t.after(()=>{assert.equal(dirname(root),temporary);assert.ok(basename(root).startsWith('machina-artifacts-test-'));rmSync(root,{recursive:true,force:true})});
 const release=join(root,'apps/desktop/src-tauri/target/release');mkdirSync(join(release,'deps'),{recursive:true});mkdirSync(join(root,'apps/desktop/dist'),{recursive:true});
 writeFileSync(join(root,'apps/desktop/dist/index.html'),'<main/>');const linked=join(release,'deps/machina_desktop.exe');const bytes=Buffer.alloc(600_000);bytes.write('MZ');writeFileSync(linked,bytes);
 const diagnostic=`error: failed to remove file ${join(release,'machina-desktop.exe')}\nAccess is denied. (os error 5)`;
 return {root,linked,diagnostic,bytes};
}
test('publishes a fresh linked preview beside a locked running executable and selects it for launch',t=>{
 const f=fixture(t),path=publishLockedPreview(f.root,Date.now()-100,f.diagnostic);assert.ok(path);assert.deepEqual(readFileSync(path),f.bytes);assert.equal(previewExecutable(f.root,true),path);
});
test('does not turn a compiler error into a successful preview',t=>{
 const f=fixture(t);assert.equal(publishLockedPreview(f.root,Date.now(),'error: compiler failed'),undefined);
});
test('refuses a stale link even when the old executable is locked',t=>{
 const f=fixture(t);utimesSync(f.linked,1,1);assert.equal(publishLockedPreview(f.root,Date.now(),f.diagnostic),undefined);
});
