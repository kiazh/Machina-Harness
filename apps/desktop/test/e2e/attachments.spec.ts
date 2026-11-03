import {test,expect} from '@playwright/test';
test('selected unsaved code enters the draft for review without automatically sending it',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();const editor=page.getByRole('textbox',{name:'Code editor',exact:true});await editor.click();await page.keyboard.press('Control+a');await page.keyboard.insertText('# selected code\nprint("review me")');await page.keyboard.press('Control+a');
 const before=await page.evaluate(()=>(window as any).fixtureController.transcript.order().length);
 await page.getByRole('button',{name:'Attach selected code',exact:true}).click();const draft=page.getByRole('textbox',{name:'Message Machina'});await expect(draft).toHaveValue(/<attached: src\/workspace.py L1–2 \(unsaved buffer\)>/);await expect(draft).toHaveValue(/print\("review me"\)/);
 expect(await page.evaluate(()=>(window as any).fixtureController.transcript.order().length)).toBe(before);
});
test('keeps an image while the agent runs and submits it through the real image RPC contract once idle',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');const draft=page.getByRole('textbox',{name:'Message Machina'});await draft.fill('Inspect this image');
 await page.getByLabel('Choose image attachments').setInputFiles({name:'context.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY9sAAAAASUVORK5CYII=','base64')});await expect(page.getByRole('button',{name:'Remove image 1: context.png'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Send images after current turn'})).toBeDisabled();await draft.press('Enter');await expect(page.getByRole('alert')).toContainText('next turn');
 await page.evaluate(()=>(window as any).fixtureEmit({type:'message.complete',sessionId:'fixture-session',turnId:'fixture-turn',text:'Done',cancelled:false}));
 await page.getByRole('button',{name:'Send message',exact:true}).click();await expect(draft).toHaveValue('');
 const sent=await page.evaluate(()=>{const params=(window as any).fixtureLastPrompt;return {text:params.text,images:params.images.map((image:any)=>({name:image.name,mediaType:image.mediaType,keys:Object.keys(image)}))}});
 expect(sent).toMatchObject({text:'Inspect this image',images:[{name:'context.png',mediaType:'image/png',keys:['name','mediaType','data']}]});
});
