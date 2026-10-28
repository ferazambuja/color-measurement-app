import {call} from './owner.js';

// Activity follows the selected authenticated source, independently of acquisition.
export class ActivityPanel {
  constructor(container,{view,onJump,onCopied,onCount}){
    this.container=container;this.view=view;this.onJump=onJump;this.onCopied=onCopied;this.onCount=onCount;
    this.active=false;this.request=0;this.detailRequest=0;this.roster=null;this.details=null;this.selected=null;this.hint=null;this.source=null;
    this.node('filter').oninput=()=>this.renderRoster();this.node('statusFilter').onchange=()=>this.renderRoster();
    this.node('follow').onchange=()=>this.renderRoster();this.node('refresh').onclick=()=>{this.hint=null;return this.refresh();};
    this.node('copy').onclick=()=>this.copy();
  }
  node(id){return this.container.querySelector('[data-activity="'+id+'"]');}
  identity(view=this.view()){return JSON.stringify(view.fence);}
  knownCount(){const view=this.view();return view.available&&this.roster&&this.source===this.identity(view)&&this.hint===view.hint?this.roster.entries.filter(entry=>entry.kind==='operation').length:null;}
  current(capture){const view=this.view();return this.active&&view.available&&capture.source===this.identity(view)&&capture.hint===view.hint;}
  open(){
    this.active=true;const source=this.identity();
    if(this.source!==source){this.clear();this.node('filter').value='';this.node('statusFilter').value='all';this.node('follow').checked=true;}
    this.source=source;this.hint=null;return this.refresh();
  }
  close(){this.active=false;this.request++;this.detailRequest++;}
  clear(){this.roster=null;this.details=null;this.selected=null;this.hint=null;this.node('list').replaceChildren();this.node('details').replaceChildren();this.node('copy').disabled=true;}
  error(error){this.node('error').textContent=error.message||String(error);this.node('error').hidden=false;}
  async refresh(){
    if(!this.active)return;const view=this.view(),source=this.identity(view);
    this.node('source').textContent=view.name;
    if(!view.available){this.clear();this.request++;this.detailRequest++;this.node('status').textContent=view.reason||'Choose available saved data or a live batch.';return;}
    if(this.source!==source){this.source=source;this.clear();this.node('filter').value='';this.node('statusFilter').value='all';this.node('follow').checked=true;}
    if(this.hint===view.hint)return;this.hint=view.hint;
    this.details=null;this.detailRequest++;this.node('details').replaceChildren();this.node('copy').disabled=true;
    const capture={source,hint:view.hint,request:++this.request};this.node('status').textContent='Loading activity…';this.node('error').hidden=true;
    try{
      const reply=await call('activity',view.fence);
      if(!this.current(capture)||capture.request!==this.request)return;
      if(reply.binding_id!==view.fence.binding_id||reply.generation!==view.fence.generation)throw Error('Activity reply belongs to another source.');
      this.roster=reply.value;this.details=null;this.detailRequest++;this.node('details').replaceChildren();
      this.onCount(this.roster.entries.filter(entry=>entry.kind==='operation').length);
      this.renderRoster();
    }catch(error){if(this.current(capture)&&capture.request===this.request){this.clear();this.hint=capture.hint;this.error(error);this.node('status').textContent='Activity unavailable. Refresh to try again.';}}
  }
  filtered(){
    const search=this.node('filter').value.trim().toLocaleLowerCase(),status=this.node('statusFilter').value;
    return (this.roster?.entries||[]).filter(entry=>{
      const terminal=['completed','failed','cancelled'].includes(entry.status);
      const matches=status==='all'||status==='attention'?(status==='all'||entry.status==='failed'||entry.state==='terminal_unknown'||entry.review_state&&entry.review_state!=='accepted'):status==='in_progress'?!terminal&&entry.kind==='operation'&&['reserved','started'].includes(entry.state):status==='observation'?entry.kind==='observation':entry.status===status;
      return matches&&(!search||[entry.name,entry.notes_preview,entry.workflow,entry.status,entry.reason,entry.review_reason,entry.provenance].filter(Boolean).join(' ').toLocaleLowerCase().includes(search));
    });
  }
  label(entry){return entry.kind==='observation'?'Imported observation':({spot:'Measurement',calibrate:'Calibration',refresh_frequency:'Refresh frequency',display_timing:'Display timing',mode_snapshot:'Device settings',xy_sheet:'X-Y sheet',stored_chart:'Stored chart'}[entry.workflow]||entry.workflow||'Operation');}
  status(entry){return ['completed','failed','cancelled'].includes(entry.status)?({completed:'Completed',failed:'Failed',cancelled:'Cancelled'}[entry.status]):entry.kind==='observation'?'No terminal outcome':({reserved:'Preparing',started:'In progress',terminal_unknown:'Terminal outcome unavailable'}[entry.state]||'Outcome unavailable');}
  reportedCount(entry){return entry.reading_count==null?'Reported reading count unavailable':entry.reading_count+' reported reading'+(entry.reading_count===1?'':'s');}
  availableCount(details){const count=(details.readings||[]).length;return count+' available reading'+(count===1?'':'s')+' in this table';}
  renderRoster(){
    const focused=document.activeElement?.dataset?.activityInspect;
    const entries=this.filtered(),list=this.node('list');list.replaceChildren();
    this.node('count').textContent=entries.length+' of '+(this.roster?.entries.length||0)+' outcomes';
    this.node('status').textContent=this.roster?.reason||(!entries.length?(this.roster?.entries.length?'No matching outcomes.':'No retained operations or observations.'):'');
    if(this.node('follow').checked)this.selected=entries.at(-1)?.key||null;
    else if(!entries.some(entry=>entry.key===this.selected))this.selected=null;
    for(const entry of entries){
      const section=document.createElement('section');section.className='activity-entry';section.dataset.activityKey=entry.key;section.dataset.selected=String(entry.key===this.selected);
      const inspect=document.createElement('button');inspect.dataset.activityInspect=entry.key;inspect.setAttribute('aria-pressed',String(entry.key===this.selected));inspect.textContent=this.label(entry)+' · '+this.status(entry)+(entry.name?' · '+entry.name:'');
      inspect.title=inspect.textContent;inspect.onclick=()=>{this.node('follow').checked=false;this.selected=entry.key;this.renderRoster();};section.append(inspect);
      const pending=entry.kind==='operation'&&['reserved','started'].includes(entry.state);
      const text=document.createElement('p');text.className='small';text.textContent=this.reportedCount(entry)+' · '+(entry.provenance||'Provenance unavailable')+' · '+(entry.saved?'Saved':'Save unconfirmed')+(entry.record_retained===false?(pending?' · No terminal result yet':' · Result unavailable or excluded'):'');section.append(text);
      if(entry.reason||entry.review_reason){const reason=document.createElement('p');reason.className='small activity-reason';reason.textContent=[entry.reason,entry.review_reason].filter((value,index,values)=>value&&values.indexOf(value)===index).join(' · ');section.append(reason);}
      list.append(section);
    }
    if(focused){const replacement=[...list.querySelectorAll('[data-activity-inspect]')].find(node=>node.dataset.activityInspect===focused);(replacement||this.container).focus({preventScroll:true});}
    if(this.selected){this.loadDetails();if(this.node('follow').checked)list.querySelector('[data-selected="true"]')?.scrollIntoView?.({block:'nearest'});}
    else{this.detailRequest++;this.details=null;this.node('details').replaceChildren();}
    this.node('copy').disabled=!this.details;
  }
  async loadDetails(){
    const key=this.selected,revision=this.roster?.activity_revision;if(!key||!revision)return;
    if(this.details?.key===key&&this.details.activity_revision===revision)return;
    this.details=null;this.node('copy').disabled=true;this.node('details').textContent='Loading outcome details…';
    const view=this.view(),capture={source:this.identity(view),hint:view.hint,request:++this.detailRequest,key,revision};
    try{
      const reply=await call('activity_details',{...view.fence,key,activity_revision:revision});
      if(!this.current(capture)||capture.request!==this.detailRequest||key!==this.selected||revision!==this.roster?.activity_revision)return;
      const details=reply.value;
      if(reply.binding_id!==view.fence.binding_id||reply.generation!==view.fence.generation||details.key!==key||details.activity_revision!==revision)throw Error('Outcome details belong to another source or revision.');
      this.details=details;this.renderDetails();
    }catch(error){if(this.current(capture)&&capture.request===this.detailRequest){this.node('details').replaceChildren();this.error(error);}}
  }
  text(parent,text,tag='p'){const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;}
  disclosure(parent,label,value){if(value==null)return;const details=document.createElement('details');this.text(details,label,'summary');this.text(details,typeof value==='string'?value:JSON.stringify(value,null,2),'pre');parent.append(details);}
  // The owner states which application events a source keeps. Saved and imported
  // records never persisted their step timeline; say so instead of showing nothing.
  eventsNote(details){
    if(details.events?.length||!details.events_scope)return '';
    return details.events_scope.startsWith('native-records-only')?'Application step timeline unavailable: application events are not saved with this source. Native records and diagnostics are original.':
      details.source?.kind==='live'?'No recent application events for this operation in the latest 32 batch events.':'Application events: '+details.events_scope;
  }
  summaryLines(details=this.details){
    const entry=details.summary,result=details.result;
    const lines=[this.label(entry)+' · '+this.status(entry),entry.name||'',entry.provenance||'Provenance unavailable','Source: '+JSON.stringify(details.source),'Outcome source: '+JSON.stringify(entry.source_ref),'Operation: '+(entry.operation_id||'Unavailable'),'Command: '+(entry.command_id||'Not present in this source'),this.reportedCount(entry),this.availableCount(details),entry.saved?'Saved':'Save unconfirmed'];
    if(entry.reason)lines.push(entry.reason);if(entry.review_reason)lines.push('Source review: '+entry.review_reason);
    lines.push(this.eventsNote(details));
    if(result){if(result.error)lines.push('Native outcome: '+result.error);if(result.error_stage!=null)lines.push('Native failure stage: '+result.error_stage+' · code '+result.error_native_code);lines.push('Software stopped: '+result.software_stopped,'Instrument state: '+result.instrument_state);}
    for(const reading of details.readings||[]){const annotation=reading.annotation?.applied||reading.annotation||{};lines.push('Reading: '+(annotation.name||reading.observation.sample_id),'Reading identity: '+JSON.stringify(reading.reading_ref),'Notes: '+(annotation.notes||''));for(const [quantity,reason] of Object.entries(reading.coordinate_reasons||{}))lines.push(quantity+': '+reason);}
    return lines.filter(value=>value!=='');
  }
  renderDetails(){
    const details=this.details,body=this.node('details');body.replaceChildren();const entry=details.summary,result=details.result;
    this.text(body,this.label(entry)+' · '+this.status(entry),'h3');if(entry.name)this.text(body,entry.name);
    this.text(body,this.reportedCount(entry)+' · '+this.availableCount(details)).className='small';
    for(const reason of new Set([entry.reason,entry.review_reason].filter(Boolean))){this.text(body,reason.length>256?reason.slice(0,256)+'…':reason).className='activity-reason';if(reason.length>256)this.disclosure(body,'Complete outcome reason',reason);}
    if(result?.error)this.text(body,result.error).className='activity-reason';
    if(result?.error_stage!=null)this.text(body,'Native failure stage: '+result.error_stage+' · code '+result.error_native_code);
    if(result)this.text(body,'Software stopped: '+result.software_stopped+' · Instrument state: '+result.instrument_state).className='small';
    if(details.recovery_result)this.disclosure(body,'Unaccepted terminal recovery',details.recovery_result);
    if(!(details.readings||[]).length)this.text(body,'No available reading to jump to.').className='small';
    for(const reading of details.readings||[]){
      const section=document.createElement('section'),annotation=reading.annotation?.applied||reading.annotation||{},observation=reading.observation;
      this.text(section,annotation.name||observation.sample_id,'h4');
      if(annotation.notes)this.text(section,annotation.notes).className='activity-notes';
      if(reading.annotation?.draft?.dirty)this.disclosure(section,'Unapplied annotation draft',reading.annotation.draft);
      const jump=document.createElement('button');jump.dataset.activityJump=reading.key;jump.textContent='Show reading in table';jump.onclick=()=>{if(this.details===details&&this.current({source:this.source,hint:this.hint})&&details.activity_revision===this.roster?.activity_revision)this.onJump(reading.reading_ref);};section.append(jump);
      this.text(section,(observation.record_kind||'color')+' · '+observation.provenance+' · '+observation.device.model).className='small';
      const reported=observation.backend_reported||{};
      for(const [quantity,label] of [['xyz','Native XYZ'],['spectrum','Native spectrum']])if((observation.record_kind||'color')==='color'&&!reported[quantity])this.text(section,label+' unavailable: not reported in this reading.').className='small';
      for(const reason of new Set(Object.values(reading.coordinate_reasons||{})))this.text(section,reason).className='small';
      if(observation.warnings?.length)this.disclosure(section,'Reading warnings',observation.warnings);
      if(observation.raw_backend_output)this.disclosure(section,'Original native output',observation.raw_backend_output);
      this.disclosure(section,'Original quantities and calibration',observation);body.append(section);
    }
    const eventsNote=this.eventsNote(details);if(eventsNote)this.text(body,eventsNote).className='small';
    if(details.events?.length){const events=document.createElement('details');this.text(events,'Recent application events','summary');this.text(events,'Recent events for this operation, from the latest 32 batch events.','p');for(const event of details.events){this.text(events,event.kind,'h4');this.text(events,JSON.stringify(event.payload,null,2),'pre');}body.append(events);}
    const diagnostics=details.native_diagnostics;
    if(diagnostics?.metadata||diagnostics?.reason){const section=document.createElement('details');this.text(section,'Native diagnostic capture','summary');if(diagnostics.reason)this.text(section,diagnostics.reason);if(diagnostics.retention_reason)this.text(section,diagnostics.retention_reason);if(diagnostics.metadata){const capture=diagnostics.metadata;this.text(section,'Capture '+capture.state+' · '+capture.retained_bytes+' of '+capture.received_bytes+' bytes retained'+(capture.truncated?' · Truncated':''));if(capture.error)this.text(section,capture.error);}for(const segment of diagnostics.segments||[]){this.text(section,'Original stream offset '+segment.source_offset+' · '+segment.length+' bytes','h4');const bytes=Uint8Array.from(atob(segment.base64),char=>char.charCodeAt(0));this.text(section,new TextDecoder().decode(bytes),'pre');}this.disclosure(section,'Capture identity and retained ranges',diagnostics.metadata);this.disclosure(section,'Capture review',diagnostics.review);this.disclosure(section,'Exact retained bytes (base64)',diagnostics.original);body.append(section);}
    this.disclosure(body,'Source and original operation details',{source:details.source,source_ref:entry.source_ref,entry:details.entry,result:details.result});
    this.node('copy').disabled=false;
  }
  async copy(){
    const details=this.details,view=this.view();if(!details||!this.active||!view.available||this.source!==this.identity(view)||this.hint!==view.hint||details.key!==this.selected||details.activity_revision!==this.roster?.activity_revision)return;
    const text=this.summaryLines(details).join('\n');
    if(new TextEncoder().encode(text).length>131072){this.error(Error('This summary exceeds the clipboard limit. Export data to retain the full notes.'));return;}
    this.node('copy').disabled=true;
    try{const reply=await call('copy_summary',{text});if(this.details===details&&this.current({source:this.source,hint:this.hint})&&reply?.copied)this.onCopied();}
    catch(error){if(this.details===details&&this.active)this.error(error);}
    finally{this.node('copy').disabled=!this.details;}
  }
}
