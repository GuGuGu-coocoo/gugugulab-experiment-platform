import {GEC} from './sdk.js';
const replies=new Map();
let client=new GEC(globalThis.GEP_CONTEXT);
const ready=client.prepare();
ready.catch(()=>{});
globalThis.GECBridge={
 async call(key,op,args){try{
   if(typeof args === "string") args=JSON.parse(args);
   let result;
   if(op==='start'){await ready;result=args[0].recovery_session?{state:"active",checkpoint:await client.recover(args[0].recovery_session,args[0].permit)}:await client.begin(args[0]);}
   else result=await client[op](...args);
   replies.set(key,JSON.stringify(result));
 }catch(e){replies.set(key,JSON.stringify({error:e.message,state:'error'}));}},
 take(key){const result=replies.get(key);replies.delete(key);return result??null;},
 status_json(){return JSON.stringify(this.status());},
 status(){return client?.status()??{state:'unprepared'};}
};
