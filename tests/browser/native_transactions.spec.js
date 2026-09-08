import {test,expect} from '@playwright/test';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {spawn,execFileSync} from 'node:child_process';
test('native SQLite abort and process kill preserve the prior complete checkpoint',async()=>{
 const launch=(dir,extra=[])=>spawn('godot',['--headless','--path','examples/synthetic_experiment','--script',path.resolve('tests/native/storage_harness.gd'),'--',...extra],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir},stdio:['ignore','pipe','pipe']});
 const firstDir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-tx-abort-'));const first=launch(firstDir);let text='';first.stdout.on('data',b=>text+=b);expect(await new Promise(r=>first.once('exit',r))).toBe(0);expect(text).toContain('TRANSACTION_FAILURE_PRESERVED_BOUNDARY');
 const secondDir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-tx-kill-'));const second=launch(secondDir,['--pause']);
 try{await expect.poll(()=>fs.existsSync(path.join(secondDir,'before_commit')),{timeout:10000}).toBe(true);second.kill('SIGKILL');await new Promise(r=>second.once('exit',r));
 const state=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute("select value from sessions").fetchone()[0])',path.join(secondDir,'queue.sqlite')],{encoding:'utf8'}));
 expect(state.records).toHaveLength(1);expect(state.checkpoint.next_trial).toBe(1);expect(state.checkpoint.dependencies).toEqual([state.records[0].event_id]);
 }finally{second.kill('SIGKILL');}
});
