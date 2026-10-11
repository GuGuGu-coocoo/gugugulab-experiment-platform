// Standalone exported Godot rendering/input smoke in a fresh Chrome process.
// No participant identity, network backend or user browser profile is involved.
import {chromium} from '@playwright/test';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {resolve, extname, sep} from 'node:path';
const [webArg,evidenceArg]=process.argv.slice(2);
if(!webArg||!evidenceArg)throw Error('Pass exported Web directory and a new evidence directory');
const root=resolve(webArg), evidence=resolve(evidenceArg);
await mkdir(evidence,{recursive:false});
const types={'.html':'text/html','.js':'application/javascript','.wasm':'application/wasm','.pck':'application/octet-stream','.png':'image/png','.ogg':'audio/ogg'};
const server=createServer(async(req,res)=>{
  if(req.url==='/favicon.ico'){res.writeHead(204);return res.end();}
  const path=resolve(root,'.'+new URL(req.url,'http://localhost').pathname);
  if(!path.startsWith(root+sep)){res.writeHead(403);return res.end();}
  try{const bytes=await readFile(path);res.writeHead(200,{'content-type':types[extname(path)]||'text/plain'});res.end(bytes);}
  catch{res.writeHead(404);res.end();}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
let browser;
try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:720}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.waitForFunction(()=>!document.querySelector('#status'),{},{timeout:60000});
  await page.screenshot({path:evidence+'/01_title.png'});
  await page.locator('#canvas').press('Enter');
  await page.waitForTimeout(250);
  await page.screenshot({path:evidence+'/02_started.png'});
  await page.keyboard.press('Escape');
  await page.screenshot({path:evidence+'/03_paused.png'});
  // Resume through the actual rendered button hit region, then move/attack.
  await page.mouse.click(640,482);
  await page.keyboard.down('d');
  await page.waitForTimeout(900);
  await page.keyboard.up('d');
  await page.keyboard.down('k');
  await page.waitForTimeout(850);
  await page.keyboard.up('k');
  await page.keyboard.press('l');
  await page.keyboard.press('Space');
  await page.keyboard.press('e');
  await page.screenshot({path:evidence+'/04_actions.png'});
  await page.waitForTimeout(25000);
  await page.screenshot({path:evidence+'/05_end.png'});
  await page.keyboard.press('Enter');
  await page.waitForTimeout(250);
  await page.screenshot({path:evidence+'/06_retry.png'});
  if(errors.length)throw Error(JSON.stringify(errors));
  console.log(JSON.stringify({status:'PASS',evidence,checks:['real standalone Web loaded','no JavaScript console errors'],
    note:'Screenshots require visual inspection; game mechanics are asserted by real Godot harnesses, not inferred from pixels.'}));
}finally{await browser?.close();server.close();}
