import {test,expect} from '@playwright/test';
test('a definition opens the actual source file at its position and leaves settings',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Toggle code editor'}).click();await expect(page.getByRole('textbox',{name:'Code editor',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Open settings',exact:true}).click();await page.getByRole('button',{name:'Language servers',exact:true}).click();await page.getByRole('button',{name:'Definition',exact:true}).click();
 const location=page.getByRole('button',{name:'Open src/settings.py:6:5',exact:true});await expect(location).toBeEnabled();await page.screenshot({path:'docs/verification/20261102-lsp-navigation.png'});await location.click();
 await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeHidden();await expect(page.getByRole('tab',{name:'settings.py',exact:true})).toHaveAttribute('aria-selected','true');
 const cursor=await page.evaluate(()=>{const controller=(window as any).fixtureController,state=controller.editorStates.get(controller.snapshot().activeId),line=state.doc.lineAt(state.selection.main.head);return {line:line.number,character:state.selection.main.head-line.from}});expect(cursor).toEqual({line:6,character:4});
});
