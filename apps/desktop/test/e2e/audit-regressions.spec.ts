import {expect,test} from '@playwright/test';

test('memory approvals distinguishes a failed load from a successfully empty queue',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('button',{name:'Open settings',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
 await dialog.getByRole('button',{name:'Memory approvals',exact:true}).click();
 await expect(dialog.getByRole('alert')).toContainText('Fixture does not implement memory.pending');
 await expect(dialog.getByText('No pending memories.',{exact:true})).toHaveCount(0);
 await page.evaluate(()=>{
  const target=window as unknown as {fixtureController:{bridge:{request:(method:string,params?:Record<string,unknown>)=>Promise<unknown>}}};
  const request=target.fixtureController.bridge.request.bind(target.fixtureController.bridge);
  target.fixtureController.bridge.request=async(method,params)=>method==='memory.pending'?{pending:[]}:request(method,params);
 });
 await dialog.getByRole('button',{name:'Refresh',exact:true}).click();
 await expect(dialog.getByRole('alert')).toHaveCount(0);
 await expect(dialog.getByText('No pending memories.',{exact:true})).toBeVisible();
});

test('model searches explain an empty result and offer a clear search action',async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('button',{name:'Open settings',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
 await dialog.getByRole('button',{name:'Models',exact:true}).click();
 await expect(dialog.getByText('Alternate fixture model',{exact:true})).toBeVisible();
 const search=dialog.getByRole('textbox',{name:'Search models',exact:true});
 await search.fill('nonexistent-model');
 await expect(dialog.getByRole('status')).toContainText('No models match');
 await dialog.getByRole('button',{name:'Clear model search',exact:true}).click();
 await expect(search).toHaveValue('');
 await expect(dialog.getByText('Alternate fixture model',{exact:true})).toBeVisible();
});

test('expanded sidebar navigation and settings stay reachable at the native minimum size',async({page},testInfo)=>{
 await page.setViewportSize({width:720,height:480});
 await page.goto('/test/fixtures/workspace.html');
 await page.getByRole('button',{name:'More',exact:true}).click();
 const sidebar=page.getByRole('complementary',{name:'Workspace sidebar',exact:true});
 const settings=sidebar.getByRole('button',{name:'Settings',exact:true});
 await expect(settings).toBeInViewport();
 const commands=page.getByRole('navigation',{name:'Workspace navigation',exact:true}).getByRole('button',{name:'Commands',exact:true});
 await commands.focus();
 await expect(commands).toBeInViewport();
 await commands.click();
 await expect(page.getByRole('heading',{name:'Commands',exact:true})).toBeVisible();
 await expect(settings).toBeInViewport();
 await settings.click();
 const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
 await expect(dialog.getByRole('textbox',{name:'Search settings',exact:true})).toBeInViewport();
 await page.screenshot({path:testInfo.outputPath('minimum-size-sidebar-settings.png')});
});
