import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test('real Chrome researcher login and study creation',async({page})=>{
 const credentials=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));
 await page.goto('http://admin.localhost:8000/login');
 await page.locator('[name=username]').fill(credentials.username);
 await page.locator('[name=password]').fill(credentials.password);
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByRole('heading',{name:'我的研究'})).toBeVisible();
 await page.locator('[name=title]').fill('Browser synthetic '+Date.now());
 await page.getByRole('button',{name:'创建',exact:true}).click();
 await expect(page.getByRole('heading',{name:'参与政策与名单'})).toBeVisible();
 await page.screenshot({path:'build/gui.png',fullPage:true});
 const cookies=await page.context().cookies('http://experiment.localhost:8000');
 expect(cookies.some(c=>c.name==='gep_admin')).toBe(false);
});
