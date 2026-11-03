import {test,expect} from '@playwright/test';
test('plain versioned completion shows an inline suffix and Tab inserts it',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();const editor=page.getByRole('textbox',{name:'Code editor',exact:true});await editor.click();await editor.press('Control+End');await editor.pressSequentially('\nPa');await expect(page.locator('.cm-inline-ghost')).toHaveText('th');await editor.press('Tab');await expect(page.locator('.cm-inline-ghost')).toHaveCount(0);await expect(editor).toContainText('Path');
});
