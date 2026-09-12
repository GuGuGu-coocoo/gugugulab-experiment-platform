import {test,expect} from '@playwright/test';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import http from 'node:http';import {spawn,execFileSync} from 'node:child_process';
const endpoint='http://admin.localhost:8014';
function inspect(study){return JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;c=sqlite3.connect("local_data/gep.sqlite3");s=sys.argv[1].replace("-","");print(json.dumps({"builds":c.execute("select digest from core_build where study_id=? order by digest",[s]).fetchall(),"releases":c.execute("select id,build_id,approved from core_release where study_id=? order by id",[s]).fetchall()}))',study],{encoding:'utf8'}));}
function send(route,headers,body,declared=body.length,interrupt=false){return new Promise(resolve=>{
 const req=http.request({hostname:'127.0.0.1',port:8014,agent:false,path:route,method:'POST',headers:{Host:'admin.localhost:8014',...headers,'Content-Length':declared}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});
 req.on('error',()=>resolve(0));req.setTimeout(10000,()=>req.destroy());
 if(interrupt)req.write(body,()=>req.destroy());else req.end(body);
});}
for(const mode of ['before_rename','after_rename'])test(`interrupted Web upload is never published; retry after ${mode}`,async({page,context})=>{
 test.setTimeout(60000);
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-upload-')),archive=path.join(dir,'package.zip'),marker=path.join(dir,'boundary');
 execFileSync('.venv/bin/python',['-c','import zipfile,json,sys,uuid;z=zipfile.ZipFile("build/synthetic_web.zip");out=zipfile.ZipFile(sys.argv[1],"w",compression=zipfile.ZIP_DEFLATED);[(out.writestr(i.filename,json.dumps({**json.loads(z.read(i)),"version":"upload-fault-"+uuid.uuid4().hex}) if i.filename=="manifest.json" else z.read(i))) for i in z.infolist()];out.close()',archive]);
 const studyUrl=fs.readFileSync('build/web/study_url.txt','utf8'),route=new URL(studyUrl).pathname,study=route.split('/').at(-1),before=inspect(study);
 const runUrl=fs.readFileSync('build/web/run_url.txt','utf8');const oldResponse=await page.request.get(runUrl);expect(oldResponse.ok()).toBe(true);const oldHTML=await oldResponse.body();
 const config=JSON.parse(fs.readFileSync('build/native/connection.json','utf8'));
 let server;
 const start=fault=>server=spawn('.venv/bin/gunicorn',['upload_fault_app:application','--bind','127.0.0.1:8014','--workers','1','--threads','2','--access-logfile','/dev/null'],{env:{...process.env,PYTHONPATH:path.resolve('tests/fixtures')+':'+path.resolve('server'),GEP_DATA_DIR:path.resolve('local_data'),GEP_EXPECTED_INSTANCE:config.instance_id,GEP_UPLOAD_FAULT:fault,GEP_UPLOAD_FAULT_MARKER:marker},stdio:'ignore',detached:true});
 const stop=async()=>{if(!server)return;const current=server;server=null;const done=new Promise(r=>current.once('exit',r));try{process.kill(-current.pid,'SIGKILL')}catch{}await done;};
 start(mode);
 try{
  await expect.poll(async()=>{try{return (await page.request.get(endpoint+'/login')).status()}catch{return 0}},{timeout:10000}).toBe(200);
  const c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));await page.goto(endpoint+'/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.goto(endpoint+route);
  const csrf=await page.locator('[name=csrfmiddlewaretoken]').first().inputValue();const cookie=(await context.cookies(endpoint)).map(c=>c.name+'='+c.value).join(';');
  const boundary='gep-upload-boundary';const raw=fs.readFileSync(archive),prefix=Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="op"\r\n\r\nupload\r\n--${boundary}\r\nContent-Disposition: form-data; name="package"; filename="package.zip"\r\nContent-Type: application/zip\r\n\r\n`),suffix=Buffer.from(`\r\n--${boundary}--\r\n`),body=Buffer.concat([prefix,raw,suffix]);
  const headers={'Content-Type':'multipart/form-data; boundary='+boundary,'X-CSRFToken':csrf,Cookie:cookie,Origin:endpoint};
  expect(await send(route,headers,Buffer.alloc(0),128*1024*1024+65537)).toBe(413);
  const truncated=Buffer.concat([prefix,raw.subarray(0,Math.floor(raw.length/2)),suffix]);expect(await send(route,headers,truncated)).toBe(422);
  expect(await send(route,headers,body.subarray(0,131072),body.length,true)).toBe(0);
  expect(inspect(study)).toEqual(before);
  const upload=send(route,headers,body);
  await expect.poll(()=>fs.existsSync(marker),{timeout:20000}).toBe(true);await stop();expect(await upload).toBe(0);
  expect(inspect(study)).toEqual(before);expect(await (await page.request.get(runUrl)).body()).toEqual(oldHTML);
  start('');await expect.poll(async()=>{try{return (await page.request.get(endpoint+'/login')).status()}catch{return 0}}).toBe(200);
  expect(await send(route,headers,body)).toBe(302);
  const after=inspect(study);expect(after.builds.length).toBe(before.builds.length+1);expect(after.releases).toEqual(before.releases);
  expect(await send(route,headers,body)).toBe(302);expect(inspect(study)).toEqual(after);
  expect(await (await page.request.get(runUrl)).body()).toEqual(oldHTML);
 }finally{await stop();}
});
