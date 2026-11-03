import {test,expect} from '@playwright/test';

test('expanded navigation scrolls while Settings stays reachable at the minimum window size',async({page})=>{
 await page.setViewportSize({width:720,height:480});
 await page.goto('/test/fixtures/workspace.html');
 const navigation=page.getByRole('navigation',{name:'Workspace navigation'});
 await navigation.getByRole('button',{name:'More',exact:true}).click();
 const bounds=await navigation.evaluate(element=>({height:element.clientHeight,content:element.scrollHeight,bottom:element.getBoundingClientRect().bottom}));
 expect(bounds.content).toBeGreaterThan(bounds.height);
 const settings=page.locator('.sidebar-bottom').getByRole('button',{name:'Settings',exact:true});
 const footer=await settings.boundingBox();
 expect(footer).not.toBeNull();
 expect(footer!.y+footer!.height).toBeLessThanOrEqual(480);
 expect(bounds.bottom).toBeLessThanOrEqual(footer!.y);
 await navigation.getByRole('button',{name:'Commands',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Commands',exact:true})).toBeVisible();
 await settings.click();
 await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();
});
