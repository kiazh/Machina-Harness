import {test,expect} from '@playwright/test';

test('create and disable a scheduled job scoped to the active conversation',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('textbox',{name:'Message Machina'}).fill('/jobs');
 await page.getByRole('textbox',{name:'Message Machina'}).press('Enter');
 await page.getByRole('button',{name:'Create job',exact:true}).click();
 await page.getByRole('textbox',{name:'Name',exact:true}).fill('Check build');
 await page.getByRole('textbox',{name:'Task',exact:true}).fill('Check the latest build status');
 await page.getByRole('button',{name:'Save job',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Check build',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Disable',exact:true}).click();
 await expect(page.getByText('Disabled',{exact:true})).toBeVisible();
});

test('create, edit and remove an agent definition',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('textbox',{name:'Message Machina'}).fill('/agents');
 await page.getByRole('textbox',{name:'Message Machina'}).press('Enter');
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('textbox',{name:'Name',exact:true}).fill('fixture-reviewer');
 await page.getByRole('textbox',{name:'Description',exact:true}).fill('Review only');
 await page.getByRole('textbox',{name:'Instructions',exact:true}).fill('Read code and report concrete defects.');
 await page.getByRole('button',{name:'Save agent',exact:true}).click();
 const row=page.getByRole('article').filter({has:page.getByRole('heading',{name:'fixture-reviewer',exact:true})});
 await expect(row).toContainText('Review only');
 await row.getByRole('button',{name:'Edit',exact:true}).click();
 await page.getByRole('textbox',{name:'Description',exact:true}).fill('Read only reviewer');
 await page.getByRole('button',{name:'Save agent',exact:true}).click();
 await expect(row).toContainText('Read only reviewer');
 await row.getByRole('button',{name:'Delete',exact:true}).click();
 await row.getByRole('button',{name:'Confirm delete agent',exact:true}).click();
 await expect(row).toHaveCount(0);
});

test('builds a parallel delegation and inspects this conversation’s recorded history',async({page},testInfo)=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.evaluate(()=>{(window as unknown as {fixtureEmit:(event:Record<string,unknown>)=>void}).fixtureEmit({type:'message.complete',sessionId:'fixture-session',turnId:'fixture-turn'})});
 await page.getByRole('textbox',{name:'Message Machina'}).fill('/agents');
 await page.getByRole('textbox',{name:'Message Machina'}).press('Enter');
 for(const name of ['fixture-researcher','fixture-reviewer']){
  await page.getByRole('button',{name:'Create agent',exact:true}).click();
  await page.getByRole('textbox',{name:'Name',exact:true}).fill(name);
  await page.getByRole('button',{name:'Save agent',exact:true}).click();
  await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();
 }
 const row=page.getByRole('article').filter({has:page.getByRole('heading',{name:'fixture-researcher',exact:true})});
 await row.getByRole('button',{name:'Delegate task',exact:true}).click();
 await page.getByRole('textbox',{name:'Task',exact:true}).fill('Inspect parser');
 await page.getByRole('button',{name:'Add step',exact:true}).click();
 await page.getByRole('combobox',{name:'Agent 2',exact:true}).click();await page.getByRole('option',{name:'fixture-reviewer',exact:true}).click();
 await page.getByRole('textbox',{name:'Task 2',exact:true}).fill('Inspect tests independently');
 await page.getByRole('combobox',{name:'Execution',exact:true}).click();await page.getByRole('option',{name:'Parallel',exact:true}).click();
 await expect(page.getByText(/Steps run independently/)).toBeVisible();
 await page.screenshot({path:testInfo.outputPath('delegation-workflow.png')});
 await page.getByRole('button',{name:'Run workflow',exact:true}).click();
 const results=page.getByRole('region',{name:'Delegation results'});
 await expect(results).toContainText('Synthetic parallel delegation result 1');
 await expect(results).toContainText('Synthetic parallel delegation result 2');
 await page.getByRole('button',{name:'View delegation history',exact:true}).click();
 const history=page.getByRole('region',{name:'Delegation history'});
 await expect(history).toContainText('orchestrator → fixture-reviewer');
 await expect(history).toContainText('Inspect tests independently');
 await history.getByText('Response',{exact:true}).last().click();
 await expect(history).toContainText('Synthetic parallel delegation result 2');
});

test('inspect context budget and require idle state before compression',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('textbox',{name:'Message Machina'}).fill('/context');
 await page.getByRole('textbox',{name:'Message Machina'}).press('Enter');
 await expect(page.getByRole('progressbar',{name:'Context window usage'})).toHaveAttribute('aria-valuenow','25');
 await expect(page.getByRole('button',{name:'Compress context',exact:true})).toBeDisabled();
});
test('tracked delegation survives navigation without resubmitting its task',async({page})=>{
 await page.goto('/test/fixtures/workspace.html?tracked=1');
 await page.evaluate(()=>{(window as unknown as {fixtureEmit:(event:Record<string,unknown>)=>void}).fixtureEmit({type:'message.complete',sessionId:'fixture-session',turnId:'fixture-turn'})});
 const composer=page.getByRole('textbox',{name:'Message Machina'});
 await composer.fill('/agents');await composer.press('Enter');
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('textbox',{name:'Name',exact:true}).fill('tracked-reviewer');
 await page.getByRole('button',{name:'Save agent',exact:true}).click();
 await page.getByRole('button',{name:'Delegate task',exact:true}).click();
 await page.getByRole('textbox',{name:'Task',exact:true}).fill('Inspect parser');
 await page.getByRole('button',{name:'Run task',exact:true}).click();
 await expect(page.getByRole('region',{name:'Background run'})).toContainText('continue browsing');
 await page.getByRole('button',{name:'Conversations',exact:true}).click();
 await composer.fill('Keep this draft while the worker runs');
 await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'View run',exact:true}).click();
 await page.evaluate(()=>(window as unknown as {fixtureFinishOperation:()=>void}).fixtureFinishOperation());
 await expect(page.getByRole('region',{name:'Delegation results'})).toContainText('Synthetic sequential delegation result 1');
 expect(await page.evaluate(()=>(window as unknown as {fixtureOperationStarts:number}).fixtureOperationStarts)).toBe(1);
});
