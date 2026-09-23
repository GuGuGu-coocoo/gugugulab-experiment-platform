/* GEC participation shell: browser-native entry/recovery panel.
 *
 * The reusable Godot shell delegates its entry UI to this companion on Web.
 * The panel is anchored to the real canvas rectangle and laid out with CSS flow
 * (not copied engine coordinates), so a resize or fullscreen change reflows it
 * instead of leaving stale overlay positions. Only the fields required by the
 * frozen mode are rendered; the six-digit recovery panel is always available and
 * the long-permit fields stay in a collapsed advanced section for legacy
 * compatibility. The panel never captures trial responses.
 */
const FIELD_IDS = {code:'gec-input-code',password:'gec-input-password',recovery:'gec-input-recovery',permit:'gec-input-permit',short_code:'gec-input-short-code'};

function el(tag,attributes={},style={}) {
  const node=document.createElement(tag);
  for(const [key,value] of Object.entries(attributes)) {
    if(key==='text') node.textContent=value;
    else if(key==='id') node.id=value;
    else node.setAttribute(key,value);
  }
  Object.assign(node.style,style);
  return node;
}

export function installShell(specification,action) {
  const spec=JSON.parse(specification);
  const canvas=document.getElementById('canvas');
  const panel=el('div',{id:'gec-shell','data-gec-shell':'1',role:'form','aria-label':spec.title},{position:'fixed',zIndex:'5',boxSizing:'border-box',display:'flex',flexDirection:'column',gap:'8px',padding:'12px 16px',background:'rgba(24,24,24,0.92)',color:'#eee',border:'1px solid #777',borderRadius:'6px',fontFamily:'sans-serif',overflow:'auto'});
  panel.dataset.localOnly=spec.local_only?'1':'0';
  const fields=new Map();
  const buttons=new Map();

  const field=(name,label,options={}) => {
    const row=el('label',{text:label},{display:'block',fontSize:'14px',lineHeight:'1.3'});
    const input=el('input',{id:FIELD_IDS[name],type:options.secret?'password':'text',placeholder:label,autocomplete:'off',spellcheck:'false'},{display:'block',width:'100%',boxSizing:'border-box',marginTop:'2px',background:'#303030',color:'#eee',border:'1px solid #777',borderRadius:'3px',padding:'4px 6px',fontSize:'16px'});
    input.setAttribute('aria-label',label);
    if(options.inputmode) input.inputMode=options.inputmode;
    if(options.maxlength) input.maxLength=options.maxlength;
    for(const event of ['keydown','keyup','keypress','paste','copy','cut']) input.addEventListener(event,e=>e.stopPropagation());
    fields.set(name,input);
    row.append(input);
    return row;
  };
  const button=(name,label,options={}) => {
    const node=el('button',{id:`gec-${name}`,type:'button',text:label},{padding:'6px 10px',fontSize:'15px',cursor:'pointer'});
    if(options.hidden) node.style.display='none';
    node.addEventListener('click',()=>action(name));
    buttons.set(name,node);
    return node;
  };

  panel.append(el('p',{id:'gec-shell-status',role:'status','aria-live':'polite',text:spec.status},{margin:'0',fontSize:'15px',minHeight:'20px'}));

  const mode=spec.mode;
  if(mode==='anonymous') {
    panel.append(el('p',{text:spec.messages.anonymous},{margin:'0',fontSize:'14px',color:'#bbb'}));
  } else {
    if(mode!=='legacy'||spec.show_code) panel.append(field('code',spec.messages.code));
    if(mode==='password'||mode==='legacy') panel.append(field('password',spec.messages.password,{secret:true,maxlength:256}));
  }

  const recovery=el('fieldset',{},{margin:'0',padding:'8px',border:'1px solid #555',borderRadius:'4px'});
  recovery.append(el('legend',{text:spec.messages.recovery_legend},{fontSize:'14px'}));
  const codeRow=el('div',{},{display:'flex',gap:'8px',alignItems:'flex-end'});
  const shortField=field('short_code',spec.messages.short_code,{inputmode:'numeric',maxlength:6});
  shortField.style.flex='1';
  codeRow.append(shortField,button('recover-code',spec.messages.recover));
  recovery.append(codeRow);
  const advanced=el('details',{},{marginTop:'6px'});
  advanced.append(el('summary',{text:spec.messages.advanced},{fontSize:'13px',color:'#bbb'}));
  advanced.append(field('recovery',spec.messages.session));
  advanced.append(field('permit',spec.messages.permit,{secret:true,maxlength:256}));
  advanced.append(button('recover-permit',spec.messages.recover_permit));
  recovery.append(advanced);
  panel.append(recovery);

  const actions=el('div',{},{display:'flex',gap:'8px',flexWrap:'wrap'});
  actions.append(button('start',spec.messages.start),button('export',spec.messages.export));
  // Local-only preview: the participant explicitly downloads the JSONL result
  // document; nothing is exported silently and no server receipt is implied.
  if(spec.local_only)actions.append(button('download-results',spec.messages.download_results||'Download results JSONL'));
  panel.append(actions);
  // Failure surface (R09C): hidden until the persisted summary reports three
  // consecutive retry failures (or the permanent deletion terminal) with legal
  // unlocked records; hidden again once the session is received.
  const failure=el('div',{id:'gec-shell-failure',role:'group'},{display:'none',gap:'8px',flexWrap:'wrap'});
  failure.append(button('retry',spec.messages.retry_upload||'Retry upload'),button('failure-export',spec.messages.export_failure||'Export failure data'));
  panel.append(failure);

  const confirm=el('div',{id:'gec-shell-confirm',role:'alertdialog','aria-modal':'false'},{display:'none',border:'1px solid #d0a',borderRadius:'4px',padding:'8px',gap:'8px',flexWrap:'wrap',alignItems:'center'});
  const confirmText=el('p',{text:''},{margin:'0',flex:'1 1 100%',fontSize:'15px'});
  confirm.append(confirmText,button('confirm-continue',spec.messages.continue_),button('confirm-new',spec.messages.start_new,{hidden:true}),button('confirm-cancel',spec.messages.cancel));
  panel.append(confirm);

  document.body.append(panel);
  const position=()=>{
    const rect=canvas.getBoundingClientRect();
    const scale=Math.max(0.6,Math.min(rect.width/1000,rect.height/700));
    Object.assign(panel.style,{left:`${rect.left+rect.width*0.07}px`,top:`${rect.top+rect.height*0.09}px`,width:`${Math.max(280,rect.width*0.86)}px`,maxHeight:`${rect.height*0.86}px`,fontSize:`${14*scale}px`});
  };
  position();
  window.addEventListener('resize',position);
  window.addEventListener('scroll',position);
  new ResizeObserver(position).observe(canvas);

  return {
    values(){return JSON.stringify(Object.fromEntries([...fields].map(([name,input])=>[name,input.value])));},
    clearSecrets(){for(const name of ['password','permit','short_code']){const input=fields.get(name);if(input)input.value='';}},
    setState(state){
      if(state.status!==undefined)document.getElementById('gec-shell-status').textContent=state.status;
      if(state.reason!==undefined)document.getElementById('gec-shell-status').dataset.reason=state.reason;
      if(state.busy!==undefined)for(const [name,node] of buttons)node.disabled=(state.busy||(panel.dataset.entryHidden==='1'&&['start','recover-code','recover-permit'].includes(name)))&&node.id!=='gec-confirm-cancel';
      if(state.entry_hidden){
        // A successful admission retires the pre-entry form: hidden, disabled
        // and unfocused, so it cannot be submitted twice or cover the stimulus.
        panel.dataset.entryHidden='1';
        for(const [name,node] of fields){node.disabled=true;node.blur();const label=node.closest('label');if(label)label.style.display='none';}
        for(const name of ['start','recover-code','recover-permit']){const node=buttons.get(name);if(node){node.disabled=true;node.style.display='none';}}
        recovery.style.display='none';confirm.style.display='none';failure.style.display='none';
      }
      if(state.failure!==undefined){
        const visible=!!(state.failure&&state.failure.visible);
        failure.style.display=visible?'flex':'none';
        buttons.get('retry').disabled=!(state.failure&&state.failure.retry);
        buttons.get('failure-export').disabled=!(state.failure&&state.failure.export);
      }
      if(state.visible)for(const [name,node] of buttons)if(!['confirm-continue','confirm-new','confirm-cancel','retry','failure-export'].includes(name))node.style.display=state.visible.includes(name)?'':'none';
      if(state.confirm!==undefined){
        if(state.confirm){confirmText.textContent=state.confirm.text;buttons.get('confirm-new').style.display=state.confirm.new_session?'':'none';confirm.style.display='flex';}
        else confirm.style.display='none';
      }
      if(state.focus){const input=fields.get(state.focus)??buttons.get(state.focus);input?.focus();}
    },
    blur(){for(const input of fields.values())input.blur();canvas?.focus();}
  };
}
