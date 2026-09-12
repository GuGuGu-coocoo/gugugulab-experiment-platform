import {test,expect} from '@playwright/test';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {spawn,execFileSync} from 'node:child_process';
function snapshot(dir){return JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;print(json.dumps([json.loads(r[0]) for r in sqlite3.connect(sys.argv[1]).execute("select value from sessions")]))',path.join(dir,'queue.sqlite')],{encoding:'utf8'}));}
function launch(dir,args){
 const proc=spawn('godot',['--headless','--path','examples/synthetic_experiment','--script',path.resolve('tests/native/cleanup_harness.gd'),'--',...args],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir,GEP_TEST_CONFIG:path.resolve('build/native/connection.json')},stdio:['ignore','pipe','pipe']});
 let output='';proc.stdout.on('data',b=>output+=b);proc.stderr.on('data',b=>output+=b);
 const exited=new Promise(resolve=>proc.once('exit',resolve));
 return {proc,exited,output:()=>output};
}
for(const mode of ['ack_abort','completion_abort','cleanup_abort','cleanup_before_commit','cleanup_after_commit']){
 test(`native durable completion and cleanup: ${mode}`,async()=>{
  test.setTimeout(45000);
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-cleanup-'));const run=launch(dir,[mode]);
  try{
   if(mode.includes('commit')){
    await expect.poll(()=>fs.existsSync(path.join(dir,'fault_boundary')),{timeout:20000}).toBe(true);
    run.proc.kill('SIGKILL');await run.exited;
    const saved=snapshot(dir)[0];
    if(mode==='cleanup_before_commit'){
     expect(saved.records).toHaveLength(4);expect(saved.pending).toEqual([]);expect(saved.checkpoint).toBeNull();expect(saved.complete_ack.state).toBe('complete');
    }else expect(saved.kind).toBe('cleaned');
    const reopened=launch(dir,['--reopen']);
    try {expect(await reopened.exited,reopened.output()).toBe(0);expect(reopened.output()).toContain('NATIVE_CLEANUP_REOPEN_VERIFIED');}finally{reopened.proc.kill('SIGTERM');}
   }else{expect(await run.exited,run.output()).toBe(0);expect(run.output()).toContain('NATIVE_CLEANUP_FAILURE_PRESERVED_DATA');}
   const rows=snapshot(dir);expect(rows).toHaveLength(1);expect(rows[0]).toEqual({id:rows[0].id,kind:'cleaned',state:'remote_acknowledged'});
   const records=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;c=sqlite3.connect("local_data/gep.sqlite3");print(json.dumps([json.loads(r[0]) for r in c.execute("select envelope from core_event where session_id=?",[sys.argv[1].replace("-","")])]))',rows[0].id],{encoding:'utf8'}));
   expect(records).toHaveLength(4);expect(new Set(records.map(e=>e.event_id)).size).toBe(4);expect(records.filter(e=>e.event_type==='exp.rt').map(e=>e.payload.rt_ms).sort()).toEqual([217.25,321.5]);
  }finally{run.proc.kill('SIGTERM');}
 });
}
