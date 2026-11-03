import {expect,test} from '@playwright/test';

test.beforeEach(async({page})=>{
 await page.goto('/test/fixtures/workspace.html');
 await expect(page.getByRole('region',{name:'Agent harness'})).toBeVisible();
 await page.evaluate(()=>{
  const target=window as any;
  target.historyTestRequests=[];
  const bridge=target.fixtureController.bridge,request=bridge.request.bind(bridge);
  bridge.request=(method:string,params:Record<string,unknown>={})=>{
   target.historyTestRequests.push({method,params});return request(method,params);
  };
 });
});

test('browses bounded history, searches explicitly, and retains editor and composer work',async({page})=>{
 await page.getByRole('button',{name:'Toggle code editor'}).click();
 const editor=page.getByRole('textbox',{name:'Code editor',exact:true});
 await editor.click();await editor.press('Control+Home');await editor.pressSequentially('# history keeps this edit\n');
 const retainedEditor=await page.locator('.cm-editor').elementHandle();
 const draft=page.getByRole('textbox',{name:'Message Machina'});await draft.fill('Keep this unsent history draft');
 await page.getByRole('button',{name:'Conversation tools',exact:true}).click();
 await page.getByRole('button',{name:'Browse conversation history',exact:true}).click();
 const history=page.getByRole('dialog',{name:'Conversation history',exact:true});
 await expect(history).toBeVisible();await expect(history).toContainText('Synthetic workspace check');
 await expect(history).toContainText('100 messages · Page 1');
 await expect.poll(()=>history.getByRole('article').count()).toBeGreaterThan(0);
 expect(await history.getByRole('article').count()).toBeLessThan(30);
 await page.screenshot({path:'test-results/conversation-history-desktop.png'});
 await history.getByRole('button',{name:'Older',exact:true}).click();await expect(history).toContainText('Page 2');
 await history.getByRole('button',{name:'Newer',exact:true}).click();await expect(history).toContainText('100 messages · Page 1');
 const search=history.getByRole('searchbox',{name:'Search conversation history'});
 await search.fill('No literal match %_ [] 3db90f');
 expect(await page.evaluate(()=>(window as any).historyTestRequests.filter((call:any)=>call.method==='session.history').length)).toBe(3);
 await history.getByRole('button',{name:'Search',exact:true}).click();
 await expect(history).toContainText('No recorded messages match this search.');
 const requests=await page.evaluate(()=>(window as any).historyTestRequests.filter((call:any)=>call.method==='session.history'));
 expect(requests[0]).toEqual({method:'session.history',params:{sessionId:'fixture-session',limit:100}});
 expect(requests[1].params.cursor).toEqual(expect.any(String));
 expect(requests[2]).toEqual({method:'session.history',params:{sessionId:'fixture-session',limit:100}});
 expect(requests[3]).toEqual({method:'session.history',params:{sessionId:'fixture-session',limit:100,query:'No literal match %_ [] 3db90f'}});
 await page.keyboard.press('Escape');await expect(history).toBeHidden();
 await expect(draft).toHaveValue('Keep this unsent history draft');await expect(editor).toContainText('history keeps this edit');
 expect(await retainedEditor!.evaluate(element=>element.isConnected)).toBe(true);
 await page.evaluate(()=>{const controller=(window as any).fixtureController;controller.navigate('context');controller.navigate('chat')});
 await expect(page.getByRole('region',{name:'Agent harness'})).toBeVisible();
 const methods=await page.evaluate(()=>(window as any).historyTestRequests.map((call:any)=>call.method));
 expect(methods.filter((method:string)=>method==='session.history')).toHaveLength(4);
 expect(methods.some((method:string)=>['session.resume','prompt.submit','models.list','providers.models'].includes(method))).toBe(false);
});

test('routes history commands to scoped search and opens the full export action',async({page})=>{
 const composer=page.getByRole('textbox',{name:'Message Machina'});
 await composer.fill('/history No literal command match %_ 92eaf');await composer.press('Enter');
 const history=page.getByRole('dialog',{name:'Conversation history',exact:true});
 await expect(history).toBeVisible();await expect(history).toContainText('No recorded messages match this search.');
 await expect(history.getByRole('searchbox',{name:'Search conversation history'})).toHaveValue('No literal command match %_ 92eaf');
 expect(await page.evaluate(()=>(window as any).historyTestRequests.find((call:any)=>call.method==='session.history').params)).toEqual({sessionId:'fixture-session',limit:100,query:'No literal command match %_ 92eaf'});
 await history.getByRole('button',{name:'Export full conversation',exact:true}).click();
 await expect(history).toBeHidden();
 const tools=page.getByRole('dialog',{name:'Conversation tools',exact:true});
 await expect(tools).toBeVisible();await expect(tools.getByRole('combobox',{name:'Conversation action'})).toHaveValue('export');
 await page.keyboard.press('Escape');await composer.fill('/history');await composer.press('Enter');
 await expect(history).toBeVisible();await expect(history.getByRole('searchbox',{name:'Search conversation history'})).toHaveValue('');
 await expect(history).toContainText('100 messages · Page 1');
 const methods=await page.evaluate(()=>(window as any).historyTestRequests.map((call:any)=>call.method));
 expect(methods.some((method:string)=>['session.resume','prompt.submit','models.list','providers.models','session.export'].includes(method))).toBe(false);
});
