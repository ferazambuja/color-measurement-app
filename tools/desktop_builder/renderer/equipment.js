import {call} from './owner.js';

// Presentation and explicit commands only. Python owns discovery and device policy.
export class EquipmentPanel {
  constructor(container,{canAct,onBusy,onSelect}) {
    this.container=container;this.canAct=canAct;this.onBusy=onBusy;this.onSelect=onSelect;
    this.state=null;this.selected=null;this.binding=null;this.busy=false;this.loading=false;this.unresolved=null;this.epoch=0;this.active=false;this.directPort=null;this.directBinding=null;this.checking=false;this.cancelling=false;
    this.node('report').onclick=()=>this.chooseReport();
    this.node('refresh').onclick=()=>this.state?this.effect('refresh_devices',{setup_generation:this.state.generation}):undefined;
    this.node('connect').onclick=()=>this.connect();
    this.node('reconcile').onclick=()=>this.setupPending?this.checkSetup():this.unresolved?this.effect(this.unresolved.method,this.unresolved.params,true):undefined;
    this.node('cancelSetup').onclick=()=>this.cancelSetup();
    this.node('route').onchange=()=>{this.directBinding=null;this.renderDirect(true);this.renderBranch();};
    this.node('directModel').onchange=()=>{this.directBinding=null;this.renderDirect(true);};
    this.node('directPort').onclick=()=>this.chooseDirectPort();this.node('directConnect').onclick=()=>this.connectDirect();
    for(const id of ['directSerial','directAccept','directResponse','directMeasurement'])this.node(id).oninput=()=>this.renderAvailability();
    this.node('model').onchange=()=>this.renderSelection(true);
    for(const id of ['serialPermission','disclosed','unassessed','timeout','serial'])this.node(id).oninput=()=>this.renderAvailability();
  }
  node(id){return this.container.querySelector('[data-equipment="'+id+'"]');}
  get setupPending(){return !!this.state?.active_setup||['connect_direct_serial','reopen_direct_serial','connect_device','reopen_device','refresh_devices','equipment_outcome'].includes(this.unresolved?.method);}
  async initialise(){this.accept(await call('equipment_setup',{}));this.onBusy();}
  current(epoch){return this.active&&this.epoch===epoch;}
  error(error){this.node('error').textContent=error.message||String(error);this.node('error').hidden=false;}
  setBusy(value){this.busy=value;this.renderAvailability();this.onBusy();}
  async open(){
    this.active=true;this.loading=true;this.renderAvailability();const epoch=++this.epoch;
    try{const state=await call('equipment_setup',{});if(this.current(epoch)){this.accept(state);if(state.reason&&['failed','interrupted'].includes(state.state))this.error(Error(state.reason));else this.node('error').hidden=true;}}
    catch(error){if(this.current(epoch)){this.state=null;this.error(error);}}
    finally{if(this.current(epoch)){this.loading=false;this.renderAvailability();}}
  }
  close(){this.active=false;this.epoch++;}
  accept(state){
    const changed=this.state?.generation!==state.generation||this.state?.listing_generation!==state.listing_generation;
    this.state=state;
    if(state.active_setup&&!this.unresolved)this.unresolved={method:'equipment_outcome',params:{command_id:state.active_setup.command_id}};
    if(changed){this.selected=null;this.binding=null;this.directPort=null;this.directBinding=null;this.resetAcknowledgements();}
    if(this.selected&&!state.candidates.some(row=>row.candidate_id===this.selected&&!row.consumed))this.selected=null;
    this.render();this.onBusy();
  }
  resetAcknowledgements(){for(const id of ['serialPermission','disclosed','unassessed'])this.node(id).checked=false;this.node('serial').value='';}
  async chooseReport(){
    if(!this.active||this.loading||!this.state||!this.canAct()||this.busy||this.unresolved)return;
    const epoch=this.epoch,generation=this.state.generation;this.setBusy(true);this.node('error').hidden=true;
    try{const state=await call('choose_build_report',{setup_generation:generation});if(state&&this.current(epoch))this.accept(state);}
    catch(error){if(this.current(epoch))this.error(error);}
    finally{this.setBusy(false);}
  }
  async effect(method,params,reconcile=false){
    if(!this.active||this.loading||!this.state||!this.canAct()||this.busy||this.unresolved&&!reconcile)return;
    const captured={method,params:reconcile?params:{command_id:crypto.randomUUID(),...params}},epoch=this.epoch;
    this.setBusy(true);this.node('error').hidden=true;
    const asynchronous=['connect_direct_serial','reopen_direct_serial','connect_device','reopen_device','refresh_devices'].includes(method);
    let replyReceived=false;
    if(asynchronous)this.unresolved=captured;
    try{
      const result=await call(reconcile&&asynchronous?'equipment_outcome':method,reconcile&&asynchronous?{command_id:captured.params.command_id}:captured.params);replyReceived=true;this.unresolved=result.state==='pending'?captured:null;
      const state=await call('equipment_setup',{});
      // An action can outlive its panel; only its current opening may paint the result.
      if(this.current(epoch)){
        this.accept(state);
        if(result.state!=='completed'&&result.state!=='pending')this.error(Error(result.reason||'Device setup did not complete.'));
        else if(result.result?.selection_id)this.node('status').textContent='Device initialized. It is ready for a new batch.';
      }
    }catch(error){
      if(error.ownerReplyDelayed)this.unresolved=captured;
      else if(!replyReceived&&this.unresolved===captured)this.unresolved=null;
      if(this.current(epoch))this.error(error);
    }finally{this.setBusy(false);}
  }
  async checkSetup(){
    if(!this.unresolved&&this.state?.active_setup)this.unresolved={method:'equipment_outcome',params:{command_id:this.state.active_setup.command_id}};
    const captured=this.unresolved,epoch=this.epoch;
    if(!this.setupPending||!captured||this.busy||this.checking)return;
    this.checking=true;this.renderAvailability();
    try{
      const result=await call('equipment_outcome',{command_id:captured.params.command_id});
      const state=await call('equipment_setup',{});
      if(this.unresolved!==captured)return;
      this.unresolved=result.state==='pending'?captured:null;
      if(this.current(epoch)||!this.active)this.accept(state);
      if(this.current(epoch)){
        if(result.state==='pending')this.node('error').hidden=true;
        else if(result.state!=='completed')this.error(Error(result.reason||'Device setup did not complete.'));
        else{this.node('error').hidden=true;if(result.result?.selection_id)this.node('status').textContent='Device initialized. It is ready for a new batch.';}
      }
    }catch(error){
      // The owner answers requests in order, so an ID it does not know was never
      // admitted: its delayed reply was a refusal, and nothing was started.
      if(error.message==='Unknown equipment command ID'&&this.unresolved===captured){this.unresolved=null;error=Error('The device request was not admitted, so nothing was started. Try it again.');}
      if(this.current(epoch))this.error(error);
    }
    finally{this.checking=false;this.renderAvailability();this.onBusy();}
  }
  async cancelSetup(){
    const active=this.state?.active_setup,epoch=this.epoch;
    if(!this.active||!active||active.cancel_requested||this.cancelling)return;
    const commandId=active.command_id;this.cancelling=true;this.renderAvailability();
    try{
      await call('cancel_setup',{command_id:commandId});
      const state=await call('equipment_setup',{});if(this.current(epoch))this.accept(state);
    }catch(error){if(this.current(epoch)&&this.state?.active_setup?.command_id===commandId)this.error(error);}
    finally{this.cancelling=false;this.renderAvailability();this.onBusy();}
  }
  candidate(){return this.state?.candidates.find(row=>row.candidate_id===this.selected)||null;}
  model(){return this.catalogModel(this.binding?.model_enum??this.node('model').value);}
  catalogModel(modelEnum){return this.state?.catalog?.instrument_types.find(row=>row.model_enum===modelEnum)||null;}
  renderSelection(reset=false){
    const candidate=this.candidate();this.node('selection').hidden=!candidate;
    if(!candidate)return this.renderAvailability();
    if(reset)this.resetAcknowledgements();
    const model=this.model(),facts=this.binding||(candidate.identified?candidate:model);
    const opening=this.binding?.opening_requires??(candidate.identified?candidate.opening_requires:model?.opening_requires);
    const reason=facts?.initialization_refusal;
    const text=reason?reason.replace(/^initialization_/,'Initialization ').replaceAll('_',' ').replace(/leds/g,'LEDs'):facts?.initialization_assessed?'Initialization has no disclosed special effects.':'Initialization effects have not been assessed for this model.';
    const identificationRefused=!candidate.identified&&model&&model.typed_serial_selection!==true&&model.generic_serial_identification!==true;
    this.node('initialization').textContent=this.binding&&!model?'This instrument model is unavailable in the selected software report. Choose a report containing this model before reinitializing.':identificationRefused?'This model cannot identify an unrecognized connection. Choose the correct model or list a recognized device.':text;
    this.node('disclosedLabel').hidden=!reason||opening==='not_permitted';
    this.node('unassessedLabel').hidden=!!reason||facts?.initialization_assessed===true;
    this.node('serialPermissionLabel').hidden=candidate.identified||!model?.generic_serial_identification;
    this.node('modelLabel').hidden=candidate.identified||!!this.binding;
    this.node('serialLabel').hidden=!!this.binding;
    const policy=this.binding?.binding_policy||model?.binding_policy;
    if(reset)this.node('timeout').value=policy?.default_binding_timeout_seconds??'';
    this.node('timeoutReason').textContent=policy?.all_modes_read_allowance_floor_seconds!=null?'Instrument allowance: more than '+policy.all_modes_read_allowance_floor_seconds+' seconds.':'';
    this.node('connect').textContent=this.binding?'Reconnect selected instrument':'Connect selected instrument';
    this.renderAvailability();
  }
  renderAvailability(){
    const blocked=!this.active||!this.state||this.loading||this.busy||!!this.unresolved||this.setupPending||!this.canAct();
    this.node('route').disabled=blocked;
    const software=this.state?.instrument_software,unavailable=software?.packaged&&!software.ready;
    this.node('report').disabled=blocked||!this.state||!!software?.packaged;
    this.node('refresh').disabled=blocked||!this.state?.report||unavailable;
    this.node('reconcile').hidden=!this.unresolved;this.node('reconcile').disabled=!this.active||this.loading||this.busy||this.checking||!this.canAct();
    const active=this.state?.active_setup;
    this.node('cancelSetup').hidden=!active;this.node('cancelSetup').disabled=!this.active||!active||active.cancel_requested||this.cancelling;
    this.node('cancelSetup').textContent=active?.action==='refresh_devices'?'Cancel listing':active?.cancellation_mode==='after_initialization'?'Cancel connection':'Cancel device setup';
    const candidate=this.candidate(),model=this.model(),facts=this.binding||(candidate?.identified?candidate:model);
    const opening=this.binding?.opening_requires??(candidate?.identified?candidate.opening_requires:model?.opening_requires);
    const timeout=Number(this.node('timeout').value),floor=(this.binding?.binding_policy||model?.binding_policy)?.all_modes_read_allowance_floor_seconds;
    const permitted=candidate&&!candidate.consumed&&opening!=='not_permitted'&&model&&
      (!facts.initialization_refusal||this.node('disclosed').checked)&&
      (facts.initialization_assessed===true||this.node('unassessed').checked)&&
      (candidate.identified||model.typed_serial_selection===true||model.generic_serial_identification===true&&this.node('serialPermission').checked)&&
      Number.isFinite(timeout)&&timeout>0&&timeout<=600&&(floor==null||timeout>floor);
    this.node('connect').disabled=blocked||unavailable||this.node('route').value!=='argyll'||!permitted;
    this.container.querySelectorAll('[data-candidate],[data-binding-action]').forEach(button=>{
      if(button.dataset.bindingAction==='reopen')button.dataset.unavailable=String(button.dataset.hasSerial!=='true'||(button.dataset.route==='direct'?button.dataset.bindingState!=='closed'||!this.directModelPolicy(button.dataset.modelEnum):!this.catalogModel(button.dataset.modelEnum)||!candidate||candidate.consumed||candidate.opening_requires==='not_permitted'));
      button.disabled=blocked||button.dataset.unavailable==='true';
    });
    for(const id of ['model','serial','timeout','serialPermission','disclosed','unassessed'])this.node(id).disabled=blocked;
    const directModel=this.directModelPolicy(this.directBinding?.model||this.node('directModel').value),response=Number(this.node('directResponse').value),measurement=Number(this.node('directMeasurement').value),maximum=directModel?.binding_policy.maximum_timeout_seconds;
    this.node('directConnect').disabled=blocked||unavailable||this.node('route').value!=='direct'||!directModel||!this.directPort||!this.node('directAccept').checked||!Number.isFinite(response)||response<=0||response>maximum||!Number.isFinite(measurement)||measurement<=0||measurement>maximum;
    for(const id of ['directPort','directAccept','directResponse','directMeasurement'])this.node(id).disabled=blocked||unavailable;
    this.node('directModel').disabled=blocked||!!this.directBinding;this.node('directSerial').disabled=blocked||!!this.directBinding;
    // The stage names desktop_setup.py records; Argyll's description initializes the instrument.
    const stages={admission:'Starting device setup…',selection_validation:'Checking selected instrument…',enumeration:'Listing connected instruments…',description:'Initializing instrument…',initialization:'Initializing instrument…',binding:'Preparing the measurement session…',settings_readback:'Reading instrument settings…',registration:'Preparing the device source…',host_cleanup:'Releasing the serial port…'};
    const listing=active?.action==='refresh_devices';
    this.node('progress').hidden=!this.busy&&!this.loading&&!this.setupPending;
    this.node('progress').textContent=active?.cancel_requested?(listing?'Cancellation requested. Waiting for the listing to finish; it will not be shown.':'Cancellation requested. Waiting for initialization to finish; device state is unconfirmed.'):this.loading?'Loading device setup…':this.setupPending?(stages[active?.stage]||'Waiting for the device setup result…')+(listing?' Cancellation discards this listing.':active?.cancellation_mode==='after_initialization'?' Cancellation waits for initialization to finish and prevents adding this connection.':''):this.busy?'Device setup in progress…':'';
  }
  render(){
    const state=this.state;
    const report=state.report;
    const software=state.instrument_software;
    this.node('reportPicker').hidden=!!software?.packaged;
    this.node('reportName').textContent=software?.error|| (report?'Instrument software ready':'Instrument software is not configured in this development installation.');
    this.node('reportName').classList.toggle('dialog-error',!!software?.error);
    this.node('reportName').hidden=!!software?.ready;
    this.node('reportDetails').textContent=report?report.path+'\nSHA-256: '+report.sha256+'\n'+JSON.stringify(report.context,null,2):'';
    const models=this.node('model'),previousModel=models.value;models.replaceChildren();
    const empty=document.createElement('option');empty.value='';empty.textContent='Choose the instrument model';models.append(empty);
    for(const item of state.catalog?.instrument_types||[]){const option=document.createElement('option');option.value=item.model_enum;option.textContent=item.display_label;models.append(option);}
    const list=this.node('candidates');list.replaceChildren();
    for(const row of state.candidates){
      const section=document.createElement('section'),button=document.createElement('button');
      button.dataset.candidate=row.candidate_id;button.dataset.unavailable=String(row.consumed||row.opening_requires==='not_permitted');
      button.textContent=(row.name||'Unidentified connection')+' · '+row.transports.join(' / ')+' #'+row.index;
      button.onclick=()=>{this.selected=row.candidate_id;this.binding=null;this.resetAcknowledgements();models.value=row.symbol||'';this.renderSelection(true);};section.append(button);
      const description=document.createElement('p');description.className='small muted';description.textContent=row.consumed?'Initialization was attempted. List again before another attempt.':row.opening_requires==='not_permitted'?'Initialization is unavailable for this device.':row.identified?'Listed from connection metadata; not initialized.':'Choose its model before initializing this connection.';section.append(description);
      const details=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('pre');summary.textContent='Connection details';text.textContent=row.path;details.append(summary,text);section.append(details);list.append(section);
    }
    const directModels=this.node('directModel'),previousDirect=directModels.value;directModels.replaceChildren();
    const directEmpty=document.createElement('option');directEmpty.value='';directEmpty.textContent='Choose the instrument model';directModels.append(directEmpty);
    for(const item of state.direct_serial?.models||[]){const option=document.createElement('option');option.value=item.model_enum;option.textContent=item.display_label;directModels.append(option);}
    directModels.value=this.directBinding?.model||previousDirect;this.renderDirect();this.renderBranch();
    this.node('connected').hidden=state.bindings.length===0;
    const bindings=this.node('bindings');bindings.replaceChildren();
    for(const binding of state.bindings){
      const direct=binding.backend==='direct_serial',section=document.createElement('section'),label=document.createElement('p');label.textContent=binding.model+(binding.serial?' · '+binding.serial:' · no reported serial')+' · '+(binding.state==='active'?(direct&&binding.connection_available===false?'Connection unavailable':'Ready'):binding.state==='cleanup_required'?'Host port needs closing':'Removed from app');section.append(label);
      if(binding.state==='active'||direct&&binding.state==='cleanup_required'){
        const use=document.createElement('button');use.dataset.bindingAction='use';use.textContent='Use for new batch';use.dataset.unavailable=String(binding.state!=='active'||!binding.device_id||direct&&binding.connection_available===false);use.onclick=()=>this.useBinding(binding);section.append(use);
        const close=document.createElement('button');close.dataset.bindingAction='close';close.textContent=direct?'Close connection':'Remove from app';close.onclick=()=>this.effect('close_device',{setup_generation:state.generation,binding_id:binding.binding_id,binding_generation:binding.generation});section.append(close);
      }
      const reopen=document.createElement('button');reopen.dataset.bindingAction='reopen';reopen.dataset.route=direct?'direct':'argyll';reopen.dataset.bindingState=binding.state;reopen.dataset.hasSerial=String(!!binding.serial);reopen.dataset.modelEnum=direct?binding.model:binding.model_enum;reopen.textContent='Reinitialize…';reopen.onclick=()=>{if(reopen.disabled)return;if(direct){this.directBinding=binding;this.node('route').value='direct';this.directPort=null;this.node('directModel').value=binding.model;this.renderDirect(true);this.renderBranch();}else{this.binding=binding;this.node('route').value='argyll';models.value=binding.model_enum;this.renderSelection(true);this.renderBranch();}};section.append(reopen);
      if(!direct&&!this.catalogModel(binding.model_enum)){const reason=document.createElement('p');reason.className='small muted';reason.textContent='This model is unavailable in the selected software report. Choose its report before reinitializing.';section.append(reason);}
      bindings.append(section);
    }
    const chosen=this.candidate();if(chosen)models.value=chosen.symbol||previousModel;
    this.renderSelection();this.renderAvailability();
  }
  async useBinding(binding){
    if(!this.active||this.loading||!this.state||!this.canAct()||this.busy||this.unresolved)return;
    const epoch=this.epoch;this.setBusy(true);this.node('error').hidden=true;
    try{
      const settings=await call('settings',{selection_id:null});if(!this.current(epoch))return;
      const source=settings.selections.find(row=>row.device_id===binding.device_id);
      if(!source)throw Error('This device source changed. Refresh its settings.');
      await this.onSelect(source.selection_id);
    }catch(error){if(this.current(epoch))this.error(error);}
    finally{this.setBusy(false);}
  }
  directModelPolicy(model){return this.state?.direct_serial?.models.find(item=>item.model_enum===model)||null;}
  renderBranch(){
    const direct=this.node('route').value==='direct',state=this.state;
    this.node('argyll').hidden=direct;this.node('direct').hidden=!direct;
    this.node('status').textContent=state?.instrument_software?.error||state?.reason||(direct?'Choose a model and port, then review its initialization effects.':!state?.report?'Instrument software is unavailable. Open Instrument software details for setup information.':state.candidates.length?'Select your instrument, review its connection settings, then connect.':state.listing_generation>0?'No instruments found. Check power and the USB or serial connection, then list again.':'Connect and power on your instrument, then choose List connected instruments.');
    this.renderAvailability();
  }
  renderDirect(reset=false){
    const model=this.directModelPolicy(this.directBinding?.model||this.node('directModel').value),policy=model?.binding_policy;
    if(reset){this.node('directAccept').checked=false;this.node('directSerial').value=this.directBinding?.serial||'';this.node('directResponse').value=policy?.response_timeout_default_seconds??5;this.node('directMeasurement').value=policy?.measurement_timeout_default_seconds??300;}
    this.node('directEffects').replaceChildren();
    if(model){const list=document.createElement('ul');for(const effect of model.initialization_effects){const item=document.createElement('li');item.textContent=effect;list.append(item);}this.node('directEffects').append(list);}
    this.node('directPortName').textContent=this.directPort?.label||'No port selected';
    this.node('directAllowance').textContent=policy?'Default measurement allowance: '+policy.measurement_timeout_default_seconds+' seconds. '+policy.measurement_floor_basis+(policy.measurement_floor_seconds!==null?' · Default response budget requires at least '+policy.measurement_floor_seconds+' seconds.':'')+' These are app wait limits; the selected allowances are checked before opening.':'';
    this.node('directConnect').textContent=this.directBinding?'Reopen selected instrument':'Initialize selected instrument';this.renderAvailability();
  }
  async chooseDirectPort(){
    if(!this.active||!this.state||this.busy||this.unresolved||!this.canAct())return;
    const epoch=this.epoch,generation=this.state.generation;this.setBusy(true);this.node('error').hidden=true;
    try{const selected=await call('choose_direct_port',{setup_generation:generation});if(selected&&this.current(epoch)&&this.state.generation===generation){this.directPort=selected;this.renderDirect();}}
    catch(error){if(this.current(epoch))this.error(error);}
    finally{this.setBusy(false);}
  }
  connectDirect(){
    if(this.node('directConnect').disabled)return;
    const binding=this.directBinding,params={setup_generation:this.state.generation,port_token:this.directPort.port_token,accept_disclosed_initialization:this.node('directAccept').checked,response_timeout:Number(this.node('directResponse').value),measurement_timeout:Number(this.node('directMeasurement').value)};
    if(binding){params.binding_id=binding.binding_id;params.binding_generation=binding.generation;}else{params.model=this.node('directModel').value;params.expected_serial=this.node('directSerial').value.trim()||null;}
    return this.effect(binding?'reopen_direct_serial':'connect_direct_serial',params);
  }
  connect(){
    if(this.node('connect').disabled)return;
    const candidate=this.candidate(),model=this.model(),binding=this.binding;
    const params={setup_generation:this.state.generation,listing_generation:this.state.listing_generation,candidate_id:candidate.candidate_id,
      allow_serial_identification:this.node('serialPermission').checked,accept_disclosed_initialization:this.node('disclosed').checked,
      accept_unassessed_initialization:this.node('unassessed').checked,timeout:Number(this.node('timeout').value)};
    if(binding){params.binding_id=binding.binding_id;params.binding_generation=binding.generation;}
    else {params.expect=candidate.identified?null:model.model_enum;params.expected_serial=this.node('serial').value.trim()||null;}
    return this.effect(binding?'reopen_device':'connect_device',params);
  }
}
