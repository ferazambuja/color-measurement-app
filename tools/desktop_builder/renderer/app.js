import { ColorSpaceView } from './space-view.js';
import {call, command} from './owner.js';
import {renderWorkflowFields, collectWorkflowFields} from './workflow-fields.js';
import {drawPresentation} from './presentation.js';
import {renderChartGuide, nominalChartColor, labelChartCell} from './chart-guide.js';
import {EquipmentPanel} from './equipment.js';
import {SciencePanel} from './science-panel.js';
import {ActivityPanel} from './activity-panel.js';
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const numberText = value => Number.isFinite(value)?Number(value.toPrecision(6)).toString():'Unavailable';
const fixedText = (value, places=3) => Number.isFinite(value)?(Math.abs(value)<0.5*10**-places?0:value).toFixed(places):'Unavailable';
const unitText = value => String(value).replaceAll('m^2','m²');
const observerText = value => ({CIE1931_2:'CIE 1931 2°',CIE1964_10:'CIE 1964 10°'}[value] || value);
const scaleText = value => value==='absolute; Y is luminance'?'absolute luminance':value;
function roundedFitScale(value) {
  const step=10**(Math.floor(Math.log10(value))-5);
  let rounded=Number(value.toPrecision(6));
  if(rounded<value)rounded=Number((rounded+step).toPrecision(6));
  return Math.max(value,rounded);
}
const freshState = () => ({batch:false, name:'Untitled batch', notes:'', destination:'', finished:false, busy:null, step:0, rows:[], outcomes:[], selected:null, unsaved:false, follow:true, tool:'measure', plotLayout:'overview', plotSlots:['spectrum','chromaticity'], plotSlotsSet:false, reference:null, plotted:new Set(), previousPlotted:null, edits:{}, inspector:false, white:null, whiteChoice:null});
let state = freshState();
// Preview is local and transient. It never changes acquisition or saved inspection.
let pointerPreview=null,focusPreview=null;

let activePanel=null,panelEpoch=0,editorPinned=false,editorRequest=0;
let datasetBinding=null,pendingExports=[];
const recordCompanionFailures=new Map();
let recordImportFormats=[],recordExportTargets=[],recordExportContext=null,recordFormatsRequest=0;
let ownerState=null, ownerRevision='', lastSnapshotKey='', queuedWrites=0, writes=Promise.resolve(), blocked=false;
const appliedDetails=new Map(),annotationLoads=new Set(),localEditValues=new Map(),localEditErrors=new Map();
let unresolvedCommand=null;
let demoLoading=false,demoLoadId=null;
let setupDescriptor=null,setupSelection=null,setupFrozen=false,setupRequest=0,setupBinding=null,setupChartDemo=false;
let setupSettingsGeneration=null,setupCorrection=null,setupCorrectionMaterial=null,correctionPending=false,currentCorrectionMaterial=null;
let equipmentPanel=null;
let equipmentStartup=null;
let setupDemo=false,equipmentReturnSetup=false;
let lastViewKey='';
let chartView=null,chartViewKey='',chartRequest=0,chartSetupPacket=null,chartSourcePacket=null,chartHistoryOffset=0,chartViewedPass=null,chartPreviewRequest=0,chartColorKey='',chartColors=null,chartColorBasis=null,expectedChartContext=null,chartColorError='';
let chartHistoryPending=null,chartHistoryTarget=null,finishChartTarget=null;
let workflowFieldsKey='',presentationController=null,presentationKey='',presentationEpoch=0;
let presentationInert=[],presentationInvoker=null;
const presentationClock='workspace-'+crypto.randomUUID();
const workflowLabels={spot:'Individual reading',strip:'Strip reading',xy:'X-Y sheet',chart:'Stored chart',refresh_frequency:'Refresh frequency',display_timing:'Display timing',mode_snapshot:'Check instrument configuration',stored_refresh:'Read stored refresh frequency',native_controls:'Read or apply instrument settings',calibrate:'Calibration'};
let defaultLabWhiteRequested=false,defaultLabWhitePending=false;
let editorTarget=null;
let sampleEditTimer=null,samplePreviewTimer=null,displayedDraftId=null,sampleActionPending=false;
let samplePreview=null,samplePreviewRequest=null,samplePreviewError='';
function editorReading(){return editorTarget&&sameDataset(editorTarget)?state.rows.find(r=>r.id===editorTarget.readingId)||null:null;}
function requireEditorReading(target){
  const row=sameDataset(target)&&target.batchId===ownerState?.batch_id&&state.rows.find(r=>r.id===target.readingId);
  if(!row||annotationsLocked(row))throw Error('This reading is no longer editable. Reopen its details.');
  return row;
}
function enqueue(work,editKey=null,captured=null,errorContext=null){
  const token=captured?{value:captured}:null;
  if(editKey&&token)localEditValues.set(editKey,token);
  queuedWrites++;
  writes=writes.then(async()=>{
    if(blocked)throw Error('Check the delayed action with Reload or Close before making another change.');
    await work();
    if(editKey&&localEditValues.get(editKey)===token){localEditValues.delete(editKey);localEditErrors.delete(editKey);}
  }).catch(error=>{
    if(editKey&&(error.refused||error.commandParams||blocked))localEditErrors.set(editKey,error.message);
    if(error.commandParams){blocked=true;unresolvedCommand={params:error.commandParams,method:error.commandMethod,editKey,token};}
    showStatusError(error,errorContext);
  }).finally(()=>{queuedWrites--;renderLocalEdits();renderSampleControls();renderDrafts();if(editorReading()&&$('editName'))renderEditorValues(editorReading());});
  return writes;
}
function renderLocalEdits(){
  $('localEditStatus').hidden=!localEditErrors.size&&!blocked;
  $('localEditMessage').textContent=blocked?'The last action has not been confirmed. Use Reload or Close to check its result.':
    'The app could not save this text. Correct or discard it before Apply, Reload or Close.';
  $('discardLocalEdits').disabled=blocked;
}
async function reconcileWrites(){
  if(sampleCompositionPending())throw Error('Finish entering the sample text before continuing.');
  clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer);
  await writes;
  if(unresolvedCommand){
    const item=unresolvedCommand,outcome=item.method?await call(item.method,item.params):await call('outcome',{command_id:item.params.command_id});
    if(outcome.admission==='admitted'&&item.editKey&&localEditValues.get(item.editKey)===item.token){
      localEditValues.delete(item.editKey);localEditErrors.delete(item.editKey);
    }
    blocked=false;unresolvedCommand=null;await refresh(true);
  }
  await enqueue(()=>saveSampleDraft());
  if(blocked)throw Error('Reconcile the delayed sample save before continuing.');
  if(localEditErrors.size)throw Error('Correct or explicitly discard refused text before Reload or Close.');
  await persistView();
}
async function send(action,payload,binding=ownerState){
  if(!ownerState)throw Error('Create a batch first.');
  const outcome=await command(binding,action,payload);
  await refresh(true);
  if(outcome.admission==='refused'||outcome.state==='failed'){const error=Error(outcome.reason||'The command did not complete; the current batch state is shown.');error.refused=outcome.admission==='refused';throw error;}
  return outcome;
}
function canEditSample(){return datasetBinding?.kind==='live'&&!datasetBinding.closed&&(ownerState?.can_measure||ownerState?.can_run_workflow||ownerState?.context_current===false&&ownerState.phase==='idle'&&ownerState.status==='open'&&!ownerState.save_reason&&!ownerState.cleanup_reason);}
async function saveSampleDraft(token=localEditValues.get('sample')){
  if(!token||localEditValues.get('sample')!==token)return;
  clearTimeout(sampleEditTimer);
  try{
    if(sampleCompositionPending())throw Error('Finish entering the sample text before continuing.');
    if(!token.value.number_samples&&!token.value.name.trim())throw Error('Enter a sample name before continuing.');
    await waitMetadata();
    if(localEditValues.get('sample')!==token)return saveSampleDraft();
    if(token.value.batchId!==ownerState?.batch_id||token.value.draftId!==ownerState?.draft.id)throw Error('The next sample changed. Copy or discard your unsaved text before continuing.');
    if(!canEditSample())throw Error('Wait until the current operation ends before saving sample text.');
    const preview=await previewSampleDraft(token);
    if(localEditValues.get('sample')!==token)return saveSampleDraft();
    if(!preview||!previewMatchesOwner(preview))throw Error('The next sample changed. Review its name before continuing.');
    const {name,notes,number_samples}=token.value;
    if(preview.resolved_name!==ownerState.draft.name||notes!==ownerState.draft.notes||number_samples!==ownerState.draft.number_samples){
      await send('edit_draft',{draft_id:token.value.draftId,version:preview.version,name,notes,options:ownerState.draft.options,number_samples});
      if(ownerState.draft.name!==preview.resolved_name)throw Error('The resolved sample name changed. Review it before continuing.');
    }
    if(localEditValues.get('sample')===token){localEditValues.delete('sample');localEditErrors.delete('sample');}
    renderSampleControls();
  }catch(error){
    if(localEditValues.get('sample')===token)localEditErrors.set('sample',error.message);
    renderSampleControls();
    throw error;
  }
}
function sampleCompositionPending(){return ['sampleName','sampleNotes'].some(id=>$(id).dataset.composing==='true');}
function previewMatchesOwner(preview){return !!preview&&!!ownerState&&preview.batch_id===ownerState.batch_id&&preview.generation===ownerState.generation&&preview.draft_id===ownerState.draft.id&&preview.version===ownerState.draft.version;}
function previewSampleDraft(token){
  if(!token||localEditValues.get('sample')!==token)return Promise.resolve(null);
  const params={batch_id:token.value.batchId,generation:ownerState.generation,draft_id:token.value.draftId,version:ownerState.draft.version,name:token.value.name,number_samples:token.value.number_samples};
  const key=JSON.stringify(params);
  if(samplePreview?.token===token&&JSON.stringify(samplePreview.params)===key)return Promise.resolve(samplePreview.value);
  if(samplePreviewRequest?.token===token&&samplePreviewRequest.key===key)return samplePreviewRequest.promise;
  const request={token,key};
  request.promise=call('preview_draft',params).then(value=>{
    if(Object.entries(params).some(([field,expected])=>value[field]!==expected)||typeof value.resolved_name!=='string'||!value.resolved_name.trim())throw Error('Sample name preview changed. Review the next target before continuing.');
    if(localEditValues.get('sample')===token&&previewMatchesOwner(value)){samplePreview={token,params,value};samplePreviewError='';renderSampleControls();}
    return value;
  }).catch(error=>{if(localEditValues.get('sample')===token){samplePreviewError=error.message;renderSampleControls();}throw error;}).finally(()=>{if(samplePreviewRequest===request)samplePreviewRequest=null;});
  samplePreviewRequest=request;return request.promise;
}
function chartMeasurementLabel(){const cell=chartView.cells.find(item=>item.index===chartView.current_cell);return cell?'Measure patch '+cell.coordinate:'Measure chart';}
function renderSampleControls(){
  const finished=datasetBinding?.kind==='live'&&state.finished;
  $('start').hidden=finished;$('sampleActions').hidden=finished;
  $('finishedBatchActions').hidden=!finished;
  $('newBatchAfterFinish').disabled=!ownerState?.can_start_new_batch;
  $('finishedBatchMessage').textContent=!finished||ownerState?.can_start_new_batch?'Readings remain available for review and export.':'Resolve saving or recovery above before a new batch.';
  const token=localEditValues.get('sample'),name=token?.value.name??ownerState?.draft.name??'';
  const numbered=token?.value.number_samples??ownerState?.draft.number_samples??true;
  if(!ownerState)$('sampleName').value='Sample #01';
  const valid=!!name.trim()||!!numbered;
  const preview=token&&samplePreview?.token===token&&previewMatchesOwner(samplePreview.value)?samplePreview.value:null;
  const resolved=token?preview?.resolved_name:ownerState?.draft.name;
  $('sampleName').setCustomValidity(valid||!ownerState?'':'Enter a sample name.');
  $('sampleNameLabel').textContent=numbered?'Name or series':'Exact sample name';
  const hint=!ownerState?'':!valid?'Enter a sample name.':localEditErrors.get('sample')||samplePreviewError||(token&&resolved&&name!==resolved?'Next reading: '+resolved:'');
  if($('sampleHint').textContent!==hint)$('sampleHint').textContent=hint;
  $('sampleHint').hidden=!$('sampleHint').textContent;
  const workflow=selectedWorkflow(),ordinary=!workflow||workflow.workflow==='spot';
  $('start').textContent=ordinary?(resolved?'Measure '+resolved:'Measure'):(workflowLabels[workflow.workflow]||workflow.workflow);
  $('start').disabled=datasetBinding?.kind!=='live'||demoLoading||!(ordinary?ownerState?.can_measure:ownerState?.can_run_workflow)||sampleActionPending||blocked||localEditErrors.size>0||!valid||!resolved;
  $('calibrate').disabled=demoLoading||datasetBinding?.kind!=='live'||!ownerState?.can_calibrate||sampleActionPending||blocked;
  $('sampleName').disabled=$('sampleNotes').disabled=$('numberSamples').disabled=sampleActionPending||!canEditSample()&&ownerState?.phase!=='saving';
  for(const id of ['sampleName','sampleNotes'])if($(id).disabled)$(id).dataset.composing='false';
  const repeat=repeatTarget();
  $('repeat').hidden=!!chartView||!ordinary||!repeat||state.finished;
  const chartAction=!!chartView&&datasetBinding?.kind==='live';
  if(chartAction){$('start').textContent=chartMeasurementLabel();$('start').disabled=!ownerState?.can_measure_chart||sampleActionPending||blocked||demoLoading||localEditErrors.size>0;}
  $('start').title=ordinary&&!chartAction&&!resolved?'Resolve the sample name before measuring':$('start').textContent+' (Ctrl+Shift+M)';
  $('repeat').disabled=datasetBinding?.kind!=='live'||demoLoading||!ownerState?.can_measure||sampleActionPending||blocked||localEditErrors.size>0||!resolved;
  $('repeat').textContent='Repeat inspected';
  $('repeat').setAttribute('aria-label',repeat?'Repeat '+repeat.name:'Repeat inspected');
  if(equipmentEffectPending())for(const id of ['start','repeat','calibrate'])$(id).disabled=true;
  updateSetupControls();
  if(equipmentPanel?.active)equipmentPanel.renderAvailability();
  $('repeat').title=repeat?'Repeat '+repeat.name+' with blank new notes. The next sample stays '+(resolved||ownerState.draft.name)+'.':'';
}
function captureSampleEdit(){
  if(!ownerState)return;
  clearTimeout(sampleEditTimer);
  clearTimeout(samplePreviewTimer);samplePreview=null;samplePreviewError='';
  const token={value:{name:$('sampleName').value,notes:$('sampleNotes').value,number_samples:$('numberSamples').checked,batchId:ownerState.batch_id,draftId:ownerState.draft.id}};
  localEditValues.set('sample',token);localEditErrors.delete('sample');
  renderSampleControls();renderLocalEdits();
  if((token.value.name.trim()||token.value.number_samples)&&!sampleCompositionPending()){
    samplePreviewTimer=setTimeout(()=>previewSampleDraft(token).catch(()=>{}),150);
    sampleEditTimer=setTimeout(()=>enqueue(()=>saveSampleDraft(token)),400);
  }
}
async function waitMetadata(requireSaved=true){
  while(ownerState?.phase==='saving'){
    await new Promise(resolve=>setTimeout(resolve,30));await refresh();
  }
  if(requireSaved&&ownerState?.save_reason)throw Error(ownerState.save_reason);
}
function resetViews(){
  pointerPreview=null;focusPreview=null;
  lastSnapshotKey='';
  clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer);samplePreview=null;samplePreviewRequest=null;samplePreviewError='';displayedDraftId=null;sampleActionPending=false;
  state=freshState();editorTarget=null;editorPinned=false;lastViewKey='';ownerRevision='';appliedDetails.clear();localEditValues.clear();localEditErrors.clear();zoom=1;readingFilter='';$('readingFilter').value='';
  state.white=null;state.reference=null;state.previousPlotted=null;
  $('workflowChoice').replaceChildren();delete $('workflowChoice').dataset.key;workflowFieldsKey='';
  $('workflowPatchIds').value='';$('workflowControls').open=false;
  defaultLabWhiteRequested=false;
}
function datasetFence(binding=datasetBinding){return {binding_id:binding?.id??null,generation:binding?.generation??0};}
function sameDataset(fence){return fence.binding_id===datasetBinding?.id&&fence.generation===datasetBinding?.generation;}
function selectedComparison(){return datasetBinding?.comparison||null;}
function comparisonReady(){const context=selectedComparison();return !!context&&datasetBinding.available&&(context.saved||context.available===true);}
function retainedReview(){return ['saved','standalone'].includes(datasetBinding?.kind);}
function canChangeComparison(){return !!datasetBinding?.available&&datasetBinding.kind!=='empty'&&(retainedReview()||ownerState?.can_apply_annotations);}
function displayDescriptor(){return datasetBinding?.source?.descriptor||null;}
async function dataCall(method,params={},binding=datasetBinding){
  const fence=datasetFence(binding),reply=await call(method,{...params,...fence});
  if(!sameDataset(fence))throw Error('The displayed data changed. Review the current source.');
  if(reply&&Object.hasOwn(reply,'binding_id')&&(reply.binding_id!==fence.binding_id||reply.generation!==fence.generation))throw Error('Dataset reply belongs to another source.');
  return reply;
}
async function synchronise(packet,attempt=0){
  const packetKey=JSON.stringify(packet);
  if(packetKey===lastSnapshotKey)return;
  const binding=packet.dataset_binding;
  if(!binding||!['empty','live','saved','standalone'].includes(binding.kind))throw Error('Dataset selection is unavailable. Restart the current app.');
  const bindingChanged=!ownerRevision||datasetBinding?.id!==binding.id||datasetBinding?.generation!==binding.generation;
  const revision=JSON.stringify([binding.id,binding.generation,binding.roster_revision,binding.available]);
  let nextData=null;
  if(binding.kind==='empty'&&revision!==ownerRevision){
    nextData={...datasetFence(binding),readings:{},drafts:{},eligible_white_sources:[],dataset_revision:'empty',roster_revision:'empty',preview_scale:FIXTURES.preview_scale};
  }else if(binding.available&&revision!==ownerRevision){
    try{nextData=await call('dataset',datasetFence(binding));}
    catch(error){if(attempt===8)throw error;return synchronise(await call('snapshot'),attempt+1);}
    if(nextData.binding_id!==binding.id||nextData.generation!==binding.generation||nextData.roster_revision!==binding.roster_revision){
      if(attempt===8)throw Error('Data changed during refresh. Retry after pending work settles.');
      return synchronise(await call('snapshot'),attempt+1);
    }
  }
  ownerState=packet.batch;
  currentCorrectionMaterial=packet.correction_material;
  if(ownerState&&typeof ownerState.draft.number_samples!=='boolean')throw Error('Sample settings are unavailable. Restart the current app.');
  if(bindingChanged){
    state=freshState();editorTarget=null;editorPinned=false;editorRequest++;appliedDetails.clear();annotationLoads.clear();
    lastViewKey='';ownerRevision='';defaultLabWhiteRequested=false;scaleRestored=false;
    analysisController?.abort();projectionController?.abort();analysisPending=projectionPending='';analysisReply=projectionReply=null;
    projectionCache.clear();comparisonCache.clear();
  }
  datasetBinding=binding;pendingExports=packet.pending_exports||[];
  state.batch=binding.kind!=='empty';state.name=binding.kind==='standalone'?binding.source.path.split('/').at(-1):binding.source?.name||packet.name;state.notes=packet.notes;
  if(bindingChanged&&packet.view&&Object.keys(packet.view).length){
    const view=packet.view;state.plotted=new Set(view.plotted);state.previousPlotted=view.previousPlotted===null?null:new Set(view.previousPlotted);
    if(!Array.isArray(view.plotSlots)||view.plotSlots.length!==2||new Set(view.plotSlots).size!==2||view.plotSlots.some(kind=>!PAIRED_PLOTS.includes(kind)))throw Error('Plot positions are unavailable. Restart the current app.');
    state.plotSlots=[...view.plotSlots];state.plotSlotsSet=true;
    displayScale=view.displayScale;scaleRestored=true;showPreviewScale();state.follow=view.follow;state.tool=view.tool;state.inspector=view.inspector;zoom=view.zoom;
    for(const [id,value] of Object.entries(view.controls)){if(id==='plotLayout'){if(!PLOT_LAYOUTS.includes(value))throw Error('Unknown plot layout. Restart the current app.');state.plotLayout=value;continue;}const el=$(id);if(!el)continue;if(el.type==='checkbox')el.checked=value;else el.value=value;}
    expandedPlots.clear();for(const id of view.expanded)expandedPlots.add(id);
  }
  if(nextData){
    const data=nextData,selected=state.selected?.id,oldIds=new Set(state.rows.map(r=>r.id));
    const scienceChanged=FIXTURES.dataset_revision!==data.dataset_revision;
    FIXTURES.readings=data.readings;FIXTURES.eligible_white_sources=data.eligible_white_sources;FIXTURES.dataset_revision=data.dataset_revision;
    defaultScale=previewScale(data);FIXTURES.preview_scale=defaultScale;if(bindingChanged&&!scaleRestored){displayScale=defaultScale;showPreviewScale();}
    state.rows=Object.entries(data.readings).map(([source,item])=>{
      const full=appliedDetails.get(source);
      return {id:source,source,obs:item.observation,name:item.name,notes:full?.version===item.version?full.notes:item.notes_preview,
        notesLength:item.notes_length,saved:item.saved,version:item.version,readingRef:item.reading_ref,inherited:item.inherited,repeatOf:item.repeat_of,chartCell:item.chart_cell};
    });
    for(const row of state.rows)if(!oldIds.has(row.id)&&!(packet.view?.plotted&&bindingChanged))state.plotted.add(row.id);
    state.edits=data.drafts;state.selected=state.rows.find(row=>row.id===selected)||((binding.kind==='live'&&ownerState?.selected)?state.rows.find(row=>matchesReading(ownerState.selected,row))||null:null);
    if(state.follow&&state.rows.length)state.selected=latestReadingRow();
    DEFAULT_PROJECTION={white:null,scale:defaultScale,reference_gamuts:FIXTURES.reference_gamuts,
      display_previews:Object.fromEntries(Object.entries(data.readings).map(([key,row])=>[key,row.display_previews])),lab_by_source:{},color_reasons:{}};
    if(scienceChanged){analysisController?.abort();projectionController?.abort();analysisPending=projectionPending='';analysisReply=projectionReply=null;}
    ownerRevision=revision;
  }
  if(!binding.available){
    // A changed source is diagnostic, never a reusable set of measured values.
    state.rows=[];state.selected=null;state.plotted.clear();state.edits={};
    FIXTURES.readings={};FIXTURES.eligible_white_sources=[];FIXTURES.dataset_revision='unavailable';
    analysisController?.abort();projectionController?.abort();analysisReply=projectionReply=null;analysisPending=projectionPending='';
  }
  const comparison=selectedComparison();
  state.reference=state.rows.find(row=>matchesReading(comparison?.reference,row))?.id||null;
  state.white=comparison?.white||null;
  if(bindingChanged&&state.white){
    state.whiteChoice=state.white.kind==='theoretical-lab-reference'?'standard:'+state.white.name:'reading:'+state.rows.find(row=>matchesReading(state.white,row))?.id;
    if(state.white.kind==='theoretical-lab-reference')$('whiteY').value=state.white.reference_y;
  }
  state.destination=binding.source?.path||binding.source?.folder||'';state.finished=binding.kind!=='empty'&&binding.read_only;
  state.busy=ownerState&&['starting','active','saving'].includes(ownerState.phase)?ownerState.phase:null;
  state.unsaved=!!ownerState&&(!!ownerState.cleanup_reason||!!ownerState.save_reason&&ownerState.phase!=='saving');
  state.outcomes=binding.kind==='live'&&ownerState?ownerState.operations.map(item=>({...item,kind:item.workflow==='calibrate'?'Calibration':item.workflow==='spot'?'Measurement':item.workflow})):[];
  if(bindingChanged){chartColorKey='';chartColors=null;chartColorBasis=null;expectedChartContext=null;chartColorError='';chartViewedPass=null;chartView=null;chartViewKey='';chartRequest++;chartSetupPacket=null;chartSourcePacket=null;}
  if(binding.available&&binding.kind==='live'&&ownerState?.chart){if(chartViewedPass===null||chartViewedPass===ownerState.chart.active_pass){const inspected=chartView?.viewed_pass===ownerState.chart.viewed_pass?chartView.inspection_cell:null;chartView=ownerState.chart;if(chartView.finished&&inspected!==null)chartView={...chartView,inspection_cell:inspected};chartViewKey=JSON.stringify([datasetFence(),chartView.revision]);}else loadSelectedChart(chartViewedPass);}
  else if(binding.kind==='saved'&&binding.available)loadSelectedChart(chartViewedPass);else {chartRequest++;chartViewKey='';chartView=null;chartColors=null;chartColorKey='';}
  render();if(state.follow)scrollSelected();lastSnapshotKey=packetKey;
}

function renderNativeDiagnosticWarnings(){
  const failures=ownerState?.native_diagnostic_failures;
  const groups=[['Log capture needs attention',failures?.retention||{}],['Log save needs attention',failures?.publication||{}]];
  const affected=new Set(groups.flatMap(([,entries])=>Object.keys(entries)));
  const notice=$('nativeDiagnosticNotice'),details=$('nativeDiagnosticFailures');
  notice.hidden=!affected.size;
  const summary=affected.size?` · ${affected.size} operation${affected.size===1?'':'s'} · Live batch: ${ownerState.batch_name||'Current batch'}`:'';
  if($('nativeDiagnosticSummary').textContent!==summary)$('nativeDiagnosticSummary').textContent=summary;
  const key=JSON.stringify([ownerState?.batch_id,ownerState?.generation,groups]);
  if(details.dataset.key===key)return;
  details.dataset.key=key;details.replaceChildren();
  for(const [label,entries] of groups){
    if(!Object.keys(entries).length)continue;
    const heading=document.createElement('h3');heading.textContent=label;
    const list=document.createElement('ul');
    for(const [command,reason] of Object.entries(entries)){
      const index=ownerState.operations.findIndex(operation=>operation.command_id===command);
      const item=document.createElement('li');item.textContent=`${index>=0?'Operation #'+(index+1):'Retained operation'}: ${reason}`;list.append(item);
    }
    details.append(heading,list);
  }
}
function renderOwnerStatus(){
  renderNativeDiagnosticWarnings();
  if(retainedReview()){$('sampleName').disabled=$('sampleNotes').disabled=true;$('requestState').textContent='';$('operationStatus').hidden=false;$('operationStatus').textContent='Read only · return to live to measure';return;}
  if(!ownerState){$('sampleName').disabled=$('sampleNotes').disabled=true;$('operationStatus').textContent=datasetBinding?.kind==='saved'?'Saved data · read only':'Create a batch to begin';return;}
  $('sampleName').disabled=$('sampleNotes').disabled=!ownerState.can_measure&&ownerState.phase!=='saving';
  for(const [id,value] of [['sampleName',ownerState.draft.name],['sampleNotes',ownerState.draft.notes]])
    if(!localEditValues.has('sample')&&(displayedDraftId!==ownerState.draft.id||document.activeElement!==$(id)))$(id).value=value;
  if(!localEditValues.has('sample'))$('numberSamples').checked=ownerState.draft.number_samples;
  displayedDraftId=ownerState.draft.id;
  const phase=ownerState.phase;
  const operationStatus=datasetBinding?.closed?'Batch closed · read only':datasetBinding?.kind==='saved'?'Saved data · return to live to measure':ownerState.context_current===false&&!state.finished?ownerState.context_reason:ownerState.cleanup_reason?'Operation cleanup needs attention. See recovery details above.':
    (phase==='saving'?'Saving…':ownerState.save_reason?'Save failed. Use the recovery controls above.':
    ownerState.cancel_requested&&['starting','active'].includes(phase)?'Cancellation requested…':
    phase==='starting'?'Preparing operation…':phase==='active'?ownerState.instructions?'Waiting for you…':ownerState.operations.find(item=>item.operation_id===ownerState.operation_id)?.workflow==='calibrate'?'Calibrating…':'Measuring…':state.finished?'Batch finished':state.batch?'Ready':'Create a batch to begin');
  $('operationStatus').hidden=operationStatus==='Ready';
  if($('operationStatus').textContent!==operationStatus)$('operationStatus').textContent=operationStatus;
  $('requestState').textContent=phase==='saving'?'Saving sample information…':'';
  const durability=ownerState.durability;
  const synchronization={synchronized:'Filesystem synchronization confirmed',limited:'Filesystem synchronization limited',unconfirmed:'Filesystem synchronization unconfirmed'}[durability.status];
  $('durabilityState').textContent=ownerState.save_reason?'Current save unconfirmed; prior file receipts retained':synchronization;
  $('durabilityDetails').textContent=durability.files.map(item=>(item.path.startsWith(state.destination+'/')?'Current batch: ':'Earlier publication attempt: ')+item.path+'\nFile sync: '+item.file_sync+' · directory sync: '+(item.directory_synced?'confirmed':'unconfirmed')+'\n'+item.limitations.join('; ')).join('\n\n');
}
let refreshPromise=null;
async function refresh(force=false){
  if(equipmentStartup)await equipmentStartup;
  if(equipmentPanel?.setupPending){await equipmentPanel.checkSetup();if(equipmentPanel.setupPending){renderSampleControls();return;}}
  if(refreshPromise){await refreshPromise;if(!force)return;}
  if(refreshPromise)return refreshPromise;
  refreshPromise=call('snapshot').then(packet=>synchronise(packet)).finally(()=>{refreshPromise=null;});
  return refreshPromise;
}
window.requestOwnerClose=async()=>{try{if(demoLoadId)await call('stop_demo_preload',{load_id:demoLoadId});await reconcileWrites();await call('request_close');}catch(error){toast(error.message);renderLocalEdits();await call('cancel_close').catch(()=>{});}};
window.reloadOwner=async()=>{try{await reconcileWrites();location.reload();}catch(error){toast(error.message);renderLocalEdits();}};
for(const id of ['sampleName','sampleNotes']){
  $(id).oninput=captureSampleEdit;
  $(id).onblur=()=>{if($(id).dataset.composing==='true'){$(id).dataset.composing='false';captureSampleEdit();}const token=localEditValues.get('sample');if(!sampleActionPending&&(token?.value.name.trim()||token?.value.number_samples)&&$(id).dataset.composing!=='true')enqueue(()=>saveSampleDraft(token));};
  $(id).oncompositionstart=()=>{$(id).dataset.composing='true';clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer);};
  $(id).oncompositionend=()=>{$(id).dataset.composing='false';captureSampleEdit();};
}
$('numberSamples').onchange=captureSampleEdit;


