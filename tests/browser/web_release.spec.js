import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {isolated} from './isolated_target.mjs';
// Isolated-instance target: never the local dev instance or the protected
// acceptance database (GEP_DEV_INSTANCE=1 is the explicit dev opt-in).
const target=isolated();
test('upload, isolated preview and publish real Godot Web package',async({page,context})=>{
 const c=target.credentials;
 await page.goto(target.admin+'/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.locator('[name=title]').fill('Web synthetic '+Date.now());await page.getByRole('button',{name:'创建',exact:true}).click();
 const studyUrl=page.url();
 await page.goto(studyUrl+'/builds');
 await page.locator('#package-upload input[name=package]').setInputFiles(target.web_zip);await page.locator('#package-upload').getByRole('button',{name:'上传并验证'}).click();
 await expect(page.getByRole('button',{name:'隔离预览（仅本地保存）'})).toBeVisible();
 const [preview]=await Promise.all([context.waitForEvent('page'),page.getByRole('button',{name:'隔离预览（仅本地保存）'}).click()]);
 await preview.waitForFunction(()=>globalThis.GECBridge!==undefined);await expect(preview.locator('#status')).toBeHidden({timeout:30000});
 await preview.screenshot({path:'build/godot_preview.png'});expect(new URL(preview.url()).hostname).toBe(new URL(target.experiment).hostname);
 expect(await preview.evaluate(()=>document.cookie.includes('gep_admin'))).toBe(false);await preview.close();
 await page.getByRole('button',{name:'批准合成发行'}).click();
 const link=await page.getByRole('link',{name:'打开参与入口'}).getAttribute('href');
 await page.goto(studyUrl+'/recruitment');
 await Promise.all([page.waitForResponse(r=>r.url().includes('/studies/')&&r.request().method()==='POST'),page.locator('[name=state]').selectOption('open')]);await page.waitForLoadState('load');
 fs.writeFileSync(target.run_url_file,link);fs.writeFileSync(target.study_url_file,studyUrl);
 const run=await context.newPage();run.on('console',msg=>{if(msg.type()==='error')console.log('Godot browser:',msg.text())});await run.goto(link);
 await run.waitForFunction(()=>globalThis.GECBridge!==undefined);await expect(run.locator('#status')).toBeHidden({timeout:30000});await run.screenshot({path:'build/godot_web.png'});
});
