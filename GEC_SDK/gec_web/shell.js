/* GEC participation shell: browser-native entry/recovery panel.
 *
 * The reusable Godot shell delegates its entry UI to this companion on Web.
 * The panel is anchored to the real canvas rectangle and laid out with CSS flow
 * (not copied engine coordinates), so a resize or fullscreen change reflows it
 * instead of leaving stale overlay positions. Only the fields required by the
 * frozen mode are rendered. Recovery entry is currently not public. Optional
 * data tools stay inside a collapsed Advanced section.
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
  const backdrop=el('div',{id:'gec-shell-backdrop'},{position:'fixed',inset:'0',zIndex:'4',background:'#f3f5f2'});
  const panel=el('div',{id:'gec-shell','data-gec-shell':'1',role:'form','aria-label':spec.title},{position:'fixed',zIndex:'5',boxSizing:'border-box',display:'flex',flexDirection:'column',gap:'16px',padding:'28px',background:'#ffffff',color:'#17211c',border:'1px solid #d5ded7',borderRadius:'12px',boxShadow:'0 8px 32px #17211c12',fontFamily:'system-ui,-apple-system,"Segoe UI",sans-serif',lineHeight:'1.6',overflow:'auto'});
  panel.dataset.localOnly=spec.local_only?'1':'0';
  const fields=new Map();
  const buttons=new Map();
  const labels=new Map();
  const buttonKeys={start:'start',export:'export','local-retry':'retry_local_save','download-results':'download_results',retry:'retry_upload','failure-export':'export_failure','confirm-continue':'continue_','confirm-new':'start_new','confirm-cancel':'cancel'};
  let busy=false;
  let localSaveRetry=!!spec.local_save_retry;

  const field=(name,label,options={}) => {
    const row=el('label',{text:label},{display:'block',fontSize:'14px',lineHeight:'1.3'});
    const input=el('input',{id:FIELD_IDS[name],type:options.secret?'password':'text',placeholder:label,autocomplete:'off',spellcheck:'false'},{display:'block',width:'100%',boxSizing:'border-box',marginTop:'6px',background:'#f8faf8',color:'#17211c',border:'1px solid #d5ded7',borderRadius:'8px',padding:'10px 12px',fontSize:'16px'});
    input.setAttribute('aria-label',label);
    if(options.inputmode) input.inputMode=options.inputmode;
    if(options.maxlength) input.maxLength=options.maxlength;
    for(const event of ['keydown','keyup','keypress','paste','copy','cut']) input.addEventListener(event,e=>e.stopPropagation());
    fields.set(name,input);
    labels.set(name,row);
    row.append(input);
    return row;
  };
  const button=(name,label,options={}) => {
    const node=el('button',{id:`gec-${name}`,type:'button',text:label},{padding:'10px 16px',fontSize:'15px',cursor:'pointer',background:'#1c5a48',color:'#ffffff',border:'1px solid #1c5a48',borderRadius:'8px'});
    if(options.hidden) node.style.display='none';
    node.addEventListener('click',()=>action(name));
    buttons.set(name,node);
    return node;
  };

  const header=el('div',{},{display:'flex',justifyContent:'space-between',alignItems:'center',gap:'12px'});
  const title=el('strong',{text:spec.title},{fontSize:'20px'});
  const languages=el('div',{},{display:'flex',gap:'6px'});
  for(const [locale,text] of [['zh','中文'],['en','English']]){
    const choice=el('button',{type:'button',text,'data-gec-locale':locale},{background:'#f8faf8',color:'#1c5a48',border:'1px solid #d5ded7',borderRadius:'16px',padding:'4px 10px',cursor:'pointer'});
    choice.addEventListener('click',()=>action('locale-'+locale));languages.append(choice);
  }
  header.append(title,languages);panel.append(header);
  panel.append(el('p',{id:'gec-shell-status',role:'status','aria-live':'polite',text:spec.status},{margin:'0',fontSize:'18px',minHeight:'28px'}));

  const mode=spec.mode;
  const modeHint=el('p',{text:spec.messages.anonymous},{margin:'0',fontSize:'14px',color:'#48584f'});
  if(mode==='anonymous') {
    panel.append(modeHint);
  } else {
    if(mode!=='legacy'||spec.show_code) panel.append(field('code',spec.messages.code));
    if(mode==='password'||mode==='legacy') panel.append(field('password',spec.messages.password,{secret:true,maxlength:256}));
  }

  const advanced=el('details',{id:'gec-advanced'},{marginTop:'6px',borderTop:'1px solid #d5ded7',paddingTop:'12px'});
  const advancedTitle=el('summary',{text:spec.messages.advanced},{fontSize:'14px',color:'#48584f',cursor:'pointer'});
  const tools=el('div',{},{display:'flex',gap:'8px',flexWrap:'wrap',paddingTop:'12px'});
  advanced.append(advancedTitle,tools);

  const actions=el('div',{},{display:'flex',gap:'8px',flexWrap:'wrap'});
  actions.append(button('start',spec.messages.start));
  tools.append(button('export',spec.messages.export));
  const localRetry=button('local-retry',spec.messages.retry_local_save||'Retry local save',{hidden:true});
  actions.append(localRetry);
  const updateLocalRetry=()=>{
    localRetry.style.display=localSaveRetry?'':'none';
    localRetry.disabled=!localSaveRetry||busy;
  };
  updateLocalRetry();
  // Local-only preview: the participant explicitly downloads the JSONL result
  // document; nothing is exported silently and no server receipt is implied.
  if(spec.local_only)tools.append(button('download-results',spec.messages.download_results||'Download results JSONL'));
  panel.append(actions);
  // Failure surface (R09C): hidden until the persisted summary reports three
  // consecutive retry failures (or the permanent deletion terminal) with legal
  // unlocked records; hidden again once the session is received.
  const failure=el('div',{id:'gec-shell-failure',role:'group'},{display:'none',gap:'8px',flexWrap:'wrap'});
  failure.append(button('retry',spec.messages.retry_upload||'Retry upload'));
  tools.append(button('failure-export',spec.messages.export_failure||'Export failure data',{hidden:true}));
  panel.append(failure);

  const confirm=el('div',{id:'gec-shell-confirm',role:'alertdialog','aria-modal':'false'},{display:'none',border:'1px solid #d0a',borderRadius:'4px',padding:'8px',gap:'8px',flexWrap:'wrap',alignItems:'center'});
  const confirmText=el('p',{text:''},{margin:'0',flex:'1 1 100%',fontSize:'15px'});
  confirm.append(confirmText,button('confirm-continue',spec.messages.continue_),button('confirm-new',spec.messages.start_new,{hidden:true}),button('confirm-cancel',spec.messages.cancel));
  panel.append(confirm,advanced);

  document.body.append(backdrop,panel);
  const position=()=>{
    Object.assign(panel.style,{left:'50%',top:'50%',transform:'translate(-50%,-50%)',width:'min(520px, calc(100vw - 32px))',maxHeight:'calc(100dvh - 32px)',fontSize:'16px'});
  };
  position();
  window.addEventListener('resize',position);
  window.addEventListener('scroll',position);
  new ResizeObserver(position).observe(canvas);

  const showPanel=hidden=>{
    panel.style.display=hidden?'none':'flex';
    backdrop.style.display=hidden?'none':'block';
    panel.inert=hidden;
    panel.setAttribute('aria-hidden',hidden?'true':'false');
    if(hidden){if(panel.contains(document.activeElement))document.activeElement.blur();canvas?.focus();}
  };
  showPanel(!!spec.panel_hidden);

  return {
    values(){return JSON.stringify(Object.fromEntries([...fields].map(([name,input])=>[name,input.value])));},
    clearSecrets(){for(const name of ['password','permit','short_code']){const input=fields.get(name);if(input)input.value='';}},
    setState(state){
      if(state.messages){
        Object.assign(spec.messages,state.messages);title.textContent=spec.messages.title;modeHint.textContent=spec.messages.anonymous;
        advancedTitle.textContent=spec.messages.advanced;
        for(const [name,input] of fields){const text=spec.messages[name];if(text){input.placeholder=text;input.setAttribute('aria-label',text);labels.get(name).firstChild.nodeValue=text;}}
        for(const [name,node] of buttons){if(buttonKeys[name]&&spec.messages[buttonKeys[name]])node.textContent=spec.messages[buttonKeys[name]];}
      }
      if(state.finished){advanced.open=false;modeHint.style.display='none';title.textContent=state.locale==='en'?'Thank you':'感谢参与';}
      if(state.panel_hidden!==undefined)showPanel(!!state.panel_hidden);
      if(state.status!==undefined)document.getElementById('gec-shell-status').textContent=state.status;
      if(state.reason!==undefined)document.getElementById('gec-shell-status').dataset.reason=state.reason;
      if(state.busy!==undefined){busy=!!state.busy;for(const [name,node] of buttons)node.disabled=(busy||(panel.dataset.entryHidden==='1'&&['start','recover-code','recover-permit'].includes(name)))&&node.id!=='gec-confirm-cancel';}
      if(state.entry_hidden){
        // A successful admission retires the pre-entry form: hidden, disabled
        // and unfocused, so it cannot be submitted twice or cover the stimulus.
        panel.dataset.entryHidden='1';
        for(const [name,node] of fields){node.disabled=true;node.blur();const label=node.closest('label');if(label)label.style.display='none';}
        for(const name of ['start','recover-code','recover-permit']){const node=buttons.get(name);if(node){node.disabled=true;node.style.display='none';}}
        modeHint.style.display='none';confirm.style.display='none';failure.style.display='none';
      }
      if(state.failure!==undefined){
        const visible=!!(state.failure&&state.failure.visible);
        failure.style.display=visible?'flex':'none';
        buttons.get('retry').disabled=!(state.failure&&state.failure.retry);
        buttons.get('failure-export').disabled=!(state.failure&&state.failure.export);
        buttons.get('failure-export').style.display=visible?'':'none';
      }
      if(state.visible)for(const [name,node] of buttons)if(!['confirm-continue','confirm-new','confirm-cancel','retry','failure-export','local-retry'].includes(name))node.style.display=state.visible.includes(name)?'':'none';
      if(state.local_save_retry!==undefined)localSaveRetry=!!state.local_save_retry;
      updateLocalRetry();
      if(state.confirm!==undefined){
        if(state.confirm){confirmText.textContent=state.confirm.text;buttons.get('confirm-new').style.display=state.confirm.new_session?'':'none';confirm.style.display='flex';}
        else confirm.style.display='none';
      }
      if(state.focus&&!panel.inert){const input=fields.get(state.focus)??buttons.get(state.focus);input?.focus();}
    },
    blur(){for(const input of fields.values())input.blur();canvas?.focus();}
  };
}