let toastTimer;
let zoom = 1;
let spaceView = null;
let spaceError = null;
let spaceContextLost = false;
let panelInvoker = {};
let analysisReply = null;
let projectionReply = null;
// The owner names one common preview scale; every bound dataset supplies it.
function previewScale(data){const scale=data?.preview_scale;if(typeof scale!=='number'||!Number.isFinite(scale)||scale<=0)throw Error('Preview scale is unavailable. Restart the current app.');return scale;}
let defaultScale = previewScale(FIXTURES);
let displayScale = defaultScale;
let scaleRestored = false;
function showPreviewScale(){$('previewScale').value=String(displayScale);}
showPreviewScale();
let analysisPending = '';
let analysisController = null;
let readingFilter = '';
let chromaticityFieldKey='';
let heldChromaticityField=null;
const expandedPlots = new Set();
let resizeFrame=null;
const layoutSizes=new Map();
const plotLayoutResize=new ResizeObserver(entries=>{
  const changed=entries.some(entry=>{
    const key=entry.contentRect.width+'/'+entry.contentRect.height,previous=layoutSizes.get(entry.target);
    layoutSizes.set(entry.target,key);return previous!==key;
  });
  if(changed&&resizeFrame===null)resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;render();});
});
for(const id of ['spectrumPlot','chromaticityPlot','spacePlot','residualPlot'])plotLayoutResize.observe($(id));
window.addEventListener('resize',()=>{if(resizeFrame===null)resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;render();});});
const TOOLS = ['measure','compare','color','data'];
let DEFAULT_PROJECTION={white:null,scale:defaultScale,reference_gamuts:FIXTURES.reference_gamuts,display_previews:Object.fromEntries(Object.entries(FIXTURES.readings).map(([key,r])=>[key,r.display_previews])),lab_by_source:{},color_reasons:{}};
const ICON_PATHS = {expand:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',restore:'M8 8H3m5 0V3m8 5h5m-5 0V3M8 16H3m5 0v5m8-5h5m-5 0v5',reset:'M4 7v5h5M4 12a8 8 0 1 0 2-6',help:'M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 4h.01',settings:'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',edit:'m4 16 12-12 4 4-12 12H4zM14 6l4 4',close:'m6 6 12 12M6 18 18 6',check:'m5 12 4 4L19 6',pending:'M12 8v4l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18',attention:'m12 3 10 18H2zM12 9v5m0 3h.01',folder:'M3 7V5h6l2 2h10v13H3z',layout:'M3 4h18v16H3zM3 10h18M12 10v10',lock:'M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5z'};
function icon(name){return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${ICON_PATHS[name]}"/></svg>`;}
function paintIcons(){document.querySelectorAll('[data-icon]').forEach(button=>{button.innerHTML=icon(button.dataset.icon)+(button.dataset.iconText?`<span>${esc(button.dataset.iconText)}</span>`:'');});}
paintIcons();
let projectionController=null,projectionPending='';
const projectionCache=new Map(),comparisonCache=new Map();
function boundedCache(cache,key,value){cache.delete(key);cache.set(key,value);if(cache.size>8)cache.delete(cache.keys().next().value);}
function projectionKey(){return JSON.stringify([datasetFence(),FIXTURES.dataset_revision,selectedComparison()?.version,displayScale]);}
function currentProjection(){
  if(!comparisonReady())return null;
  if(projectionReply?.key===projectionKey())return projectionReply;
  if(displayScale===defaultScale&&!state.white)return DEFAULT_PROJECTION;
  return null;
}
function requestProjection(){
  if(!comparisonReady()){projectionController?.abort();projectionPending='';projectionReply=null;return;}
  if(currentProjection())return;
  const key=projectionKey(),white=state.white,scale=displayScale,comparison_version=selectedComparison().version,reference=referenceReading()?.source||null;
  if(projectionCache.has(key)){projectionReply=projectionCache.get(key);return;}
  if(projectionPending===key)return;
  projectionController?.abort();projectionController=new AbortController();const controller=projectionController;projectionPending=key;
  dataCall('projection',{comparison_version,scale,dataset_revision:FIXTURES.dataset_revision}).then(data=>{
    if(controller.signal.aborted)return;
    if(data.source_commit!==FIXTURES.source_commit||data.dataset_revision!==FIXTURES.dataset_revision||data.comparison_version!==comparison_version||data.reference!==reference||!sameWhite(data.white,white)||data.scale!==scale)throw Error('Projection identity mismatch.');
    projectionReply={key,...data};boundedCache(projectionCache,key,projectionReply);projectionPending='';render();
  }).catch(error=>{if(controller.signal.aborted)return;projectionReply={key,error:error.message};projectionPending='';render();});
}
function excessText(status){if(!status)return 'Unavailable';const lower=status.max_lower_excess||0,upper=status.max_upper_excess||0;if(!lower&&!upper)return 'No excess';return `${lower?'−'+numberText(lower):'0'} / ${upper?'+'+numberText(upper):'0'}`;}
function matchedRows(){
  const mode=$('gamutFilter').value;
  let rows=state.rows.filter(r=>(!readingFilter||(rowName(r)+' '+r.notes+' '+r.id).toLocaleLowerCase().includes(readingFilter))&&(()=>{const status=referenceStatus(r);return mode==='all'||mode==='unavailable'&&!status||mode==='chromaticity'&&status?.numerical_state==='outside'||mode==='upper'&&status?.volume_numerical_state==='outside'&&(status?.max_upper_excess||0)>0||mode==='boundary'&&(status?.numerical_state==='on-boundary'||status?.volume_numerical_state==='on-boundary');})());
  const order=$('readingSort').value;if(order!=='acquired')rows.sort((a,b)=>((referenceStatus(b)?.[order==='lower'?'max_lower_excess':'max_upper_excess']||0)-(referenceStatus(a)?.[order==='lower'?'max_lower_excess':'max_upper_excess']||0))||state.rows.indexOf(a)-state.rows.indexOf(b));return rows;
}
function niceStep(span,target=5){const raw=span/target,power=10**Math.floor(Math.log10(raw||1)),ratio=raw/power;return [1,2,2.5,5,10].find(v=>v>=ratio)*power;}
function ticks(low,high,target=5){const step=niceStep(high-low,target),out=[];for(let v=Math.ceil(low/step)*step;v<=high+step*1e-9;v+=step)out.push(Math.abs(v)<step*1e-9?0:v);return out;}
function tickText(value,step){const places=Math.max(0,-Math.floor(Math.log10(step))+([2.5].includes(step/10**Math.floor(Math.log10(step)))?1:0));return value.toFixed(Math.min(6,places));}

const plotTitles = {spectrumPanel:'spectrum',chromaticityPanel:'chromaticity',spacePanel:'3D',chartPanel:'chart guide',residualPanel:'residuals'};
const p3Supported=CSS.supports('color','color(display-p3 1 0 0)');
$('previewGamut').querySelector('[value="display-p3"]').disabled=!p3Supported;
if(!p3Supported)$('previewGamut').value='srgb';
const panelVersions=new Map(),toastMessages=new Set();
function editingPanel(){return activePanel==='setupDialog'||activePanel==='annotationDialog';}
const panelErrors={setupDialog:'setupError',equipmentDialog:'equipmentError',annotationDialog:'annotationError',exportDialog:'exportError',recordDialog:'recordError',chartSetupDialog:'chartSetupError',finishChartDialog:'finishChartError',chartHistoryDialog:'chartHistoryError',diagnosticsDialog:'diagnosticsError',positionReferenceDialog:'positionReferenceError'};
function actionErrorContext(id){return {id,epoch:panelEpoch,version:panelVersions.get(id),element:$(panelErrors[id]||''),target:id==='annotationDialog'?editorTarget:null};}
function showStatusError(error,context=null){
  const targetMatches=!context?.target||context.target===editorTarget&&context.target.batchId===ownerState?.batch_id;
  const visible=context?.element?.isConnected&&targetMatches&&context.epoch===panelEpoch&&(
    activePanel===context.id&&panelVersions.get(context.id)===context.version||
    context.id==='annotationDialog'&&!$('inspector').hidden&&$('details').contains(context.element));
  if(visible){context.element.textContent=error.message;context.element.hidden=false;return;}
  toast(error.message);
}
function toast(message) { toastMessages.add(message);$('toast').textContent=[...toastMessages].join('\n');$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').hidden=true;toastMessages.clear();},4500); }
const topMenus=[...document.querySelectorAll('.compact-menu')].filter(menu=>!menu.parentElement.closest('.compact-menu'));
function closeMenus(){for(const menu of topMenus)menu.open=false;}
$('closeView').onclick=()=>{const menu=$('closeView').closest('details');menu.open=false;menu.querySelector('summary').focus();};
for(const menu of topMenus)menu.querySelector(':scope > summary').addEventListener('click',()=>{
  for(const other of topMenus)if(other!==menu)other.open=false;
});
document.addEventListener('click',event=>{
  if(!event.target.closest('.compact-menu'))closeMenus();
},true);
function showPanel(id, invoker) {
  closeMenus();
  const prior=invoker||document.activeElement;
  if(activePanel&&activePanel!==id)closePanel(activePanel);
  clearTimeout(toastTimer);$('toast').hidden=true;
  panelEpoch++;panelVersions.set(id,(panelVersions.get(id)||0)+1);
  const error=$(panelErrors[id]||'');
  if(error){error.textContent='';error.hidden=true;}
  panelInvoker[id]=prior;activePanel=id;$(id).hidden=false;
  ($(id).querySelector('button:not([disabled]),input:not([disabled]),textarea:not([disabled])')||$(id)).focus();
}
function closePanel(id) {
  if($(id).hidden)return;
  if(id==='equipmentDialog')equipmentPanel.close();
  if(id==='scienceDialog')sciencePanel.close();
  if(id==='activityDialog')activityPanel.close();
  panelEpoch++;$(id).hidden=true;if(activePanel===id)activePanel=null;
  if(id==='annotationDialog'){
    editorPinned=false;
    const editor=$('annotationBody').querySelector('.annotation-editor');
    if(editor)$('details').insertBefore(editor,$('detailMetadata'));
  }
  render();renderLocalEdits();
  if(id==='annotationDialog')persistView();
  const prior=panelInvoker[id],target=prior?.id?$(prior.id):prior;
  const candidates=[target,state.selected&&$('edit-row-'+state.selected.id),state.selected&&$('row-'+state.selected.id),$('workspace')];
  candidates.find(node=>node?.isConnected&&!node.disabled&&!node.closest('[hidden]'))?.focus({preventScroll:false});
}
function rowName(r) { return '#' + (state.rows.indexOf(r)+1) + ' · ' + r.name; }
function previewCss(rgb,target=$('previewGamut').value) { return target==='display-p3'?`color(display-p3 ${rgb.join(' ')})`:`rgb(${rgb.map(v=>(v*100)+'%').join(' ')})`; }
function color(r) { const preview=displayPreview(r,$('previewGamut').value);return preview?.display_rgb?previewCss(preview.display_rgb):'var(--muted)'; }
function curveColor(r) { const base=color(r),mix=`color-mix(in xyz-d65, ${base} 72%, white)`;return $('curveContrast').checked&&CSS.supports('color',mix)?mix:base; }
function plottedRows() { return state.rows.filter(r=>state.plotted.has(r.id)); }
function comparisonActive() { return !!state.reference||state.tool==='compare'||state.plotLayout==='residual'; }
function analysisSources() { return comparisonActive()?state.rows.map(r=>r.source):[]; }
const PAIRED_PLOTS=['spectrum','chromaticity','space','chart'];
const PLOT_LAYOUTS=['overview',...PAIRED_PLOTS,'residual','table'];
function expectedSpectrumAvailable(descriptor,mode,options={}){
  if(descriptor?.spectral===false||options.xyz_only===true)return false;
  const quantities=descriptor?.modes.find(entry=>entry.mode===mode)?.expected_quantities;
  return quantities&&Object.hasOwn(quantities,'spectrum_unit')?!!quantities.spectrum_unit:null;
}
function spectrumAvailable(){
  if(state.rows.length)return state.rows.some(row=>row.obs.backend_reported.spectrum);
  return expectedSpectrumAvailable(displayDescriptor(),datasetBinding?.source?.mode,datasetBinding?.kind==='live'?ownerState?.draft.options||{}:{});
}
function setPlotSlot(index,kind){
  const other=1-index,previous=state.plotSlots[index],single=state.plotLayout!=='overview'&&state.plotLayout!=='table';
  if(state.plotSlots[other]===kind)state.plotSlots[other]=previous;
  state.plotSlots[index]=kind;state.plotSlotsSet=true;
  state.plotLayout=single?kind:'overview';expandedPlots.clear();
}
function visiblePlotIds(){
  if(state.plotLayout==='table')return [];
  if(expandedPlots.size)return [[...expandedPlots][0]];
  return state.plotLayout==='overview'?state.plotSlots.slice(0,innerWidth<=900?1:2).map(kind=>kind+'Panel'):[state.plotLayout+'Panel'];
}
function setPlotLayout(layout){
  state.plotLayout=layout==='single'?state.plotSlots[0]:layout;
  state.plotSlotsSet=true;expandedPlots.clear();render();$('plotLayout').focus();
}
$('plotLayout').onchange=()=>setPlotLayout($('plotLayout').value);
for(const choice of document.querySelectorAll('[data-plot-choice]'))choice.onchange=()=>{
  const current=choice.dataset.plotChoice,kind=choice.value,index=innerWidth<=900||state.plotLayout!=='overview'?0:Math.max(0,state.plotSlots.indexOf(current));
  if(kind==='residual'){state.plotLayout='residual';expandedPlots.clear();}
  else setPlotSlot(index,kind);
  render();$(kind+'PlotChoice').focus();
};
function updatePlotLayout() {
  document.querySelectorAll('[data-expand]').forEach(button=>{const id=button.dataset.expand,expanded=expandedPlots.has(id);$(id).classList.toggle('expanded-plot',expanded);button.setAttribute('aria-expanded',String(expanded));button.innerHTML=icon(expanded?'restore':'expand');const label=(expanded?'Restore size: ':'Expand ')+plotTitles[id];button.setAttribute('aria-label',label);button.title=label;});
}
function referenceReading() { return state.rows.find(r=>r.id===state.reference)||null; }
function matchesReading(identity,row){const actual=readingIdentity(row);return !!identity&&!!actual&&['operation_id','observation_id','segment_id','source_manifest_sha256'].every(key=>identity[key]===actual[key]);}
function readingIdentity(row){return row.readingRef;}
function sameWhite(value,expected){return value===null?expected===null:!!value&&!!expected&&Object.keys(value).length===Object.keys(expected).length&&Object.entries(expected).every(([key,item])=>value[key]===item);}
async function sendComparison(changes,fence=datasetFence()){
  if(datasetBinding?.kind==='live')await waitMetadata();
  if(!sameDataset(fence)||!canChangeComparison())throw Error('The data changed. Review comparison settings before continuing.');
  const context=selectedComparison(),params={...fence,command_id:crypto.randomUUID(),comparison_version:context.version,reference:context.reference,white:context.white,...changes};
  let outcome;
  try{outcome=await call('set_analysis_context',params);}
  catch(error){try{outcome=await call('set_analysis_context',params);}catch(error){error.commandParams=params;error.commandMethod='set_analysis_context';throw error;}}
  await refresh(true);
  if(outcome.admission!=='admitted'||outcome.state==='failed')throw Error(outcome.reason||'Comparison settings were refused.');
}
function changeComparison(changes){const fence=datasetFence();return enqueue(()=>sendComparison(changes,fence));}
function xyzEntry(r){return currentAnalysis()?.xyz_comparison_by_source?.[r.source];}
function xyzValue(r,key){const entry=xyzEntry(r),value=entry?.[key];return r===referenceReading()?'Reference':typeof value==='number'?(key==='y_percent'&&value>0?'+':'')+numberText(value):'Unavailable';}
function xyzReason(r,key){const entry=xyzEntry(r);return entry?(entry[key]||(key==='y_reason'?'Signed native Y change relative to the saved reference.':'CIE 1976 coordinate distance to the saved reference; dimensionless.')):currentAnalysis()?.error||(!state.reference?'Choose a reference reading.':'Waiting for the current comparison.');}
const signedMetrics=[
  {metric:'delta_xyz',labels:['ΔX','ΔY','ΔZ'],keys:['deltaX','deltaY','deltaZ'],reason:'xyz_reason',nativeUnit:true},
  {metric:'delta_xy',labels:['Δx','Δy'],keys:['deltaSmallX','deltaSmallY'],reason:'xy_reason'},
  {metric:'delta_uv',labels:['Δu′','Δv′'],keys:['deltaU','deltaV'],reason:'uv_reason'},
  {metric:'delta_lab',labels:['ΔL*','Δa*','Δb*'],keys:['deltaL','deltaA','deltaB'],reason:'delta_lab_reason',requiresWhite:true},
  {metric:'delta_chroma',labels:['ΔC*'],keys:['deltaC'],reason:'chroma_reason',requiresWhite:true},
  {metric:'delta_hue',labels:['ΔH*'],keys:['deltaH'],reason:'hue_reason',requiresWhite:true},
];
function colorComparisonEntry(row){return currentAnalysis()?.color_comparison_by_source?.[row.source];}
function signedValue(value){return typeof value==='number'?(value>0?'+':'')+numberText(value):'Unavailable';}
function signedComparisonCells(row){
  const entry=colorComparisonEntry(row),values={},reasons={};
  for(const group of signedMetrics)for(const [index,key] of group.keys.entries()){
    values[key]=signedValue(group.keys.length===1?entry?.[group.metric]:entry?.[group.metric]?.[index]);
    reasons[key]=entry?.[group.reason]||currentAnalysis()?.error||(entry?'Reading minus reference · '+(group.nativeUnit?unitText(entry.xyz_unit):'dimensionless'):'Waiting for the current comparison.');
  }
  return {values,reasons};
}
function comparisonSummaryText(){
  const analysis=currentAnalysis(),reference=referenceReading();
  if(!datasetBinding?.available||!reference||!analysis||analysis.error||analysis.key!==analysisKey())throw Error(analysis?.error||'Wait for the current comparison before copying.');
  const source=datasetBinding.source,summary=analysis.summary,sourceLines=datasetBinding.kind==='standalone'?[`Record: ${state.name}`,`Record format: ${source.format}`,`Record source provenance: ${source.provenance}`,`Original record kind: ${source.record_kind}`,`Source SHA-256: ${source.source_sha256}`,...(source.operation_status?[`Original operation status: ${source.operation_status}`,`Original operation reason: ${source.operation_reason??'None'}`]:[])]:[`Batch: ${source.name}`,`Batch source provenance: ${source.descriptor.provenance??'Unavailable'}`],lines=['Measurement comparison',...sourceLines,`Reference: ${rowName(reference)}`,`White: ${whiteLabel()}`,`Compared readings: ${analysis.selected.filter(key=>key!==reference.source).length}`,'Signed differences: reading minus reference'];
  const addRange=(label,range,unavailable)=>{
    if(typeof unavailable!=='number')throw Error('Summary availability is incomplete. Refresh the comparison.');
    lines.push(range?`${label}: ${range.min} to ${range.max}${range.unit?' '+unitText(range.unit):''} (${range.count} reading${range.count===1?'':'s'}${unavailable?', '+unavailable+' unavailable':''})`:`${label}: ${unavailable?'unavailable ('+unavailable+' reading'+(unavailable===1?'':'s')+')':'no comparison readings'}`);
  };
  addRange('Y change (%)',summary.y_percent_range,summary.y_unavailable);
  addRange('Δu′v′ distance',summary.uv_distance_range,summary.uv_unavailable);
  addRange('ΔE00',summary.delta_range,summary.color_unavailable);
  addRange('Spectral RMS',summary.rms_range,summary.spectral_unavailable);
  for(const group of signedMetrics)for(const [index,label] of group.labels.entries()){
    const range=summary[group.metric+'_range'];addRange(label,group.labels.length===1?range:range?.[index],summary[group.metric+'_unavailable']);
  }
  const context=analysis.white_descriptor;
  if(context)lines.push('Declared white context: '+JSON.stringify(context));
  const reasons=new Map();
  const addReason=(label,reason,key)=>{if(!reason)return;const identity=JSON.stringify([label,reason]);if(!reasons.has(identity))reasons.set(identity,{label,reason,records:new Set()});reasons.get(identity).records.add(key);};
  for(const key of analysis.selected.filter(value=>value!==reference.source)){
    const entry=analysis.color_comparison_by_source[key];
    for(const group of signedMetrics)addReason(group.labels.join(' / '),entry[group.reason],key);
    addReason('ΔE00',entry.delta_e2000_reason,key);
    addReason('Spectral RMS',analysis.comparison_by_source[key]?.reason,key);
    addReason('Y change (%)',analysis.xyz_comparison_by_source[key].y_reason,key);
    addReason('Δu′v′ distance',analysis.xyz_comparison_by_source[key].uv_reason,key);
  }
  for(const item of reasons.values())lines.push(`${item.label}: ${item.records.size} unavailable (${item.reason})`);
  const pair=analysis.color_comparison_by_source[reference.source];
  if(pair)lines.push('Pair context: '+JSON.stringify({unit:pair.xyz_unit,scale:pair.xyz_scale,observer:pair.observer,illuminant:pair.illuminant,algorithm:pair.algorithm,algorithm_version:pair.algorithm_version}));
  lines.push('Source revision: '+analysis.dataset_revision);
  return lines.join('\n');
}
async function copyComparisonSummary(){
  try{const copied=await call('copy_summary',{text:comparisonSummaryText()});if(copied?.copied)toast('Comparison summary copied.');}
  catch(error){showStatusError(error);}
}
function comparisonRange(label,range,unavailable){return range?`<div class="metric">${label}<strong>${range.min===range.max?numberText(range.max):numberText(range.min)+' – '+numberText(range.max)}</strong><span>${range.unit?esc(unitText(range.unit))+' · ':''}${range.count} reading${range.count===1?'':'s'}${unavailable?' · '+unavailable+' unavailable':''}</span></div>`:unavailable?`<div class="unavailable"><strong>${label} unavailable</strong><span>${unavailable} reading${unavailable===1?'':'s'} · see each table entry for the reason.</span></div>`:'';}
function compareEntry(r) { return currentAnalysis()?.comparison_by_source?.[r.source]; }
function currentAnalysis() { const projection=currentProjection();if(!projection)return null;if(projection.error)return projection;const reply=analysisReply?.key===analysisKey()?analysisReply:null;if(comparisonActive())return reply?{...projection,...reply}:null;return {...projection,comparison_by_source:{},delta_e_by_source:{},summary:{},key:analysisKey()}; }
function analysisKey() { return JSON.stringify([datasetFence(),FIXTURES.dataset_revision,analysisSources(),selectedComparison()?.version,displayScale]); }
function updateReference() { if(!state.rows.some(r=>r.id===state.reference))state.reference=null; }

function rowSavingState(r) { return r.saved?'Saved':ownerState?.phase==='saving'?'Saving…':'Not saved'; }
function targetName(target) { return FIXTURES.reference_gamuts[target]?.name || target; }
function referenceTargets() {
  return [...new Set([$('referenceGamut').value,$('comparisonGamut').value])].filter(target=>target!=='none');
}
function displayPreview(r,target){return currentProjection()?.display_previews?.[r.source]?.[target];}
function previewContextText(preview){
  if(!preview?.display_rgb)return preview?.reason||currentProjection()?.error||'Calculating current projections';
  if(preview.reference_unit==='1')return `Relative ${preview.source_white.name} · common white Y=${numberText(preview.common_reference_y)} · ${observerText(preview.native_context.observer)} · Bradford to D65`;
  return `${numberText(preview.reference_luminance)} ${unitText(preview.reference_unit)} · direct D65 target · no adaptation`;
}
function referenceGeometry(target,lab=false){return currentProjection()?.reference_gamuts?.[target]||(!ownerState&&!lab?FIXTURES.reference_gamuts[target]:null);}

function referenceStatus(r) {
  const status=displayPreview(r,$('referenceGamut').value)?.gamut_status;
  return status?{...status,volume_numerical_state:status.numerical_state,...status.chromaticity[$('coordinates').value]}:null;
}
function warningDiamond(x,y,r,volume=false) {
  const status=referenceStatus(r);
  if(!$('gamutWarnings').checked||!(volume?status?.volume_numerical_state==='outside':status?.numerical_state==='outside'))return '';
  return `<path class="reference-halo" d="M${x} ${y-10}L${x+10} ${y}L${x} ${y+10}L${x-10} ${y}Z"/><path class="gamut-alert" d="M${x} ${y-10}L${x+10} ${y}L${x} ${y+10}L${x-10} ${y}Z"/>`;
}
function statusText(status) {
  if(!status)return 'Reference comparison unavailable';
  const state=status.numerical_state,volume=status.volume_numerical_state;
  const chromaticity=state===null||state===undefined?'Triangle comparison unavailable':state==='outside'?'Outside chromaticity':state==='on-boundary'?'On chromaticity boundary':'Inside chromaticity';
  const range=volume==='outside'?'Outside RGB channel range':volume==='on-boundary'?'On RGB boundary':'Inside RGB range';return chromaticity+' · '+range;
}
function statusLabel(status){if(!status)return 'Unavailable';if(status.numerical_state==='outside')return 'Outside chromaticity';if(status.volume_numerical_state==='outside')return status.max_upper_excess>0?'Above channel range':'Outside RGB range';if(status.numerical_state==='on-boundary'||status.volume_numerical_state==='on-boundary')return 'On numeric boundary';if(status.numerical_state==null)return 'Unavailable';return 'Inside';}

function gamutDiagnostics(r) {
  const target=$('referenceGamut').value, preview=displayPreview(r,target), status=referenceStatus(r);
  if(!status)return '<section class="gamut-diagnostics"><h3>Reference gamut</h3><p class="small">Reference diagnostics unavailable or no reference selected.</p></section>';
  const channels=indices=>indices.map(i=>['R','G','B'][i]).join(', ');
  return `<section class="gamut-diagnostics" aria-label="Reference gamut diagnostics"><h3>${esc(targetName(target))} reference</h3><p class="small ${status.volume_numerical_state==='outside'?'gamut-warning':''}">${statusText(status)}</p><p class="small">Max signed channel excess below 0 / above 1: ${excessText(status)} (unit 1)<br>Numerical boundary: ${esc(status.volume_numerical_state)} (arithmetic only)</p><p class="small">Signed linear RGB:<br><code>${preview.linear_rgb.map(v=>numberText(v,7)).join(' / ')}</code></p><p class="small">Raw below 0: ${channels(status.below_zero)||'none'}<br>Raw above 1: ${channels(status.above_one)||'none'}</p><p class="small muted">Above 1 may reflect brightness beyond the chosen scale even when chromaticity is inside. Exact boundary comparisons can flag numerical rounding. Native XYZ is unchanged.</p></section>`;
}
function renderGamut() {
  const primary=$('referenceGamut').value, comparison=$('comparisonGamut').value;
  $('gamutLegend').innerHTML=referenceTargets().map(target=>`<span><i class="gamut-key ${target!==primary?'comparison':''}"></i>${esc(targetName(target))}${target===primary?' reference':' comparison'}</span>`).join('')+($('showLocus').checked?'<span><i class="gamut-key locus"></i>Spectral locus</span><span><i class="gamut-key purples"></i>Line of purples</span>':'')+(primary!=='none'&&$('gamutWarnings').checked?'<span class="gamut-warning">◇ outside reference · triangle in 2D / volume in 3D</span>':'');
  const statuses=state.rows.map(referenceStatus), eligible=statuses.filter(Boolean), outside=eligible.filter(s=>s.numerical_state==='outside').length, undefinedCount=eligible.filter(s=>s.numerical_state==null).length, volume=eligible.filter(s=>s.volume_numerical_state==='outside').length;
  $('gamutWarnings').disabled=primary==='none';
  $('gamutSummary').textContent=primary==='none'?'No primary reference or membership warning. Swatch encoding remains independent.':`All retained readings · ${targetName(primary)}: ${outside}/${eligible.length} outside chromaticity triangle; ${volume}/${eligible.length} outside bounded RGB volume at ${numberText(displayScale)} cd/m²${undefinedCount?`; ${undefinedCount} triangle comparisons unavailable`:''}${statuses.length!==eligible.length?`; ${statuses.length-eligible.length} unavailable`:''}. Arithmetic boundary states are not counted as exclusion. These are numerical reference comparisons, not measured display coverage.${comparison===primary?' Same comparison is drawn once.':''}`;
  $('gamutHeading').innerHTML=esc(primary==='none'?'Reference gamut':targetName(primary)+' reference')+'<br><span class="gamut-excess">Excess below 0 / above 1</span>';
  $('gamutHeading').title='Numerical chromaticity / RGB range; excess below 0 / above 1 in dimensionless linear RGB.';
}
function swatch(r,prefix) {
  const target=$('previewGamut').value,name=targetName(target),preview=displayPreview(r,target),status=preview?.gamut_status;
  if(!preview?.display_rgb)return `<span class="swatch-unavailable" title="${esc(preview?.reason||currentProjection()?.error||'Calculating current projections')}">Unavailable</span>`;
  const lower=(status?.max_lower_excess||0)>0,upper=(status?.max_upper_excess||0)>0,boundary=status?.numerical_state==='on-boundary',outside=status?.numerical_state==='outside';
  const glyph=boundary?'≈':lower&&upper?'↕':lower?'↓':'↑',clipped=preview.clipped_channels.some(Boolean),description=!status?(clipped?'Preview channels clipped':'Illustrative preview; native gamut comparison unavailable'):boundary?'On numerical boundary':lower&&upper?'Negative and upper channel excess':lower?'Negative channel excess':upper?'Upper channel excess':'Inside RGB range',excess=lower||upper?'; max excess '+excessText(status):'';
  return `<span class="swatch-wrap"><button id="${prefix}-${r.id}" class="swatch" style="background:${previewCss(preview.display_rgb)}" data-inspect="${r.id}" title="${esc(rowName(r))} · ${name} · ${esc(previewContextText(preview))}; ${description}${excess}${clipped?'; raw channel clamp':''}" aria-label="Inspect ${esc(rowName(r))}, approximate ${name} preview${outside||clipped?', '+description+excess:''}"></button>${outside||boundary?`<span class="clip-badge" aria-hidden="true" title="${esc(description+excess)}">${glyph}</span>`:''}</span>`;
}

function coordinate(r) { return FIXTURES.readings[r.source][$('coordinates').value]; }
function inspect(id, origin) { const row=state.rows.find(r=>r.id===id);if(!row)return;state.selected=row; state.follow=false; render(); scrollSelected(); if(origin) document.getElementById(origin)?.focus({preventScroll:true});if(!row.inherited&&datasetBinding?.kind==='live')enqueue(()=>send('select',{operation_id:row.obs.operation_id,observation_id:row.obs.observation_id})); }
function previewReading(id,origin,kind='pointer') {
  const next=id&&state.rows.some(row=>row.id===id)?{id,origin}:null;
  const previous=kind==='focus'?focusPreview:pointerPreview;
  if(previous?.id===next?.id&&previous?.origin===next?.origin)return;
  if(kind==='focus')focusPreview=next;else pointerPreview=next;
  paintReadingPreview();
}
function clearReadingPreview(origin,kind='pointer') {
  if((kind==='focus'?focusPreview:pointerPreview)?.origin===origin)previewReading(null,origin,kind);
}
function paintReadingPreview() {
  const preview=pointerPreview||focusPreview,row=state.rows.find(row=>row.id===preview?.id);
  document.querySelectorAll('.plot-preview').forEach(el=>el.remove());
  document.querySelectorAll('[data-reading],.plot [data-inspect]').forEach(el=>{
    const active=!!row&&row!==state.selected&&(el.dataset.reading||el.dataset.inspect)===row.id;
    el.classList.toggle('reading-preview',active);
    if(active&&el.closest('svg')) {
      // Draw a non-interactive copy above crowded curves without moving hit targets.
      const overlay=el.cloneNode(true);
      for(const node of [overlay,...overlay.querySelectorAll('*')])for(const key of ['id','data-inspect','tabindex','role','aria-label'])node.removeAttribute(key);
      overlay.querySelectorAll('.curve-hit,.marker-hit,title').forEach(node=>node.remove());
      overlay.classList.add('plot-preview');overlay.setAttribute('aria-hidden','true');el.parentElement.append(overlay);
    }
  });
  spaceView?.setPreview?.(row?.id||null);
  $('selectionState').textContent=row&&row!==state.selected?'Preview '+rowName(row):state.selected?`${state.follow?'Following':'Inspecting'} ${rowName(state.selected)}`:'No inspected reading';
}
function annotationsLocked(row=null) { return !!row?.inherited||datasetBinding?.kind!=='live'||!ownerState?.can_edit_annotations; }
async function editReading(id, field) {
  const request=++editorRequest;
  const invokerId=document.activeElement?.id;const requestPanel=activePanel,requestPanelVersion=panelVersions.get(activePanel),requestEpoch=panelEpoch;
  const row=state.rows.find(r=>r.id===id);
  if(!row||annotationsLocked(row))return;
  const batchId=ownerState.batch_id;
  let detail;
  try{detail=await dataCall('annotation',{reading_ref:readingIdentity(row)});}
  catch(error){showStatusError(error);return;}
  // A later editor, panel or owner transition supersedes this read-only request.
  if(request!==editorRequest||ownerState?.batch_id!==batchId||annotationsLocked(row)||panelEpoch!==requestEpoch||activePanel!==requestPanel||panelVersions.get(activePanel)!==requestPanelVersion||!state.rows.some(r=>r.id===id))return;
  appliedDetails.set(id,detail.applied); row.notes=detail.applied.notes;
  if(activePanel)closePanel(activePanel);
  editorTarget={...datasetFence(),batchId,readingId:id};editorPinned=true;
  if(innerWidth>1200)state.inspector=true;inspect(id);
  if(innerWidth<=1200){$('annotationBody').append(document.querySelector('.annotation-editor'));$('annotationTitle').textContent='Edit '+rowName(state.selected);showPanel('annotationDialog',$(invokerId));}
  $(field==='notes'?'editNotes':'editName').focus();
}
function scrollSelected() {
  if(!state.selected || $('readingPanel').hidden)return;
  const row=document.getElementById('row-'+state.selected.id)?.closest('tr'), container=$('readingPanel').querySelector('.table-scroll');
  if(!row)return;
  const area=container.getBoundingClientRect(), box=row.getBoundingClientRect(), header=container.querySelector('thead').getBoundingClientRect().height;
  if(box.top<area.top+header)container.scrollTop+=box.top-area.top-header;
  else if(box.bottom>area.bottom)container.scrollTop+=box.bottom-area.bottom;
}
function setTool(tool) { state.tool=tool;document.querySelector('.dock-analysis').open=true;render();$(tool+'Tab').focus(); }
function selectionKey() {
  const ref=referenceReading();
  return `<span>${state.plotted.size} plotted</span>`;
}
function curveDash(r) { const i=state.rows.indexOf(r);return i%3===1?'7 3':i%3===2?'2 3':''; }
const tableColumns = [
  {key:'frequency',label:'Refresh frequency · Hz',checked:true,kind:'scalar'},
  {key:'displayDelay',label:'Display update delay · ms',checked:true,kind:'timing'},
  {key:'reaction',label:'Instrument reaction · ms',checked:true,kind:'timing',hint:'Signed native reaction time'},
  {key:'preview',label:'Preview',checked:true},
  {key:'x',label:'X',hint:'Native XYZ'},
  {key:'y',label:'Y',checked:true,hint:'Native XYZ; retained units'},
  {key:'z',label:'Z',hint:'Native XYZ'},
  {key:'xy',label:'x / y',hint:'CIE 1931 coordinates'},
  {key:'uv',label:'u′ / v′',checked:true,hint:'CIE 1976 coordinates'},
  {key:'lstar',label:'L*',hint:'Declared Lab white required'},
  {key:'astar',label:'a*',hint:'Declared Lab white required'},
  {key:'bstar',label:'b*',hint:'Declared Lab white required'},
  {key:'gamut',label:'Reference gamut',heading:'gamutHeading',hint:'Ideal RGB reference; preview scale'},
  {key:'yChange',label:'Y change (%)',heading:'yChangeHeading',checked:true,requiresReference:true},
  {key:'uvDistance',label:'Δu′v′',heading:'uvDistanceHeading',checked:true,requiresReference:true,hint:'Coordinate distance to reference'},
  {key:'delta',label:'ΔE00',heading:'deltaHeading',checked:true,requiresReference:true,requiresWhite:true},
  {key:'rms',label:'Spectral RMS',heading:'rmsHeading',requiresReference:true,hint:'Matching spectra and reference required'},
  ...signedMetrics.flatMap(group=>group.keys.map((key,index)=>({key,label:group.labels[index],requiresReference:true,requiresWhite:group.requiresWhite,hint:'Signed reading minus reference'+(group.nativeUnit?' in native XYZ units':'' )}))),
  {key:'spectrum',label:'Spectrum',hint:'Native range, band count and units'},
  {key:'units',label:'XYZ units / scale'},
  {key:'device',label:'Instrument'},
  {key:'mode',label:'Mode'},
  {key:'time',label:'Acquired (UTC)'},
  {key:'calibration',label:'Calibration'},
  {key:'saved',label:'Saved'},
  {key:'notes',label:'Notes',checked:true},
];
$('columnChoices').innerHTML=tableColumns.map(c=>`<label class="check"><input type="checkbox" id="column-${esc(c.key)}" aria-label="${esc(c.label)}" ${c.checked?'checked':''}><span>${esc(c.label)}${c.hint?`<span class="small muted">${esc(c.hint)}</span>`:''}</span></label>`).join('');
$('columnHeadings').outerHTML=tableColumns.map(c=>`<th id="${esc(c.heading||'heading-'+c.key)}" data-column="${esc(c.key)}">${esc(c.label)}</th>`).join('');
function columnShown(c){return $('column-'+c.key).checked&&(!c.kind||state.rows.some(row=>row.obs.record_kind===c.kind))&&(!c.requiresReference||!!state.reference)&&(!c.requiresWhite||!!state.white);}
function renderTableColumns(){
  const units=[...new Set(state.rows.map(r=>r.obs.backend_reported.xyz?.unit).filter(Boolean))];
  $('heading-y').textContent='Y'+(units.length===1?' · '+unitText(units[0]):units.length>1?' · mixed units':'');
  for(const c of tableColumns){
    const input=$('column-'+c.key),reason=c.requiresReference&&!state.reference?'Choose a comparison reference.':c.requiresWhite&&!state.white?'Declare a Lab reference white.':'';
    input.disabled=!!reason;input.closest('label').title=reason||c.hint||c.label;
    document.querySelector(`[data-column="${esc(c.key)}"]`).hidden=!columnShown(c);
  }
}
function sampleFlags(row){
  // Lineage travels with the dataset row: saved and imported sources keep their own.
  const repeat=row.repeatOf;
  const source=repeat&&state.rows.find(item=>matchesReading(repeat,item));
  const flags=[];
  // Chart readings keep their canonical A1/B1 coordinate beside the operator's name.
  if(row.chartCell)flags.push('Patch '+row.chartCell.coordinate+(row.chartCell.patch_name?' · '+row.chartCell.patch_name:''));
  if(repeat)flags.push(source?'Repeat of #'+(state.rows.indexOf(source)+1):'Repeat');
  if(state.rows.filter(item=>item.name===row.name).length>1)flags.push('Shared name');
  return flags.map(flag=>`<span class="reference-tag">${esc(flag)}</span>`).join('');
}
function tableCells(r){
  const native=r.obs.backend_reported,xyz=native.xyz,source=FIXTURES.readings[r.source],analysis=currentAnalysis(),ref=referenceReading(),status=referenceStatus(r);
  const coordinateText=key=>source[key]?source[key].slice(0,2).map(v=>fixedText(v,6)).join(' / '):'Unavailable';
  const lab=currentProjection()?.lab_by_source?.[r.source],delta=analysis?.delta_e_by_source?.[r.source],spectral=compareEntry(r),spectrum=native.spectrum;
  const xyzContext=xyz?unitText(xyz?.unit||'Unavailable')+' · '+scaleText(xyz?.scale||'Unavailable'):'No native XYZ was returned.';
  const signed=signedComparisonCells(r);
  const values={
    frequency:r.obs.record_kind==='scalar'?numberText(native.value):'Unavailable',
    displayDelay:r.obs.record_kind==='timing'?numberText(native.display_update_delay_ms):'Unavailable',
    reaction:r.obs.record_kind==='timing'?numberText(native.instrument_reaction_ms):'Unavailable',
    preview:swatch(r,'table-swatch'), x:numberText(xyz?.values[0]),y:numberText(xyz?.values[1]),z:numberText(xyz?.values[2]),
    xy:coordinateText('xyy'),uv:coordinateText('uvy'),lstar:numberText(lab?.[0]),astar:numberText(lab?.[1]),bstar:numberText(lab?.[2]),
    gamut:`<span class="gamut-state">${$('referenceGamut').value==='none'?'—':statusLabel(status)}</span><span class="gamut-excess">${$('referenceGamut').value==='none'?'':excessText(status)}</span>`,
    yChange:xyzValue(r,'y_percent'),uvDistance:xyzValue(r,'uv_distance'),delta:r===ref?'Reference':numberText(delta),rms:r===ref?'Reference':numberText(spectral?.rms),
    ...signed.values,
    spectrum:spectrum?esc(`${spectrum.wavelengths_nm[0]}–${spectrum.wavelengths_nm.at(-1)} nm · ${spectrum.values.length} bands · ${unitText(spectrum.unit)}`):'Unavailable',
    units:esc(xyzContext),device:esc(r.obs.device.model),mode:esc(r.obs.actual_mode.replaceAll('_',' ')),time:esc(r.obs.acquired_at),calibration:esc(r.obs.calibration.status),saved:esc(rowSavingState(r)),
    notes:`<button id="edit-notes-${r.id}" class="notes-action" data-edit="${r.id}" data-field="notes" title="${esc(r.notes||'Add notes')}" aria-label="${esc(r.notes||'Add notes')} · Edit notes for reading ${state.rows.indexOf(r)+1}" ${annotationsLocked(r)?'disabled':''}><span class="note-preview">${esc(r.notes||'Add notes')}</span></button>`,
  };
  const reasons={...signed.reasons,frequency:native.native_method,displayDelay:'Native white-change marker; no optical-onset inference',reaction:'Signed instrument reaction relative to the native white-change marker',x:xyzContext,y:xyzContext,z:xyzContext,xy:source.coordinate_reasons?.xyy,uv:source.coordinate_reasons?.uvy,...Object.fromEntries(['lstar','astar','bstar'].map(key=>[key,currentProjection()?.color_reasons?.[r.source]||'Lab needs compatible native XYZ and a declared white.'])),gamut:statusText(status),yChange:xyzReason(r,'y_reason'),uvDistance:xyzReason(r,'uv_reason'),delta:colorComparisonEntry(r)?.delta_e2000_reason||'ΔE00 under '+whiteLabel(),rms:spectral?.reason||'Reading minus reference on identical spectral grids and units.',spectrum:spectrum?.quantity||'No native spectrum was returned.',calibration:r.obs.calibration.detail};
  return tableColumns.map(c=>`<td data-column="${esc(c.key)}" class="${c.key==='preview'?'preview-cell':c.key==='saved'?'saving-cell':c.key==='notes'?'note':'numeric'}" ${columnShown(c)?'':'hidden'} title="${esc(reasons[c.key]||c.hint||c.label)}">${values[c.key]}</td>`).join('');
}
for(const c of tableColumns)$('column-'+c.key).onchange=()=>render();
function spectralPlot(readings, residuals=null) {
  const spectra=residuals||readings.filter(r=>r.obs.backend_reported.spectrum).map(r=>({s:r.obs.backend_reported.spectrum,r}));
  if(!spectra.length)return '<div class="empty">'+(readings.length?'No spectrum is available for the checked readings.':'Check readings in the table to show their spectra.')+'</div>';
  const frame=$(residuals?'residualPlot':'spectrumPlot'),W=frame.clientWidth||600,H=frame.clientHeight||320,L=H<180?56:82,R=16,T=10,B=H<180?32:48;
  const quantities=new Set(spectra.map(({s})=>JSON.stringify([residuals?s.source_quantity:s.quantity,s.unit])));
  if(quantities.size>1)return '<div class="empty">These spectra use different quantities or units. Check compatible readings to overlay them.</div>';
  let xmin=Infinity,xmax=-Infinity,ymin=0,ymax=0;
  for(const {s} of spectra){for(const x of s.wavelengths_nm){xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);}for(const y of s.values){ymin=Math.min(ymin,y);ymax=Math.max(ymax,y);}}
  if(ymin===ymax){ymin=residuals?-1:0;ymax=1;}
  const tickCount=Math.max(1,Math.min(5,Math.floor((H-T-B)/30))),step=niceStep(ymax-ymin,tickCount);ymin=Math.floor(ymin/step)*step;ymax=Math.ceil(ymax/step)*step;
  const px=x=>L+(x-xmin)/(xmax-xmin||1)*(W-L-R),py=y=>H-B-(y-ymin)/(ymax-ymin)*(H-T-B);
  const guide=FIXTURES.wavelength_guide.samples,first=Math.max(xmin,guide[0].wavelength_nm),last=Math.min(xmax,guide.at(-1).wavelength_nm),style=$('wavelengthStyle').value;
  const gradient=style!=='off'&&first<last?`<defs><linearGradient id="wave-${residuals?'residual':'native'}" gradientUnits="userSpaceOnUse" x1="${px(guide[0].wavelength_nm)}" y1="0" x2="${px(guide.at(-1).wavelength_nm)}" y2="0">${guide.map(sample=>`<stop offset="${(sample.wavelength_nm-guide[0].wavelength_nm)/(guide.at(-1).wavelength_nm-guide[0].wavelength_nm)}" stop-color="${previewCss(sample.colors[$('previewGamut').value])}"/>`).join('')}</linearGradient></defs><rect class="wavelength-field" x="${px(first)}" y="${style==='strip'?H-B+8:T}" width="${px(last)-px(first)}" height="${style==='strip'?10:H-T-B}" fill="url(#wave-${residuals?'residual':'native'})"/>`:'';
  const grid=ticks(ymin,ymax,tickCount).map(y=>`<path class="gridline" d="M${L} ${py(y)}H${W-R}"/><text text-anchor="end" x="${L-10}" y="${py(y)+4}">${tickText(y,step)}</text>`).join('');
  const xstep=niceStep(xmax-xmin,6),xticks=ticks(xmin,xmax,6).map(x=>`<path class="axis" d="M${px(x)} ${H-B}v5"/><text text-anchor="middle" x="${px(x)}" y="${H-23}">${tickText(x,xstep)}</text>`).join('');
  const curves=[...spectra].sort((a,b)=>(a.r===state.selected)-(b.r===state.selected)).map(({s,r})=>{const points=s.values.map((v,j)=>`${px(s.wavelengths_nm[j])},${py(v)}`).join(' '),dash=curveDash(r);return `<g id="${residuals?'residual':'curve'}-${r.id}" data-inspect="${r.id}" tabindex="${r===state.selected?0:-1}" role="button" aria-label="Inspect ${esc(rowName(r))}"><polyline class="curve-halo" style="stroke-dasharray:${dash}" points="${points}"/><polyline class="curve ${r===state.selected?'selected':''}" style="stroke:${curveColor(r)};stroke-dasharray:${dash}" points="${points}"/><title>${esc(rowName(r))}${residuals?' minus reference':''}; ${$('curveContrast').checked?'contrast-styled curve':'calculated preview color'}</title></g>`;}).join('');
  const units=[...new Set(spectra.map(({s})=>unitText(s.unit)))],quantity=[...new Set(spectra.map(({s})=>(residuals?s.source_quantity:s.quantity).replaceAll('_',' ')))].join(' / '),ordinate=(residuals?'Residual · ':'')+quantity+' · '+units.join(' / ');
  return `<svg class="plot" viewBox="0 0 ${W} ${H}" role="group" aria-label="${residuals?'Signed reading minus reference spectral residuals':'Native spectra'} · ${esc(ordinate)} with illustrative wavelength guide">${gradient}${grid}<path class="axis" d="M${L} ${T}V${H-B}H${W-R}"/><path class="zero" d="M${L} ${py(0)}H${W-R}"/>${curves}${xticks}${H>=180?`<text class="spectral-ordinate" text-anchor="middle" transform="translate(16 ${(T+H-B)/2}) rotate(-90)">${esc(ordinate)}</text>`:''}<text x="${W/2}" text-anchor="middle" y="${H-4}">Wavelength · nm</text></svg>`;
}

const rasterCache=new Map();
function paintChromaticityRaster() {
  const holder=$('chromaticityPlot').querySelector('.raster-holder');
  if(!holder)return;
  const view=$('coordinates').value,requested=$('previewGamut').value,key=view+':'+requested;
  if(!rasterCache.has(key)) {
    const raster=FIXTURES.chromaticity_field.views[view];
    let canvas=document.createElement('canvas');canvas.width=raster.width;canvas.height=raster.height;
    let actual=requested,ctx;
    try {ctx=canvas.getContext('2d',{colorSpace:requested});if(ctx?.getContextAttributes().colorSpace!==requested)actual='srgb';}
    catch {actual='srgb';}
    if(actual!==requested){canvas=document.createElement('canvas');canvas.width=raster.width;canvas.height=raster.height;ctx=canvas.getContext('2d',{colorSpace:'srgb'});}
    try {
      if(!ctx)throw Error('Canvas2D unavailable.');
      const bytes=Uint8ClampedArray.from(atob(raster.rgba8[actual]),c=>c.charCodeAt(0));
      const pixels=new ImageData(bytes,raster.width,raster.height,{colorSpace:actual});
      if(pixels.colorSpace!==actual)throw Error('ImageData color-space mismatch.');
      ctx.putImageData(pixels,0,0);
      canvas.dataset.outputColorSpace=ctx.getContextAttributes().colorSpace;canvas.dataset.coordinateView=view;
      canvas.setAttribute('aria-hidden','true');
      rasterCache.set(key,{canvas,actual});
    } catch(error){rasterCache.set(key,{error:error.message,actual});}
  }
  const entry=rasterCache.get(key);
  if(entry.error){holder.textContent='Field unavailable';$('fieldContext').textContent+=' · '+entry.error;return;}
  holder.replaceChildren(entry.canvas);
  positionChromaticityRaster();
  if(entry.actual!==requested)$('fieldContext').textContent+=' · sRGB field fallback: P3 canvas unavailable';
}

function positionChromaticityRaster() {
  const stage=$('chromaticityPlot').querySelector('.chromaticity-stage'),svg=stage?.querySelector('svg'),field=stage?.querySelector('.chromaticity-field');
  if(!field||!svg.getBoundingClientRect().width)return;
  const transform=svg.getScreenCTM(),rect=stage.getBoundingClientRect(),holder=field.querySelector('.raster-holder');
  if(!transform)return;
  const frame=JSON.parse(field.dataset.frame),raster=JSON.parse(field.dataset.raster);
  Object.assign(field.style,{left:(transform.e-rect.left+frame[0]*transform.a)+'px',top:(transform.f-rect.top+frame[1]*transform.d)+'px',width:frame[2]*transform.a+'px',height:frame[3]*transform.d+'px'});
  Object.assign(holder.style,{left:(raster[0]-frame[0])*transform.a+'px',top:(raster[1]-frame[1])*transform.d+'px',width:raster[2]*transform.a+'px',height:raster[3]*transform.d+'px'});
}

const chromaticityResize=new ResizeObserver(positionChromaticityRaster);
chromaticityResize.observe($('chromaticityPlot'));

function chromaticityPlot() {
  const key=$('coordinates').value,names=key==='uvy'?['u′','v′']:['x','y'],retained=plottedRows().map(coordinate).filter(Boolean),inspected=state.selected&&coordinate(state.selected);
  const center=zoom===1?(key==='xyy'?[.4,.45]:[.33,.32]):inspected||[0,1].map(i=>retained.length?(Math.min(...retained.map(c=>c[i]))+Math.max(...retained.map(c=>c[i])))/2:.35);
  const W=$('chromaticityPlot').clientWidth||390,H=$('chromaticityPlot').clientHeight||345,T=12,S=Math.max(20,Math.min(W-85,H-T-38)),L=Math.max(45,(W-S)/2),half=(key==='xyy'?.47:.38)/zoom,px=x=>L+(x-center[0]+half)/(2*half)*S,py=y=>T+S-(y-center[1]+half)/(2*half)*S;
  const tickCount=Math.max(2,Math.min(5,Math.floor(S/45))),step=niceStep(2*half,tickCount),xticks=ticks(center[0]-half,center[0]+half,tickCount),yticks=ticks(center[1]-half,center[1]+half,tickCount);
  const grid=xticks.map(x=>`<path class="gridline" d="M${px(x)} ${T}V${T+S}"/><text text-anchor="middle" x="${px(x)}" y="${T+S+20}">${tickText(x,step)}</text>`).join('')+yticks.map(y=>`<path class="gridline" d="M${L} ${py(y)}H${L+S}"/><text text-anchor="end" x="${L-10}" y="${py(y)+4}">${tickText(y,step)}</text>`).join('');
  const locus=FIXTURES.spectral_locus,fullBoundary=locus.samples.map(sample=>`${px(sample[key][0])},${py(sample[key][1])}`).join(' '),field=FIXTURES.chromaticity_field;
  const backgroundKey=JSON.stringify([key,zoom,center,W,H,S,$('previewGamut').value,$('colorBackgrounds').checked]);
  const currentField=$('chromaticityPlot').querySelector('.chromaticity-field');
  heldChromaticityField=backgroundKey===chromaticityFieldKey&&currentField?currentField:null;chromaticityFieldKey=backgroundKey;
  const raster=field.views[key],extent=raster.extent,rasterBox=[px(extent[0]),py(extent[3]),px(extent[1])-px(extent[0]),py(extent[2])-py(extent[3])];
  const clip=locus.samples.map(sample=>`${(sample[key][0]-extent[0])/(extent[1]-extent[0])*100}% ${(extent[3]-sample[key][1])/(extent[3]-extent[2])*100}%`).join(',');
  const background=!heldChromaticityField&&$('colorBackgrounds').checked?`<div class="chromaticity-field" data-frame='${JSON.stringify([L,T,S,S])}' data-raster='${JSON.stringify(rasterBox)}' aria-hidden="true"><div class="raster-holder" style="clip-path:polygon(${clip})"></div></div>`:'';
  const labels=[450,470,490,510,530,550,570,590,610,650];
  const labelBoxes=[],referenceLabels=[];
  function referenceLabel(x,y,text,left=false) {
    if(x<L-20||x>L+S+20||y<T-12||y>T+S+12)return '';
    const width=String(text).length*7;
    const candidates=[-8,-26,10,-44,28].flatMap(dy=>[left,!left].map(side=>({x:x+(side?-9-width:12),y:y+dy,width,height:14})));
    const box=candidates.find(b=>b.x>=L-27&&b.x+b.width<=L+S+27&&b.y>=T-12&&b.y+b.height<=T+S+24&&!labelBoxes.some(a=>b.x<a.x+a.width+3&&b.x+b.width+3>a.x&&b.y<a.y+a.height+3&&b.y+b.height+3>a.y));
    // Keep the tick/reference geometry when this view has no readable label slot.
    if(!box)return '';
    labelBoxes.push(box);
    return `<text class="locus-label" x="${box.x}" y="${box.y+11}">${esc(text)}</text>`;
  }
  const locusPath=$('showLocus').checked?`<g class="spectral-reference" aria-label="CIE 1931 2 degree spectral locus 360 to 830 nm; wavelength labels in nm"><polyline class="reference-halo" points="${fullBoundary}"/><polyline class="spectral-locus" points="${fullBoundary}"/><line class="reference-halo purple-halo" x1="${px(locus.samples[0][key][0])}" y1="${py(locus.samples[0][key][1])}" x2="${px(locus.samples.at(-1)[key][0])}" y2="${py(locus.samples.at(-1)[key][1])}"/><line class="purple-boundary" x1="${px(locus.samples[0][key][0])}" y1="${py(locus.samples[0][key][1])}" x2="${px(locus.samples.at(-1)[key][0])}" y2="${py(locus.samples.at(-1)[key][1])}"><title>Line of purples joins 360 and 830 nm; not monochromatic light</title></line>${locus.samples.filter(sample=>labels.includes(sample.wavelength_nm)).map(sample=>{const x=px(sample[key][0]),y=py(sample[key][1]);referenceLabels.push(referenceLabel(x,y,sample.wavelength_nm,x<L+S/2));return `<circle class="locus-tick" cx="${x}" cy="${y}" r="2"/>`;}).join('')}</g>`:'';
  const outlines=referenceTargets().map(target=>{const geometry=referenceGeometry(target),primary=target===$('referenceGamut').value;if(!geometry?.primaries)return '';const points=geometry.primaries[key].map(c=>`${px(c[0])},${py(c[1])}`).join(' '),white=geometry.white_point[key];if(primary)referenceLabels.push(referenceLabel(px(white[0]),py(white[1]),'D65'));return `<g data-gamut="${target}"><polygon class="reference-halo ${primary?'':'comparison-halo'}" points="${points}"/><polygon class="gamut-outline ${primary?'':'comparison'}" points="${points}"><title>${esc(geometry.name)} ideal chromaticity triangle · CIE 1931 2° · D65</title></polygon>${primary?referenceCross(px(white[0]),py(white[1]),4):''}</g>`;}).join('');
  const visible=plottedRows().filter(r=>coordinate(r)).sort((a,b)=>(a===state.selected)-(b===state.selected));
  const points=visible.map(r=>{const c=coordinate(r),selected=r===state.selected,ref=comparisonActive()&&r.id===state.reference,radius=selected?7:visible.length>12?3.5:6,label=selected||ref||visible.length<=8?'#'+(state.rows.indexOf(r)+1):'';return `<g id="point-${r.id}" data-inspect="${r.id}" tabindex="${selected?0:-1}" role="button" aria-label="Inspect chromaticity ${esc(rowName(r))}; ${esc(statusText(referenceStatus(r)))}">${warningDiamond(px(c[0]),py(c[1]),r)}<circle class="marker-halo" cx="${px(c[0])}" cy="${py(c[1])}" r="${radius+1.5}"/><circle class="marker ${selected?'inspected':''}" cx="${px(c[0])}" cy="${py(c[1])}" r="${radius}" style="fill:${color(r)}"/>${ref?`${referenceCross(px(c[0]),py(c[1]),10)}`:''}<text x="${px(c[0])+12}" y="${py(c[1])-10}">${label}</text><title>${esc(rowName(r))}: ${names[0]}=${c[0].toFixed(6)}, ${names[1]}=${c[1].toFixed(6)}; ${esc(statusText(referenceStatus(r)))}</title></g>`;}).join('');
  $('fieldContext').textContent=($('colorBackgrounds').checked?'Approximate spectral colors · '+targetName($('previewGamut').value)+' encoding · ':'')+'CIE 1931 2° · wavelength labels in nm · equal axis scale';
  $('fieldLimits').hidden=!$('colorBackgrounds').checked;
  $('fieldLimits').textContent=`Field illustration: ideal relative Y ≤ ${numberText(field.relative_y_cap)} after channel clamp, before 8-bit rounding. ${raster.width} × ${raster.height} pixels; browser scaling and out-of-gamut clipping remain approximate. Expansion adds no samples.`;
  return `<div class="chromaticity-stage">${background}<svg class="plot" viewBox="0 0 ${W} ${H}" role="group" aria-label="${key==='uvy'?'CIE 1976 u prime v prime':'CIE 1931 x y'} retained points, spectral reference and RGB triangles. Equal axis scale."><defs><clipPath id="point-clip"><rect x="${L-28}" y="${T-16}" width="${S+56}" height="${S+40}"/></clipPath></defs>${grid}<path class="axis" d="M${L} ${T}V${T+S}H${L+S}"/><g clip-path="url(#point-clip)">${locusPath}${outlines}${points}<g class="reference-labels">${referenceLabels.join('')}</g></g><text text-anchor="middle" x="${L+S/2}" y="${H-4}">${names[0]}</text><text x="${L-42}" y="${T+S/2}">${names[1]}</text>${!visible.length?`<text text-anchor="middle" x="${W/2}" y="${H/2}">${plottedRows().length?'Checked coordinates unavailable':'No checked readings'}</text>`:''}</svg></div>`;
}

function referenceCross(x,y,radius) {
  const path=`M${x-radius} ${y}h${radius*2}M${x} ${y-radius}v${radius*2}`;
  return `<path class="reference-halo cross-halo" d="${path}"/><path class="reference-cross" d="${path}"/>`;
}

function declaredWhite() { return state.white ? currentProjection()?.white_descriptor || null : null; }
function whiteLabel() {
  if(!state.white)return 'undeclared';
  if(state.white.kind==='theoretical-lab-reference')return state.white.name+' · assumed standard';
  const row=state.rows.find(r=>matchesReading(state.white,r));
  return row?'#'+(state.rows.indexOf(row)+1)+' · '+row.name:'unavailable retained white';
}
function whiteChoiceContext() { return state.selected?.obs.backend_reported.xyz||null; }
function requestDefaultLabWhite(){
  if(!defaultLabWhiteRequested||defaultLabWhitePending||state.white||$('spaceKind').value!=='lab'||!canChangeComparison()||blocked)return;
  const native=whiteChoiceContext()||state.rows.find(row=>row.obs.backend_reported.xyz)?.obs.backend_reported.xyz;
  if(!native)return;
  if(!FIXTURES.standard_whites.some(white=>white.name==='D50'&&white.observer===native.observer))return;
  const binding=datasetFence();
  const white={kind:'theoretical-lab-reference',name:'D50',observer:native.observer,reference_y:100,unit:native.unit,scale:native.scale};
  defaultLabWhitePending=true;defaultLabWhiteRequested=false;
  return enqueue(async()=>{
    if(!sameDataset(binding))throw Error('The data changed before D50 could be declared. Choose the white again.');
    if(selectedComparison().white)return;
    await sendComparison({white},binding);
  }).finally(()=>{defaultLabWhitePending=false;render();});
}
function whiteEligible(r) { return !!r.obs.backend_reported.xyz&&FIXTURES.eligible_white_sources.includes(r.source); }
function requestAnalysis() {
  const selected=analysisSources(),reference=referenceReading()?.source||null,white=state.white,scale=displayScale,key=analysisKey(),comparison_version=selectedComparison()?.version;
  requestProjection();if(!comparisonActive())return currentAnalysis();
  if(!reference||!comparisonReady()){analysisController?.abort();analysisPending='';analysisReply=null;return null;}
  if(currentAnalysis())return currentAnalysis();if(comparisonCache.has(key)){analysisReply=comparisonCache.get(key);return currentAnalysis();}
  if(analysisPending===key)return null;
  analysisReply=null;analysisController?.abort();analysisController=new AbortController();
  const controller=analysisController;analysisPending=key;
  dataCall('analysis',{comparison_version,selected,scale,dataset_revision:FIXTURES.dataset_revision}).then(data=>{
    if(controller.signal.aborted)return;
    if(data.source_commit!==FIXTURES.source_commit||data.dataset_revision!==FIXTURES.dataset_revision)throw Error('Preview source changed. Reload the current preview.');
    if(JSON.stringify(data.selected)!==JSON.stringify(selected)||data.comparison_version!==comparison_version||data.reference!==reference||!sameWhite(data.white,white)||data.scale!==scale)throw Error('Analysis identity mismatch.');
    if(!data.xyz_comparison_by_source||selected.some(source=>!data.xyz_comparison_by_source[source]))throw Error('Native comparison response is unavailable. Restart the current app.');
    if(!data.color_comparison_by_source||selected.some(source=>!data.color_comparison_by_source[source]))throw Error('Signed comparison response is unavailable. Restart the current app.');
    analysisReply={key,...data};boundedCache(comparisonCache,key,analysisReply);analysisPending='';render();
  }).catch(error=>{if(controller.signal.aborted)return;analysisReply={key,error:error.message};analysisPending='';render();});
  return null;
}
function renderWhite() {
  const candidates=state.rows.filter(whiteEligible);
  const standards=FIXTURES.standard_whites;
  const validChoices=[...standards.map(w=>'standard:'+w.name),...candidates.map(r=>'reading:'+r.id)];
  if(!validChoices.includes(state.whiteChoice))state.whiteChoice=null;
  $('whiteChoice').innerHTML='<option value="">Choose a white</option><optgroup label="Assumed standard white">'+standards.map(w=>`<option value="standard:${esc(w.name)}">${esc(w.name)} · xy ${w.xy.map(v=>fixedText(v,4)).join(' / ')}</option>`).join('')+'</optgroup><optgroup label="Retained readings">'+candidates.map(r=>`<option value="reading:${esc(r.id)}" title="${esc(rowName(r))}">#${state.rows.indexOf(r)+1} · ${esc(r.name)} · Y ${numberText(r.obs.backend_reported.xyz.values[1])}</option>`).join('')+'</optgroup>';
  $('whiteChoice').value=state.whiteChoice||'';
  $('whiteCandidateLabel').textContent=state.whiteChoice?$('whiteChoice').selectedOptions[0].textContent:'Choose a retained reading or an assumed standard white.';
  $('whiteChoice').title=$('whiteCandidateLabel').textContent;
  const isStandard=state.whiteChoice?.startsWith('standard:'),context=whiteChoiceContext();
  $('standardWhiteFields').hidden=!isStandard;
  $('standardWhiteContext').textContent=context?`Declare Y in ${unitText(context.unit)} · ${scaleText(context.scale)} · ${observerText(context.observer)}. These are the inspected reading's units; this does not measure the white.`:'Inspect a reading to declare compatible units, scale and observer.';
  $('declareWhite').disabled=!canChangeComparison()||blocked||!state.whiteChoice || (isStandard&&!context);$('clearWhite').disabled=!canChangeComparison()||blocked||!state.white;
  const white=state.white,descriptor=declaredWhite(),analysis=currentAnalysis(),summary=analysis?.summary;
  $('whiteContext').textContent=white?(descriptor?`${whiteLabel()} · Y ${numberText(descriptor.reference_y)} ${unitText(descriptor.unit)} · ${observerText(descriptor.observer)}. Direct Lab; no adaptation.`:currentProjection()?.error||'Resolving reference white…'):'Lab and ΔE00 need an explicitly declared white.';
  $('whiteContext').title=descriptor?.convention||'Declaration does not establish physical suitability.';
  const available=white&&summary?.delta_range;
  const pairReasons=[...new Set((analysis?.selected||[]).filter(key=>key!==referenceReading()?.source).map(key=>analysis.color_comparison_by_source?.[key]?.delta_e2000_reason).filter(Boolean))].join(' · ');
  $('colorDifference').className=available?'metric':'unavailable';
  $('colorDifference').innerHTML=available?`ΔE00 to reference<strong>${summary.delta_range.min===summary.delta_range.max?numberText(summary.delta_range.max):numberText(summary.delta_range.min)+' – '+numberText(summary.delta_range.max)}</strong><span>${summary.delta_range.count} comparisons${summary.color_unavailable?' · '+summary.color_unavailable+' unavailable':''} · individual values in the table</span>`:`<strong>ΔE00 ${state.reference&&white&&state.rows.some(r=>r.id!==state.reference)&&!analysis?'calculating':'unavailable'}</strong><span>${esc(!state.reference?'Choose a reference reading in Compare.':analysis?.error||pairReasons||(!white?'Declare a Lab white.':!state.rows.some(r=>r.id!==state.reference)?'Retain another reading to compare.':analysis?'No checked comparison readings.':'Waiting for comparison.'))}</span>`;
  $('spaceWhite').hidden=$('spaceKind').value!=='lab';
  $('spaceWhite').textContent=white?'Change white…':defaultLabWhitePending?'Declaring D50…':'Choose white…';
  $('spaceWhite').title=white?'Declared CIELAB white: '+whiteLabel():'Choose a reference white for CIELAB';
  requestDefaultLabWhite();
}
function renderColor() {
  const r=state.selected,white=state.white,analysis=currentProjection();
  $('colorSample').textContent=r?rowName(r):'Inspect a reading in the table.';
  $('colorSample').title=r?rowName(r):'';
  const lab=r&&analysis?.lab_by_source?.[r.source];
  $('labValues').className=lab?'metric':'unavailable';
  $('labValues').innerHTML=lab?`<h3>CIELAB · ${esc(whiteLabel())}</h3><dl><dt>L*</dt><dd>${numberText(lab[0])}</dd><dt>a*</dt><dd>${numberText(lab[1])}</dd><dt>b*</dt><dd>${numberText(lab[2])}</dd></dl><p class="small muted">Six significant figures for display; full values remain in the record.</p>`:`<strong>CIELAB unavailable</strong><span>${esc(!r?'Inspect a retained reading.':!white?'Declare its Lab reference white below.':analysis?.color_reasons?.[r.source]||analysis?.error||(!analysis?'Calculating coordinates…':'Reading/white conditions are incompatible.'))}</span>`;
}

function renderSpace() {
  if($('spacePanel').hidden)return;
  const lab=$('spaceKind').value==='lab',supplied=currentProjection();
  const coordinates=r=>lab?(state.white?supplied?.lab_by_source?.[r.source]:null):r.obs.backend_reported.xyz?.values;
  const rows=plottedRows().filter(r=>coordinates(r));
  const shells=referenceTargets().map(target=>{
    const g=referenceGeometry(target),points=lab?(state.white?g?.lab_points:null):g?.xyz_points;
    return {target,primary:target===$('referenceGamut').value,points:points||[],lines:points?g.lines:[],triangles:points?g.triangles:[],colors:points?g.vertex_colors:{}};
  });
  const gradient=$('spaceAppearance').value==='gradient';
  const names=lab?['L*','a*','b*']:['X','Y','Z'];
  const input={lab,target:$('previewGamut').value,whiteKey:JSON.stringify(state.white),surface:gradient,shells,
    cameraPreset:$('spaceCamera').value,rows:rows.map(r=>({id:r.id,number:state.rows.indexOf(r)+1,selected:r===state.selected,outside:$('gamutWarnings').checked&&referenceStatus(r)?.volume_numerical_state==='outside',values:coordinates(r),
      colors:Object.fromEntries(['srgb','display-p3'].map(target=>[target,displayPreview(r,target)?.display_rgb||null])),
      label:rowName(r)+' · '+names.map((n,i)=>n+'='+numberText(coordinates(r)[i])).join(' · ')+' · '+statusText(referenceStatus(r))}))};
  if(!spaceContextLost)try {if(!spaceView)spaceView=new ColorSpaceView($('spacePlot'),id=>inspect(id),id=>previewReading(id,$('spacePlot')));spaceView.update(input);spaceError=null;}
  catch(error){spaceError=error.message;}
  const actualTarget=spaceView?.actualTarget||$('previewGamut').value;
  const wireKey=shells.filter(shell=>shell.points.length).map(shell=>targetName(shell.target)+(shell.primary?(gradient&&shell.colors[actualTarget]?' gradient':' outline'):' dashed')).join(' · ');
  const fallback=actualTarget!==$('previewGamut').value?' · sRGB preview':'';
  const white=declaredWhite()||state.white;
  const whiteKey=lab&&state.white?(state.white.kind==='theoretical-lab-reference'?'Assumed '+state.white.name: 'White '+whiteLabel())+(white?.reference_y?` · white Y ${numberText(white.reference_y)} ${unitText(white.unit)}`:''):'';
  $('spaceWireKey').textContent=(wireKey?'Ideal '+wireKey+` · Y ${numberText(displayScale)} cd/m²`+fallback:'')+(whiteKey?'\n'+whiteKey:'');
  $('spaceWireKey').hidden=!wireKey&&!whiteKey;
  let failure=$('spacePlot').querySelector('.space-error');
  if(spaceError){if(!failure){failure=document.createElement('p');failure.className='space-error';failure.setAttribute('role','status');$('spacePlot').append(failure);}failure.textContent=spaceError+' Exact values and selection remain in the readings table.';}
  else failure?.remove();
  $('spaceContext').textContent=(spaceError?'3D unavailable: '+spaceError:lab?(!state.white?'Declare a reference white to show CIELAB.':supplied?.error||(!supplied?'Calculating Lab coordinates…':`CIELAB · ${whiteLabel()} · equal units · L* vertical · no adaptation.`)):'Reported XYZ · equal units · Y vertical. Units: '+[...new Set(rows.map(row=>unitText(row.obs.backend_reported.xyz.unit)))].join(' / ')+'.')+' Drag to orbit; scroll to zoom. Table selects hidden/coincident points.';
  if(spaceView?.actualTarget!==$('previewGamut').value&&!spaceError)$('spaceContext').textContent+=' 3D uses sRGB fallback; P3 WebGL output unavailable.';
  $('spaceReference').textContent=referenceTargets().length?referenceTargets().map(target=>targetName(target)).join(' + ')+` ideal RGB · D65 · zero black · Y=${numberText(displayScale)} cd/m². `+(lab?`Direct Lab under ${whiteLabel()}; no adaptation.`:'Reference model; no measured coverage.'):'No RGB reference selected.';
  const reasons=referenceTargets().flatMap(target=>{
    const reasons=referenceGeometry(target)?.component_reasons;
    return [reasons?.[lab?'lab':'xyz'],reasons?.geometry,gradient&&reasons?.surface_colors].filter(Boolean);
  });if(reasons.length)$('spaceContext').textContent+=' Reference unavailable: '+[...new Set(reasons)].join('; ');
  if(rows.length!==plottedRows().length)$('spaceContext').textContent+=` ${plottedRows().length-rows.length} checked coordinates unavailable.`;
  const primary=shells.find(shell=>shell.primary),missing=plottedRows().length-rows.length;
  const notice=spaceError?'3D unavailable. Exact values remain in the table.':
    lab&&!state.white?(defaultLabWhitePending?'Declaring assumed D50 white…':defaultLabWhiteRequested?'D50 needs an XYZ reading with a supported observer. Choose white to change the assumption.':'Choose a white to show CIELAB coordinates.'):
    supplied?.error||(!supplied&&ownerState?'Calculating color-space view…':
      reasons.length?'Reference unavailable: '+[...new Set(reasons)].join('; '):
      gradient&&(!primary?.points.length||!primary?.colors[actualTarget])?'Color gradient unavailable for this reference. Choose an RGB reference in View › Appearance.':
      missing?`${missing} checked reading${missing===1?' has':'s have'} no compatible ${lab?'CIELAB':'XYZ'} coordinates.`:'');
  $('spaceStatus').hidden=!notice;$('spaceStatus').textContent=notice;
}

function bindInspection(root=document) {
  root.querySelectorAll('[data-inspect]:not(#annotationDrafts [data-inspect])').forEach(el=>{
    const plot=el.closest('svg.plot');
    if(!plot)el.onclick=()=>inspect(el.dataset.inspect,el.id);
    el.onfocus=()=>{if(plot)plot.querySelectorAll('[data-inspect]').forEach(peer=>peer.tabIndex=peer===el?0:-1);previewReading(el.dataset.inspect,el,'focus');};el.onblur=()=>clearReadingPreview(el,'focus');
    if(el.tagName!=='BUTTON')el.onkeydown=e=>{
      if(e.key==='Enter'||e.key===' '){e.preventDefault();inspect(el.dataset.inspect,el.id);}
      else if(plot&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key)){
        const order=new Map(state.rows.map((row,i)=>[row.id,i]));
        const peers=[...plot.querySelectorAll('[data-inspect]')].sort((a,b)=>order.get(a.dataset.inspect)-order.get(b.dataset.inspect)),current=peers.indexOf(el),next=e.key==='Home'?0:e.key==='End'?peers.length-1:Math.max(0,Math.min(peers.length-1,current+(['ArrowLeft','ArrowUp'].includes(e.key)?-1:1)));
        e.preventDefault();el.tabIndex=-1;peers[next].tabIndex=0;peers[next].focus({preventScroll:true});
      }
    };
  });
  root.querySelectorAll('#rows [data-reading]').forEach(el=>{
    el.addEventListener('pointerenter',e=>{if(e.pointerType!=='touch')previewReading(el.dataset.reading,el);});el.addEventListener('pointerleave',()=>clearReadingPreview(el));
  });
  root.querySelectorAll('svg.plot').forEach(svg=>bindPlotInspection(svg));
}
function bindPlotInspection(svg) {
  const groups=[...svg.querySelectorAll('[data-inspect]')];
  if(groups.length&&!groups.some(el=>el.tabIndex===0))groups[0].tabIndex=0;
  const targets=groups.map(el=>{
    const title=el.querySelector(':scope > title'),label=title?.textContent||el.getAttribute('aria-label');title?.remove();
    const clip=el.closest('[clip-path]')?svg.querySelector('clipPath rect'):null;
    return {el,label,points:el.querySelector('.curve')?.getAttribute('points').trim().split(/\s+/).map(pair=>pair.split(',').map(Number)),marker:el.querySelector('.marker'),clip:clip?['x','y','width','height'].map(key=>Number(clip.getAttribute(key))):null};
  });
  const tooltip=document.createElementNS(svg.namespaceURI,'title');tooltip.classList.add('reading-tooltip');svg.prepend(tooltip);
  const pick=event=>{
    const rect=svg.getBoundingClientRect(),[left,top,width,height]=svg.getAttribute('viewBox').split(/\s+/).map(Number),scale=Math.min(rect.width/width,rect.height/height);
    if(!(scale>0))return null;
    const x=(event.clientX-rect.left-(rect.width-width*scale)/2)/scale+left,y=(event.clientY-rect.top-(rect.height-height*scale)/2)/scale+top;
    let closest=null,best=(8/scale)**2;
    for(const target of targets){
      let distance=Infinity;
      if(target.clip){const [cx,cy,cw,ch]=target.clip;if(x<cx||x>cx+cw||y<cy||y>cy+ch)continue;}
      if(target.points)for(let i=1;i<target.points.length;i++){
        const [ax,ay]=target.points[i-1],[bx,by]=target.points[i],dx=bx-ax,dy=by-ay;
        const t=Math.max(0,Math.min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy||1)));
        distance=Math.min(distance,(x-ax-t*dx)**2+(y-ay-t*dy)**2);
      }
      else if(target.marker){
        const mx=Number(target.marker.getAttribute('cx')),my=Number(target.marker.getAttribute('cy')),radius=Number(target.marker.getAttribute('r'));
        if(target.clip){const [cx,cy,cw,ch]=target.clip;if(mx+radius<cx||mx-radius>cx+cw||my+radius<cy||my-radius>cy+ch)continue;}
        distance=(x-mx)**2+(y-my)**2;
      }
      // Drawing order breaks exact ties; coincident readings remain accessible in the table.
      if(distance<=best){best=distance;closest=target;}
    }
    return closest;
  };
  svg.addEventListener('pointermove',event=>{if(event.pointerType==='touch')return;const hit=pick(event);svg.style.cursor=hit?'pointer':'';tooltip.textContent=hit?.label||'';previewReading(hit?.el.dataset.inspect,svg);});
  svg.addEventListener('pointerleave',()=>{svg.style.cursor='';tooltip.textContent='';clearReadingPreview(svg);});
  svg.onclick=event=>{
    // Assistive activation has no pointer coordinates; preserve its explicit target.
    const hit=event.detail===0?event.target.closest('[data-inspect]'):pick(event)?.el;
    if(hit)inspect(hit.dataset.inspect,hit.id);
  };
}

