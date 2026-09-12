import {test,expect} from '@playwright/test';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {spawn} from 'node:child_process';
test('native new participation protects old data and bounded buffer rejects overflow',async()=>{
 const storage=fs.mkdtempSync(path.join(os.tmpdir(),'gep-shared-'));
 const proc=spawn('godot',['--headless','--path','examples/synthetic_experiment','--script',path.resolve('tests/native/shared_device_harness.gd')],{env:{...process.env,GEP_SYNTHETIC_STORAGE:storage,GEP_TEST_CONFIG:path.resolve('build/native/connection.json')},stdio:['ignore','pipe','pipe']});
 let out='';proc.stdout.on('data',b=>out+=b);proc.stderr.on('data',b=>out+=b);
 try{await expect.poll(()=>out,{timeout:12000}).toContain('NATIVE_SHARED_DEVICE_AND_BACKPRESSURE_VERIFIED');}finally{proc.kill('SIGTERM');}
});
