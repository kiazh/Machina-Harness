import {expect,test} from '@playwright/test';

test('marketplace opens with featured skills and independent source rankings, then scopes a search',async({page},testInfo)=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Capabilities',exact:true}).click();await page.getByRole('button',{name:'Marketplace',exact:true}).click();
 await expect(page.getByRole('region',{name:'Featured skills'})).toBeVisible();await expect(page.getByRole('region',{name:'Popular on skills.sh'})).toContainText('installs');await expect(page.getByRole('region',{name:'Development on SkillsMP'})).toContainText('GitHub stars');
 await expect(page.locator('.feature-toolbar nav [aria-current="page"]')).toHaveCount(1);expect(await page.getByRole('button',{name:'Installed',exact:true}).evaluate(button=>getComputedStyle(button).borderBottomColor)).toBe('rgba(0, 0, 0, 0)');
 await page.screenshot({path:testInfo.outputPath('marketplace-discovery.png')});
 const active=await page.evaluate(()=>(window as unknown as {fixtureController:{snapshot:()=>{running:boolean}}}).fixtureController.snapshot().running);await page.getByRole('combobox',{name:'Skill catalog'}).click();await page.keyboard.press('Escape');expect(await page.evaluate(()=>(window as unknown as {fixtureController:{snapshot:()=>{running:boolean}}}).fixtureController.snapshot().running)).toBe(active);
 await page.getByRole('combobox',{name:'Skill catalog'}).click();await page.getByRole('option',{name:'SkillsMP',exact:true}).click();await expect(page.getByRole('region',{name:'Popular on skills.sh'})).toHaveCount(0);
 const search=page.getByRole('textbox',{name:'Search marketplace'});await search.fill('workflow');
 expect(await page.evaluate(()=>(window as unknown as {fixtureMarketplaceCalls:{method:string}[]}).fixtureMarketplaceCalls.filter(row=>row.method==='skills.marketplace.search').length)).toBe(0);
 await search.press('Enter');await expect(page.getByText('4 matches for “workflow”')).toBeVisible();
 await page.getByRole('button',{name:'Review fixture-workflow-1',exact:true}).click();await expect(page.getByRole('region',{name:'Review marketplace skill'})).toContainText('Read these synthetic instructions');
 expect(await page.evaluate(()=>(window as unknown as {fixtureMarketplaceCalls:{method:string;params:Record<string,unknown>}[]}).fixtureMarketplaceCalls.find(row=>row.method==='skills.marketplace.preview')?.params)).toMatchObject({source:'fixture/skills',skill:'workflow-1'});
});