function automaticFileAttention() {
  const attention=Object.entries(ownerState?.companions?.formats||{}).filter(([kind,item])=>
    item.state==='failed'||item.state==='stale'&&item.retryable&&item.refresh_pending!==true||kind==='mat'&&item.state==='unavailable');
  if(ownerState?.ti3?.retryable&&ownerState.export_state!=='current')attention.push(['ti3',{state:ownerState.export_state,retryable:true}]);
  return attention;
}
function automaticFileRefreshPending() {
  return Object.values(ownerState?.companions?.formats||{}).some(item=>item.state==='stale'&&item.refresh_pending===true);
}
function automaticFileLabel(kind) {
  return {mat:'MAT',csv:'CSV',chart_selected_csv:'Chart reference CSV',ti3:'TI3'}[kind];
}
function ti3FileDetails(receipt=ownerState?.ti3){
  if(!receipt)return '';
  const reasons=new Map();
  for(const item of receipt.unavailable){
    const key=JSON.stringify([item.component,item.reason]);
    if(!reasons.has(key))reasons.set(key,{...item,records:new Set()});
    reasons.get(key).records.add(item.record_id);
  }
  const unavailable=[...reasons.values()].map(item=>`<li><strong>${esc({record:'Reading',xyz:'XYZ',spectrum:'Spectrum'}[item.component])}</strong> · ${item.records.size} record${item.records.size===1?'':'s'}: ${esc(item.reason)}</li>`).join('');
  const files=(receipt.files||[]).map(file=>`<li><strong>${esc({ordinary:'Readings',chart_reference:'Chart reference',chart_target:'Chart target'}[file.scope])} · ${esc({current:'Saved',unavailable:'Unavailable',stale:'Out of date',retired:'Previous export'}[file.state])}</strong> · ${file.row_count} record${file.row_count===1?'':'s'}${file.total_rows!==null?' of '+file.total_rows:''}${file.reason?`<p>${esc(file.reason)}</p>`:''}<details data-format="ti3-file:${esc(file.group_id)}"><summary>File details</summary><p>${esc(file.path)}</p></details></li>`).join('');
  return unavailable||files?`<details data-format="ti3"><summary>TI3 files and unavailable data</summary>${files?'<ul>'+files+'</ul>':''}${unavailable?'<ul>'+unavailable+'</ul>':''}</details>`:'';
}
function renderAutomaticFiles() {
  const companions=ownerState?.companions;
  $('automaticFiles').hidden=retainedReview()||!companions;
  $('showAutomaticFiles').hidden=retainedReview();
  const attention=automaticFileAttention();
  $('automaticFileNotice').hidden=!attention.length;
  if(!companions)return;
  const labels={absent:'Not prepared',preparing:'Preparing…',publishing:'Saving…',current:'Out of date',stale:'Out of date',failed:'Save failed',unavailable:'Unavailable'};
  const entries=Object.entries(companions.formats);
  const identity=JSON.stringify([entries,ownerState.export_state,ownerState.export_reason,ownerState.ti3]);
  if($('automaticFileList').dataset.identity!==identity){
    const expanded=new Set([...$('automaticFileList').querySelectorAll('details[open]')].map(detail=>detail.dataset.format));
    const focused=document.activeElement?.closest('[data-format]')?.dataset.format;
    $('automaticFileList').dataset.identity=identity;
    $('automaticFileList').innerHTML=entries.map(([kind,item])=>`<div><strong>${esc(automaticFileLabel(kind))} · ${item.state==='current'&&item.current?'Saved':item.state==='stale'&&item.refresh_pending===true?'Waiting to save':labels[item.state]}</strong>${item.reason?`<p>${esc(item.reason)}</p>`:''}<details data-format="${esc(kind)}"><summary>File details</summary><p>${esc(item.path)}</p><p>Source: ${esc(item.source_manifest_sha256||'Not prepared')}</p>${item.publication?`<p>File sync: ${esc(item.publication.file_sync)} · directory sync: ${item.publication.directory_synced?'confirmed':'unconfirmed'}</p>`:''}</details></div>`).join('')+`<div><strong>TI3 · ${esc({current:'Saved',unavailable:'Unavailable',stale:'Out of date',unverified:'Not confirmed'}[ownerState.export_state])}${ownerState.ti3?.unavailable?.length?' · Some values unavailable':''}</strong>${ownerState.export_reason?`<p>${esc(ownerState.export_reason)}</p>`:''}${ti3FileDetails()}</div>`;
    for(const detail of $('automaticFileList').querySelectorAll('details[data-format]')){
      detail.open=expanded.has(detail.dataset.format);
      if(detail.dataset.format===focused)detail.querySelector('summary').focus({preventScroll:true});
    }
  }
  const live=datasetBinding?.kind==='live';
  const message=(live?'':'Live batch '+ownerState.batch_name+': ')+attention.map(([kind,item])=>automaticFileLabel(kind)+(item.state==='stale'?' is out of date':item.state==='unavailable'?' is unavailable':' could not be saved')).join(' · ')+(live?'':'. Return to live to recover its files.');
  if($('automaticFileMessage').textContent!==message)$('automaticFileMessage').textContent=message;
  $('retryFiles').hidden=!attention.some(([,item])=>item.retryable);
  $('anotherFiles').hidden=!attention.some(([kind,item])=>kind==='ti3'?ownerState.ti3.relocatable:item.payload_sha256);
  $('retryFiles').disabled=$('anotherFiles').disabled=!live||companions.saving||!!state.busy||blocked;
}
function renderDrafts() {
  const drafts=state.rows.filter(r=>state.edits[r.id]||localEditErrors.has(r.id));
  $('annotationDrafts').hidden=!drafts.length;
  $('annotationDrafts').innerHTML=`${drafts.length} unapplied reading edit(s). Apply or cancel before Finish.<div class="draft-actions">${drafts.map(r=>`<button id="return-edit-${esc(r.id)}" data-inspect="${esc(r.id)}">Return to edit #${state.rows.indexOf(r)+1}</button>`).join('')}</div>`;
  $('annotationDrafts').querySelectorAll('[data-inspect]').forEach(el=>{el.onclick=()=>editReading(el.dataset.inspect,'name');});
  // One chain names the label and its indicator, so the two cannot disagree.
  const [saveLabel,saveKind]=ownerState?.cleanup_reason?['Needs attention','attention']:state.unsaved?['Save failed','attention']:ownerState?.phase==='saving'?['Saving…','pending']:ownerState?.companions?.saving?['Saving files…','pending']:automaticFileAttention().length?['Files need attention','attention']:automaticFileRefreshPending()?['Files pending…','pending']:drafts.length?['Edits pending','pending']:retainedReview()?['Read only','readonly']:datasetBinding?.closed?['Closed','readonly']:state.finished?['Finished','readonly']:state.batch?['Saved','saved']:['No batch','neutral'];
  const saveIcon={attention:'attention',pending:'pending',readonly:'lock',saved:'check'}[saveKind],status=$('saveState');
  // Serialised SVG never equals its source string; compare the state instead, so
  // an unchanged live region keeps its nodes and is not announced again.
  if(status.dataset.state!==saveKind||status.textContent!==saveLabel){status.innerHTML=(saveIcon?icon(saveIcon):'')+'<span>'+saveLabel+'</span>';status.dataset.state=saveKind;}
  $('finish').disabled=datasetBinding?.kind!=='live'||!ownerState?.can_finish||blocked||localEditErrors.size>0;
}
function loadExactAnnotation(r){
    const fence=datasetFence(),key=JSON.stringify([fence,r.id,r.version]);
    if(!annotationLoads.has(key)){
      annotationLoads.add(key);
      const errorContext=editorReading()?.id===r.id?actionErrorContext('annotationDialog'):null;
      dataCall('annotation',{reading_ref:readingIdentity(r)}).then(async detail=>{
        if(!sameDataset(fence))return;
        appliedDetails.set(r.id,detail.applied);
        if(detail.applied.version!==r.version)await refresh(true);
        render();
      }).catch(error=>{if(!sameDataset(fence))return;showStatusError(error,errorContext);})
        .finally(()=>annotationLoads.delete(key));
    }
}
function renderDetails() {
  const inspected=state.selected,r=editorPinned?editorReading():state.selected;
  $('inspector').hidden=!state.inspector;
  document.querySelector('.app-shell').classList.toggle('no-inspector',!state.inspector);
  $('showDetails').checked=state.inspector;
  if(activePanel==='annotationDialog'){
    renderEditorValues(editorReading());
    if(inspected)renderReadingMetadata(inspected);
    return;
  }
  if(!r){editorTarget=null;$('details').textContent='Select a reading to inspect it.';delete $('details').dataset.readingId;return;}
  const exact=appliedDetails.get(r.id);
  if(!exact||exact.version!==r.version){
    for(const id of ['editName','editNotes','editSave','editCancel'])if($(id)){$(id).disabled=true;$(id).dataset.composing='false';}
    if($('details').dataset.readingId!==r.id){$('details').textContent='Loading exact annotation text…';delete $('details').dataset.readingId;editorTarget=null;}
    else if($('annotationStatus'))$('annotationStatus').textContent='Loading exact annotation text…';
    loadExactAnnotation(r);
    return;
  }
  r.notes=exact.notes;
  const freshEditor=$('details').dataset.readingId!==r.id||!editorTarget||!sameDataset(editorTarget)||editorTarget?.readingId!==r.id;
  if(freshEditor){
    $('details').innerHTML=`<strong id="detailTitle"></strong><div id="detailPreview"></div>
      <section class="annotation-editor" aria-label="Sample annotations"><h3 id="editorHeading"></h3>
      <label>Name<input id="editName"></label><label>Notes<textarea id="editNotes" rows="3"></textarea></label>
      <div class="button-pair"><button id="editSave">Apply edit</button><button id="editCancel">Cancel edit</button></div>
      <p id="annotationStatus" class="small" role="status"></p>
      <p id="annotationError" class="dialog-error small" role="alert" hidden></p>
      </section><div id="detailMetadata"></div>`;
    $('details').dataset.readingId=r.id;
    editorTarget={...datasetFence(),batchId:ownerState?.batch_id??null,readingId:r.id};
    const target=editorTarget;
    const keepDraft=()=>{
      editorPinned=true;
      const captured={name:$('editName').value,notes:$('editNotes').value};
      $('annotationError').hidden=true;
      enqueue(async()=>{
        requireEditorReading(target);
        await waitMetadata(false);
        const detail=await dataCall('annotation',{reading_ref:readingIdentity(r)});
        requireEditorReading(target);
        await send('edit_annotation_draft',{operation_id:r.obs.operation_id,observation_id:r.obs.observation_id,
          version:detail.applied.version,draft_version:detail.draft?.draft_version||0,...captured});
      },r.id,captured,actionErrorContext('annotationDialog'));
    };
    $('editName').oninput=$('editNotes').oninput=keepDraft;
    for(const id of ['editName','editNotes']){
      $(id).onfocus=()=>{editorPinned=true;};
      $(id).oncompositionstart=()=>$(id).dataset.composing='true';
      $(id).oncompositionend=()=>{$(id).dataset.composing='false';keepDraft();};
      $(id).onblur=()=>{if($(id).dataset.composing==='true'){$(id).dataset.composing='false';keepDraft();}};
    }
    $('editSave').onclick=()=>{const context=actionErrorContext('annotationDialog');$('annotationError').hidden=true;return enqueue(async()=>{
      requireEditorReading(target);
      if(localEditErrors.has(r.id))throw Error('Correct or discard refused annotation text first.');
      await waitMetadata(false);
      const detail=await dataCall('annotation',{reading_ref:readingIdentity(r)});
      requireEditorReading(target);
      if(!detail.draft?.dirty)return;
      await send('annotate',{operation_id:r.obs.operation_id,observation_id:r.obs.observation_id,
        version:detail.applied.version,draft_version:detail.draft.draft_version});
      appliedDetails.delete(r.id);
    },null,null,context);};
    $('editCancel').onclick=()=>{const context=actionErrorContext('annotationDialog'),token=localEditValues.get(r.id);$('annotationError').hidden=true;return enqueue(async()=>{
      requireEditorReading(target);
      const detail=await dataCall('annotation',{reading_ref:readingIdentity(r)});
      requireEditorReading(target);
      if(detail.draft)await send('discard_annotation_draft',{operation_id:r.obs.operation_id,
        observation_id:r.obs.observation_id,draft_version:detail.draft.draft_version});
      if(localEditValues.get(r.id)===token){localEditValues.delete(r.id);localEditErrors.delete(r.id);}
    },null,null,context);};
  }
  $('editorHeading').textContent='Editing '+rowName(r);
  renderEditorValues(r);
  if(inspected)renderReadingMetadata(inspected);
}
function latestReadingRow(){
  // Live and saved snapshots both append inherited chart-source rows after this
  // batch's own rows (dataset_binding.py), so neither may name one as latest.
  return state.rows.findLast(row=>!row.inherited)||null;
}
function latestReading(r){
  const heading='<strong class="latest-name">'+esc(rowName(r))+'</strong>';
  const native=r.obs.backend_reported;
  if(['scalar','timing'].includes(r.obs.record_kind))return heading+typedReadingValues(r);
  const xyz=native.xyz,source=FIXTURES.readings[r.source],lab=currentProjection()?.lab_by_source?.[r.source];
  const pair=value=>value?value.slice(0,2).map(v=>fixedText(v,5)).join(' / '):'Unavailable';
  const metric=(label,value)=>'<div><dt>'+label+'</dt><dd>'+value+'</dd></div>';
  const values=xyz?'<div class="latest-y"><span class="metric-label">Y</span><strong>'+numberText(xyz.values[1])+'</strong><span class="latest-unit">'+esc(unitText(xyz.unit))+'</span></div>':'<p class="latest-no-xyz">No native XYZ</p>';
  const context=xyz?'<p class="latest-context">'+esc(scaleText(xyz.scale))+'</p>':'';
  const spectrum=native.spectrum;
  const spectral=spectrum?'<p class="latest-spectrum">'+spectrum.values.length+' bands · '+spectrum.wavelengths_nm[0]+'–'+spectrum.wavelengths_nm.at(-1)+' nm</p>':'<p class="latest-spectrum">No native spectrum</p>';
  const metrics=xyz?'<dl class="latest-metrics">'+metric('X / Z',[xyz.values[0],xyz.values[2]].map(v=>numberText(v)).join(' / '))+metric('x / y',pair(source.xyy))+metric('u′ / v′',pair(source.uvy))+(lab?metric('L* / a* / b*',lab.map(v=>numberText(v)).join(' / '))+metric('Lab white',esc(whiteLabel())):'')+'</dl>':'';
  return heading+'<div class="latest-primary">'+swatch(r,'latest-swatch')+values+'</div>'+context+metrics+spectral+'<span class="latest-preview-label">Approximate color preview</span>';
}
// Scalar and timing records only; color readings use latestReading's own values.
function typedReadingValues(r){
  const native=r.obs.backend_reported;
  if(r.obs.record_kind==='scalar')return `<span class="latest-value">Refresh frequency ${numberText(native.value)} ${esc(native.unit)}</span>`;
  return `<span class="latest-value">Display update ${numberText(native.display_update_delay_ms)} ms</span><br>Instrument reaction ${numberText(native.instrument_reaction_ms)} ms`;
}
// Producer warnings qualify the values they accompany (simulated provenance,
// inferred spectral units). Show them beside those values, not only in raw data.
function readingWarnings(r){return r.obs.warnings?.length?`<dt>Reading warnings</dt><dd>${r.obs.warnings.map(esc).join('<br>')}</dd>`:'';}
function renderReadingMetadata(r){
  $('detailTitle').textContent='Reading #'+(state.rows.indexOf(r)+1);
  $('detailTitle').title=rowName(r);
  const preview=displayPreview(r,$('previewGamut').value);
  if(['scalar','timing'].includes(r.obs.record_kind)){$('detailPreview').innerHTML=typedReadingValues(r);$('detailMetadata').innerHTML=`<dl>${readingWarnings(r)}<dt>Native method</dt><dd>${esc(r.obs.backend_reported.native_method)}</dd><dt>Acquired (UTC)</dt><dd>${esc(r.obs.acquired_at)}</dd><dt>Instrument</dt><dd>${esc(r.obs.device.model)}</dd>${r.obs.record_kind==='timing'?'<dt>Time reference</dt><dd>'+esc(r.obs.backend_reported.time_reference)+'</dd>':''}</dl><details><summary>Stimulus and original native record</summary><pre>${esc(JSON.stringify(r.obs,null,2))}</pre></details>`;delete $('detailMetadata').dataset.key;return;}
  $('detailPreview').innerHTML=`<p>${swatch(r,'details-swatch')} <span class="small">Approximate ${esc(targetName($('previewGamut').value))}${preview?.clipped_channels?.some(Boolean)?' · preview channels clipped':''}</span></p>`;
  const c=coordinate(r),xyz=r.obs.backend_reported.xyz;
  const metadataKey=JSON.stringify([r.id,$('coordinates').value,$('previewGamut').value,$('referenceGamut').value,r.saved,displayScale,projectionReply?.key,previewContextText(preview)]);
  if($('detailMetadata').dataset.key!==metadataKey){
    const open=[...$('detailMetadata').querySelectorAll('details[open]')].map(el=>el.id);
    $('detailMetadata').innerHTML=`${gamutDiagnostics(r)}<dl>${readingWarnings(r)}<dt>X / Y / Z</dt><dd>${xyz?xyz.values.map(v=>numberText(v)).join(' / '):'Unavailable: no native XYZ'}</dd><dt>Preview context</dt><dd>${esc(previewContextText(preview))}</dd><dt>XYZ unit</dt><dd>${esc(unitText(xyz?.unit||'Unavailable'))}</dd><dt>Scale</dt><dd>${esc(scaleText(xyz?.scale||'Unavailable'))}</dd><dt>${$('coordinates').value==='uvy'?'u′ / v′':'x / y'}</dt><dd>${c?c.slice(0,2).map(v=>fixedText(v,6)).join(' / '):esc(FIXTURES.readings[r.source].coordinate_reasons?.[$('coordinates').value]||'Unavailable: coordinates undefined')}</dd><dt>Observer</dt><dd>${esc(observerText(xyz?.observer||'Unavailable'))}</dd><dt>Illuminant</dt><dd>${esc(xyz?.illuminant||'Unavailable')}</dd><dt>Acquired (UTC)</dt><dd>${esc(r.obs.acquired_at)}<br>${esc(Object.fromEntries(r.obs.configuration).acquired_at_source)}</dd><dt>Instrument</dt><dd>${esc(r.obs.device.model)}</dd><dt>Serial</dt><dd>${esc(r.obs.device.serial||'Unavailable')}</dd><dt>Retained calibration</dt><dd>${esc(r.obs.calibration.status)}<br>${esc(r.obs.calibration.detail)}</dd><dt>Mode</dt><dd>${esc(r.obs.actual_mode)}</dd></dl><details id="nativeSpectrum"><summary>Native spectrum values</summary>${nativeSpectrumTable(r.obs.backend_reported.spectrum)}</details><details id="nativeData"><summary>Identity &amp; original native data</summary><p class="small">Reading ${esc(r.id)}<br>Operation ${esc(r.obs.operation_id)}</p><pre>${esc(JSON.stringify(r.obs,null,2))}</pre></details>`;
    $('detailMetadata').dataset.key=metadataKey;
    for(const id of open)document.getElementById(id).open=true;
  }
}
function renderEditorValues(r){
  if(!r||!editorTarget||!sameDataset(editorTarget)||editorTarget.readingId!==r.id||$('details').dataset.readingId!==r.id){
    for(const id of ['editName','editNotes','editSave','editCancel'])if($(id)){$(id).disabled=true;$(id).dataset.composing='false';}
    if($('annotationError')){$('annotationError').textContent='This reading is no longer available. Close the editor.';$('annotationError').hidden=false;}return;
  }
  const exact=appliedDetails.get(r.id);
  if(!exact||exact.version!==r.version){
    for(const id of ['editName','editNotes','editSave','editCancel'])if($(id)){$(id).disabled=true;$(id).dataset.composing='false';}
    if($('annotationStatus'))$('annotationStatus').textContent='Loading exact annotation text…';
    loadExactAnnotation(r);return;
  }
  r.notes=exact.notes;
  const draft=localEditValues.get(r.id)?.value||state.edits[r.id]||r;
  // Preserve editor nodes, caret and IME composition across analysis/status renders.
  // DOM value assignment also preserves a leading newline in textarea content.
  for(const [id,value] of [['editName',draft.name],['editNotes',draft.notes]]){
    if($(id).dataset.composing!=='true'&&!(queuedWrites&&document.activeElement===$(id))&&$(id).value!==value)$(id).value=value;
    $(id).disabled=annotationsLocked(r);
  }
  updateEditorStatus(r);
}
function updateEditorStatus(r){
  const changed=!!state.edits[r.id],locked=annotationsLocked(r),valid=!!$('editName').value.trim();
  $('editName').disabled=$('editNotes').disabled=locked;
  for(const id of ['editName','editNotes'])if($(id).disabled)$(id).dataset.composing='false';
  $('editSave').disabled=locked||queuedWrites>0||localEditErrors.has(r.id)||!ownerState?.can_apply_annotations||!changed||!valid;
  $('editCancel').disabled=locked||(!changed&&!localEditErrors.has(r.id));
  $('annotationStatus').textContent=r.inherited?'Original source reading · annotations are read only':localEditErrors.has(r.id)?'Text not accepted: '+localEditErrors.get(r.id):changed?valid?'Edits pending · Apply to save':'Sample name cannot be blank':r.saved?'Saved':'Not saved';
}
function nativeSpectrumTable(s) {
  if(!s)return '<p>Unavailable: no native spectrum.</p>';
  return `<div class="table-scroll"><table><thead><tr><th>nm</th><th>${esc(unitText(s.unit))}</th></tr></thead><tbody>${s.values.map((v,i)=>`<tr><td>${s.wavelengths_nm[i]}</td><td>${numberText(v)}</td></tr>`).join('')}</tbody></table></div>`;
}
function render() {
  pointerPreview=null;focusPreview=null;
  if(recordExportContext&&(!sameDataset(recordExportContext)||recordExportContext.dataset_revision!==FIXTURES.dataset_revision)){
    recordExportContext=null;clearRecordExportReceipt();$('saveRecord').disabled=true;
  }
  // Ordinary app panels leave the workspace live; only active editor nodes stay pinned.
  renderOwnerStatus();
  renderExportRecovery();
  renderAutomaticFiles();
  $('exportData').disabled=!datasetBinding?.id||!datasetBinding.available;
  const focused=document.activeElement,focusedId=focused?.id,renderStarted=performance.now();
  updateReference();
  const count=state.rows.length,selected=state.selected,latest=latestReadingRow(),visible=plottedRows(),ref=referenceReading();
  const analysis=requestAnalysis(),summary=analysis?.summary;
  const relativeContexts=[...new Set(state.rows.map(r=>displayPreview(r,$('previewGamut').value)).filter(p=>p?.display_rgb&&p.reference_unit==='1').map(previewContextText))];
  $('gamutSupport').textContent=`${targetName($('previewGamut').value)} previews. ${relativeContexts.length?relativeContexts.join('; ')+'. ':''}Absolute emissive previews and ideal RGB references use ${numberText(displayScale)} cd/m². ${p3Supported?'P3 CSS supported.':'P3 syntax unavailable; sRGB selected.'} ${matchMedia('(color-gamut: p3)').matches?'Browser reports P3 display support.':'Actual monitor gamut is not established by this browser.'} Browser/OS manages rendering; the selected reference is independent.`;
  renderGamut();
  const descriptor=displayDescriptor();
  $('sourceContext').textContent=datasetBinding?.kind==='standalone'?datasetBinding.source.device.model:descriptor?(descriptor.synthetic?'Demo · ':'')+descriptor.device.model:'No source selected';
  $('modeContext').textContent=descriptor?descriptor.modes.find(entry=>entry.mode===datasetBinding.source.mode)?.label||datasetBinding.source.mode:'';
  const empty=datasetBinding?.kind==='empty';
  $('batchTitle').textContent=empty?'Color Measurement':state.name;$('batchContext').hidden=empty;$('saveState').hidden=empty;
  $('batchBadge').textContent=retainedReview()?(datasetBinding.kind==='standalone'?'Imported record · read only':'Saved data · read only'):datasetBinding?.closed?'Closed · read only':state.finished?'Finished · read only':'Active batch';
  $('batchBadge').hidden=empty||datasetBinding?.kind==='live'&&!state.finished&&!datasetBinding.closed;
  $('sourceContext').title=$('sourceContext').textContent;$('modeContext').title=$('modeContext').textContent;
  $('emptyMeasurement').hidden=!empty;$('measureTools').hidden=empty;
  $('calState').textContent=latest?'Last reading calibration: '+latest.obs.calibration.status:'Calibration instructions appear when required';$('destState').textContent=state.destination||'Choose a batch destination';
  $('readingCount').textContent=count;$('outcomeCount').textContent=activityPanel.knownCount()??(datasetBinding?.kind==='live'?state.outcomes.length:datasetBinding?.kind==='standalone'?(datasetBinding.source.record_kind==='operation'?1:0):datasetBinding?.kind==='empty'?0:'See Activity');$('tableCount').textContent=count;
  // Cleanup and save can fail together; the details carry both owner reasons.
  const recoveryReason=(retainedReview()&&ownerState?'Live batch: '+(ownerState.batch_name||'Current batch')+'. ':'')+([ownerState?.cleanup_reason,ownerState?.save_reason].filter(Boolean).join(' ')||'Resolve retained work before another operation.')+(ownerState?.folder?' Folder: '+ownerState.folder:'');
  if($('recoveryReason').textContent!==recoveryReason)$('recoveryReason').textContent=recoveryReason;
  $('recoverySummary').textContent=(retainedReview()&&ownerState?'Live batch: '+(ownerState.batch_name||'Current batch')+'. ':'')+(ownerState?.cleanup_reason?'Review the cleanup details before continuing.'+(ownerState.save_reason?' Saving also did not complete.':''):'Saving did not complete. Retry save or choose another folder.');
  $('recoveryDestination').textContent=ownerState?.folder?'Folder: '+ownerState.folder.split(/[\\/]/).at(-1):'';
  $('recoveryDetails').querySelector('summary').textContent=ownerState?.cleanup_reason?(ownerState.save_reason?'Cleanup and save details':'Cleanup details'):'Save details';
  if(!state.unsaved)$('recoveryDetails').open=false;
  $('recovery').hidden=!state.unsaved;$('follow').checked=state.follow;
  const recoveryTitle=ownerState?.cleanup_reason?'Operation cleanup needs attention':'Save failed';
  if($('recoveryTitle').textContent!==recoveryTitle)$('recoveryTitle').textContent=recoveryTitle;
  $('closeRecovery').hidden=!ownerState?.cleanup_reason;
  $('retry').hidden=$('another').hidden=!!ownerState?.cleanup_reason&&!ownerState?.save_reason;
  $('previewScale').disabled=$('applyScale').disabled=!state.batch;
  renderWorkflowControls();renderSampleControls();updatePresentation();
  $('cancel').disabled=demoLoading||!ownerState?.operation_id||!['starting','active'].includes(ownerState.phase);$('newBatch').disabled=!!ownerState&&ownerState.status==='open';
  $('returnLive').hidden=!retainedReview();$('openRecord').disabled=$('openSaved').disabled=!!state.busy||demoLoading||blocked;
  $('datasetNotice').hidden=!retainedReview()||datasetBinding.available;
  document.querySelector('.control-dock').hidden=retainedReview();
  document.querySelector('.app-shell').classList.toggle('saved-review',retainedReview());
  $('datasetMessage').textContent=datasetBinding&&!datasetBinding.available?'Saved source unavailable: '+datasetBinding.reason:'Inspecting saved data. Return to your live batch to measure.';
  const exclusions=datasetBinding?.review?.excluded_operations||[];
  $('datasetExclusions').hidden=!exclusions.length;$('datasetExclusions').textContent=exclusions.map(item=>'Excluded operation: '+item.state+' · '+(item.reason||'No reason returned')).join('\n');
  $('datasetNotice').hidden=!retainedReview()||(datasetBinding.available&&!exclusions.length);
  const original=datasetBinding?.kind==='standalone'?datasetBinding.source:null;
  $('originalRecordNotice').hidden=!original?.operation_status||(original.operation_status==='completed'&&!original.operation_reason&&original.software_stopped&&count>0);
  if(original?.operation_status){$('originalRecordStatus').textContent='Original operation: '+original.operation_status;$('originalRecordReason').textContent=original.operation_reason?' · '+original.operation_reason:'';$('originalRecordFacts').textContent='Software stop: '+(original.software_stopped?'confirmed':'unconfirmed')+' · Recorded instrument state: '+original.instrument_state;$('originalRecordNotice').classList.toggle('attention',original.operation_status==='failed'||original.software_stopped===false);}
  $('latest').innerHTML=latest?latestReading(latest):'No reading yet.';
  const attempt=state.outcomes.at(-1),attemptKnown=attempt?.accepted;
  $('lastAttempt').hidden=!attempt||state.finished||['starting','active'].includes(state.busy);
  $('cancel').hidden=!state.busy;
  $('demoToggle').hidden=retainedReview()||!!ownerState&&!ownerState.descriptor.synthetic;
  $('demoBar').hidden=$('demoToggle').hidden;
  $('demoControls').hidden=$('demoToggle').hidden||$('demoToggle').getAttribute('aria-expanded')!=='true';
  for(const id of ['demo40','demo160']){$(id).disabled=datasetBinding?.kind!=='live'||!!ownerState?.chart||demoLoading||!ownerState?.can_measure||blocked||localEditErrors.size>0;$(id).title=ownerState?.chart?'Load individual readings in an individual-reading Demo batch.':'';}
  const chartDemoReason=demoChartUnavailable();$('demoChart').disabled=!!chartDemoReason;$('demoChart').title=chartDemoReason||'Create an empty simulated batch, then choose Classic or Digital SG.';
  $('stopDemoLoad').hidden=!demoLoading;
  if(!demoLoading&&document.activeElement===$('stopDemoLoad'))$('demoToggle').focus({preventScroll:true});
  const calibrationOutcome=attempt?.workflow==='calibrate'?({completed:'Calibration completed.',failed:'Calibration failed. See Activity.',cancelled:'Calibration cancelled.'}[attempt.status]):null;
  const attemptText=!attempt?'':!attemptKnown?'Outcome unavailable. See Activity.':calibrationOutcome||
    (attempt.status==='completed'?'Recorded':attempt.status==='cancelled'?'Cancelled':attempt.status==='failed'?'Measurement failed':attempt.status)+
    ' · '+attempt.sample_count+' reading'+(attempt.sample_count===1?'':'s')+' retained'+(attempt.reason?'. See Activity.':'.');
  if($('lastAttempt').textContent!==attemptText)$('lastAttempt').textContent=attemptText;
  $('lastAttempt').classList.toggle('attempt-failed',!!attempt&&(!attemptKnown||attempt.status==='failed'));
  $('selectionState').textContent=selected?`${state.follow?'Following':'Inspecting'} ${rowName(selected)}`:'No inspected reading';
  for(const tool of TOOLS){if(tool!=='measure')$(tool+'Tools').hidden=tool!==state.tool;$(tool+'Tab').setAttribute('aria-pressed',String(tool===state.tool));$(tool+'Tab').tabIndex=0;}
  $('selectionKey').innerHTML=selectionKey();
  $('selectionKey').hidden=!count;document.querySelector('.reading-heading-tools').hidden=empty;
  $('showDetails').closest('label').hidden=!selected;
  $('selectionState').hidden=!selected;
  $('plotInspected').disabled=!selected;$('restorePlotted').hidden=!state.previousPlotted;$('scaleToWhite').disabled=!declaredWhite();
  $('whiteScaleReason').textContent=declaredWhite()?'Uses declared Y without changing the white.':!state.white?'Declare a Lab reference white in Color first.':currentProjection()?.error||'Waiting for the declared white projection.';
  const fitTarget=$('referenceGamut').value;
  $('scaleToChecked').textContent=fitTarget==='none'?'Choose a reference gamut to fit':`Fit checked ${targetName(fitTarget)} RGB range`;
  $('scaleToChecked').disabled=fitTarget==='none'||!visible.some(r=>displayPreview(r,fitTarget)?.gamut_status?.fit_positive_rgb_scale>0);
  $('zoomIn').disabled=zoom>=256;$('zoomOut').disabled=zoom<=.5;
$('referenceChoiceSummary').textContent=referenceTargets().map(targetName).join(' + ');
  $('referenceReading').textContent=ref?rowName(ref):selectedComparison()?.reference?'Reference reading unavailable':'No reference reading';$('referenceReading').title=ref?rowName(ref):'';$('useReference').disabled=!canChangeComparison()||blocked||!selected;
  $('comparisonSaveState').textContent=selectedComparison()?.available===true&&selectedComparison()?.persisted===false?'Reference and white apply to this review. They are retained in analysis exports; source files stay unchanged.':datasetBinding?.kind==='saved'?(selectedComparison()?.persisted?'Saved reference and white. Changes apply to this review.':'Reference and white apply to this review; source files stay unchanged.'):!ownerState?'':selectedComparison()?.saved?'':ownerState.phase==='saving'?'Saving comparison settings…':'Save comparison settings before using analysis.';
  const range=summary?.rms_range;
  $('comparisonContext').hidden=!selectedComparison()?.reference&&!state.white;
  $('comparisonContext').textContent=(ref?'Compare to '+rowName(ref):selectedComparison()?.reference?'Reference unavailable':'')+(state.white?(ref?' · ':'')+'White: '+whiteLabel():'')+(datasetBinding?.kind==='live'&&selectedComparison()?.saved===false?(selectedComparison()?.available===true?' · Review context':' · Not saved'):'');
  $('comparisonContext').title=$('comparisonContext').textContent+' · Open comparison settings';
  $('clearReference').disabled=!canChangeComparison()||blocked||!selectedComparison()?.reference;
  $('copyComparison').disabled=!datasetBinding?.available||!ref||!analysis||!!analysis.error;
  $('signedComparison').innerHTML=!ref||!analysis||analysis.error?'':signedMetrics.map(group=>group.labels.map((label,index)=>comparisonRange(label,group.labels.length===1?summary?.[group.metric+'_range']:summary?.[group.metric+'_range']?.[index],summary?.[group.metric+'_unavailable'])).join('')).join('');
  $('xyzComparison').innerHTML=!ref?'':analysis?.error?`<p class="dialog-error">${esc(analysis.error)}</p>`:comparisonRange('Y change (%)',summary?.y_percent_range,summary?.y_unavailable)+comparisonRange('Δu′v′ · coordinate distance',summary?.uv_distance_range,summary?.uv_unavailable);
  $('comparison').innerHTML=!ref?'<p class="muted small">Inspect a reading, then set it as the reference.</p>':!visible.some(r=>r!==ref)?'<p class="muted small">Check readings to display spectral overlays.</p>':range?`<div class="metric">Spectral RMS${range.count===1?'':' range'}<strong>${range.min===range.max?numberText(range.max):numberText(range.min)+' – '+numberText(range.max)}</strong><span>${esc(unitText(range.unit))} · ${range.count} comparisons to #${state.rows.indexOf(ref)+1}${summary.spectral_unavailable?' · '+summary.spectral_unavailable+' unavailable':''}</span></div>`:`<p class="muted small">${esc(analysis?.error||(analysis?.summary?.spectral_unavailable?'Checked spectra unavailable; inspect their conditions.':'Calculating checked spectra…'))}</p>`;
  renderChart();
  if(!state.plotSlotsSet&&chartView){state.plotSlots=['chart',spectrumAvailable()?'spectrum':'chromaticity'];state.plotSlotsSet=true;}
  if(!state.plotSlotsSet&&spectrumAvailable()!==null){
    state.plotSlots=spectrumAvailable()?['spectrum','chromaticity']:['space','chromaticity'];
    if(state.rows.length)state.plotSlotsSet=true;
  }
  const plotIds=visiblePlotIds();
  $('plotFrame').hidden=plotIds.length===0;
  for(const id of Object.keys(plotTitles))$(id).hidden=!plotIds.includes(id);
  $('plotFrame').classList.toggle('single-plot',plotIds.length!==2);
  $('plotFrame').style.gridTemplateColumns=plotIds.length===2?'minmax(0,1fr) minmax(0,1fr)':'1fr';
  const panelOrder=[...plotIds,...Object.keys(plotTitles).filter(id=>!plotIds.includes(id))];
  panelOrder.forEach((id,index)=>{
    const frame=$('plotFrame'),panel=$(id);panel.style.order='';
    if(frame.children[index]!==panel)frame.insertBefore(panel,frame.children[index]||null);
  });
  $('plotFrame').classList.toggle('is-expanded',expandedPlots.size>0);
  $('plotLayout').value=state.plotLayout==='overview'?'overview':state.plotLayout==='table'?'table':'single';
  for(const choice of document.querySelectorAll('[data-plot-choice]')){
    choice.value=choice.dataset.plotChoice;
    const option=choice.querySelector('[value="spectrum"]');option.disabled=spectrumAvailable()===false;
    option.textContent=option.disabled?'Spectrum · unavailable':'Spectrum';
    const guide=choice.querySelector('[value=chart]');guide.hidden=guide.disabled=!chartView;
    const residual=choice.querySelector('[value=residual]');residual.disabled=spectrumAvailable()===false;
    residual.textContent=residual.disabled?'Spectral residuals · unavailable':'Spectral residuals';
  }
  $('spectrumPlot').innerHTML=spectralPlot(visible);
  $('spectrumContext').textContent=`Native samples · ${$('wavelengthStyle').value!=='off'?'Approximate CIE wavelength guide · ':''}${$('curveContrast').checked?'contrast-styled curves':'calculated preview colors'}`;
  const spectrumUnits=[...new Set(visible.filter(r=>r.obs.backend_reported.spectrum).map(r=>unitText(r.obs.backend_reported.spectrum.unit)))];
  $('spectrumLabel').textContent=spectrumUnits.join(' / ');$('spectrumLabel').hidden=!spectrumUnits.length;
  $('chromaticityTitle').textContent='Chromaticity';const chromaticityMarkup=chromaticityPlot();if(heldChromaticityField)heldChromaticityField.remove();$('chromaticityPlot').innerHTML=chromaticityMarkup;if(heldChromaticityField)$('chromaticityPlot').querySelector('.chromaticity-stage').prepend(heldChromaticityField);heldChromaticityField=null;paintChromaticityRaster();
  $('readingPanel').hidden=false;
  const residuals=visible.filter(r=>r!==ref&&analysis?.comparison_by_source?.[r.source]?.residual).map(r=>({r,s:analysis.comparison_by_source[r.source].residual}));
  renderTableColumns();
  updatePlotLayout();
  $('residualPlot').innerHTML=residuals.length?spectralPlot([],residuals):'<div class="empty">Set a reference in Analysis › Compare, then check compatible spectra in the table.</div>';
  $('rmsValue').textContent=residuals.length?`${residuals.length} residual${residuals.length===1?'':'s'} · ${unitText(range.unit)}`:'';
  const whiteRow=state.rows.find(r=>matchesReading(state.white,r)),whiteName=state.white?.kind==='theoretical-lab-reference'?state.white.name:whiteRow?'white #'+(state.rows.indexOf(whiteRow)+1):'white unavailable';
  $('deltaHeading').textContent='ΔE00 · '+whiteName; $('deltaHeading').title='To '+(ref?rowName(ref):'no reference')+' under '+whiteLabel();
  const matched=matchedRows();
  $('showSelected').hidden=!selected||matched.includes(selected);$('showSelected').title=selected?rowName(selected):'';
  $('filterCount').textContent=`${matched.length} of ${count} readings · ${state.plotted.size} checked`;
  const checked=matched.filter(r=>state.plotted.has(r.id)).length;
  $('plotAll').checked=matched.length>0&&checked===matched.length;$('plotAll').indeterminate=checked>0&&checked<matched.length;$('plotAll').disabled=!matched.length;
  $('rows').innerHTML=matched.map(r=>{const i=state.rows.indexOf(r),status=referenceStatus(r),c=coordinate(r),delta=analysis?.delta_e_by_source?.[r.source],checked=state.plotted.has(r.id);
    return `<tr data-reading="${r.id}" class="${r===selected?'selected-row':''} ${checked?'':'not-plotted'}"><td class="plot-check-cell"><input id="plot-${r.id}" type="checkbox" data-plot="${r.id}" ${checked?'checked':''} aria-label="Plot reading ${i+1}: ${esc(r.name)}"></td><td class="reading-index">${i+1}${comparisonActive()&&r===ref?'<span class="reference-tag">Ref</span>':''}</td><td><div class="sample-actions"><button id="row-${r.id}" data-inspect="${r.id}" aria-pressed="${r===selected}" title="${esc(r.name)}"><span class="sample-label">${esc(r.name)}</span></button>${sampleFlags(r)}<button id="edit-row-${r.id}" class="row-edit" data-edit="${r.id}" data-field="name" aria-label="Edit name and notes for reading ${i+1}" ${annotationsLocked(r)?'disabled':''}>Edit</button></div></td>${tableCells(r)}</tr>`;
  }).join('');
  $('rows').querySelectorAll('[data-plot]').forEach(input=>input.onchange=()=>{input.checked?state.plotted.add(input.dataset.plot):state.plotted.delete(input.dataset.plot);render();$('plot-'+input.dataset.plot)?.focus();});
  $('rows').querySelectorAll('[data-edit]').forEach(button=>{button.onclick=()=>editReading(button.dataset.edit,button.dataset.field);if(button.classList.contains('row-edit')){button.innerHTML=icon('edit');button.title='Edit name and notes';}});
  const activeRow=matched.find(r=>r===selected)||matched[0];
  $('rows').querySelectorAll('tr').forEach(tr=>tr.querySelectorAll('button,input').forEach(control=>control.tabIndex=tr.querySelector('[data-plot]')?.dataset.plot===activeRow?.id?0:-1));
  $('rows').onkeydown=event=>{if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;const tr=event.target.closest('tr'),current=matched.findIndex(r=>r.id===tr?.querySelector('[data-plot]')?.dataset.plot),next=event.key==='Home'?0:event.key==='End'?matched.length-1:Math.max(0,Math.min(matched.length-1,current+(event.key==='ArrowDown'?1:-1)));if(matched[next]){event.preventDefault();inspect(matched[next].id,'row-'+matched[next].id);}};
  $('noRows').hidden=!!matched.length;
  $('emptyReadingsMessage').textContent=count?'No readings match these filters.':retainedReview()?'This source contains no color, scalar or timing readings. Its original record remains available for export.':state.batch?state.finished?'This finished batch has no available readings.':'No readings yet. '+(chartView&&datasetBinding?.kind==='live'?chartMeasurementLabel():'Measure '+(ownerState?.draft.name||'the next sample'))+'.':'Readings will appear here.';
  $('startBatch').hidden=state.batch;
  $('outcomes').textContent='Inspect completed, failed and cancelled outcomes, including operations without readings.';
  activityPanel.refresh();
  sciencePanel.checkCurrent();renderOperatorForms();renderDrafts();renderWhite();renderColor();renderDetails();renderSpace();$('retryAnalysis').hidden=!analysis?.error&&!spaceError;$('retryAnalysis').textContent=spaceError?'Retry analysis / 3D':'Retry analysis';bindInspection();
  $('workspace').dataset.renderMs=(performance.now()-renderStarted).toFixed(2);
  if(focusedId&&(!focused.isConnected||focused.closest('#plotFrame')&&document.activeElement!==focused)){
    const replacement=$(focusedId);if(replacement&&!replacement.disabled&&!replacement.closest('[hidden]'))replacement.focus({preventScroll:true});
  }
  paintReadingPreview();
}
function selectLatest() { state.follow=true; state.selected=latestReadingRow();render();scrollSelected(); }
function queueSampleAction(work,errorContext=null){
  closeMenus();
  if(equipmentEffectPending()){showStatusError(Error('Wait for device setup or check its delayed result before changing acquisition.'),errorContext);return writes;}
  if(demoLoading){showStatusError(Error('Wait for Demo loading to stop before changing acquisition.'),errorContext);return writes;}
  if(sampleCompositionPending()){showStatusError(Error('Finish entering the sample text before continuing.'),errorContext);return writes;}
  if(sampleActionPending)return writes;
  sampleActionPending=true;clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer);renderSampleControls();
  return enqueue(async()=>{
    if(blocked||localEditErrors.size)throw Error('Resolve refused text, or check the delayed action with Reload, before continuing.');
    await work();
  },null,null,errorContext).finally(()=>{sampleActionPending=false;renderSampleControls();renderOperatorForms();});
}
function begin(kind) { return queueSampleAction(async()=>{
  await saveSampleDraft();
  await waitMetadata();
  if(kind==='Calibrate'&&!ownerState.can_calibrate)throw Error('Calibration is unavailable in the current instrument state.');
  const draft=ownerState.draft;
  const base={context_id:ownerState.context_id,draft_id:draft.id,draft_version:draft.version};
  const workflow=selectedWorkflow();
  if(kind==='Measure'&&ownerState.chart){await send('measure_chart',{...base,chart_revision:ownerState.chart.revision});}
  else if(kind==='Measure'&&workflow&&workflow.workflow!=='spot'){
    try{
      const layout=collectWorkflowFields($('workflowFields'),workflow);
      const patch_ids=workflow.max_patches===0?[]:workflow.max_patches===1?[draft.id]:$('workflowPatchIds').value.split(/\r?\n/u).map(value=>value.trim()).filter(Boolean);
      if(patch_ids.length<workflow.min_patches||patch_ids.length>workflow.max_patches||new Set(patch_ids).size!==patch_ids.length)throw Error(`Enter ${workflow.min_patches} to ${workflow.max_patches} distinct sample IDs, one per returned position.`);
      $('workflowError').hidden=true;
      await send('run_workflow',{...base,workflow:workflow.workflow,patch_ids,layout});
    }catch(error){$('workflowError').textContent=error.message;$('workflowError').hidden=false;throw error;}
  }else await send(kind==='Measure'?'measure':'calibrate',base);
}); }
function cancel(binding=ownerState) {
  const active=binding?.operations.find(item=>item.operation_id===binding.operation_id);
  if(!active)return writes;
  const target={batch_id:binding.batch_id,generation:binding.generation,operation_id:binding.operation_id,command_id:active.command_id};
  const current=()=>ownerState?.batch_id===target.batch_id&&ownerState.generation===target.generation&&ownerState.operation_id===target.operation_id&&ownerState.operations.some(item=>item.operation_id===target.operation_id&&item.command_id===target.command_id);
  if(current())stopPresentation();
  return enqueue(async()=>{if(!current()||ownerState.operation_terminal)return;await send('cancel',{command_id:target.command_id},target);});
}
function recover(other=false,files=false) {
  const binding=ownerState,fence=datasetFence();
  const attention=automaticFileAttention();
  const actions=files?[...(attention.some(([kind,item])=>kind!=='ti3'&&item.retryable)?['retry_companions']:[]),...(attention.some(([kind])=>kind==='ti3')?['export_ti3']:[])]:['retry_save'];
  return enqueue(async()=>{
  if(!sameDataset(fence)||datasetBinding?.kind!=='live')throw Error('The workspace changed. Return to live and retry saving there.');
  if(other){const outcome=await call('another_folder');if(outcome&&outcome.admission!=='admitted')throw Error(outcome.reason);}
  else for(const action of actions)await send(action,{},binding);
  await refresh();
}); }
function selectedWorkflow(){return ownerState?.workflows?.find(item=>item.workflow===$('workflowChoice').value)||null;}
function availableMeasurementTask(item){return item.available&&!['calibrate','strip'].includes(item.workflow);}
function renderWorkflowControls(){
  const workflows=(ownerState?.workflows||[]).filter(availableMeasurementTask);
  const key=JSON.stringify([ownerState?.context_id,workflows]);
  if($('workflowChoice').dataset.key!==key){
    const current=$('workflowChoice').value;
    $('workflowChoice').replaceChildren(...workflows.map(item=>{const option=document.createElement('option');option.value=item.workflow;option.textContent=workflowLabels[item.workflow]||item.workflow;return option;}));
    if(workflows.some(item=>item.workflow===current))$('workflowChoice').value=current;
    else if(workflows.some(item=>item.workflow==='spot'))$('workflowChoice').value='spot';
    $('workflowChoice').dataset.key=key;workflowFieldsKey='';
  }
  const workflow=selectedWorkflow(),fieldKey=JSON.stringify([ownerState?.context_id,workflow]);
  if(workflowFieldsKey!==fieldKey){
    $('workflowFields').replaceChildren();
    if(workflow)renderWorkflowFields($('workflowFields'),workflow);
    workflowFieldsKey=fieldKey;$('workflowError').hidden=true;
  }
  $('workflowControls').hidden=state.finished||!workflows.length||!!ownerState?.chart;
  $('workflowChoice').disabled=sampleActionPending||!!state.busy||demoLoading;
  $('workflowHint').textContent=workflow?.instruction||(workflow?.workflow==='mode_snapshot'?'Checks which settings are available in this mode. After a successful check, finish this batch and create a new batch to use the refreshed settings.':workflow?.workflow==='xy'||workflow?.workflow==='chart'?'Enter the target geometry and returned-position sample IDs.':'');
  $('workflowPatchLabel').hidden=!workflow||workflow.max_patches<=1;
  $('workflowPatchHint').textContent=workflow?`${workflow.min_patches} to ${workflow.max_patches} distinct IDs. Their count must match the target geometry.`:'';
  $('taskContext').textContent=datasetBinding?.kind==='standalone'?'Imported record':chartView||datasetBinding?.kind==='live'&&ownerState?.chart?'Guided chart':workflow&&workflow.workflow!=='spot'?(workflowLabels[workflow.workflow]||workflow.workflow):'Individual readings';
}
$('workflowChoice').onchange=()=>{renderWorkflowControls();renderSampleControls();};
function stopPresentation(){presentationEpoch++;presentationController?.abort();presentationController=null;$('presentationSurface').hidden=true;for(const [node,value] of presentationInert)node.inert=value;presentationInert=[];if(presentationInvoker?.isConnected)presentationInvoker.focus();presentationInvoker=null;}
function updatePresentation(){
  const pending=ownerState?.presentation;
  const active=pending&&datasetBinding?.kind==='live'&&ownerState.phase==='active'&&!ownerState.cancel_requested;
  if(!active){const timing=ownerState?.operations.find(item=>item.operation_id===ownerState.operation_id)?.workflow==='display_timing';if(!pending&&timing&&datasetBinding?.kind==='live'&&ownerState.phase==='active'&&!ownerState.cancel_requested)return;stopPresentation();presentationKey='';return;}
  const key=JSON.stringify([ownerState.batch_id,ownerState.generation,pending.operation_id,pending.request_id]);
  if(key===presentationKey)return;
  stopPresentation();presentationKey=key;const epoch=presentationEpoch,binding=ownerState;
  const controller=new AbortController();presentationController=controller;
  presentationInvoker=document.activeElement;presentationInert=[...document.body.children].filter(node=>node!==$('presentationSurface')).map(node=>[node,node.inert]);for(const [node] of presentationInert)node.inert=true;
  drawPresentation($('presentationSurface'),pending,{signal:controller.signal}).then(stamp=>{
    if(controller.signal.aborted||epoch!==presentationEpoch||presentationKey!==key||document.visibilityState==='hidden')return;
    return send('presented',{operation_id:pending.operation_id,request_id:pending.request_id,stamp:{...stamp,clock_id:presentationClock}},binding);
  }).catch(error=>{if(controller.signal.aborted)return;stopPresentation();showStatusError(error);cancel(binding);});
}
$('cancelPresentation').onclick=()=>cancel();
for(const event of ['pagehide','blur'])window.addEventListener(event,()=>{if(presentationController){stopPresentation();cancel();}});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&presentationController){stopPresentation();cancel();}});
function chartBinding(){return {batch_id:ownerState.batch_id,generation:ownerState.generation,context_id:ownerState.context_id};}
function loadSelectedChart(pass_id=null){
  chartViewedPass=pass_id;
  const fence=datasetFence(),key=JSON.stringify([fence,datasetBinding.roster_revision,pass_id]);
  if(key===chartViewKey)return;
  chartViewKey=key;const request=++chartRequest;
  dataCall('chart_view',{pass_id}).then(reply=>{if(request!==chartRequest||!sameDataset(fence)||!datasetBinding.available)return;const next=reply.value||null,inspected=chartView?.viewed_pass===next?.viewed_pass?chartView?.inspection_cell:null;chartView=next;if(chartView&&inspected!==null&&chartView.cells.some(cell=>cell.index===inspected))chartView={...chartView,inspection_cell:inspected};render();}).catch(error=>{if(request!==chartRequest||!sameDataset(fence)||!datasetBinding.available)return;chartView=null;if(!error.message.includes('no guided chart'))showStatusError(error);render();});
}
function renderChart(){
  $('sampleTargetFields').hidden=state.finished||!!chartView;
  const view=chartView,live=datasetBinding?.kind==='live',mutable=live&&ownerState?.phase==='idle'&&!ownerState.save_reason&&!blocked;
  $('setupChart').disabled=!live||!ownerState||!!ownerState.chart||ownerState.operations.length>0||!ownerState.workflows?.some(item=>item.workflow==='spot'&&item.available);
  $('chartTaskControls').hidden=!view||!live;
  if(!view){if(state.plotSlots.includes('chart')){state.plotSlots=state.plotSlots.map(kind=>kind==='chart'?((spectrumAvailable()===false?['space','chromaticity']:['spectrum','chromaticity','space']).find(value=>!state.plotSlots.includes(value))):kind);if(state.plotLayout==='chart')state.plotLayout='overview';}return;}
  const target=view.cells.find(cell=>cell.index===view.current_cell),inspected=view.cells.find(cell=>cell.index===view.inspection_cell);
  $('chartProgress').textContent=`${view.coverage} / ${view.total}`;
  $('chartTargetLabel').textContent=`Next patch: ${target.coordinate}`;
  $('chartTargetLabel').hidden=!view.paused;
  renderChartGuide($('chartGuide'),view,{onInspect:cell=>inspectChartCell(cell)});requestChartColors();paintChartColors();
  const inspectKey=view.definition.id;
  if($('chartInspect').dataset.key!==inspectKey){$('chartInspect').replaceChildren(...view.cells.map(cell=>{const option=document.createElement('option');option.value=String(cell.index);option.textContent=cell.coordinate+(cell.patch_name?' · '+cell.patch_name:'');return option;}));$('chartInspect').dataset.key=inspectKey;}
  $('chartInspect').value=String(view.inspection_cell);$('chartInspect').disabled=!datasetBinding.available;
  if(![...$('chartPass').options].some(option=>option.value===view.viewed_pass)){
    const option=document.createElement('option');option.value=view.viewed_pass;option.textContent='Current pass';$('chartPass').append(option);
  }
  $('chartPass').value=view.viewed_pass;$('chartOrientation').value=String(view.orientation);
  $('chartOrientation').disabled=!mutable;$('chartTarget').disabled=!mutable||view.finished||view.viewed_pass!==view.active_pass;
  $('chartTarget').textContent='Target '+inspected.coordinate;
  $('chartNewPass').disabled=!mutable||view.finished||view.paused||!!view.pending_position;
  $('chartPause').disabled=!mutable||view.finished;$('chartPause').textContent=view.paused?'Resume chart':'Pause chart';
  $('finishChart').disabled=!mutable||view.finished||!!view.pending_position;
  $('chartHistory').disabled=!datasetBinding.available;$('expectedReferenceControls').hidden=view.definition.kind!=='ti2';$('retryChartColors').hidden=!chartColorError;
  $('chartDecision').hidden=!view.pending_position&&!view.pending_context;
  $('chartDecisionReason').textContent=view.pending_position?.reason||view.pending_context?.reason||'';
  $('chartPlacementActions').hidden=!view.pending_position;$('chartAcceptContext').hidden=!view.pending_context;$('chartContextDetails').hidden=!view.pending_context;
  $('chartAcceptContext').disabled=!mutable;
  for(const button of $('chartPlacementActions').querySelectorAll('button'))button.disabled=!mutable;
  $('chartStatus').textContent=view.finished?'Chart finished':view.paused?'Chart paused':view.pending_position?'Resolve the placement check before measuring the next patch.':view.pending_context?'Review changed settings before starting another pass.':chartColorError||(chartColors?.measured.length&&!chartColors.measured.some(item=>item.color.available)?'Color preview unavailable for these readings.':'');
}
// A preview depends on its own reading, colour space and expected context, not on
// the chart revision; an unchanged selection keeps its face while a reply is pending.
function chartColorBasisKey(view){return JSON.stringify([datasetFence(),view.definition.id,view.viewed_pass,$('previewGamut').value,expectedChartContext]);}
function requestChartColors(){
  if(!chartView)return;
  const fence=datasetFence(),view=chartView,color_space=$('previewGamut').value,basis=chartColorBasisKey(view);
  const key=JSON.stringify([fence,datasetBinding.roster_revision,view.definition.id,view.revision,view.viewed_pass,color_space,expectedChartContext]);
  if(key===chartColorKey)return;chartColorKey=key;chartColors=null;chartColorError='';
  dataCall('chart_colors',{pass_id:view.viewed_pass,color_space,expected_context:expectedChartContext}).then(async reply=>{
    if(!sameDataset(fence)||!datasetBinding.available||key!==chartColorKey||!chartView)return;const colors=reply.value;
    // A live query can reach the owner after its revision advances but before
    // the renderer receives that snapshot. Refresh the witness once; never
    // paint the newer reply onto the older chart or hide a refused refresh.
    if(datasetBinding.kind==='live'&&view.viewed_pass===ownerState?.chart?.active_pass&&colors.definition_id===view.definition.id&&colors.pass_id===view.viewed_pass&&typeof colors.source_revision==='string'&&Number.isSafeInteger(colors.chart_revision)&&colors.chart_revision>view.revision){
      await refresh(true);
      if(!sameDataset(fence)||!datasetBinding.available||key!==chartColorKey||!chartView)return;
    }
    if(colors.definition_id!==chartView.definition.id||colors.chart_revision!==chartView.revision||colors.pass_id!==chartView.viewed_pass||typeof colors.source_revision!=='string')throw Error('Chart color source changed. Refresh the chart.');
    chartColors=colors;chartColorBasis={key:basis,colors,selected:new Map(chartView.cells.map(cell=>[cell.index,cell.selected_attempt]))};renderChart();
  }).catch(error=>{if(!sameDataset(fence)||key!==chartColorKey)return;chartColorBasis=null;chartColorError=error.message;renderChart();});
}
function paintChartColors(){
  if(!chartView)return;
  const carried=!chartColors&&!chartColorError&&chartColorBasis?.key===chartColorBasisKey(chartView)?chartColorBasis:null;
  const colors=chartColors||carried?.colors;
  const measured=new Map((colors?.measured||[]).map(item=>[item.cell_index,item]));
  const expected=new Map((colors?.expected.cells||[]).map(item=>[item.index,item]));
  let inspection=null;
  for(const button of $('chartGuide').querySelectorAll('[data-chart-cell]')){
    const index=Number(button.dataset.chartCell),cell=chartView.cells.find(cell=>cell.index===index);
    if(!cell)continue;
    const kept=cell.measured,settled=!!chartColors||!!carried&&(!kept||carried.selected.get(index)===cell.selected_attempt);
    const native=settled?measured.get(index):undefined,reference=settled?expected.get(index):undefined;
    const item=kept?(native?.color.available?native:null):(reference?.color.available?reference:null);
    const nominal=!kept&&!item?nominalChartColor(chartView,index):null;
    const loading=!settled&&!chartColorError;
    if(index===chartView.inspection_cell)inspection={cell,kept,native,reference,loading,expected:settled?colors?.expected:null};
    const kind=item?(kept?'measured':'expected'):nominal?'nominal':loading?'loading':'unavailable';
    button.style.backgroundColor='';button.style.color='';button.dataset.chartColorKind=kind;
    const target=cell.coordinate+(cell.patch_name?' ('+cell.patch_name+')':'');
    let description;
    if(item){const encoding=item.color.encoding;button.style.backgroundColor=previewCss(encoding.display_rgb);button.style.color='var(--ink)';description=(kept?'Measured color preview':'Reference color preview')+(encoding.clipped_channels.some(Boolean)?' · Preview channels clipped':'')+' · common reference white Y=100';}
    else if(nominal){button.style.backgroundColor=`rgb(${nominal.join(' ')})`;description='Nominal location guide · supplied layout illustration';}
    else description=chartColorError||(kept?(native?.color.reason||(loading?'Preparing measured color preview':'Measured color preview unavailable')):(reference?.color.reason||chartColors?.expected.reason||(loading?'Preparing reference preview':'Reference color preview unavailable')));
    button.title=target+' · '+description;button.setAttribute('aria-description',description);
    labelChartCell(button,cell,kind);
  }
  renderChartInspection(inspection);
}
// Expected and measured values of the inspected patch, side by side, each with
// its own white, source and preview clipping. Values come from the chart color
// reply as the producers retained them; nothing is converted here.
function renderChartInspection(inspection){
  $('chartInspection').hidden=!inspection;if(!inspection)return;
  const {cell,kept,native,reference,loading,expected}=inspection,row=reference?.expected,context=expected?.context;
  $('chartInspectionSummary').textContent='Inspected '+cell.coordinate+(cell.patch_name?' · '+cell.patch_name:'');
  const triple=values=>Array.isArray(values)&&values.length===3?values.map(numberText).join(' · '):'—';
  const readingRef=native?.source?.reading_ref;
  const reading=readingRef&&state.rows.find(row=>{
    const actual=readingIdentity(row);
    return actual?.operation_id===readingRef.operation_id&&actual?.observation_id===readingRef.observation_id&&
      (actual.segment_id??chartView.segment_id)===readingRef.segment_id&&row.obs.sample_id===readingRef.sample_id;
  });
  const expectedReason=chartColorError||row?.reason||expected?.reason||(loading?'Preparing reference values':'Expected values unavailable');
  const measuredReason=!kept?'Not measured':chartColorError||native?.reason||(loading?'Preparing measured values':'Measured values unavailable');
  const preview=(item,reason)=>{
    if(!item?.color?.available)return item?.color?.reason||reason;
    const encoding=item.color.encoding,swatch=document.createElement('span'),text=document.createElement('span');
    swatch.className='chart-inspection-swatch';swatch.style.backgroundColor=previewCss(encoding.display_rgb);swatch.setAttribute('aria-hidden','true');
    text.textContent=encoding.clipped_channels.some(Boolean)?'Preview channels clipped':'No preview channel clipped';
    const node=document.createElement('span');node.append(swatch,text);return node;
  };
  const hasExpected=!!row?.available,hasMeasured=kept&&!!native?.available;
  const rows=[
    ['Preview',hasExpected?preview(reference,expectedReason):expectedReason,hasMeasured?preview(native,measuredReason):measuredReason],
    ['XYZ',hasExpected?triple(row.XYZ):'—',hasMeasured?triple(native.xyz):'—'],
    ['Lab',hasExpected?(row.originalFields?.[0]==='LAB_L'?triple(row.originalValues):'Not in the selected columns'):'—',hasMeasured?triple(native.lab):'—'],
    ['White XYZ',hasExpected?triple(context?.whiteXYZ)+' · '+(context?.illuminant||'—'):'—',hasMeasured?triple(native.context?.white?.xyz)+' · '+(native.context?.illuminant||'—'):'—'],
    ['Source',hasExpected?`TI2 ${row.sample_id} at ${row.sample_loc} · ${context?.meaning||'—'}`:'—',hasMeasured?(reading?rowName(reading):'Reading name unavailable')+' · native XYZ':'—'],
  ];
  const table=document.createElement('table');table.className='chart-inspection-table';
  const head=table.createTHead().insertRow();
  for(const text of ['Value','Expected','Measured']){const th=document.createElement('th');th.scope='col';th.textContent=text;head.append(th);}
  const body=table.createTBody();
  for(const [label,...values] of rows){
    const line=body.insertRow(),th=document.createElement('th');th.scope='row';th.textContent=label;line.append(th);
    for(const value of values){const td=line.insertCell();if(typeof value==='string')td.textContent=value;else td.append(value);}
  }
  $('chartInspectionBody').replaceChildren(table);
}
$('applyExpectedChart').onclick=()=>{
  const whiteXYZ=['X','Y','Z'].map(axis=>Number($('expectedChartWhite'+axis).value));
  if(['X','Y','Z'].some(axis=>$('expectedChartWhite'+axis).value==='')||whiteXYZ.some(value=>!Number.isFinite(value)||value<=0)||whiteXYZ[1]!==100){$('expectedChartError').textContent='Enter a finite positive white XYZ with Y=100.';$('expectedChartError').hidden=false;return;}
  const illuminant=$('expectedChartIlluminant').value,interpretation=$('expectedChartInterpretation').value,meaning=$('expectedChartMeaning').value;
  if([illuminant,interpretation,meaning].some(value=>!value.trim())){$('expectedChartError').textContent='Declare illuminant, interpretation and reference meaning.';$('expectedChartError').hidden=false;return;}
  expectedChartContext={observerId:'CIE1931_2deg',xyzUnits:'1',xyzScale:'relative; perfect-diffuser Y=100',whiteXYZ,illuminant,interpretation,meaning,sourceFields:$('expectedChartFields').value==='xyz'?['XYZ_X','XYZ_Y','XYZ_Z']:['LAB_L','LAB_A','LAB_B']};
  $('expectedChartError').hidden=true;chartColorKey='';renderChart();
};
$('retryChartColors').onclick=()=>{chartColorKey='';renderChart();};
$('clearExpectedChart').onclick=()=>{expectedChartContext=null;chartColorKey='';renderChart();};
function chartEdit(action,operands={}){
  const revision=ownerState?.chart?.revision,fence=datasetFence();
  const followCurrent=['rotate','inspect_cell','select_cell','set_paused'].includes(action);
  return enqueue(async()=>{await waitMetadata();if(!sameDataset(fence)||datasetBinding?.kind!=='live')throw Error('Return to the live chart to change acquisition.');await send('chart_edit',{chart_revision:followCurrent?ownerState?.chart?.revision:revision,action,operands});});
}
function inspectChartCell(cell){if(!datasetBinding?.available||!chartView)return;if(datasetBinding.kind==='live'&&ownerState?.status==='open'&&!chartView.finished&&chartView.viewed_pass===chartView.active_pass)return chartEdit('inspect_cell',{cell});chartView={...chartView,inspection_cell:cell};renderChart();}
$('chartTarget').onclick=()=>chartEdit('select_cell',{cell:chartView.inspection_cell});
$('chartOrientation').onchange=()=>chartEdit('rotate',{degrees:Number($('chartOrientation').value)});
$('chartPause').onclick=()=>chartEdit('set_paused',{paused:!chartView.paused});
$('chartNewPass').onclick=()=>chartEdit('start_pass',{});
let diagnosticTarget=null,positionReferenceTarget=null;
function operatorFormsMutable(){return datasetBinding?.kind==='live'&&!datasetBinding.closed&&ownerState?.status==='open'&&ownerState.phase==='idle'&&!ownerState.save_reason&&!ownerState.cleanup_reason&&!blocked&&!sampleActionPending;}
function renderOperatorForms(){
  const mutable=operatorFormsMutable();
  $('nativeDiagnostics').disabled=!mutable;
  $('chartReference').disabled=!mutable||!ownerState?.chart||ownerState.chart.finished;
  $('applyDiagnostics').disabled=!mutable||!diagnosticTarget||!sameDataset(diagnosticTarget);
  const emptyPass=!!ownerState?.chart&&ownerState.chart.attempt_count===0&&!ownerState.chart.reference&&!ownerState.chart.pending_context;
  $('positionStartPass').hidden=emptyPass;$('positionStartPass').disabled=!mutable||!positionReferenceTarget||!sameDataset(positionReferenceTarget)||ownerState?.chart?.revision!==positionReferenceTarget.revision;
  $('positionPassState').textContent=emptyPass?'Current comparison pass is empty and ready for a reference.':'Start an empty comparison pass before freezing its reference. Previous readings stay available.';
  $('applyPositionReference').disabled=!emptyPass||!mutable||!positionReferenceTarget||!sameDataset(positionReferenceTarget)||ownerState?.chart?.revision!==positionReferenceTarget.revision;
  if(activePanel==='positionReferenceDialog'&&positionReferenceTarget&&(!sameDataset(positionReferenceTarget)||ownerState?.chart?.revision!==positionReferenceTarget.revision))$('positionReferenceStatus').textContent='The chart changed. Reopen this form before freezing a reference.';
  if(activePanel==='diagnosticsDialog'&&diagnosticTarget&&!sameDataset(diagnosticTarget))$('diagnosticsStatus').textContent='The batch changed. Reopen Native diagnostics.';
}
$('nativeDiagnostics').onclick=()=>{
  diagnosticTarget={...datasetFence(),batch_id:ownerState.batch_id,context_id:ownerState.context_id};
  showPanel('diagnosticsDialog');
  const offer=ownerState.descriptor?.limits?.native_diagnostics,settings=ownerState.native_diagnostics;
  $('diagnosticLevel').value=settings?.debug_level??0;$('diagnosticLimit').value=settings?.limit_bytes??65536;
  for(const option of $('diagnosticLevel').options)option.disabled=option.value!=='0'&&offer?.available!==true;
  $('diagnosticsReason').textContent=offer?.available===true?'Native worker stderr capture. Normally leave this off; enable it when investigating an operation.':offer?.unavailable_reason||'This source does not provide native diagnostics.';
  $('diagnosticsStatus').textContent='';renderOperatorForms();
};
$('dismissDiagnostics').onclick=()=>closePanel('diagnosticsDialog');
$('applyDiagnostics').onclick=()=>{
  $('diagnosticsError').hidden=true;
  const context=actionErrorContext('diagnosticsDialog'),target=diagnosticTarget,payload={debug_level:Number($('diagnosticLevel').value),limit_bytes:Number($('diagnosticLimit').value)};
  if(!$('diagnosticLimit').value.trim()||!Number.isInteger(payload.limit_bytes)||payload.limit_bytes<1||payload.limit_bytes>1048576){showStatusError(Error('Enter a whole-number byte limit from 1 to 1,048,576.'),context);return writes;}
  return queueSampleAction(async()=>{
    if(activePanel!==context.id||panelEpoch!==context.epoch||!sameDataset(target)||ownerState.batch_id!==target.batch_id||ownerState.context_id!==target.context_id)throw Error('The batch changed. Reopen Native diagnostics.');
    await saveSampleDraft();await waitMetadata();await send('configure_native_diagnostics',payload);
    if(activePanel===context.id&&panelEpoch===context.epoch)$('diagnosticsStatus').textContent=payload.debug_level?'Diagnostics enabled for subsequent operations.':'Diagnostics off.';
  },context);
};
$('chartReference').onclick=async()=>{
  positionReferenceTarget={...datasetFence(),batch_id:ownerState.batch_id,context_id:ownerState.context_id,revision:ownerState.chart.revision,total:ownerState.chart.total};
  showPanel('positionReferenceDialog');const context=actionErrorContext('positionReferenceDialog'),target=positionReferenceTarget;
  $('positionReferenceStatus').textContent='';$('positionSourcePass').innerHTML='<option value="">Choose a pass</option>';
  for(const id of ['positionSpecimen','positionEdition','positionGeometry','positionBacking','positionLighting','positionDeclarer','positionAllowance','positionReferenceBound','positionMeasurementBound','positionBasis','positionCoverage'])$(id).value='';
  $('positionMatched').checked=false;renderOperatorForms();
  try{const reply=await dataCall('chart_decision_details');if(activePanel!==context.id||panelEpoch!==context.epoch||!sameDataset(target))return;
    for(const [index,pass] of reply.value.passes.entries()){const option=document.createElement('option');option.value=pass.id;option.textContent='Pass '+(index+1);$('positionSourcePass').append(option);}
  }catch(error){showStatusError(error,context);}
};
$('positionStartPass').onclick=()=>{
  const context=actionErrorContext('positionReferenceDialog'),target=positionReferenceTarget;$('positionReferenceError').hidden=true;
  return queueSampleAction(async()=>{
    if(activePanel!==context.id||panelEpoch!==context.epoch||!sameDataset(target)||ownerState.chart?.revision!==target.revision)throw Error('The chart changed. Reopen Measured position reference.');
    await saveSampleDraft();await waitMetadata();await send('chart_edit',{chart_revision:target.revision,action:'start_pass',operands:{}});
    if(activePanel===context.id&&panelEpoch===context.epoch){positionReferenceTarget={...target,revision:ownerState.chart.revision};renderOperatorForms();}
  },context);
};
$('dismissPositionReference').onclick=()=>closePanel('positionReferenceDialog');
function positionLimit(id,count){
  const text=$(id).value.trim();if(!text)throw Error('Enter every position warning limit; use an explicit zero when appropriate.');
  const tokens=text.split(/[\s,]+/u),values=tokens.map(Number);
  if(values.some(value=>!Number.isFinite(value)||value<0)||!tokens.every(value=>/^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/u.test(value))||![1,count].includes(values.length))throw Error('Each limit needs one finite nonnegative number or exactly '+count+' values.');
  return values.length===1?values[0]:values;
}
$('applyPositionReference').onclick=()=>{
  $('positionReferenceError').hidden=true;
  const context=actionErrorContext('positionReferenceDialog'),target=positionReferenceTarget;let payload;
  try{
    const qualification={matched_conditions_confirmed:$('positionMatched').checked};
    for(const [key,id] of Object.entries({specimen_id:'positionSpecimen',edition:'positionEdition',geometry:'positionGeometry',backing:'positionBacking',lighting:'positionLighting',declarer:'positionDeclarer'})){qualification[key]=$(id).value.trim();if(!qualification[key])throw Error('Complete every specimen and condition field.');}
    if(!qualification.matched_conditions_confirmed)throw Error('Confirm that the source measurement conditions match.');
    const criteria={maximum_delta_e:positionLimit('positionAllowance',target.total),reference_bound:positionLimit('positionReferenceBound',target.total),measurement_bound:positionLimit('positionMeasurementBound',target.total),basis:$('positionBasis').value.trim(),coverage:$('positionCoverage').value.trim()};
    if(!criteria.basis||!criteria.coverage||!$('positionSourcePass').value)throw Error('Choose the measured pass and declare the limit basis and coverage.');
    payload={chart_revision:target.revision,source_pass_id:$('positionSourcePass').value,qualification,criteria};
  }catch(error){showStatusError(error,context);return writes;}
  return queueSampleAction(async()=>{
    if(activePanel!==context.id||panelEpoch!==context.epoch||!sameDataset(target)||ownerState.batch_id!==target.batch_id||ownerState.context_id!==target.context_id||ownerState.chart?.revision!==target.revision)throw Error('The chart changed. Reopen Measured position reference.');
    await saveSampleDraft();await waitMetadata();await send('chart_reference',payload);
    if(activePanel===context.id&&panelEpoch===context.epoch){closePanel(context.id);toast('Measured reference frozen. Comparison pass ready.');}
  },context);
};

