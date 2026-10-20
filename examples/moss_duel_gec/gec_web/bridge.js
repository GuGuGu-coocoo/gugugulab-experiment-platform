import {GEC} from './sdk.js';
import {installInputs} from './inputs.js';
import {installShell} from './shell.js';
let inputs,shell,action;
const replies=new Map();
let client=new GEC(globalThis.GEP_CONTEXT);
const ready=client.prepare();
ready.catch(e=>{globalThis.GECBridge.prepare_error=e.message;});
globalThis.GECBridge={
 // Legacy fallback panel (a frozen config without shell capability).
 mount_inputs(spec){inputs=installInputs(spec);},
 inputs_json(){return inputs.values();},
 clear_input_secrets(){inputs.clearSecrets();inputs.blur();},
 // Shell-owned panel: fields and buttons are built by the Web companion and
 // every button reports one action name back to the Godot shell.
 mount_shell(spec){shell=installShell(spec,name=>{if(action)action(name);});},
 on_shell_action(callback){action=callback;},
 shell_values(){return shell?shell.values():'{}';},
 shell_clear_secrets(){shell?.clearSecrets();},
 shell_state(spec){shell?.setState(typeof spec==='string'?JSON.parse(spec):spec);},
 shell_blur(){shell?.blur();},
 async call(key,op,args){try{
   if(typeof args === "string") args=JSON.parse(args);
   let result;
   if(op==='start'){await ready;result=args[0].recovery_session?await client.recover(args[0].recovery_session,args[0].permit):await client.begin(args[0]);}
   else if(op==='download_recovery'){const data=await client.recovery_export();const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='recovery-'+data.session_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);result={state:'download_requested'};}
  else if(op==='download_results'){const text=await client.results_jsonl();const name=client.results_filename();const url=URL.createObjectURL(new Blob([text],{type:'application/x-ndjson'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);result={state:'download_requested',filename:name};}
   else result=await client[op](...args);
   // A successful flush() resolves undefined. That is still a completed call,
   // so the reply channel must carry an explicit result: an undefined reply is
   // indistinguishable from "the bridge never answered" and would leave the
   // Godot shell waiting in its busy state forever.
   replies.set(key,JSON.stringify(result===undefined?{state:'ok'}:result));
 }catch(e){replies.set(key,JSON.stringify({error:e.message,code:e.code,state:'error'}));}},
 take(key){const result=replies.get(key);replies.delete(key);return result??null;},
 context_json(){return JSON.stringify(globalThis.GEP_CONTEXT??{});},
 status_json(){return JSON.stringify(this.status());},
 status(){return client?.status()??{state:'unprepared'};}
};
