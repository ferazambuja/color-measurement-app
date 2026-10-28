import {call} from './owner.js';

// Forms declare inputs. The Python scientific owners calculate and qualify results.
export class SciencePanel {
  constructor(container,{view,white,format,name}){
    this.container=container;this.view=view;this.white=white;this.format=format;this.name=name;
    this.epoch=0;this.active=false;this.busy=false;this.materials={};this.result=null;this.capture=null;
    this.node('method').onchange=()=>{this.invalidate();this.node('error').textContent='';this.node('error').hidden=true;this.renderFields();};
    this.container.addEventListener('input',()=>{this.invalidate();this.renderFields();});
    this.node('calculate').onclick=()=>this.calculate();
    this.node('chooseIlluminant').onclick=()=>this.choose('illuminant');
    this.node('chooseCharacterization').onclick=()=>this.choose('characterization');
  }
  node(id){return this.container.querySelector('[data-science="'+id+'"]');}
  invalidate(){this.epoch++;this.result=null;this.capture=null;this.node('results').replaceChildren();this.node('status').textContent='';this.node('error').textContent='';this.node('error').hidden=true;}
  signature(view=this.view()){return JSON.stringify([view.fence,view.revision,this.selected(view)]);}
  selected(view=this.view()){return this.node('scope').value==='all'?view.rows.map(row=>row.id):view.checked;}
  current(capture){return this.active&&this.view().available&&capture.epoch===this.epoch&&capture.signature===this.signature();}
  open(){
    const view=this.view(),source=JSON.stringify([view.fence,view.revision]);
    if(this.source!==source){this.materials={};this.invalidate();}this.source=source;this.active=true;
    const options=[['','Choose a white'],...view.standards.map(item=>['standard:'+item.name,item.name+' · assumed standard']),...view.rows.filter(row=>row.whiteEligible).map(row=>['reading:'+row.id,this.name(row)])];
    for(const id of ['sourceWhite','destinationWhite'])this.options(id,options);
    this.options('reference',[['','Choose a measured reference'],...view.rows.filter(row=>row.saved&&(row.obs.record_kind??'color')==='color').map(row=>[row.id,this.name(row)])]);
    this.renderFields();this.checkCurrent();
  }
  close(){this.active=false;this.epoch++;}
  options(id,values){const select=this.node(id),previous=select.value;select.replaceChildren(...values.map(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;return option;}));if(values.some(item=>item[0]===previous))select.value=previous;}
  checkCurrent(){
    if(this.capture&&this.capture.signature!==this.signature()){this.invalidate();this.node('status').textContent='Readings or selection changed. Calculate again.';}
    if(this.busy&&this.pendingCapture&&!this.current(this.pendingCapture))this.node('status').textContent='Inputs changed. Calculate again when the current request finishes.';
    this.renderAvailability();
  }
  renderAvailability(){const available=this.view().available;this.node('calculate').disabled=this.busy||!available||!this.selected().length;for(const id of ['chooseIlluminant','chooseCharacterization'])this.node(id).disabled=this.busy||!available;}
  renderFields(){
    const method=this.node('method').value,factor=method==='characterized_reflectance'||method==='relight_reflectance'&&this.node('basis').value==='characterized-reference';
    this.node('adaptation').hidden=method!=='adapt_xyz';this.node('spectral').hidden=method==='adapt_xyz';
    this.node('factor').hidden=!factor;this.node('relight').hidden=method!=='relight_reflectance';
    this.node('illumination').hidden=method==='adapt_xyz'||method==='characterized_reflectance';
    this.node('target').hidden=method!=='relight_reflectance';
    this.node('illuminantFile').hidden=method==='spectral_xyz'?this.node('emission').checked:this.node('illuminant').value!=='file';
    this.node('noIlluminant').hidden=method!=='spectral_xyz';
    this.node('characterizationFile').hidden=this.node('idealWhite').checked;
    for(const id of ['source','destination'])this.node(id+'YLabel').hidden=!this.node(id+'White').value.startsWith('standard:');
    const native=this.view().rows.find(row=>this.selected().includes(row.id)&&row.obs.backend_reported?.xyz)?.obs.backend_reported.xyz;
    this.node('whiteUnits').textContent=native?'Assumed white Y uses '+native.unit+' · '+native.scale+' · '+native.observer+'.':'A standard white needs native XYZ units, scale and observer from the selected readings.';
    for(const role of ['illuminant','characterization'])this.node(role+'Name').textContent=this.materials[role]?.source_name||'No file chosen';
    this.renderAvailability();
  }
  async choose(role){
    if(this.busy)return;this.invalidate();this.node('error').hidden=true;const view=this.view(),capture={epoch:this.epoch,signature:this.signature(view)};this.busy=true;this.renderAvailability();
    try{const selected=await call('choose_science_material',{...view.fence,dataset_revision:view.revision,role});if(selected&&this.current(capture))this.materials[role]=selected;}
    catch(error){if(this.current(capture))this.error(error);}
    finally{this.busy=false;this.renderFields();}
  }
  material(role){const material=this.materials[role];if(!material)throw Error('Choose the '+(role==='illuminant'?'illuminant SPD':'reference characterization')+' file.');return {token:material.token};}
  factorContext(view){
    const reference=view.rows.find(row=>row.id===this.node('reference').value),ideal=this.node('idealWhite').checked,setup=this.node('setup').value.trim();
    if(!reference)throw Error('Choose a saved measured reference reading.');
    if(!setup||!this.node('matched').checked)throw Error('Describe the setup and confirm that sample and reference match.');
    if(!this.node('nonfluorescent').checked)throw Error('Confirm the nonfluorescent reflectance model before calculating a factor.');
    return {reference:reference.readingRef,characterization:ideal?null:this.material('characterization'),characterization_id:null,assume_ideal_white:ideal,setup_declaration:setup,matched_setup:true,nonfluorescent_reflectance:true};
  }
  request(view){
    const method=this.node('method').value,native=view.rows.find(row=>this.selected(view).includes(row.id)&&row.obs.backend_reported?.xyz)?.obs.backend_reported.xyz;
    if(method==='adapt_xyz')return {method,context:{source_white:this.white(this.node('sourceWhite').value,this.node('sourceY').value,native),destination_white:this.white(this.node('destinationWhite').value,this.node('destinationY').value,native)}};
    if(method==='characterized_reflectance')return {method,context:this.factorContext(view)};
    if(!this.node('air').checked)throw Error('Declare standard-air nanometres for this calculation.');
    const context={observer:this.node('observer').value,wavelength_convention:'standard-air-nm',nonfluorescent_reflectance:this.node('nonfluorescent').checked};
    if(method==='spectral_xyz')return {method,context:{...context,illuminant:this.node('emission').checked?null:this.material('illuminant'),illuminant_id:null}};
    if(!context.nonfluorescent_reflectance)throw Error('Confirm the nonfluorescent reflectance model before relighting.');
    const illuminant=this.node('illuminant').value;
    return {method,context:{...context,reflectance:this.node('basis').value==='native'?{kind:'native'}:{kind:'characterized-reference',context:this.factorContext(view)},illuminant:illuminant==='file'?this.material('illuminant'):illuminant,illuminant_id:null,color_space:this.node('encoding').value||null}};
  }
  error(error){this.node('error').textContent=error.message||String(error);this.node('error').hidden=false;}
  async calculate(){
    if(this.busy||!this.selected().length)return;this.invalidate();this.node('error').hidden=true;
    const view=this.view(),capture={epoch:this.epoch,signature:this.signature(view),view};let derivation;
    try{derivation=this.request(view);}catch(error){this.error(error);return;}
    this.busy=true;this.pendingCapture=capture;this.node('status').textContent='Calculating…';this.renderAvailability();
    try{
      const reply=await call('derive',{...view.fence,dataset_revision:view.revision,selected:this.selected(view),derivation});
      if(!this.current(capture))return;
      if(reply.binding_id!==view.fence.binding_id||reply.generation!==view.fence.generation||reply.dataset_revision!==view.revision||reply.method!==derivation.method)throw Error('Calculation reply does not match these inputs.');
      this.result={derivation,reply};this.capture=capture;this.renderResults();this.node('status').textContent='Calculation complete. Native readings are unchanged.';
    }catch(error){if(this.current(capture)){this.node('status').textContent='';this.error(error);}}
    finally{this.busy=false;this.pendingCapture=null;this.renderAvailability();}
  }
  exportRequest(){this.checkCurrent();return this.result?.derivation||null;}
  renderResults(){
    const body=this.node('results');body.replaceChildren();
    for(const [key,entry] of Object.entries(this.result.reply.by_source)){
      const section=document.createElement('section'),heading=document.createElement('h3');heading.textContent=this.name(this.capture.view.rows.find(row=>row.id===key));section.append(heading);
      const result=entry.result,xyz=result?.xyz||result,lab=result?.lab;
      const lines=[];
      if(entry.reason)lines.push('Unavailable: '+entry.reason);
      if(xyz?.values)lines.push('Derived XYZ: '+xyz.values.map(this.format).join(' / ')+' · '+(xyz.unit||'')+' · '+(xyz.scale||''));
      if(lab)lines.push('Derived Lab: '+lab.map(this.format).join(' / '));
      if(result?.spectrum||result?.reflectance)lines.push('Derived reflectance: '+(result.spectrum||result.reflectance).values.length+' bands');
      for(const field of ['xyz_reason','lab_reason','color_reason'])if(result?.[field])lines.push(result[field]);
      for(const line of lines){const p=document.createElement('p');p.className='small';p.textContent=line;section.append(p);}
      const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Source and method details';pre.textContent=JSON.stringify(entry,null,2);details.append(summary,pre);section.append(details);body.append(section);
    }
  }
}
