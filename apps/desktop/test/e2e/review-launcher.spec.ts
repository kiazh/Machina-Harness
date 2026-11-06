import {expect,test} from '@playwright/test';

test('command launcher finds changes, preserves the draft and hands off safely to settings',async({page},info)=>{
 await page.goto('/test/fixtures/workspace.html');const composer=page.getByRole('textbox',{name:'Message Machina'});await composer.fill('Keep this draft while I inspect the code.');
 await page.keyboard.press('Control+Shift+P');const commands=page.getByRole('dialog',{name:'Commands',exact:true});await expect(commands).toBeVisible();
 await commands.getByRole('combobox',{name:'Search commands'}).fill('git');await expect(commands.getByRole('option',{name:'Review workspace changes'})).toBeVisible();
 await page.screenshot({path:info.outputPath('command-launcher.png')});await page.keyboard.press('Enter');await expect(commands).not.toBeVisible();
 await expect(page.getByRole('heading',{name:'Workspace changes',exact:true})).toBeVisible();await expect(page.getByRole('region',{name:'Diff for src/workspace.py'})).toContainText('working change');
 await page.getByRole('button',{name:'Staged',exact:true}).click();await expect(page.getByRole('region',{name:'Diff for src/workspace.py'})).toContainText('staged change');
 await page.getByRole('button',{name:'Attach diff to draft'}).click();await expect(composer).toContainText('Keep this draft');await expect(composer).toContainText('<attached diff: src/workspace.py (HEAD to index)>');
 expect(await page.evaluate(()=>(window as unknown as {fixtureController:{snapshot:()=>{queue:unknown[]}}}).fixtureController.snapshot().queue.length)).toBe(0);
 await page.keyboard.press('Control+Shift+P');await commands.getByRole('combobox',{name:'Search commands'}).fill('settings');await page.keyboard.press('Enter');
 await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();await expect(commands).not.toBeVisible();
});

test('palette Escape clears then closes without interrupting a running agent, including Command on macOS',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.keyboard.press('Meta+Shift+P');const commands=page.getByRole('dialog',{name:'Commands',exact:true});await expect(commands).toBeVisible();
 await commands.getByRole('combobox',{name:'Search commands'}).fill('nothing-matches-this');await expect(commands.getByText('No commands match. Try a different search.')).toBeVisible();
 await page.keyboard.press('Escape');await expect(commands).toBeVisible();await expect(commands.getByRole('combobox')).toHaveValue('');await page.keyboard.press('Escape');await expect(commands).not.toBeVisible();
 expect(await page.evaluate(()=>(window as unknown as {fixtureController:{snapshot:()=>{running:boolean}}}).fixtureController.snapshot().running)).toBe(true);
});

test('compact file chooser keeps focus and escapes back without moving the app viewport',async({page},info)=>{
 await page.setViewportSize({width:720,height:480});await page.goto('/test/fixtures/workspace.html');await page.keyboard.press('Control+Shift+G');
 const choose=page.getByRole('button',{name:'Choose changed file'});await expect(choose).toBeVisible();await choose.click();const filter=page.getByRole('textbox',{name:'Filter changed files'});await expect(filter).toBeFocused();await filter.fill('notes');
 await page.getByRole('button',{name:'Inspect notes.txt'}).click();await expect(page.getByRole('region',{name:'Diff for notes.txt'})).toContainText('Review this note');await expect(choose).toBeFocused();
 await choose.click();await page.keyboard.press('Escape');await expect(choose).toBeFocused();await expect(filter).not.toBeVisible();
 await expect(page.getByRole('banner')).toBeInViewport();expect(await page.locator('#root').evaluate(element=>element.scrollTop)).toBe(0);
 await page.screenshot({path:info.outputPath('compact-review.png')});
});

test('large changes and diff stay bounded and binary/incomplete states stay honest',async({page},info)=>{
 await page.goto('/test/fixtures/workspace.html?changes=large');await page.keyboard.press('Control+Shift+G');await page.getByRole('region',{name:'Diff for src/file-0.py'}).waitFor();
 expect(await page.getByRole('button',{name:/^Inspect /}).count()).toBe(100);expect(await page.locator('.diff-line').count()).toBeLessThan(80);
 await page.locator('.diff-scroll').evaluate(element=>{element.scrollTop=50000});await expect(page.locator('.diff-lines')).toContainText('new line 21');
 await page.screenshot({path:info.outputPath('large-diff-review.png')});
 await page.goto('/test/fixtures/workspace.html?changes=incomplete');await page.keyboard.press('Control+Shift+G');await expect(page.getByText('Inspection was incomplete.')).toBeVisible();await expect(page.getByText('Your working tree is clean.')).toHaveCount(0);
 await page.goto('/test/fixtures/workspace.html');await page.keyboard.press('Control+Shift+G');await page.getByRole('button',{name:'Inspect art/image.png'}).click();await expect(page.getByText('Binary file changed. A text diff is unavailable.')).toBeVisible();
});

test('live workflow approval survives navigation and resolves the actual child once',async({page},info)=>{
 await page.goto('/test/fixtures/workspace.html?tracked=1&approval=1');
 await page.evaluate(()=>{(window as unknown as {fixtureEmit:(event:Record<string,unknown>)=>void}).fixtureEmit({type:'message.complete',sessionId:'fixture-session',turnId:'fixture-turn'})});
 const composer=page.getByRole('textbox',{name:'Message Machina'});await composer.fill('/agents');await composer.press('Enter');
 await page.getByRole('button',{name:'Create agent',exact:true}).click();await page.getByRole('textbox',{name:'Name',exact:true}).fill('approval-reviewer');await page.getByRole('button',{name:'Save agent',exact:true}).click();
 await page.getByRole('button',{name:'Delegate task',exact:true}).click();await page.getByRole('textbox',{name:'Task',exact:true}).fill('Run workspace checks');await page.getByRole('button',{name:'Run task',exact:true}).click();
 await expect(page.getByRole('region',{name:'Background run'})).toContainText('Waiting for approval');
 await page.getByRole('button',{name:'Conversations',exact:true}).click();await expect(page.getByRole('button',{name:'Approval needed',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Approval needed',exact:true}).click();await expect(page.getByRole('region',{name:'Workflow approvals'})).toContainText('Run workspace checks');
 await page.getByText('Reviewed action',{exact:true}).click();await page.screenshot({path:info.outputPath('workflow-approval.png')});
 await page.getByRole('button',{name:'Allow once',exact:true}).click();await expect(page.getByRole('region',{name:'Delegation results'})).toContainText('Synthetic sequential delegation result 1');
 expect(await page.evaluate(()=>(window as unknown as {fixtureApprovalResolutions:Record<string,unknown>[]}).fixtureApprovalResolutions)).toEqual([{sessionId:'fixture-child',requestId:'fixture-approval',verdict:'approved',grant:'once'}]);
 expect(await page.evaluate(()=>(window as unknown as {fixtureOperationStarts:number}).fixtureOperationStarts)).toBe(1);
});
