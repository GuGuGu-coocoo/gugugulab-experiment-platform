import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test('register native build and export public config through GUI',async({page})=>{
 const creds=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));
 await page.goto('http://admin.localhost:8000/login');await page.locator('[name=username]').fill(creds.username);await page.locator('[name=password]').fill(creds.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.locator('[name=title]').fill('Native synthetic '+Date.now());await page.getByRole('button',{name:'创建',exact:true}).click();
 await page.locator('[name=descriptor]').fill(fs.readFileSync('build/native/descriptor.json','utf8'));await page.getByRole('button',{name:'登记不可变构建'}).click();
 await page.getByRole('button',{name:'批准合成发行'}).click();
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'导出连接配置'}).click()]);
 await download.saveAs('build/native/connection.json');
 const config=JSON.parse(fs.readFileSync('build/native/connection.json','utf8'));expect(config.purpose).toBe('synthetic');expect(JSON.stringify(config)).not.toMatch(/password|token|secret/);
 await page.locator('[name=state]').selectOption('open');await page.getByRole('button',{name:'更新招募'}).click();
 fs.writeFileSync('build/native/study_url.txt',page.url());
});
