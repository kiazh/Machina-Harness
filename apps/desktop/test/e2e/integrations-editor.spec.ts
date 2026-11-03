import {expect,test} from '@playwright/test';

test('editor shows syntax squiggles and removes them after a correction',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();
 const editor=page.getByRole('textbox',{name:'Code editor',exact:true});await editor.fill('def (');
 await expect(page.locator('.cm-lintRange-error').first()).toBeVisible();
 await editor.fill('value = 1\n');await expect(page.locator('.cm-lintRange-error')).toHaveCount(0);
});
test('harness communications shows both directions and a paired exchange',async({page},info)=>{
 await page.goto('/test/fixtures/workspace.html');const composer=page.getByRole('textbox',{name:'Message Machina'});await composer.fill('/harnesses');await composer.press('Enter');
 await page.getByRole('button',{name:'Communications',exact:true}).click();
 await page.getByRole('button',{name:/From Machina.*machina_status/}).click();
 const detail=page.getByRole('region',{name:'Exchange details'});await expect(detail).toContainText('To Machina · request');await expect(detail).toContainText('From Machina · response');
 await page.getByRole('button',{name:'Pause updates',exact:true}).click();await expect(page.getByText('Updates paused',{exact:true})).toBeVisible();
 await page.screenshot({path:info.outputPath('harness-communications.png')});
});
test('skill titles open exact-file review without an open conversation',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.evaluate(()=>{const target=window as unknown as {fixtureController:{newSession:()=>void;snapshot:()=>{session:unknown}},fixtureEmit:(event:Record<string,unknown>)=>void};target.fixtureEmit({type:'message.complete',sessionId:'fixture-session',turnId:'fixture-turn',text:'Fixture complete'});target.fixtureController.newSession();if(target.fixtureController.snapshot().session)throw new Error('Fixture did not close conversation')});
 await page.getByRole('button',{name:'Capabilities',exact:true}).click();await page.getByRole('button',{name:'Marketplace',exact:true}).click();
 await page.getByRole('region',{name:'Development on SkillsMP'}).getByRole('button',{name:'fixture-workflow-1',exact:true}).click();
 await expect(page.getByRole('region',{name:'Review marketplace skill'})).toContainText('Read these synthetic instructions');
 await expect(page.getByRole('button',{name:'Install reviewed skill',exact:true})).toBeEnabled();
});

test('self-hosted Firecrawl setup uses the local bridge endpoint and does not connect automatically',async({page},info)=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Capabilities',exact:true}).click();await page.getByRole('button',{name:'Connectors',exact:true}).click();
 await page.getByRole('button',{name:'Set up Firecrawl · self-hosted',exact:true}).click();
 await expect(page.getByRole('banner')).toBeInViewport();
 expect(await page.locator('#root').evaluate(element=>element.scrollTop)).toBe(0);
 await expect(page.getByRole('textbox',{name:'Server ID'})).toBeFocused();
 await expect(page.getByRole('textbox',{name:'Endpoint URL'})).toBeInViewport();
 await expect(page.getByRole('textbox',{name:'Endpoint URL'})).toHaveValue('http://127.0.0.1:3000/mcp');
 await expect(page.getByRole('checkbox',{name:'Allow local HTTP access'})).toBeChecked();
 await page.screenshot({path:info.outputPath('firecrawl-selfhosted-setup.png')});
});

test('all nine palettes retain readable settings controls and contained dropdowns',async({page},info)=>{
 test.setTimeout(60000);
 for(const preset of ['porcelain','ink','afterimage','volt','chrome','vellum','glacier','oxide','nocturne']){
  await page.addInitScript(value=>localStorage.setItem('machina.desktop.appearance.v1',JSON.stringify({preset:value,background:'art',motion:'off'})),preset);
  await page.goto('/test/fixtures/workspace.html');await expect(page.locator('.desktop-app')).toHaveAttribute('data-preset',preset);
  await page.getByRole('button',{name:'Open settings',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
  await dialog.getByRole('button',{name:'Models',exact:true}).click();const select=dialog.getByRole('combobox',{name:'Model provider'});await select.click();
  const popup=page.getByRole('listbox');await expect(popup).toBeVisible();const box=await popup.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(1440);expect(box!.y+box!.height).toBeLessThanOrEqual(900);
  if(preset==='porcelain'||preset==='afterimage')await page.screenshot({path:info.outputPath(`settings-${preset}.png`)});
  await page.keyboard.press('Escape');await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'Close settings'}).click();
 }
});
