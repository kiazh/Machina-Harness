import {expect,test} from '@playwright/test';

test('settings search has compact stable spacing and navigation keeps a single current item',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Open settings',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Settings',exact:true}),search=dialog.getByRole('textbox',{name:'Search settings',exact:true});
 const metrics=await search.evaluate(input=>{const box=input.closest('label')!,style=getComputedStyle(box);return {height:box.getBoundingClientRect().height,gap:parseFloat(style.gap),padding:parseFloat(style.paddingLeft),shrink:style.flexShrink}});
 expect(metrics.height).toBeGreaterThanOrEqual(34);expect(metrics.height).toBeLessThanOrEqual(38);expect(metrics.gap).toBeLessThanOrEqual(10);expect(metrics.padding).toBeGreaterThanOrEqual(8);expect(metrics.shrink).toBe('0');
 await dialog.getByRole('button',{name:'Workspace',exact:true}).click();
 await expect(dialog.locator('nav [aria-current="page"]')).toHaveCount(1);
 await expect(dialog.getByRole('button',{name:'Workspace',exact:true})).toHaveAttribute('aria-current','page');
 await search.fill('theme');await search.press('ArrowDown');await expect(dialog.getByRole('button',{name:'Appearance',exact:true})).toBeFocused();await page.keyboard.press('Enter');
 await expect(dialog.getByRole('heading',{name:'Appearance',exact:true})).toBeVisible();await expect(search).toHaveValue('');
});

test('dropdown focus does not change the stored setting until confirmed, and Escape keeps settings open',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');await page.getByRole('button',{name:'Open settings',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Settings',exact:true}),motion=dialog.getByRole('combobox',{name:'Motion',exact:true});
 const original=await motion.innerText();await motion.click();await expect(dialog.getByRole('listbox')).toBeVisible();
 await expect(dialog.getByRole('option',{selected:true})).toHaveCount(1);
 await page.keyboard.press('ArrowDown');await page.keyboard.press('Escape');
 await expect(dialog).toBeVisible();await expect(dialog.getByRole('listbox')).toHaveCount(0);await expect(motion).toHaveText(original);await expect(motion).toBeFocused();
 await motion.click();await dialog.getByRole('option',{name:'Off',exact:true}).click();await expect(motion).toHaveText('Off');
 await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();
});

test('dropdowns stay inside compact dark settings with legible surfaces',async({page},testInfo)=>{
 await page.setViewportSize({width:900,height:800});await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('button',{name:'Open settings',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Settings',exact:true});await dialog.locator('.preset-ink').click();await dialog.getByRole('button',{name:'Typography',exact:true}).click();
 await dialog.getByRole('combobox',{name:'Interface font',exact:true}).click();const popup=dialog.getByRole('listbox');await expect(popup).toBeVisible();
 const surface=await popup.evaluate(element=>({background:getComputedStyle(element).backgroundColor,color:getComputedStyle(element).color}));
 expect(surface.background).not.toBe('rgba(0, 0, 0, 0)');expect(surface.background).not.toBe(surface.color);
 const parent=await dialog.boundingBox(),menu=await popup.boundingBox();expect(menu!.x).toBeGreaterThanOrEqual(parent!.x);expect(menu!.x+menu!.width).toBeLessThanOrEqual(parent!.x+parent!.width+1);
 await page.screenshot({path:testInfo.outputPath('dark-settings-dropdown.png')});await page.keyboard.press('Escape');await expect(dialog).toBeVisible();
});
