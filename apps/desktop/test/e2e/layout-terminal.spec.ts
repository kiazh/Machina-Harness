import {test,expect} from '@playwright/test';

test('temporarily hides navigation on a narrow editor layout and restores the saved layout when widened',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();
 const app=page.locator('.desktop-app');await expect(app).toHaveAttribute('data-sidebar','true');
 await page.setViewportSize({width:900,height:800});await expect(app).toHaveAttribute('data-sidebar','false');
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('machina.desktop.layout.v1')!).sidebar)).toBe(true);
 await page.setViewportSize({width:1440,height:900});await expect(app).toHaveAttribute('data-sidebar','true');
});

test('saved layout can place code on the left and retain edited text',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();const editor=page.getByRole('textbox',{name:'Code editor',exact:true});await editor.click();await editor.pressSequentially('# retained left layout');
 await page.getByRole('button',{name:'Open settings'}).click();const dialog=page.getByRole('dialog',{name:'Settings',exact:true});await dialog.getByRole('button',{name:'Workspace',exact:true}).click();await dialog.getByRole('combobox',{name:'Code editor position'}).click();await dialog.locator('[role=option][data-select-value=left]').click();await dialog.getByRole('button',{name:'Close settings'}).click();
 const code=await page.getByRole('region',{name:'Code workspace',exact:true}).boundingBox(),harness=await page.getByRole('region',{name:'Agent harness',exact:true}).boundingBox();expect(code!.x).toBeLessThan(harness!.x);await expect(editor).toContainText('retained left layout');
 await page.getByRole('button',{name:'Capabilities',exact:true}).click();await expect(page.getByRole('heading',{name:'Your skills',exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('custom shortcuts take effect while shell access stays native-only in the web preview',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Open settings'}).click();const dialog=page.getByRole('dialog',{name:'Settings',exact:true});await dialog.getByRole('button',{name:'Keyboard shortcuts',exact:true}).click();await dialog.getByRole('button',{name:'Change Find a project file shortcut',exact:true}).click();await dialog.getByRole('textbox',{name:'Shortcut for Find a project file',exact:true}).press('Control+Shift+p');await dialog.getByRole('button',{name:'Close settings'}).click();await page.keyboard.press('Control+Shift+p');await expect(page.getByRole('dialog',{name:'Open a file',exact:true})).toBeVisible();await page.keyboard.press('Escape');await page.getByRole('button',{name:'Toggle terminal',exact:true}).click();await expect(page.getByText('Interactive shells run in the native desktop app. This browser preview has no shell access.',{exact:true})).toBeVisible();
});
