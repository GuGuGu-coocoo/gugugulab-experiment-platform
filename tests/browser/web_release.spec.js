import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test('upload, isolated preview and publish real Godot Web package',async({page,context})=>{
 const c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));
 await page.goto('http://admin.localhost:8000/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.locator('[name=title]').fill('Web synthetic '+Date.now());await page.getByRole('button',{name:'创建',exact:true}).click();
 await page.locator('[name=package]').setInputFiles('build/synthetic_web.zip');await page.getByRole('button',{name:'上传并验证'}).click();
 await expect(page.getByRole('button',{name:'隔离预览（仅本地保存）'})).toBeVisible();
 const [preview]=await Promise.all([context.waitForEvent('page'),page.getByRole('button',{name:'隔离预览（仅本地保存）'}).click()]);
 await preview.waitForFunction(()=>globalThis.GECBridge!==undefined);await expect(preview.locator('#status')).toBeHidden({timeout:30000});
 await preview.screenshot({path:'build/godot_preview.png'});expect(new URL(preview.url()).hostname).toBe('experiment.localhost');
 expect(await preview.evaluate(()=>document.cookie.includes('gep_admin'))).toBe(false);await preview.close();
 await page.getByRole('button',{name:'批准合成发行'}).click();await page.locator('[name=state]').selectOption('open');await page.getByRole('button',{name:'更新招募'}).click();
 const link=await page.getByRole('link',{name:'打开参与入口'}).getAttribute('href');fs.writeFileSync('build/web/run_url.txt',link);fs.writeFileSync('build/web/study_url.txt',page.url());
 const run=await context.newPage();run.on('console',msg=>{if(msg.type()==='error')console.log('Godot browser:',msg.text())});await run.goto(link);
 await run.waitForFunction(()=>globalThis.GECBridge!==undefined);await expect(run.locator('#status')).toBeHidden({timeout:30000});await run.screenshot({path:'build/godot_web.png'});
});
