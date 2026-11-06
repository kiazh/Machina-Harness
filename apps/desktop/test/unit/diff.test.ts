import {expect,it} from 'vitest';
import {parseDiff,diffAttachment} from '../../src/features/changes/diff.ts';

it('keeps hunk positions and literal plus/minus headers distinct from code lines',()=>{
 const rows=parseDiff('--- a/demo.py\n+++ b/demo.py\n@@ -3,2 +3,3 @@\n same\n-old\n+new\n+++literal\n');
 expect(rows.map(row=>[row.kind,row.oldLine,row.newLine,row.text])).toEqual([
 ['header',null,null,'--- a/demo.py'],['header',null,null,'+++ b/demo.py'],['hunk',null,null,'@@ -3,2 +3,3 @@'],
 ['context',3,3,'same'],['remove',4,null,'old'],['add',null,4,'new'],['add',null,5,'++literal']]);
});

it('fences attached diff content without sending it or allowing oversized context',()=>{
 expect(diffAttachment('file\nname.md','working','@@ -1 +1 @@\n-old\n+```\n')).toContain('````diff');
 expect(diffAttachment('file\nname.md','staged','+new')).toContain('file name.md (HEAD to index)');
 expect(()=>diffAttachment('large','working','x'.repeat(16001))).toThrow(/16,000/);
});
it('supports omitted hunk counts, empty new files and no-final-newline annotations',()=>{
 const rows=parseDiff('@@ -0,0 +1 @@\n+new\n\\ No newline at end of file\n');
 expect(rows[1]).toMatchObject({kind:'add',oldLine:null,newLine:1,text:'new'});
 expect(rows[2]).toMatchObject({kind:'note',oldLine:null,newLine:null});
 expect(parseDiff('')).toEqual([]);
});
