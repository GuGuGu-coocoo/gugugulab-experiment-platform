import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {isolated} from './isolated_target.mjs';
// Isolated-instance target: never the local dev instance or the protected
// acceptance database (GEP_DEV_INSTANCE=1 is the explicit dev opt-in).
const target=isolated();
test('register native build and export public config through GUI',async({page})=>{
 const creds=target.credentials;
 await page.goto(target.admin+'/login');await page.locator('[name=username]').fill(creds.username);await page.locator('[name=password]').fill(creds.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.locator('[name=title]').fill('Native synthetic '+Date.now());await page.getByRole('button',{name:'创建',exact:true}).click();
 const studyUrl=page.url();
 await page.goto(studyUrl+'/builds');await page.locator('[name=descriptor]').fill(fs.readFileSync(target.descriptor,'utf8'));await page.getByRole('button',{name:'登记不可变构建'}).click();
 await page.getByRole('button',{name:'批准合成发行'}).click();
 if(!target.connection)throw new Error('GEP_ISO_CONNECTION is required for this spec');
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'导出连接配置'}).click()]);
 await download.saveAs(target.connection);
 const config=JSON.parse(fs.readFileSync(target.connection,'utf8'));expect(config.purpose).toBe('synthetic');expect(JSON.stringify(config)).not.toMatch(/password|token|secret/);
 await page.goto(studyUrl+'/recruitment');
 await Promise.all([page.waitForResponse(r=>r.url().includes('/studies/')&&r.request().method()==='POST'),page.locator('[name=state]').selectOption('open')]);await page.waitForLoadState('load');
 fs.writeFileSync(target.study_url_file,studyUrl);
});
