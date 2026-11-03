/// <reference types="node" />
import {test,expect} from '@playwright/test';
import {existsSync,readFileSync,unlinkSync} from 'node:fs';
import {resolve} from 'node:path';
const input=resolve(import.meta.dirname,'../../.local-acceptance/real-long-history.json');
test('local recorded session stays responsive while scrolling, typing and streaming',async({page})=>{
 test.skip(!existsSync(input),'Optional local acceptance data; no user transcripts are committed.');
 const payload=JSON.parse(readFileSync(input,'utf-8'));unlinkSync(input);
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 await page.evaluate(history=>{const controller=(window as any).fixtureController;controller.transcript.clear();for(const [index,row] of history.entries())controller.transcript.put({id:`recorded:${index}`,role:row.role==='system'?'notice':row.role,text:row.content,title:row.tool});controller.transcript.flush()},payload.history);
 const editor=page.getByRole('textbox',{name:'Code editor',exact:true});await editor.click();
 const frames=page.evaluate(async()=>{const target=window as any,scroller=document.querySelector('.transcript')!;let direction=-1;const times:number[]=[];let previous=performance.now();for(let index=0;index<100;index++)await new Promise<void>(resolve=>requestAnimationFrame(now=>{times.push(now-previous);previous=now;if(scroller.scrollTop<100)direction=1;if(scroller.scrollTop+scroller.clientHeight>=scroller.scrollHeight-100)direction=-1;scroller.scrollTop+=direction*100;target.fixtureController.transcript.append('recorded:1',' streaming');resolve()}));return times.sort((a,b)=>a-b)});
 const started=Date.now();await editor.pressSequentially('# local recorded-session typing check while scrolling\n',{delay:4});const inputMs=Date.now()-started;const intervals=await frames;const p95=intervals[Math.floor(intervals.length*.95)]!;
 console.log(JSON.stringify({recordedEntries:payload.history.length,totalChunks:payload.totalChunks,typingMs:inputMs,frameP95Ms:p95,renderedRows:await page.locator('.message,.tool-row').count()}));
 await expect(editor).toContainText('local recorded-session typing check');expect(inputMs).toBeLessThan(4000);expect(p95).toBeLessThan(45);expect(errors).toEqual([]);
});
