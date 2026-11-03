import {constants,copyFileSync,existsSync,readFileSync,statSync,writeFileSync} from 'node:fs';
import {basename,join} from 'node:path';

const release=root=>join(root,'apps/desktop/src-tauri/target/release');
const manifest=root=>join(release(root),'.preview-latest.json');
export function rememberPreview(root,path){
  if(join(release(root),basename(path))!==path)throw new Error('Preview must be a release-directory artifact.');
  writeFileSync(manifest(root),JSON.stringify({executable:basename(path)})+'\n');
}
export function previewExecutable(root,windows=process.platform==='win32'){
  try{
    const value=JSON.parse(readFileSync(manifest(root),'utf8'));
    if(typeof value.executable==='string'&&/^(machina-desktop|machina-preview-[0-9]+)(\.exe)?$/.test(value.executable)){
      const path=join(release(root),value.executable);if(existsSync(path))return path;
    }
  }catch{}
  return join(release(root),windows?'machina-desktop.exe':'machina-desktop');
}
/** Recover only Cargo's final Windows copy failure, after a verified fresh link. */
export function publishLockedPreview(root,startedAt,diagnostic){
  const canonical=join(release(root),'machina-desktop.exe');
  const normalized=diagnostic.replaceAll('\\','/').toLowerCase();
  if(!normalized.includes('failed to remove file')||!normalized.includes(canonical.replaceAll('\\','/').toLowerCase())||!normalized.includes('os error 5'))return;
  const linked=join(release(root),'deps/machina_desktop.exe');
  const frontend=join(root,'apps/desktop/dist/index.html');
  try{
    const binary=statSync(linked);
    if(binary.size<512_000||binary.mtimeMs<startedAt-2000||binary.mtimeMs<statSync(frontend).mtimeMs)return;
    const bytes=readFileSync(linked);if(bytes[0]!==0x4d||bytes[1]!==0x5a)return;
    const destination=join(release(root),`machina-preview-${Date.now()}.exe`);
    copyFileSync(linked,destination,constants.COPYFILE_EXCL);
    rememberPreview(root,destination);return destination;
  }catch{return;}
}
