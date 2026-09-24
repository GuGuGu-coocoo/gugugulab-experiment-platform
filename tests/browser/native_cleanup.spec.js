import {test,expect} from '@playwright/test';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {spawn,execFileSync} from 'node:child_process';
/* Isolated-instance native cleanup boundary. The connection, the server
 * database and the scratch root are REQUIRED from the orchestrator
 * (GEP_ISO_CONNECTION / GEP_ISO_DB / GEP_ISO_SCRATCH); they are validated
 * before this spec starts Godot or opens any database. A missing, relative,
 * dev-default or protected-acceptance input is refused - this spec never
 * silently falls back to build/native/connection.json or the protected
 * local_data/gep.sqlite3, and its scratch storage is the orchestrator's unique
 * per-run root, never the system temporary directory. */
const ROOT=process.cwd();
export function requireCleanupTarget(env,root){
 const required=['GEP_ISO_CONNECTION','GEP_ISO_DB','GEP_ISO_SCRATCH'];
 const missing=required.filter(name=>!String(env[name]||'').trim());
 if(missing.length)throw new Error('native cleanup requires an isolated instance; missing '+missing.join(', ')+
  ' (never falls back to the dev instance or the protected acceptance database)');
 for(const name of required)if(!path.isAbsolute(String(env[name])))throw new Error('native cleanup refused a relative '+name+': '+env[name]);
 const connection=path.resolve(String(env.GEP_ISO_CONNECTION)),db=path.resolve(String(env.GEP_ISO_DB)),scratch=path.resolve(String(env.GEP_ISO_SCRATCH));
 const refuse=message=>{throw new Error('native cleanup refused '+message)};
 const oldNative=path.join(root,'build','native'),oldDb=path.join(root,'local_data','gep.sqlite3'),oldAcceptance=path.join(root,'local_data','independent_acceptance_20260912');
 const under=(value,base)=>value===base||value.startsWith(base+path.sep);
 if(under(connection,oldNative))refuse('a protected historical connection: '+connection);
 if(connection===oldDb||under(connection,oldAcceptance))refuse('a protected database path: '+connection);
 if(db===oldDb||under(db,oldAcceptance))refuse('a protected acceptance/dev database: '+db);
 if(!under(scratch,root))refuse('a scratch root outside the project: '+scratch);
 if(under(scratch,os.tmpdir())||under(scratch,oldNative)||under(scratch,oldAcceptance))refuse('a protected or system scratch root: '+scratch);
 let config;try{config=JSON.parse(fs.readFileSync(connection,'utf8'));}catch(error){refuse('an unreadable connection ('+error.code+ '): '+connection)}
 if(!config||typeof config!=='object'||!config.instance_id||!config.study_id)refuse('a connection without an isolated instance binding: '+connection);
 if(!fs.statSync(scratch).isDirectory())refuse('a scratch root that is not a directory: '+scratch);
 if(!fs.statSync(db).isFile())refuse('a missing isolated server database: '+db);
 return {CONNECTION:connection,SERVER_DB:db,SCRATCH:scratch};
}
const {CONNECTION,SERVER_DB,SCRATCH}=requireCleanupTarget(process.env,ROOT);
function snapshot(dir){return JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;print(json.dumps([json.loads(r[0]) for r in sqlite3.connect(sys.argv[1]).execute("select value from sessions")]))',path.join(dir,'queue.sqlite')],{encoding:'utf8'}));}
function serverEvents(id){return Number(execFileSync('.venv/bin/python',['-c','import sqlite3,sys;print(sqlite3.connect(sys.argv[2]).execute("select count(*) from core_event where session_id=?",[sys.argv[1].replace("-","")]).fetchone()[0])',id,SERVER_DB],{encoding:'utf8'}));}
function serverRecords(id){return JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;c=sqlite3.connect(sys.argv[2]);print(json.dumps([json.loads(r[0]) for r in c.execute("select envelope from core_event where session_id=?",[sys.argv[1].replace("-","")])]))',id,SERVER_DB],{encoding:'utf8'}));}
function advanceRetry(dir){
 // Explicit, recorded fixture manipulation: the confirmed delivery contract
 // keeps the persisted retry deadline, so a retry that is not due must not be
 // sent. Advancing the fixture deadline makes the *next real* retry due; the
 // production backoff itself is never changed. Cleaned tombstones are never
 // touched (they have no retry schedule).
 execFileSync('.venv/bin/python',['-c','import json,sqlite3,sys\nc=sqlite3.connect(sys.argv[1])\nfor sid,value in c.execute("select id,value from sessions").fetchall():\n s=json.loads(value)\n if "retry_at" in s:\n  s["retry_at"]=0\n  c.execute("update sessions set value=? where id=?",[json.dumps(s),sid])\nc.commit()',path.join(dir,'queue.sqlite')]);
}
function launch(dir,args){
 const proc=spawn('godot',['--headless','--path','examples/synthetic_experiment','--script',path.resolve('tests/native/cleanup_harness.gd'),'--',...args],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir,GEP_TEST_CONFIG:CONNECTION},stdio:['ignore','pipe','pipe']});
 let output='';proc.stdout.on('data',b=>output+=b);proc.stderr.on('data',b=>output+=b);
 const exited=new Promise(resolve=>proc.once('exit',resolve));
 return {proc,exited,output:()=>output};
}
const preserved={
 ack_abort:{pending:4,checkpoint:true,complete:false,events:4,not_due:true},
 completion_abort:{pending:0,checkpoint:true,complete:false,events:4,not_due:true},
 cleanup_abort:{pending:0,checkpoint:false,complete:true,events:4,not_due:false},
};
for(const mode of ['ack_abort','completion_abort','cleanup_abort','cleanup_before_commit','cleanup_after_commit']){
 test(`native durable completion and cleanup: ${mode}`,async()=>{
  test.setTimeout(90000);
  const dir=fs.mkdtempSync(path.join(SCRATCH,'cleanup-'));const run=launch(dir,[mode]);
  try{
   if(mode.includes('commit')){
    await expect.poll(()=>fs.existsSync(path.join(dir,'fault_boundary')),{timeout:20000}).toBe(true);
    run.proc.kill('SIGKILL');await run.exited;
    const saved=snapshot(dir)[0];
    if(mode==='cleanup_before_commit'){
     expect(saved.records).toHaveLength(4);expect(saved.pending).toEqual([]);expect(saved.checkpoint).toBeNull();expect(saved.complete_ack.state).toBe('complete');
    }else{
     expect(saved.kind).toBe('cleaned');
    }
    advanceRetry(dir);
    const reopened=launch(dir,['--reopen']);
    try {expect(await reopened.exited,reopened.output()).toBe(0);expect(reopened.output()).toContain('NATIVE_CLEANUP_REOPEN_VERIFIED');}finally{reopened.proc.kill('SIGTERM');}
   }else{
    // The confirmed backoff contract: the harness pauses exactly at the
    // preserved interruption, nothing is retried while the persisted deadline
    // is not due, and only the reopen performs the real retry after the
    // recorded fixture deadline is advanced.
    await expect.poll(()=>fs.existsSync(path.join(dir,'fault_boundary')),{timeout:20000}).toBe(true);
    const saved=snapshot(dir)[0];
    const expected=preserved[mode];
    expect(saved.pending).toHaveLength(expected.pending);
    expect(Boolean(saved.checkpoint)).toBe(expected.checkpoint);
    expect(Boolean(saved.complete_ack)).toBe(expected.complete);
    expect(Number(saved.retry_at||0)>Math.floor(Date.now()/1000)).toBe(expected.not_due);
    expect(serverEvents(saved.id)).toBe(expected.events);
    run.proc.kill('SIGKILL');await run.exited;
    advanceRetry(dir);
    const reopened=launch(dir,['--reopen']);
    try {expect(await reopened.exited,reopened.output()).toBe(0);expect(reopened.output()).toContain('NATIVE_CLEANUP_REOPEN_VERIFIED');}finally{reopened.proc.kill('SIGTERM');}
   }
   const rows=snapshot(dir);expect(rows).toHaveLength(1);
   const binding=JSON.parse(fs.readFileSync(CONNECTION,'utf8'));
   expect(rows[0]).toEqual({id:rows[0].id,kind:'cleaned',state:'remote_acknowledged',instance_id:binding.instance_id,study_id:binding.study_id});
   const records=serverRecords(rows[0].id);
   expect(records).toHaveLength(4);expect(new Set(records.map(e=>e.event_id)).size).toBe(4);expect(records.filter(e=>e.event_type==='exp.rt').map(e=>e.payload.rt_ms).sort()).toEqual([217.25,321.5]);
  }finally{run.proc.kill('SIGTERM');}
 });
}
test('native cleanup refuses missing, relative or protected configuration before any start',async()=>{
 // The negative must never connect to the protected old database: every case
 // below is refused on the resolved path/config shape before a connection,
 // and the protected paths themselves are only named, never opened.
 const before=fs.readdirSync(SCRATCH).sort();
 const canaries=[path.join(ROOT,'local_data','independent_acceptance_20260912','cleanup-negative.sqlite3'),
                 path.join(ROOT,'local_data','independent_acceptance_20260912','cleanup-negative-connection.json')];
 for(const canary of canaries)expect(fs.existsSync(canary)).toBe(false);
 const cases=[
  {},
  {GEP_ISO_CONNECTION:path.join(ROOT,'build','native','connection.json'),GEP_ISO_DB:SERVER_DB,GEP_ISO_SCRATCH:SCRATCH},
  {GEP_ISO_CONNECTION:CONNECTION,GEP_ISO_DB:path.join(ROOT,'local_data','gep.sqlite3'),GEP_ISO_SCRATCH:SCRATCH},
  {GEP_ISO_CONNECTION:CONNECTION,GEP_ISO_DB:canaries[0],GEP_ISO_SCRATCH:SCRATCH},
  {GEP_ISO_CONNECTION:canaries[1],GEP_ISO_DB:SERVER_DB,GEP_ISO_SCRATCH:SCRATCH},
  {GEP_ISO_CONNECTION:CONNECTION,GEP_ISO_DB:SERVER_DB,GEP_ISO_SCRATCH:path.join(ROOT,'build','native','scratch')},
  {GEP_ISO_CONNECTION:CONNECTION,GEP_ISO_DB:SERVER_DB,GEP_ISO_SCRATCH:os.tmpdir()},
  {GEP_ISO_CONNECTION:'build/native/connection.json',GEP_ISO_DB:'local_data/gep.sqlite3',GEP_ISO_SCRATCH:'build/native/scratch'},
 ];
 for(const env of cases)expect(()=>requireCleanupTarget(env,ROOT),JSON.stringify(env)).toThrow();
 for(const canary of canaries)expect(fs.existsSync(canary)).toBe(false);
 expect(fs.readdirSync(SCRATCH).sort()).toEqual(before);
});
