/* Browser-native setup fields: paste/IME stays within the browser input event. */
export function installInputs(specification) {
  const spec=JSON.parse(specification), canvas=document.getElementById('canvas');
  const fields=new Map();
  const position=()=>{
    const rect=canvas.getBoundingClientRect();
    for(const field of spec.fields){
      const input=fields.get(field.name),sx=rect.width/spec.width,sy=rect.height/spec.height;
      Object.assign(input.style,{left:`${rect.left+field.x*sx}px`,top:`${rect.top+field.y*sy}px`,width:`${field.width*sx}px`,height:`${field.height*sy}px`,fontSize:`${20*sy}px`});
    }
  };
  for(const field of spec.fields){
    const input=document.createElement('input');
    input.id=`gec-input-${field.name}`;input.type=field.secret?'password':'text';
    input.placeholder=field.label;input.setAttribute('aria-label',field.label);
    input.autocomplete='off';input.spellcheck=false;input.maxLength=field.name==='code'?128:256;
    Object.assign(input.style,{position:'fixed',zIndex:'5',boxSizing:'border-box',background:'#303030',color:'#eee',border:'1px solid #777',borderRadius:'3px',padding:'4px 6px',fontFamily:'sans-serif'});
    // Do not let setup-field keys become task responses or Godot clipboard reads.
    for(const event of ['keydown','keyup','keypress','paste','copy','cut'])input.addEventListener(event,e=>e.stopPropagation());
    fields.set(field.name,input);document.body.append(input);
  }
  position();window.addEventListener('resize',position);window.addEventListener('scroll',position);
  new ResizeObserver(position).observe(canvas);
  return {
    values(){return JSON.stringify(Object.fromEntries([...fields].map(([name,input])=>[name,input.value])));},
    clearSecrets(){for(const name of ['password','permit'])fields.get(name).value='';},
    blur(){for(const input of fields.values())input.blur();canvas.focus();}
  };
}
