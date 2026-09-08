import {GEC} from './sdk.js';
import {installInputs} from './inputs.js';
let inputs;
const replies=new Map();
let client=new GEC(globalThis.GEP_CONTEXT);
const ready=client.prepare();
ready.catch(()=>{});
globalThis.GECBridge={
 mount_inputs(spec){inputs=installInputs(spec);},
 inputs_json(){return inputs.values();},
 clear_input_secrets(){inputs.clearSecrets();inputs.blur();},
 async call(key,op,args){try{
   if(typeof args === "string") args=JSON.parse(args);
   let result;
   if(op==='start'){await ready;result=args[0].recovery_session?await client.recover(args[0].recovery_session,args[0].permit):await client.begin(args[0]);}
   else if(op==='download_recovery'){const data=await client.recovery_export();const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='recovery-'+data.session_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);result={state:'download_requested'};}
   else result=await client[op](...args);
   replies.set(key,JSON.stringify(result));
 }catch(e){replies.set(key,JSON.stringify({error:e.message,state:'error'}));}},
 take(key){const result=replies.get(key);replies.delete(key);return result??null;},
 status_json(){return JSON.stringify(this.status());},
 status(){return client?.status()??{state:'unprepared'};}
};