$('chartAcceptContext').onclick=()=>chartEdit('accept_context',{});
$('chartContextDetails').onclick=async()=>{
  showPanel('chartHistoryDialog');$('chartHistoryTitle').textContent='Changed chart settings';$('chartHistoryContent').replaceChildren();$('moreChartHistory').hidden=true;const context=actionErrorContext('chartHistoryDialog'),epoch=panelEpoch;
  try{const reply=await dataCall('chart_decision_details');if(activePanel!=='chartHistoryDialog'||panelEpoch!==epoch)return;const pending=reply.value.pending_context;if(!pending){$('chartHistoryContent').textContent='No settings decision remains.';return;}const pre=document.createElement('pre');pre.textContent=JSON.stringify(pending,null,2);const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Exact recorded differences';details.append(summary,pre);const reason=document.createElement('p');reason.textContent=pending.reason;$('chartHistoryContent').append(reason,details);}catch(error){showStatusError(error,context);}
};
for(const button of document.querySelectorAll('[data-position-action]'))button.onclick=()=>chartEdit('resolve_position',{token:chartView.pending_position.token,action:button.dataset.positionAction});
$('chartPass').onchange=()=>loadSelectedChart($('chartPass').value);
$('chartInspect').onchange=()=>inspectChartCell(Number($('chartInspect').value));
$('chartPass').onfocus=async()=>{try{const fence=datasetFence(),reply=await dataCall('chart_decision_details');if(!sameDataset(fence))return;const selected=chartView?.viewed_pass;$('chartPass').replaceChildren(...reply.value.passes.map((item,index)=>{const option=document.createElement('option');option.value=item.id;option.textContent='Pass '+(index+1);return option;}));$('chartPass').value=selected;}catch(error){showStatusError(error);}};
function chartRecipeChanged(){chartPreviewRequest++;chartSetupPacket=null;$('configureChart').disabled=true;$('chartMappingConfirmed').checked=false;$('chartMappingLabel').hidden=true;$('chartSetupPreview').replaceChildren();$('customChartFields').hidden=$('chartKind').value!=='custom';$('chartStripsLabel').hidden=$('chartKind').value!=='ti2';}
$('chartKind').onchange=chartRecipeChanged;$('chartStripsAs').onchange=chartRecipeChanged;
for(const id of ['customChartName','customChartRows','customChartColumns'])$(id).oninput=chartRecipeChanged;
$('setupChart').onclick=()=>{closeMenus();chartRecipeChanged();chartSourcePacket=null;$('chartSourceControls').hidden=true;$('chartSetupError').hidden=true;showPanel('chartSetupDialog');};
$('dismissChartSetup').onclick=()=>closePanel('chartSetupDialog');
$('previewChart').onclick=()=>{const context=actionErrorContext('chartSetupDialog'),fence=chartBinding(),request=++chartPreviewRequest,kind=$('chartKind').value;
  const params=kind==='ti2'?{...fence,strips_as:$('chartStripsAs').value}:{...fence,recipe:kind==='custom'?{kind,name:$('customChartName').value,rows:Number($('customChartRows').value),columns:Number($('customChartColumns').value)}:{kind}};
  return enqueue(async()=>{await saveSampleDraft();await waitMetadata();if(request!==chartPreviewRequest)return;
  const packet=await call(kind==='ti2'?'stage_chart_target':'stage_chart',params);
  if(!packet||request!==chartPreviewRequest||activePanel!==context.id||panelEpoch!==context.epoch)return;
  if(JSON.stringify([packet.batch_id,packet.generation,packet.context_id])!==JSON.stringify([ownerState.batch_id,ownerState.generation,ownerState.context_id]))throw Error('Chart setup changed. Preview it again.');
  chartSetupPacket=packet;const preview=packet.preview;
  renderChartGuide($('chartSetupPreview'),{definition:preview,cells:preview.cells.map(cell=>({...cell,measured:false,attempt_count:0,selected_attempt:null})),orientation:0,current_cell:1,inspection_cell:1,coverage:0,total:preview.cells.length},{onInspect:()=>{}});
  $('chartMappingLabel').hidden=packet.mapping_confirmation===null;$('chartMappingConfirmed').checked=false;
  $('configureChart').disabled=packet.mapping_confirmation!==null;
},null,null,context);};
$('chartMappingConfirmed').onchange=()=>{$('configureChart').disabled=!chartSetupPacket||!!chartSetupPacket.mapping_confirmation&&!$('chartMappingConfirmed').checked;};
$('configureChart').onclick=()=>{const context=actionErrorContext('chartSetupDialog'),packet=chartSetupPacket;return queueSampleAction(async()=>{
  if(activePanel!==context.id||panelEpoch!==context.epoch||packet!==chartSetupPacket)throw Error('Chart setup changed. Preview and confirm it again.');
  if(!packet||packet.mapping_confirmation&&!$('chartMappingConfirmed').checked)throw Error('Preview and confirm the physical target mapping.');
  await send('configure_chart',{definition_token:packet.definition_token,mapping_confirmation:packet.mapping_confirmation},{batch_id:packet.batch_id,generation:packet.generation});
  state.plotSlots=['chart',spectrumAvailable()?'spectrum':'chromaticity'];state.plotSlotsSet=true;state.plotLayout='overview';
  if(activePanel===context.id&&panelEpoch===context.epoch)closePanel(context.id);render();
},context);};
$('chooseChartSource').onclick=()=>{const context=actionErrorContext('chartSetupDialog'),fence=chartBinding();return enqueue(async()=>{
  const packet=await call('choose_chart_source',fence);if(!packet||activePanel!==context.id||panelEpoch!==context.epoch)return;
  chartSourcePacket=packet;$('chartSourceName').textContent=packet.source_name;$('chartSourceControls').hidden=false;
  $('chartSourcePass').replaceChildren(...packet.passes.map((item,index)=>{const option=document.createElement('option');option.value=item.id;option.textContent=`Pass ${index+1} · ${item.coverage} of ${item.total} cells`;return option;}));renderSourceDeclarations();
},null,null,context);};
function renderSourceDeclarations(){const source=chartSourcePacket?.passes.find(item=>item.id===$('chartSourcePass').value);$('chartSourceDeclarations').replaceChildren(...(source?.declarations||[]).map(item=>{const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';input.dataset.material=item.key;input.disabled=$('chartContinuationAction').value==='new-pass';const text=document.createElement('span');text.textContent=item.key.replaceAll('_',' ')+': '+(typeof item.value==='string'?item.value:JSON.stringify(item.value));label.append(input,text);return label;}));}
$('chartSourcePass').onchange=$('chartContinuationAction').onchange=renderSourceDeclarations;
$('continueChart').onclick=()=>{const context=actionErrorContext('chartSetupDialog'),packet=chartSourcePacket,fence=chartBinding();
  if(!packet){showStatusError(Error('Choose a saved source chart.'),context);return writes;}
  const params={...fence,command_id:crypto.randomUUID(),source_token:packet.source_token,action:$('chartContinuationAction').value,source_pass_id:$('chartSourcePass').value,acknowledged_material:[...$('chartSourceDeclarations').querySelectorAll('input:checked:not(:disabled)')].map(input=>input.dataset.material)};
  return queueSampleAction(async()=>{
  if(activePanel!==context.id||panelEpoch!==context.epoch||packet!==chartSourcePacket)throw Error('Chart source setup changed. Review it again.');
  let result;try{result=await call('continue_chart',params);}catch(error){try{result=await call('continue_chart',params);}catch(error){error.commandParams=params;error.commandMethod='continue_chart';throw error;}}
  if(result.admission!=='admitted'||result.state==='failed')throw Error(result.reason||'Source chart could not be adopted.');
  state.plotSlots=['chart',spectrumAvailable()?'spectrum':'chromaticity'];state.plotSlotsSet=true;state.plotLayout='overview';await refresh(true);if(activePanel===context.id&&panelEpoch===context.epoch)closePanel(context.id);
},context);};
function showChartFinish(){if(datasetBinding?.kind!=='live'||!datasetBinding.available||!ownerState?.chart||ownerState.chart.finished)return;const view=ownerState.chart;finishChartTarget={...datasetFence(),passId:view.active_pass,revision:view.revision,incomplete:view.incomplete};showPanel('finishChartDialog');$('finishChartError').hidden=true;$('allowIncompleteChart').checked=false;$('allowIncompleteLabel').hidden=!view.incomplete;$('finishChartStatus').textContent=`Current pass · ${view.coverage} of ${view.total} cells measured.`;$('confirmFinishChart').disabled=view.incomplete;}
$('finishChart').onclick=showChartFinish;
$('allowIncompleteChart').onchange=()=>{$('confirmFinishChart').disabled=finishChartTarget.incomplete&&!$('allowIncompleteChart').checked;};
$('dismissFinishChart').onclick=()=>closePanel('finishChartDialog');
$('confirmFinishChart').onclick=()=>{
  const context=actionErrorContext('finishChartDialog'),target=finishChartTarget,allowIncomplete=$('allowIncompleteChart').checked;
  const requireCurrent=()=>{
    if(!target||!sameDataset(target)||!datasetBinding.available||datasetBinding.kind!=='live'||activePanel!==context.id||panelEpoch!==context.epoch||ownerState?.chart?.active_pass!==target.passId||ownerState.chart.revision!==target.revision)
      throw Error('Chart changed. Reopen Finish to review the current pass.');
  };
  return queueSampleAction(async()=>{
    requireCurrent();await saveSampleDraft();await waitMetadata();requireCurrent();
    await send('finish_chart',{chart_revision:target.revision,allow_incomplete:allowIncomplete});closePanel('finishChartDialog');
  },context);
};
function chartHistoryScopeMatches(scope){return !!scope&&sameDataset(scope.fence)&&datasetBinding.available&&activePanel==='chartHistoryDialog'&&panelEpoch===scope.epoch&&chartView?.viewed_pass===scope.passId&&chartView.inspection_cell===scope.cell;}
async function loadChartHistory(more=false){
  const view=chartView,fence=datasetFence();
  if(!view||!datasetBinding.available)return;
  if(more){if(chartHistoryPending&&chartHistoryScopeMatches(chartHistoryPending))return;if(!chartHistoryScopeMatches(chartHistoryTarget))return loadChartHistory();}
  if(!more){chartHistoryOffset=0;showPanel('chartHistoryDialog');$('chartHistoryContent').replaceChildren();}
  const context=actionErrorContext('chartHistoryDialog'),scope={fence,epoch:panelEpoch,passId:view.viewed_pass,cell:view.inspection_cell,offset:chartHistoryOffset};
  chartHistoryPending=chartHistoryTarget=scope;$('moreChartHistory').disabled=true;
  if(!more)$('moreChartHistory').hidden=true;
  try{
    const reply=await dataCall('chart_history',{pass_id:scope.passId,cell:scope.cell,offset:scope.offset,limit:50});
    if(chartHistoryPending!==scope||!chartHistoryScopeMatches(scope)||chartHistoryOffset!==scope.offset)return;
    const history=reply.value;$('chartHistoryTitle').textContent='Patch '+view.cells.find(cell=>cell.index===view.inspection_cell).coordinate+' history';
    const attempts=history.attempts;
    for(const item of attempts){const section=document.createElement('section');const text=document.createElement('p');text.textContent=(item.status||item.result_status||'Attempt')+(view.cells.find(cell=>cell.index===scope.cell).selected_attempt===item.id?' · Selected':'');const inspect=document.createElement('button');inspect.textContent='Details';inspect.onclick=async()=>{if(!chartHistoryScopeMatches(scope))return;try{const detail=await dataCall('chart_details',{pass_id:scope.passId,attempt_id:item.id});if(!chartHistoryScopeMatches(scope)||!section.isConnected)return;const pre=document.createElement('pre');pre.textContent=JSON.stringify(detail.value,null,2);section.append(pre);inspect.disabled=true;}catch(error){if(chartHistoryScopeMatches(scope))showStatusError(error,context);}};section.append(text,inspect);if(datasetBinding.kind==='live'&&ownerState?.status==='open'&&!view.finished&&view.viewed_pass===view.active_pass){const select=document.createElement('button');select.textContent='Use this attempt';select.disabled=item.status!=='completed';select.onclick=()=>{if(!chartHistoryScopeMatches(scope)||ownerState?.status!=='open'||chartView.finished)return;return chartEdit('select_attempt',{pass_id:scope.passId,cell:scope.cell,attempt_id:item.id});};section.append(select);}$('chartHistoryContent').append(section);}
    if(!attempts.length&&!more)$('chartHistoryContent').textContent='No attempts for this cell.';
    chartHistoryOffset=scope.offset+attempts.length;$('moreChartHistory').hidden=chartHistoryOffset>=history.total;
  }catch(error){if(chartHistoryPending===scope&&chartHistoryScopeMatches(scope))showStatusError(error,context);}
  finally{if(chartHistoryPending===scope){chartHistoryPending=null;if(chartHistoryScopeMatches(scope))$('moreChartHistory').disabled=false;}}
}
$('chartHistory').onclick=()=>loadChartHistory();$('moreChartHistory').onclick=()=>loadChartHistory(true);$('dismissChartHistory').onclick=()=>closePanel('chartHistoryDialog');
function canChangeSettings(){return datasetBinding?.kind==='live'&&ownerState?.status==='open'&&ownerState.can_apply_annotations&&ownerState.phase==='idle'&&ownerState.context_current!==false&&!ownerState.cleanup_reason;}
function equipmentEffectPending(){return !!(equipmentPanel?.busy||equipmentPanel?.unresolved||equipmentPanel?.setupPending);}
function requireEquipmentReady(){if(equipmentEffectPending())throw Error('Wait for device setup or check its delayed result before changing acquisition.');}
// Finish batch stays reachable while this non-modal panel is open, so its
// title, hint and actions follow the batch status on every render.
function renderSetupFrame(){
  const reviewOnly=setupFrozen&&ownerState?.status!=='open';
  const [title,hint]=setupChartDemo?['New guided chart Demo','Simulated readings only. Choose a new save folder, then select Classic or Digital SG.']
    :reviewOnly?['Batch settings','This batch is read only. Start a new batch to change measurement settings.']
    :setupFrozen?['Measurement settings','Source and mode stay with this batch. Change measurement settings for the next reading below.']
    :setupDemo?['New Demo batch','Simulated readings. Choose a Demo instrument and mode, then a new save folder.']
    :['New batch','Choose a connected instrument and measurement mode, then a new save folder.'];
  $('setupTitle').textContent=title;$('setupHint').textContent=hint;
  $('applySetup').hidden=!setupFrozen||reviewOnly;$('cancelSetup').textContent=reviewOnly?'Close':'Cancel';
  renderSetupHint();
}
function updateSetupControls(){
  if(activePanel!=='setupDialog')return;
  renderSetupFrame();
  const disabled=equipmentEffectPending()||correctionPending||sampleActionPending||demoLoading||blocked||setupFrozen&&!canChangeSettings();
  $('create').disabled=disabled||setupFrozen||!setupSelection||!$('mode').value;
  $('applySetup').disabled=disabled||!setupFrozen;
  $('device').disabled=setupFrozen||correctionPending;
  $('mode').disabled=setupFrozen||correctionPending||!setupSelection||!setupDescriptor||!$('mode').value;
  for(const field of $('setupOptions').querySelectorAll('[data-option], [data-workflow-field]'))field.disabled=disabled;
  renderSetupCorrection();
}
function measurementModes(descriptor){return descriptor.modes.filter(entry=>entry.available&&entry.workflows.some(availableMeasurementTask));}
function optionFields(option){
  if(option?.type!=='object'||!Array.isArray(option.members))throw Error('Typed instrument settings are unavailable. Refresh the source.');
  return {layout_fields:option.members,layout_keys:option.members.map(field=>field.key),required_layout_keys:option.members.filter(field=>field.required).map(field=>field.key)};
}
function setupOptions(){
  const options={};
  for(const element of $('setupOptions').querySelectorAll('[data-option]')){
    if(element.dataset.type==='object'){
      const option=setupDescriptor.options.find(item=>item.id===element.dataset.option);
      const changes=collectWorkflowFields(element,optionFields(option));
      if(Object.keys(changes).length)options[element.dataset.option]=changes;
      continue;
    }
    const value=element.type==='checkbox'?element.checked:element.dataset.type==='choice'?element.value:element.value===''?null:Number(element.value);
    if(value!==null&&value!=='')options[element.dataset.option]=value;
  }
  if(setupDescriptor?.options.some(option=>option.id==='correction')||setupCorrection)options.correction=setupCorrection;
  return options;
}
function renderSetupCorrection(){
  const offer=setupDescriptor?.options.find(option=>option.id==='correction'),mode=$('mode').value,selected=offer?.by_mode?.[mode];
  const reviewOnly=setupFrozen&&ownerState.status!=='open';
  $('setupCorrectionControls').hidden=!selected?.available&&!setupCorrection;
  $('correctionStatus').textContent=setupCorrectionMaterial?.source_name?'Selected: '+setupCorrectionMaterial.source_name+' · '+setupCorrectionMaterial.kind.toUpperCase():setupCorrection?(reviewOnly?'Correction selected; details unavailable.':'Reselect the correction to view its details.'):reviewOnly?'No correction file selected.':'Next reading uses the driver calibration.';
  $('correctionDetails').textContent=setupCorrectionMaterial?JSON.stringify(setupCorrectionMaterial,null,2):'';
  $('correctionKinds').textContent=selected?.available?'Available files: '+selected.kinds.map(kind=>kind.toUpperCase()).join(' / '):selected?.unavailable_reason||'';
  const blocked=correctionPending||demoLoading||sampleActionPending||equipmentEffectPending()||setupFrozen&&!canChangeSettings();
  $('chooseCorrection').disabled=blocked||!selected?.available;
  $('clearCorrection').disabled=blocked||!setupCorrection;
  if(correctionPending)$('create').disabled=$('applySetup').disabled=true;
}
function renderSetupHint(){
  const descriptor=setupDescriptor;if(!descriptor)return;
  const mode=$('mode').value,quantities=descriptor.modes.find(entry=>entry.mode===mode)?.expected_quantities;
  const spectral=expectedSpectrumAvailable(descriptor,mode,{xyz_only:$('setupOptions').querySelector('[data-option="xyz_only"]')?.checked||false});
  $('setupSourceHint').textContent=(descriptor.synthetic?'Simulated readings. ':'')+(quantities?[quantities.xyz_unit?'XYZ '+unitText(quantities.xyz_unit):'',spectral===true?quantities.quantity?.replaceAll('_',' '):''].filter(Boolean).join(' · '):'');
  if(setupFrozen&&ownerState.status==='open')$('setupSourceHint').textContent+=' Settings below apply to later readings.';
}
function renderSetupOptions(options={}){
  const mode=$('mode').value,descriptor=setupDescriptor;
  if(!descriptor)return;
  const selectedOptions=descriptor.options.map(option=>{
    const modeState=option.by_mode?.[mode];
    if(!modeState)throw Error('Mode-specific instrument settings are unavailable. Refresh the source.');
    return {...option,...modeState};
  });
  // Only typed member groups belong in this form. Opaque file tokens and Demo
  // chart samples retain their existing picker and chart owners.
  const supported=selectedOptions.filter(option=>option.available&&(['boolean','integer','number','choice'].includes(option.type)||option.type==='object'&&Object.hasOwn(option,'members')));
  $('setupOptions').innerHTML=supported.map(option=>{
    const value=options[option.id]??option.default,id='setup-option-'+option.id,disabled=setupFrozen&&!canChangeSettings()?' disabled':'',attributes=`id="${esc(id)}" data-option="${esc(option.id)}" data-type="${esc(option.type)}"${disabled}`;
    if(option.type==='object')return `<details class="full instrument-settings"><summary>Advanced instrument settings</summary><fieldset class="settings-content" ${attributes}><legend>${esc(option.label)}</legend><p class="small muted">Leave blank to omit a change. Entered values, including 0, are explicit changes. For coded values, consult the instrument manual and the current report from the “Read or apply instrument settings” task.</p><div class="fields" data-option-fields></div></fieldset></details>`;
    if(option.type==='boolean')return `<label class="check"><input type="checkbox" ${attributes}${value?' checked':''}>${esc(option.label)}</label>`;
    if(option.type==='choice')return `<label>${esc(option.label)}<select ${attributes}><option value="">Driver default</option>${option.choices.map(choice=>`<option value="${esc(choice)}"${choice===value?' selected':''}>${esc(option.choice_details?.find(detail=>detail.id===choice)?.presentation_label||choice)}</option>`).join('')}</select></label>`;
    return `<label>${esc(option.label)}${option.unit?' · '+esc(option.unit):''}<input type="number" ${attributes} min="${option.minimum}" max="${option.maximum}" step="${option.type==='integer'?1:'any'}" value="${value??''}"></label>`;
  }).join('');
  for(const option of supported.filter(item=>item.type==='object')){
    const element=[...$('setupOptions').querySelectorAll('[data-option]')].find(item=>item.dataset.option===option.id);
    renderWorkflowFields(element.querySelector('[data-option-fields]'),optionFields(option),options[option.id]??{});
    element.addEventListener('invalid',event=>{element.closest('details').open=true;event.target.focus();},true);
  }
  const unavailable=selectedOptions.filter(option=>!option.available);
  $('setupUnavailable').innerHTML=unavailable.map(option=>`<li><strong>${esc(option.label)}</strong>: ${esc(option.unavailable_reason)}</li>`).join('');
  $('setupUnavailableDetails').hidden=!unavailable.length;
  const chosen=descriptor.modes.find(entry=>entry.mode===mode);
  renderSetupHint();
  $('setupPreparation').textContent=chosen?.operator_setup?.text||'Follow the instrument instructions when prompted.';
  $('create').disabled=setupFrozen||!setupSelection||!chosen;
  $('applySetup').disabled=!setupFrozen||!canChangeSettings()||demoLoading||sampleActionPending;
  updateSetupControls();
}
async function loadSetupSelection(selection_id,request,context){
  if(request!==setupRequest||activePanel!==context.id||panelEpoch!==context.epoch)return;
  if(!selection_id){setupSelection=null;setupDescriptor=null;setupSettingsGeneration=null;setupCorrection=null;setupCorrectionMaterial=null;$('mode').innerHTML='<option value="">Choose an instrument first</option>';$('setupOptions').replaceChildren();$('setupSourceHint').textContent='';$('setupPreparation').textContent='';$('setupUnavailable').replaceChildren();$('setupUnavailableDetails').hidden=true;$('setupCorrectionControls').hidden=true;updateSetupControls();return;}
  const settings=await call('settings',{selection_id});
  if(request!==setupRequest||activePanel!==context.id||panelEpoch!==context.epoch)return;
  setupDescriptor=settings.descriptor;setupSelection=settings.selection_id;
  setupSettingsGeneration=settings.settings_generation;setupCorrection=null;setupCorrectionMaterial=null;
  if(setupChartDemo&&!setupDescriptor.synthetic)throw Error('Guided chart Demo requires a simulated source.');
  renderSetupSources(settings.selections,setupSelection);
  const modes=measurementModes(setupDescriptor).filter(entry=>!setupChartDemo||entry.workflows.some(item=>item.workflow==='spot'&&item.available));
  // A source can remain listed with no available mode (a failed direct channel);
  // say why instead of offering an empty menu.
  const unavailable=[...new Set(setupDescriptor.modes.map(entry=>entry.unavailable_reason).filter(Boolean))].join(' ')||'This source offers no measurement mode.';
  $('mode').innerHTML=modes.length?modes.map(entry=>`<option value="${esc(entry.mode)}">${esc(entry.label)}</option>`).join(''):`<option value="">No available mode: ${esc(unavailable)}</option>`;
  if(modes.some(entry=>entry.mode==='emissive_spot'))$('mode').value='emissive_spot';
  if(setupChartDemo&&modes.some(entry=>entry.mode==='reflective_spot'))$('mode').value='reflective_spot';
  renderSetupOptions();
}
function renderSetupSources(selections,selected=null){
  const sources=selections.filter(source=>!!source.synthetic===setupDemo);
  $('device').innerHTML=`<option value="">${setupDemo?'Choose a Demo instrument':'Choose a connected instrument'}</option>`+sources.map(source=>`<option value="${esc(source.selection_id)}"${source.selection_id===selected?' selected':''}>${source.synthetic?'Demo · ':''}${esc(source.device.model)}${source.device.serial?' · '+esc(source.device.serial):''}</option>`).join('');
  $('device').value=selected||'';
  $('setupNoInstrument').hidden=setupDemo||sources.length>0;
}
const CHART_DEMO_SEED=[['batchName','Guided chart Demo'],['batchNotes','Simulated chart workflow; not physical chart measurements.']];
function setup(newBatch=false,preferredSelection=null,chartDemo=false,demo=false) {
  setupFrozen=!!ownerState&&!newBatch;setupDescriptor=null;setupSelection=null;
  setupChartDemo=chartDemo;
  setupDemo=chartDemo||demo;
  setupSettingsGeneration=null;setupCorrection=null;setupCorrectionMaterial=null;
  const request=++setupRequest;
  renderSetupFrame();
  if(setupChartDemo){for(const [id,value] of CHART_DEMO_SEED)$(id).value=value;}
  // Connect instrument and Use Demo leave chart Demo setup for another source;
  // its untouched name and notes would otherwise label that batch simulated chart work.
  else if(newBatch)for(const [id,value] of CHART_DEMO_SEED)if($(id).value===value)$(id).value='';
  for(const id of ['device','mode','batchName','batchNotes','destination'])$(id).disabled=setupFrozen||id==='destination';
  for(const id of ['batchName','batchNotes','destination'])$(id).closest('label').hidden=setupFrozen;
  $('setupBatchHint').hidden=setupFrozen;
  $('setupPreparationDetails').open=false;$('setupUnavailableDetails').open=false;$('setupUnavailableDetails').hidden=true;
  setupBinding=null;
  $('setupSourceActions').hidden=setupFrozen;$('setupNoInstrument').hidden=true;
  $('create').hidden=setupFrozen;$('create').disabled=true;$('setupOptions').replaceChildren();$('device').replaceChildren();$('mode').innerHTML='<option value="">Choose an instrument first</option>';$('mode').disabled=true;$('setupSourceHint').textContent='';$('setupPreparation').textContent='';$('setupUnavailable').replaceChildren();$('setupUnavailableDetails').hidden=true;$('setupCorrectionControls').hidden=true;
  $('setupError').hidden=true;showPanel('setupDialog');
  const context=actionErrorContext('setupDialog');
  if(setupFrozen){
    setupDescriptor=ownerState.descriptor;
    setupBinding={batch_id:ownerState.batch_id,generation:ownerState.generation,context_id:ownerState.context_id,draft:JSON.parse(JSON.stringify(ownerState.draft)),capability_id:setupDescriptor.capability_id};
    setupCorrection=setupBinding.draft.options.correction||null;setupCorrectionMaterial=currentCorrectionMaterial;
    $('device').innerHTML=`<option>${esc(setupDescriptor.device.model)}</option>`;
    $('mode').innerHTML=`<option value="${esc(ownerState.mode)}">${esc(setupDescriptor.modes.find(entry=>entry.mode===ownerState.mode)?.label||ownerState.mode)}</option>`;
    $('batchName').value=state.name;$('batchNotes').value=state.notes;$('destination').value=state.destination;
    renderSetupOptions(ownerState.draft.options);return;
  }
  $('destination').value='Choose a new folder in the native dialog';
  return enqueue(async()=>{
    const settings=await call('settings',{selection_id:null});
    if(request!==setupRequest||activePanel!==context.id||panelEpoch!==context.epoch)return;
    const selected=preferredSelection!==null?settings.selections.find(source=>source.selection_id===preferredSelection):setupDemo?settings.selections.find(source=>source.synthetic&&source.device_id==='demo:i1pro3')||settings.selections.find(source=>source.synthetic):null;
    if(preferredSelection!==null&&!selected)throw Error('The selected device source changed. Return to Device setup and select it again.');
    if(selected)setupDemo=!!selected.synthetic;
    renderSetupFrame();
    renderSetupSources(settings.selections,selected?.selection_id);
    if(selected)await loadSetupSelection(selected.selection_id,request,context);
    else updateSetupControls();
  },null,null,context);
}
$('device').onchange=()=>{const selection=$('device').value,request=++setupRequest,context=actionErrorContext('setupDialog');$('create').disabled=true;return enqueue(()=>loadSetupSelection(selection,request,context),null,null,context);};
equipmentPanel=new EquipmentPanel($('equipmentDialog'),{
  canAct:()=>!demoLoading&&!sampleActionPending&&!queuedWrites&&!blocked&&(!ownerState||ownerState.phase==='idle'&&ownerState.software_stopped!==false),
  onBusy:()=>renderSampleControls(),
  onSelect:async selection=>{closePanel('equipmentDialog');await setup(true,selection);},
});
function connectInstrument(){equipmentReturnSetup=activePanel==='setupDialog';showPanel('equipmentDialog');return equipmentPanel.open();}
$('manageEquipment').onclick=$('connectInstrument').onclick=connectInstrument;
$('setupDemo').onclick=()=>setup(true,null,false,true);
$('startDemo').onclick=()=>setup(true,null,false,true);
$('dismissEquipment').onclick=()=>{const returnSetup=equipmentReturnSetup;closePanel('equipmentDialog');if(returnSetup)return setup(true);};
$('mode').onchange=()=>{setupCorrection=null;setupCorrectionMaterial=null;renderSetupOptions();};
$('setupOptions').addEventListener('input',renderSetupHint);
$('clearCorrection').onclick=()=>{setupCorrection=null;setupCorrectionMaterial=null;renderSetupCorrection();};
function correctionScope(){
  if(setupFrozen){const binding=setupBinding;return {batch_id:binding.batch_id,generation:binding.generation,context_id:binding.context_id,draft_id:binding.draft.id,version:binding.draft.version,options:{...binding.draft.options,...setupOptions()}};}
  return {selection_id:setupSelection,mode:$('mode').value,capability_id:setupDescriptor.capability_id,settings_generation:setupSettingsGeneration,options:setupOptions()};
}
$('chooseCorrection').onclick=()=>{
  if($('chooseCorrection').disabled)return writes;
  const context=actionErrorContext('setupDialog'),request=setupRequest,scope=correctionScope(),key=JSON.stringify(scope),method=setupFrozen?'choose_current_correction':'choose_correction';
  correctionPending=true;updateSetupControls();
  return enqueue(async()=>{
    const selected=await call(method,scope);
    if(!selected)return;
    if(activePanel!==context.id||panelEpoch!==context.epoch||request!==setupRequest)return;
    if(JSON.stringify(correctionScope())!==key)throw Error('Settings changed while selecting the correction. Choose the file again.');
    setupCorrection=selected.correction;setupCorrectionMaterial=selected.material;
  },null,null,context).finally(()=>{correctionPending=false;updateSetupControls();});
};
function repeatTarget(){
  const row=state.selected,operation=row&&ownerState?.operations.find(item=>item.operation_id===row.obs.operation_id);
  if(!row?.inherited&&operation?.accepted&&operation.status==='completed'&&operation.workflow==='spot')return {...readingIdentity(row),name:matchesReading(ownerState.repeat_source,row)?ownerState.repeat_source.name:'reading #'+(state.rows.indexOf(row)+1)};
  // With no inspected row, use the owner's immutable last retained target.
  return ownerState?.repeat_source&&!row?ownerState.repeat_source:null;
}
function createBatch() { const context=actionErrorContext('setupDialog'),chartDemo=setupChartDemo;try{requireEquipmentReady();validateSetupFields();if(chartDemo&&!setupDescriptor?.synthetic)throw Error('Guided chart Demo requires a simulated source.');}catch(error){showStatusError(error,context);return writes;}const setupValues={name:$('batchName').value,notes:$('batchNotes').value,selection_id:setupSelection,mode:$('mode').value,options:setupOptions()};$('setupError').hidden=true;enqueue(async()=>{
  if(activePanel!==context.id||panelEpoch!==context.epoch||panelVersions.get(context.id)!==context.version)throw Error('Batch setup changed. Open setup and create again.');
  if(localEditErrors.size||blocked)throw Error('Resolve refused text before creating another batch.');
  if(!setupValues.name.trim())throw Error('Enter a batch name.');
  if(!setupValues.selection_id||!setupValues.mode)throw Error('Choose a source and measurement mode.');
  requireEquipmentReady();
  const packet=await call('create_batch',setupValues);
  if(!packet)return;
  resetViews();await synchronise(packet);if(activePanel===context.id&&panelEpoch===context.epoch&&panelVersions.get(context.id)===context.version){closePanel('setupDialog');if(chartDemo)$('setupChart').click();else $('start').focus();}
},null,null,context); }
function validateSetupFields(){
  for(const field of $('setupOptions').querySelectorAll('input'))if(!field.checkValidity())throw Error('Check the instrument setting: '+field.closest('label').textContent.trim());
}
$('applySetup').onclick=()=>{
  const binding=setupBinding,context=actionErrorContext('setupDialog');
  try{validateSetupFields();}catch(error){showStatusError(error,context);return writes;}
  const options={...binding?.draft.options};
  for(const field of $('setupOptions').querySelectorAll('[data-option]'))delete options[field.dataset.option];
  Object.assign(options,setupOptions());
  return queueSampleAction(async()=>{
    if(!binding||activePanel!==context.id||panelEpoch!==context.epoch||panelVersions.get(context.id)!==context.version)throw Error('Setup changed. Open setup again.');
      await saveSampleDraft();
      await waitMetadata();
      if(activePanel!==context.id||panelEpoch!==context.epoch||panelVersions.get(context.id)!==context.version)throw Error('Setup changed. Open setup again.');
      if(ownerState?.batch_id!==binding.batch_id||ownerState.generation!==binding.generation||ownerState.context_id!==binding.context_id||ownerState.draft.id!==binding.draft.id||ownerState.descriptor.capability_id!==binding.capability_id)throw Error('The next reading changed. Reopen setup before applying settings.');
      const draft=ownerState.draft;
      if(JSON.stringify(draft.options)!==JSON.stringify(binding.draft.options))throw Error('The measurement settings changed. Reopen setup before applying settings.');
      await send('edit_draft',{draft_id:draft.id,version:draft.version,name:draft.name,notes:draft.notes,number_samples:draft.number_samples,options},binding);
      await waitMetadata();
      if(activePanel===context.id&&panelEpoch===context.epoch)closePanel('setupDialog');
  },context);
};
function savedReadingTime(item){return item.review_state==='accepted'?item.result?.patches?.[0]?.observation?.acquired_at||null:null;}
function savedReadingCount(item){return item.review_state==='accepted'&&Array.isArray(item.result?.patches)?item.result.patches.length:null;}
function review() { const requestEpoch=++panelEpoch;enqueue(async()=>{
  if(panelEpoch!==requestEpoch)return;
  const saved=await call('review');if(!saved||panelEpoch!==requestEpoch)return;
  $('savedSummary').textContent=`${{finished:'Finished',abandoned:'Abandoned'}[saved.manifest.status]||'Open'} batch · ${saved.sample_count} verified reading${saved.sample_count===1?'':'s'} · Read only · ${saved.folder}`;
  const labels={accepted:'File verified',found_unaccepted:'File found; saved identity unconfirmed',terminal_unknown:'Outcome could not be confirmed',unknown:'File could not be verified'};
  $('savedList').innerHTML=saved.operations.map((item,i)=>`<section class="saved-operation"><strong>${item.request.workflow==='calibrate'?'Calibration':'Measurement'} ${i+1} · ${esc(labels[item.review_state])}</strong><p class="small">${savedReadingCount(item)===null?'Reading count unverified':savedReadingCount(item)+' retained reading'+(savedReadingCount(item)===1?'':'s')}</p>${savedReadingTime(item)?`<p class="small">First retained reading time: <time datetime="${esc(savedReadingTime(item))}">${esc(savedReadingTime(item))}</time></p>`:''}${item.review_state!=='accepted'?`<p class="dialog-error" role="alert">${esc(item.review_reason||labels[item.review_state])}</p>`:''}<details><summary>File-check details</summary><dl><dt>Command</dt><dd>${esc(item.command_id)}</dd><dt>Review state</dt><dd>${esc(item.review_state)}</dd></dl></details></section>`).join('')+
    `<p class="small muted">This check verifies measurement records. TI3 companion files are checked separately when exporting.</p><details><summary>TI3 check details</summary><p class="small">${esc(saved.export_reason)}</p></details>`;
  showPanel('reviewDialog');
}); }
document.querySelectorAll('[data-expand]').forEach(button=>button.onclick=()=>{const id=button.dataset.expand;if(expandedPlots.has(id))expandedPlots.clear();else{expandedPlots.clear();expandedPlots.add(id);}render();button.focus();});
$('start').onclick=()=>begin('Measure');$('calibrate').onclick=()=>begin('Calibrate');$('cancel').onclick=()=>cancel();
$('demoToggle').onclick=()=>{const enabled=$('demoToggle').getAttribute('aria-expanded')!=='true';$('demoToggle').setAttribute('aria-expanded',String(enabled));$('demoControls').hidden=!enabled;};
function demoChartUnavailable(){
  if(retainedReview()||ownerState&&!ownerState.descriptor.synthetic)return 'Return to a Demo batch to try a guided chart.';
  if(demoLoading||sampleActionPending||state.busy||blocked||localEditErrors.size||equipmentEffectPending())return 'Wait for pending work or resolve refused text before starting a chart Demo.';
  if(ownerState?.status==='open'){
    if(ownerState.chart)return 'A guided chart is already active. Use its Measure patch controls.';
    if(ownerState.operations.length)return 'Finish this batch before starting a new guided chart Demo.';
    if(!ownerState.workflows?.some(item=>item.workflow==='spot'&&item.available))return 'A guided chart needs a spot-reading Demo source.';
    if(ownerState.save_reason||ownerState.cleanup_reason)return 'Resolve save or cleanup recovery before setting up a chart Demo.';
  }
  return '';
}
$('demoChart').onclick=()=>{
  const reason=demoChartUnavailable();if(reason){toast(reason);return writes;}
  if(ownerState?.status==='open'){$('setupChart').click();return writes;}
  return setup(true,null,true);
};
function preloadDemo(count){return queueSampleAction(async()=>{
  await saveSampleDraft();await waitMetadata();
  const binding={batch_id:ownerState.batch_id,generation:ownerState.generation,count,load_id:crypto.randomUUID()};
  demoLoading=true;demoLoadId=binding.load_id;$('demoLoadStatus').textContent='Loading '+count+' simulated readings…';render();
  try{
    const result=await call('preload_demo',binding);
    if(result.batch_id!==binding.batch_id||result.requested!==count)throw Error('Demo load identity changed.');
    await refresh(true);
    $('demoLoadStatus').textContent=result.completed+' of '+result.requested+' completed · '+result.retained+' new readings retained';
    if(result.reason)toast(result.reason);
  }catch(error){$('demoLoadStatus').textContent='Demo loading stopped: '+error.message;throw error;}
  finally{demoLoading=false;demoLoadId=null;render();}
});}
$('demo40').onclick=()=>preloadDemo(40);$('demo160').onclick=()=>preloadDemo(160);
$('stopDemoLoad').onclick=()=>{const load_id=demoLoadId;if(load_id)call('stop_demo_preload',{load_id}).catch(error=>toast(error.message));};
$('repeat').onclick=()=>{const source=repeatTarget();if(!source)return;return queueSampleAction(async()=>{await saveSampleDraft();await waitMetadata();const draft=ownerState.draft;await send('repeat_sample',{context_id:ownerState.context_id,draft_id:draft.id,draft_version:draft.version,operation_id:source.operation_id,observation_id:source.observation_id});});};


$('retry').onclick=()=>recover();$('another').onclick=()=>recover(true);
$('retryFiles').onclick=()=>recover(false,true);
$('anotherFiles').onclick=()=>recover(true,true);
$('showAutomaticFiles').onclick=()=>{closeMenus();$('automaticFiles').closest('.batch-menu').open=true;$('automaticFiles').open=true;$('automaticFiles').querySelector('summary').focus();};
$('setup').onclick=()=>setup();$('newBatch').onclick=()=>setup(true);$('create').onclick=createBatch;
$('newBatchAfterFinish').onclick=()=>setup(true);
$('startBatch').onclick=()=>setup(true);$('inspectSaved').onclick=()=>$('openSaved').click();$('closeRecovery').onclick=()=>window.requestOwnerClose();
$('dismissSetup').onclick=$('cancelSetup').onclick=()=>closePanel('setupDialog');
$('finish').onclick=()=>ownerState?.chart?showChartFinish():queueSampleAction(async()=>{await saveSampleDraft();await waitMetadata();await send('finish',{});});
$('comparisonContext').onclick=()=>{setTool('compare');$('useReference').focus();};
async function switchDataset(method){
  closeMenus();
  const fence=datasetFence();
  await reconcileWrites();
  if(!sameDataset(fence))throw Error('The data changed before switching sources. Try again.');
  const packet=await call(method,fence);
  if(packet)await synchronise(packet);
}
function renderExportRecovery(){
  $('exportNotice').hidden=!pendingExports.length;
  const identity=JSON.stringify(pendingExports.map(item=>[item.recovery_id,item.payload_sha256,item.reason,item.published,item.retry_method,item.native_format]));
  if($('pendingExportList').dataset.identity===identity)return;
  $('pendingExportList').dataset.identity=identity;
  $('pendingExportList').innerHTML=pendingExports.map(item=>`<div><span>${esc(item.format.toUpperCase())}: ${esc(item.reason)}${item.published?' An earlier file was published; final saving is unconfirmed.':''}</span><button data-retry-export="${esc(item.recovery_id)}">Save retained export…</button></div>`).join('');
  $('pendingExportList').querySelectorAll('[data-retry-export]').forEach(button=>button.onclick=async()=>{
    try{button.disabled=true;const item=pendingExports.find(value=>value.recovery_id===button.dataset.retryExport);if(!item||!['retry_analysis_export','retry_record_export'].includes(item.retry_method))throw Error('This retained export changed. Refresh before retrying.');const receipt=await call(item.retry_method,{recovery_id:item.recovery_id});if(receipt){if(item.retry_method==='retry_record_export')recordSaved(receipt);toast(receipt.ti3?.retryable||receipt.ti3?.relocatable||receipt.ti3?.status==='stale'?'Original record saved. TI3 companions need attention.':'Retained export saved. Its original snapshot was preserved.');}}
    catch(error){showStatusError(error);}
    finally{button.disabled=false;await refresh(true);}
  });
}
async function exportPanel(){
  closeMenus();showPanel('exportDialog');$('exportStatus').textContent='';$('exportError').hidden=true;
  recordExportContext=null;clearRecordExportReceipt();$('saveRecord').disabled=true;
  const epoch=panelEpoch,formats=await call('formats');
  if(activePanel!=='exportDialog'||epoch!==panelEpoch)return;
  const mat=formats.analysis.mat;
  $('exportFormat').querySelector('[value="mat"]').disabled=!mat.available;
  if(!mat.available&&$('exportFormat').value==='mat')$('exportFormat').value='json';
  $('saveBatchMat').disabled=datasetBinding.kind==='standalone'||!formats.batch.mat.available;
  $('saveAnalysis').disabled=!state.rows.length;
  $('exportDerivationLabel').hidden=!sciencePanel.exportRequest();$('exportDerivation').checked=false;
  recordExportTargets=[];
  if(datasetBinding.kind==='standalone')recordExportTargets.push({label:'Original imported record',reading_ref:null});
  if(state.selected&&state.selected.saved&&(state.selected.obs.record_kind??'color')==='color')recordExportTargets.push({label:'Reading: '+rowName(state.selected),reading_ref:readingIdentity(state.selected)});
  $('recordExportScope').innerHTML=recordExportTargets.map((target,index)=>`<option value="${index}">${esc(target.label)}</option>`).join('');
  await loadRecordFormats();
  $('exportMatReason').hidden=mat.available;$('exportMatReason').textContent=mat.reason||'';
}
async function saveExport(wholeBatch=false){
  const context=actionErrorContext('exportDialog'),fence=datasetFence();
  $('exportError').hidden=true;$('exportStatus').textContent='';
  try{
    await reconcileWrites();
    if(!sameDataset(fence))throw Error('The data changed before export. Reopen Export data.');
    const scope=$('exportScope').value,selected=scope==='selected'?[...state.plotted]:[];
    if(wholeBatch&&datasetBinding.kind==='standalone')throw Error('An imported record has no whole-batch archive. Use Original record export.');
    if(!wholeBatch&&scope==='selected'&&!selected.length)throw Error('Check at least one reading in the table.');
    const derivation=$('exportDerivation').checked?sciencePanel.exportRequest():null;
    if(!wholeBatch&&$('exportDerivation').checked&&!derivation)throw Error('The scientific calculation changed. Calculate again before including it.');
    const params=wholeBatch?fence:{...fence,dataset_revision:FIXTURES.dataset_revision,comparison_version:selectedComparison().version,scope,selected,scale:displayScale,format:$('exportFormat').value,derivation};
    const receipt=await call(wholeBatch?'export_batch_mat':'export_analysis',params);
    if(receipt&&activePanel===context.id&&panelEpoch===context.epoch)$('exportStatus').textContent='Saved to '+receipt.path;
  }catch(error){showStatusError(error,context);}
  finally{await refresh(true);}
}
const recordImportLabels={operation_json:'Operation JSON',operation_mat:'Operation MAT',observation_json:'Color observation JSON',observation_mat:'Color observation MAT',observation_csv:'Color observation CSV'};
async function recordPanel(){
  closeMenus();showPanel('recordDialog');$('recordError').hidden=true;
  const epoch=panelEpoch,formats=await call('formats');
  if(activePanel!=='recordDialog'||panelEpoch!==epoch)return;
  recordImportFormats=formats.standalone_imports;
  $('recordImportFormat').innerHTML=recordImportFormats.map(item=>`<option value="${esc(item.format)}" ${item.available?'':'disabled'}>${esc(recordImportLabels[item.format])}</option>`).join('');
  const available=recordImportFormats.find(item=>item.available);$('recordImportFormat').value=available?.format||'';recordImportChanged();
}
function recordImportChanged(){
  const item=recordImportFormats.find(item=>item.format===$('recordImportFormat').value);
  $('chooseRecord').disabled=!item?.available;
  $('recordImportReason').hidden=!!item?.available;$('recordImportReason').textContent=item?.reason||'No supported import format is available.';
}
async function chooseRecord(){
  const context=actionErrorContext('recordDialog'),fence=datasetFence(),format=$('recordImportFormat').value;
  try{await reconcileWrites();if(!sameDataset(fence))throw Error('The data changed. Choose the record again.');
    const packet=await call('open_record',{...fence,format});
    if(packet){await synchronise(packet);if(activePanel===context.id&&panelEpoch===context.epoch)closePanel('recordDialog');}
  }catch(error){showStatusError(error,context);}
}
const recordExportLabels={json:'Original JSON',mat:'Original MAT',csv:'Color observation CSV',sp:'Native SPECT (.sp)'};
async function loadRecordFormats(){
  const request=++recordFormatsRequest,epoch=panelEpoch,target=recordExportTargets[Number($('recordExportScope').value)],fence=datasetFence();
  recordExportContext=null;clearRecordExportReceipt();$('saveRecord').disabled=true;$('recordExportFormat').innerHTML='';
  $('recordExportUnavailable').hidden=true;$('recordExportUnavailable').open=false;$('recordExportUnavailableList').textContent='';
  if(!target){$('recordExportReason').hidden=false;$('recordExportReason').textContent='Inspect a saved color reading to export its original observation.';return;}
  const params={...fence,dataset_revision:FIXTURES.dataset_revision,reading_ref:target.reading_ref};
  try{const result=await call('record_formats',params);
    if(request!==recordFormatsRequest||activePanel!=='exportDialog'||panelEpoch!==epoch||!sameDataset(fence)||params.dataset_revision!==FIXTURES.dataset_revision)return;
    recordExportContext={...params,formats:result.formats};
    $('recordExportFormat').innerHTML=result.formats.map(item=>`<option value="${esc(item.format)}" ${item.available?'':'disabled'}>${esc(recordExportLabels[item.format])}</option>`).join('');
    $('recordExportFormat').value=result.formats.find(item=>item.available)?.format||'';recordExportChanged();
  }catch(error){if(request===recordFormatsRequest&&activePanel==='exportDialog'&&panelEpoch===epoch&&sameDataset(fence)&&params.dataset_revision===FIXTURES.dataset_revision)showStatusError(error,actionErrorContext('exportDialog'));}
}
function recordExportChanged(){
  const item=recordExportContext?.formats.find(item=>item.format===$('recordExportFormat').value);
  $('saveRecord').disabled=!item?.available;
  const unavailable=recordExportContext?.formats.filter(item=>!item.available)||[];
  $('recordExportReason').hidden=!!item?.available;$('recordExportReason').textContent=item?.reason||'No original record format is available.';
  $('recordExportUnavailable').hidden=!unavailable.length;
  $('recordExportUnavailableList').innerHTML=unavailable.map(item=>`<li>${esc(recordExportLabels[item.format])}: ${esc(item.reason)}</li>`).join('');
  $('recordExportHint').textContent=item?.format==='sp'?'Choose a new SPECT file, then a distinct JSON companion in the same folder. Both preserve the original native data.':'JSON and MAT retain original quantities and metadata. CSV stores one color observation.';
}
function clearRecordExportReceipt(){
  $('recordExportDetails').textContent='';$('exportStatus').textContent='';
}
function currentRecordExport(panel){
  return !!panel?.request&&activePanel===panel.id&&panelEpoch===panel.epoch&&sameDataset(panel.request)&&panel.request.dataset_revision===FIXTURES.dataset_revision&&panel.request===recordExportContext&&panel.format===$('recordExportFormat').value;
}
function recordSaved(receipt,panel=null){
  const ti3=receipt.ti3;if(!ti3)return;
  const attention=ti3.retryable||ti3.relocatable||ti3.status==='stale';
  if(attention)recordCompanionFailures.set(receipt.path,receipt);else recordCompanionFailures.delete(receipt.path);
  if(currentRecordExport(panel))$('recordExportDetails').innerHTML=ti3FileDetails(ti3);
  $('recordCompanionNotice').hidden=!recordCompanionFailures.size;
  $('recordCompanionDetails').innerHTML=[...recordCompanionFailures.values()].map(item=>`<details><summary>${esc(item.path.split('/').at(-1))} · TI3 file details</summary><p>Original file: ${esc(item.path)}</p>${ti3FileDetails(item.ti3)}</details>`).join('');
}
async function saveRecord(){
  const context=actionErrorContext('exportDialog'),captured=recordExportContext,format=$('recordExportFormat').value;
  const panel={...context,request:captured,format};let dispatched=false;
  $('exportError').hidden=true;$('exportStatus').textContent='';$('saveRecord').disabled=true;
  try{await reconcileWrites();if(!captured||!sameDataset(captured)||captured.dataset_revision!==FIXTURES.dataset_revision||!captured.formats.some(item=>item.format===format&&item.available))throw Error('The original record changed. Reopen Export data.');
    const {formats,...params}=captured;dispatched=true;const receipt=await call('export_record',{...params,format});
    if(receipt){recordSaved(receipt,panel);if(currentRecordExport(panel))$('exportStatus').textContent='Saved original record to '+receipt.path+(recordCompanionFailures.has(receipt.path)?' · TI3 companions need attention.':'');}
  }catch(error){if(!dispatched||currentRecordExport(panel))showStatusError(error,context);}
  finally{recordExportChanged();await refresh(true);}
}
$('openRecord').onclick=()=>recordPanel().catch(showStatusError);
$('dismissRecord').onclick=()=>closePanel('recordDialog');$('recordImportFormat').onchange=recordImportChanged;$('chooseRecord').onclick=chooseRecord;
$('recordExportScope').onchange=()=>loadRecordFormats();$('recordExportFormat').onchange=()=>{clearRecordExportReceipt();recordExportChanged();};$('saveRecord').onclick=saveRecord;
function whiteDeclaration(choice,y,context){
  if(choice.startsWith('standard:')){
    if(!context)throw Error('Select a reading with native XYZ to declare compatible white units.');
    if(!String(y).trim()||!Number.isFinite(Number(y))||Number(y)<=0)throw Error('Enter a finite positive reference white Y.');
    return {kind:'theoretical-lab-reference',name:choice.slice(9),observer:context.observer,reference_y:Number(y),unit:context.unit,scale:context.scale};
  }
  const row=state.rows.find(item=>'reading:'+item.id===choice);
  if(!row||!whiteEligible(row))throw Error('Choose a saved positive native XYZ white.');
  return {kind:'retained-reading',...readingIdentity(row)};
}
const sciencePanel=new SciencePanel($('scienceDialog'),{
  view:()=>({fence:datasetFence(),revision:FIXTURES.dataset_revision,available:!!datasetBinding?.available&&datasetBinding.kind!=='empty',rows:state.rows.map(row=>({...row,readingRef:readingIdentity(row),whiteEligible:whiteEligible(row)})),checked:[...state.plotted],standards:FIXTURES.standard_whites}),
  white:whiteDeclaration,format:numberText,name:row=>{const current=state.rows.find(item=>item.id===row?.id);return current?rowName(current):row?.name||'Unavailable reading';},
});
const activityPanel=new ActivityPanel($('activityDialog'),{
  view:()=>({fence:datasetFence(),hint:JSON.stringify([datasetBinding?.roster_revision||'empty',datasetBinding?.read_only,datasetBinding?.closed]),available:!!datasetBinding?.available&&datasetBinding.kind!=='empty',name:state.name,reason:datasetBinding?.reason}),
  onJump:reference=>{const row=state.rows.find(item=>matchesReading(reference,item));if(!row){toast('This retained reading is unavailable in the current table.');return;}readingFilter='';$('readingFilter').value='';$('gamutFilter').value='all';inspect(row.id);scrollSelected();closePanel('activityDialog');},
  onCopied:()=>toast('Outcome summary copied.'),onCount:count=>$('outcomeCount').textContent=count,
});
function openActivity(){showPanel('activityDialog');return activityPanel.open();}
$('openActivity').onclick=openActivity;$('dismissActivity').onclick=()=>closePanel('activityDialog');
$('scientificCalculations').onclick=()=>{showPanel('scienceDialog');sciencePanel.open();};
$('dismissScience').onclick=()=>closePanel('scienceDialog');
$('exportData').onclick=()=>exportPanel().catch(showStatusError);
$('dismissExport').onclick=()=>closePanel('exportDialog');
$('saveAnalysis').onclick=()=>saveExport();$('saveBatchMat').onclick=()=>saveExport(true);
$('openSaved').onclick=()=>switchDataset('open_saved').catch(showStatusError);
$('returnLive').onclick=()=>switchDataset('activate_live').catch(showStatusError);
$('savedReview').onclick=review;$('dismissReview').onclick=()=>closePanel('reviewDialog');
$('help').onclick=()=>showPanel('helpDialog');$('dismissHelp').onclick=()=>closePanel('helpDialog');
for(const tool of TOOLS){$(tool+'Tab').onclick=()=>{setTool(tool);if(tool==='data')openActivity();};$(tool+'Tab').onkeydown=e=>{const tools=TOOLS,i=tools.indexOf(tool);if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();setTool(tools[(i+(e.key==='ArrowRight'?1:tools.length-1))%tools.length]);}};}
$('follow').onchange=()=>{if($('follow').checked)selectLatest();else{state.follow=false;render();}};
$('coordinates').onchange=()=>{zoom=1;render();$('coordinates').focus();};
$('useReference').onclick=()=>{if(!state.selected)return;return changeComparison({reference:readingIdentity(state.selected)});};
$('clearReference').onclick=()=>changeComparison({reference:null});
$('copyComparison').onclick=copyComparisonSummary;
$('previewGamut').onchange=()=>{render();$('previewGamut').focus();};
for(const id of ['referenceGamut','comparisonGamut','gamutWarnings'])$(id).onchange=()=>{render();$(id).focus();};
$('retryAnalysis').onclick=()=>{analysisController?.abort();analysisPending='';analysisReply=null;render();projectionController?.abort();projectionPending='';projectionReply=null;render();$(state.tool+'Tab').focus();};
$('readingFilter').oninput=()=>{readingFilter=$('readingFilter').value.toLocaleLowerCase();render();};
$('showSelected').onclick=()=>{readingFilter='';$('readingFilter').value='';$('gamutFilter').value='all';render();scrollSelected();$('row-'+state.selected.id).focus();};
$('clearFilter').onclick=()=>{readingFilter='';$('readingFilter').value='';$('gamutFilter').value='all';render();$('readingFilter').focus();};
$('showDetails').onchange=()=>{state.inspector=$('showDetails').checked;if(!state.inspector&&activePanel!=='annotationDialog')editorPinned=false;renderDetails();};
$('dismissInspector').onclick=()=>{state.inspector=false;if(activePanel!=='annotationDialog')editorPinned=false;renderDetails();$('showDetails').focus();};
$('setLabWhite').onclick=()=>{setTool('color');$('whiteChoice').focus();};
$('plotInspected').onclick=()=>{if(!state.selected)return;if(state.previousPlotted===null)state.previousPlotted=new Set(state.plotted);state.plotted=new Set([state.selected.id]);render();$('plotInspected').focus();};
$('restorePlotted').onclick=()=>{if(!state.previousPlotted)return;state.plotted=state.previousPlotted;state.previousPlotted=null;render();$('plotInspected').focus();};
$('whiteChoice').onchange=()=>{state.whiteChoice=$('whiteChoice').value || null;render();$('whiteChoice').focus();};
$('whiteY').oninput=()=>$('whiteY').setCustomValidity('');
$('declareWhite').onclick=()=>{
  let white;
  if(state.whiteChoice?.startsWith('standard:')) {
    const context=whiteChoiceContext(),y=Number($('whiteY').value);
    if(!Number.isFinite(y)||y<=0){$('whiteY').setCustomValidity('Enter a finite positive reference Y.');$('whiteY').reportValidity();return;}
    if(!context)return;
    white={kind:'theoretical-lab-reference',name:state.whiteChoice.slice(9),observer:context.observer,reference_y:y,unit:context.unit,scale:context.scale};
  } else {
    const candidate=state.rows.find(r=>'reading:'+r.id===state.whiteChoice);
    if(!candidate||!whiteEligible(candidate))return;
    white={kind:'retained-reading',...readingIdentity(candidate)};
  }
  return changeComparison({white});
};
$('clearWhite').onclick=()=>changeComparison({white:null});
$('spaceKind').onchange=()=>{state.plotSlotsSet=true;defaultLabWhiteRequested=$('spaceKind').value==='lab'&&!state.white;render();$('spaceKind').focus();};
$('spaceWhite').onclick=()=>{setTool('color');$('whiteChoice').focus();};
for(const id of ['spaceReferences','chromaticityReferences'])$(id).onclick=()=>{
  $(id).closest('details').open=false;
  document.querySelector('.workspace-view').open=true;$('referenceGamut').focus();
};
for(const id of ['colorBackgrounds','curveContrast','showLocus','wavelengthStyle','gamutFilter','readingSort'])$(id).onchange=()=>{render();$(id).focus();};
$('plotAll').onchange=()=>{const check=$('plotAll').checked;for(const row of matchedRows())check?state.plotted.add(row.id):state.plotted.delete(row.id);render();$('plotAll').focus();};
$('rotateLeft').onclick=()=>spaceView?.nudge(-.2,0);$('rotateRight').onclick=()=>spaceView?.nudge(.2,0);$('rotateUp').onclick=()=>spaceView?.nudge(0,-.15);$('rotateDown').onclick=()=>spaceView?.nudge(0,.15);$('reset3D').onclick=()=>spaceView?.reset();
$('spaceZoomIn').onclick=()=>spaceView?.zoom(1.3);$('spaceZoomOut').onclick=()=>spaceView?.zoom(1/1.3);
$('spaceCamera').onchange=()=>spaceView?.preset($('spaceCamera').value);
$('spacePlot').addEventListener('plotcamerachange',event=>{$('spaceCamera').value=event.detail.preset;});
$('spaceAppearance').onchange=()=>renderSpace();
$('spacePlot').addEventListener('plotcontextchange',event=>{spaceContextLost=event.detail.lost;spaceError=spaceContextLost?'WebGL context lost. Waiting for restoration.':null;render();});
$('zoomIn').onclick=()=>{zoom=Math.min(256,zoom*1.5);render();$('zoomIn').focus();};$('zoomOut').onclick=()=>{zoom=Math.max(.5,zoom/1.5);render();$('zoomOut').focus();};$('resetPlot').onclick=()=>{zoom=1;render();$('resetPlot').focus();};
$('close').onclick=()=>window.requestOwnerClose();


$('inspector').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();state.inspector=false;if(activePanel!=='annotationDialog')editorPinned=false;renderDetails();$('showDetails').focus();}});
document.addEventListener('keydown',event=>{
  const menu=event.target.closest?.('.compact-menu[open]');
  if(event.key==='Escape'&&menu&&!event.isComposing){event.preventDefault();menu.open=false;menu.querySelector('summary').focus();return;}
  if(event.key==='Escape'&&activePanel&&!event.isComposing&&event.target.dataset?.composing!=='true'){event.preventDefault();closePanel(activePanel);return;}
  if(!event.ctrlKey||!event.shiftKey||event.altKey||event.metaKey||event.key.toLowerCase()!=='m'||event.repeat||event.isComposing)return;
  const input=event.target.closest?.('input'),textEntry=event.target.closest?.('textarea,[contenteditable]:not([contenteditable="false"])')||input&&!['checkbox','radio','range','button','submit','reset','color','file'].includes(input.type);
  if(textEntry)return;
  if(editingPanel()){event.preventDefault();toast('Close the open panel before measuring.');return;}
  event.preventDefault();
  if($('start').disabled||$('start').hidden){toast(ownerState?.save_reason||ownerState?.cleanup_reason||(!state.batch?'Create a batch first.':state.finished?'This batch is finished. Start a new batch to measure.':sampleActionPending||state.busy?'Wait for the current operation or saving to finish.':'Resolve the sample text before measuring.'));return;}
  begin('Measure');
});

$('applyScale').onclick=()=>{
  const next=Number($('previewScale').value);
  if(!Number.isFinite(next)||next<=0){$('previewScale').setCustomValidity('Enter a finite positive common luminance scale.');$('previewScale').reportValidity();return;}
  $('previewScale').setCustomValidity('');displayScale=next;analysisController?.abort();analysisPending='';analysisReply=null;render();$('applyScale').focus();
};
$('previewScale').oninput=()=>$('previewScale').setCustomValidity('');

const witness=FIXTURES.gamut_witness;
$('gamutWitness').textContent=`Calculated XYZ (${witness.xyz.join(', ')}) at ${witness.scale} cd/m² reference: signed sRGB (${witness.previews.srgb.linear_rgb.map(v=>v.toFixed(6)).join(', ')}) has a negative red channel; Display P3 (${witness.previews['display-p3'].linear_rgb.map(v=>v.toFixed(6)).join(', ')}) is within the RGB cube. This is a numerical example, not a measured sample or proof of the local display.`;

$('scaleToWhite').onclick=()=>{const white=declaredWhite();if(!white)return;$('previewScale').value=String(white.reference_y);$('applyScale').click();$('scaleToWhite').closest('details').open=false;};
$('scaleToChecked').onclick=()=>{const target=$('referenceGamut').value,required=plottedRows().map(r=>displayPreview(r,target)?.gamut_status?.fit_positive_rgb_scale).filter(v=>Number.isFinite(v)&&v>0);if(!required.length)return;$('previewScale').value=String(roundedFitScale(Math.max(...required)));$('applyScale').click();$('scaleToChecked').closest('details').open=false;};

$('dismissAnnotation').onclick=()=>closePanel('annotationDialog');


// Private performance observation uses one page clock; no monitoring transport.
const interactionSamples=[];
document.addEventListener('change',event=>{
  if(!event.target.matches('[data-plot],#plotAll,#gamutFilter,#readingSort'))return;
  const start=performance.now(),action=event.target.id;
  const settled=()=>{
    if(analysisPending||projectionPending){requestAnimationFrame(settled);return;}
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      interactionSamples.push({action,milliseconds:performance.now()-start});
      if(interactionSamples.length>32)interactionSamples.shift();
      $('workspace').dataset.interactions=JSON.stringify(interactionSamples);
    }));
  };requestAnimationFrame(settled);
},true);

