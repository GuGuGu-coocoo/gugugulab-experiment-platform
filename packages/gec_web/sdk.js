/* GEC 0.1.0 / GEP/1. Browser owns record identity and durable queue. */
export const VERSION = '0.1.0';
/* Delivery accounting (R00 F): versioned, per-session, persisted in the same
   transaction as the queue itself. Only the fields below are added; the legacy
   attempts/retryAt/paused budget stays independent. */
export const DELIVERY_VERSION = 2;
const copy = value => structuredClone(value);
const uuid = () => crypto.randomUUID();
const fail = (message,kind) => { const error = new Error(message); error.kind = kind ?? message; throw error; };
const sameSet = (a,b) => a.length === new Set(a).size && b.length === new Set(b).size && a.length === b.length && a.every(x=>b.includes(x));
const defaultDelivery = () => ({initial_failed:false,retry_failures:0,round_id:0,last_error:'',last_error_kind:'',ack_progress:0,terminal_reason:'',inflight:false});
const deliveryOf = s => { const delivery = defaultDelivery(); const stored = s && typeof s.delivery === 'object' && s.delivery ? s.delivery : {}; for(const key of Object.keys(delivery)) if(stored[key] !== undefined) delivery[key] = stored[key]; return delivery; };
const withDelivery = s => { const out = {...s}; out.delivery_version = DELIVERY_VERSION; out.delivery = deliveryOf(s); return out; };
const isRetryable = error => !(error && (error.retryable === false || [401,403,409,422].includes(error.status)));
const storageFailure = cause => { const error = new Error('local_storage_error'); error.kind = 'local_storage_error'; error.local = true; error.retryable = false; error.cause = cause; return error; };
export class GEC {
  constructor(config) { this.config=copy(config);this.localOnly=config.preview===true;this.buffer=[];this.state='unprepared';this.error=null;this.sequence=0;this.segment=uuid();this.busy=false;this.stopped=false; }
  async prepare() {
    const c=this.config;
    if(c.config_version!=='1'||c.protocol_version!=='gep/1'||c.sdk_version!==VERSION||c.purpose!=='synthetic') fail('unsupported_configuration');
    const url=new URL(c.api_url);
    if(url.protocol!=='https:' && !(url.protocol==='http:'&&['experiment.localhost','127.0.0.1'].includes(url.hostname))) fail('https_required');
    if(!navigator.locks||!indexedDB) fail('storage_capability_missing');
    await new Promise((resolve,reject)=>navigator.locks.request('gec-device-writer',{ifAvailable:true},async lock=>{
      if(!lock){reject(new Error('writer_busy'));return;}
      resolve();await new Promise(r=>this.unlock=r);
    }));
    this.db=await new Promise((resolve,reject)=>{const r=indexedDB.open('gec-1',1);r.onupgradeneeded=()=>r.result.createObjectStore('sessions',{keyPath:'id'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);r.onblocked=()=>reject(new Error('storage_blocked'));});
    await this.mutate(store=>{store.put({id:'preflight',probe:true});store.delete('preflight');});
    // One reconciliation pass for the durable queue: an old record only gets the
    // missing delivery defaults (its attempts/records/proof/binding stay), and a
    // round that was interrupted by a process exit is conservatively recorded as
    // outcome_unknown instead of a failure; the next real round decides.
    await this.mutate(store=>{const r=store.getAll();r.onsuccess=()=>{for(const row of r.result){
      if(!row||row.kind!=='session')continue;
      const delivery=deliveryOf(row);
      const needs=row.delivery_version!==DELIVERY_VERSION||!row.delivery||Object.keys(defaultDelivery()).some(key=>row.delivery[key]===undefined)||delivery.inflight===true;
      if(!needs)continue;
      if(delivery.inflight){delivery.inflight=false;delivery.last_error_kind='outcome_unknown';delivery.last_error='outcome_unknown';}
      store.put({...row,delivery_version:DELIVERY_VERSION,delivery});
    };}});
    this.state='ready';
    if(!this.localOnly)this.timer=setInterval(()=>this.flush(false).catch(e=>this.report(e)),1000);
    return this.status();
  }
  mutate(fn) { return new Promise((resolve,reject)=>{const tx=this.db.transaction('sessions','readwrite',{durability:'strict'});tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error||new Error('local_transaction_error'));tx.onabort=()=>reject(tx.error||new Error('local_transaction_aborted'));try{fn(tx.objectStore('sessions'),tx);}catch(e){tx.abort();reject(e);}}); }
  all() { return new Promise((resolve,reject)=>{const tx=this.db.transaction('sessions','readonly');const r=tx.objectStore('sessions').getAll();tx.oncomplete=()=>resolve(r.result);tx.onerror=()=>reject(tx.error);}); }
  async get(id){return (await this.all()).find(s=>s.id===id);}
  async request(config,path,body,token) {
    let response;
    try {
      response=await fetch(config.api_url+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:body===undefined?undefined:JSON.stringify(body),credentials:'omit',redirect:'error',signal:AbortSignal.timeout(10000)});
    } catch(error) {
      // A real network refusal/timeout is a sent attempt; the transport keeps
      // the error class so the delivery accounting can count it correctly.
      const timeout=!!error&&error.name==='TimeoutError';
      const transport=new Error(timeout?'timeout':'network');
      transport.kind=timeout?'timeout':'network';transport.retryable=true;
      throw transport;
    }
    if(!response.ok) {const e=new Error('http_'+response.status);e.status=response.status;e.kind='http';try{const body=await response.json();if(body&&typeof body.code==='string')e.code=body.code;if(body&&typeof body.retryable==='boolean')e.retryable=body.retryable;}catch{}const wait=response.headers.get('Retry-After');e.retryAfter=wait&&/^\d+$/.test(wait)?Number(wait)*1000:0;throw e;}
    return response.json();
  }
  /* Local candidate discovery. The shell never asks the participant for a public
     session UUID: it searches the device's own durable store and only reports a
     redacted summary (no proof, token, identity or payload). */
  async candidates() {
    return (await this.all()).filter(s=>['session','cleaned'].includes(s.kind)&&s.config?.instance_id===this.config.instance_id&&s.config?.study_id===this.config.study_id)
      .map(s=>({id:s.id,kind:s.kind,participant_code:s.context?.participant_code??null,front_locked:!!s.front_locked,unfinished:s.completion===null,checkpoint_next:s.checkpoint?.next_trial??null,checkpoint_strategy:s.checkpoint?.strategy??null}));
  }
  resumable(s) {
    return !s.completion&&!!s.checkpoint&&s.checkpoint.version===1&&s.checkpoint.strategy==='trial_boundary_v1'&&s.config?.purpose==='synthetic';
  }
  async recovery_request(target,capability,extra) {
    return this.request(target.config,'/v1/participant/recovery',{capability,proof:target.proof,instance_id:target.config.instance_id,study_id:target.config.study_id,release_id:target.config.release_id,build_id:target.config.build_id,...extra});
  }
  /* Six-digit researcher code: search local proofs privately and redeem against
     the server. Returns a pending candidate that must be explicitly continued. */
  async recover_code(code) {
    if(this.state!=='ready')fail('not_ready');
    if(!/^\d{6}$/.test(String(code)))fail('invalid_code');
    const list=(await this.all()).filter(s=>s.kind==='session'&&s.proof&&s.config?.instance_id===this.config.instance_id&&s.config?.study_id===this.config.study_id);
    if(!list.length)fail('not_recoverable');
    let last;
    for(const s of list.slice(0,3)){
      try {
        const response=await this.recovery_request(s,'recovery_code/v1',{code:String(code)});
        return {state:'confirm',session_id:s.id,token:response.token,can_resume:this.resumable(s)&&!response.task_finished,task_finished:!!response.task_finished};
      } catch(error) { if(error.status!==403)throw error; last=error; }
    }
    throw last;
  }
  /* Named same-device continuation for frozen id/password releases.
     A declared completion only limits what may be adopted; it never stands for a
     received receipt or a cleaned session, so a candidate with ``completion`` is
     still verified against the server and then continued as data only. The
     neutral "already uploaded" hint needs a cleaned tombstone that belongs to this
     study on this device; a tombstone from another study or participant is never
     treated as evidence about the named candidate. */
  async recover_named(participant_code,password) {
    if(this.state!=='ready')fail('not_ready');
    const all=await this.all();
    const scoped=all.filter(s=>s.config?.instance_id===this.config.instance_id&&s.config?.study_id===this.config.study_id);
    const matching=scoped.filter(s=>s.kind==='session'&&(s.context?.participant_code??null)===participant_code);
    if(!matching.length)return {state:scoped.some(s=>s.kind==='cleaned')?'cleaned':'none'};
    const live=matching.filter(s=>!s.completion);
    // Several unfinished sessions, or several finished ones, need a researcher:
    // the client never picks one and never lists the others.
    if(live.length>1||(live.length===0&&matching.length>1))return {state:'ambiguous'};
    const target=live.length?live[0]:matching[0];
    // The persisted foreground lock blocks continuing trials, not a verified
    // data-only recovery of a session that already declared completion.
    if(!target.completion&&target.front_locked)return {state:'front_locked'};
    const response=await this.recovery_request(target,'recovery_named/v1',{participant_code,password});
    return {state:'confirm',session_id:target.id,token:response.token,can_resume:this.resumable(target)&&!response.task_finished,task_finished:!!response.task_finished};
  }
  /* Explicit continue: only this call adopts a redeemed credential locally. */
  async confirm_recovery(session_id,token,can_resume) {
    const s=await this.get(session_id);if(!s||s.kind!=='session')fail('not_recoverable');
    await this.mutate(store=>{const r=store.get(session_id);r.onsuccess=()=>{const fresh=r.result;fresh.context.token=token;fresh.front_locked=false;fresh.paused=false;fresh.attempts=0;fresh.retryAt=0;if(can_resume&&!fresh.segments.includes(this.segment))fresh.segments.push(this.segment);store.put(fresh);};});
    this.id=session_id;this.config=s.config;this.state=can_resume?'active':'data_only';
    return {state:this.state,checkpoint:can_resume?copy(s.checkpoint):null};
  }
  async begin(credentials={}) {
    if(this.state!=='ready')fail('not_ready');
    if(this.localOnly){this.id=uuid();await this.mutate(store=>store.put({id:this.id,kind:'local',records:[],pending:[],segments:[this.segment],checkpoint:null,completion:null}));this.state='active';return {state:'active'};}
    // Persist proof and operation before admission. Failed admission reuses this operation.
    let draft=(await this.all()).find(s=>s.kind==='admission'&&JSON.stringify(s.config)===JSON.stringify(this.config));
    if(!draft){draft={id:uuid(),kind:'admission',config:this.config,proof:Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join('')};await this.mutate(store=>{const r=store.getAll();r.onsuccess=()=>{for(const old of r.result){if(old.kind==='cleaned')continue;old.front_locked=true;store.put(old);}store.put(draft);};});}
    const c=this.config;
    const request={operation_id:draft.id,proof:draft.proof,instance_id:c.instance_id,study_id:c.study_id,release_id:c.release_id,build_id:c.build_id,...credentials};
    // A stable study entry injects the observed release and publication revision;
    // the server then refuses a superseded binding with stale_entry instead of
    // silently admitting against a release the page no longer observed.
    if(c.expected_release_id!==undefined&&c.expected_revision!==undefined){request.expected_release_id=c.expected_release_id;request.expected_revision=c.expected_revision;}
    const response=await this.request(c,'/v1/participant/sessions',request);
    for(const k of ['instance_id','study_id','release_id','build_id'])if(response[k]!==c[k])fail('admission_binding');
    if(c.expected_release_id!==undefined&&response.release_id!==c.expected_release_id)fail('admission_binding');
    this.id=response.session_id;
    await this.mutate(store=>{store.delete(draft.id);store.put({id:this.id,kind:'session',config:c,context:response,records:[],pending:[],segments:[this.segment],checkpoint:null,completion:null,complete_ack:null,front_locked:false,proof:draft.proof,delivery_version:DELIVERY_VERSION,delivery:defaultDelivery()});});
    this.state='active';return {session_id:this.id};
  }
  record(type,payload,schema,observed_time) {
    if(this.state!=='active'||this.buffer.length>=64)fail('not_recording_or_backpressure');
    const event={protocol_version:'gep/1',event_id:uuid(),session_id:this.id,segment_id:this.segment,sequence:++this.sequence,event_type:type,schema_id:schema.id,schema_version:schema.version,payload:copy(payload)};
    if(observed_time)event.observed_time=copy(observed_time);
    this.buffer.push(event);return {event_id:event.event_id,state:'buffered'};
  }
  async commit(checkpoint=null) {
    const events=copy(this.buffer);
    await this.mutate(store=>{const r=store.get(this.id);r.onsuccess=()=>{const s=r.result;if(!s||s.completion)fail('session_closed');for(const e of events)if(!s.records.some(x=>x.event_id===e.event_id)){s.records.push(e);if(!this.localOnly)s.pending.push(e.event_id);}if(checkpoint){if(checkpoint.version!==1||!checkpoint.dependencies.every(id=>s.records.some(e=>e.event_id===id))){r.transaction?.abort();fail('checkpoint_dependencies');}s.checkpoint=copy(checkpoint);}store.put(s);};});
    const ids=events.map(e=>e.event_id);this.buffer=this.buffer.filter(e=>!ids.includes(e.event_id));return {state:'local_committed',event_ids:ids};
  }
  async finish(){
    // The local test is only finished when both the record commit and the
    // completion set are durably stored; a failed write rejects and never
    // reports a saved finish.
    await this.commit();
    await this.mutate(store=>{const r=store.get(this.id);r.onsuccess=()=>{const s=r.result;s.completion={event_ids:s.records.map(e=>e.event_id),segment_ids:s.segments};store.put(s);};});
    this.state=this.localOnly?'finished_saved':'finished';
    return {state:this.localOnly?'finished_saved':'local_committed'};
  }
  /* Explicit local-test result export: the durable record envelopes as JSONL,
     one original event per line. It never deletes or rewrites the queue.
     Only this device's own current, unlocked local test session may be
     exported: a missing, cleaned, front-locked or remote session is refused
     here, in the SDK, so a stale or hidden panel can never become a download. */
  async results_jsonl(){
    const s=this.id?await this.get(this.id):null;
    if(!this.localOnly||!s||s.kind!=='local'||s.front_locked)fail('results_export_unavailable');
    const records=s.records??[];
    return records.map(record=>JSON.stringify(record)).join('\n')+(records.length?'\n':'');
  }
  results_filename(){return 'local-results-'+(this.id??'session')+'.jsonl';}
  async flush(manual=true) {
    if(this.busy||this.stopped)return;
    this.busy=true;let firstError;
    try {for(const raw of await this.all()){
      // The durable retry schedule (backoff or Retry-After) is respected by an
      // automatic and a manual round alike: neither may clear the counters,
      // lift a safe pause or jump the wait.
      if(raw.kind!=='session'||raw.paused||raw.retryAt>Date.now())continue;
      // A browser that already knows it is offline sends nothing for an
      // automatic round: no request, no attempt and no delivery count.
      if(!manual&&typeof navigator!=='undefined'&&navigator.onLine===false)continue;
      const s=withDelivery(raw);
      if(s.complete_ack){await this.cleanup(s.id);continue;}
      if(!s.pending.length&&!s.completion)continue;
      // Delivery counting only applies to a declared completion set: failures
      // during the experiment never carry into the finish threshold.
      const counting=!!s.completion;
      let progress=false,failure=null;
      try {
        if(counting){
          try {
            await this.mutate(store=>{const r=store.get(s.id);r.onsuccess=()=>{const fresh=r.result;if(!fresh||fresh.kind!=='session')return;const delivery=deliveryOf(fresh);delivery.round_id=(Number(delivery.round_id)||0)+1;delivery.inflight=true;fresh.delivery_version=DELIVERY_VERSION;fresh.delivery=delivery;store.put(fresh);};});
          } catch(error) { failure=storageFailure(error); }
        }
        if(!failure&&s.pending.length){
          const records=s.records.filter(e=>s.pending.includes(e.event_id)).slice(0,32);const batch={batch_id:uuid(),events:records};
          const ack=await this.request(s.config,`/v1/participant/sessions/${s.id}/event-batches`,batch,s.context.token);
          const ids=records.map(e=>e.event_id);
          if(ack.protocol_version!=='gep/1'||ack.instance_id!==s.config.instance_id||ack.session_id!==s.id||ack.batch_id!==batch.batch_id||!Array.isArray(ack.accepted)||!Array.isArray(ack.duplicate)||!sameSet([...ack.accepted,...ack.duplicate],ids))fail('invalid_ack','invalid_ack');
          try {
            // One durable transaction: the pending reduction and the delivery
            // progress (ack_progress+1, retry_failures=0) commit together, so a
            // process exit right after the ACK keeps a consistent queue+count.
            await this.mutate(store=>{const r=store.get(s.id);r.onsuccess=()=>{const fresh=r.result;if(!fresh||fresh.kind!=='session')return;fresh.pending=fresh.pending.filter(id=>!ids.includes(id));if(counting){const delivery=deliveryOf(fresh);delivery.ack_progress=(Number(delivery.ack_progress)||0)+1;delivery.retry_failures=0;fresh.delivery_version=DELIVERY_VERSION;fresh.delivery=delivery;}store.put(fresh);};});
            progress=true;
          } catch(error) { failure=storageFailure(error); }
        }
        if(!failure){
          const fresh=await this.get(s.id);
          // An incomplete completion is never sent: while any pending record
          // remains, only the next batch is attempted.
          if(fresh.completion&&!fresh.pending.length){
            const ack=await this.request(fresh.config,`/v1/participant/sessions/${s.id}/completion`,fresh.completion,fresh.context.token);
            const declaration=ack.declaration??{};
            if(ack.state!=='complete'||ack.protocol_version!=='gep/1'||ack.instance_id!==fresh.config.instance_id||ack.session_id!==s.id||!Array.isArray(declaration.event_ids)||!Array.isArray(declaration.segment_ids)||!sameSet(declaration.event_ids,fresh.completion.event_ids)||!sameSet(declaration.segment_ids,fresh.completion.segment_ids)||!Array.isArray(ack.missing)||ack.missing.length)fail('invalid_completion_ack','invalid_ack');
            try {
              await this.mutate(store=>{const r=store.get(s.id);r.onsuccess=()=>{const current=r.result;if(!current||current.pending.length)return;current.complete_ack=ack;current.checkpoint=null;if(counting){const delivery=deliveryOf(current);delivery.ack_progress=(Number(delivery.ack_progress)||0)+1;delivery.retry_failures=0;current.delivery_version=DELIVERY_VERSION;current.delivery=delivery;}store.put(current);};});
              progress=true;
              await this.cleanup(s.id);
            } catch(error) { failure=storageFailure(error); }
          }
        }
      } catch(error) { failure=failure??error; }
      if(failure&&failure.session_id===undefined)failure.session_id=s.id;
      if(failure)firstError??=failure;
      if(counting||progress||failure){
        try { await this.finishRound(s.id,progress,failure,counting); }
        catch(error) { firstError??=error; }
      }
    }} finally {this.busy=false;}
    if(firstError)throw firstError;
  }
  async finishRound(id,progress,failure,counting){
    /* One real transaction records the whole round outcome. The ACK progress
       itself was already committed in the same transaction as the queue change,
       so this pass never double-counts it: it only clears the round marker, the
       diagnostics and the independent legacy attempts/backoff budget. */
    await this.mutate(store=>{const r=store.get(id);r.onsuccess=()=>{
      const s=r.result;if(!s||s.kind!=='session')return;
      const delivery=deliveryOf(s);
      if(counting)delivery.inflight=false;
      if(!failure){
        if(progress){delivery.last_error='';delivery.last_error_kind='';}
      } else {
        delivery.last_error=String(failure.message||failure.kind||'error');
        delivery.last_error_kind=String(failure.code||failure.kind||'error');
        const local=!!failure.local;
        if(failure.code==='study_deleted'){
          // A permanent deletion terminates immediately whether it arrived
          // during the experiment, on a completion-only round or after a legal
          // batch ACK in the same round; the persisted progress stays.
          delivery.terminal_reason='study_deleted';s.paused=true;
        } else if(counting&&!progress&&!local&&isRetryable(failure)){
          if(!delivery.initial_failed&&!(Number(delivery.ack_progress)>0))delivery.initial_failed=true;
          else delivery.retry_failures=(Number(delivery.retry_failures)||0)+1;
        }
        s.attempts=(s.attempts||0)+1;
        // A server refusal that is explicitly not retryable (any status) is a
        // safe pause; a local storage failure is not a remote authorization
        // denial and must stay retryable instead of locking the queue forever.
        s.paused=!!s.paused||(!local&&!isRetryable(failure))||s.attempts>=32;
        const jitter=crypto.getRandomValues(new Uint32Array(1))[0]/4294967296;
        s.retryAt=Date.now()+Math.max(failure.retryAfter||0,Math.min(60000,1000*2**Math.min(s.attempts,6))*(1+jitter));
      }
      s.delivery_version=DELIVERY_VERSION;s.delivery=delivery;store.put(s);
    };});
  }
  async cleanup(id){await this.mutate(store=>{const r=store.get(id);r.onsuccess=()=>{const s=r.result;if(s.complete_ack&&!s.pending.length&&!s.checkpoint)store.put({id,kind:'cleaned',state:'remote_acknowledged',instance_id:s.config?.instance_id??null,study_id:s.config?.study_id??null});};});if(id===this.id){this.state='remote_acknowledged';this.error=null;}}
  async recover(id,permit){
    if(this.state!=='ready')fail('not_ready');
    const s=await this.get(id);if(!s||s.kind!=='session')fail('not_recoverable');
    let canResume=!s.completion&&!!s.checkpoint&&s.checkpoint.version===1&&s.checkpoint.strategy==='trial_boundary_v1'&&s.config.purpose==='synthetic';
    const recovered=await this.request(s.config,`/v1/participant/sessions/${id}/recover`,{permit,proof:s.proof},s.context.token);s.context.token=recovered.token;
    canResume=canResume&&!recovered.task_finished;
    await this.mutate(store=>{s.front_locked=false;s.paused=false;s.attempts=0;s.retryAt=0;if(canResume)s.segments.push(this.segment);store.put(s);});this.id=id;this.config=s.config;this.state=canResume?'active':'data_only';return {state:this.state,checkpoint:canResume?copy(s.checkpoint):null};
  }
  async recovery_export(){
    const s=await this.get(this.id);if(!s||!['session','local'].includes(s.kind)||s.front_locked)fail('recovery_export_unavailable');
    const binding={};for(const k of ['instance_id','study_id','release_id','build_id','protocol_version'])if(s.config?.[k])binding[k]=s.config[k];
    return {format_version:1,session_id:s.id,binding,records:copy(s.records),checkpoint:copy(s.checkpoint),pending:copy(s.pending),completion:copy(s.completion)};
  }
  status(){return {state:this.state,error:this.error,buffered:this.buffer.length};}
  async summary(){
    // Rebuilt from the current session's persisted row on every call, so the
    // state, pending, error and failure counters can never leak from another
    // session or from a stale in-memory state string.
    const s=this.id?await this.get(this.id):null;
    const delivery=deliveryOf(s);
    return {state:this.state,error:s?delivery.last_error:this.error,buffered:this.buffer.length,records:s?.records?.length??0,pending:s?.pending?.length??0,kind:s?.kind??null,checkpoint_next:s?.checkpoint?.next_trial??null,delivery_version:DELIVERY_VERSION,delivery};
  }
  report(error){this.error=error.message;}
  close(){clearInterval(this.timer);this.stopped=true;this.db?.close();this.unlock?.();}
}