equipmentStartup=equipmentPanel.initialise();
Promise.all([call('bind'),equipmentStartup]).then(([packet])=>{
  const softwareError=equipmentPanel.state?.instrument_software?.error;
  $('instrumentSoftwareError').textContent=softwareError||'';$('instrumentSoftwareError').hidden=!softwareError;
  if(equipmentPanel.setupPending){showPanel('equipmentDialog');equipmentPanel.active=true;equipmentPanel.renderAvailability();renderSampleControls();return;}
  return synchronise(packet);
}).then(()=>setInterval(()=>refresh().catch(showStatusError),300)).catch(showStatusError);

const viewControls=[...tableColumns.map(c=>'column-'+c.key),'coordinates','previewGamut','referenceGamut','comparisonGamut','gamutWarnings','showDetails','colorBackgrounds','curveContrast','showLocus','wavelengthStyle','gamutFilter','readingSort','spaceKind','spaceAppearance','spaceCamera'];
function persistView(){
  if(equipmentEffectPending())return;
  if(!datasetBinding?.id||!datasetBinding.available)return;
  const params={...datasetFence(),view:{plotSlots:[...state.plotSlots],plotted:[...state.plotted],previousPlotted:state.previousPlotted===null?null:[...state.previousPlotted],displayScale,follow:state.follow,tool:state.tool,inspector:state.inspector,controls:{...Object.fromEntries(viewControls.map(id=>[id,$(id).type==='checkbox'?$(id).checked:$(id).value])),plotLayout:state.plotLayout},expanded:[...expandedPlots],zoom}};
  const key=JSON.stringify(params);
  if(key===lastViewKey)return writes;
  lastViewKey=key;
  return enqueue(async()=>{try{await call('set_view',params);}catch(error){if(lastViewKey===key)lastViewKey='';throw error;}});
}
document.addEventListener('change',persistView);
document.addEventListener('click',event=>{if(event.target.closest('button'))persistView();});

$('discardLocalEdits').onclick=()=>enqueue(async()=>{
  if(blocked)throw Error('Check the delayed action with Reload or Close first.');
  for(const id of ['sampleName','sampleNotes','editName','editNotes'])if($(id))$(id).dataset.composing='false';
  clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer);samplePreview=null;samplePreviewError='';
  localEditErrors.clear();localEditValues.clear();lastSnapshotKey='';await refresh(true);
  if(ownerState){$('sampleName').value=ownerState.draft.name;$('sampleNotes').value=ownerState.draft.notes;}
  renderDetails();renderLocalEdits();
});
