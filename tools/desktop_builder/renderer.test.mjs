import './workflow-fields.test.mjs';
import './chart-guide.test.mjs';
// Execute the actual renderer and bridge against a DOM and an inert owner peer.
// This proves interleavings and error presentation, not layout, WebGL or native AX.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { execFileSync, spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createHash } from 'node:crypto';
import { JSDOM } from 'jsdom';

const assets = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../src/color_measurement/desktop_assets');
const sources = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'renderer');
const spaceViewModuleURL = pathToFileURL(path.join(sources, 'space-view.js'));
const threeModuleURL = pathToFileURL(path.join(sources, 'vendor/three.module.js'));
const copy = value => JSON.parse(JSON.stringify(value));

async function waitForComparisonAnalysis(app) {
  const deadline = performance.now() + 10000;
  let result = app.run('currentAnalysis()');
  while (!result?.color_comparison_by_source) {
    assert.ok(!result?.error, result?.error);
    assert.ok(performance.now() < deadline, 'The current comparison analysis did not settle');
    await new Promise(resolve => setTimeout(resolve, 20));
    result = app.run('currentAnalysis()');
  }
  return copy(result);
}

function pythonOwnerPeer(nativeContext = null) {
  const root = path.resolve(assets, '../../..');
  const python = process.env.CM_RENDERER_PYTHON || path.join(root, '.venv/bin/python');
  const environment = { ...process.env, PYTHONPATH: path.join(root, 'src') };
  delete environment.CM_RENDERER_NATIVE_REPORT;
  delete environment.CM_RENDERER_NATIVE_SCENARIO;
  delete environment.CM_COLORIMETER_FIXTURE;
  delete environment.CM_CHART_PERSONA;
  delete environment.CM_CHART_FIXTURE;
  if (nativeContext) Object.assign(environment, {
    CM_RENDERER_NATIVE_REPORT: nativeContext.report,
    CM_RENDERER_NATIVE_SCENARIO: nativeContext.scenario,
    ...(nativeContext.scenario==='i1pro3'
      ? {CM_CHART_PERSONA:'i1pro3',CM_CHART_FIXTURE:'startup'}
      : {CM_COLORIMETER_FIXTURE:nativeContext.persona}),
  });
  const child = spawn(python, [path.join(root, 'tools/desktop_builder/owner-peer.py')], {
    cwd: root, env: environment, stdio: ['pipe', 'pipe', 'pipe'],
  });
  const pending = new Map();
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4000); });
  const output = createInterface({ input: child.stdout });
  output.on('line', line => {
    const reply = JSON.parse(line), callback = pending.get(reply.id);
    if (callback) { pending.delete(reply.id); callback.resolve(reply); }
  });
  const ended = new Promise(resolve => child.on('close', code => {
    for (const callback of pending.values()) callback.reject(Error('Python owner peer exited: ' + stderr));
    pending.clear(); resolve(code);
  }));
  child.on('error', error => { for (const callback of pending.values()) callback.reject(error); pending.clear(); });
  return {
    send(message) { return new Promise((resolve, reject) => { pending.set(message.id, { resolve, reject }); child.stdin.write(JSON.stringify(message) + '\n'); }); },
    async close() {
      child.stdin.end();
      const timer = setTimeout(() => child.kill(), 12000);
      try { assert.equal(await ended, 0, stderr); } finally { clearTimeout(timer); output.close(); }
    },
  };
}

// One-shot producer output, so a scripted case consumes the real reply shape.
function pythonJson(source) {
  const root = path.resolve(assets, '../../..');
  const python = process.env.CM_RENDERER_PYTHON || path.join(root, '.venv/bin/python');
  return JSON.parse(execFileSync(python, ['-c', source], {
    cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src') }, encoding: 'utf8',
  }));
}

async function workspace(peer = null, options = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(assets, 'index.html'), 'utf8'), {
    runScripts: 'outside-only', url: 'https://renderer.invalid/', pretendToBeVisual: true,
  });
  const window = dom.window, context = dom.getInternalVMContext();
  window.TextEncoder=TextEncoder;window.TextDecoder=TextDecoder;
  window.CSS = { supports: () => true };
  window.matchMedia = () => ({ matches: false });
  window.ResizeObserver = class { observe() {} disconnect() {} };
  window.setInterval = () => 0;
  window.document.getElementById('colorBackgrounds').checked = false;
  const annotations = {
    A: { version: 0, name: 'Sample A', notes: 'Notes belonging to A' },
    B: { version: 0, name: 'Sample B', notes: 'Notes belonging to B' },
  };
  const readings = Object.fromEntries(Object.entries(annotations).map(([id, applied]) => [id, {
    version: applied.version, name: applied.name, notes_preview: applied.notes.slice(0, 256),
    notes_length: applied.notes.length, saved: true, inherited:false, reading_ref:{operation_id:'operation-'+id,observation_id:id},
    observation: {
      operation_id: 'operation-' + id, observation_id:id, acquired_at: '2026-10-12T00:00:00Z',
      configuration: [['acquired_at_source', 'synthetic renderer test']],
      device: { model: 'Demo', serial: null }, calibration: { status: 'simulated', detail: 'Test input' },
      actual_mode: 'emissive_spot', backend_reported: {
        xyz: { values: [20, 30, 40], unit: 'cd/m^2', scale: 'absolute; Y is luminance', observer: 'CIE1931_2', illuminant: 'none (emissive)' },
        spectrum: null,
      },
    },
    uvy: [.16, .54, 30], xyy: [2/9, 1/3, 30],
    display_previews: Object.fromEntries(['srgb', 'display-p3'].map(target => [target, {
      display_rgb: [.2, .3, .4], linear_rgb: [.03, .07, .13], clipped_channels: [false, false, false],
      reference_luminance: options.previewScale ?? 100, reference_unit: 'cd/m^2',
    }])),
  }]));
  const batch = {
    batch_id: 'batch', revision: 1, generation: 1, context_id: 'context', status: 'open', phase: 'idle',
    folder: '/synthetic/batch', mode:'emissive_spot', descriptor:{synthetic:true,device_id:'demo:i1pro3',device:{model:'X-Rite i1 Pro 3'},modes:[{mode:'emissive_spot',label:'Emissive spot',available:true,workflows:[{workflow:'spot',available:true,min_patches:1,max_patches:1}]}],options:[]}, draft: { id: 'draft', version: 0, name: 'Sample #01', notes: '', options: {}, number_samples: true },
    repeat_source: { operation_id: 'operation-B', observation_id: 'B', name: 'Original B' },
    comparison: { version: 0, reference: null, white: null, white_descriptor: null, saved: true },
    can_measure: true, can_finish: true, can_edit_annotations: true, can_apply_annotations: true,
    native_diagnostic_failures:{retention:{},publication:{}},
    save_reason: null, cleanup_reason: null, operations: ['A','B'].map(id=>({operation_id:'operation-'+id,accepted:true,status:'completed',workflow:'spot'})), selected: { operation_id:'operation-B',observation_id: 'B' },
    accepted_count: 2, saved_count: 2, durability: { status: 'synchronized', files: [] },
    companions:{source_manifest_sha256:'manifest-current',saving:false,formats:{
      mat:{path:'/synthetic/batch/measurements.mat',state:'current',current:true,reason:null,retryable:false,refresh_pending:false,source_manifest_sha256:'manifest-current',payload_sha256:'mat-payload',publication:null},
      csv:{path:'/synthetic/batch/measurements.csv',state:'current',current:true,reason:null,retryable:false,refresh_pending:false,source_manifest_sha256:'manifest-current',payload_sha256:'csv-payload',publication:null},
      chart_selected_csv:{path:'/synthetic/batch/chart-selected.csv',state:'unavailable',current:false,reason:'This is not a chart batch',retryable:false,refresh_pending:false,source_manifest_sha256:'manifest-current',payload_sha256:null,publication:null},
    }},
    export_state:'current',export_reason:null,
  };
  let displayedBinding=null;
  const packet = { batch, name: 'Synthetic batch', notes: '', view: {},
    get dataset_binding(){return displayedBinding||(!this.batch?{id:null,generation:0,kind:'empty',read_only:true,available:true,roster_revision:'empty',source:null,comparison:null}:{id:'dataset-live',generation:this.batch.generation,kind:'live',read_only:this.batch.status!=='open',available:true,roster_revision:this.batch.batch_id+'/'+this.batch.revision,source:{name:this.name,folder:this.batch.folder,descriptor:this.batch.descriptor,mode:this.batch.mode},comparison:{...this.batch.comparison,persisted:this.batch.comparison.saved}});},
    set dataset_binding(value){displayedBinding=value;},
  };
  const reviewResult = { manifest: { status: 'finished' }, folder: '/synthetic/batch', sample_count: 2,
    operations: [{ command_id: 'saved-command', request: { workflow: 'spot' }, review_state: 'accepted', review_reason: null }],
    export_reason: 'TI3 files require export-owner authentication' };
  const messages = [], drafts = {};
  const xyzComparisons=Object.fromEntries(['A','B'].map(id=>[id,{y_percent:0,uv_distance:0,y_reason:null,uv_reason:null,y_unit:'%',uv_unit:'1',algorithm:'xyz-native-reference-comparison',algorithm_version:'1'}]));
  const colorPacket={delta_xyz:[0,0,0],delta_xy:[0,0],delta_uv:[0,0],delta_lab:null,delta_chroma:null,delta_hue:null,delta_e2000:null,xyz_reason:null,xy_reason:null,uv_reason:null,delta_lab_reason:'Choose an explicit Lab reference white',chroma_reason:'Choose an explicit Lab reference white',hue_reason:'Choose an explicit Lab reference white',delta_e2000_reason:'Choose an explicit Lab reference white',xyz_unit:'cd/m^2',xyz_scale:'absolute; Y is luminance',observer:'CIE1931_2',illuminant:'none (emissive)',algorithm:'signed-color-pair-comparison',algorithm_version:'1'};
  const colorComparisons=Object.fromEntries(['A','B'].map(id=>[id,copy(colorPacket)]));
  const previewNames = new Map([['', 'Sample #01'], ['White', 'White 01'], ['Newer', 'Newer 01'], ['白', '白 01'], ['Pending name', 'Pending name 01'], ['Changed', 'Changed 01']]);
  const resolvedName = params => params.number_samples ? (previewNames.get(params.name) ?? params.name) : params.name;
  let holdPreviews = false;
  const heldPreviews = [];
  let refusedAction = '';
  window.ownerTransport = { ready: Promise.resolve(), isReady: true, postMessage(message) {
    messages.push(copy(message));
    if (peer) {
      peer(copy(message)).then(reply => window.ownerReply(reply)).catch(error => window.ownerReply({ id: message.id, ok: false, error: error.message }));
      return;
    }
    window.queueMicrotask(() => {
      let value;
      if (message.method === 'settings') value = {settings_generation:7,selections:options.selections||[{selection_id:'selection',device_id:'demo:i1pro3',device:batch.descriptor.device,synthetic:true}],selection_id:message.params.selection_id,descriptor:message.params.selection_id?options.descriptors?.[message.params.selection_id]||batch.descriptor:null};
      else if (message.method === 'create_batch') {
        window.ownerReply({ id: message.id, ok: false, error: 'Batch name must contain text' }); return;
      }
      else if (message.method === 'equipment_setup') value=options.equipmentState||{generation:0,listing_generation:0,state:'unselected',reason:null,report:null,catalog:null,candidates:[],bindings:[],active_setup:null};
      else if (message.method === 'formats') value={analysis:{json:true,csv:true,mat:{available:false,reason:'MAT dependencies are absent in this synthetic runtime'}},batch:{mat:{available:false}},standalone_imports:['operation_json','operation_mat','observation_json','observation_mat','observation_csv'].map(format=>({format,available:!format.endsWith('mat'),reason:format.endsWith('mat')?'MAT dependencies are absent in this synthetic runtime':null}))};
      else if(message.method==='record_formats')value={formats:[{format:'json',available:true,reason:null},{format:'mat',available:false,reason:'MAT dependencies are absent in this synthetic runtime'},{format:'csv',available:!!message.params.reading_ref,reason:message.params.reading_ref?null:'CSV stores one color observation'},{format:'sp',available:false,reason:'Native reflective spectrum required'}]};
      else if (message.method === 'copy_summary') value={copied:true};
      else if (message.method === 'review') value = reviewResult;
      else if (message.method === 'dataset'&&packet.dataset_binding.kind==='empty') value={...message.params,readings:{},drafts:{},eligible_white_sources:[],dataset_revision:'empty',roster_revision:'empty',preview_scale:'previewScale' in options ? options.previewScale : 100};
      else if (message.method === 'dataset') value = { ...message.params, readings, drafts, eligible_white_sources: ['A', 'B'], dataset_revision: 'batch/' + batch.revision, roster_revision:packet.dataset_binding.roster_revision, preview_scale: 'previewScale' in options ? options.previewScale : 100 };
      else if (message.method === 'preview_draft') {
        value = { ...message.params, resolved_name: resolvedName(message.params) };
        if (holdPreviews) {
          const original = copy(value);
          heldPreviews.push({ reply(override = {}) { window.ownerReply({ id: message.id, ok: true, value: { ...original, ...override } }); } });
          return;
        }
      }
      else if (message.method === 'analysis' || message.method === 'projection') value = {
        ...message.params, source_commit: 'installed-science', reference: packet.dataset_binding.comparison.reference?.observation_id ?? null,
        white: packet.dataset_binding.comparison.white, white_descriptor: packet.dataset_binding.comparison.white_descriptor,
        comparison_by_source: {}, xyz_comparison_by_source:Object.fromEntries((message.params.selected||[]).map(id=>[id,xyzComparisons[id]])), color_comparison_by_source:Object.fromEntries((message.params.selected||[]).map(id=>[id,colorComparisons[id]||copy(colorPacket)])), delta_e_by_source: {}, summary: packet.analysis_summary||{}, display_previews: {}, reference_gamuts: {},
      };
      else if (message.method === 'annotation') value = { applied: annotations[message.params.reading_ref.observation_id], draft: drafts[message.params.reading_ref.observation_id] || null };
      else if (message.method === 'set_analysis_context') {
        const payload=message.params,context=packet.dataset_binding.comparison;
        if(payload.comparison_version!==context.version){window.ownerReply({id:message.id,ok:true,value:{admission:'refused',reason:'Comparison version changed'}});return;}
        const comparison={...context,reference:payload.reference,white:payload.white,version:context.version+1};
        if(displayedBinding){displayedBinding.comparison={...comparison,saved:false,persisted:false,available:true};}
        else{batch.comparison=comparison;batch.revision++;}
        value={admission:'admitted',state:'completed'};
      }
      else if (message.method === 'command') {
        if (message.params.action === refusedAction) value = { admission: 'refused', reason: 'Synthetic action refusal' };
        else {
          if (message.params.action === 'select') batch.selected = { operation_id:message.params.payload.operation_id,observation_id: message.params.payload.observation_id };
          if (message.params.action === 'edit_draft') {
            const payload = message.params.payload;
            if (payload.draft_id !== batch.draft.id || payload.version !== batch.draft.version) {
              window.ownerReply({ id: message.id, ok: true, value: { admission: 'refused', reason: 'Draft identity changed' } }); return;
            }
            batch.draft = { ...batch.draft, name: resolvedName(payload), notes: payload.notes, options: payload.options, number_samples: payload.number_samples, version: batch.draft.version + 1 };
            batch.revision++;
          }
          value = { admission: 'admitted', state: 'completed' };
        }
      } else value = packet;
      window.ownerReply({ id: message.id, ok: true, value: copy(value) });
    });
  } };
  const run = source => vm.runInContext(source, context);
  run(fs.readFileSync(path.join(assets, 'reference-data.js'), 'utf8'));
  run(fs.readFileSync(path.join(sources, 'owner.js'), 'utf8').replaceAll('export ', ''));
  run(fs.readFileSync(path.join(sources, 'workflow-fields.js'), 'utf8').replaceAll('export ', ''));
  run(fs.readFileSync(path.join(sources, 'presentation.js'), 'utf8').replaceAll('export ', ''));
  run('const nominalGuides = '+fs.readFileSync(path.join(assets,'nominal-chart-guides.json'),'utf8')+';');
  run(fs.readFileSync(path.join(sources, 'chart-guide.js'), 'utf8').replace(/^import .*;\n/gm,'').replaceAll('export ', ''));
  run(fs.readFileSync(path.join(sources, 'equipment.js'), 'utf8').replace(/^import .*;\n/gm, '').replaceAll('export ', ''));
  run(fs.readFileSync(path.join(sources, 'science-panel.js'), 'utf8').replace(/^import .*;\n/gm, '').replaceAll('export ', ''));
  run(fs.readFileSync(path.join(sources, 'activity-panel.js'), 'utf8').replace(/^import .*;\n/gm, '').replaceAll('export ', ''));
  run(fs.readFileSync(path.join(sources, 'app.js'), 'utf8').replace(/^import .*;\n/gm, ''));
  try { await run('refresh(true)'); } catch (error) { dom.window.close(); throw error; }
  return { window, run, packet, batch, readings, annotations, drafts, messages, heldPreviews, previewNames, reviewResult, xyzComparisons,colorComparisons,
    holdPreviews() { holdPreviews = true; },
    refuse(action) { refusedAction = action; }, close() { run('analysisController?.abort(); projectionController?.abort()'); dom.window.close(); } };
}

function recordingEquipment(app){
  const original=app.window.ownerTransport.postMessage;
  const model={enum:'instI1Disp3',model_enum:'instI1Disp3',source_label:'Xrite i1 DisplayPro',display_label:'X-Rite i1 DisplayPro',native_type:17,initialization_assessed:true,initialization_refusal:'initialization_changes_indicator_leds',opening_requires:'accept_disclosed_initialization',binding_policy:{default_binding_timeout_seconds:300,all_modes_read_allowance_floor_seconds:120}};
  const candidate={candidate_id:'candidate-current',index:1,native_type:17,symbol:model.enum,name:model.display_label,path:'hid:/recording-only',transports:['hid'],identified:true,attachable:false,initialization_assessed:true,initialization_refusal:model.initialization_refusal,opening_requires:model.opening_requires,consumed:false};
  let state={generation:0,listing_generation:0,state:'unselected',reason:null,report:null,catalog:null,candidates:[],bindings:[]},dropConnect=false,initializations=0;
  const retained=new Map(),messages=[];
  app.window.ownerTransport.postMessage=message=>{
    if(!['equipment_setup','choose_build_report','refresh_devices','connect_device','close_device','reopen_device','equipment_outcome'].includes(message.method))return original(message);
    messages.push(copy(message));app.window.queueMicrotask(()=>{
      let value;
      if(message.method==='equipment_setup')value=copy(state);
      else if(message.method==='choose_build_report'){state={...state,generation:1,state:'ready',report:{path:'/recording-only/report.json',sha256:'a'.repeat(64),context:{kind:'recording-only'}},catalog:{instrument_types:[model]}};value=copy(state);}
      else if(retained.has(message.params.command_id))value=retained.get(message.params.command_id);
      else if(message.method==='equipment_outcome'){app.window.ownerReply({id:message.id,ok:false,error:'Unknown equipment command ID'});return;}
      else{
        let result;
        if(message.method==='refresh_devices'){state={...state,listing_generation:state.listing_generation+1,state:'listed',candidates:[{...candidate,candidate_id:'candidate-'+(state.listing_generation+1)}]};result=copy(state);}
        else if(message.method==='close_device'){state.bindings[0]={...state.bindings[0],state:'closed'};result={instrument_state:'unknown'};}
        else{initializations++;state.candidates[0].consumed=true;state.bindings=[{binding_id:'binding',generation:initializations,state:'active',device_id:'instrument:recording',model:model.display_label,model_enum:model.enum,serial:'SERIAL-1',binding_policy:model.binding_policy,initialization_assessed:true,initialization_refusal:model.initialization_refusal,opening_requires:model.opening_requires}];result={selection_id:'source-recording',descriptor:copy(app.batch.descriptor)};}
        value={command_id:message.params.command_id,state:'completed',reason:null,result};retained.set(message.params.command_id,copy(value));
        // Like the host's setup executor, listing and initialization reply pending;
        // this worker finishes at once, so the outcome is already terminal.
        if(message.method!=='close_device')value={...value,state:'pending',result:null,stage:message.method==='refresh_devices'?'enumeration':'description'};
      }
      if(dropConnect&&message.method==='connect_device'){dropConnect=false;return;}
      app.window.ownerReply({id:message.id,ok:true,value:copy(value)});
    });
  };
  // settle() is the panel's poll: it reconciles the pending reply with its outcome.
  return {messages,get state(){return state;},get initializations(){return initializations;},dropNextConnect(){dropConnect=true;},settle(){return app.run('equipmentPanel.checkSetup()');}};
}

test('kept chart colors never fall back to references, including while replies are pending',async()=>{
  const app=await workspace();
  try{
    app.run(`chartView={definition:{kind:'classic',name:'Classic chart',rows:4,columns:6},orientation:0,current_cell:1,inspection_cell:2,coverage:1,total:24,cells:Array.from({length:24},(_,i)=>({
      index:i+1,row:Math.floor(i/6)+1,column:i%6+1,coordinate:String.fromCharCode(65+i%6)+(Math.floor(i/6)+1),
      measured:i===0,selected_attempt:i===0?'kept':null,attempt_count:i===0?1:0}))};
      $('previewGamut').value='srgb';renderChartGuide($('chartGuide'),chartView,{onInspect:()=>{}});
      chartColors={measured:[{cell_index:1,color:{available:false,reason:'Native color context unavailable'}}],expected:{cells:[1,2].map(index=>({index,color:{available:true,encoding:{display_rgb:[.2,.4,.6],clipped_channels:[false,false,false]}}}))}};
      paintChartColors();`);
    const kept=app.window.document.querySelector('#chartGuide [data-chart-cell="1"]');
    const reference=app.window.document.querySelector('#chartGuide [data-chart-cell="2"]');
    assert.equal(kept.dataset.chartColorKind,'unavailable');
    assert.equal(kept.style.backgroundColor,'');
    assert.match(kept.title,/Native color context unavailable/);
    assert.doesNotMatch(kept.title,/Reference color preview/);
    assert.equal(kept.textContent,'A1 ✓');
    assert.equal(reference.dataset.chartColorKind,'expected');
    assert.equal(reference.textContent,'B1 ○');
    assert.match(reference.getAttribute('aria-description'),/Reference color preview/);
    assert.equal(reference.style.backgroundColor,'rgb(51, 102, 153)','Reference channels remain unchanged');
    const nominal=app.window.document.querySelector('#chartGuide [data-chart-cell="3"]');
    assert.equal(nominal.dataset.chartColorKind,'nominal');
    assert.equal(nominal.textContent,'C1 ◇');
    assert.match(nominal.title,/Nominal location guide/);
    app.run(`chartColors.measured[0].color={available:true,encoding:{display_rgb:[0,0,0],clipped_channels:[false,false,false]}};paintChartColors();`);
    assert.equal(kept.dataset.chartColorKind,'measured');
    assert.equal(kept.style.backgroundColor,'rgb(0, 0, 0)','A valid black preview is not unavailable');
    assert.equal(kept.textContent,'A1 ✓');
    app.run(`chartColors={measured:[],expected:{cells:[]}};paintChartColors();`);
    assert.equal(kept.dataset.chartColorKind,'unavailable');
    assert.equal(kept.style.backgroundColor,'');
    assert.doesNotMatch(kept.title,/Nominal location guide/);
    assert.equal(reference.dataset.chartColorKind,'nominal');
    assert.equal(reference.textContent,'B1 ◇');
    app.run('chartColors=null;paintChartColors();');
    assert.equal(kept.dataset.chartColorKind,'loading');
    assert.equal(kept.style.backgroundColor,'');
    assert.match(kept.title,/Preparing measured color preview/);
    assert.equal(kept.textContent,'A1 ✓');
  }finally{app.close();}
});

test('inspected patch detail shows expected and measured values with their white, source and clipping',async()=>{
  const real=pythonJson(`import json
from pathlib import Path
from color_measurement import chart_science, chart_state
from color_measurement.chart_targets import import_ti2_json
fixture = Path('tests/fixtures/targets/explicit-lab-six-patch.ti2')
context = fixture.with_suffix('.context.json').read_text()
definition = chart_state.definition('ti2', target_json=import_ti2_json(str(fixture)), strips_as='rows', mapping_confirmed=True)
print(json.dumps(dict(definition=definition, expected=chart_science.chart_expected_colors(definition, context, color_space='srgb'))))`);
  const app=await workspace();
  try{
    app.window.real=real;
    app.run(`chartView={definition:{...window.real.definition},segment_id:'segment-current',orientation:0,current_cell:1,inspection_cell:2,coverage:1,total:6,
      cells:window.real.definition.cells.map(cell=>({...cell,patch_name:null,measured:cell.index===2,selected_attempt:cell.index===2?'kept':null,attempt_count:cell.index===2?1:0}))};
      $('previewGamut').value='srgb';renderChartGuide($('chartGuide'),chartView,{onInspect:()=>{}});
      chartColors={expected:window.real.expected,measured:[{cell_index:2,available:true,reason:'',xyz:[3.1,3.2,2.9],lab:[20.5,0.25,-1.5],
        context:{illuminant:'D50',white:{xyz:[96.42,100,82.51]}},source:{reading_ref:{segment_id:'segment-current',operation_id:'operation-selected',observation_id:'observation-elsewhere',sample_id:'patch-original'}},
        color:{available:true,reason:'',encoding:{display_rgb:[.2,.2,.19],clipped_channels:[false,false,true]}}}]};
      paintChartColors();`);
    const document=app.window.document;
    const table=()=>Object.fromEntries([...document.querySelectorAll('#chartInspectionBody tbody tr')].map(tr=>[tr.children[0].textContent,[tr.children[1].textContent,tr.children[2].textContent]]));
    const triple=values=>app.run(`${JSON.stringify(values)}.map(numberText).join(' · ')`);
    const inspected=real.expected.cells[1],row=inspected.expected,context=real.expected.context;
    assert.notDeepEqual(row.originalValues,row.XYZ,'The inspected patch distinguishes source Lab from its XYZ');
    assert.equal(document.getElementById('chartInspection').hidden,false);
    assert.equal(document.getElementById('chartInspectionSummary').textContent,'Inspected '+inspected.coordinate);
    assert.deepEqual([...document.querySelectorAll('#chartInspectionBody thead th')].map(th=>th.textContent),['Value','Expected','Measured']);
    let values=table();
    assert.deepEqual(values.XYZ,[triple(row.XYZ),triple([3.1,3.2,2.9])]);
    assert.deepEqual(values.Lab,[triple(row.originalValues),triple([20.5,0.25,-1.5])],'Expected Lab is the source columns, not a conversion');
    assert.deepEqual(values['White XYZ'],[triple(context.whiteXYZ)+' · '+context.illuminant,triple([96.42,100,82.51])+' · D50']);
    assert.deepEqual(values.Source,[`TI2 ${row.sample_id} at ${row.sample_loc} · ${context.meaning}`,'Reading name unavailable · native XYZ']);
    assert.deepEqual(values.Preview,['No preview channel clipped','Preview channels clipped']);
    const swatches=document.querySelectorAll('#chartInspectionBody .chart-inspection-swatch');
    assert.equal(swatches.length,2);assert.equal(swatches[1].style.backgroundColor,app.window.document.querySelector('#chartGuide [data-chart-cell="2"]').style.backgroundColor,'The measured swatch is the face the board shows');
    // Native observation IDs are scoped to their operation and chart segment.
    // Distinct rows may legitimately reuse them; the caption must name the
    // same source as the measured values, including a continued source.
    app.run(`const selected=chartColors.measured[0].source.reading_ref;
      const row=state.rows[0];
      const makeRow=(id,name,operation,segment)=>({...row,id,name,inherited:!!segment,
        obs:{...row.obs,operation_id:operation,observation_id:selected.observation_id,sample_id:selected.sample_id},
        readingRef:{operation_id:operation,observation_id:selected.observation_id,
          ...(segment?{segment_id:segment,source_manifest_sha256:'a'.repeat(64)}:{})}});
      state.rows=[makeRow('wrong-operation','Wrong operation','operation-other'),makeRow('selected-local','Selected local','operation-selected')];
      paintChartColors();`);
    assert.equal(table().Source[1],'#2 · Selected local · native XYZ');
    app.run(`state.rows=[{...state.rows[1],id:'wrong-segment',name:'Wrong segment',inherited:true,readingRef:{...state.rows[1].readingRef,segment_id:'segment-other',source_manifest_sha256:'b'.repeat(64)}},state.rows[1]];paintChartColors();`);
    assert.equal(table().Source[1],'#2 · Selected local · native XYZ');
    app.run(`chartColors.measured[0].source.reading_ref.segment_id='segment-inherited';
      state.rows=[state.rows[1],{...state.rows[1],id:'selected-inherited',name:'Selected inherited',inherited:true,readingRef:{...state.rows[1].readingRef,segment_id:'segment-inherited',source_manifest_sha256:'c'.repeat(64)}}];paintChartColors();`);
    assert.equal(table().Source[1],'#2 · Selected inherited · native XYZ');
    app.run(`state.rows=state.rows.map(row=>({...row,obs:{...row.obs,sample_id:'different-original'}}));paintChartColors();`);
    assert.equal(table().Source[1],'Reading name unavailable · native XYZ');
    // A retained expected row whose preview is unavailable keeps its values and says why.
    const unavailable=real.expected.cells.find(cell=>cell.expected.available&&!cell.color.available);
    assert.ok(unavailable,'The fixture keeps one expected row without a preview');
    app.run(`chartView={...chartView,inspection_cell:${unavailable.index}};paintChartColors();`);
    values=table();
    assert.equal(document.getElementById('chartInspectionSummary').textContent,'Inspected '+unavailable.coordinate);
    assert.deepEqual(values.Preview,[unavailable.color.reason,'Not measured']);
    assert.deepEqual(values.XYZ,[triple(unavailable.expected.XYZ),'—']);
    assert.equal(document.querySelectorAll('#chartInspectionBody .chart-inspection-swatch').length,0);
    // No TI2 reference: the reason is stated, never a nominal or measured stand-in.
    app.run(`chartView={...chartView,inspection_cell:2};chartColors={...chartColors,expected:{available:false,reason:'No authenticated TI2 expected values',cells:[]}};paintChartColors();`);
    values=table();
    assert.deepEqual(values.Preview[0],'No authenticated TI2 expected values');
    assert.deepEqual([values.XYZ[0],values.Lab[0],values['White XYZ'][0],values.Source[0]],['—','—','—','—']);
    assert.equal(values.XYZ[1],triple([3.1,3.2,2.9]));
    app.run('chartColors=null;paintChartColors();');
    values=table();
    assert.deepEqual(values.Preview,['Preparing reference values','Preparing measured values']);
  }finally{app.close();}
});

test('a pending chart color reply keeps unchanged selections and covers new ones',async()=>{
  const app=await workspace();
  try{
    app.run(`chartView={definition:{id:'classic-definition',kind:'classic',name:'Classic chart',rows:4,columns:6},viewed_pass:'pass',revision:3,orientation:0,current_cell:2,inspection_cell:2,coverage:1,total:24,cells:Array.from({length:24},(_,i)=>({
      index:i+1,row:Math.floor(i/6)+1,column:i%6+1,coordinate:String.fromCharCode(65+i%6)+(Math.floor(i/6)+1),
      measured:i===0,selected_attempt:i===0?'kept':null,attempt_count:i===0?1:0}))};
      $('previewGamut').value='srgb';renderChartGuide($('chartGuide'),chartView,{onInspect:()=>{}});
      chartColors={measured:[{cell_index:1,color:{available:true,encoding:{display_rgb:[.6,.4,.2],clipped_channels:[false,false,false]}}}],expected:{cells:[3].map(index=>({index,color:{available:true,encoding:{display_rgb:[.2,.4,.6],clipped_channels:[false,false,false]}}}))}};
      chartColorBasis={key:chartColorBasisKey(chartView),colors:chartColors,selected:new Map(chartView.cells.map(cell=>[cell.index,cell.selected_attempt]))};
      chartView={...chartView,revision:4,coverage:2,current_cell:3,cells:chartView.cells.map(cell=>cell.index===2?{...cell,measured:true,selected_attempt:'new',attempt_count:1}:cell)};
      chartColors=null;renderChartGuide($('chartGuide'),chartView,{onInspect:()=>{}});paintChartColors();`);
    const cell=index=>app.window.document.querySelector(`#chartGuide [data-chart-cell="${index}"]`);
    assert.equal(cell(1).dataset.chartColorKind,'measured','An unchanged selected reading keeps its accepted preview');
    assert.equal(cell(1).style.backgroundColor,'rgb(153, 102, 51)');
    assert.equal(cell(2).dataset.chartColorKind,'loading','A new selection never inherits an older face');
    assert.equal(cell(2).style.backgroundColor,'');
    assert.equal(cell(3).dataset.chartColorKind,'expected','Reference previews do not depend on the chart revision');
    app.run(`chartView={...chartView,cells:chartView.cells.map(cell=>cell.index===1?{...cell,selected_attempt:'replacement'}:cell)};paintChartColors();`);
    assert.equal(cell(1).dataset.chartColorKind,'loading','A replaced selection waits for its own preview');
    app.run(`chartView={...chartView,cells:chartView.cells.map(cell=>cell.index===1?{...cell,selected_attempt:'kept'}:cell)};$('previewGamut').value='display-p3';paintChartColors();`);
    assert.equal(cell(1).dataset.chartColorKind,'loading','Another color space never reuses an older encoding');
    assert.equal(cell(3).dataset.chartColorKind,'nominal');
    app.run(`$('previewGamut').value='srgb';chartColorError='Chart color source changed. Refresh the chart.';paintChartColors();`);
    assert.equal(cell(1).dataset.chartColorKind,'unavailable','A refused reply is shown rather than an older face');
    assert.match(cell(1).title,/Chart color source changed/);
    const original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='chart_colors'
      ? app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:false,error:'Frozen source refused'})) : original(message);
    app.run("chartColorError='';chartColorKey='';requestChartColors()");
    for(let attempt=0;attempt<20&&!app.run('chartColorError');attempt++)await new Promise(resolve=>setImmediate(resolve));
    assert.equal(app.run('chartColorBasis'),null,'A refused current query invalidates carried colors');
    app.run("chartColorError='';paintChartColors()");
    assert.equal(cell(1).dataset.chartColorKind,'loading','Retry cannot resurrect a refused source preview');
    app.window.ownerTransport.postMessage=original;
  }finally{app.close();}
});

test('chart color reconciliation refuses unconfirmed revisions and failed refreshes',async()=>{
  for(const fault of ['definition','pass','unconfirmed revision','failed refresh']){
    const app=await workspace();
    try{
      const view={definition:{id:'classic-definition',kind:'classic',name:'Classic chart',rows:4,columns:6},active_pass:'pass',viewed_pass:'pass',revision:3,orientation:0,current_cell:2,inspection_cell:1,coverage:1,total:24,cells:Array.from({length:24},(_,i)=>({
        index:i+1,row:Math.floor(i/6)+1,column:i%6+1,coordinate:String.fromCharCode(65+i%6)+(Math.floor(i/6)+1),
        measured:i===0,selected_attempt:i===0?'kept':null,attempt_count:i===0?1:0}))};
      app.batch.chart=copy(view);app.window.testChart=view;
      const original=app.window.ownerTransport.postMessage,seen=[];
      app.window.ownerTransport.postMessage=message=>{
        seen.push(message.method);
        if(message.method==='snapshot'&&fault==='failed refresh'){
          app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:false,error:'Snapshot source refused'}));return;
        }
        if(message.method!=='chart_colors')return original(message);
        const colors={definition_id:fault==='definition'?'another-definition':view.definition.id,pass_id:fault==='pass'?'another-pass':view.viewed_pass,chart_revision:4,source_revision:'source/4',measured:[],expected:{cells:[]}};
        app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:true,value:{binding_id:'dataset-live',generation:1,value:colors}}));
      };
      app.run(`chartView=window.testChart;ownerState.chart=window.testChart;lastSnapshotKey='';
        chartColors={measured:[{cell_index:1,color:{available:true,encoding:{display_rgb:[.6,.4,.2],clipped_channels:[false,false,false]}}}],expected:{cells:[]}};
        chartColorBasis={key:chartColorBasisKey(chartView),colors:chartColors,selected:new Map(chartView.cells.map(cell=>[cell.index,cell.selected_attempt]))};
        chartColorKey='';renderChart();`);
      for(let attempt=0;attempt<30&&!app.run('chartColorError');attempt++)await new Promise(resolve=>setImmediate(resolve));
      assert.match(app.run('chartColorError'),fault==='failed refresh'?/Snapshot source refused/:/Chart color source changed/,fault);
      assert.equal(seen.filter(method=>method==='snapshot').length,['unconfirmed revision','failed refresh'].includes(fault)?1:0,'Only a matching live reply ahead of the view requests one witness refresh');
      assert.equal(app.run('chartColorBasis'),null,'An unconfirmed or refused reply cannot keep an old measured face');
      assert.equal(app.window.document.querySelector('#chartGuide [data-chart-cell="1"]').dataset.chartColorKind,'unavailable');
      app.window.ownerTransport.postMessage=original;
    }finally{app.close();}
  }
});

// These three pairs run the contributed CR/PR C drivers over their existing
// scripted serial actors. Both workers authenticate against the original report;
// they never enumerate OS devices or establish physical instrument behavior.
const nativeSetupReport=process.env.CM_RENDERER_NATIVE_REPORT;
for(const device of nativeSetupReport?[
  {persona:'cr250',scenario:'cr250_native',model:'Colorimetry Research CR-250',symbol:'instCR250',serial:'SYNTHETIC-250',spectral:true},
  {persona:'cr100',scenario:'cr100_native',model:'Colorimetry Research CR-100',symbol:'instCR100',serial:'SYNTHETIC-100',spectral:false},
  {persona:'pr655',scenario:'pr655_native',model:'Photo Research PR-655',symbol:'instPR655',serial:'SYNTHETIC-P7',spectral:true,disclosed:true},
  {persona:'cr250_native_error',scenario:'cr250_native',model:'Colorimetry Research CR-250',symbol:'instCR250',serial:'SYNTHETIC-250',failure:true},
]:[])test('actual Argyll C path '+(device.failure?'retains '+device.persona+' without a false reading':'connects '+device.persona+' through the renderer and saved review'),{
  timeout:30000,
},async()=>{
  const report=path.resolve(nativeSetupReport),reportBytes=fs.readFileSync(report),receipt=JSON.parse(reportBytes);
  const reportSHA=createHash('sha256').update(reportBytes).digest('hex');
  const peer=pythonOwnerPeer({report,scenario:device.scenario,persona:device.persona});let app;
  try{
    app=await workspace(message=>peer.send(message));
    const doc=app.window.document,n=id=>doc.querySelector('[data-equipment="'+id+'"]');
    assert.equal(app.run('ownerState'),null);
    assert.equal(app.run('equipmentPanel.state.bindings.length'),0);
    assert.equal(app.run('equipmentPanel.state.report.context.kind'),'authenticated-substituted-workers');
    await app.run("$('startBatch').onclick()");
    assert.equal(app.run('setupSelection'),null);assert.equal(doc.getElementById('mode').disabled,true);
    await doc.getElementById('manageEquipment').onclick();
    await n('refresh').onclick();
    const settledSetup=async()=>{
      const deadline=Date.now()+10000;
      while(app.run('equipmentEffectPending()')){
        if(Date.now()>deadline)throw Error('Authenticated Argyll setup did not settle: '+n('error').textContent);
        await app.run('refresh(true)');await new Promise(resolve=>setTimeout(resolve,20));
      }
      if(!n('error').hidden)throw Error(n('error').textContent);
    };
    await settledSetup();
    assert.equal(doc.querySelectorAll('[data-candidate]').length,1);
    doc.querySelector('[data-candidate]').click();
    n('model').value=device.symbol;n('model').dispatchEvent(new app.window.Event('change'));
    n('serial').value=device.serial;n('serial').dispatchEvent(new app.window.Event('input'));
    if(device.disclosed)n('disclosed').click();
    assert.equal(n('serialPermission').checked,false,'Typed model selection never enables generic protocol probing');
    assert.equal(n('unassessed').checked,false);
    assert.equal(n('connect').disabled,false);
    await n('connect').onclick();await settledSetup();
    assert.equal(app.run('equipmentPanel.state.bindings.length'),1);
    await n('bindings').querySelector('[data-binding-action="use"]').onclick();
    const descriptor=copy(app.run('setupDescriptor'));
    assert.ok(descriptor.device_id.startsWith('instrument:'));
    assert.equal(descriptor.device.backend,'argyll-native');assert.equal(descriptor.device.serial,device.serial);
    assert.equal(descriptor.device.model,device.model);assert.equal(descriptor.provenance,'simulated');
    const binding=descriptor.current_binding_identity;
    assert.equal(binding.provenance.build_report_sha256,reportSHA);
    assert.equal(binding.provenance.description_helper_sha256,receipt.description_workflow.executable_sha256);
    assert.equal(binding.provenance.session_helper_sha256,receipt.generic_session.executable_sha256);
    // A scripted-serial C source is simulated, so setup presents it as Demo; the
    // physical presentation is covered by the recorded-source setup tests.
    assert.deepEqual([...doc.getElementById('device').options].map(item=>item.textContent),['Choose a Demo instrument','Demo · '+device.model+' · '+device.serial],'The simulated C source is labelled Demo, never as a connected instrument');
    assert.match(doc.getElementById('setupTitle').textContent,/Demo/);
    assert.equal(doc.getElementById('mode').value,'emissive_spot');
    app.window.nativeBatchName='Authenticated C '+device.persona+' fixture';
    app.run("$('batchName').value=window.nativeBatchName;createBatch()");await app.run('writes');
    assert.equal(app.run('ownerState.descriptor.device_id'),descriptor.device_id);
    for(let count=1;count<=(device.failure?1:2);count++){
      await app.run("$('start').onclick()");
      const deadline=Date.now()+10000;
      while(true){
        await app.run('refresh(true)');
        const state=app.run('ownerState');
        if(state.save_reason||state.cleanup_reason)throw Error(state.save_reason||state.cleanup_reason);
        if(state.phase==='idle'&&state.operations.length===count&&!state.companions.saving&&(device.failure?state.operations.at(-1).status==='failed':state.sample_count===count&&state.can_measure))break;
        if(!device.failure&&state.phase==='idle'&&state.operations.at(-1)?.status==='failed')throw Error(state.operations.at(-1).reason||'Native fixture measurement failed');
        if(Date.now()>deadline)throw Error('Authenticated C reading did not settle');
        await new Promise(resolve=>setTimeout(resolve,20));
      }
    }
    if(device.failure){
      assert.equal(app.run('ownerState.sample_count'),0);assert.equal(app.run('ownerState.draft.name'),'Sample #01');
      assert.match(doc.getElementById('lastAttempt').textContent,/Measurement failed.*0 readings retained/);
      assert.equal(doc.getElementById('lastAttempt').hidden,false);assert.equal(doc.getElementById('lastAttempt').classList.contains('attempt-failed'),true);
      const saved=await app.run("call('review')");
      assert.equal(saved.operations.length,1);assert.equal(saved.operations[0].result.status,'failed');
      assert.equal(saved.operations[0].result.patches.length,0);assert.ok(saved.operations[0].result.error);
      assert.equal(saved.operations[0].result.error_stage,'read_sample');
      assert.equal(saved.operations[0].result.error_native_code,0x191011,'The original C-driver error code survives saving');
      const configuration=Object.fromEntries(saved.operations[0].result.configuration);
      const native=JSON.parse(configuration.native_facts_json);
      assert.deepEqual(native.replies.filter(reply=>reply.context==='M'),[{status:'ER',code:-17,context:'M',value:'Not enough light'}]);
      assert.equal(native.capture.xyz,null);assert.equal(native.capture.spectrum,null);
      assert.equal(native.raw_replies.filter(reply=>reply.command==='M').length,1);
      assert.match(configuration.error_native_text,/-17/);
      assert.equal(app.messages.filter(message=>message.method==='command'&&message.params.action==='measure').length,1,'A failed native exposure is never retried automatically');
    }else{
      assert.equal(app.run('ownerState.saved_count'),2);assert.equal(app.run('ownerState.draft.name'),'Sample #03');
      assert.equal(app.run('ownerState.export_state'),'current','C-driver observations have a current automatic TI3 companion');
      for(const format of ['csv','mat'])assert.equal(app.run('ownerState.companions.formats.'+format+'.current'),true,format+' automatically retains the C-driver observations');
    }
    const observations=copy(app.run('state.rows.map(row=>row.obs)'));
    assert.equal(observations.length,device.failure?0:2);
    for(const observation of observations){
      assert.equal(observation.device.backend,'argyll-native');assert.equal(observation.device.serial,device.serial);
      assert.equal(observation.provenance,'simulated');assert.ok(observation.backend_reported.xyz);
      assert.equal(!!observation.backend_reported.spectrum,device.spectral);
      assert.ok(Object.fromEntries(observation.configuration).native_facts_json,'Original native driver facts survive the renderer and canonical save');
    }
    assert.equal(doc.querySelectorAll('#rows > tr').length,observations.length);
    assert.equal(doc.querySelectorAll('#spectrumPlot .curve').length,device.spectral?2:0);
    await app.run("send('finish',{})");
    await app.run("$('openSaved').onclick()");
    assert.equal(app.run('datasetBinding.kind'),'saved');
    assert.deepEqual(copy(app.run('state.rows.map(row=>row.obs)')),observations);
    if(device.failure){
      await app.run('openActivity()');
      const deadline=Date.now()+5000;
      while(!app.run('activityPanel.details')){if(Date.now()>deadline)throw Error('Saved native failure details did not load');await new Promise(resolve=>setTimeout(resolve,20));}
      assert.equal(app.run('activityPanel.roster.entries.length'),1);
      assert.equal(app.run('activityPanel.roster.entries[0].status'),'failed');
      assert.equal(doc.querySelectorAll('[data-activity-jump]').length,0,'A retained failure supplies no fabricated reading to inspect');
    }
    assert.equal(app.messages.filter(message=>message.method==='connect_device').length,1);
    assert.equal(app.messages.some(message=>['connect_direct_serial','choose_direct_port','preload_demo'].includes(message.method)),false);
    assert.deepEqual(fs.readFileSync(report),reportBytes,'The original native receipt remains unchanged');
  }finally{
    if(app){
      await app.run('writes');
      // Saved review also requests chart/analysis metadata. Let those ordinary
      // replies settle before destroying the DOM they are meant to update.
      for(let attempt=0;attempt<200&&app.run('pending.size||analysisPending||projectionPending||annotationLoads.size');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(app.run('pending.size||analysisPending||projectionPending||annotationLoads.size'),0,'The joined workflow leaves no pending renderer replies');
      app.close();
    }
    await peer.close();
  }
});

if(nativeSetupReport)test('actual i1Pro3 renderer joins configured modes new batch and saved review',{
  timeout:30000,
},async()=>{
  const report=path.resolve(nativeSetupReport),reportBytes=fs.readFileSync(report),receipt=JSON.parse(reportBytes);
  const reportSHA=createHash('sha256').update(reportBytes).digest('hex');
  const peer=pythonOwnerPeer({report,scenario:'i1pro3'});let app;
  try{
    app=await workspace(message=>peer.send(message));
    const doc=app.window.document,n=id=>doc.querySelector('[data-equipment="'+id+'"]');
    const settleSetup=async()=>{
      const deadline=Date.now()+10000;
      while(app.run('equipmentEffectPending()')){
        if(Date.now()>deadline)throw Error('i1Pro3 setup did not settle: '+n('error').textContent);
        await app.run('refresh(true)');await new Promise(resolve=>setTimeout(resolve,20));
      }
      assert.equal(n('error').hidden,true,n('error').textContent);
    };
    const settleOperation=async()=>{
      const deadline=Date.now()+10000;
      while(true){
        await app.run('refresh(true)');
        const owner=app.run('ownerState');
        if(owner.save_reason||owner.cleanup_reason)throw Error(owner.save_reason||owner.cleanup_reason);
        if(owner.phase==='idle'&&owner.operations.length&&!owner.companions.saving)return owner;
        if(Date.now()>deadline)throw Error('i1Pro3 operation did not settle');
        await new Promise(resolve=>setTimeout(resolve,20));
      }
    };
    assert.equal(app.run('ownerState'),null);
    assert.equal(app.run('equipmentPanel.state.bindings.length'),0);
    await app.run("$('startBatch').onclick()");
    await doc.getElementById('manageEquipment').onclick();await n('refresh').onclick();await settleSetup();
    assert.equal(doc.querySelectorAll('[data-candidate]').length,1);
    doc.querySelector('[data-candidate]').click();
    assert.equal(n('model').value,'instI1Pro3','The identified USB candidate supplies its model');
    n('serial').value='CM-CHART-0001';n('serial').dispatchEvent(new app.window.Event('input'));
    assert.equal(n('serialPermission').checked,false);
    assert.equal(n('connect').disabled,false);
    await n('connect').onclick();await settleSetup();
    await n('bindings').querySelector('[data-binding-action="use"]').onclick();
    const initial=copy(app.run('setupDescriptor'));
    assert.equal(initial.device.model,'X-Rite i1 Pro 3');assert.equal(initial.device.backend,'argyll-native');
    assert.equal(initial.provenance,'simulated');assert.ok(initial.device_id.startsWith('instrument:'));
    const identity=initial.current_binding_identity.provenance;
    assert.equal(identity.build_report_sha256,reportSHA);
    assert.equal(identity.description_helper_sha256,receipt.description_workflow.executable_sha256);
    assert.equal(identity.session_helper_sha256,receipt.generic_session.chart_executable_sha256);
    app.run("$('mode').value='reflective_spot';$('mode').onchange()");
    const initialFilter=doc.getElementById('setup-option-filter');
    assert.ok(initialFilter,'The generic leave-unchanged condition remains available');
    assert.equal([...initialFilter.options].some(option=>['m0','m1','m2'].includes(option.value)),false,
      'Initial capabilities do not invent explicit configured M0/M1/M2 offers');
    app.run("$('batchName').value='Configuration query';createBatch()");await app.run('writes');
    assert.ok([...doc.getElementById('workflowChoice').options].some(option=>option.value==='mode_snapshot'&&option.textContent==='Check instrument configuration'));
    app.run("$('workflowChoice').value='mode_snapshot';$('workflowChoice').onchange()");
    assert.equal(doc.getElementById('start').textContent,'Check instrument configuration');
    assert.ok(doc.getElementById('workflowHint').textContent.trim(),'The task supplies operator guidance');
    await doc.getElementById('start').onclick();
    const configured=await settleOperation();
    assert.equal(configured.operations.at(-1).status,'completed');assert.equal(configured.context_current,false);
    assert.equal(doc.getElementById('start').disabled,true,'Configuration changes require a fresh context');
    assert.match(doc.getElementById('operationStatus').textContent,/configuration|context|instrument/i);
    const firstFolder=configured.folder;
    await doc.getElementById('finish').onclick();await app.run('writes');
    assert.equal(doc.getElementById('finishedBatchActions').hidden,false);
    await doc.getElementById('newBatchAfterFinish').onclick();await app.run('writes');
    await doc.getElementById('setupDemo').onclick();await app.run('writes');
    const source=[...doc.getElementById('device').options].find(option=>option.value);
    assert.ok(source,'The initialized simulated instrument remains available after explicit simulation selection');
    doc.getElementById('device').value=source.value;await doc.getElementById('device').onchange();await app.run('writes');
    app.run("$('mode').value='reflective_spot';$('mode').onchange()");
    const filter=doc.getElementById('setup-option-filter');
    assert.ok(filter&&[...filter.options].some(option=>option.value==='m1'),'The configured descriptor offers M1');
    filter.value='m1';
    app.run("$('batchName').value='Configured M1 reading';createBatch()");await app.run('writes');
    assert.notEqual(app.run('ownerState.folder'),firstFolder,'The chooser supplies a genuinely new batch destination');
    assert.equal(doc.getElementById('workflowChoice').value,'spot','A new measurement batch does not silently retain the earlier configuration-query task');
    assert.equal(doc.getElementById('start').textContent,'Measure Sample #01');
    await doc.getElementById('start').onclick();const owner=await settleOperation();
    assert.equal(owner.operations.at(-1).status,'completed');assert.equal(owner.sample_count,1);
    assert.equal(owner.draft.name,'Sample #02');assert.equal(owner.saved_count,1);
    const observations=copy(app.run('state.rows.map(row=>row.obs)'));
    assert.equal(observations.length,1);const observation=observations[0];
    assert.equal(observation.actual_mode,'reflective_spot');assert.equal(observation.provenance,'simulated');
    assert.equal(observation.device.serial,'CM-CHART-0001');assert.equal(observation.backend_reported.xyz.unit,'1');
    assert.match(observation.backend_reported.xyz.scale,/relative/);assert.ok(observation.backend_reported.spectrum);
    assert.equal(doc.querySelectorAll('#rows > tr').length,1);assert.equal(doc.querySelectorAll('#spectrumPlot .curve').length,1);
    assert.match(doc.getElementById('latest').textContent,/relative/);
    assert.equal(owner.export_state,'current');
    for(const format of ['csv','mat'])assert.equal(owner.companions.formats[format].current,true);
    const saved=await app.run("call('review')");
    assert.equal(JSON.parse(saved.operations[0].result.request_json).options.filter,'m1');
    await doc.getElementById('finish').onclick();await app.run('writes');
    await doc.getElementById('openSaved').onclick();
    assert.equal(app.run('datasetBinding.kind'),'saved');
    assert.deepEqual(copy(app.run('state.rows.map(row=>row.obs)')),observations);
    assert.equal(app.messages.filter(message=>message.method==='connect_device').length,1);
    assert.equal(app.messages.some(message=>['connect_direct_serial','choose_direct_port','preload_demo'].includes(message.method)),false);
    assert.deepEqual(fs.readFileSync(report),reportBytes);
  }finally{
    if(app){
      await app.run('writes');
      for(let attempt=0;attempt<200&&app.run('pending.size||analysisPending||projectionPending||annotationLoads.size');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(app.run('pending.size||analysisPending||projectionPending||annotationLoads.size'),0);
      app.close();
    }
    await peer.close();
  }
});

test('a listed instrument with no available mode says why instead of offering an empty menu',async()=>{
  const reason='Direct connection requires explicit reconnect';
  const failed={synthetic:false,device_id:'instrument:cr100',device:{model:'Colorimetry Research CR-100',serial:'CR1'},modes:[{mode:'emissive_spot',label:'Ordinary direct light measurement',available:false,unavailable_reason:reason,workflows:[{workflow:'spot',available:true,min_patches:1,max_patches:1}]}],options:[]};
  const app=await workspace(null,{selections:[{selection_id:'failed-direct',device_id:failed.device_id,device:failed.device,synthetic:false}],descriptors:{'failed-direct':failed}});
  try{
    const doc=app.window.document,mode=doc.getElementById('mode');
    await app.run('setup(true)');
    doc.getElementById('device').value='failed-direct';doc.getElementById('device').dispatchEvent(new app.window.Event('change',{bubbles:true}));await app.run('writes');
    assert.equal(app.run('setupSelection'),'failed-direct');
    assert.equal(mode.disabled,true,'An empty mode menu is not offered');
    assert.equal(mode.selectedOptions[0].textContent,'No available mode: '+reason);
    assert.equal(doc.getElementById('create').disabled,true);
  }finally{await app.run('writes');app.close();}
});

test('real instrument setup stays unselected and Demo requires its own explicit action',async()=>{
  const real={synthetic:false,device_id:'instrument:pr655',device:{model:'Photo Research PR-655',serial:'PR123'},modes:[{mode:'emissive_spot',label:'Emissive spot',available:true,workflows:[{workflow:'spot',available:true,min_patches:1,max_patches:1}]}],options:[]};
  const selections=[{selection_id:'physical',device_id:real.device_id,device:real.device,synthetic:false},{selection_id:'selection',device_id:'demo:i1pro3',device:{model:'X-Rite i1 Pro 3'},synthetic:true}];
  const app=await workspace(null,{selections,descriptors:{physical:real}});
  try{
    const doc=app.window.document;
    await app.run('setup(true)');
    assert.equal(app.run('setupSelection'),null);
    assert.equal(doc.getElementById('device').value,'');
    assert.equal(doc.getElementById('mode').disabled,true);
    assert.equal(doc.getElementById('mode').selectedOptions[0].textContent,'Choose an instrument first');
    assert.equal(doc.getElementById('create').disabled,true);
    assert.deepEqual([...doc.getElementById('device').options].map(item=>item.value),['','physical']);
    assert.match(doc.getElementById('device').textContent,/PR-655.*PR123/);
    doc.getElementById('device').value='physical';doc.getElementById('device').dispatchEvent(new app.window.Event('change',{bubbles:true}));await app.run('writes');
    assert.equal(app.run('setupSelection'),'physical');assert.equal(doc.getElementById('create').disabled,false);assert.equal(doc.getElementById('mode').disabled,false);
    doc.getElementById('device').value='';doc.getElementById('device').dispatchEvent(new app.window.Event('change',{bubbles:true}));await app.run('writes');
    assert.equal(app.run('setupSelection'),null);assert.equal(doc.getElementById('create').disabled,true);
    assert.equal(doc.getElementById('mode').disabled,true);
    assert.equal(doc.getElementById('mode').selectedOptions[0].textContent,'Choose an instrument first');
    doc.getElementById('device').value='physical';doc.getElementById('device').dispatchEvent(new app.window.Event('change',{bubbles:true}));await app.run('writes');
    app.run("$('batchName').value='Real readings';createBatch()");await app.run('writes');
    assert.equal(app.messages.findLast(item=>item.method==='create_batch').params.selection_id,'physical');
    await doc.getElementById('setupDemo').onclick();
    assert.equal(app.run('setupSelection'),'selection');
    assert.match(doc.getElementById('setupTitle').textContent,/Demo/);
    assert.deepEqual([...doc.getElementById('device').options].map(item=>item.value),['','selection']);
    await app.run('setup(true)');assert.equal(app.run('setupSelection'),null,'New real setup never inherits a Demo choice');
    assert.equal(app.messages.some(item=>['refresh_devices','connect_device','connect_direct_serial'].includes(item.method)),false);
  }finally{await app.run('writes');app.close();}
});

// `selected` is the startup failure after selection succeeded ("changed during
// startup"): a report is selected, so only the unavailable guard can disable List.
for(const [failed,selected] of [[false,true],[true,false],[true,true]])test('packaged instrument software '+(failed?'failure stays visible without falling back to Demo'+(selected?' after its report was selected':''):'is ready without an operator report chooser'),async()=>{
  const reason=failed?'Instrument software could not be loaded. Reinstall this application from a complete build.':null;
  const app=await workspace(null,{equipmentState:{generation:selected?1:0,listing_generation:0,state:selected?'ready':'unselected',reason:null,report:selected?{path:'/application/Resources/instrument-software/report.json',sha256:'a'.repeat(64),context:{}}:null,catalog:null,candidates:[],bindings:[],active_setup:null,instrument_software:{packaged:true,ready:!failed,error:reason}}});
  try{
    const doc=app.window.document,n=id=>doc.querySelector('[data-equipment="'+id+'"]');
    assert.equal(doc.getElementById('instrumentSoftwareError').hidden,!failed);
    await doc.getElementById('connectInstrument').onclick();
    assert.equal(app.run('activePanel'),'equipmentDialog');
    assert.equal(n('reportPicker').hidden,true);assert.equal(n('report').disabled,true);
    assert.equal(n('connected').hidden,true,'Empty connected-instrument sections do not compete with the connection action');
    assert.equal(n('refresh').disabled,failed);
    assert.equal(n('directPort').disabled,failed,'A failed packaged backend cannot offer a port chooser that the host refuses');
    if(failed)assert.match(n('status').textContent,/Reinstall/);
    assert.equal(app.messages.some(item=>['choose_build_report','refresh_devices','connect_device'].includes(item.method)),false,'Opening never lists or connects');
    await doc.getElementById('dismissEquipment').onclick();
    assert.equal(app.run('activePanel'),null,'Closing a connection panel opened from the workspace does not open another panel');
    await doc.getElementById('startBatch').onclick();
    assert.equal(app.run('setupSelection'),null);assert.equal(doc.getElementById('setupNoInstrument').hidden,false);
    await doc.getElementById('setupDemo').onclick();assert.equal(app.run('setupDescriptor.synthetic'),true,'Demo remains an explicit independent choice');
  }finally{await app.run('writes');app.close();}
});

test('leaving chart Demo setup does not label another source with its simulated name and notes',async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document,value=id=>doc.getElementById(id).value;
    await app.run('setup(true,null,true)');
    assert.equal(value('batchName'),'Guided chart Demo');assert.match(value('batchNotes'),/not physical/);
    await doc.getElementById('manageEquipment').onclick();await doc.getElementById('dismissEquipment').onclick();
    assert.equal(app.run('activePanel'),'setupDialog','Closing the panel that setup opened returns to setup');
    assert.equal(app.run('setupChartDemo'),false);
    assert.equal(value('batchName'),'','A physical batch does not inherit the chart Demo name');
    assert.equal(value('batchNotes'),'','A physical batch does not inherit notes calling it simulated');
    await app.run('setup(true,null,true)');doc.getElementById('batchName').value='Operator chart name';
    await doc.getElementById('setupDemo').onclick();
    assert.equal(value('batchName'),'Operator chart name','Text the operator typed is kept');
    assert.equal(value('batchNotes'),'');
  }finally{await app.run('writes');app.close();}
});

test('equipment panel lists explicitly and initializes only the acknowledged selected candidate',async()=>{
  const app=await workspace();const peer=recordingEquipment(app);
  try{
    const document=app.window.document,n=id=>document.querySelector('[data-equipment="'+id+'"]');
    const readings=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');
    assert.deepEqual(peer.messages.map(message=>message.method),['equipment_setup'],'Opening reads only the setup snapshot');
    await n('report').onclick();assert.equal(n('refresh').disabled,false);
    assert.deepEqual(peer.messages[1].params,{setup_generation:0},'Renderer supplies no report path');
    await n('refresh').onclick();
    assert.equal(app.run('equipmentPanel.setupPending'),true,'A listing replies pending, as the host does');
    await peer.settle();
    assert.equal(app.run('equipmentPanel.setupPending'),false);
    assert.doesNotMatch(n('status').textContent,/initialized/,'A completed listing initialized nothing');
    assert.equal(peer.messages.filter(message=>message.method==='refresh_devices').length,1);
    document.querySelector('[data-candidate]').click();
    await app.run('writes');
    assert.equal(n('timeout').value,'300');assert.equal(n('connect').disabled,true);
    n('disclosed').click();assert.equal(n('connect').disabled,false);
    await n('connect').onclick();await peer.settle();
    assert.equal(n('status').textContent,'Device initialized. It is ready for a new batch.');
    assert.equal(peer.initializations,1);assert.equal(document.querySelector('[data-candidate]').disabled,true);
    const sent=peer.messages.find(message=>message.method==='connect_device');
    assert.equal(sent.params.listing_generation,1);assert.equal(sent.params.candidate_id,'candidate-1');
    assert.equal(sent.params.accept_disclosed_initialization,true);assert.equal(sent.params.accept_unassessed_initialization,false);
    assert.equal(sent.params.allow_serial_identification,false);assert.equal(sent.params.expect,null);
    assert.equal(sent.params.timeout,300);assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),readings);
    await n('refresh').onclick();await peer.settle();document.querySelector('[data-candidate]').click();await app.run('writes');
    assert.equal(n('disclosed').checked,false,'A new listing never carries initialization acceptance forward');
    assert.equal(n('connect').disabled,true);
    await document.querySelector('[data-binding-action="close"]').onclick();
    assert.match(n('bindings').textContent,/Removed from app/);
  }finally{await app.run('writes');app.close();}
});

test('equipment delayed initialization reconciles its original identity and blocks acquisition',async()=>{
  const app=await workspace();const peer=recordingEquipment(app);
  try{
    const document=app.window.document,n=id=>document.querySelector('[data-equipment="'+id+'"]');
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');await n('report').onclick();await n('refresh').onclick();await peer.settle();document.querySelector('[data-candidate]').click();await app.run('writes');n('disclosed').click();
    const originalTimer=app.window.setTimeout;let expire;
    app.window.setTimeout=(callback,delay,...args)=>delay===30000?(expire=callback,0):originalTimer(callback,delay,...args);
    peer.dropNextConnect();const initialized=n('connect').onclick();
    assert.equal(document.getElementById('start').disabled,true);
    await new Promise(resolve=>setImmediate(resolve));expire();await initialized;app.window.setTimeout=originalTimer;
    assert.equal(peer.initializations,1);assert.equal(n('reconcile').hidden,false);
    await app.run("begin('Measure')");await app.run('writes');
    assert.equal(app.messages.some(message=>message.params.action==='measure'),false);
    // With a Demo selected, only the unresolved initialization can disable Create.
    await app.run('setup(true,null,false,true)');
    assert.equal(document.getElementById('create').disabled,true);assert.equal(document.getElementById('applySetup').disabled,true);
    app.run("$('batchName').value='Delayed device batch';createBatch()");await app.run('writes');
    assert.equal(app.messages.some(message=>message.method==='create_batch'),false);
    assert.match(document.getElementById('setupError').textContent,/check its delayed result/);
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');
    await n('reconcile').onclick();
    assert.equal(peer.initializations,1);assert.equal(n('reconcile').hidden,true);
    const requests=peer.messages.filter(message=>message.method==='connect_device');
    assert.equal(requests.length,1);assert.deepEqual(peer.messages.findLast(message=>message.method==='equipment_outcome').params,{command_id:requests[0].params.command_id},'A lost reply queries the original command without repeating initialization');
    await app.run('setup(true,null,false,true)');assert.equal(document.getElementById('create').disabled,false);
  }finally{await app.run('writes');app.close();}
});

test('a pending Argyll listing names its stages and its cancellation without initialization',async()=>{
  const active={command_id:'argyll-listing',action:'refresh_devices',backend:'argyll',stage:'enumeration',cancel_requested:false,cancellation_mode:'after_initialization'};
  const setupState={generation:1,listing_generation:1,state:'ready',reason:null,report:{path:'/recording/report.json',sha256:'a'.repeat(64),context:{}},catalog:null,candidates:[],bindings:[],active_setup:active};
  const app=await workspace(null,{equipmentState:setupState});
  const original=app.window.ownerTransport.postMessage;
  app.window.ownerTransport.postMessage=message=>{
    if(!['equipment_setup','equipment_outcome','cancel_setup'].includes(message.method))return original(message);
    if(message.method==='cancel_setup')active.cancel_requested=true;
    const value=message.method==='equipment_setup'?copy(setupState):{command_id:'argyll-listing',state:'pending',reason:null,result:null};
    app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:true,value}));
  };
  try{
    const node=id=>app.window.document.querySelector('[data-equipment="'+id+'"]');
    assert.equal(node('progress').textContent,'Listing connected instruments… Cancellation discards this listing.');
    assert.equal(node('cancelSetup').textContent,'Cancel listing');
    for(const [stage,label] of [['description','Initializing instrument…'],['binding','Preparing the measurement session…']]){
      Object.assign(active,{action:'connect_device',stage});await app.run('equipmentPanel.open()');
      assert.match(node('progress').textContent,new RegExp('^'+label),stage+' has its own label');
    }
    Object.assign(active,{action:'refresh_devices',stage:'enumeration'});await app.run('equipmentPanel.open()');
    await node('cancelSetup').onclick();
    assert.equal(node('progress').textContent,'Cancellation requested. Waiting for the listing to finish; it will not be shown.');
    assert.doesNotMatch(node('progress').textContent,/initialization|unconfirmed/);
  }finally{app.close();}
});

test('a delayed setup reply that was never admitted releases the workspace',async()=>{
  const app=await workspace();const peer=recordingEquipment(app);
  try{
    const document=app.window.document,n=id=>document.querySelector('[data-equipment="'+id+'"]');
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');await n('report').onclick();
    // The refusal's reply is lost behind a stalled owner, and the double never admits it.
    const original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='refresh_devices'?undefined:original(message);
    const originalTimer=app.window.setTimeout;let expire;
    app.window.setTimeout=(callback,delay,...args)=>delay===30000?(expire=callback,0):originalTimer(callback,delay,...args);
    const listing=n('refresh').onclick();await new Promise(resolve=>setImmediate(resolve));expire();await listing;app.window.setTimeout=originalTimer;
    assert.equal(app.run('equipmentPanel.setupPending'),true,'A delayed reply keeps setup pending');
    await peer.settle();
    assert.equal(app.run('equipmentPanel.setupPending'),false,'An unknown command was never admitted');
    assert.match(n('error').textContent,/not admitted, so nothing was started/);
    assert.equal(n('refresh').disabled,false);
  }finally{app.close();}
});

test('pending Argyll setup reconciles its original command and makes deferred cancellation explicit',async()=>{
  const setupState={generation:1,listing_generation:1,state:'initializing',reason:null,report:{path:'/recording/report.json',sha256:'a'.repeat(64),context:{}},catalog:null,candidates:[],bindings:[],active_setup:{command_id:'argyll-pending',action:'connect_device',backend:'argyll',stage:'description',cancel_requested:false,cancellation_mode:'after_initialization'}};
  const app=await workspace(null,{equipmentState:setupState}),calls=[];
  let terminal=false;
  const original=app.window.ownerTransport.postMessage;
  app.window.ownerTransport.postMessage=message=>{
    if(!['equipment_setup','equipment_outcome','cancel_setup'].includes(message.method))return original(message);
    calls.push(copy(message));
    if(message.method==='cancel_setup')setupState.active_setup.cancel_requested=true;
    const value=message.method==='equipment_setup'?copy(setupState):{command_id:'argyll-pending',state:terminal?'failed':'pending',reason:terminal?'Connection cancelled after initialization finished. Instrument state is unconfirmed.':null,result:null};
    app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:true,value}));
  };
  try{
    const node=id=>app.window.document.querySelector('[data-equipment="'+id+'"]');
    assert.equal(app.run('activePanel'),'equipmentDialog','Reload restores the pending setup');
    assert.match(node('progress').textContent,/^Initializing instrument… Cancellation waits for initialization/,'Argyll description is labelled by the stage Python records');
    assert.equal(node('cancelSetup').textContent,'Cancel connection');
    await node('cancelSetup').onclick();
    assert.match(node('progress').textContent,/Cancellation requested.*Waiting.*unconfirmed/);
    assert.equal(node('cancelSetup').disabled,true);
    app.run("closePanel('equipmentDialog')");await app.run('refresh(true)');
    assert.equal(app.run('equipmentEffectPending()'),true,'Navigation cannot release pending setup');
    assert.equal(calls.some(message=>['connect_device','refresh_devices'].includes(message.method)),false);
    setupState.active_setup=null;terminal=true;
    await app.run('refresh(true)');
    assert.equal(app.run('equipmentEffectPending()'),false);
    assert.ok(calls.filter(message=>message.method==='equipment_outcome').every(message=>message.params.command_id==='argyll-pending'));
    assert.deepEqual(calls.find(message=>message.method==='cancel_setup').params,{command_id:'argyll-pending'});
    assert.equal(app.run('state.rows.length'),2,'Setup preserves existing measurements');
  }finally{app.close();}
});

test('equipment snapshot loading fences early actions and superseded openings',async()=>{
  const app=await workspace(),peer=recordingEquipment(app),held=[];
  const original=app.window.ownerTransport.postMessage;
  app.window.ownerTransport.postMessage=message=>message.method==='equipment_setup'?held.push(copy(message)):original(message);
  try{
    const n=id=>app.window.document.querySelector('[data-equipment="'+id+'"]');
    app.run("showPanel('equipmentDialog')");const first=app.run('equipmentPanel.open()');
    assert.equal(n('report').disabled,true);assert.equal(n('refresh').disabled,true);
    await n('report').onclick();await n('refresh').onclick();await peer.settle();
    assert.equal(peer.messages.length,0,'No mutation starts before a snapshot');
    app.run("closePanel('equipmentDialog');showPanel('equipmentDialog')");const second=app.run('equipmentPanel.open()');
    app.window.ownerReply({id:held[1].id,ok:true,value:{...copy(peer.state),generation:8}});await second;
    assert.equal(n('report').disabled,false);
    app.window.ownerReply({id:held[0].id,ok:true,value:copy(peer.state)});await first;
    assert.equal(app.run('equipmentPanel.state.generation'),8);
    const failed=app.run('equipmentPanel.open()');
    app.window.ownerReply({id:held[2].id,ok:false,error:'Snapshot unavailable'});await failed;
    assert.equal(n('report').disabled,true);assert.match(n('error').textContent,/Snapshot unavailable/);
  }finally{app.close();}
});

test('equipment reopening requires the retained enum in the current catalog',async()=>{
  const app=await workspace(),peer=recordingEquipment(app);
  try{
    const document=app.window.document,n=id=>document.querySelector('[data-equipment="'+id+'"]');
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');await n('report').onclick();await n('refresh').onclick();await peer.settle();
    document.querySelector('[data-candidate]').click();await app.run('writes');n('disclosed').click();await n('connect').onclick();await peer.settle();await n('refresh').onclick();await peer.settle();
    peer.state.catalog.instrument_types=[];peer.state.generation++;
    await app.run('equipmentPanel.open()');document.querySelector('[data-candidate]').click();await app.run('writes');
    const reopen=document.querySelector('[data-binding-action="reopen"]');
    assert.equal(reopen.disabled,true);assert.match(n('bindings').textContent,/model is unavailable/);
    // A retained binding cannot bypass the catalog check even if its old controls survive.
    app.run('equipmentPanel.binding=equipmentPanel.state.bindings[0];equipmentPanel.renderSelection(true)');
    n('disclosed').click();assert.equal(n('connect').disabled,true);
    assert.match(n('initialization').textContent,/model is unavailable/);
    await n('connect').onclick();await peer.settle();assert.equal(peer.initializations,1);
  }finally{app.close();}
});

test('equipment Use serializes source selection and refuses a vanished explicit source',async()=>{
  const app=await workspace(),peer=recordingEquipment(app),held=[];
  try{
    const document=app.window.document,n=id=>document.querySelector('[data-equipment="'+id+'"]');
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');await n('report').onclick();await n('refresh').onclick();await peer.settle();
    document.querySelector('[data-candidate]').click();await app.run('writes');n('disclosed').click();await n('connect').onclick();await peer.settle();
    const original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='settings'?held.push(copy(message)):original(message);
    const using=document.querySelector('[data-binding-action="use"]').onclick();
    assert.equal(document.querySelector('[data-binding-action="close"]').disabled,true);
    await document.querySelector('[data-binding-action="close"]').onclick();
    assert.equal(peer.messages.some(message=>message.method==='close_device'),false);
    app.window.ownerReply({id:held[0].id,ok:true,value:{selections:[{selection_id:'explicit-source',device_id:'instrument:recording'}]}});
    await new Promise(resolve=>setImmediate(resolve));
    app.window.ownerReply({id:held[1].id,ok:true,value:{selections:[{selection_id:'demo-source',device_id:'demo:i1pro3'}]}});
    await using;
    assert.match(document.getElementById('setupError').textContent,/selected device source changed/);
    assert.equal(document.getElementById('create').disabled,true);
    assert.equal(app.run('setupSelection'),null,'No implicit Demo fallback after an explicit selection');
  }finally{await app.run('writes');app.close();}
});

test('equipment unidentified serial and reopening acknowledgements use authoritative policies',async()=>{
  const app=await workspace(),peer=recordingEquipment(app);
  try{
    const document=app.window.document,n=id=>document.querySelector('[data-equipment="'+id+'"]');
    app.run("showPanel('equipmentDialog')");await app.run('equipmentPanel.open()');await n('report').onclick();await n('refresh').onclick();await peer.settle();
    peer.state.candidates[0].identified=false;
    const model=peer.state.catalog.instrument_types[0];model.generic_serial_identification=false;model.typed_serial_selection=false;
    await app.run('equipmentPanel.open()');document.querySelector('[data-candidate]').click();await app.run('writes');
    n('disclosed').click();assert.equal(n('connect').disabled,true);
    assert.match(n('initialization').textContent,/cannot identify an unrecognized connection/);
    model.typed_serial_selection=true;await app.run('equipmentPanel.open()');
    assert.equal(n('connect').disabled,false,'Explicit typed-serial policy permits its declared model');
    model.typed_serial_selection=false;model.generic_serial_identification=true;await app.run('equipmentPanel.open()');
    assert.equal(n('connect').disabled,true,'Generic identification still requires acknowledgement');
    n('serialPermission').click();n('serial').value='SERIAL-1';assert.equal(n('connect').disabled,false);
    await n('connect').onclick();await peer.settle();const connected=peer.messages.find(message=>message.method==='connect_device');
    assert.equal(connected.params.expect,'instI1Disp3');assert.equal(connected.params.expected_serial,'SERIAL-1');assert.equal(connected.params.allow_serial_identification,true);
    await n('refresh').onclick();await peer.settle();document.querySelector('[data-candidate]').click();await app.run('writes');
    document.querySelector('[data-binding-action="reopen"]').click();
    await app.run('writes');
    assert.equal(n('disclosed').checked,false);assert.equal(n('serialPermission').checked,false);
    n('disclosed').click();n('serialPermission').click();await n('connect').onclick();await peer.settle();
    const reopened=peer.messages.find(message=>message.method==='reopen_device');
    assert.equal(reopened.params.binding_id,'binding');assert.equal(reopened.params.binding_generation,1);
    assert.equal('expect' in reopened.params,false);assert.equal('expected_serial' in reopened.params,false);
  }finally{app.close();}
});

function offerCorrections(app){
  const descriptor=app.batch.descriptor;descriptor.capability_id='capability-correction';
  descriptor.modes.push({...copy(descriptor.modes[0]),mode:'ambient',label:'Ambient'});
  descriptor.options=[
    {id:'average',label:'Averages',type:'integer',default:1,minimum:1,maximum:20,by_mode:{emissive_spot:{available:true},ambient:{available:true}}},
    {id:'correction',label:'Correction',type:'native_file',by_mode:{emissive_spot:{available:true,kinds:['ccmx']},ambient:{available:false,kinds:[],unavailable_reason:'Ambient does not accept corrections'}}},
  ];
  return descriptor;
}

test('correction choice follows selected mode and Create carries only its opaque token',async()=>{
  const app=await workspace(),held=[];
  try{
    offerCorrections(app);const document=app.window.document;
    await app.run('setup(true,null,false,true)');document.getElementById('mode').value='ambient';document.getElementById('mode').onchange();
    assert.equal(document.getElementById('chooseCorrection').disabled,true);
    document.getElementById('mode').value='emissive_spot';document.getElementById('mode').onchange();
    assert.equal(document.getElementById('chooseCorrection').disabled,false);assert.match(document.getElementById('correctionKinds').textContent,/CCMX/);
    const original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='choose_correction'?held.push(copy(message)):original(message);
    document.getElementById('setup-option-average').value='4';
    const chosen=document.getElementById('chooseCorrection').onclick();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(document.getElementById('create').disabled,true);assert.equal(document.getElementById('setup-option-average').disabled,true);
    assert.deepEqual(held[0].params,{selection_id:'selection',mode:'emissive_spot',capability_id:'capability-correction',settings_generation:7,options:{average:4,correction:null}});
    app.window.ownerReply({id:held[0].id,ok:true,value:{correction:{token:'held-token'},material:{kind:'ccmx',source_name:'display.ccmx',source_sha256:'source',material_sha256:'material'}}});await chosen;
    assert.equal(document.getElementById('setup-option-average').value,'4');assert.equal(document.getElementById('create').disabled,false);
    app.run("$('batchName').value='Correction batch';createBatch()");await app.run('writes');
    const create=app.messages.find(message=>message.method==='create_batch');assert.deepEqual(create.params.options,{average:4,correction:{token:'held-token'}});
    assert.equal('path' in create.params.options.correction,false);
  }finally{app.close();}
});

test('current correction chooser preserves primitive edits and Apply or Clear binds exact draft',async()=>{
  const app=await workspace(),held=[];
  try{
    offerCorrections(app);app.batch.draft.options={average:1,correction:{token:'previous'}};
    app.packet.correction_material={kind:'ccmx',source_name:'previous.ccmx'};await app.run('refresh(true)');app.run('setup()');
    const document=app.window.document,original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='choose_current_correction'?held.push(copy(message)):original(message);
    document.getElementById('setup-option-average').value='5';const chosen=document.getElementById('chooseCorrection').onclick();await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(held[0].params,{batch_id:'batch',generation:1,context_id:'context',draft_id:'draft',version:0,options:{average:5,correction:{token:'previous'}}});
    app.window.ownerReply({id:held[0].id,ok:true,value:{correction:{token:'new-token'},material:{kind:'ccmx',source_name:'new.ccmx'}}});await chosen;
    assert.equal(document.getElementById('setup-option-average').value,'5');await document.getElementById('applySetup').onclick();
    const edits=()=>app.messages.filter(message=>message.method==='command'&&message.params.action==='edit_draft');
    assert.deepEqual(edits()[0].params.payload.options,{average:5,correction:{token:'new-token'}});
    assert.equal(edits()[0].params.payload.draft_id,'draft');assert.equal(edits()[0].params.payload.version,0);
    app.run('setup()');document.getElementById('clearCorrection').click();await document.getElementById('applySetup').onclick();
    assert.deepEqual(edits()[1].params.payload.options,{average:5,correction:null});assert.equal(edits()[1].params.payload.version,1);
  }finally{app.close();}
});

test('correction cancellation failure and stale replies retain the current material and edits',async()=>{
  const app=await workspace(),held=[];
  try{
    offerCorrections(app);app.batch.draft.options={average:1,correction:{token:'previous'}};
    app.packet.correction_material={kind:'ccmx',source_name:'previous.ccmx'};await app.run('refresh(true)');app.run('setup()');
    const document=app.window.document,original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='choose_current_correction'?held.push(copy(message)):original(message);
    document.getElementById('setup-option-average').value='6';
    for(const failure of [false,true]){
      const choosing=document.getElementById('chooseCorrection').onclick();await new Promise(resolve=>setImmediate(resolve));
      const message=held.at(-1);app.window.ownerReply(failure?{id:message.id,ok:false,error:'Invalid correction material'}:{id:message.id,ok:true,value:null});await choosing;
      assert.equal(document.getElementById('setup-option-average').value,'6');assert.match(document.getElementById('correctionStatus').textContent,/previous.ccmx/);
      assert.equal(document.getElementById('applySetup').disabled,false);
      if(failure)assert.match(document.getElementById('setupError').textContent,/Invalid correction/);
    }
    const stale=document.getElementById('chooseCorrection').onclick();await new Promise(resolve=>setImmediate(resolve));
    app.run("closePanel('setupDialog');setup()");
    app.window.ownerReply({id:held.at(-1).id,ok:true,value:{correction:{token:'stale'},material:{kind:'ccmx',source_name:'stale.ccmx'}}});await stale;
    assert.match(document.getElementById('correctionStatus').textContent,/previous.ccmx/);assert.equal(document.getElementById('applySetup').disabled,false);
  }finally{app.close();}
});

test('Create refusal is visible before closing the setup dialog', async () => {
  const app = await workspace();
  try {
    app.run("setup(true); $('batchName').value='   '; createBatch()");
    await app.run('writes');
    assert.equal(app.window.document.getElementById('setupDialog').hidden, false);
    assert.match(app.window.document.getElementById('setupDialog').textContent, /Enter a batch name/);
    assert.equal(app.messages.some(message => message.method === 'create_batch'),false);
  } finally { app.close(); }
});

test('automatic file progress stays quiet and unavailable chart output is ordinary', async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document;
    assert.equal(doc.getElementById('automaticFileNotice').hidden,true);
    assert.equal(doc.getElementById('automaticFiles').hidden,false);
    assert.equal(doc.getElementById('automaticFiles').open,false);
    assert.match(doc.getElementById('automaticFileList').textContent,/MAT · Saved/);
    assert.match(doc.getElementById('automaticFileList').textContent,/TI3 · Saved/);
    assert.match(doc.getElementById('automaticFileList').textContent,/This is not a chart batch/);
    const detail=doc.querySelector('#automaticFileList details[data-format="mat"]');
    detail.open=true;detail.querySelector('summary').focus();
    app.batch.phase='active';
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'stale',current:false,retryable:true,refresh_pending:true};
    await app.run('refresh(true)');
    assert.equal(doc.getElementById('automaticFileNotice').hidden,true,'The owner witnesses this exact deferred refresh');
    assert.equal(doc.getElementById('saveState').textContent,'Files pending…');
    assert.match(doc.getElementById('automaticFileList').textContent,/MAT · Waiting to save/);
    app.batch.phase='idle';
    app.batch.companions.saving=true;
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'preparing',current:false,refresh_pending:false};
    await app.run('refresh(true)');
    assert.equal(doc.getElementById('automaticFileNotice').hidden,true);
    assert.equal(doc.getElementById('saveState').textContent,'Saving files…');
    assert.match(doc.getElementById('automaticFileList').textContent,/MAT · Preparing…/);
    assert.equal(doc.querySelector('#automaticFileList details[data-format="mat"]').open,true);
    assert.equal(doc.activeElement.closest('[data-format]').dataset.format,'mat');
  }finally{app.close();}
});

test('automatic file progress cannot hide unwitnessed stale or failed output',async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document;
    app.batch.phase='active';
    for(const witness of [false,'true']){
      app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'stale',current:false,retryable:true,refresh_pending:witness};
      await app.run('refresh(true)');
      assert.equal(doc.getElementById('automaticFileNotice').hidden,false,'Active work or a truthy field does not qualify pending refresh');
      assert.equal(doc.getElementById('saveState').textContent,'Files need attention');
      assert.match(doc.getElementById('automaticFileMessage').textContent,/MAT is out of date/);
    }
    app.batch.phase='idle';
    app.batch.companions.formats.csv={...app.batch.companions.formats.csv,state:'stale',current:false,retryable:true,refresh_pending:true};
    for(const failure of ['failed','unavailable']){
      app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:failure,current:false,retryable:failure==='failed',refresh_pending:true,reason:'Controlled refusal'};
      await app.run('refresh(true)');
      assert.equal(doc.getElementById('automaticFileNotice').hidden,false,'Another pending format cannot hide this refusal');
      assert.equal(doc.getElementById('saveState').textContent,'Files need attention');
      assert.match(doc.getElementById('automaticFileMessage').textContent,/MAT/);
      assert.doesNotMatch(doc.getElementById('automaticFileMessage').textContent,/CSV/);
      assert.match(doc.getElementById('automaticFileList').textContent,/Controlled refusal/);
    }
  }finally{app.close();}
});

test('unavailable MAT runtime offers details without ineffective destination recovery', async()=>{
  const app=await workspace();
  try{
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'unavailable',current:false,reason:'MAT files are unavailable in this app build.',payload_sha256:null,retryable:false};
    await app.run('refresh(true)');
    const doc=app.window.document;
    assert.equal(doc.getElementById('automaticFileNotice').hidden,false);
    assert.equal(doc.getElementById('retryFiles').hidden,true);
    assert.equal(doc.getElementById('anotherFiles').hidden,true);
    assert.equal(doc.getElementById('showAutomaticFiles').hidden,false);
  }finally{app.close();}
});

test('automatic file failure preserves saved readings and retries through its owner', async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document;
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'failed',current:true,reason:'Controlled disk error',retryable:true,publication:{file_sync:'fullsync',directory_synced:false}};
    await app.run('refresh(true)');
    assert.equal(doc.getElementById('automaticFileNotice').hidden,false);
    assert.equal(doc.getElementById('saveState').textContent,'Files need attention');
    assert.match(doc.getElementById('automaticFileList').textContent,/MAT · Save failed/);
    assert.equal(app.run('state.rows.every(row=>row.saved)'),true);
    assert.equal(doc.getElementById('retryFiles').disabled,false);
    doc.getElementById('retryFiles').click();await app.run('writes');
    const retry=app.messages.findLast(message=>message.method==='command');
    assert.equal(retry.params.action,'retry_companions');
    assert.deepEqual(retry.params.payload,{});
    assert.equal(app.messages.some(message=>message.method==='command'&&message.params.action==='measure'),false);
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'current',current:true,reason:null,retryable:false};
    await app.run('refresh(true)');
    assert.equal(doc.getElementById('automaticFileNotice').hidden,true);
    assert.equal(doc.getElementById('saveState').textContent,'Saved');
  }finally{app.close();}
});

test('saved review keeps automatic file recovery bound to the live workspace', async()=>{
  const app=await workspace();
  try{
    app.batch.companions.formats.csv={...app.batch.companions.formats.csv,state:'stale',current:false,reason:'Source advanced',retryable:true};
    const binding=copy(app.packet.dataset_binding);binding.kind='saved';binding.id='saved-binding';binding.read_only=true;
    app.packet.dataset_binding=binding;
    await app.run('refresh(true)');
    const doc=app.window.document;
    assert.equal(doc.getElementById('automaticFileNotice').hidden,false);
    assert.equal(doc.getElementById('retryFiles').disabled,true);
    assert.equal(doc.getElementById('anotherFiles').disabled,true);
    assert.match(doc.getElementById('automaticFileMessage').textContent,/Return to live/);
    assert.equal(doc.getElementById('showAutomaticFiles').hidden,true);
    const before=app.messages.length;
    doc.getElementById('retryFiles').click();await app.run('writes');
    assert.equal(app.messages.slice(before).some(message=>message.method==='command'),false);
  }finally{app.close();}
});

test('automatic TI3 failure uses the existing TI3 recovery owner', async()=>{
  const app=await workspace();
  try{
    app.batch.export_state='stale';app.batch.export_reason='Controlled TI3 failure';
    app.batch.ti3={retryable:true,unavailable:[]};
    await app.run('refresh(true)');
    const doc=app.window.document;
    assert.equal(doc.getElementById('automaticFileNotice').hidden,false);
    assert.match(doc.getElementById('automaticFileMessage').textContent,/TI3 is out of date/);
    doc.getElementById('retryFiles').click();await app.run('writes');
    const actions=app.messages.filter(message=>message.method==='command').map(message=>message.params.action);
    assert.deepEqual(actions,['export_ti3']);
  }finally{app.close();}
});

test('TI3 details group dense unavailable reasons and keep ordinary absence out of recovery',async()=>{
  const app=await workspace();
  try{
    app.batch.ti3={retryable:false,unavailable:Array.from({length:160},(_,index)=>({record_id:'original-'+index,component:'spectrum',reason:'This instrument provides XYZ without a spectrum'})),files:[{group_id:'group-1',scope:'ordinary',state:'current',row_count:160,total_rows:null,reason:'',path:'/synthetic/batch/readings.ti3'}]};
    app.batch.ti3.unavailable.push(copy(app.batch.ti3.unavailable[0]));
    app.batch.ti3.unavailable.push({record_id:'scalar-original',component:'record',reason:'CTI3 cannot represent scalar outcomes <kept in JSON/MAT>'});
    await app.run('refresh(true)');
    const document=app.window.document,list=document.getElementById('automaticFileList'),details=list.querySelector('[data-format="ti3"]');
    assert.equal(document.getElementById('automaticFileNotice').hidden,true);
    assert.match(list.textContent,/TI3 · Saved · Some values unavailable/);
    assert.equal(details.open,false);
    assert.match(details.textContent,/Spectrum · 160 records: This instrument provides XYZ/);
    assert.match(details.textContent,/Reading · 1 record: CTI3 cannot represent scalar outcomes <kept in JSON\/MAT>/);
    assert.match(details.textContent,/Readings · Saved · 160 records/);
    assert.equal(details.querySelectorAll(':scope > ul > li').length,3,'Repeated reasons have one row rather than 160 paragraphs');
    assert.equal(details.querySelector('kept'),null,'Reasons remain escaped text');
    assert.equal(details.textContent.includes('original-159'),false,'Opaque IDs stay out of operator copy');
    details.open=true;const file=details.querySelector('details');file.open=true;file.querySelector('summary').focus();
    app.batch.ti3.files[0].state='stale';app.batch.ti3.files[0].reason='The source changed after this export';
    app.batch.revision++;await app.run('refresh(true)');
    const updated=list.querySelector('[data-format="ti3"]');
    assert.equal(updated.open,true);assert.equal(updated.querySelector('details').open,true);
    assert.match(updated.textContent,/Readings · Out of date/);
    assert.equal(document.activeElement.closest('[data-format]').dataset.format,'ti3-file:group-1');
    assert.equal(app.messages.some(message=>message.method==='command'),false,'Reading file details never reacquires or retries');
  }finally{app.close();}
});

test('annotation keeps A text and action identity through a B owner revision and queue completion', async () => {
  const app = await workspace();
  try {
    app.run("state.follow=false; state.inspector=true; state.selected=state.rows.find(r=>r.id==='B'); render()");
    await new Promise(resolve => setImmediate(resolve));
    await app.run('writes');
    app.run('enqueue(()=>new Promise(resolve=>window.releaseWrite=resolve))');
    await new Promise(resolve => setImmediate(resolve));
    await app.run("editReading('A','name')");
    assert.equal(app.window.document.getElementById('annotationDialog').hidden, false);
    app.batch.revision++;
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    assert.equal(app.window.document.getElementById('editName').value, 'Sample A');
    assert.equal(app.window.document.getElementById('editNotes').value, 'Notes belonging to A');
    app.run('window.releaseWrite()');
    await app.run('writes');
    assert.equal(app.window.document.getElementById('editNotes').value, 'Notes belonging to A');
    app.window.document.getElementById('editNotes').value = 'Edited A';
    app.window.document.getElementById('editNotes').dispatchEvent(new app.window.Event('input'));
    await app.run('writes');
    const edit = app.messages.findLast(message => message.params.action === 'edit_annotation_draft');
    assert.equal(edit.params.payload.observation_id, 'A');
    assert.equal(edit.params.payload.operation_id, 'operation-A');
    assert.equal(edit.params.payload.name, 'Sample A');
    assert.equal(edit.params.payload.notes, 'Edited A');
  } finally { app.close(); }
});

test('Cancel refusal is visible in its editor and keeps refused local text', async () => {
  const app = await workspace();
  try {
    await app.run("editReading('A','notes')");
    await app.run('writes');
    app.run("state.edits.A={name:'Draft A',notes:'Unsaved A'}; localEditValues.set('A',{value:state.edits.A}); render()");
    app.refuse('discard_annotation_draft');
    app.drafts.A = { draft_version: 1, dirty: true, name: 'Draft A', notes: 'Unsaved A' };
    await app.run("$('editCancel').onclick()");
    await app.run('writes');
    assert.match(app.window.document.getElementById('annotationDialog').textContent, /Synthetic action refusal/);
    assert.equal(app.run("localEditValues.get('A').value.notes"), 'Unsaved A');
  } finally { app.close(); }
});

test('Apply refusal stays visible in its dialog after editor refresh', async () => {
  const app = await workspace();
  try {
    await app.run("editReading('A','notes')");
    await app.run('writes');
    app.drafts.A = { draft_version: 1, dirty: true, name: 'Sample A', notes: 'Edited A' };
    app.run("state.edits.A={name:'Sample A',notes:'Edited A'}; render()");
    app.refuse('annotate');
    await app.run("$('editSave').onclick()");
    await app.run('writes');
    app.run('render()');
    assert.match(app.window.document.getElementById('annotationDialog').textContent, /Synthetic action refusal/);
  } finally { app.close(); }
});

test('ordinary saving shows progress rather than recovery and measurement controls survive analysis', async () => {
  const app = await workspace();
  try {
    app.batch.phase = 'saving'; app.batch.save_reason = 'Metadata publication pending';
    app.batch.can_measure = false; app.batch.revision++;
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    assert.equal(app.window.document.getElementById('recovery').hidden, true);
    assert.equal(app.window.document.getElementById('start').disabled, true);
    assert.match(app.window.document.getElementById('operationStatus').textContent, /Saving/);
    app.run("state.tool='color'; render()");
    assert.equal(app.window.document.getElementById('measureTools').hidden, false);
    assert.equal(app.window.document.getElementById('readingPanel').hidden, false);
  } finally { app.close(); }
});

test('Repeat names immutable intent and sends both source identities without replacing next draft', async () => {
  const app = await workspace();
  try {
    assert.equal(app.window.document.getElementById('repeat').getAttribute('aria-label'), 'Repeat Original B');
    await app.run("$('repeat').onclick()");
    await app.run('writes');
    const repeat = app.messages.find(message => message.params.action === 'repeat_sample');
    assert.deepEqual(repeat.params.payload, { context_id: 'context', draft_id: 'draft', draft_version: 0, operation_id: 'operation-B', observation_id: 'B' });
    assert.equal(app.batch.draft.name, 'Sample #01');
  } finally { app.close(); }
});

test('table tags take repeat lineage and chart cells from each dataset row, not the live batch', async () => {
  const app = await workspace();
  try {
    // A saved or imported source has no live operations to borrow lineage from.
    app.batch.operations = [];
    app.readings.B.repeat_of = { operation_id: 'operation-A', observation_id: 'A' };
    app.readings.A.chart_cell = { coordinate: 'A1', patch_name: 'Dark skin' };
    app.readings.B.chart_cell = { coordinate: 'B1', patch_name: null };
    app.batch.revision++; await app.run('refresh(true)');
    const row = name => [...app.window.document.querySelectorAll('#rows > tr')].find(tr => new RegExp(name).test(tr.textContent));
    const rowB = () => row('Sample B');
    assert.match(rowB().textContent, /Repeat of #1/);
    assert.match(row('Sample A').textContent, /Patch A1 · Dark skin/);
    assert.match(rowB().textContent, /Patch B1/);assert.doesNotMatch(rowB().textContent, /Patch B1 ·/, 'No invented name for an unnamed patch');
    app.readings.B.repeat_of = null;
    app.batch.revision++; await app.run('refresh(true)');
    assert.doesNotMatch(rowB().textContent, /Repeat/);
  } finally { app.close(); }
});

test('typing publishes one final draft and keeps local text through owner refreshes', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').focus(); $('sampleName').value='W'; $('sampleName').oninput(); $('sampleName').value='White'; $('sampleName').oninput(); $('sampleNotes').focus(); render()");
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 0);
    assert.equal(app.window.document.getElementById('sampleName').value, 'White');
    assert.equal(app.window.document.getElementById('sampleHint').hidden, true);
    assert.equal(app.window.document.getElementById('start').disabled, true);
    await new Promise(resolve => setTimeout(resolve, 450));
    await app.run('writes');
    const edits = app.messages.filter(message => message.params.action === 'edit_draft');
    assert.equal(edits.length, 1);
    assert.equal(edits[0].params.payload.name, 'White');
    assert.equal(app.batch.draft.name, 'White 01');
  } finally { app.close(); }
});

test('a temporarily blank exact name stays local without a save failure banner', async () => {
  const app = await workspace();
  try {
    app.run("$('numberSamples').checked=false; $('sampleName').value=''; $('sampleName').oninput()");
    await new Promise(resolve => setTimeout(resolve, 450));
    await app.run('writes');
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 0);
    assert.equal(app.window.document.getElementById('recovery').hidden, true);
    assert.equal(app.window.document.getElementById('localEditStatus').hidden, true);
    assert.match(app.window.document.getElementById('sampleHint').textContent, /Enter a sample name/);
  } finally { app.close(); }
});

test('an empty numbered name uses the owner default instead of refusing the sample', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value=''; $('sampleName').oninput()");
    await app.run('enqueue(()=>saveSampleDraft())');
    await app.run('writes');
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure Sample #01');
    assert.equal(app.window.document.getElementById('localEditStatus').hidden, true);
    assert.equal(app.window.document.getElementById('recovery').hidden, true);
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 0);
  } finally { app.close(); }
});

test('the full owner-resolved target is visible before Measure and its draft fence is sent', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value='White'; $('sampleName').oninput()");
    await app.run("previewSampleDraft(localEditValues.get('sample'))");
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure White 01');
    assert.equal(app.messages.some(message => message.params.action === 'edit_draft'), false);
    await app.run("$('start').onclick()");
    const edit = app.messages.find(message => message.params.action === 'edit_draft');
    assert.equal(edit.params.payload.draft_id, 'draft');
    assert.equal(edit.params.payload.name, 'White');
    assert.equal(edit.params.payload.number_samples, true);
    assert.equal(app.batch.draft.name, 'White 01');
  } finally { app.close(); }
});

test('exact mode keeps a numeric-looking label unchanged', async () => {
  const app = await workspace();
  try {
    app.run("$('numberSamples').checked=false; $('sampleName').value='D65'; $('numberSamples').onchange()");
    await app.run('enqueue(()=>saveSampleDraft())');
    await app.run('writes');
    assert.equal(app.batch.draft.name, 'D65');
    assert.equal(app.batch.draft.number_samples, false);
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure D65');
  } finally { app.close(); }
});

test('a delayed old preview cannot relabel newer input', async () => {
  const app = await workspace();
  try {
    app.holdPreviews();
    app.run("$('sampleName').value='White'; $('sampleName').oninput()");
    const old = app.run("previewSampleDraft(localEditValues.get('sample'))");
    await new Promise(resolve => setImmediate(resolve));
    app.previewNames.set('Yellow', 'Yellow 01');
    app.run("$('sampleName').value='Yellow'; $('sampleName').oninput()");
    const current = app.run("previewSampleDraft(localEditValues.get('sample'))");
    await new Promise(resolve => setImmediate(resolve));
    app.heldPreviews[1].reply(); await current;
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure Yellow 01');
    app.heldPreviews[0].reply(); await old;
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure Yellow 01');
  } finally { app.close(); }
});

test('a preview echo mismatch blocks Measure without publishing draft text', async () => {
  const app = await workspace();
  try {
    app.holdPreviews();
    app.run("$('sampleName').value='White'; $('sampleName').oninput()");
    const preview = app.run("previewSampleDraft(localEditValues.get('sample'))");
    await new Promise(resolve => setImmediate(resolve));
    app.heldPreviews[0].reply({ number_samples: false });
    await assert.rejects(preview, /Sample name preview changed/);
    assert.equal(app.window.document.getElementById('start').disabled, true);
    assert.equal(app.messages.some(message => message.params.action === 'edit_draft'), false);
  } finally { app.close(); }
});

test('skipping an obsolete queued edit does not cancel publication of newer text', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value='Old'; $('sampleName').oninput(); window.oldSampleToken=localEditValues.get('sample'); $('sampleName').value='Newer'; $('sampleName').oninput(); enqueue(()=>saveSampleDraft(window.oldSampleToken))");
    await app.run('writes');
    await new Promise(resolve => setTimeout(resolve, 450));
    await app.run('writes');
    assert.equal(app.batch.draft.name, 'Newer 01');
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 1);
  } finally { app.close(); }
});

test('Measure flushes pending text before freezing the owner draft', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value='White 03'; $('sampleName').oninput(); $('sampleNotes').value='Before measure'; $('sampleNotes').oninput()");
    await app.run("$('start').onclick()");
    await app.run('writes');
    const commands = app.messages.filter(message => ['edit_draft', 'measure'].includes(message.params.action));
    assert.deepEqual(commands.map(message => message.params.action), ['edit_draft', 'measure']);
    assert.equal(commands[0].params.payload.notes, 'Before measure');
    assert.equal(commands[1].params.payload.draft_id, 'draft');
    assert.equal(commands[1].params.payload.draft_version, 1);
  } finally { app.close(); }
});

test('a pending edit cannot cross a draft identity replacement with the same version', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value='Old target text'; $('sampleName').oninput()");
    app.batch.draft = { ...app.batch.draft, id: 'replacement', name: 'White 04' };
    app.batch.revision++;
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket); enqueue(()=>saveSampleDraft())');
    await app.run('writes');
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 0);
    assert.equal(app.window.document.getElementById('sampleName').value, 'Old target text');
    assert.equal(app.window.document.getElementById('localEditStatus').hidden, false);
    assert.equal(app.batch.draft.name, 'White 04');
  } finally { app.close(); }
});

test('IME composition does not publish an unfinished sample name', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').oncompositionstart(); $('sampleName').value='白'; $('sampleName').oninput()");
    await new Promise(resolve => setTimeout(resolve, 450));
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 0);
    app.run("$('sampleName').oncompositionend(); $('sampleName').onblur()");
    await app.run('writes');
    assert.equal(app.batch.draft.name, '白 01');
    assert.equal(app.messages.filter(message => message.params.action === 'edit_draft').length, 1);
  } finally { app.close(); }
});

test('Close flushes a pending sample name before asking the host to close', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value='Pending name'; $('sampleName').oninput()");
    await app.window.requestOwnerClose();
    const write = app.messages.findIndex(message => message.params.action === 'edit_draft');
    const close = app.messages.findIndex(message => message.method === 'request_close');
    assert.ok(write >= 0 && close > write);
    assert.equal(app.batch.draft.name, 'Pending name 01');
  } finally { app.close(); }
});

test('Close refuses an unfinished composition even before the first input event', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').oncompositionstart()");
    app.run("showPanel('setupDialog')");
    await app.window.requestOwnerClose();
    assert.equal(app.messages.some(message => message.method === 'request_close'), false);
    assert.equal(app.messages.some(message => message.method === 'cancel_close'), true);
    assert.equal(app.window.document.getElementById('toast').hidden,false);
    assert.match(app.window.document.getElementById('toast').textContent,/Finish entering/);
  } finally { app.close(); }
});

test('composition interrupted by blur does not leave acquisition permanently blocked', async () => {
  const app=await workspace();
  try {
    app.run("$('sampleName').oncompositionstart();$('sampleName').value='White';$('sampleName').oninput();$('sampleName').onblur()");
    await app.run('writes');
    assert.equal(app.run('sampleCompositionPending()'),false);
    assert.equal(app.batch.draft.name,'White 01');
    assert.equal(app.window.document.getElementById('start').disabled,false);
  } finally {app.close();}
});

test('a completed sample save updates the next target while Help stays available', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').value='Changed'; $('sampleName').oninput(); showPanel('helpDialog')");
    await app.run('enqueue(()=>saveSampleDraft())');
    await app.run('writes');
    assert.equal(app.batch.draft.name, 'Changed 01');
    assert.equal(app.window.document.getElementById('start').disabled,false);
    assert.equal(app.window.document.getElementById('start').textContent,'Measure Changed 01');
    assert.equal(app.window.document.getElementById('helpDialog').hidden,false);
  } finally { app.close(); }
});

test('an owner-prepared next target replaces a focused name after success', async () => {
  const app = await workspace();
  try {
    app.run("$('sampleName').focus()");
    app.batch.draft = { ...app.batch.draft, id: 'next', name: 'White 04', notes: '' };
    app.batch.revision++;
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    assert.equal(app.window.document.getElementById('sampleName').value, 'White 04');
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure White 04');
  } finally { app.close(); }
});

test('deferred background errors remain available without replacing current owner status on close', async () => {
  const app = await workspace();
  try {
    app.run("showPanel('setupDialog'); showStatusError(Error('Earlier background failure')); showStatusError(Error('Later background failure')); closePanel('setupDialog')");
    assert.match(app.window.document.getElementById('toast').textContent, /Earlier background failure/);
    assert.match(app.window.document.getElementById('toast').textContent, /Later background failure/);
    assert.equal(app.window.document.getElementById('operationStatus').textContent, 'Ready');
  } finally { app.close(); }
});

test('readings follow plots and the renderer creates no duplicate element identities', async () => {
  const app = await workspace();
  try {
    const document = app.window.document;
    const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
    assert.equal(new Set(ids).size, ids.length);
    assert.ok(document.getElementById('spectrumPanel').compareDocumentPosition(document.getElementById('readingPanel')) & app.window.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.equal(document.getElementById('showReadings'), null);
  } finally { app.close(); }
});

test('comparison requires an explicit reference and clearing never selects reading one', async () => {
  const app = await workspace();
  try {
    app.run("setTool('compare')");
    assert.equal(app.run('state.reference'), null);
    assert.equal(app.messages.some(message => message.method === 'analysis'), false);
    assert.match(app.window.document.getElementById('comparison').textContent, /set it as the reference/);
    await app.run("$('useReference').onclick()");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.run('state.reference'), 'B');
    const calls = app.messages.filter(message => message.method === 'analysis');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].params.comparison_version, 1);
    assert.equal(Object.hasOwn(calls[0].params, 'reference'), false);
    await app.run("$('clearReference').onclick(); render()");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.run('state.reference'), null);
    assert.equal(app.messages.filter(message => message.method === 'analysis').length, 1);
  } finally { app.close(); }
});

test('queued reference and white changes merge against the latest owner version', async () => {
  const app = await workspace();
  try {
    await app.run("changeComparison({reference:{operation_id:'operation-A',observation_id:'A'}}); changeComparison({white:{kind:'retained-reading',operation_id:'operation-B',observation_id:'B'}})");
    await app.run('writes');
    assert.equal(app.batch.comparison.version, 2);
    assert.deepEqual(app.batch.comparison.reference, { operation_id: 'operation-A', observation_id: 'A' });
    assert.deepEqual(copy(app.batch.comparison.white), { kind: 'retained-reading', operation_id: 'operation-B', observation_id: 'B' });
    const commands = app.messages.filter(message => message.method === 'set_analysis_context');
    assert.deepEqual(commands.map(message => message.params.comparison_version), [0, 1]);
  } finally { app.close(); }
});

test('stored comparison restores paired identities and assumed white Y on rebind', async () => {
  const app = await workspace();
  try {
    app.batch.comparison = {
      ...app.batch.comparison, version: 3, reference: { operation_id: 'operation-A', observation_id: 'A' },
      white: { kind: 'theoretical-lab-reference', name: 'D65', observer: 'CIE1931_2', reference_y: 250, unit: 'cd/m^2', scale: 'absolute; Y is luminance' },
    };
    app.batch.revision++;
    app.window.nextPacket = copy(app.packet);
    await app.run('resetViews(); synchronise(window.nextPacket)');
    assert.equal(app.run('state.reference'), 'A');
    assert.equal(app.window.document.getElementById('whiteChoice').value, 'standard:D65');
    assert.equal(app.window.document.getElementById('whiteY').value, '250');
    await app.run('persistView()');
    const view = app.messages.findLast(message => message.method === 'set_view').params.view;
    assert.equal(Object.hasOwn(view, 'reference'), false);
    assert.equal(Object.hasOwn(view, 'white'), false);
  } finally { app.close(); }
});

test('unsaved comparison settings suppress quantitative owner queries', async () => {
  const app = await workspace();
  try {
    app.batch.comparison = { ...app.batch.comparison, version: 1, saved: false, reference: { operation_id: 'operation-B', observation_id: 'B' } };
    app.batch.phase = 'saving';
    app.window.nextPacket = copy(app.packet);
    await app.run("synchronise(window.nextPacket); setTool('compare')");
    assert.equal(app.messages.some(message => ['analysis', 'projection'].includes(message.method)), false);
    assert.match(app.window.document.getElementById('comparisonSaveState').textContent, /Saving comparison settings/);
  } finally { app.close(); }
});

test('actual renderer connects mixed series, Repeat, retained white/reference and Finish to Python', { timeout: 25000 }, async () => {
  const peer = pythonOwnerPeer();
  let app;
  try {
    app = await workspace(message => peer.send(message));
    await app.run('setup(true,null,false,true)');
    app.run("$('batchName').value='Synthetic renderer workflow'; $('batchNotes').value='Software-only actor'; createBatch()");
    await app.run('writes');
    const settle = async count => {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        await app.run('refresh(true)');
        const state = app.run('ownerState');
        if (state.sample_count === count && state.phase === 'idle' && state.can_measure) return;
        if (state.save_reason || state.cleanup_reason) throw Error(state.save_reason || state.cleanup_reason);
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      throw Error('Synthetic reading did not settle');
    };
    const name = async value => {
      app.window.nextName = value;
      app.run("$('sampleName').value=window.nextName; $('sampleName').oninput()");
      await app.run("previewSampleDraft(localEditValues.get('sample'))");
    };
    await name('White');
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure White 01');
    await app.run("$('start').onclick()"); await settle(1);
    assert.equal(app.run('ownerState.draft.name'), 'White 02');
    assert.equal(app.run('ownerState.draft.notes'), '');
    await name('Yellow');
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure Yellow 01');
    await app.run("$('start').onclick()"); await settle(2);
    await name('White');
    assert.equal(app.window.document.getElementById('start').textContent, 'Measure White 02');
    assert.equal(app.window.document.getElementById('repeat').getAttribute('aria-label'), 'Repeat Yellow 01');
    await app.run("$('repeat').onclick()"); await settle(3);
    assert.equal(app.run('ownerState.draft.name'), 'White 02');
    assert.equal(app.run('state.rows.length'), 3);
    assert.equal(new Set(app.run('state.rows.map(row=>row.id)')).size, 3);
    await app.run("$('useReference').onclick()"); await app.run('waitMetadata()');
    for (let attempt=0;attempt<100&&!app.run('currentAnalysis()');attempt++) await new Promise(resolve=>setTimeout(resolve,20));
    const nativeBefore=JSON.stringify(app.run('currentAnalysis().xyz_comparison_by_source'));
    assert.equal(app.run('state.white'),null);
    assert.equal(app.window.document.getElementById('yChangeHeading').hidden,false);
    assert.equal(app.window.document.getElementById('uvDistanceHeading').hidden,false);
    assert.equal(app.window.document.getElementById('deltaHeading').hidden,true);
    assert.ok(Object.values(app.run('currentAnalysis().xyz_comparison_by_source')).some(item=>Number.isFinite(item.y_percent)&&Number.isFinite(item.uv_distance)));
    app.run("state.whiteChoice='reading:'+state.rows[0].id; render()");
    await app.run("$('declareWhite').onclick()"); await app.run('waitMetadata()');
    app.run("setTool('compare')");
    for (let attempt = 0; attempt < 100 && !app.run('currentAnalysis()'); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    const analysis = app.run('currentAnalysis()');
    assert.ok(analysis && !analysis.error, analysis?.error);
    assert.ok(Object.values(analysis.delta_e_by_source).some(value => Number.isFinite(value)));
    assert.equal(JSON.stringify(analysis.xyz_comparison_by_source),nativeBefore);
    app.run("state.plotted.delete(state.reference); setTool('measure')");
    assert.equal(app.window.document.getElementById('yChangeHeading').hidden,false);
    assert.equal(Object.keys(app.run('currentAnalysis().xyz_comparison_by_source')).length,3);
    assert.match(app.window.document.getElementById('deltaHeading').textContent,/white #1/);
    assert.equal(app.run('ownerState.comparison.saved'), true);
    assert.equal(app.run('ownerState.comparison.white.kind'), 'retained-reading');
    const scienceRevision=app.run('FIXTURES.dataset_revision'),rosterRevision=app.run('ownerRevision');
    const scienceResult=app.run('currentAnalysis()');
    await name('Green');
    await app.run('saveSampleDraft()');await app.run('waitMetadata()');
    await app.run('refresh(true)');
    assert.notEqual(app.run('ownerRevision'),rosterRevision);
    assert.equal(app.run('FIXTURES.dataset_revision'),scienceRevision);
    assert.deepEqual(app.run('currentAnalysis()'),scienceResult);
    app.run("inspect(state.rows[0].id)");await app.run('writes');
    assert.equal(app.window.document.getElementById('repeat').getAttribute('aria-label'),'Repeat reading #1');
    await app.run("$('repeat').onclick()");await settle(4);
    assert.equal(app.run('ownerState.draft.name'),'Green 01');
    assert.match(app.window.document.getElementById('rows').textContent,/Repeat of #1/);
    await app.run("$('finish').onclick()");
    assert.equal(app.run('ownerState.status'), 'finished');
    app.run('review()'); await app.run('writes');
    assert.match(app.window.document.getElementById('savedSummary').textContent, /Finished batch · 4 verified readings/);
  } finally {
    // Settle the peer first: a read still in flight (the inspector's exact
    // annotation) is rejected while its document exists, as a lost owner
    // would be in the host, instead of against a closed window.
    await peer.close(); app?.close();
  }
});

test('Measure shortcut works in analysis but ignores typing, composition, repeats and modal dialogs', async () => {
  const app = await workspace();
  try {
    const key = options => new app.window.KeyboardEvent('keydown', { bubbles: true, key: 'm', ctrlKey: true, shiftKey: true, ...options });
    app.run("setTool('color')");
    app.window.document.getElementById('sampleName').dispatchEvent(key());
    app.window.document.body.dispatchEvent(key({ isComposing: true }));
    app.window.document.body.dispatchEvent(key({ repeat: true }));
    app.run("showPanel('setupDialog')");
    app.window.document.body.dispatchEvent(key());
    app.run("closePanel('setupDialog')");
    assert.equal(app.messages.some(message => message.params.action === 'measure'), false);
    app.window.document.body.dispatchEvent(key());
    await app.run('writes');
    assert.equal(app.messages.filter(message => message.params.action === 'measure').length, 1);
    app.window.document.getElementById('previewGamut').dispatchEvent(key());
    await app.run('writes');
    app.window.document.getElementById('colorBackgrounds').dispatchEvent(key());
    await app.run('writes');
    assert.equal(app.messages.filter(message=>message.params.action==='measure').length,3);
  } finally { app.close(); }
});

test('blocked Measure shortcut gives a reason without submitting acquisition', async () => {
  const app = await workspace();
  try {
    app.batch.phase = 'saving'; app.batch.can_measure = false;
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    app.window.document.body.dispatchEvent(new app.window.KeyboardEvent('keydown', { bubbles: true, key: 'm', ctrlKey: true, shiftKey: true }));
    await app.run('writes');
    assert.equal(app.messages.some(message => message.params.action === 'measure'), false);
    assert.match(app.window.document.getElementById('toast').textContent, /saving/);
  } finally { app.close(); }
});

test('save failure exposes its destination and recovery without opening Batch', async () => {
  const app = await workspace();
  try {
    app.batch.save_reason = 'Synthetic publication failure';
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    const document = app.window.document;
    assert.equal(document.getElementById('recovery').hidden, false);
    assert.equal(document.getElementById('recovery').getAttribute('role'), 'alert');
    assert.equal(document.getElementById('retry').hidden, false);
    assert.match(document.getElementById('recoveryReason').textContent, /Synthetic publication failure/);
    assert.match(document.getElementById('recoveryReason').textContent,/synthetic\/batch/);
    assert.equal(document.getElementById('recoveryDetails').open,false,'Technical failure details stay available without filling the workspace');
    assert.equal(document.getElementById('recoveryDestination').textContent,'Folder: batch');
    assert.match(document.getElementById('recoverySummary').textContent,/Retry save or choose another folder/);
    assert.doesNotMatch(document.getElementById('operationStatus').textContent,/Synthetic publication failure|synthetic\/batch/,
      'The narrow measurement dock does not repeat the raw failure');
    assert.match(document.getElementById('destState').textContent, /synthetic\/batch/);
    // Cleanup and save can fail together: both owner reasons stay in the details.
    app.batch.cleanup_reason='Software stop is unconfirmed; retain operation ownership';app.batch.revision++;
    app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    assert.equal(document.getElementById('recoveryTitle').textContent,'Operation cleanup needs attention');
    assert.match(document.getElementById('recoveryReason').textContent,/Software stop is unconfirmed.*Synthetic publication failure/);
    assert.equal(document.getElementById('recoveryDetails').querySelector('summary').textContent,'Cleanup and save details');
    assert.match(document.getElementById('recoverySummary').textContent,/Saving also did not complete/);
    assert.equal(document.getElementById('retry').hidden,false,'The save failure keeps its retry');
    // Resolved recovery collapses its details, so the next failure opens closed.
    document.getElementById('recoveryDetails').open=true;
    app.batch.cleanup_reason=null;app.batch.save_reason=null;app.batch.revision++;
    app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    assert.equal(document.getElementById('recovery').hidden,true);
    assert.equal(document.getElementById('recoveryDetails').open,false);
  } finally { app.close(); }
});

test('finished batches keep reading review and offer New batch without obsolete acquisition fields',async()=>{
  const app=await workspace();
  try{
    app.batch.descriptor.options=[{id:'xyz_only',type:'boolean',default:false,label:'XYZ only',
      by_mode:{emissive_spot:{available:true}}}];
    // Host-shaped: a finished batch cannot edit or apply, and New batch is admitted.
    // instrument_frontend.py's offer shape; the host always offers mode_snapshot.
    app.batch.workflows=[{workflow:'spot',available:true,min_patches:1,max_patches:1,layout_fields:[],layout_keys:[],required_layout_keys:[]},{workflow:'mode_snapshot',available:true,min_patches:0,max_patches:0,layout_fields:[],layout_keys:[],required_layout_keys:[]}];
    app.batch.status='finished';app.batch.can_measure=false;app.batch.can_finish=false;app.batch.can_edit_annotations=false;app.batch.can_apply_annotations=false;app.batch.can_start_new_batch=true;app.batch.revision++;
    app.batch.operations=[{workflow:'calibrate',accepted:true,status:'completed',sample_count:0}];
    // The host keeps a configuration check's stale context after Finish.
    app.batch.context_current=false;app.batch.context_reason='Instrument context changed; Finish and create a new batch';
    await app.run('refresh(true)');
    const document=app.window.document;
    for(const id of ['sampleTargetFields','workflowControls','start','sampleActions','lastAttempt'])assert.equal(document.getElementById(id).hidden,true,id);
    assert.equal(document.getElementById('finishedBatchActions').hidden,false);
    assert.equal(document.getElementById('rows').children.length,2,'Finishing retains reviewable results');
    assert.equal(document.getElementById('latest').textContent.includes('Sample B'),true);
    assert.equal(document.getElementById('operationStatus').textContent,'Batch finished','A finished batch is not told to Finish');
    assert.equal(document.getElementById('newBatchAfterFinish').disabled,false);
    document.body.dispatchEvent(new app.window.KeyboardEvent('keydown',{bubbles:true,key:'m',ctrlKey:true,shiftKey:true}));await app.run('writes');
    assert.equal(document.getElementById('toast').textContent,'This batch is finished. Start a new batch to measure.');
    assert.equal(app.messages.some(message=>message.params?.action==='measure'),false);
    app.batch.save_reason='Synthetic Finish publication failure';app.batch.can_start_new_batch=false;app.batch.revision++;await app.run('refresh(true)');
    assert.equal(document.getElementById('newBatchAfterFinish').disabled,true,'New batch follows the owner rule the host enforces');
    assert.match(document.getElementById('finishedBatchMessage').textContent,/Resolve saving/);
    app.batch.save_reason=null;app.batch.can_start_new_batch=true;app.batch.revision++;await app.run('refresh(true)');
    assert.equal(document.getElementById('newBatchAfterFinish').disabled,false);
    await app.run('setup()');
    assert.equal(document.getElementById('setupTitle').textContent,'Batch settings');
    assert.match(document.getElementById('setupHint').textContent,/read only/);
    assert.doesNotMatch(document.getElementById('setupSourceHint').textContent,/later readings/);
    assert.equal(document.getElementById('applySetup').hidden,true);
    assert.equal(document.getElementById('cancelSetup').textContent,'Close');
    assert.equal(document.getElementById('device').disabled,true);
    assert.equal(document.getElementById('setup-option-xyz_only').disabled,true);
    app.run("closePanel('setupDialog')");
    await document.getElementById('newBatchAfterFinish').onclick();await app.run('writes');
    assert.equal(document.getElementById('setupDialog').hidden,false);
    assert.equal(document.getElementById('setupTitle').textContent,'New batch');
    assert.equal(document.getElementById('cancelSetup').textContent,'Cancel');
    assert.equal(document.getElementById('batchName').closest('label').hidden,false);
    app.run("closePanel('setupDialog')");
    app.batch.status='open';app.batch.can_measure=true;app.batch.can_edit_annotations=true;app.batch.can_apply_annotations=true;app.batch.can_start_new_batch=false;app.batch.context_current=true;app.batch.context_reason=null;app.batch.revision++;
    await app.run('refresh(true)');
    assert.equal(document.getElementById('finishedBatchActions').hidden,true);
    assert.equal(document.getElementById('workflowControls').hidden,false,'The offered tasks were hidden only by Finish');
    // Finish batch stays reachable while this non-modal panel is open.
    await app.run('setup()');
    assert.equal(document.getElementById('setupTitle').textContent,'Measurement settings');
    assert.equal(document.getElementById('applySetup').hidden,false);
    app.batch.status='finished';app.batch.can_measure=false;app.batch.can_finish=false;app.batch.can_edit_annotations=false;app.batch.can_apply_annotations=false;app.batch.revision++;
    await app.run('refresh(true)');
    assert.equal(document.getElementById('setupTitle').textContent,'Batch settings','The open panel follows Finish');
    assert.equal(document.getElementById('applySetup').hidden,true);
    assert.equal(document.getElementById('cancelSetup').textContent,'Close');
    assert.doesNotMatch(document.getElementById('setupSourceHint').textContent,/later readings/);
    app.run("closePanel('setupDialog')");
    app.batch.status='open';app.batch.can_measure=true;app.batch.can_edit_annotations=true;app.batch.can_apply_annotations=true;app.batch.revision++;
    await app.run('refresh(true)');
    assert.equal(document.getElementById('sampleTargetFields').hidden,false);
    assert.equal(document.getElementById('start').hidden,false);
  }finally{await app.run('writes');app.close();}
});

test('a new batch starts on Individual reading after a configuration check',async()=>{
  const app=await workspace();
  try{
    const document=app.window.document,choice=document.getElementById('workflowChoice');
    app.batch.workflows=[{workflow:'spot',available:true,min_patches:1,max_patches:1,layout_fields:[],layout_keys:[],required_layout_keys:[]},{workflow:'mode_snapshot',available:true,min_patches:0,max_patches:0,layout_fields:[],layout_keys:[],required_layout_keys:[]}];app.batch.revision++;
    await app.run('refresh(true)');
    app.run("$('workflowChoice').value='mode_snapshot';$('workflowChoice').onchange()");
    assert.equal(choice.value,'mode_snapshot');
    // createBatch's sequence: reset the views, then bind the new owner's snapshot.
    app.run('resetViews()');app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    assert.equal(choice.value,'spot','The new batch does not inherit the previous task');
  }finally{await app.run('writes');app.close();}
});

test('attempt status preserves cancelled retained count and exposes pending instructions beside Measure', async () => {
  const app = await workspace();
  try {
    app.batch.operations = [{ workflow: 'spot', accepted: true, saved: true, sample_count: 1, status: 'cancelled', reason: 'Synthetic late cancellation' }];
    app.batch.revision++;
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    assert.match(app.window.document.getElementById('lastAttempt').textContent, /Cancelled · 1 reading retained/);
    app.batch.phase = 'active'; app.batch.instructions = { request_id: 'instruction' };
    app.window.nextPacket = copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    const status = app.window.document.getElementById('operationStatus');
    assert.equal(status.textContent, 'Waiting for you…');
    assert.equal(status.closest('#measureTools')?.id, 'measureTools');
  } finally { app.close(); }
});

test('actual 3D view restores a named preset and labels manual orientation as custom', async () => {
  const { ColorSpaceView } = await import(spaceViewModuleURL);
  const THREE = await import(threeModuleURL);
  const dom = new JSDOM('<div></div>');
  const originalEvent = globalThis.CustomEvent;
  globalThis.CustomEvent = dom.window.CustomEvent;
  try {
    const view = Object.create(ColorSpaceView.prototype);
    Object.assign(view, {
      container: dom.window.document.querySelector('div'), scene: new THREE.Scene(),
      camera: new THREE.OrthographicCamera(), controls: { target: new THREE.Vector3(), update() {} },
      renderer: { getContext: () => ({ drawingBufferColorSpace: 'srgb' }) },
      tooltip: dom.window.document.createElement('div'), points: [], axisLabels: [], axisSegments: [], billboards: [],
      boundsKey: '', dataKey: '', span: 1, cameraPreset: 'oblique', contextLost: false,
    });
    const cameras = [];
    view.container.addEventListener('plotcamerachange', event => cameras.push(event.detail.preset));
    const input = { target: 'srgb', lab: false, rows: [], shells: [], whiteKey: 'null', surface: false, cameraPreset: 'front' };
    view.update(input);
    assert.equal(cameras.at(-1), 'front');
    assert.equal(view.cameraPreset, 'front');
    assert.ok(Math.abs(view.camera.position.x - view.controls.target.x) < 1e-10);
    view.nudge(.2, 0);
    assert.equal(cameras.at(-1), 'custom');
    const direction = view.camera.position.clone().sub(view.controls.target).normalize();
    view.update({ ...input, whiteKey: 'changed bounds' });
    assert.equal(view.cameraPreset, 'custom');
    assert.ok(direction.distanceTo(view.camera.position.clone().sub(view.controls.target).normalize()) < 1e-10);
    view.reset();
    assert.equal(cameras.at(-1), 'oblique');
    view.preset('side');
    view.camera.zoom=2;view.cameraChanged();
    assert.equal(view.cameraPreset, 'side', 'Zoom preserves the named direction');
    view.camera.position.add(new THREE.Vector3(0, 1, 1));view.cameraChanged();
    assert.equal(cameras.at(-1), 'custom', 'Orbit changes expose the actual custom direction');
    view.clear();
  } finally { globalThis.CustomEvent = originalEvent; dom.window.close(); }
});

test('reference wires use the supplied encoded vertex colors and distinct line styles', async () => {
  const {ColorSpaceView}=await import(spaceViewModuleURL);
  const THREE=await import(threeModuleURL);
  const view=Object.create(ColorSpaceView.prototype);
  Object.assign(view,{scene:new THREE.Scene(),span:1,actualTarget:'srgb'});
  const points=[new THREE.Vector3(0,0,0),new THREE.Vector3(1,1,1)];
  view.line(points,0x888888,true,[[1,0,0],[0,1,0]]);
  const line=view.scene.children[0];
  assert.equal(line.material.vertexColors,true);
  assert.equal(line.material.isLineDashedMaterial,true);
  assert.deepEqual([...line.geometry.getAttribute('color').array],[1,0,0,0,1,0]);
  assert.deepEqual([...line.geometry.getAttribute('position').array],[0,0,0,1,1,1]);
  assert.throws(()=>view.line(points,0x888888,false,[[1,0,0]]),/do not match/);
  line.geometry.dispose();line.material.dispose();
});

test('an editor whose batch changed refuses a queued action before sending a mutation', async () => {
  const app = await workspace();
  try {
    await app.run("editReading('A','notes')");
    await app.run('writes');
    app.run("ownerState.batch_id='replacement-batch'; render()");
    assert.equal(app.window.document.getElementById('editSave').disabled, true);
    const commands = app.messages.filter(message => message.method === 'command').length;
    await app.run("$('editSave').onclick()");
    await app.run('writes');
    assert.equal(app.messages.filter(message => message.method === 'command').length, commands);
  } finally { app.close(); }
});

test('view persistence deduplicates the last queued state without dropping a later return to it', async () => {
  const app = await workspace();
  try {
    app.run('persistView();persistView();persistView()');
    await app.run('writes');
    assert.equal(app.messages.filter(message => message.method === 'set_view').length, 1);
    app.run('state.follow=false;persistView();state.follow=true;persistView()');
    await app.run('writes');
    const views = app.messages.filter(message => message.method === 'set_view');
    assert.equal(views.length, 3);
    assert.equal(views.at(-2).params.view.follow, false);
    assert.equal(views.at(-1).params.view.follow, true);
  } finally { app.close(); }
});


test('saved-file review exposes failed authentication and keeps command identifiers in details', async () => {
  const app = await workspace();
  try {
    app.reviewResult.operations.push({ command_id: 'corrupt-command', request: { workflow: 'calibrate' }, review_state: 'unknown', review_reason: 'Referenced operation byte digest does not match' });
    app.run('review()');await app.run('writes');
    const dialog = app.window.document.getElementById('reviewDialog');
    assert.equal(dialog.hidden, false);
    assert.match(dialog.querySelector('[role=alert]').textContent, /byte digest does not match/);
    assert.match(dialog.textContent, /Measurement 1 · File verified/);
    assert.match(dialog.textContent, /Calibration 2 · File could not be verified/);
    assert.ok([...dialog.querySelectorAll('details')].every(detail => !detail.open));
    assert.match(app.window.document.getElementById('savedSummary').textContent, /^Finished batch/);
    app.reviewResult.manifest.status = 'abandoned';app.run('review()');await app.run('writes');
    assert.match(app.window.document.getElementById('savedSummary').textContent, /^Abandoned batch/, 'An abandoned batch is not reported as open');
    const ordinary = dialog.cloneNode(true);ordinary.querySelectorAll('details').forEach(detail => detail.remove());
    assert.doesNotMatch(ordinary.textContent, /saved-command|corrupt-command|review_state/);
  } finally { app.close(); }
});

test('160 spectra use table and curve selection without a per-reading legend', async () => {
  const app = await workspace();
  try {
    const template=copy(app.readings.A);
    for(let i=1;i<=158;i++) {
      const id='R'+i,row=copy(template);
      row.name='Sample #'+String(i+2).padStart(3,'0');row.observation.operation_id='operation-'+id;row.observation.observation_id=id;row.reading_ref={operation_id:row.observation.operation_id,observation_id:id};
      row.observation.backend_reported.spectrum={wavelengths_nm:Array.from({length:107},(_,j)=>380+j*4),values:Array.from({length:107},(_,j)=>1+(i%12)/12+Math.sin(j/10)/2),unit:'mW/(m^2 sr nm)',quantity:'spectral_radiance'};
      app.readings[id]=row;app.annotations[id]={version:0,name:row.name,notes:row.notes};
    }
    for(const id of ['A','B'])app.readings[id].observation.backend_reported.spectrum=copy(app.readings.R1.observation.backend_reported.spectrum);
    app.readings.R127.observation.backend_reported.spectrum.values=Array.from({length:107},(_,j)=>3.2+Math.sin(j/10)/2);
    app.readings.R127.uvy=[.25,.4,30];
    app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    const document=app.window.document;
    assert.equal(document.querySelectorAll('#rows tr').length,160);
    assert.equal(document.querySelectorAll('#spectrumPlot .curve').length,160);
    assert.equal(document.querySelector('.curve-legend'),null);
    assert.equal(document.getElementById('spectrumLegend'),null);
    await app.run('writes');
    const commands=()=>app.messages.filter(message=>message.method==='command').length,baseline=commands();
    const svg=document.querySelector('#spectrumPlot svg'),point=document.querySelector('#curve-R127 .curve').getAttribute('points').split(' ')[50].split(',').map(Number);
    svg.getBoundingClientRect=()=>({left:10,top:20,width:600,height:320});
    // Coordinates beat SVG stacking order, even when the event targets another curve.
    document.getElementById('curve-B').dispatchEvent(new app.window.MouseEvent('pointermove',{bubbles:true,clientX:10+point[0],clientY:20+point[1]+3}));
    assert.equal(app.run('pointerPreview?.id'),'R127');
    assert.equal(app.run('state.selected.id'),'R158');assert.equal(app.run('state.follow'),true);
    assert.equal(document.querySelector('#row-R127').closest('tr').classList.contains('reading-preview'),true);
    assert.equal(document.querySelector('#point-R127').classList.contains('reading-preview'),true);
    assert.equal(document.querySelectorAll('#spectrumPlot .plot-preview').length,1);
    assert.equal(document.querySelector('.plot-preview [data-inspect]'),null);
    assert.match(document.getElementById('selectionState').textContent,/Preview #129/);
    assert.equal(commands(),baseline,'Hover never persists inspection or another mutation');
    const scroller=document.querySelector('#readingPanel .table-scroll');
    assert.equal(scroller.scrollTop,0,'Hover does not scroll the table');
    svg.dispatchEvent(new app.window.MouseEvent('pointerleave'));
    assert.equal(document.querySelector('.reading-preview'),null);assert.equal(document.querySelector('.plot-preview'),null);
    assert.match(document.getElementById('selectionState').textContent,/Following #160/);
    const tr=document.getElementById('row-R127').closest('tr');
    tr.dispatchEvent(new app.window.MouseEvent('pointerenter'));
    assert.equal(document.querySelector('#curve-R127').classList.contains('reading-preview'),true);
    tr.dispatchEvent(new app.window.MouseEvent('pointerleave'));
    // Click reveals only the table row, without jumping or stealing page focus.
    scroller.getBoundingClientRect=()=>({top:400,bottom:600});
    scroller.querySelector('thead').getBoundingClientRect=()=>({height:30});
    const oldBounds=app.window.Element.prototype.getBoundingClientRect;
    app.window.Element.prototype.getBoundingClientRect=function(){return this.matches('tr[data-reading="R127"]')?{top:1000,bottom:1020}:oldBounds.call(this);};
    document.getElementById('curve-B').dispatchEvent(new app.window.MouseEvent('click',{bubbles:true,detail:1,clientX:10+point[0],clientY:20+point[1]+3}));
    assert.equal(scroller.scrollTop,420);
    app.window.Element.prototype.getBoundingClientRect=oldBounds;
    assert.equal(app.run('state.selected.id'),'R127');assert.equal(app.run('state.follow'),false);
    assert.equal(document.getElementById('row-R127').getAttribute('aria-pressed'),'true');
    await app.run('writes');assert.equal(commands(),baseline+1,'Click persists one inspection');
    document.getElementById('curve-R127').dispatchEvent(new app.window.MouseEvent('click',{bubbles:true}));
    assert.equal(app.run('state.selected.id'),'R127');assert.equal(app.run('state.plotted.size'),160);
    assert.equal(document.querySelector('#spectrumPlot .curve.selected').parentElement.id,'curve-R127');
    document.getElementById('plotInspected').click();
    assert.equal(document.querySelectorAll('#spectrumPlot .curve').length,1);
    document.getElementById('restorePlotted').click();assert.equal(document.querySelectorAll('#spectrumPlot .curve').length,160);
    const reading=document.getElementById('readingPanel'),plots=document.getElementById('plotFrame');
    assert.ok(plots.compareDocumentPosition(reading)&app.window.Node.DOCUMENT_POSITION_FOLLOWING);
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
  } finally { app.close(); }
});

test('plot keyboard preview and redraw preserve inspection and clear obsolete hover',async()=>{
  const app=await workspace();
  try{
    const document=app.window.document;
    app.readings.A.observation.backend_reported.spectrum={wavelengths_nm:[400,500,600],values:[1,2,1],unit:'mW/(m^2 sr nm)',quantity:'spectral_radiance'};
    app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    app.run("state.plotted.delete('B');render()");
    const curve=document.getElementById('curve-A');assert.equal(curve.tabIndex,0,'An inspected but unchecked reading does not trap plot keyboard access');
    curve.focus();
    assert.equal(app.run('state.selected.id'),'B');assert.match(document.getElementById('selectionState').textContent,/Preview #1/);
    curve.dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    assert.equal(app.run('state.selected.id'),'A');assert.equal(app.run('state.follow'),false);
    const point=document.getElementById('point-A');point.focus();
    // Add the other point back, then traverse the plot without committing selection.
    app.run("state.plotted.add('B');render()");
    document.getElementById('point-A').dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    assert.equal(document.activeElement.id,'point-B');assert.equal(app.run('state.selected.id'),'A');
    assert.equal(document.getElementById('row-B').closest('tr').classList.contains('reading-preview'),true);
    app.run('render()');
    assert.equal(document.activeElement.id,'point-B');
    assert.equal(document.getElementById('point-B').tabIndex,0);assert.equal(document.getElementById('point-A').tabIndex,-1,'Redraw preserves the focused plot tab stop');
    document.getElementById('point-B').dispatchEvent(new app.window.KeyboardEvent('keydown',{key:' ',bubbles:true}));
    assert.equal(app.run('state.selected.id'),'B');
    document.activeElement.blur();
    const row=document.getElementById('row-A').closest('tr');row.dispatchEvent(new app.window.MouseEvent('pointerenter'));
    app.run('render()');
    assert.equal(document.querySelector('.reading-preview'),null);assert.equal(document.querySelector('.plot-preview'),null);
    row.dispatchEvent(new app.window.MouseEvent('pointerleave'));
    const binding=copy(app.packet.dataset_binding);binding.kind='saved';binding.id='saved-binding';binding.read_only=true;
    app.packet.dataset_binding=binding;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    await app.run('writes');const baseline=app.messages.filter(message=>message.method==='command').length;
    document.getElementById('point-A').dispatchEvent(new app.window.MouseEvent('click',{bubbles:true}));
    await app.run('writes');assert.equal(app.run('state.selected.id'),'A');
    assert.equal(app.messages.filter(message=>message.method==='command').length,baseline,'Saved inspection stays local');
    const touch=new app.window.MouseEvent('pointerenter');Object.defineProperty(touch,'pointerType',{value:'touch'});
    document.getElementById('row-B').closest('tr').dispatchEvent(touch);
    assert.equal(document.querySelector('.reading-preview'),null,'Touch does not leave a hover behind');
  }finally{await app.run('writes');app.close();}
});

test('chromaticity picking ignores clipped markers and uses screen pixels after resizing',async()=>{
  const app=await workspace();
  try{
    const document=app.window.document,svg=document.querySelector('#chromaticityPlot svg'),clip=svg.querySelector('clipPath rect'),marker=document.querySelector('#point-A .marker');
    const right=Number(clip.getAttribute('x'))+Number(clip.getAttribute('width')),top=Number(clip.getAttribute('y'));
    svg.getBoundingClientRect=()=>({left:0,top:0,width:390,height:345});
    marker.setAttribute('cx',right+10);marker.setAttribute('cy',top+20);
    svg.dispatchEvent(new app.window.MouseEvent('pointermove',{bubbles:true,clientX:right+10,clientY:top+20}));
    assert.equal(app.run('pointerPreview'),null,'An invisible clipped marker cannot preview');
    svg.dispatchEvent(new app.window.MouseEvent('click',{bubbles:true,detail:1,clientX:right+10,clientY:top+20}));
    assert.equal(app.run('state.selected.id'),'B','An invisible clipped marker cannot select');
    marker.setAttribute('cx',right-1);marker.setAttribute('cy',top+20);
    svg.dispatchEvent(new app.window.MouseEvent('pointermove',{bubbles:true,clientX:right+1,clientY:top+20}));
    assert.equal(app.run('pointerPreview'),null,'A pointer outside the clip cannot pick a nearby point');
    marker.setAttribute('cx',70);marker.setAttribute('cy',70);
    svg.getBoundingClientRect=()=>({left:0,top:0,width:780,height:690});
    svg.dispatchEvent(new app.window.MouseEvent('pointermove',{bubbles:true,clientX:147,clientY:140}));
    assert.equal(app.run('pointerPreview?.id'),'A','Seven screen pixels stays within the pick radius at twice the size');
    svg.dispatchEvent(new app.window.MouseEvent('pointermove',{bubbles:true,clientX:149,clientY:140}));
    assert.equal(app.run('pointerPreview'),null,'Nine screen pixels stays outside the pick radius at twice the size');
  }finally{await app.run('writes');app.close();}
});

test('fine chromaticity zoom retains equal axes and saved reference context remains visible in readings', async () => {
  const app = await workspace();
  try {
    app.batch.comparison={...app.batch.comparison,reference:{operation_id:'operation-A',observation_id:'A'}};
    app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    const document=app.window.document, context=document.getElementById('comparisonContext');
    assert.equal(context.hidden,false);assert.match(context.textContent,/Compare to #1 · Sample A/);
    for(let i=0;i<20;i++)document.getElementById('zoomIn').click();
    assert.equal(app.run('zoom'),256);assert.equal(document.getElementById('zoomIn').disabled,true);
    const ticks=[...document.querySelectorAll('#chromaticityPlot .gridline')];
    assert.ok(ticks.length>4);
    const svg=document.querySelector('#chromaticityPlot svg');assert.match(svg.getAttribute('aria-label'),/Equal axis scale/);
    document.getElementById('resetPlot').click();assert.equal(app.run('zoom'),1);
    assert.equal(document.getElementById('zoomIn').disabled,false);
    context.click();assert.equal(app.run('state.tool'),'compare');
    await app.run('writes');await new Promise(resolve => setImmediate(resolve));
  } finally { app.close(); }
});


test('workspace stays available before a batch and empty results explain filters separately', async () => {
  const app = await workspace();
  try {
    app.window.noBatch={...copy(app.packet),batch:null,dataset_binding:{id:null,generation:0,kind:'empty',read_only:true,available:true,roster_revision:'empty',source:null,comparison:null}};
    app.run('resetViews()');await app.run('synchronise(window.noBatch)');
    const document=app.window.document;
    assert.equal(document.getElementById('workspaceContent').hidden,false);
    assert.equal(document.getElementById('welcome'),null);
    assert.equal(document.getElementById('readingPanel').hidden,false);
    assert.equal(document.getElementById('startBatch').hidden,false);
    assert.equal(document.getElementById('sampleName').value,'Sample #01');
    assert.match(document.getElementById('sampleNameLabel').textContent,/Name or series/);
    assert.ok(document.querySelector('#chromaticityPlot [data-gamut]'));
    app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    assert.equal(document.getElementById('startBatch').hidden,true);
    document.getElementById('readingFilter').value='No such reading';
    document.getElementById('readingFilter').dispatchEvent(new app.window.Event('input'));
    assert.equal(document.getElementById('noRows').hidden,false);
    assert.match(document.getElementById('noRows').textContent,/No readings match these filters/);
    assert.equal(app.run('state.rows.length'),2);
    await app.run('writes');await new Promise(resolve => setImmediate(resolve));
  } finally { app.close(); }
});


test('nonmodal panels keep inspection live, editor target pinned, and closure preserves selection', async () => {
  const app=await workspace();
  try {
    const document=app.window.document;
    app.run("state.inspector=true; inspect('B'); showPanel('helpDialog')");
    assert.equal(document.querySelector('dialog'),null);
    assert.equal(document.querySelector('[aria-modal]'),null);
    assert.equal(document.querySelector('[inert]'),null);
    app.run("inspect('A')");assert.equal(app.run('state.selected.id'),'A');
    assert.equal(document.getElementById('helpDialog').hidden,false);
    app.run("closePanel('helpDialog')");await app.run("editReading('A','notes')");
    const editor=document.getElementById('editNotes');
    app.run("inspect('B')");
    assert.equal(document.getElementById('editNotes'),editor);
    assert.equal(editor.value,'Notes belonging to A');
    assert.equal(document.getElementById('detailTitle').textContent,'Reading #2');
    editor.value='A remains the edit target';editor.dispatchEvent(new app.window.Event('input'));
    await app.run('writes');
    const edit=app.messages.findLast(message=>message.params.action==='edit_annotation_draft');
    assert.equal(edit.params.payload.observation_id,'A');assert.equal(edit.params.payload.operation_id,'operation-A');
    document.dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(app.run('state.selected.id'),'B');assert.equal(app.run('state.inspector'),true);
    assert.equal(document.getElementById('annotationDialog').hidden,true);
    const ids=[...document.querySelectorAll('[id]')].map(element=>element.id);assert.equal(new Set(ids).size,ids.length);
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
  } finally {app.close();}
});


test('native comparison cells show independent unavailability and stay visible outside Compare', async () => {
  const app=await workspace();
  try {
    app.xyzComparisons.A={...app.xyzComparisons.A,y_percent:-100,uv_distance:null,uv_reason:'u prime v prime is undefined for black'};
    app.batch.comparison={...app.batch.comparison,reference:{operation_id:'operation-B',observation_id:'B'}};
    app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    await new Promise(resolve=>setImmediate(resolve));
    const document=app.window.document,row=document.getElementById('row-A').closest('tr');
    assert.match(row.textContent,/-100/);assert.match(row.textContent,/Unavailable/);
    assert.ok([...row.querySelectorAll('td')].some(cell=>/undefined for black/.test(cell.title)));
    assert.equal(document.getElementById('deltaHeading').hidden,true);
    app.run("state.plotted.delete('A'); setTool('data')");
    assert.equal(document.getElementById('yChangeHeading').hidden,false);
    assert.equal(app.run('analysisSources().length'),2);
    assert.match(document.getElementById('row-A').closest('tr').textContent,/-100/);
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
  } finally {app.close();}
});

test('signed comparison columns and copied summary keep owner values units and independent reasons',async()=>{
  const app=await workspace();
  try{
    app.batch.comparison={...app.batch.comparison,reference:{operation_id:'operation-A',observation_id:'A'},white:{kind:'theoretical-lab-reference',name:'D50',observer:'CIE1931_2',reference_y:100,unit:'cd/m^2',scale:'absolute; Y is luminance'}};
    app.colorComparisons.B={...app.colorComparisons.B,delta_xyz:[-9,2.25,-3],delta_xy:[-.015,.02],delta_uv:[.03,-.04],delta_lab:[-1,2.5,-3.8],delta_chroma:2,delta_hue:null,delta_lab_reason:null,chroma_reason:null,hue_reason:'Hue is undefined at zero chroma',delta_e2000_reason:'Pair illuminant differs'};
    const range=(min,unit='1')=>({min,max:min,count:1,unit});
    const summary={y_percent_range:range(-25,'%'),y_unavailable:0,uv_distance_range:range(.05),uv_unavailable:0,delta_range:null,color_unavailable:1,rms_range:null,spectral_unavailable:1};
    for(const metric of ['delta_xyz','delta_xy','delta_uv','delta_lab']){summary[metric+'_range']=app.colorComparisons.B[metric].map(value=>range(value,metric==='delta_xyz'?'cd/m^2':'1'));summary[metric+'_unavailable']=0;}
    summary.delta_chroma_range=range(2);summary.delta_chroma_unavailable=0;summary.delta_hue_range=null;summary.delta_hue_unavailable=1;
    app.packet.analysis_summary=summary;app.batch.revision++;await app.run('refresh(true)');await new Promise(resolve=>setImmediate(resolve));
    assert.ok(app.run('currentAnalysis()?.color_comparison_by_source?.B'),'The current signed comparison packet is available');
    const document=app.window.document;
    assert.equal(document.getElementById('column-deltaX').checked,false,'Signed columns are opt-in');
    app.run("$('column-deltaX').checked=true;$('column-deltaH').checked=true;render()");
    const row=document.getElementById('row-B').closest('tr');
    assert.equal(row.querySelector('[data-column="deltaX"]').textContent,'-9');assert.match(row.querySelector('[data-column="deltaX"]').title,/cd\/m²|cd\/m/);
    assert.equal(row.querySelector('[data-column="deltaH"]').textContent,'Unavailable');assert.match(row.querySelector('[data-column="deltaH"]').title,/zero chroma/);
    assert.match(row.querySelector('[data-column="delta"]').title,/Pair illuminant differs/);
    assert.match(document.getElementById('colorDifference').textContent,/Pair illuminant differs/);
    assert.match(document.getElementById('signedComparison').textContent,/cd\/m²|cd\/m/);
    assert.equal(document.getElementById('signedComparison').closest('details').open,false);
    await document.getElementById('copyComparison').onclick();
    const copied=app.messages.findLast(message=>message.method==='copy_summary').params.text;
    assert.match(copied,/Compared readings: 1/);assert.match(copied,/ΔX: -9 to -9/);assert.match(copied,/ΔH\*: unavailable \(1 reading\)/);
    assert.match(copied,/Hue is undefined at zero chroma/);assert.match(copied,/Pair illuminant differs/);
    assert.match(copied,/Spectral RMS: unavailable/);assert.match(copied,/Source revision:/);
    assert.equal(app.messages.some(message=>message.method==='command'&&message.params.action==='measure'),false);
    app.packet.dataset_binding={...copy(app.packet.dataset_binding),available:false,reason:'Source changed'};await app.run('refresh(true)');
    const count=app.messages.filter(message=>message.method==='copy_summary').length;
    await document.getElementById('copyComparison').onclick();assert.equal(app.messages.filter(message=>message.method==='copy_summary').length,count,'Unavailable sources cannot copy cached results');
  }finally{app.close();}
});

test('actual signed comparison rendering and full summary consume Python values without changing observations',{timeout:20000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));await app.run('setup(true,null,false,true)');app.run("$('batchName').value='Signed comparison proof';createBatch()");await app.run('writes');
    for(let count=1;count<=2;count++){
      await app.run("$('start').onclick()");
      for(let attempt=0;attempt<150;attempt++){await app.run('refresh(true)');if(app.run(`state.rows.length===${count}&&ownerState.phase==='idle'`))break;await new Promise(resolve=>setTimeout(resolve,20));}
      assert.equal(app.run('state.rows.length'),count);
    }
    const native=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    await app.run("sendComparison({reference:readingIdentity(state.rows[0]),white:{kind:'theoretical-lab-reference',name:'D50',observer:state.rows[0].obs.backend_reported.xyz.observer,reference_y:100,unit:state.rows[0].obs.backend_reported.xyz.unit,scale:state.rows[0].obs.backend_reported.xyz.scale}})");
    app.run("setTool('compare');$('column-deltaX').checked=true;render()");
    const result=await waitForComparisonAnalysis(app);assert.ok(result.color_comparison_by_source);
    assert.ok(app.run('comparisonSummaryText()').includes('ΔX: '+result.summary.delta_xyz_range[0].min));
    const row=app.run('state.rows[1].id'),cell=app.window.document.getElementById('row-'+row).closest('tr').querySelector('[data-column="deltaX"]');
    assert.equal(cell.textContent,app.run('signedValue(currentAnalysis().color_comparison_by_source[state.rows[1].source].delta_xyz[0])'));
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),native);
  }finally{app?.close();await peer.close();}
});


test('wide editor ignores an older Edit reply and stays pinned through stale selection', async () => {
  const app=await workspace();
  try {
    app.window.innerWidth=1400;
    const pending=[],owner=app.window.ownerTransport,dispatch=owner.postMessage;
    owner.postMessage=message=>message.method==='annotation'?pending.push(copy(message)):dispatch(message);
    const first=app.run("editReading('A','notes')"),second=app.run("editReading('B','notes')");
    const reply=message=>app.window.ownerReply({id:message.id,ok:true,value:{applied:copy(app.annotations[message.params.reading_ref.observation_id]),draft:null}});
    reply(pending[1]);await second;reply(pending[0]);await first;
    owner.postMessage=dispatch;
    assert.equal(app.run('editorTarget.readingId'),'B');
    app.batch.selected={observation_id:'A'};app.batch.revision++;app.window.nextPacket=copy(app.packet);
    await app.run('synchronise(window.nextPacket)');
    const document=app.window.document;
    assert.equal(document.getElementById('editNotes').value,'Notes belonging to B');
    assert.match(document.getElementById('editorHeading').textContent,/Editing #2/);
    document.getElementById('editNotes').value='Pinned B';document.getElementById('editNotes').dispatchEvent(new app.window.Event('input'));
    await app.run('writes');
    assert.equal(app.messages.findLast(m=>m.params.action==='edit_annotation_draft').params.payload.observation_id,'B');
  } finally {app.close();}
});

test('superseded queued review never opens its native chooser', async () => {
  const app=await workspace();
  try {
    app.run('enqueue(()=>new Promise(resolve=>window.releaseWrite=resolve))');await new Promise(resolve=>setImmediate(resolve));
    app.run("review(); showPanel('helpDialog'); window.releaseWrite()");await app.run('writes');
    assert.equal(app.messages.some(m=>m.method==='review'),false);
    assert.equal(app.window.document.getElementById('helpDialog').hidden,false);
  } finally {app.close();}
});

test('assumed white requires inspected XYZ and columns persist independent of analysis tools', async () => {
  const app=await workspace();
  try {
    const document=app.window.document;
    app.run("state.follow=false;state.selected=null;state.whiteChoice='standard:D65';render()");
    assert.equal(document.getElementById('declareWhite').disabled,true);
    assert.match(document.getElementById('standardWhiteContext').textContent,/Inspect a reading/);
    document.getElementById('column-x').click();document.getElementById('column-xy').click();document.getElementById('column-uv').click();
    app.run("setTool('color');setTool('measure')");
    assert.equal(document.getElementById('heading-x').hidden,false);
    assert.equal(document.getElementById('heading-xy').hidden,false);
    assert.equal(document.getElementById('heading-uv').hidden,true);
    await app.run('writes');
    const view=app.messages.findLast(m=>m.method==='set_view').params.view;
    assert.equal(view.controls['column-x'],true);assert.equal(view.controls['column-uv'],false);
    assert.match(document.getElementById('heading-y').textContent,/cd\/m²/);
    app.readings.A.observation.backend_reported.xyz=null;app.readings.A.xyy=null;app.readings.A.uvy=null;
    app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    const row=document.getElementById('row-A').closest('tr');
    assert.equal(row.querySelector('[data-column="y"]').textContent,'Unavailable');
    assert.equal(row.querySelector('[data-column="xy"]').textContent,'Unavailable');
    assert.match(row.querySelector('[data-column="x"]').title,/No native XYZ/);
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
  } finally {app.close();}
});


test('Cancel keeps a newer refused token and blocks a queued Finish', async () => {
  const app=await workspace();
  try {
    await app.run("editReading('A','notes')");await app.run('writes');
    app.drafts.A={draft_version:1,dirty:true,name:'Sample A',notes:'Old draft'};
    app.run("state.edits.A={name:'Sample A',notes:'Old draft'};localEditValues.set('A',{value:state.edits.A});render()");
    const owner=app.window.ownerTransport,dispatch=owner.postMessage,pending=[];
    owner.postMessage=m=>m.method==='command'&&m.params.action==='discard_annotation_draft'?pending.push(copy(m)):dispatch(m);
    const cancel=app.run("$('editCancel').onclick()");await new Promise(resolve=>setImmediate(resolve));
    assert.equal(pending.length,1);
    app.refuse('edit_annotation_draft');
    const document=app.window.document,notes=document.getElementById('editNotes');
    notes.value='Newer text that Python refuses';notes.dispatchEvent(new app.window.Event('input'));
    delete app.drafts.A;
    app.window.ownerReply({id:pending[0].id,ok:true,value:{admission:'admitted',state:'completed'}});
    await cancel;await app.run('writes');
    assert.equal(app.run("localEditValues.get('A').value.notes"),'Newer text that Python refuses');
    assert.equal(document.getElementById('editNotes').value,'Newer text that Python refuses');
    assert.equal(document.getElementById('finish').disabled,true);assert.equal(document.getElementById('start').disabled,true);
    await app.run("$('finish').onclick()");await app.run('writes');
    assert.equal(app.messages.some(m=>m.params.action==='finish'),false);
  } finally {app.close();}
});


test('empty workspace prioritizes setup without duplicated status or disabled sample fields',async()=>{
  const app=await workspace();
  try{
    assert.doesNotThrow(()=>app.run('datasetBinding=null;render()'),'Initial owner binding can still be pending when a click renders the workspace');
    app.packet.batch=null;await app.run('refresh(true)');
    const document=app.window.document;
    assert.equal(document.getElementById('batchTitle').textContent,'Color Measurement');
    for(const id of ['batchContext','batchBadge','saveState','measureTools','spectrumLabel','selectionKey'])assert.equal(document.getElementById(id).hidden,true,id);
    assert.equal(document.getElementById('emptyMeasurement').hidden,false);
    assert.ok(document.getElementById('startBatch').closest('#emptyMeasurement'));
    assert.equal(document.querySelector('.reading-heading-tools').hidden,true);
    assert.equal(document.getElementById('emptyReadingsMessage').textContent,'Readings will appear here.');
    assert.equal(document.getElementById('setup').hidden,false);assert.equal(document.getElementById('demoBar').hidden,false);
    document.getElementById('startBatch').click();await app.run('writes');
    assert.equal(app.run('activePanel'),'setupDialog');assert.equal(app.run('setupChartDemo'),false);
  }finally{await app.run('writes');app.close();}
});

test('responsive layout and per-plot choices preserve readings, checked membership and comparison context', async () => {
  const app=await workspace();
  try {
    const document=app.window.document;
    app.window.innerWidth=1280;app.run('render()');
    assert.equal(document.getElementById('spacePanel').hidden,false);
    assert.equal(document.getElementById('chromaticityPanel').hidden,false);
    app.batch.comparison.reference={operation_id:'operation-A',observation_id:'A'};
    app.batch.revision++;app.window.nextPacket=copy(app.packet);await app.run('synchronise(window.nextPacket)');
    app.window.innerWidth=760;app.run('render()');
    assert.equal(document.getElementById('plotLayout').value,'overview');
    assert.equal(document.querySelector('[data-plot-view]'),null,'Plot kinds have one control per card');
    assert.equal(document.getElementById('workspaceContent').firstElementChild.id,'plotFrame','No toolbar row separates the header from plots');
    assert.ok(document.getElementById('plotLayout').closest('.app-header'));
    assert.ok(document.querySelector('.dock-analysis').closest('#readingPanel'));
    assert.equal(document.getElementById('spacePanel').hidden,false);
    assert.equal(document.getElementById('chromaticityPanel').hidden,true);
    app.run("$('spacePlotChoice').value='chromaticity'; $('spacePlotChoice').dispatchEvent(new Event('change',{bubbles:true}))");
    assert.equal(document.getElementById('spectrumPanel').hidden,true);
    assert.equal(document.getElementById('chromaticityPanel').hidden,false);
    assert.equal(document.getElementById('readingPanel').hidden,false);
    assert.equal(document.getElementById('measureTools').hidden,false);
    assert.equal(app.run('state.reference'),'A');assert.deepEqual([...app.run('state.plotted')],['A','B']);
    const layout=document.getElementById('plotLayout');layout.value='table';layout.dispatchEvent(new app.window.Event('change'));assert.equal(document.getElementById('plotFrame').hidden,true);
    assert.equal(document.querySelectorAll('#rows tr').length,2);
    layout.value='overview';layout.dispatchEvent(new app.window.Event('change'));
    assert.equal(document.getElementById('chromaticityPanel').hidden,false,'Plots returns the chosen portrait view');
    app.run("$('chromaticityPlotChoice').value='space'; $('chromaticityPlotChoice').dispatchEvent(new Event('change',{bubbles:true}))");
    document.querySelector('[data-expand="spacePanel"]').click();
    assert.equal(document.getElementById('plotFrame').classList.contains('is-expanded'),true);
    assert.equal(document.getElementById('readingPanel').hidden,false);
    document.querySelector('[data-expand="spacePanel"]').click();
    assert.equal(document.getElementById('plotFrame').classList.contains('is-expanded'),false);
    assert.ok(document.getElementById('gamutLegend').closest('.plot-settings'));
    assert.equal(document.querySelector('#chromaticityPanel > .plot-footnote'),null);
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
    assert.equal(app.messages.findLast(m=>m.method==='set_view').params.view.controls.plotLayout,'overview');
    app.window.innerWidth=1280;app.run('render()');
    assert.deepEqual([...app.run('visiblePlotIds()')],['spacePanel','chromaticityPanel'],'A wider window restores both chosen slots');
    layout.value='single';layout.dispatchEvent(new app.window.Event('change'));
    assert.deepEqual([...app.run('visiblePlotIds()')],['spacePanel']);assert.equal(document.activeElement,layout);
    app.run("$('spacePlotChoice').value='chromaticity';$('spacePlotChoice').dispatchEvent(new Event('change'))");
    assert.deepEqual([...app.run('visiblePlotIds()')],['chromaticityPanel'],'Changing plot kind preserves an explicit one-plot choice');
    app.run("state.rows[0].obs.backend_reported.spectrum={wavelengths_nm:[400,500],values:[1,2],unit:'mW/(m^2 sr nm)',quantity:'spectral_radiance'};render();$('chromaticityPlotChoice').value='residual';$('chromaticityPlotChoice').dispatchEvent(new Event('change'))");
    assert.deepEqual([...app.run('visiblePlotIds()')],['residualPanel']);assert.equal(document.activeElement.id,'residualPlotChoice');
    assert.equal(app.run('state.plotSlots.includes("residual")'),false,'The existing paired-slot contract remains intact');
    assert.equal(app.run('state.reference'),'A');assert.deepEqual([...app.run('state.plotted')],['A','B']);
    await app.run('writes');
  } finally {app.close();}
});

test('resizing chromaticity refreshes raster geometry and keeps the empty state in frame', async () => {
  const app=await workspace();
  try {
    const document=app.window.document,frame=document.getElementById('chromaticityPlot');
    Object.defineProperties(frame,{clientWidth:{value:420,configurable:true},clientHeight:{value:240,configurable:true}});
    app.run("$('colorBackgrounds').checked=true; $('chromaticityPlot').innerHTML=chromaticityPlot()");
    const before=JSON.parse(frame.querySelector('.chromaticity-field').dataset.frame);
    Object.defineProperties(frame,{clientWidth:{value:600,configurable:true},clientHeight:{value:170,configurable:true}});
    const changed=app.run('chromaticityPlot()');
    const holder=document.createElement('div');holder.innerHTML=changed;
    const after=JSON.parse(holder.querySelector('.chromaticity-field').dataset.frame);
    assert.notDeepEqual(after,before);
    const axis=holder.querySelector('.axis').getAttribute('d').match(/M([\d.]+) ([\d.]+)V([\d.]+)H([\d.]+)/).slice(1).map(Number);
    assert.equal(after[0],axis[0]);assert.equal(after[1],axis[1]);
    assert.equal(after[2],axis[3]-axis[0]);assert.equal(after[3],axis[2]-axis[1]);
    app.run('state.plotted.clear()');holder.innerHTML=app.run('chromaticityPlot()');
    const message=[...holder.querySelectorAll('text')].find(node=>node.textContent==='No checked readings');
    assert.ok(Number(message.getAttribute('x'))>0&&Number(message.getAttribute('x'))<600);
    assert.ok(Number(message.getAttribute('y'))>0&&Number(message.getAttribute('y'))<170);
    app.run("state.plotted.add('A');FIXTURES.readings.A.uvy=null");holder.innerHTML=app.run('chromaticityPlot()');
    assert.match(holder.textContent,/Checked coordinates unavailable/);
  } finally {app.close();}
});

test('top-level app menus exclude each other and panel actions close them', async () => {
  const app=await workspace();
  try {
    const document=app.window.document,batch=document.querySelector('.batch-menu'),columns=document.querySelector('.columns-menu');
    batch.querySelector('summary').click();assert.equal(batch.open,true);
    columns.querySelector('summary').click();assert.equal(columns.open,true);assert.equal(batch.open,false);
    document.getElementById('setup').click();assert.equal(columns.open,false);
    assert.equal(document.getElementById('setupDialog').hidden,false);
    app.run("closePanel('setupDialog')");
    const analysis=document.querySelector('.dock-analysis');
    analysis.querySelector('summary').click();assert.equal(analysis.open,true);
    assert.equal(analysis.closest('#readingPanel')!==null,true);
    document.getElementById('compareTab').click();assert.equal(analysis.open,true);
    assert.equal(document.getElementById('measureTools').hidden,false);
    const appearance=document.querySelector('.workspace-view'),scale=document.querySelector('.scale-menu');
    appearance.querySelector('summary').click();scale.querySelector('summary').click();
    assert.equal(analysis.open,false);
    assert.equal(appearance.open,true);assert.equal(scale.open,true);
    scale.querySelector('summary').dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(scale.open,false);assert.equal(appearance.open,true);
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
  } finally {app.close();}
});

test('short spectral frames retain separated ordinate ticks and their wavelength axis', async () => {
  const app=await workspace();
  try {
    const document=app.window.document,frame=document.getElementById('spectrumPlot');
    app.run("state.rows[0].obs.backend_reported.spectrum={wavelengths_nm:[400,500,600,700],values:[0,3,4,1],quantity:'spectral_radiance',unit:'mW/(m² sr nm)'}");
    for(const height of [90,140,240]) {
      Object.defineProperties(frame,{clientWidth:{value:600,configurable:true},clientHeight:{value:height,configurable:true}});
      frame.innerHTML=app.run('spectralPlot(state.rows)');
      const ticks=[...frame.querySelectorAll('text[text-anchor="end"]')].map(node=>Number(node.getAttribute('y'))).sort((a,b)=>a-b);
      assert.ok(ticks.length>=2);
      for(let index=1;index<ticks.length;index++)assert.ok(ticks[index]-ticks[index-1]>=25);
      assert.match(frame.textContent,/Wavelength · nm/);
      assert.equal(frame.querySelector('svg').getAttribute('viewBox'),'0 0 600 '+height);
    }
  } finally {app.close();}
});


test('actual renderer source settings and Demo tools retain 40 readings with native quantity availability', {timeout:40000}, async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));
    await app.run("$('startDemo').onclick()");
    assert.ok(app.window.document.getElementById('device').options.length>30);
    assert.equal(app.run('setupDescriptor.device_id'),'demo:i1pro3');
    app.run("$('setup-option-xyz_only').checked=true; $('batchName').value='Dense XYZ-only Demo'; createBatch()");
    await app.run('writes');
    assert.equal(app.run('ownerState.draft.options.xyz_only'),true);
    const beforeLoad=app.messages.filter(message=>message.params.action==='calibrate').length;
    app.run('demoLoading=true;renderSampleControls()');
    assert.equal(app.window.document.getElementById('calibrate').disabled,true);
    await app.run("begin('Calibrate')");await app.run('writes');
    assert.equal(app.messages.filter(message=>message.params.action==='calibrate').length,beforeLoad);
    app.run('demoLoading=false;renderSampleControls()');
    await app.run("$('demoToggle').click(); $('demo40').onclick()");
    for(let attempt=0;attempt<100&&app.run('annotationLoads.size||analysisPending||projectionPending');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(app.run('state.rows.length'),40);
    assert.equal(new Set(app.run('state.rows.map(row=>row.id)')).size,40);
    assert.equal(app.window.document.getElementById('rows').children.length,40);
    assert.ok(app.run('state.rows.every(row=>row.obs.backend_reported.xyz!==null&&row.obs.backend_reported.spectrum===null)'));
    assert.match(app.window.document.getElementById('demoLoadStatus').textContent,/40 of 40 completed/);
    assert.match(app.window.document.getElementById('spectrumPlot').textContent,/No spectrum is available/);
    assert.equal(app.window.document.getElementById('stopDemoLoad').hidden,true);
    assert.equal(app.window.document.getElementById('start').disabled,false);
    const retained=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    app.run("spaceView={actualTarget:'display-p3',update(input){window.spaceInput=input}}; $('spaceKind').value='lab'; $('spaceKind').dispatchEvent(new Event('change',{bubbles:true}))");
    await app.run('writes');await app.run('waitMetadata()');
    for(let attempt=0;attempt<100&&!app.run('currentProjection()?.lab_by_source');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(app.run('ownerState.comparison.white.name'),'D50');
    assert.equal(app.run('ownerState.comparison.white.reference_y'),100);
    assert.equal(app.run('ownerState.comparison.white.unit'),'cd/m^2');
    assert.equal(app.run('ownerState.comparison.saved'),true);
    await app.run('persistView()');
    const snapshot=await app.run("call('snapshot')");
    assert.deepEqual(copy(snapshot.view.plotSlots),['space','chromaticity'],'Actual Python session retains the paired view');
    assert.equal(Object.keys(app.run('currentProjection().lab_by_source')).length,40);
    assert.equal(app.window.spaceInput.rows.length,40);
    assert.match(app.window.document.getElementById('spaceWireKey').textContent,/Assumed D50.*white Y 100 cd\/m²/);
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),retained);
    await app.run('setup()');
    assert.equal(app.window.document.getElementById('device').disabled,true);
    assert.equal(app.window.document.getElementById('mode').disabled,true);
    assert.equal(app.window.document.getElementById('setup-option-xyz_only').disabled,false);
    app.run("$('setup-option-xyz_only').checked=false");
    await app.run("$('applySetup').onclick()");await app.run('waitMetadata()');
    assert.equal(app.run('ownerState.draft.options.xyz_only'),false);
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),retained);
    await app.run("$('start').onclick()");
    for(let attempt=0;attempt<300;attempt++){
      await app.run('refresh(true)');
      if(app.run("state.rows.length===41&&ownerState.phase==='idle'&&ownerState.can_measure"))break;
      await new Promise(resolve=>setTimeout(resolve,20));
    }
    assert.ok(app.run('state.rows.at(-1).obs.backend_reported.spectrum'));
    assert.equal(JSON.stringify(app.run('state.rows.slice(0,40).map(row=>row.obs)')),retained);
    for(const stale of ['generation','settings']){
      await app.run('setup()');
      app.run("$('setup-option-xyz_only').checked=true");
      if(stale==='generation')await app.run("call('bind').then(packet=>synchronise(packet))");
      else await app.run("send('edit_draft',{draft_id:ownerState.draft.id,version:ownerState.draft.version,name:ownerState.draft.name,notes:'Newer text',number_samples:ownerState.draft.number_samples,options:{...ownerState.draft.options,xyz_only:true}}).then(()=>waitMetadata())");
      const edits=app.messages.filter(message=>message.params.action==='edit_draft').length;
      await app.run("$('applySetup').onclick()");
      assert.equal(app.messages.filter(message=>message.params.action==='edit_draft').length,edits);
      assert.match(app.window.document.getElementById('setupError').textContent,/changed/);
      assert.equal(app.run('ownerState.draft.options.xyz_only'),stale==='settings');
    }
    for(let attempt=0;attempt<100&&app.run('annotationLoads.size||analysisPending||projectionPending');attempt++)await new Promise(resolve=>setTimeout(resolve,20));


  }finally{app?.close();await peer.close();}
});


test('measurement settings keep batch fields out of editing and disclose unavailable options on request',async()=>{
  const app=await workspace();
  try{
    app.batch.descriptor.options=[{id:'averaging',type:'integer',label:'Readings averaged into one',available:false,
      by_mode:{emissive_spot:{available:false,unavailable_reason:'This instrument does not provide measurement averaging.'}}}];
    await app.run('refresh(true)');await app.run('setup()');
    const document=app.window.document;
    assert.equal(document.getElementById('setupTitle').textContent,'Measurement settings');
    for(const id of ['batchName','batchNotes','destination'])assert.equal(document.getElementById(id).closest('label').hidden,true);
    assert.equal(document.getElementById('setupBatchHint').hidden,true);
    assert.equal(document.getElementById('applySetup').hidden,false);
    assert.equal(document.getElementById('setupUnavailableDetails').hidden,false);
    assert.equal(document.getElementById('setupUnavailableDetails').open,false);
    assert.match(document.getElementById('setupUnavailable').textContent,/does not provide measurement averaging/);
    assert.equal(document.getElementById('setupPreparation').closest('details').contains(document.getElementById('setupUnavailable')),false,
      'Reading preparation does not expose unrelated unsupported settings');
    document.getElementById('setupUnavailableDetails').open=true;
    await app.run('setup(true,null,false,true)');await app.run('writes');
    for(const id of ['batchName','batchNotes','destination'])assert.equal(document.getElementById(id).closest('label').hidden,false);
    assert.equal(document.getElementById('setupBatchHint').hidden,false);
    assert.equal(document.getElementById('setupUnavailableDetails').open,false,'A new setup starts with optional reasons collapsed');
    await app.run("loadSetupSelection('',setupRequest,actionErrorContext('setupDialog'))");
    assert.equal(document.getElementById('setupUnavailableDetails').hidden,true,'An unselected source has no unavailable-settings list');
  }finally{await app.run('writes');app.close();}
});

test('next-reading settings save unsent text first and refuse a changed source descriptor', async()=>{
  const app=await workspace();
  try{
    app.run('setup()');
    app.run("$('sampleName').value='Unsent'; $('numberSamples').checked=false; captureSampleEdit(); clearTimeout(sampleEditTimer); clearTimeout(samplePreviewTimer)");
    await app.run("$('applySetup').onclick()");
    assert.equal(app.run("localEditValues.has('sample')"),false,'Apply saves pending sample text first');
    assert.equal(app.messages.findLast(message=>message.params.action==='edit_draft').params.payload.name,'Unsent');
    assert.equal(app.run('ownerState.draft.name'),'Unsent');
    assert.doesNotMatch(app.window.document.getElementById('setupError').textContent,/Finish saving sample text/);
    app.run("setup(); ownerState.descriptor={...ownerState.descriptor,capability_id:'changed'}");
    const messages=app.messages.filter(message=>message.method==='command').length;
    await app.run("$('applySetup').onclick()");
    assert.equal(app.messages.filter(message=>message.method==='command').length,messages);
    assert.match(app.window.document.getElementById('setupError').textContent,/next reading changed/);
  }finally{app.close();}
});

test('typed native settings use shared numeric fields and preserve explicit zero', async()=>{
  const app=await workspace();
  try{
    const members=[
      {key:'exposureMilliseconds',label:'Exposure',help:'Zero requests Adaptive exposure.',type:'integer',required:false,default:null,minimum:0,maximum:2147483647,exclusive_minimum:false,unit:'ms'},
      {key:'cycles',label:'Cycles',help:'The driver validates the complete target.',type:'integer',required:false,default:null,minimum:0,maximum:2147483647,exclusive_minimum:false,unit:''},
    ];
    app.batch.descriptor.options=[
      {id:'native_controls',type:'object',label:'Native settings',default:null,available:true,members,by_mode:{emissive_spot:{available:true}}},
      {id:'correction',type:'object',label:'Correction',default:null,available:true,by_mode:{emissive_spot:{available:true,kinds:['ccmx']}}},
    ];
    app.batch.draft.options={native_controls:{exposureMilliseconds:0,cycles:3}};
    await app.run('refresh(true)');app.run('setup()');
    const section=app.window.document.querySelector('[data-option="native_controls"]');
    assert.ok(section,'Structured settings are visible in ordinary setup');
    const advanced=section.closest('details');
    assert.equal(advanced.querySelector('summary').textContent,'Advanced instrument settings');
    assert.equal(advanced.classList.contains('full'),true,'The group spans the setup grid');
    assert.equal(advanced.open,false,'Expert changes stay collapsed until requested');
    assert.equal(section.querySelector('[data-option-fields]').classList.contains('fields'),true,'Members reuse the shared responsive grid');
    assert.match(section.textContent,/Leave blank to omit a change/);
    assert.match(section.textContent,/including 0, are explicit changes/);
    assert.match(section.textContent,/instrument manual and the current report/);
    assert.doesNotMatch(section.textContent,/keep its current value/);
    assert.equal(app.window.document.querySelector('[data-option="correction"]'),null,'Correction keeps its native-held picker');
    const exposure=section.querySelector('[data-workflow-field="exposureMilliseconds"]');
    const cycles=section.querySelector('[data-workflow-field="cycles"]');
    assert.equal(exposure.value,'0');assert.equal(cycles.value,'3');
    assert.deepEqual(JSON.parse(app.run('JSON.stringify(setupOptions())')),{native_controls:{exposureMilliseconds:0,cycles:3},correction:null});
    assert.ok(exposure.getAttribute('aria-labelledby'));assert.ok(exposure.getAttribute('aria-describedby'));
    cycles.value='1.5';assert.throws(()=>app.run('setupOptions()'),/whole number/);
    assert.equal(advanced.open,true,'Invalid hidden inputs reveal their settings group');
    assert.equal(app.window.document.activeElement,cycles,'The invalid setting receives focus after disclosure');
    exposure.value='';cycles.value='';
    assert.deepEqual(JSON.parse(app.run('JSON.stringify(setupOptions())')),{correction:null},'Empty fields do not invent native changes');
    exposure.value='0';cycles.value='2';
    await app.run("$('applySetup').onclick()");
    const edit=app.messages.findLast(message=>message.params.action==='edit_draft');
    assert.deepEqual(edit.params.payload.options,{native_controls:{exposureMilliseconds:0,cycles:2},correction:null});
    assert.equal(app.run("workflowLabels.native_controls"),'Read or apply instrument settings');
  }finally{app.close();}
});


test('calibration outcomes remain visible and name the completed failed or cancelled workflow',async()=>{
  const app=await workspace();
  try{
    const native=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    for(const [status,text] of [['completed','Calibration completed.'],['failed','Calibration failed'],['cancelled','Calibration cancelled']]){
      app.batch.operations=[{accepted:true,workflow:'calibrate',status,sample_count:0,reason:status==='failed'?'Controlled failure':null}];app.batch.revision++;
      await app.run('refresh(true)');
      const outcome=app.window.document.getElementById('lastAttempt');
      assert.equal(outcome.hidden,false);assert.ok(outcome.textContent.includes(text),outcome.textContent);
      assert.doesNotMatch(outcome.textContent,/Measurement failed/);
      assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),native);
    }
    app.batch.operations=[{accepted:true,workflow:'spot',status:'completed',sample_count:1,reason:null}];app.batch.revision++;
    await app.run('refresh(true)');assert.equal(app.window.document.getElementById('lastAttempt').hidden,false);
    app.batch.phase='saving';await app.run('refresh(true)');assert.equal(app.window.document.getElementById('lastAttempt').hidden,false);
    app.batch.phase='active';await app.run('refresh(true)');assert.equal(app.window.document.getElementById('lastAttempt').hidden,true);
  }finally{app.close();}
});

test('next-reading settings rebase saved sample text without overwriting changed settings or targets',async()=>{
  const app=await workspace();
  try{
    app.batch.descriptor.options=[{id:'xyz_only',type:'boolean',label:'XYZ only',default:false,by_mode:{emissive_spot:{available:true,unavailable_reason:null}}}];
    app.batch.draft.options={xyz_only:false};
    app.batch.revision++;await app.run('refresh(true)');
    app.run("$('sampleName').value='Saved sample';$('sampleNotes').value='Newer notes';$('numberSamples').checked=false;captureSampleEdit();clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer);setup()");
    const field=app.window.document.getElementById('setup-option-xyz_only');field.checked=true;
    const native=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    await app.run('saveSampleDraft()');assert.equal(field.checked,true);
    const draft=copy(app.run('ownerState.draft'));
    assert.ok(draft.version>app.run('setupBinding.draft.version'));
    await app.run("$('applySetup').onclick()");
    const applied=app.messages.findLast(message=>message.params.action==='edit_draft').params.payload;
    assert.equal(applied.version,draft.version);assert.equal(applied.name,draft.name);assert.equal(applied.notes,'Newer notes');assert.equal(applied.number_samples,false);
    assert.equal(app.run('ownerState.draft.options.xyz_only'),true);assert.equal(app.run('activePanel'),null);
    app.run("setup();$('numberSamples').checked=true;$('numberSamples').onchange()");
    await app.run("$('applySetup').onclick()");
    assert.equal(app.run('ownerState.draft.number_samples'),true,'Apply flushes a just-toggled naming choice');
    assert.equal(app.run('activePanel'),null,'No reopening is needed after a local sample edit');
    for(const change of ['settings','target']){
      app.run('setup()');app.window.document.getElementById('setup-option-xyz_only').checked=false;
      if(change==='settings')app.batch.draft.options={xyz_only:false};
      else app.batch.draft={...app.batch.draft,id:'successor-target',version:0};
      app.batch.draft.version++;app.batch.revision++;await app.run('refresh(true)');
      const requests=app.messages.filter(message=>message.params.action==='edit_draft').length;
      await app.run("$('applySetup').onclick()");
      assert.equal(app.messages.filter(message=>message.params.action==='edit_draft').length,requests);
      assert.equal(app.run('activePanel'),'setupDialog');
      assert.equal(app.window.document.getElementById('setup-option-xyz_only').checked,false);
      assert.match(app.window.document.getElementById('setupError').textContent,/changed/);
    }
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),native);
  }finally{await app.run('writes');app.close();}
});

test('queued Create validates and submits the captured settings despite later invalid input', async()=>{
  const app=await workspace();
  try{
    app.batch.descriptor.options=[{id:'averages',type:'integer',available:true,applies_to:null,default:0,minimum:0,maximum:1000,label:'Readings averaged',by_mode:{emissive_spot:{available:true,unavailable_reason:null,capability_source:'synthetic mode',choices:[]}}}];
    await app.run('setup(true,null,false,true)');
    app.run("$('setup-option-averages').value='2'; window.releaseIntent=null; enqueue(()=>new Promise(resolve=>window.releaseIntent=resolve)); createBatch()");
    await Promise.resolve();
    app.run("$('setup-option-averages').value='-1'; window.releaseIntent()");
    await app.run('writes');
    assert.equal(app.messages.findLast(message=>message.method==='create_batch').params.options.averages,2);
    assert.match(app.window.document.getElementById('setupError').textContent,/Batch name must contain text/);
  }finally{app.close();}
});


test('3D point labels retain six significant figures for small and large values', async()=>{
  const app=await workspace();
  try{
    app.run("spaceView={actualTarget:'display-p3',update(input){window.pointInput=input}}; state.rows[0].obs.backend_reported.xyz.values=[0.00012345678,-0.00043219876,123456.78]; $('spacePanel').hidden=false; renderSpace()");
    const point=app.window.pointInput.rows[0];
    assert.match(point.label,/X=0.000123457/);
    assert.match(point.label,/Y=-0.000432199/);
    assert.match(point.label,/Z=123457/);
    assert.deepEqual([...point.values],[0.00012345678,-0.00043219876,123456.78]);
  }finally{app.close();}
});

test('3D gradient is visible by default and reference choices are shared between plots', async()=>{
  const app=await workspace();
  try{
    app.run("spaceView={actualTarget:'display-p3',update(input){window.spaceInput=input}}; state.plotLayout='space'; render()");
    assert.equal(app.window.spaceInput.surface,true);
    assert.equal(app.window.document.getElementById('spaceAppearance').closest('.panel-heading')?.parentElement.id,'spacePanel');
    assert.equal(app.window.document.getElementById('spaceKind').closest('.panel-heading')?.parentElement.id,'spacePanel');
    assert.equal(app.window.document.getElementById('spaceStatus').hidden,true);
    assert.match(app.window.document.getElementById('spaceWireKey').textContent,/Display P3 gradient.*sRGB dashed.*100 cd\/m²/);
    const original=copy(app.window.spaceInput.rows);
    app.run("$('spaceAppearance').value='outline'; $('spaceAppearance').dispatchEvent(new Event('change',{bubbles:true}))");
    await app.run('writes');
    assert.equal(app.window.spaceInput.surface,false);
    assert.deepEqual(copy(app.window.spaceInput.rows),original);
    const view=app.messages.findLast(message=>message.method==='set_view').params.view;
    assert.equal(view.controls.spaceAppearance,'outline');
    assert.equal('show3DSurface' in view.controls,false);
    assert.equal(app.window.document.getElementById('referenceGamut').closest('.workspace-view')?.tagName,'DETAILS');
    app.run("$('spaceReferences').onclick()");
    assert.equal(app.window.document.activeElement.id,'referenceGamut');
    assert.equal(app.window.document.querySelector('.workspace-view').open,true);
    app.run("$('referenceGamut').value='none'; $('spaceAppearance').value='gradient'; renderSpace()");
    assert.equal(app.window.document.getElementById('spaceStatus').hidden,false);
    assert.match(app.window.document.getElementById('spaceStatus').textContent,/Color gradient unavailable.*Appearance/);
  }finally{app.close();}
});

test('CIELAB selection stays explicit without a white and uses only Python supplied coordinates', async()=>{
  const app=await workspace();
  try{
    app.run("spaceView={actualTarget:'display-p3',update(input){window.spaceInput=input}}; state.plotLayout='space'; $('spaceKind').value='lab'; render()");
    assert.equal(app.window.document.getElementById('spaceKind').value,'lab');
    assert.equal(app.window.spaceInput.lab,true);
    assert.equal(app.window.spaceInput.rows.length,0);
    assert.match(app.window.document.getElementById('spaceStatus').textContent,/Choose a white/);
    assert.equal(app.window.document.getElementById('spaceWhite').hidden,false);
    app.run("$('spaceWhite').onclick()");
    assert.equal(app.window.document.activeElement.id,'whiteChoice');
    app.run("state.white={kind:'retained-reading',operation_id:'operation-A',observation_id:'A'}; currentProjection=()=>({lab_by_source:{A:[50.1234,-2.75,18.125]},reference_gamuts:{'display-p3':{lab_points:null,component_reasons:{lab:'Incompatible reference conditions'}}},color_reasons:{B:'Different observer'}}); render()");
    assert.equal(app.window.spaceInput.rows.length,1);
    assert.deepEqual([...app.window.spaceInput.rows[0].values],[50.1234,-2.75,18.125]);
    assert.match(app.window.document.getElementById('spaceStatus').textContent,/Incompatible reference conditions/);
    app.run("state.white=null; render()");
    assert.equal(app.window.document.getElementById('spaceKind').value,'lab');
  }finally{app.close();}
});

test('3D coordinate and gradient choices restore on a fresh owner binding', async()=>{
  const app=await workspace();
  try{
    app.run("spaceView={actualTarget:'display-p3',update(input){window.spaceInput=input}}; state.plotLayout='space'; $('spaceKind').value='lab'; $('spaceAppearance').value='outline'; persistView()");
    await app.run('writes');
    app.packet.view=copy(app.messages.findLast(message=>message.method==='set_view').params.view);
    app.run("ownerRevision=''; lastSnapshotKey=''; $('spaceKind').value='xyz'; $('spaceAppearance').value='gradient'");
    await app.run('refresh(true)');
    assert.equal(app.window.document.getElementById('spaceKind').value,'lab');
    assert.equal(app.window.document.getElementById('spaceAppearance').value,'outline');
    assert.equal(app.window.spaceInput.lab,true);
    assert.equal(app.window.spaceInput.surface,false);
  }finally{app.close();}
});

test('3D primary gradient preserves supplied colors and depth-tested reading inspection', async()=>{
  const {ColorSpaceView}=await import(spaceViewModuleURL);
  const THREE=await import(threeModuleURL);
  const dom=new JSDOM('<div></div>'),originalEvent=globalThis.CustomEvent;
  globalThis.CustomEvent=dom.window.CustomEvent;
  const view=Object.create(ColorSpaceView.prototype);
  Object.assign(view,{
    container:dom.window.document.querySelector('div'),scene:new THREE.Scene(),camera:new THREE.OrthographicCamera(),
    controls:{target:new THREE.Vector3(),update(){}},renderer:{getContext:()=>({drawingBufferColorSpace:'srgb'})},
    tooltip:dom.window.document.createElement('div'),points:[],axisLabels:[],axisSegments:[],billboards:[],boundsKey:'',dataKey:'',span:1,cameraPreset:'oblique',contextLost:false,
  });
  try{
    const shell={points:[[0,0,0],[1,0,0],[0,1,0]],lines:[[0,1],[1,2],[2,0]],triangles:[[0,1,2]],colors:{srgb:[[.2,.3,.4],[1,0,0],[0,1,0]]}};
    const input={target:'srgb',lab:false,whiteKey:'null',surface:true,cameraPreset:'oblique',shells:[{...shell,primary:true},{...shell,primary:false}],
      rows:[{id:'reading',values:[.2,.3,.4],colors:{srgb:[.2,.3,.4]},selected:true,outside:false,number:1,label:'Reading'}]};
    view.update(input);
    const surfaces=view.scene.children.filter(object=>object.isMesh&&object.geometry.index?.count===3);
    assert.equal(surfaces.length,1,'Only the primary RGB reference supplies the gradient');
    const surface=surfaces[0];
    assert.equal(surface.material.opacity,.5);
    assert.equal(surface.material.depthWrite,false);
    assert.equal(surface.renderOrder,0);
    const color=view.color([.2,.3,.4]).toArray();
    [...surface.geometry.getAttribute('color').array].slice(0,3).forEach((value,index)=>assert.ok(Math.abs(value-color[index])<1e-7));
    assert.equal(view.points[0].material.depthTest,true);
    assert.equal(view.points[0].material.depthWrite,true);
    assert.equal(view.points[0].renderOrder,10);
    view.raycaster={setFromCamera(){},intersectObjects(objects){assert.deepEqual(objects,view.points);return[{object:objects[0]}];}};
    view.renderer.domElement={getBoundingClientRect:()=>({left:0,top:0,width:200,height:200})};
    assert.equal(view.pick({clientX:50,clientY:50}).userData.id,'reading');
    const dataKey=view.dataKey,point=view.points[0];
    view.setPreview('reading');
    assert.equal(point.scale.x,1.35);assert.equal(view.previewRing.visible,true);
    assert.equal(view.dataKey,dataKey,'Hover does not rebuild scientific geometry');
    view.setPreview(null);assert.equal(point.scale.x,1);assert.equal(view.previewRing.visible,false);
    const canvas=dom.window.document.createElement('canvas');canvas.getBoundingClientRect=()=>({left:0,top:0,width:200,height:200});view.renderer.domElement=canvas;
    const hovered=[],inspected=[];view.onPreview=id=>hovered.push(id);view.onInspect=id=>inspected.push(id);view.bindPointerInspection(canvas);
    const dispatch=(type,x,y,button=0,id=1)=>{const event=new dom.window.MouseEvent(type,{clientX:x,clientY:y,button});Object.defineProperty(event,'pointerId',{value:id});canvas.dispatchEvent(event);};
    dispatch('pointermove',50,50);assert.equal(hovered.at(-1),'reading');assert.equal(view.tooltip.hidden,false);
    dispatch('pointerleave',50,50);assert.equal(hovered.at(-1),null);assert.equal(view.tooltip.hidden,true);
    dispatch('pointerdown',50,50);dispatch('pointermove',70,50);dispatch('pointerup',70,50);
    assert.deepEqual(inspected,[],'Orbit does not inspect a reading');assert.equal(hovered.at(-1),null);
    dispatch('pointerdown',50,50);dispatch('pointermove',70,50);dispatch('pointermove',50,50);dispatch('pointerup',50,50);
    assert.deepEqual(inspected,[],'Returning to the drag origin is still an orbit');
    for(const button of [1,2]){dispatch('pointerdown',50,50,button);dispatch('pointerup',50,50,button);}
    assert.deepEqual(inspected,[],'Middle and right camera gestures do not inspect');
    dispatch('pointerdown',50,50,0,1);dispatch('pointerdown',50,50,0,2);dispatch('pointerup',50,50,0,2);dispatch('pointerup',50,50,0,1);
    assert.deepEqual(inspected,[],'A second pointer cancels click candidacy');
    dispatch('pointerdown',50,50);dispatch('pointercancel',50,50);dispatch('pointerup',50,50);assert.deepEqual(inspected,[]);
    dispatch('pointerdown',50,50);dispatch('pointerup',50,50);assert.deepEqual(inspected,['reading']);
    view.update({...input,surface:false});
    assert.equal(view.scene.children.some(object=>object.isMesh&&object.geometry.index?.count===3),false);
  }finally{view.clear();globalThis.CustomEvent=originalEvent;dom.window.close();}
});

test('tight 3D views keep inspection and axis names ahead of overlapping ticks', async()=>{
  const {ColorSpaceView}=await import(spaceViewModuleURL);
  const THREE=await import(threeModuleURL);
  const dom=new JSDOM('<svg xmlns="http://www.w3.org/2000/svg"></svg>'),originalDocument=globalThis.document;
  globalThis.document=dom.window.document;
  try{
    dom.window.SVGElement.prototype.getBBox=function(){return {x:Number(this.getAttribute('x')),y:Number(this.getAttribute('y'))-12,width:this.textContent.length*7,height:12};};
    const camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);
    camera.position.z=3;camera.updateMatrixWorld();
    const position=new THREE.Vector3(0,0,0),axis=new THREE.Vector3(.7,.5,0);
    const view=Object.create(ColorSpaceView.prototype);
    Object.assign(view,{container:{clientWidth:240,clientHeight:140},camera,billboards:[],axisSegments:[],renderer:{render(){}},
      labels:dom.window.document.querySelector('svg'),axisLabels:[
        {position:new THREE.Vector3(0,.3,0),text:'0',tick:true},{position,text:'L*'},
        {position:new THREE.Vector3(.7,.8,0),text:'50',tick:true},{position:axis,text:'a*'},
        {position,text:'#78',selected:true},
      ]});
    view.draw();
    assert.deepEqual([...view.labels.children].map(label=>label.textContent),['#78','a*']);
    assert.equal(view.labels.querySelector('.selected-label')?.textContent,'#78');
  }finally{globalThis.document=originalDocument;dom.window.close();}
});

test('XYZ-only plot positions can swap 3D and chromaticity and persist the paired layout', async()=>{
  const app=await workspace();
  try{
    app.window.innerWidth=1280;
    app.batch.descriptor.spectral=false;app.batch.descriptor.modes[0].expected_quantities={xyz_unit:'cd/m^2',spectrum_unit:'mW/(m^2 sr nm)',quantity:'spectral_radiance'};
    app.window.currentDescriptor=copy(app.batch.descriptor);
    app.run("window.retainedView={rows:state.rows,selected:state.selected,plotted:state.plotted};state.rows=[];state.selected=null;state.plotted=new Set();datasetBinding.source.descriptor=window.currentDescriptor;state.plotSlotsSet=false;render()");
    assert.deepEqual([...app.run('visiblePlotIds()')],['spacePanel','chromaticityPanel']);
    assert.equal(app.window.document.getElementById('spacePlotChoice').querySelector('[value="spectrum"]').disabled,true);
    app.run("state.plotSlots=['chart','chromaticity'];chartView=null;renderChart()");
    assert.deepEqual([...app.run('state.plotSlots')],['space','chromaticity'],'A missing chart cannot select an unavailable spectrum');
    await app.run('setup(true,null,false,true)');
    assert.match(app.window.document.getElementById('setupSourceHint').textContent,/XYZ cd\/m²/);assert.doesNotMatch(app.window.document.getElementById('setupSourceHint').textContent,/spectral/);
    app.run("closePanel('setupDialog');Object.assign(state,window.retainedView);state.plotSlotsSet=false");
    app.run("spaceView={actualTarget:'display-p3',update(input){window.spaceInput=input}}; render()");
    const document=app.window.document;
    assert.deepEqual([...app.run('visiblePlotIds()')],['spacePanel','chromaticityPanel']);
    assert.equal(document.getElementById('spectrumPlotChoice').querySelector('[value="spectrum"]').disabled,true);
    assert.equal(document.getElementById('spacePlotChoice').querySelector('[value="residual"]').disabled,true);
    assert.match(document.getElementById('plotFrame').style.gridTemplateColumns,/1fr.*1fr/);
    app.run("$('spacePlotChoice').value='chromaticity'; $('spacePlotChoice').dispatchEvent(new Event('change',{bubbles:true}))");
    assert.deepEqual([...app.run('visiblePlotIds()')],['chromaticityPanel','spacePanel']);
    assert.deepEqual([...document.getElementById('plotFrame').children].filter(panel=>!panel.hidden).map(panel=>panel.id),['chromaticityPanel','spacePanel']);
    assert.equal(document.activeElement.id,'chromaticityPlotChoice');
    assert.deepEqual([...app.run('state.plotted')],['A','B']);
    await app.run('writes');
    app.packet.view=copy(app.messages.findLast(message=>message.method==='set_view').params.view);
    app.run("ownerRevision='';lastSnapshotKey='';state.plotSlots=['spectrum','chromaticity'];state.plotSlotsSet=false");
    await app.run('refresh(true)');
    assert.deepEqual([...app.run('state.plotSlots')],['chromaticity','space']);
    app.window.innerWidth=650;app.run('render()');
    assert.deepEqual([...app.run('visiblePlotIds()')],['chromaticityPanel']);
    app.run("$('chromaticityPlotChoice').value='space'; $('chromaticityPlotChoice').dispatchEvent(new Event('change',{bubbles:true}))");
    assert.deepEqual([...app.run('visiblePlotIds()')],['spacePanel']);
    assert.equal(document.getElementById('readingPanel').hidden,false);
    app.batch.descriptor.spectral=true;app.batch.descriptor.options=[{id:'xyz_only',type:'boolean',label:'XYZ only',default:false,by_mode:{emissive_spot:{available:true,unavailable_reason:null}}}];
    await app.run('setup(true,null,false,true)');assert.match(document.getElementById('setupSourceHint').textContent,/spectral radiance/);
    const xyzOnly=document.getElementById('setup-option-xyz_only');xyzOnly.checked=true;xyzOnly.dispatchEvent(new app.window.Event('input',{bubbles:true}));
    assert.doesNotMatch(document.getElementById('setupSourceHint').textContent,/spectral/);assert.equal(xyzOnly.checked,true);
    xyzOnly.checked=false;xyzOnly.dispatchEvent(new app.window.Event('input',{bubbles:true}));assert.match(document.getElementById('setupSourceHint').textContent,/spectral radiance/);
    app.run("closePanel('setupDialog');window.heldOwner=ownerState;ownerState=null;datasetBinding.kind='saved';state.plotSlotsSet=false;render()");
    assert.deepEqual([...app.run('state.plotSlots')],['space','chromaticity'],'Saved XYZ-only data chooses available plots without a live batch');
    assert.deepEqual([...app.run('visiblePlotIds()')],['spacePanel'],'The narrow saved view shows its available first plot');
    app.run("ownerState=window.heldOwner;datasetBinding.kind='live';state.rows[0].obs.backend_reported.spectrum={wavelengths_nm:[400,500],values:[1,2],unit:'mW/(m^2 sr nm)',quantity:'spectral_radiance'};datasetBinding.source.descriptor.spectral=false");
    assert.equal(app.run('spectrumAvailable()'),true,'Retained spectra stay available independently of current descriptor forecasts');
    await app.run('writes');await new Promise(resolve=>setImmediate(resolve));
  }finally{app.close();}
});

test('first CIELAB choice saves assumed D50 once and retains a later chosen white', async()=>{
  const app=await workspace();
  try{
    app.run("spaceView={actualTarget:'display-p3',update(){}}; $('spaceKind').value='lab'; $('spaceKind').dispatchEvent(new Event('change',{bubbles:true}))");
    await app.run('writes');
    const whites=()=>app.messages.filter(message=>message.method==='set_analysis_context');
    assert.equal(whites().length,1);
    assert.deepEqual(whites()[0].params.white,{kind:'theoretical-lab-reference',name:'D50',observer:'CIE1931_2',reference_y:100,unit:'cd/m^2',scale:'absolute; Y is luminance'});
    app.run('render();render();render()');await app.run('writes');
    assert.equal(whites().length,1);
    app.batch.comparison.white={...app.batch.comparison.white,name:'D65',reference_y:80};app.batch.comparison.version++;app.batch.revision++;
    await app.run('refresh(true)');
    app.run("$('spaceKind').value='xyz'; $('spaceKind').dispatchEvent(new Event('change',{bubbles:true})); $('spaceKind').value='lab'; $('spaceKind').dispatchEvent(new Event('change',{bubbles:true}))");
    await app.run('writes');
    assert.equal(whites().length,1);
    assert.equal(app.run('state.white.name'),'D65');
    assert.match(app.window.document.getElementById('spaceWireKey').textContent,/Assumed D65.*white Y 80 cd\/m²/);
  }finally{app.close();}
});

test('automatic D50 never crosses a queued owner generation or invents an observer', async()=>{
  const app=await workspace();
  try{
    app.run("spaceView={actualTarget:'display-p3',update(){}}; enqueue(()=>new Promise(resolve=>window.releaseWrite=resolve))");
    await new Promise(resolve=>setImmediate(resolve));
    app.run("$('spaceKind').value='lab'; $('spaceKind').dispatchEvent(new Event('change',{bubbles:true})); datasetBinding.generation++; window.releaseWrite()");
    await app.run('writes');
    assert.equal(app.messages.some(message=>message.method==='set_analysis_context'),false);
    assert.match(app.window.document.getElementById('toast').textContent,/data changed before D50/);
    app.run("state.rows.forEach(row=>row.obs.backend_reported.xyz.observer='CIE1964_10'); $('spaceKind').value='lab'; $('spaceKind').dispatchEvent(new Event('change',{bubbles:true}))");
    await app.run('writes');
    assert.equal(app.messages.some(message=>message.method==='set_analysis_context'),false);
    assert.match(app.window.document.getElementById('spaceStatus').textContent,/supported observer/);
  }finally{app.close();}
});

test('3D camera fits retained geometry with one scale and preserves user zoom on resize', async()=>{
  const {ColorSpaceView}=await import(spaceViewModuleURL);
  const THREE=await import(threeModuleURL);
  const view=Object.create(ColorSpaceView.prototype);
  const points=[[-100,0,-100],[-100,0,100],[100,0,-100],[100,0,100],[0,100,0]].map(p=>new THREE.Vector3(...p));
  const camera=new THREE.OrthographicCamera();
  const center=new THREE.Vector3(0,50,0);
  camera.position.copy(center).add(new THREE.Vector3(450,330,480));camera.lookAt(center);
  Object.assign(view,{framePoints:points,center,span:300,camera,contextLost:false,draw(){},renderer:{setSize(){}}});
  for(const [width,height] of [[520,270],[240,210],[900,400]]) {
    view.container={clientWidth:width,clientHeight:height};
    view.resize();camera.updateMatrixWorld();
    const projected=points.map(p=>p.clone().project(camera));
    assert.ok(projected.every(p=>Math.abs(p.x)<1&&Math.abs(p.y)<1),'Retained bounds stay visible');
    for(const axis of ['x','y']) {
      const values=projected.map(p=>p[axis]);
      assert.ok(Math.abs(Math.min(...values)+Math.max(...values))<1e-9,'Returned geometry is centered in its frame');
    }
    assert.ok(Math.abs((camera.right-camera.left)/width-(camera.top-camera.bottom)/height)<1e-10,'Scientific units retain equal scale');
    if(width===520)assert.ok(camera.top<view.span*.56,'Default fills more space than the old diagonal fit');
  }
  camera.zoom=2;
  view.resize();
  assert.equal(camera.zoom,2,'Window resize retains an explicit user zoom');
  camera.zoom=1;view.span=100;view.cameraPreset='custom';
  view.center=new THREE.Vector3(0,0,50);
  view.framePoints=[new THREE.Vector3(0,0,0),new THREE.Vector3(0,0,100)];
  camera.position.set(0,0,350);camera.lookAt(view.center);view.resize();
  const oldWidth=camera.right-camera.left;
  camera.position.set(60,0,350);camera.lookAt(view.center);view.cameraChanged();camera.updateMatrixWorld();
  assert.ok(camera.right-camera.left>oldWidth,'Orbit refits newly exposed depth');
  assert.ok(view.framePoints.every(p=>Math.abs(p.clone().project(camera).x)<1),'Rotation retains endpoints inside the frame');
});


test('saved-only review permits ephemeral comparison and fences delayed data replies', async()=>{
  const app=await workspace();
  try{
    const source=copy(app.packet.dataset_binding);
    source.id='saved-source';source.kind='saved';source.read_only=true;source.review={excluded_operations:[]};
    source.comparison={...source.comparison,available:true,persisted:true};
    app.packet.batch=null;app.packet.dataset_binding=source;
    await app.run('refresh(true)');
    assert.equal(app.run('ownerState'),null);
    assert.equal(app.run('state.rows.length'),2);
    assert.equal(app.window.document.getElementById('start').disabled,true);
    assert.equal(app.window.document.querySelector('.control-dock').hidden,true);
    assert.equal(app.window.document.getElementById('datasetNotice').hidden,true);
    for(let attempt=0;attempt<100&&app.run('annotationLoads.size');attempt++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(app.window.document.getElementById('editName').value,'Sample B');
    assert.equal(app.window.document.getElementById('editNotes').value,'Notes belonging to B');
    app.run("inspect('A')");await app.run('writes');
    assert.equal(app.messages.some(message=>message.params.action==='select'),false);
    await app.run("changeComparison({reference:readingIdentity(state.selected)})");
    app.run("setTool('compare')");await new Promise(resolve=>setImmediate(resolve));
    assert.ok(app.run('currentAnalysis()?.xyz_comparison_by_source'));
    assert.equal(app.run('selectedComparison().saved'),false);
    assert.match(app.window.document.getElementById('comparisonSaveState').textContent,/source files stay unchanged/);
    for(const method of ['dataset','projection','analysis','annotation']){
      app.window.delayedMethod=method;
      app.run("window.originalCall=call; call=(method,params)=>method===window.delayedMethod?new Promise(resolve=>window.releaseData=resolve):window.originalCall(method,params); window.delayedData=dataCall(window.delayedMethod).then(()=>null,error=>error.message)");
      app.run('datasetBinding.generation++; window.releaseData({}); call=window.originalCall');
      assert.match(await app.run('window.delayedData'),/displayed data changed/);
    }
    source.available=false;source.reason='Saved source bytes changed';
    await app.run('refresh(true)');
    assert.equal(app.run('state.rows.length'),0);
    assert.match(app.window.document.getElementById('datasetMessage').textContent,/Saved source bytes changed/);
    assert.equal(app.window.document.getElementById('returnLive').hidden,false);
  }finally{app.close();}
});


test('actual saved review preserves live edits and retries exact prepared export bytes', {timeout:40000}, async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));
    await app.run('setup(true,null,false,true)');
    app.run("$('batchName').value='Saved source and export integration';createBatch()");await app.run('writes');
    for(let index=1;index<=4;index++){
      await app.run("$('start').onclick()");
      for(let attempt=0;attempt<100;attempt++){
        await app.run('refresh(true)');
        if(app.run('state.rows.length')===index&&app.run('ownerState.can_measure'))break;
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      assert.equal(app.run('state.rows.length'),index);
    }
    app.run("closePanel('setupDialog')");
    const liveRows=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    const first=app.run('state.rows[0].id');app.window.editSource=first;
    await app.run("editReading(window.editSource,'notes')");
    app.run("$('editNotes').value='Live draft survives saved review'; $('editNotes').dispatchEvent(new Event('input'))");
    await app.run('writes');
    const liveDraft=copy(app.run('state.edits[window.editSource]'));
    await app.run("switchDataset('open_saved')");
    assert.equal(app.run('datasetBinding.kind'),'saved');
    assert.equal(app.window.document.getElementById('start').disabled,true);
    assert.equal(app.window.document.getElementById('returnLive').hidden,false);
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),liveRows);
    assert.equal(app.run('Object.keys(state.edits).length'),0);
    app.run("inspect(state.rows[0].id)");await app.run('writes');
    await app.run("changeComparison({reference:readingIdentity(state.selected)})");
    assert.equal(app.run('selectedComparison().persisted'),false);
    app.run("setTool('compare')");
    for(let attempt=0;attempt<100&&!app.run('currentAnalysis()?.xyz_comparison_by_source');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
    assert.ok(app.run('currentAnalysis()?.xyz_comparison_by_source'));
    await app.run("switchDataset('activate_live')");
    assert.equal(app.run('datasetBinding.kind'),'live');
    assert.deepEqual(copy(app.run('state.edits[window.editSource]')),liveDraft);
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),liveRows);
    assert.equal(app.run('selectedComparison().reference'),null,'Saved review reference does not replace the live reference');
    await app.run('exportPanel()');
    await app.run('saveExport()');
    assert.match(app.window.document.getElementById('exportStatus').textContent,/Saved to/);
    await app.run('saveExport()');
    assert.equal(app.window.document.getElementById('exportError').hidden,false);
    assert.equal(app.window.document.getElementById('exportStatus').textContent,'');
    assert.equal(app.run('pendingExports.length'),1);
    const failedHash=app.run('pendingExports[0].payload_sha256');
    await app.run("changeComparison({reference:readingIdentity(state.rows[0])})");
    app.run("$('pendingExportList').querySelector('button').click()");
    for(let attempt=0;attempt<100&&app.run('pendingExports.length');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(app.run('pendingExports.length'),0);
    const retry=app.messages.findLast(message=>message.method==='retry_analysis_export');
    assert.ok(retry);
    const fsDigest=await import('node:crypto');
    const receipt=await peer.send({id:'receipt-readback',method:'test_export_receipt',params:{}});
    assert.equal(receipt.ok,true);
    assert.equal(fsDigest.createHash('sha256').update(receipt.value.payload).digest('hex'),failedHash);

  }finally{app?.close();await peer.close();}
});


test('whole-batch MAT export does not require checked analysis rows', async()=>{
  const app=await workspace();
  try{
    app.run("showPanel('exportDialog');state.plotted.clear();$('exportScope').value='selected'");
    await app.run('saveExport(true)');
    assert.equal(app.messages.filter(message=>message.method==='export_batch_mat').length,1);
  }finally{app.close();}
});


test('changed source blocks measurement but retains text under the frozen draft options', async()=>{
  const app=await workspace();
  try{
    app.batch.context_current=false;app.batch.context_reason='Source changed; create a new batch';app.batch.can_measure=false;app.batch.revision++;
    await app.run('refresh(true)');
    assert.equal(app.window.document.getElementById('start').disabled,true);
    assert.equal(app.window.document.getElementById('sampleName').disabled,false);
    app.run("$('sampleNotes').value='Preserved after source change';captureSampleEdit();clearTimeout(sampleEditTimer);clearTimeout(samplePreviewTimer)");
    await app.run('saveSampleDraft()');
    assert.equal(app.batch.draft.notes,'Preserved after source change');
    assert.equal(app.window.document.getElementById('finish').disabled,false);
  }finally{app.close();}
});

test('relative previews keep their declared white and clipping separate from emissive scaling', async()=>{
  const app=await workspace(null,{previewScale:120});
  try{
    app.run(`$('previewGamut').value='srgb';
      delete DEFAULT_PROJECTION.display_previews.A.srgb.reference_luminance;
      Object.assign(DEFAULT_PROJECTION.display_previews.A.srgb,{
        reference_unit:'1',common_reference_y:100,source_white:{name:'D50'},
        native_context:{unit:'1',scale:'relative; perfect-diffuser Y=100',observer:'CIE1931_2',illuminant:'D50'},
        display_rgb:[0,.3,.4],linear_rgb:[-.01,.07,.13],clipped_channels:[true,false,false],gamut_status:null
      });
      Object.assign(state.rows[0].obs.backend_reported.xyz,{unit:'1',scale:'relative; perfect-diffuser Y=100',illuminant:'D50'});
      state.rows[0].obs.actual_mode='reflective_spot';
      render();`);
    const native=JSON.stringify(app.run('state.rows[0].obs'));
    const swatches=[...app.window.document.querySelectorAll('#rows .swatch')];
    assert.equal(swatches.length,2);
    const titles=swatches.map(node=>node.title);
    assert.ok(titles.some(title=>/120 cd\/m²/.test(title)),'Absolute previews retain the owner scale');
    const relativeTitle=titles.find(title=>/Relative D50/.test(title));
    assert.ok(relativeTitle);assert.match(relativeTitle,/white Y=100.*Bradford to D65.*Preview channels clipped/);
    assert.doesNotMatch(relativeTitle,/cd\/m²|Inside RGB range|no adaptation/);
    app.run('state.selected=state.rows[0];renderReadingMetadata(state.selected)');
    assert.match(app.window.document.getElementById('detailMetadata').textContent,/Preview contextRelative D50.*white Y=100.*Bradford to D65/);
    assert.equal(JSON.stringify(app.run('state.rows[0].obs')),native);
  }finally{app.close();}
});

test('the owner names the preview scale and the renderer neither restates nor invents it', async () => {
  // Catches a renderer-owned 100: with the owner at 120 the default projection,
  // the applied-scale field and every swatch title would still say 100.
  const w = await workspace(null, { previewScale: 120 });
  try {
    assert.equal(w.run('displayScale'), 120);
    assert.equal(w.run('DEFAULT_PROJECTION.scale'), 120);
    assert.equal(w.run('currentProjection()===DEFAULT_PROJECTION'), true);
    assert.equal(w.window.document.getElementById('previewScale').value, '120');
    const swatches = [...w.window.document.querySelectorAll('.swatch')];
    assert.ok(swatches.length > 0, 'no swatch rendered');
    for (const swatch of swatches) assert.match(swatch.title, /120 cd\/m²/);
    assert.equal(w.messages.filter(m => m.method === 'projection').length, 0);
  } finally { w.close(); }
  // Catches a fabricated default: a reply without the number is refused, not
  // completed at a renderer-chosen scale.
  await assert.rejects(workspace(null, { previewScale: null }), /Preview scale is unavailable/);
});


test('latest reading keeps its own color and values while an earlier reading is inspected',async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document;
    app.run("state.rows.push({...state.rows[0],id:'Parent',name:'Inherited older reading',inherited:true});state.follow=false;state.selected=state.rows[0];render()");
    const latest=doc.getElementById('latest');
    assert.match(latest.querySelector('.latest-name').textContent,/#2 · Sample B/);
    assert.equal(latest.querySelector('.latest-y strong').textContent,'30');
    assert.match(latest.textContent,/X \/ Z20 \/ 40/);
    assert.match(latest.textContent,/x \/ y0.22222 \/ 0.33333/);
    assert.match(latest.textContent,/u′ \/ v′0.16000 \/ 0.54000/);
    const swatch=latest.querySelector('.swatch');
    assert.equal(swatch.dataset.inspect,'B');
    assert.match(swatch.getAttribute('aria-label'),/approximate Display P3 preview/);
    swatch.click();
    assert.equal(app.run('state.selected.id'),'B');
    assert.equal(app.run('state.follow'),false);
    assert.equal(app.messages.some(message=>message.params?.action==='measure'),false);
    // Saved snapshots append inherited chart-source rows the same way.
    app.run("datasetBinding={...datasetBinding,kind:'saved',read_only:true};render()");
    assert.match(latest.querySelector('.latest-name').textContent,/#2 · Sample B/,'Saved review does not name an inherited row as latest');
    app.run('selectLatest()');
    assert.equal(app.run('state.selected.id'),'B','Follow latest in saved review selects this batch\'s own last reading');
    assert.doesNotMatch(latest.textContent,/L\* \/ a\* \/ b\*/);
    app.run("state.white={kind:'theoretical-lab-reference',name:'D50',observer:'CIE1931_2',reference_y:100,unit:'cd/m^2',scale:'absolute; Y is luminance'};currentProjection=()=>({...DEFAULT_PROJECTION,lab_by_source:{B:[50.1234,-2.75,18.125]}});render()");
    assert.match(latest.textContent,/L\* \/ a\* \/ b\*50\.1234 \/ -2\.75 \/ 18\.125/);
    assert.match(latest.textContent,/Lab whiteD50 · assumed standard/,'Lab is never shown without the white it is relative to');
  }finally{await app.run('writes');await new Promise(resolve=>setTimeout(resolve,30));app.close();}
});

test('latest reading uses retained units and does not invent XYZ for spectrum-only data',async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document,latest=doc.getElementById('latest');
    for(const [unit,scale] of [['1','relative; perfect-diffuser Y=100'],['lux','absolute; Y is illuminance'],['1','relative; perfect-transmission Y=100'],['lux s','absolute; Y is luminous exposure'],['cd s/m^2','absolute; Y is time-integrated luminance']]){
      app.run("Object.assign(state.rows.at(-1).obs.backend_reported.xyz,"+JSON.stringify({unit,scale})+");render()");
      assert.equal(latest.querySelector('.latest-unit').textContent,unit.replaceAll('m^2','m²'));
      assert.match(latest.querySelector('.latest-context').textContent,new RegExp(scale.split(';')[0]));
      assert.doesNotMatch(latest.textContent,/cd\/m²/);
    }
    app.run("state.rows.at(-1).obs.backend_reported.xyz=null;state.rows.at(-1).obs.backend_reported.spectrum={wavelengths_nm:[400,700],values:[.1,.2],unit:'1',quantity:'reflectance'};DEFAULT_PROJECTION.display_previews.B={};render()");
    assert.match(latest.textContent,/No native XYZ/);
    assert.match(latest.textContent,/2 bands · 400–700 nm/);
    assert.equal(latest.querySelector('.latest-y'),null);
    assert.equal(latest.querySelector('.latest-metrics'),null);
    assert.equal(latest.querySelector('.swatch'),null);
  }finally{await app.run('writes');await new Promise(resolve=>setTimeout(resolve,30));app.close();}
});

test('save status has a distinct noninteractive state and view settings close with focus restored',async()=>{
  const app=await workspace();
  try{
    const doc=app.window.document,status=doc.getElementById('saveState');
    assert.equal(status.getAttribute('role'),'status');
    assert.equal(status.closest('.brand-heading')!==null,true);
    assert.equal(status.closest('button'),null);
    assert.equal(status.dataset.state,'saved');
    assert.equal(status.querySelector('svg').getAttribute('aria-hidden'),'true');
    const label=status.querySelector('span');app.run('render()');
    assert.equal(status.querySelector('span'),label,'An unchanged live status keeps its nodes and is not announced again');
    app.batch.companions.saving=true;await app.run('refresh(true)');
    assert.equal(status.dataset.state,'pending');
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'failed',current:true,reason:'Controlled disk error',retryable:true};await app.run('refresh(true)');
    assert.equal(status.textContent,'Saving files…');
    assert.equal(status.dataset.state,'pending','The indicator follows the label it accompanies');
    app.batch.companions.saving=false;await app.run('refresh(true)');
    assert.equal(status.textContent,'Files need attention');
    assert.equal(status.dataset.state,'attention');
    app.batch.companions.formats.mat={...app.batch.companions.formats.mat,state:'current',reason:null,retryable:false};
    app.batch.companions.saving=false;
    app.batch.save_reason='Controlled disk failure';await app.run('refresh(true)');
    assert.equal(status.dataset.state,'attention');
    assert.equal(status.textContent,'Save failed');
    const view=doc.querySelector('.workspace-view');
    view.open=true;doc.getElementById('closeView').click();
    assert.equal(view.open,false);
    assert.equal(doc.activeElement,view.querySelector('summary'));
    assert.equal(doc.getElementById('gamutSummary').closest('details').open,false);
    assert.notEqual(doc.getElementById('gamutSummary').tagName,'SUMMARY');
  }finally{await app.run('writes');await new Promise(resolve=>setTimeout(resolve,30));app.close();}
});

test('typed scalar and signed timing values appear without colour substitutes', async()=>{
  const app=await workspace();
  try{
    app.readings.A.observation.record_kind='scalar';
    app.readings.A.observation.backend_reported={value:59.94,unit:'Hz',quantity:'refresh_frequency',native_method:'read_refrate'};
    app.readings.B.observation.record_kind='timing';
    app.readings.B.observation.backend_reported={display_update_delay_ms:18,instrument_reaction_ms:-7,unit:'ms',native_method:'meas_delay',time_reference:'white_change_call'};
    app.batch.revision++;await app.run('refresh(true)');
    const doc=app.window.document;
    assert.match(doc.getElementById('latest').textContent,/Display update 18 ms.*Instrument reaction -7 ms/);
    assert.equal(doc.querySelector('#row-B').closest('tr').querySelector('[data-column="reaction"]').textContent,'-7');
    assert.equal(doc.querySelector('#row-A').closest('tr').querySelector('[data-column="frequency"]').textContent,'59.94');
    assert.equal(doc.querySelector('#row-B').closest('tr').querySelector('[data-column="y"]').textContent,'Unavailable');
    app.run("state.inspector=true; inspect('B')");await new Promise(resolve=>setTimeout(resolve,30));
    assert.match(doc.getElementById('detailMetadata').textContent,/white_change_call/);
  }finally{app.close();}
});

test('inherited inspection keeps qualified identity and cannot edit a colliding local pair',async()=>{
  const app=await workspace();
  try{
    const row=copy(app.readings.B);
    row.inherited=true;row.reading_ref={...row.reading_ref,segment_id:'11111111-1111-4111-8111-111111111111',source_manifest_sha256:'a'.repeat(64)};
    row.name='Parent reading';app.readings.Parent=row;app.batch.revision++;
    await app.run('refresh(true)');
    app.run("state.inspector=true;inspect('Parent')");await app.run('writes');
    await new Promise(resolve=>setTimeout(resolve,30));
    assert.equal(app.window.document.getElementById('editName').disabled,true);
    assert.equal(app.window.document.getElementById('edit-row-Parent').disabled,true);
    assert.equal(app.messages.some(message=>message.params.action==='select'&&message.params.payload?.observation_id==='B'),false);
    assert.deepEqual(app.messages.findLast(message=>message.method==='annotation').params.reading_ref,row.reading_ref);
    await app.run('editReading("Parent","notes")');
    assert.equal(app.messages.some(message=>message.params.action==='edit_annotation_draft'),false);
    await app.run('sendComparison({reference:readingIdentity(state.selected)})');
    assert.deepEqual(app.messages.findLast(message=>message.method==='set_analysis_context').params.reference,row.reading_ref);
    assert.equal(app.run('state.reference'),'Parent');
    app.batch.revision++;await app.run('refresh(true)');
    assert.equal(app.run('state.selected.id'),'Parent','A roster refresh preserves inherited inspection while Follow latest is off');
  }finally{app.close();}
});

test('software presenter waits for drawing and aborts queued white',async()=>{
  const app=await workspace();
  try{
    const callbacks=new Map();let next=0;
    app.window.requestAnimationFrame=callback=>{callbacks.set(++next,callback);return next;};
    app.window.cancelAnimationFrame=id=>callbacks.delete(id);
    app.window.pendingPresentation={phase:'black',rgb:[0,0,0],minimum_delay_ms:0,deadline_ms:1000,stimulus_id:'S',display_id:'D'};
    app.run("window.drawController=new AbortController();window.drawPromise=drawPresentation($('presentationSurface'),window.pendingPresentation,{signal:window.drawController.signal})");
    assert.equal(callbacks.size,1);
    const first=callbacks.values().next().value;callbacks.clear();first();
    assert.equal(callbacks.size,1);
    const second=callbacks.values().next().value;callbacks.clear();second();
    const stamp=await app.run('window.drawPromise');
    assert.equal(stamp.source,'client_draw_completed');assert.equal(stamp.phase,'black');assert.ok(stamp.presented_at_seconds>=0);
    app.run("window.drawController=new AbortController();window.pendingPresentation={...window.pendingPresentation,phase:'white',rgb:[1,1,1]};window.drawPromise=drawPresentation($('presentationSurface'),window.pendingPresentation,{signal:window.drawController.signal})");
    const rejected=assert.rejects(app.run('window.drawPromise'),/cancelled/);
    app.run('window.drawController.abort()');await rejected;
    assert.equal(callbacks.size,0);assert.equal(app.window.document.getElementById('presentationSurface').hidden,true);
  }finally{app.close();}
});

test('actual renderer runs refresh and display timing through Python draw receipts', {timeout:25000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));
    await app.run('setup(true,null,false,true)');
    await app.run("call('settings',{selection_id:null}).then(settings=>loadSetupSelection(settings.selections.find(source=>source.device_id==='demo:i1_display_3').selection_id,setupRequest,actionErrorContext('setupDialog')))");
    await app.run("$('batchName').value='Timing software proof';createBatch()");await app.run('writes');
    const settle=async count=>{const end=Date.now()+16000;while(Date.now()<end){await app.run('refresh(true)');if(app.run(`ownerState.sample_count===${count}&&ownerState.phase==='idle'`))return;await new Promise(resolve=>setTimeout(resolve,30));}throw Error('Typed workflow did not settle: '+app.run('JSON.stringify(ownerState.operations)'));};
    for(const [task,count] of [['refresh_frequency',1],['display_timing',2]]){
      app.run(`$('workflowChoice').value='${task}';renderWorkflowControls();`);
      app.run("$('workflowFields').querySelector('[data-workflow-field=stimulus_id]').value='STIMULUS-1';$('workflowFields').querySelector('[data-workflow-field=stimulus_description]').value='Software test stimulus';");
      if(task==='display_timing')app.run("$('workflowFields').querySelector('[data-workflow-field=display_id]').value='WORKSPACE-DISPLAY';");
      await app.run("begin('Measure')");await app.run('writes');if(!app.run('ownerState.operations.length'))throw Error(app.window.document.getElementById('workflowError').textContent+' | '+app.window.document.getElementById('toast').textContent);await settle(count);
    }
    assert.equal(app.run('state.rows.map(row=>row.obs.record_kind).join(",")'),'scalar,timing');
    const receipts=app.messages.filter(message=>message.params.action==='presented');
    assert.equal(receipts.length,2);
    assert.deepEqual(receipts.map(message=>message.params.payload.stamp.phase),['black','white']);
    assert.equal(new Set(receipts.map(message=>message.params.payload.stamp.clock_id)).size,1);
    assert.ok(receipts[1].params.payload.stamp.presented_at_seconds>receipts[0].params.payload.stamp.presented_at_seconds);
    assert.equal(app.window.document.getElementById('presentationSurface').hidden,true);
  }finally{if(app){for(let i=0;i<200&&app.run('pending.size||annotationLoads.size||analysisPending||projectionPending');i++)await new Promise(resolve=>setTimeout(resolve,10));app.close();}await peer.close();}
});


test('actual renderer selects a nonspot X-Y mode and retains returned-position IDs', {timeout:20000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));await app.run('setup(true,null,false,true)');
    await app.run("call('settings',{selection_id:null}).then(settings=>loadSetupSelection(settings.selections.find(source=>source.device_id==='demo:spectroscan').selection_id,setupRequest,actionErrorContext('setupDialog')))");
    assert.ok([...app.window.document.getElementById('mode').options].some(option=>option.value==='reflective_xy'));
    assert.equal([...app.window.document.getElementById('mode').options].some(option=>option.value.includes('strip')),false);
    await app.run("$('mode').value='reflective_xy';renderSetupOptions();$('batchName').value='Grid workflow proof';createBatch()");await app.run('writes');
    const workflows=copy(app.run('ownerState.workflows'));
    app.window.offeredTasks=[...workflows,...['strip','chart','refresh_frequency'].map(workflow=>({workflow,available:true,min_patches:1,max_patches:2,fields:[]}))];
    app.run('ownerState.workflows=window.offeredTasks;renderWorkflowControls()');
    const tasks=[...app.window.document.getElementById('workflowChoice').options].map(option=>option.value);
    assert.equal(tasks.includes('strip'),false);for(const workflow of ['xy','chart','refresh_frequency'])assert.ok(tasks.includes(workflow));
    app.run('ownerState.workflows=ownerState.workflows.filter(item=>!["chart","refresh_frequency"].includes(item.workflow));renderWorkflowControls()');
    app.run("$('workflowChoice').value='xy';renderWorkflowControls();$('workflowFields').querySelector('[data-workflow-field=passes]').value='1';$('workflowFields').querySelector('[data-workflow-field=steps]').value='2';");
    app.window.document.getElementById('workflowPatchIds').value=['GRID-01','GRID-02'].join('\n');
    const field=app.window.document.querySelector('[data-workflow-field=passes]');field.focus();
    await app.run('refresh(true)');assert.equal(app.window.document.querySelector('[data-workflow-field=passes]'),field);
    await app.run("begin('Measure')");await app.run('writes');
    const end=Date.now()+12000;
    while(Date.now()<end){await app.run('refresh(true)');if(app.run("ownerState.phase==='idle'&&ownerState.sample_count===2"))break;await new Promise(resolve=>setTimeout(resolve,30));}
    assert.equal(app.run('ownerState.sample_count'),2,app.window.document.getElementById('toast').textContent);
    assert.equal(app.run('state.rows.map(row=>row.obs.sample_id).join(",")'),'GRID-01,GRID-02');
    const command=app.messages.findLast(message=>message.params.action==='run_workflow');
    assert.deepEqual(command.params.payload.layout,{passes:1,steps:2});
    assert.deepEqual(command.params.payload.patch_ids,['GRID-01','GRID-02']);
  }finally{if(app){for(let i=0;i<200&&app.run('pending.size||annotationLoads.size||analysisPending||projectionPending');i++)await new Promise(resolve=>setTimeout(resolve,10));app.close();}await peer.close();}
});


test('queued cancellation retains its original operation and never targets a successor',async()=>{
  const app=await workspace();
  try{
    app.run("ownerState={...ownerState,phase:'active',operation_id:'operation-A',operations:[{operation_id:'operation-A',command_id:'command-A'}]};writes=new Promise(resolve=>window.releaseCancelQueue=resolve);");
    app.run('cancel()');
    app.run("ownerState={...ownerState,operation_id:'operation-B',operations:[{operation_id:'operation-B',command_id:'command-B'}]};window.releaseCancelQueue();");
    await app.run('writes');
    assert.equal(app.messages.some(message=>message.params.action==='cancel'),false);
    app.run("ownerState={...ownerState,phase:'active',operation_id:'operation-B',operations:[{operation_id:'operation-B',command_id:'command-B'}]};");
    await app.run('cancel()');
    const command=app.messages.findLast(message=>message.params.action==='cancel');
    assert.deepEqual(command.params.payload,{command_id:'command-B'});
  }finally{app.close();}
});


test('actual renderer chart guide separates inspection, targeting, history and incomplete finish', {timeout:45000},async()=>{
  const peer=pythonOwnerPeer();let app,crossSnapshot=false,crossedSnapshot=false;const held=[],holdMethods=new Set();
  const settledMetadata=async command_id=>{
    const end=Date.now()+8000;let query=0;
    while(Date.now()<end){
      const outcome=await peer.send({id:command_id+'/outcome/'+query++,method:'outcome',params:{command_id}});
      assert.equal(outcome.ok,true);
      if(outcome.value.state==='completed'){
        const snapshot=await peer.send({id:command_id+'/snapshot/'+query++,method:'snapshot',params:{}});
        assert.equal(snapshot.ok,true);if(snapshot.value.batch.phase==='idle')return snapshot.value;
      }else assert.ok(['pending','saving'].includes(outcome.value.state),JSON.stringify(outcome.value));
      await new Promise(resolve=>setTimeout(resolve,10));
    }
    throw Error('Real chart metadata publication did not settle');
  };
  try{
    app=await workspace(async message=>{
      const hold=holdMethods.has(message.method),reply=await peer.send(message);
      if(message.method==='settings')reply.value.selections.push({selection_id:'physical-source',device_id:'instrument:physical',device:{model:'Physical source'},synthetic:false});
      if(message.method==='snapshot'&&crossSnapshot){
        // Advance real metadata after the captured witness, before its dataset
        // query. Synchronisation must retry that snapshot without refreshing twice.
        crossSnapshot=false;const batch=reply.value.batch,draft=batch.draft;
        const updated=await peer.send({id:message.id+'/metadata',method:'command',params:{
          command_id:message.id,batch_id:batch.batch_id,generation:batch.generation,action:'edit_draft',
          payload:{draft_id:draft.id,version:draft.version,name:draft.name,notes:'Chart snapshot crossing',options:draft.options,number_samples:draft.number_samples},
        }});
        assert.equal(updated.ok,true);assert.equal(updated.value.admission,'admitted');
        const settled=await settledMetadata(message.id);
        assert.notEqual(settled.dataset_binding.roster_revision,reply.value.dataset_binding.roster_revision,'Actual publication must advance the captured snapshot witness');
        crossedSnapshot=true;
      }
      if(hold)return new Promise(resolve=>held.push({message,reply,resolve}));return reply;
    });
    const demoButton=app.window.document.getElementById('demoChart');assert.equal(demoButton.disabled,false);
    app.window.document.getElementById('demoToggle').click();app.run('render()');
    assert.equal(app.window.document.getElementById('demoControls').hidden,false,'Owner updates preserve the expanded toolbar');
    await demoButton.onclick();
    assert.equal(app.run('setupChartDemo'),true);assert.equal(app.window.document.getElementById('mode').value,'reflective_spot');
    const sourceOptions=[...app.window.document.getElementById('device').options];
    const demoSources=sourceOptions.filter(option=>option.value);
    assert.equal(sourceOptions.find(option=>!option.value)?.selected,false,'The blank placeholder is not the chosen Demo source');
    assert.ok(demoSources.length>0,'The Demo entry offers selectable simulated instruments');
    assert.equal(demoSources.some(option=>option.value==='physical-source'),false,'The injected physical source never enters Demo choices');
    assert.ok(demoSources.every(option=>option.textContent.startsWith('Demo · ')),'Every selectable Demo source is labelled as simulated');
    app.window.document.getElementById('dismissSetup').click();await app.run('setup(true,null,false,true)');
    assert.equal(app.run('setupChartDemo'),false,'Ordinary setup cannot inherit a cancelled Demo intent');
    app.window.document.getElementById('dismissSetup').click();await demoButton.onclick();
    const waitFor=async check=>{const end=Date.now()+8000;while(!check()){if(Date.now()>end)throw Error('Controlled chart reply did not settle');await new Promise(resolve=>setTimeout(resolve,10));}};
    await app.run("$('batchName').value='Chart controls proof';createBatch()");await app.run('writes');
    assert.equal(app.run('activePanel'),'chartSetupDialog','Creating the Demo opens the existing chart setup');
    assert.equal(app.messages.some(message=>['connect_device','refresh_devices'].includes(message.method)),false);
    app.window.document.getElementById('chartKind').value='sg';app.window.document.getElementById('chartKind').onchange();app.window.document.getElementById('previewChart').click();await app.run('writes');
    assert.equal(app.window.document.querySelectorAll('#chartSetupPreview [data-chart-cell]').length,140);
    app.window.document.getElementById('chartKind').value='classic';app.window.document.getElementById('chartKind').onchange();app.window.document.getElementById('previewChart').click();await app.run('writes');
    assert.equal(app.window.document.querySelectorAll('#chartSetupPreview [data-chart-cell]').length,24);
    app.window.document.getElementById('configureChart').click();await app.run('writes');
    const settle=async count=>{const end=Date.now()+12000;while(Date.now()<end){await app.run('refresh(true)');if(app.run(`ownerState.sample_count===${count}&&ownerState.phase==='idle'`))return;await new Promise(resolve=>setTimeout(resolve,30));}throw Error(app.window.document.getElementById('toast').textContent);};
    await settle(0);assert.equal(app.run('state.plotSlots[0]'),'chart');
    const inspect=app.window.document.getElementById('chartInspect');
    assert.equal(inspect.options.length,24,'A normal-size equivalent control offers every compact chart cell');
    inspect.value='2';await inspect.onchange();await app.run('writes');
    assert.equal(app.run('ownerState.chart.inspection_cell'),2);assert.equal(app.run('ownerState.chart.current_cell'),1,'The inspection picker cannot retarget acquisition');
    inspect.value='1';await inspect.onchange();await app.run('writes');
    for(const id of ['demo40','demo160'])assert.equal(app.window.document.getElementById(id).disabled,true,'Individual bulk loading cannot fill a guided chart');
    await Promise.all([app.run("chartEdit('rotate',{degrees:90})"),app.run('inspectChartCell(2)')]);
    assert.equal(app.run('ownerState.chart.orientation'),90);
    assert.equal(app.run('ownerState.chart.inspection_cell'),2,'A queued inspection follows the completed rotation revision');
    await Promise.all([app.run("chartEdit('rotate',{degrees:0})"),app.run('inspectChartCell(1)')]);
    assert.equal(app.window.document.getElementById('start').title,'Measure patch A1 (Ctrl+Shift+M)');
    assert.equal(app.window.document.getElementById('emptyReadingsMessage').textContent,'No readings yet. Measure patch A1.');
    // Read the settled owner state: one captured while the queued edits ran
    // still reports calibration as unavailable.
    await app.run('refresh(true)');
    assert.equal(app.window.document.getElementById('calibrate').disabled,false,'Shared calibration remains available in chart mode');
    await app.run('setup()');await app.run('writes');
    assert.equal(app.window.document.getElementById('applySetup').disabled,false,'Shared device settings remain available in chart mode');
    app.window.document.getElementById('dismissSetup').click();
    await app.run("begin('Measure')");await settle(1);
    assert.equal(app.run('ownerState.chart.current_cell'),2);
    const checkMeasuredPreview=row=>{
      app.window.previewReading=row;
      const preview=app.run('displayPreview(window.previewReading,$("previewGamut").value)');
      assert.equal(preview.reference_unit,'1');assert.equal(preview.source_white.name,'D50');
      assert.equal(preview.common_reference_y,100);assert.equal('reference_luminance' in preview,false);assert.equal(preview.gamut_status,null);
      const css=app.run('previewCss(displayPreview(window.previewReading,$("previewGamut").value).display_rgb)');
      const swatch=app.window.document.getElementById('table-swatch-'+row.id);
      assert.ok(swatch,'Each retained chart attempt has its own measured swatch');
      assert.equal(swatch.getAttribute('style'),'background:'+css);
      assert.match(swatch.title,/Relative D50.*white Y=100.*Bradford to D65/);
      assert.doesNotMatch(swatch.title,/cd\/m²|Inside RGB range/);
      const curve=app.window.document.querySelector('#curve-'+row.id+' .curve');
      assert.ok(curve);assert.ok(curve.getAttribute('style').startsWith('stroke:'+css+';'));
    };
    app.run("$('curveContrast').checked=false;render()");
    checkMeasuredPreview(app.run('state.rows[0]'));
    await waitFor(()=>app.run('chartColors!==null&&!chartColorError'));
    // The inspected-patch detail reads the actual Python reply's fields.
    assert.equal(app.run('chartView.inspection_cell'),1);
    const measuredA1=app.run('chartColors.measured.find(item=>item.cell_index===1)');
    assert.equal(measuredA1.available,true,measuredA1.reason);
    const detail=Object.fromEntries([...app.window.document.querySelectorAll('#chartInspectionBody tbody tr')].map(tr=>[tr.children[0].textContent,[tr.children[1].textContent,tr.children[2].textContent]]));
    assert.equal(detail.XYZ[1],app.run(`${JSON.stringify(measuredA1.xyz)}.map(numberText).join(' · ')`));
    assert.equal(detail.Lab[1],app.run(`${JSON.stringify(measuredA1.lab)}.map(numberText).join(' · ')`));
    assert.equal(detail['White XYZ'][1],app.run(`${JSON.stringify(measuredA1.context.white.xyz)}.map(numberText).join(' · ')`)+' · D50');
    assert.equal(detail.Source[1],app.run('rowName(state.rows[0])')+' · native XYZ');
    assert.equal(detail.Preview[0],'No authenticated TI2 expected values','A Classic chart has no TI2 reference to compare');
    await waitFor(()=>app.run('chartColors?.chart_revision===ownerState.chart.revision&&!chartColorError'));
    const earlierChart=copy(app.run('chartView'));
    // Advance the actual owner without refreshing the renderer: the next color
    // query must recover a genuinely older displayed witness.
    const rotationId=app.run('crypto.randomUUID()'),binding=copy(app.run('ownerState'));
    const rotated=await peer.send({id:rotationId,method:'command',params:{
      command_id:rotationId,batch_id:binding.batch_id,generation:binding.generation,action:'chart_edit',
      payload:{chart_revision:binding.chart.revision,action:'rotate',operands:{degrees:90}},
    }});
    assert.equal(rotated.ok,true);assert.equal(rotated.value.admission,'admitted');
    const published=await settledMetadata(rotationId),currentChartRevision=published.batch.chart.revision;
    assert.ok(currentChartRevision>earlierChart.revision);
    assert.equal(app.run('chartView.revision'),earlierChart.revision,'The displayed witness must remain behind the real owner');
    assert.notEqual(published.dataset_binding.roster_revision,app.run('datasetBinding.roster_revision'),'The recovery snapshot must require a dataset query');
    const snapshotsBefore=app.messages.filter(message=>message.method==='snapshot').length;
    const originalRefresh=app.run('refresh');let witnessRefreshes=0;
    app.window.refreshChartWitness=(...args)=>{witnessRefreshes++;return originalRefresh(...args);};
    app.run('refresh=window.refreshChartWitness');crossSnapshot=true;
    try{
      app.run("chartColorKey='';requestChartColors()");
      await waitFor(()=>app.run('chartColors!==null&&!chartColorError&&chartColors.chart_revision===chartView.revision')&&app.run('chartView.revision')===currentChartRevision);
      assert.equal(witnessRefreshes,1,'A newer actual Python color reply refreshes its authoritative chart witness once');
      assert.equal(crossedSnapshot,true,'The real owner revision must cross the captured snapshot');
      assert.ok(app.messages.filter(message=>message.method==='snapshot').length-snapshotsBefore>=2,'One witness refresh must retry a snapshot crossed by real metadata progress');
    }finally{app.window.refreshChartWitness=originalRefresh;app.run('refresh=window.refreshChartWitness');delete app.window.refreshChartWitness;}
    assert.equal(app.window.document.getElementById('retryChartColors').hidden,true,'Ordinary owner progress does not show a false refusal');
    assert.equal(app.window.document.querySelector('#chartGuide [data-chart-cell="1"]').dataset.chartColorKind,'measured');
    await app.run("chartEdit('rotate',{degrees:0})");await app.run('writes');
    app.window.document.querySelector('#chartGuide [data-chart-cell="1"]').click();await app.run('writes');
    assert.equal(app.run('ownerState.chart.inspection_cell'),1);
    assert.equal(app.run('ownerState.chart.current_cell'),2,'Inspection never retargets acquisition');
    assert.equal(app.window.document.getElementById('start').title,'Measure patch B1 (Ctrl+Shift+M)');
    app.window.document.getElementById('chartTarget').click();await app.run('writes');
    assert.equal(app.run('ownerState.chart.current_cell'),1);
    assert.equal(app.window.document.getElementById('start').title,'Measure patch A1 (Ctrl+Shift+M)');
    await app.run("begin('Measure')");await settle(2);
    assert.equal(app.run('ownerState.chart.coverage'),1,'Repeating a cell retains both attempts without inventing coverage');
    app.run('render()');for(const row of app.run('state.rows'))checkMeasuredPreview(row);
    await app.run('loadChartHistory()');
    assert.equal(app.window.document.querySelectorAll('#chartHistoryContent section').length,2);
    app.window.document.getElementById('dismissChartHistory').click();
    // Close/reopen and change cell while the first history is still in flight.
    holdMethods.add('chart_history');const staleHistory=app.run('loadChartHistory()');await waitFor(()=>held.length===1);
    app.window.document.getElementById('dismissChartHistory').click();await app.run('inspectChartCell(2)');await app.run('writes');
    const currentHistory=app.run('loadChartHistory()');await waitFor(()=>held.length===2);
    held[0].resolve(held[0].reply);await staleHistory;
    assert.equal(app.window.document.querySelectorAll('#chartHistoryContent section').length,0,'Old cell history cannot append to a reopened panel');
    held[1].resolve(held[1].reply);await currentHistory;
    assert.match(app.window.document.getElementById('chartHistoryContent').textContent,/No attempts/);
    holdMethods.delete('chart_history');held.length=0;
    app.window.document.getElementById('dismissChartHistory').click();await app.run('inspectChartCell(1)');await app.run('writes');await app.run('loadChartHistory()');
    const staleSection=app.window.document.querySelector('#chartHistoryContent section'),staleSelect=staleSection.querySelectorAll('button')[1];
    holdMethods.add('chart_details');const staleDetails=staleSection.querySelector('button').onclick();await waitFor(()=>held.length===1);
    await app.run('inspectChartCell(2)');await app.run('writes');
    held[0].resolve(held[0].reply);await staleDetails;
    assert.equal(staleSection.querySelector('pre'),null,'Old-cell details cannot append after inspection changes');
    const commandCount=app.messages.filter(message=>message.params.action==='chart_edit').length;
    staleSelect.click();await app.run('writes');assert.equal(app.messages.filter(message=>message.params.action==='chart_edit').length,commandCount,'Old-cell attempt controls cannot mutate a new inspection');
    holdMethods.delete('chart_details');held.length=0;
    app.window.document.getElementById('dismissChartHistory').click();await app.run('inspectChartCell(1)');await app.run('writes');await app.run('loadChartHistory()');
    holdMethods.add('chart_history');const more=app.run('loadChartHistory(true)');await app.run('loadChartHistory(true)');await waitFor(()=>held.length===1);
    assert.equal(app.window.document.getElementById('moreChartHistory').disabled,true);
    held[0].resolve(held[0].reply);await more;
    assert.equal(app.window.document.querySelectorAll('#chartHistoryContent section').length,2,'Concurrent More does not duplicate a page');
    holdMethods.delete('chart_history');held.length=0;app.window.document.getElementById('dismissChartHistory').click();
    const priorPass=app.run('chartView.active_pass');await app.run("chartEdit('start_pass',{})");await app.run('writes');
    // Use the real Python historical view under a controlled saved binding summary.
    app.run("datasetBinding={...datasetBinding,kind:'saved',read_only:true}");app.window.priorPass=priorPass;app.run('loadSelectedChart(window.priorPass)');
    await waitFor(()=>app.run('chartView?.viewed_pass')===priorPass);
    const snapshot=await app.run("call('snapshot')");snapshot.dataset_binding.kind='saved';snapshot.dataset_binding.read_only=true;snapshot.view={follow:false};
    app.window.savedSnapshot=snapshot;await app.run('synchronise(window.savedSnapshot)');
    assert.equal(app.run('chartViewedPass'),priorPass,'A saved-view refresh preserves historical pass selection');
    await waitFor(()=>app.run('chartView?.viewed_pass')===priorPass);
    // An in-flight view and history cannot restore a source that became unavailable.
    holdMethods.add('chart_view');holdMethods.add('chart_history');
    app.run('chartViewKey="";loadSelectedChart(window.priorPass)');const staleSourceHistory=app.run('loadChartHistory()');await waitFor(()=>held.length===2);
    snapshot.dataset_binding.available=false;snapshot.dataset_binding.reason='Saved source changed';app.window.savedSnapshot=copy(snapshot);await app.run('synchronise(window.savedSnapshot)');
    for(const item of held)item.resolve(item.reply);await staleSourceHistory;await new Promise(resolve=>setImmediate(resolve));
    assert.equal(app.run('chartView'),null,'Late view cannot resurrect unavailable coverage');
    assert.equal(app.window.document.querySelectorAll('#chartHistoryContent section').length,0,'Late history cannot resurrect unavailable attempts');
    holdMethods.clear();held.length=0;app.window.document.getElementById('dismissChartHistory').click();await app.run('refresh(true)');
    app.run('loadSelectedChart(window.priorPass)');await waitFor(()=>app.run('chartView?.viewed_pass')===priorPass);
    // An old displayed pass must not supply active-pass Finish consent.
    app.run('chartView={...chartView,coverage:24,total:24,incomplete:false}');
    app.window.document.getElementById('finish').click();
    assert.equal(app.window.document.getElementById('allowIncompleteLabel').hidden,false);
    assert.match(app.window.document.getElementById('finishChartStatus').textContent,/Current pass · 0 of 24/);
    assert.equal(app.window.document.getElementById('confirmFinishChart').disabled,true);
    app.window.document.getElementById('dismissFinishChart').click();
    app.run('chartView={...chartView,incomplete:true};ownerState.chart={...ownerState.chart,coverage:24,incomplete:false}');
    app.window.document.getElementById('finishChart').click();
    assert.equal(app.window.document.getElementById('allowIncompleteLabel').hidden,true,'An old incomplete view does not require consent for a complete active pass');
    assert.equal(app.window.document.getElementById('confirmFinishChart').disabled,false);
    app.window.document.getElementById('dismissFinishChart').click();app.run("lastSnapshotKey=''");await app.run('refresh(true)');
    app.window.document.getElementById('finishChart').click();app.window.document.getElementById('allowIncompleteChart').click();
    await app.run("chartEdit('select_cell',{cell:2})");await app.run('writes');
    await app.window.document.getElementById('confirmFinishChart').onclick();
    assert.match(app.window.document.getElementById('finishChartError').textContent,/Chart changed/,'Consent cannot finish a later chart revision');
    assert.equal(app.messages.filter(message=>message.params.action==='finish_chart').length,0);
    // Reopen Finish on the settled revision, as above.
    app.window.document.getElementById('dismissFinishChart').click();app.run("lastSnapshotKey=''");await app.run('refresh(true)');
    app.window.document.getElementById('finishChart').click();
    assert.equal(app.window.document.getElementById('confirmFinishChart').disabled,true);
    app.window.document.getElementById('allowIncompleteChart').click();app.window.document.getElementById('confirmFinishChart').click();await app.run('writes');
    // The finish reply and the owner state refresh land asynchronously.
    await waitFor(()=>app.run('ownerState.chart.finished')===true);
    const finishCommands=app.messages.filter(message=>message.method==='command').length;
    app.window.activePass=app.run('ownerState.chart.active_pass');app.run('loadSelectedChart(window.activePass)');await waitFor(()=>app.run('chartView?.viewed_pass')===app.window.activePass);
    app.window.document.querySelector('#chartGuide [data-chart-cell="2"]').click();await app.run('writes');
    assert.equal(app.run('chartView.inspection_cell'),2,'Finished charts remain locally inspectable');
    assert.equal(app.messages.filter(message=>message.method==='command').length,finishCommands,'Finished inspection never mutates the owner');
    assert.equal(app.messages.filter(message=>message.params.action==='finish').length,0,'Ordinary Finish uses chart consent instead of a refused ordinary command');
    const staged=app.messages.filter(message=>message.method==='stage_chart');
    assert.deepEqual(staged.map(message=>message.params.recipe),[{kind:'sg'},{kind:'classic'}]);
    const configure=app.messages.find(message=>message.params.action==='configure_chart');
    assert.deepEqual(Object.keys(configure.params.payload).sort(),['definition_token','mapping_confirmation']);
  }finally{holdMethods.clear();for(const item of held)item.resolve(item.reply);if(app){for(let i=0;i<200&&app.run('pending.size||annotationLoads.size||analysisPending||projectionPending');i++)await new Promise(resolve=>setTimeout(resolve,10));app.close();}await peer.close();}
});


test('standalone review keeps original terminal facts and offers record formats without borrowed live controls',async()=>{
  const app=await workspace();
  try{
    delete app.readings.A;delete app.readings.B;
    const source={kind:'standalone',path:'/recording-only/cancelled.json',format:'operation_json',record_kind:'operation',operation_id:'cancelled-native-id',operation_status:'cancelled',operation_reason:'Operator cancelled before any readings',source_sha256:'original-hash',source_bytes:42,device:{model:'Original device'},provenance:'retained original',software_stopped:true,instrument_state:'unconfirmed'};
    app.packet.dataset_binding={id:'standalone-current',generation:1,kind:'standalone',read_only:true,available:true,roster_revision:'source/0',source,comparison:{version:0,reference:null,white:null,white_descriptor:null,available:true,persisted:false}};
    await app.run('refresh(true)');
    const document=app.window.document;
    assert.equal(document.getElementById('batchTitle').textContent,'cancelled.json');
    assert.equal(document.getElementById('sourceContext').textContent,'Original device');
    assert.equal(document.getElementById('modeContext').textContent,'');
    assert.equal(document.getElementById('taskContext').textContent,'Imported record');
    assert.equal(document.querySelector('.control-dock').hidden,true);
    assert.equal(document.getElementById('returnLive').hidden,false);
    assert.equal(document.getElementById('demoBar').hidden,true);
    assert.equal(document.getElementById('automaticFiles').hidden,true);
    assert.equal(document.getElementById('originalRecordNotice').hidden,false);
    assert.equal(document.getElementById('originalRecordStatus').textContent,'Original operation: cancelled');
    assert.match(document.getElementById('originalRecordReason').textContent,/Operator cancelled/);
    assert.match(document.getElementById('emptyReadingsMessage').textContent,/source contains no/);
    await app.run('exportPanel()');
    assert.equal(document.getElementById('saveBatchMat').disabled,true);
    assert.equal(document.getElementById('saveAnalysis').disabled,true);
    assert.equal(document.getElementById('saveRecord').disabled,false);
    assert.equal(document.getElementById('recordExportReason').hidden,true,'Available JSON must not show refusals for other formats');
    assert.equal(document.getElementById('recordExportUnavailable').hidden,false);
    assert.equal(document.getElementById('recordExportUnavailable').open,false);
    assert.match(document.getElementById('recordExportUnavailableList').textContent,/Native SPECT.*Native reflective spectrum required/);
    assert.equal(document.getElementById('recordExportFormat').querySelector('[value="sp"]').disabled,true,'The disclosure does not change format admission');
    await app.run('saveRecord()');
    const request=app.messages.findLast(message=>message.method==='export_record');
    assert.deepEqual(request.params,{binding_id:'standalone-current',generation:1,dataset_revision:'batch/1',reading_ref:null,format:'json'});
    assert.ok(!Object.hasOwn(request.params,'path'));
    app.window.exportReceipt={path:'/recording-only/export.json',ti3:{status:'unavailable',retryable:false,relocatable:true,unavailable:[],files:[{scope:'ordinary',state:'unavailable',path:'/recording-only/export.ti3',group_id:'native-group',row_count:1,total_rows:null,reason:'Destination belongs to another export'}]}};
    app.run("recordSaved(window.exportReceipt,{...actionErrorContext('exportDialog'),request:recordExportContext,format:$('recordExportFormat').value})");
    assert.equal(document.getElementById('recordCompanionNotice').hidden,false);
    assert.match(document.getElementById('recordCompanionDetails').textContent,/Destination belongs to another export/);
    assert.match(document.getElementById('recordExportDetails').textContent,/Readings.*Unavailable/);
    app.window.exportReceipt={path:'/recording-only/export.json',ti3:{status:'unavailable',retryable:false,relocatable:false,unavailable:[{record_id:'original',component:'spectrum',reason:'No native spectrum'}],files:[]}};
    app.run("recordSaved(window.exportReceipt,{...actionErrorContext('exportDialog'),request:recordExportContext,format:$('recordExportFormat').value})");
    assert.equal(document.getElementById('recordCompanionNotice').hidden,true,'Ordinary absent spectra do not create a saving alarm');
    app.run("$('recordExportDetails').textContent='Current panel details';recordSaved(window.exportReceipt,{id:'exportDialog',epoch:panelEpoch-1,request:recordExportContext,format:$('recordExportFormat').value})");
    assert.equal(document.getElementById('recordExportDetails').textContent,'Current panel details','Obsolete export details cannot replace the current panel');
    app.run('recordSaved(window.exportReceipt)');
    assert.equal(document.getElementById('recordExportDetails').textContent,'Current panel details','Global recovery receipts do not overwrite panel details');
    const before=app.messages.filter(message=>message.method==='export_batch_mat').length;
    await app.run('saveExport(true)');
    assert.equal(app.messages.filter(message=>message.method==='export_batch_mat').length,before);
  }finally{app.close();}
});


test('record format replies and save actions stay bound to the inspected original',async()=>{
  const app=await workspace();
  try{
    app.run("inspect('A');state.follow=false");
    await app.run('exportPanel()');
    app.run("inspect('B')");
    await app.run('saveRecord()');
    const first=app.messages.findLast(message=>message.method==='export_record');
    assert.deepEqual(first.params.reading_ref,{operation_id:'operation-A',observation_id:'A'});
    assert.match(app.window.document.getElementById('recordExportScope').textContent,/Sample A/);
    app.batch.revision++;await app.run('refresh(true)');
    const count=app.messages.filter(message=>message.method==='export_record').length;
    await app.run('saveRecord()');
    assert.equal(app.messages.filter(message=>message.method==='export_record').length,count);
    assert.match(app.window.document.getElementById('exportError').textContent,/original record changed/);
    await app.run('recordPanel()');
    const format=app.window.document.getElementById('recordImportFormat');
    assert.equal(format.querySelector('[value="operation_mat"]').disabled,true);
    assert.equal(format.querySelector('[value="observation_csv"]').disabled,false);
    const original=app.window.ownerTransport.postMessage,held=[];
    app.window.ownerTransport.postMessage=message=>{
      if(message.method==='record_formats')held.push(message);else original(message);
    };
    const waiting=app.run('exportPanel()');
    for(let attempt=0;attempt<100&&!held.length;attempt++)await new Promise(resolve=>setTimeout(resolve,1));
    assert.equal(held.length,1);
    app.run("closePanel('exportDialog')");
    app.window.ownerReply({id:held[0].id,ok:true,value:{formats:[{format:'json',available:true,reason:null}]}});
    await waiting;
    assert.equal(app.run('recordExportContext'),null);
    const stale=app.run('exportPanel()');
    for(let attempt=0;attempt<100&&held.length<2;attempt++)await new Promise(resolve=>setTimeout(resolve,1));
    assert.equal(held.length,2);
    app.batch.generation++;await app.run('refresh(true)');
    app.window.ownerReply({id:held[1].id,ok:false,error:'Old source no longer readable'});
    await stale;
    assert.equal(app.window.document.getElementById('exportError').hidden,true,'An obsolete source error cannot paint the new export panel');
  }finally{app.close();}
});


test('original export receipts clear on a new target and source revision',async()=>{
  const app=await workspace();
  try{
    app.run("inspect('A')");await app.run('exportPanel()');
    app.run("$('recordExportDetails').textContent='Prior A files';closePanel('exportDialog');inspect('B')");
    await app.run('exportPanel()');
    assert.equal(app.window.document.getElementById('recordExportDetails').textContent,'');
    app.run("$('recordExportDetails').textContent='Prior B files'");
    app.batch.revision++;await app.run('refresh(true)');
    assert.equal(app.window.document.getElementById('recordExportDetails').textContent,'');
    await app.run('writes');
    const deadline=Date.now()+2000;
    while(app.run('annotationLoads.size')){
      assert.ok(Date.now()<deadline,'Current annotation requests must finish before destroying the test DOM');
      await new Promise(resolve=>setImmediate(resolve));
    }
  }finally{app.close();}
});


test('late original export receipt cannot paint a changed target or format',async()=>{
  const app=await workspace();
  try{
    app.run("inspect('A')");await app.run('exportPanel()');
    const original=app.window.ownerTransport.postMessage,held=[];
    app.window.ownerTransport.postMessage=message=>{
      if(message.method==='export_record')held.push(message);else original(message);
    };
    const saving=app.run('saveRecord()');
    for(let attempt=0;attempt<100&&!held.length;attempt++)await new Promise(resolve=>setTimeout(resolve,1));
    assert.equal(held.length,1);
    assert.deepEqual(held[0].params.reading_ref,{operation_id:'operation-A',observation_id:'A'});
    app.run("$('recordExportFormat').value='csv';$('recordExportFormat').onchange()");
    app.window.ownerReply({id:held[0].id,ok:true,value:{path:'/A.json',ti3:{status:'stale',retryable:true,unavailable:[],files:[{scope:'ordinary',state:'stale',path:'/A.ti3',group_id:'A',row_count:1,total_rows:null,reason:'A companion needs retry'}]}}});
    await saving;
    assert.equal(app.window.document.getElementById('recordExportDetails').textContent,'');
    assert.equal(app.window.document.getElementById('exportStatus').textContent,'');
    assert.match(app.window.document.getElementById('recordCompanionDetails').textContent,/A companion needs retry/,'Global failure remains visible');
    app.window.ownerTransport.postMessage=original;
  }finally{app.close();}
});


test('imported review names the retained live batch and its recovery folder',async()=>{
  const app=await workspace();
  try{
    app.batch.batch_name='Retained live batch';app.batch.save_reason='Live original failed';
    app.packet.dataset_binding={...app.packet.dataset_binding,kind:'standalone',read_only:true,
      source:{kind:'standalone',path:'/imported/observation.json',format:'observation_json',record_kind:'observation',operation_status:null,device:{model:'Imported original'},provenance:'simulated'}};
    await app.run('refresh(true)');
    const reason=app.window.document.getElementById('recoveryReason').textContent;
    assert.match(reason,/Retained live batch/);assert.match(reason,/synthetic\/batch/);
    assert.doesNotMatch(reason,/imported\/observation/);
  }finally{app.close();}
});


test('late original export receipt respects an explicit target switch in the same panel',async()=>{
  const app=await workspace();
  try{
    app.packet.dataset_binding={...app.packet.dataset_binding,kind:'standalone',read_only:true,
      source:{kind:'standalone',path:'/imported/operation.json',format:'operation_json',record_kind:'operation',operation_status:'completed',device:{model:'Imported original'},provenance:'simulated'}};
    await app.run('refresh(true)');app.run("inspect('A')");await app.run('exportPanel()');
    app.run("$('recordExportScope').value='1'");await app.run('loadRecordFormats()');
    const original=app.window.ownerTransport.postMessage,held=[];
    app.window.ownerTransport.postMessage=message=>{
      if(message.method==='export_record')held.push(message);else original(message);
    };
    const saving=app.run('saveRecord()');
    for(let attempt=0;attempt<100&&!held.length;attempt++)await new Promise(resolve=>setTimeout(resolve,1));
    assert.equal(held.length,1);assert.equal(held[0].params.reading_ref.observation_id,'A');
    app.run("$('recordExportScope').value='0'");await app.run('loadRecordFormats()');
    app.window.ownerReply({id:held[0].id,ok:true,value:{path:'/A.json',ti3:{status:'stale',retryable:true,unavailable:[],files:[{scope:'ordinary',state:'stale',path:'/A.ti3',group_id:'A',row_count:1,total_rows:null,reason:'A companion needs retry'}]}}});
    await saving;
    assert.equal(app.window.document.getElementById('recordExportDetails').textContent,'');
    assert.equal(app.window.document.getElementById('exportStatus').textContent,'');
    assert.match(app.window.document.getElementById('recordCompanionDetails').textContent,/A companion needs retry/);
    assert.equal(app.run('recordExportContext.reading_ref'),null);
  }finally{app.close();}
});


test('actual imported observation analysis export and native recovery preserve the live measurement owner',{timeout:35000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));
    await app.run('setup(true,null,false,true)');app.run("$('batchName').value='Standalone integration';createBatch()");await app.run('writes');
    for(let index=1;index<=2;index++){
      await app.run("$('start').onclick()");
      for(let attempt=0;attempt<150;attempt++){
        await app.run('refresh(true)');
        if(app.run('state.rows.length')===index&&app.run('ownerState.can_measure'))break;
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      assert.equal(app.run('state.rows.length'),index);
    }
    const live=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    await app.run('recordPanel()');app.run("$('recordImportFormat').value='observation_json'");await app.run('chooseRecord()');
    assert.equal(app.run('datasetBinding.kind'),'standalone');
    assert.equal(app.run('state.rows.length'),1);
    const native=copy(app.run('state.rows[0].obs'));
    assert.equal(native.observation_id,JSON.parse(live)[0].observation_id);
    assert.equal(app.run('displayDescriptor()'),null);
    app.run('inspect(state.rows[0].id)');await app.run('changeComparison({reference:readingIdentity(state.selected)})');
    app.run("setTool('compare')");
    await waitForComparisonAnalysis(app);
    const text=app.run('comparisonSummaryText()');
    assert.match(text,/Record format: observation_json/);assert.match(text,/Original record kind: observation/);
    assert.ok(!text.includes('Batch:'));
    await app.run('exportPanel()');await app.run('saveExport()');
    assert.match(app.window.document.getElementById('exportStatus').textContent,/Saved to/);
    await app.run('saveRecord()');
    assert.match(app.window.document.getElementById('exportStatus').textContent,/Saved original record/);
    await app.run('saveRecord()');
    assert.equal(app.run('pendingExports.length'),1);
    assert.equal(app.run('pendingExports[0].retry_method'),'retry_record_export');
    const retainedHash=app.run('pendingExports[0].payload_sha256');
    app.run("$('pendingExportList').querySelector('button').click()");
    for(let attempt=0;attempt<150&&app.run('pendingExports.length');attempt++)await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(app.run('pendingExports.length'),0);
    assert.ok(app.messages.some(message=>message.method==='retry_record_export'));
    const receipt=await peer.send({id:'native-readback',method:'test_native_export_receipt',params:{}});
    assert.equal(receipt.ok,true);
    const {createHash}=await import('node:crypto');
    assert.equal(createHash('sha256').update(receipt.value.payload).digest('hex'),retainedHash);
    assert.deepEqual(JSON.parse(receipt.value.payload),native);
    await app.run("switchDataset('activate_live')");
    assert.equal(app.run('datasetBinding.kind'),'live');
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),live);
    assert.equal(app.run('selectedComparison().reference'),null);
  }finally{if(app){for(let attempt=0;attempt<150&&app.run('annotationLoads.size');attempt++)await new Promise(resolve=>setTimeout(resolve,20));app.close();}await peer.close();}
});


test('direct setup holds native port intent and reconciles one initialization without repeating selection',{timeout:12000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    const catalog=await peer.send({id:'direct-catalog',method:'equipment_setup',params:{}});
    assert.equal(catalog.ok,true);
    app=await workspace();
    const original=app.window.ownerTransport.postMessage;
    const calls=[],outcomes=new Map();
    let directState={generation:0,listing_generation:0,state:'unselected',reason:null,report:null,catalog:null,candidates:[],bindings:[],direct_serial:catalog.value.direct_serial};
    let chooseCount=0,initializeCount=0,drop=true,delayedReject=null;
    app.window.ownerTransport.postMessage=message=>{
      if(!['equipment_setup','choose_direct_port','connect_direct_serial','reopen_direct_serial','equipment_outcome','close_device'].includes(message.method))return original(message);
      calls.push(copy(message));let value;
      if(message.method==='equipment_setup')value=directState;
      else if(message.method==='choose_direct_port'){chooseCount++;value={port_token:'native-held-port',label:'Chosen serial connection',setup_generation:directState.generation};}
      else if(message.method==='equipment_outcome')value=outcomes.get(message.params.command_id);
      else if(message.method==='connect_direct_serial'){
        initializeCount++;
        const result={command_id:message.params.command_id,state:'completed',result:{selection_id:'direct-selection'}};
        outcomes.set(message.params.command_id,result);
        directState={...directState,generation:1,state:'registered',bindings:[{binding_id:'direct-binding',generation:1,backend:'direct_serial',model:'CS-2000',model_enum:'CS-2000',serial:'recorded-serial',device_id:'direct-device',state:'active',connection_available:true}]};
        if(drop){drop=false;delayedReject=()=>app.run("[...pending.values()].at(-1).reject(Object.assign(Error('Delayed initialization reply'),{ownerReplyDelayed:true}))");return;}
        value=result;
      }else if(message.method==='close_device'){
        directState={...directState,generation:2,bindings:directState.bindings.map(binding=>({...binding,state:'closed',generation:2,connection_available:false}))};
        value={command_id:message.params.command_id,state:'completed',result:{}};
      }else value={command_id:message.params.command_id,state:'completed',result:{}};
      app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:true,value:copy(value)}));
    };
    await app.run('equipmentPanel.open()');
    const node=id=>app.window.document.querySelector('[data-equipment="'+id+'"]');
    node('route').value='direct';node('route').dispatchEvent(new app.window.Event('change'));
    assert.equal(node('argyll').hidden,true);assert.equal(node('direct').hidden,false);
    assert.match(node('status').textContent,/Choose a model and port/);
    node('route').value='argyll';node('route').dispatchEvent(new app.window.Event('change'));
    assert.match(node('status').textContent,/Instrument software is unavailable/);
    node('route').value='direct';node('route').dispatchEvent(new app.window.Event('change'));
    node('directModel').value='CS-2000';node('directModel').dispatchEvent(new app.window.Event('change'));
    assert.match(node('directEffects').textContent,/Turn backlight off/);assert.match(node('directEffects').textContent,/Disable instrument measurement switch/);
    assert.equal(node('directConnect').disabled,true);
    await node('directPort').onclick();assert.equal(chooseCount,1);
    node('directAccept').checked=true;node('directAccept').dispatchEvent(new app.window.Event('input'));
    assert.equal(node('directConnect').disabled,false);
    const initializing=node('directConnect').onclick();
    assert.equal(initializeCount,1);delayedReject();await initializing;
    assert.ok(app.run('equipmentPanel.unresolved'));assert.equal(app.run('equipmentEffectPending()'),true);
    const intent=calls.find(message=>message.method==='connect_direct_serial').params;
    assert.equal(intent.port_token,'native-held-port');assert.equal(intent.model,'CS-2000');
    assert.equal(Object.hasOwn(intent,'port'),false);
    await node('reconcile').onclick();
    const reconciliation=calls.findLast(message=>message.method==='equipment_outcome');
    assert.deepEqual(reconciliation.params,{command_id:intent.command_id});
    assert.equal(chooseCount,1);assert.equal(initializeCount,1);assert.equal(app.run('equipmentPanel.unresolved'),null);
    let reopen=node('bindings').querySelector('[data-binding-action="reopen"]');
    assert.equal(reopen.disabled,true,'An active direct connection must close before reopening');
    directState={...directState,bindings:directState.bindings.map(binding=>({...binding,connection_available:false}))};
    app.window.directSnapshot=copy(directState);app.run('equipmentPanel.accept(window.directSnapshot)');
    assert.match(node('bindings').textContent,/Connection unavailable/);
    assert.equal(node('bindings').querySelector('[data-binding-action="use"]').disabled,true);
    await node('bindings').querySelector('[data-binding-action="close"]').onclick();
    reopen=node('bindings').querySelector('[data-binding-action="reopen"]');assert.equal(reopen.disabled,false);
    reopen.click();assert.equal(node('directSerial').disabled,true);assert.equal(node('directSerial').value,'recorded-serial');
    assert.equal(node('directConnect').disabled,true,'Reopen requires another deliberate native port selection');
  }finally{if(app){await app.run('writes');app.close();}await peer.close();}
});

test('actual CR-250 Demo persona measures through Python and renders its retained native quantities',{timeout:20000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));
    const settings=await app.run("call('settings',{selection_id:null})");
    const selection=settings.selections.find(item=>item.synthetic&&item.device.model.includes('CR-250'));
    assert.ok(selection,'The actual settings owner offers its CR-250 Demo source');
    app.window.crDemoSelection=selection.selection_id;await app.run('setup(true,window.crDemoSelection)');
    assert.ok(app.run('setupDescriptor.synthetic'));assert.match(app.run('setupDescriptor.device.model'),/CR-250/);
    app.window.document.getElementById('batchName').value='CR-250 Demo renderer control';await app.run('createBatch()');await app.run('writes');
    await app.run("begin('Measure')");
    const deadline=Date.now()+12000;
    while(true){await app.run('refresh(true)');if(app.run("ownerState.phase==='idle'&&ownerState.operations.length>0"))break;if(Date.now()>deadline)throw Error('CR Demo acquisition did not settle');await new Promise(resolve=>setTimeout(resolve,20));}
    assert.equal(app.run('ownerState.operations.at(-1).status'),'completed');assert.equal(app.run('ownerState.operations.at(-1).accepted'),true);
    assert.equal(app.run('state.rows.length'),1);assert.equal(app.window.document.querySelectorAll('#rows > tr').length,1);
    const observation=copy(app.run('state.rows[0].obs'));assert.match(observation.device.model,/CR-250/);assert.ok(observation.backend_reported.xyz);assert.ok(observation.backend_reported.spectrum);
    assert.equal(observation.backend_reported.xyz.values.length,3);assert.ok(observation.backend_reported.spectrum.values.length>1);
    assert.equal(app.window.document.querySelectorAll('#spectrumPlot .curve').length,1);
    app.run('state.selected=state.rows[0];renderReadingMetadata(state.selected)');
    // The raw JSON details also contain the warning: read the curated row itself.
    const warningTerm=[...app.window.document.querySelectorAll('#detailMetadata dt')].find(node=>node.textContent==='Reading warnings');
    assert.ok(warningTerm,'Producer warnings stay beside the values they qualify');
    assert.match(warningTerm.nextElementSibling.textContent,/^SIMULATED: these values did not come from a physical measurement/);
    assert.equal(app.run('ownerState.draft.name'),'Sample #02');assert.equal(app.run('ownerState.saved_count'),1);
    assert.equal(app.messages.some(message=>['connect_direct_serial','refresh_devices','connect_device','choose_direct_port'].includes(message.method)),false,'This is a Demo selection, with no equipment action');
  }finally{if(app){await app.run('writes');app.close();}await peer.close();}
});

for(const loseReply of [false,true])test('actual direct setup '+(loseReply?'recovers a lost reply and reload, then cancels its original UUID':'keeps progress through navigation until the real initialization completes'),{timeout:20000},async()=>{
  const peer=pythonOwnerPeer();let app,heldReply;
  const calls=[];
  const serve=async message=>{calls.push(copy(message));const reply=await peer.send(message);if(loseReply&&message.method==='connect_direct_serial')return new Promise(resolve=>{heldReply={message,reply,resolve};});return reply;};
  try{
    app=await workspace(serve);
    await app.run("setup(true,null,false,true)");app.window.document.getElementById('batchName').value='Pending equipment control';await app.run('createBatch()');await app.run('writes');
    await app.run("showPanel('equipmentDialog');equipmentPanel.open()");
    let node=id=>app.window.document.querySelector('[data-equipment="'+id+'"]');
    node('route').value='direct';node('route').dispatchEvent(new app.window.Event('change'));
    node('directModel').value='CR-100';node('directModel').dispatchEvent(new app.window.Event('change'));
    await node('directPort').onclick();node('directAccept').checked=true;node('directAccept').dispatchEvent(new app.window.Event('input'));
    const connecting=node('directConnect').onclick();
    if(loseReply){
      const deadline=Date.now()+5000;while(!heldReply){if(Date.now()>deadline)throw Error('The actual initialization reply was not held');await new Promise(resolve=>setTimeout(resolve,10));}
      app.window.lostRequestId=heldReply.message.id;
      app.run("{const waiter=pending.get(window.lostRequestId);clearTimeout(waiter.timer);pending.delete(window.lostRequestId);waiter.reject(Object.assign(Error('Setup reply lost in this control'),{ownerReplyDelayed:true}));}");
    }
    await connecting;await app.run('refresh(true)');
    const intent=calls.find(message=>message.method==='connect_direct_serial').params;
    assert.equal(app.run('equipmentPanel.state.active_setup.command_id'),intent.command_id);
    assert.equal(app.run('equipmentPanel.state.active_setup.stage'),'initialization');
    assert.match(node('progress').textContent,/Initializing instrument/);assert.equal(node('error').hidden,true,'A confirmed pending outcome is progress, including after a lost reply');
    assert.equal(app.run('equipmentEffectPending()'),true);assert.equal(app.window.document.getElementById('start').disabled,true);
    assert.equal(node('directConnect').disabled,true);assert.equal(node('cancelSetup').disabled,false);
    if(loseReply){
      heldReply.resolve(heldReply.reply);heldReply=null;app.close();app=null;
      app=await workspace(async message=>{calls.push(copy(message));return peer.send(message);});
      node=id=>app.window.document.querySelector('[data-equipment="'+id+'"]');
      assert.equal(app.run('activePanel'),'equipmentDialog','Reload restores pending setup controls without another initialization');
      assert.equal(app.run('equipmentPanel.state.active_setup.command_id'),intent.command_id);
      await node('cancelSetup').onclick();await app.run('refresh(true)');
      assert.deepEqual(calls.findLast(message=>message.method==='cancel_setup').params,{command_id:intent.command_id});
      assert.match(node('progress').textContent,/Cancellation requested.*Waiting/);assert.equal(node('cancelSetup').disabled,true);
      assert.equal(app.run('equipmentEffectPending()'),true,'Requesting cancel is not terminal confirmation');
    }else{
      app.run("closePanel('equipmentDialog')");await app.run('refresh(true)');
      assert.equal(app.run('equipmentEffectPending()'),true,'Closing the panel does not release the measurement fence');
    }
    const finished=await peer.send({id:'release-direct',method:'test_release_direct_setup',params:{}});assert.equal(finished.ok,true);
    assert.equal(finished.value.state,loseReply?'failed':'completed');
    await app.run('refresh(true)');assert.equal(app.run('equipmentEffectPending()'),false);
    assert.equal(calls.filter(message=>message.method==='connect_direct_serial').length,1);
    assert.equal(calls.filter(message=>message.method==='choose_direct_port').length,1);
    const outcomes=calls.filter(message=>message.method==='equipment_outcome');assert.ok(outcomes.length);assert.ok(outcomes.every(message=>message.params.command_id===intent.command_id));
    if(loseReply){
      assert.equal(finished.value.result.native_receipt.cleanup.port_closed,true);assert.equal(node('error').hidden,false);assert.equal(node('error').textContent,finished.value.reason);
      const receipt=await peer.send({id:'direct-receipt',method:'test_direct_setup_receipt',params:{}});assert.equal(receipt.value.port_closed,true);
    }else{await app.run('equipmentPanel.open()');assert.match(node('bindings').textContent,/Ready/);assert.equal(node('error').hidden,true);}
    assert.equal(app.run('ownerState.sample_count'),0,'Setup neither acquires nor changes the existing Demo batch');
  }finally{if(heldReply)heldReply.resolve(heldReply.reply);if(app){await app.run('writes');app.close();}await peer.close();}
});

test('scientific operator forms use actual Python owners and preserve native readings',{timeout:60000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));await app.run('setup(true,null,false,true)');
    await app.run("$('batchName').value='Scientific form proof';createBatch()");await app.run('writes');
    const settle=async count=>{const end=Date.now()+10000;while(Date.now()<end){await app.run('refresh(true)');if(app.run(`ownerState.sample_count===${count}&&ownerState.phase==='idle'`))return;await new Promise(resolve=>setTimeout(resolve,25));}throw Error('Demo measurement did not settle');};
    await app.run("begin('Measure')");await settle(1);await app.run("begin('Measure')");await settle(2);
    await app.run("call('preload_demo',{batch_id:ownerState.batch_id,generation:ownerState.generation,count:40})");await app.run('refresh(true)');
    assert.equal(app.run('state.rows.length'),42);
    const native=JSON.stringify(app.run('state.rows.map(row=>row.obs)'));
    app.window.document.getElementById('scientificCalculations').click();
    const n=id=>app.window.document.querySelector('[data-science="'+id+'"]');
    const set=(id,value)=>{n(id).value=value;n(id).dispatchEvent(new app.window.Event('input',{bubbles:true}));};
    set('sourceWhite','reading:'+app.run('state.rows[0].id'));set('destinationWhite','standard:D50');set('destinationY','100');
    await n('calculate').onclick();
    assert.equal(n('error').hidden,true,n('error').textContent);assert.match(n('results').textContent,/Derived XYZ/);assert.match(n('results').textContent,/Derived Lab/);assert.doesNotMatch(n('results').textContent,/#0 ·/);assert.match(n('results').textContent,/#1/);assert.doesNotMatch(n('reference').textContent,/#0 ·/);
    app.window.document.getElementById('dismissScience').click();await app.run('exportPanel()');
    assert.equal(app.window.document.getElementById('exportDerivationLabel').hidden,false);
    app.window.document.getElementById('exportDerivation').checked=true;await app.run('saveExport()');
    assert.equal(app.messages.findLast(message=>message.method==='export_analysis').params.derivation.method,'adapt_xyz');
    assert.match(app.window.document.getElementById('exportStatus').textContent,/Saved to/);
    app.window.document.getElementById('scientificCalculations').click();
    set('method','spectral_xyz');n('method').dispatchEvent(new app.window.Event('change'));
    n('air').checked=true;n('emission').checked=true;
    await n('calculate').onclick();assert.equal(n('error').hidden,true,n('error').textContent);
    assert.match(n('results').textContent,/Derived XYZ/);
    set('method','characterized_reflectance');n('method').dispatchEvent(new app.window.Event('change'));
    set('reference',app.run('state.rows[0].id'));set('setup','Same simulated emitting setup');n('matched').checked=true;n('nonfluorescent').checked=true;n('idealWhite').checked=true;
    await n('calculate').onclick();assert.equal(n('error').hidden,true,n('error').textContent);assert.match(n('results').textContent,/Derived reflectance/);
    set('method','relight_reflectance');n('method').dispatchEvent(new app.window.Event('change'));set('basis','characterized-reference');set('illuminant','D65');
    await n('calculate').onclick();assert.equal(n('error').hidden,true,n('error').textContent);assert.match(n('results').textContent,/Derived XYZ/);
    const request=app.messages.findLast(message=>message.method==='derive').params.derivation;
    assert.equal(request.context.illuminant,'D65');assert.equal(request.context.illuminant_id,null);assert.equal(request.context.reflectance.context.assume_ideal_white,true);
    assert.equal(JSON.stringify(app.run('state.rows.map(row=>row.obs)')),native);
  }finally{if(app){await app.run('writes');app.close();}await peer.close();}
});

test('scientific forms reject absent declarations and ignore superseded calculations',async()=>{
  const app=await workspace();
  try{
    app.window.document.getElementById('scientificCalculations').click();
    const n=id=>app.window.document.querySelector('[data-science="'+id+'"]');
    await n('calculate').onclick();assert.equal(n('error').hidden,false);assert.match(n('error').textContent,/white/);
    assert.equal(app.messages.filter(message=>message.method==='derive').length,0);
    n('method').value='spectral_xyz';n('method').dispatchEvent(new app.window.Event('change'));
    assert.equal(n('error').hidden,true);assert.equal(n('error').textContent,'');
    await n('calculate').onclick();assert.equal(n('error').hidden,false);assert.match(n('error').textContent,/standard-air/);
    assert.equal(app.messages.filter(message=>message.method==='derive').length,0);
    n('method').value='adapt_xyz';n('method').dispatchEvent(new app.window.Event('change'));
    assert.equal(n('error').hidden,true);assert.equal(n('error').textContent,'');
    n('sourceWhite').value='reading:A';n('destinationWhite').value='standard:D65';n('destinationY').value='100';
    const original=app.window.ownerTransport.postMessage;let held;const intercepted=[];
    app.window.ownerTransport.postMessage=message=>{if(message.method==='derive'){held=message;intercepted.push(message);}else original(message);};
    const calculating=n('calculate').onclick();assert.ok(held);assert.match(n('status').textContent,/Calculating/);
    app.run("state.plotted=new Set(['A']);render()");
    assert.match(n('status').textContent,/Inputs changed/);
    app.window.ownerReply({id:held.id,ok:true,value:{...held.params,method:'adapt_xyz',by_source:{A:{result:{values:[99,99,99]},reason:null}}}});
    await calculating;assert.equal(n('results').textContent,'');assert.equal(app.run('sciencePanel.exportRequest()'),null);
    n('method').value='relight_reflectance';n('method').dispatchEvent(new app.window.Event('change'));n('air').checked=true;
    await n('calculate').onclick();assert.match(n('error').textContent,/nonfluorescent/);
    assert.equal(intercepted.length,1);
  }finally{await app.run('writes');app.close();}
});

test('diagnostic and measured-reference forms use fenced owner commands',{timeout:30000},async()=>{
  const peer=pythonOwnerPeer();let app;
  try{
    app=await workspace(message=>peer.send(message));await app.run('setup(true,null,false,true)');
    await app.run("$('mode').value='reflective_spot';renderSetupOptions();$('batchName').value='Operator policy proof';createBatch()");await app.run('writes');
    const document=app.window.document;
    document.getElementById('nativeDiagnostics').click();
    assert.equal(document.querySelector('#diagnosticLevel [value="1"]').disabled,true);
    document.getElementById('diagnosticLimit').value='';await document.getElementById('applyDiagnostics').onclick();
    assert.match(document.getElementById('diagnosticsError').textContent,/whole-number/);
    document.getElementById('diagnosticLimit').value='4096';await document.getElementById('applyDiagnostics').onclick();await app.run('writes');
    assert.match(document.getElementById('diagnosticsStatus').textContent,/Diagnostics off/);
    assert.deepEqual(app.messages.findLast(message=>message.params.action==='configure_native_diagnostics').params.payload,{debug_level:0,limit_bytes:4096});
    document.getElementById('dismissDiagnostics').click();document.getElementById('setupChart').click();document.getElementById('previewChart').click();await app.run('writes');document.getElementById('configureChart').click();await app.run('writes');
    await app.run("begin('Measure')");
    const end=Date.now()+10000;while(Date.now()<end){await app.run('refresh(true)');if(app.run("ownerState.sample_count===1&&ownerState.phase==='idle'"))break;await new Promise(resolve=>setTimeout(resolve,25));}
    assert.equal(app.run('ownerState.sample_count'),1);
    await document.getElementById('chartReference').onclick();await document.getElementById('applyPositionReference').onclick();
    assert.match(document.getElementById('positionReferenceError').textContent,/Complete every/);
    assert.equal(app.messages.filter(message=>message.params.action==='chart_reference').length,0);
    for(const id of ['positionSpecimen','positionEdition','positionGeometry','positionBacking','positionLighting','positionDeclarer','positionBasis','positionCoverage'])document.getElementById(id).value='Explicit simulated declaration';
    document.getElementById('positionSourcePass').selectedIndex=1;document.getElementById('positionMatched').checked=true;
    document.getElementById('positionAllowance').value='2';document.getElementById('positionReferenceBound').value='1';document.getElementById('positionMeasurementBound').value='1';
    const pass=app.run('ownerState.chart.active_pass');
    assert.equal(document.getElementById('applyPositionReference').disabled,true);
    await document.getElementById('positionStartPass').onclick();await app.run('writes');
    assert.equal(document.getElementById('applyPositionReference').disabled,false);
    await document.getElementById('applyPositionReference').onclick();await app.run('writes');
    assert.equal(document.getElementById('positionReferenceError').hidden,true,document.getElementById('positionReferenceError').textContent);
    assert.notEqual(app.run('ownerState.chart.active_pass'),pass);assert.equal(app.run('ownerState.chart.coverage'),0);
    const payload=app.messages.findLast(message=>message.params.action==='chart_reference').params.payload;
    assert.equal(payload.source_pass_id,pass);assert.equal(payload.criteria.maximum_delta_e,2);assert.equal(payload.qualification.matched_conditions_confirmed,true);
    await document.getElementById('chartReference').onclick();await app.run("chartEdit('rotate',{degrees:90})");await app.run('writes');
    assert.equal(document.getElementById('applyPositionReference').disabled,true);
    assert.match(document.getElementById('positionReferenceStatus').textContent,/chart changed/);
  }finally{if(app){await app.run('writes');app.run("chartRequest++;chartColorKey='test-closed';");app.close();}await peer.close();}
});

test('Activity failure retries only on request and scientific input clears an obsolete error',async()=>{
  const app=await workspace();
  try{
    const original=app.window.ownerTransport.postMessage;let requests=0;
    app.window.ownerTransport.postMessage=message=>{
      if(message.method!=='activity')return original(message);
      requests++;app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:false,error:'Activity source refused'}));
    };
    await app.run('openActivity()');
    for(let count=0;count<5;count++)app.run('render()');
    assert.equal(requests,1,'Rendering a failed source does not retry or hide its error');
    const node=id=>app.window.document.querySelector('[data-activity="'+id+'"]');
    assert.equal(node('error').hidden,false);assert.match(node('error').textContent,/source refused/);
    await node('refresh').onclick();assert.equal(requests,2,'Explicit Refresh permits one retry');
    app.run("closePanel('activityDialog')");app.window.ownerTransport.postMessage=original;
    app.window.document.getElementById('scientificCalculations').click();
    const field=id=>app.window.document.querySelector('[data-science="'+id+'"]');
    await field('calculate').onclick();assert.equal(field('error').hidden,false);
    field('destinationY').value='80';field('destinationY').dispatchEvent(new app.window.Event('input',{bubbles:true}));
    assert.equal(field('error').hidden,true,'Input changes invalidate the old result and its old error');
  }finally{app.run('activityPanel.close()');await app.run('writes');app.close();}
});

test('diagnostic failures survive primary save rescue without blocking Measure and clear only with owner state',async()=>{
  const app=await workspace();
  try{
    const document=app.window.document,notice=document.getElementById('nativeDiagnosticNotice');
    app.batch.batch_name='Live diagnostic batch';app.batch.operations.forEach((operation,index)=>operation.command_id='log-'+index);
    app.batch.native_diagnostic_failures={retention:{'log-0':'Capture getter failed <plain reason>'},publication:{'log-0':'Original log publication unconfirmed','log-1':'Second log could not be saved'}};
    app.batch.save_reason='Primary save failed';app.batch.saved_count=1;app.batch.can_measure=false;app.batch.revision++;
    await app.run('refresh(true)');
    assert.equal(notice.hidden,false);assert.match(document.getElementById('nativeDiagnosticSummary').textContent,/2 operations.*Live diagnostic batch/);
    assert.match(document.getElementById('nativeDiagnosticFailures').textContent,/Log capture needs attention.*Operation #1: Capture getter failed <plain reason>.*Log save needs attention/s);
    assert.equal(document.querySelector('#nativeDiagnosticFailures plain'),null,'Original reasons are text, not interpreted HTML');
    const disclosure=notice.querySelector('details');disclosure.open=true;
    const original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>{
      if(message.method!=='another_folder')return original(message);
      app.batch.folder='/synthetic/rescued';app.batch.save_reason=null;app.batch.saved_count=2;app.batch.can_measure=true;app.batch.revision++;
      app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:true,value:{admission:'admitted',state:'completed'}}));
    };
    await app.run('recover(true)');await app.run('writes');
    assert.equal(app.run('ownerState.folder'),'/synthetic/rescued');assert.equal(app.run('ownerState.saved_count'),2);
    assert.equal(document.getElementById('saveState').textContent,'Saved');assert.equal(document.getElementById('recovery').hidden,true);
    assert.equal(document.getElementById('start').disabled,false,'Auxiliary log failure never disables acquisition after primary rescue');
    assert.equal(notice.hidden,false);assert.equal(disclosure.open,true);assert.match(document.getElementById('nativeDiagnosticSummary').textContent,/2 operations/);
    app.batch.native_diagnostic_failures.retention={};app.batch.revision++;await app.run('refresh(true)');
    assert.doesNotMatch(document.getElementById('nativeDiagnosticFailures').textContent,/Log capture needs attention/);
    assert.equal(notice.hidden,false,'Publication failures remain after successful capture');
    app.batch.native_diagnostic_failures.publication={};app.batch.revision++;await app.run('refresh(true)');
    assert.equal(notice.hidden,true);assert.equal(document.getElementById('nativeDiagnosticFailures').textContent,'');
    app.batch.native_diagnostic_failures.retention={'log-0':'Still retained'};app.batch.revision++;await app.run('refresh(true)');
    assert.equal(notice.hidden,false);app.packet.batch=null;await app.run('refresh(true)');assert.equal(notice.hidden,true,'Another owner cannot inherit a cached warning');
    app.window.ownerTransport.postMessage=original;
  }finally{await app.run('writes');app.close();}
});

test('Demo failure exits loading state and tools use one fixed disclosure label',async()=>{
  const app=await workspace();
  try{
    await app.run('writes');app.run('render()');
    const button=app.window.document.getElementById('demoToggle');
    button.click();assert.equal(button.getAttribute('aria-expanded'),'true');app.run('render()');assert.equal(app.window.document.getElementById('demoControls').hidden,false);
    button.click();assert.equal(button.getAttribute('aria-expanded'),'false');
    assert.equal(button.hasAttribute('aria-pressed'),false);assert.equal(button.textContent,'Demo tools');
    const demoChart=app.window.document.getElementById('demoChart');assert.equal(demoChart.disabled,true);
    await demoChart.onclick();assert.match(app.window.document.getElementById('toast').textContent,/Finish this batch/);
    assert.equal(app.messages.some(message=>message.method==='create_batch'),false,'A populated batch cannot be replaced by the Demo entry');
    const original=app.window.ownerTransport.postMessage;
    app.window.ownerTransport.postMessage=message=>message.method==='preload_demo'
      ? app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:false,error:'Controlled Demo refusal'})) : original(message);
    await app.run('preloadDemo(40)');
    assert.match(app.window.document.getElementById('demoLoadStatus').textContent,/stopped.*Controlled Demo refusal/);
    assert.equal(app.run('demoLoading'),false);assert.equal(app.run('demoLoadId'),null);
    app.run("showPanel('setupDialog')");
    app.window.document.body.dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'m',ctrlKey:true,shiftKey:true,bubbles:true}));
    assert.match(app.window.document.getElementById('toast').textContent,/Close the open panel/);
    assert.equal(app.messages.some(message=>message.params.action==='measure'),false);
    app.window.ownerTransport.postMessage=original;
  }finally{await app.run('writes');app.close();}
});

test('Activity follows retained live saved and imported outcomes without borrowing live state',{timeout:60000},async()=>{
  const peer=pythonOwnerPeer();let app,advancedRoster=false,staleDetails=0;const copied=[];
  try{
    app=await workspace(async message=>{
      if(message.method==='copy_summary'){copied.push(message.params.text);return {id:message.id,ok:true,value:{copied:true}};}
      const reply=await peer.send(message);
      if(message.method==='activity'&&!advancedRoster){
        // A real admitted metadata update makes this captured roster stale.
        // Exercise the same revision race as automatic files finishing preload.
        advancedRoster=true;
        const snapshot=await peer.send({id:message.id+'/snapshot',method:'snapshot',params:{}});
        assert.equal(snapshot.ok,true);const batch=snapshot.value.batch,draft=batch.draft;
        const updated=await peer.send({id:message.id+'/advance',method:'command',params:{
          command_id:message.id,batch_id:batch.batch_id,generation:batch.generation,action:'edit_draft',
          payload:{draft_id:draft.id,version:draft.version,name:draft.name,notes:'Activity refresh proof',options:draft.options,number_samples:draft.number_samples},
        }});
        assert.equal(updated.ok,true);assert.equal(updated.value.admission,'admitted');
      }
      if(message.method==='activity_details'&&!reply.ok&&/Activity changed/.test(reply.error))staleDetails++;
      return reply;
    });
    await app.run('setup(true,null,false,true)');await app.run("$('batchName').value='Live Activity proof';createBatch()");await app.run('writes');
    await app.run("call('preload_demo',{batch_id:ownerState.batch_id,generation:ownerState.generation,count:40})");await app.run('refresh(true)');
    const n=id=>app.window.document.querySelector('[data-activity="'+id+'"]');
    // Periodic polling is disabled in workspace(). Optional automatic files can
    // still advance the owner revision between Activity roster and detail reads.
    // Drive the normal refresh path so stale details recover as they do in the app.
    const waitFor=async predicate=>{const end=Date.now()+10000;while(!predicate()){if(Date.now()>end)throw Error(n('error').textContent||'Activity did not settle');await app.run('refresh(true)');await new Promise(resolve=>setTimeout(resolve,10));}};
    await app.run('openActivity()');await waitFor(()=>app.run('!!activityPanel.details'));
    assert.equal(advancedRoster,true);assert.ok(staleDetails>=1,'The real owner must refuse the captured stale roster before refresh recovers');
    assert.equal(app.run('activityPanel.roster.entries.length'),40);assert.equal(app.window.document.querySelectorAll('[data-activity-inspect]').length,40);assert.equal(app.window.document.getElementById('outcomeCount').textContent,'40');
    assert.equal(app.run('activityPanel.selected'),app.run('activityPanel.roster.entries.at(-1).key'));
    const first=app.window.document.querySelector('[data-activity-inspect]');first.focus();first.click();
    await waitFor(()=>app.run('activityPanel.details?.key===activityPanel.roster.entries[0].key'));
    assert.equal(n('follow').checked,false);assert.equal(app.window.document.activeElement.dataset.activityInspect,app.run('activityPanel.selected'));
    n('filter').value='Sample #01';n('filter').dispatchEvent(new app.window.Event('input'));assert.equal(app.window.document.querySelectorAll('[data-activity-inspect]').length,1);
    await n('copy').onclick();assert.match(copied.at(-1),/Live Activity proof/);assert.match(copied.at(-1),/simulated/);assert.match(copied.at(-1),/Reading identity/);
    n('filter').value='';n('filter').dispatchEvent(new app.window.Event('input'));n('follow').checked=true;n('follow').dispatchEvent(new app.window.Event('change'));await waitFor(()=>app.run('activityPanel.details?.key===activityPanel.roster.entries.at(-1).key'));
    const next=app.run('activityPanel.details.readings[0].key');
    app.run("readingFilter='Hidden';$('readingFilter').value='Hidden';$('gamutFilter').value='outside';render()");
    app.window.document.querySelector('[data-activity-jump]').click();assert.equal(app.run('state.selected.id'),next);assert.equal(app.run('readingFilter'),'');assert.equal(app.run("$('gamutFilter').value"),'all');assert.equal(app.window.document.getElementById('activityDialog').hidden,true);
    await app.run("call('open_saved',datasetFence()).then(synchronise)");await app.run('openActivity()');await waitFor(()=>app.run('!!activityPanel.details'));
    assert.equal(app.run('activityPanel.roster.source.kind'),'saved');assert.equal(app.run('activityPanel.roster.entries.length'),40);
    assert.equal(app.run('activityPanel.details.source.kind'),'saved');assert.equal(app.run('activityPanel.details.summary.read_only'),true);
    // Saved sources never persisted application events; the owner's scope is stated, not left blank.
    assert.match(n('details').textContent,/Application step timeline unavailable: application events are not saved with this source/);
    assert.match(app.run('activityPanel.summaryLines().join("\\n")'),/Application step timeline unavailable/);
    for(const variant of [{status:'failed',zero_readings:true,observation_only:false},{status:'cancelled',zero_readings:true,observation_only:false},{status:'completed',zero_readings:false,observation_only:true}]){
      app.window.variant=variant;await app.run("call('test_activity_record',window.variant).then(synchronise)");await app.run('openActivity()');await waitFor(()=>app.run('!!activityPanel.details'));
      const summary=app.run('activityPanel.details.summary');assert.equal(summary.command_id,null);assert.equal(app.run('activityPanel.roster.entries.length'),1);assert.equal(app.run('activityPanel.details.source.kind'),'standalone');
      assert.equal(summary.status,variant.observation_only?null:variant.status);
      assert.equal(app.window.document.querySelectorAll('[data-activity-jump]').length,variant.observation_only?1:0);
      assert.equal(app.run('ownerState.operations.length'),40,'Retained live operations are present but never borrowed by imported Activity');
      if(variant.status==='failed')assert.match(n('details').textContent,/Native failure stage: read_sample · code -1/);
      if(variant.observation_only)assert.match(n('details').textContent,/No terminal outcome/);else assert.match(n('details').textContent,/No available reading/);
      await n('copy').onclick();assert.match(copied.at(-1),/scripted-activity.json/);assert.doesNotMatch(copied.at(-1),/Live Activity proof/);
      assert.match(copied.at(-1),/Application step timeline unavailable/);
    }
  }finally{if(app){await app.run('writes');app.run("activityPanel.close();chartRequest++;chartColorKey='test-closed'");app.close();}await peer.close();}
});

test('Activity progress filters and late source or panel replies cannot restore old details',async()=>{
  const app=await workspace();const original=app.window.ownerTransport.postMessage;const held=[];let rosterCalls=0;
  const entries=[{key:'preparing',kind:'operation',source_ref:{kind:'live'},command_id:'reserved-command',operation_id:'reserved-operation',workflow:'spot',status:'unknown',state:'reserved',reason:null,review_state:null,reading_count:null,reading_refs:[],name:'Next target',provenance:null,saved:false,record_retained:false},{key:'failed',kind:'operation',source_ref:{kind:'live'},command_id:'failed-command',operation_id:'failed-operation',workflow:'spot',status:'failed',state:'accepted',reason:'Original error',review_state:'accepted',reading_count:0,reading_refs:[],name:'Failed sample',provenance:'simulated',saved:true,record_retained:true},{key:'single',kind:'operation',source_ref:{kind:'live'},command_id:'single-command',operation_id:'operation-A',workflow:'spot',status:'completed',state:'accepted',reason:null,review_state:'accepted',reading_count:1,reading_refs:[{operation_id:'operation-A',observation_id:'A'}],name:'Single sample',provenance:'simulated',saved:true,record_retained:true},{key:'excluded',kind:'operation',source_ref:{kind:'saved'},command_id:'excluded-command',operation_id:'excluded-operation',workflow:'spot',status:'failed',state:'accepted',reason:'Long original failure reason. '.repeat(40),review_state:'unknown',review_reason:'Source bytes are not accepted',reading_count:2,reading_refs:[],name:'Excluded sample',provenance:'simulated',saved:false,record_retained:false}];
  const send=(message,value)=>app.window.queueMicrotask(()=>app.window.ownerReply({id:message.id,ok:true,value}));
  app.window.ownerTransport.postMessage=message=>{
    if(message.method==='activity'){rosterCalls++;send(message,{...message.params,value:{activity_revision:'activity/'+rosterCalls,source:{kind:'live'},entries:copy(entries),reason:null}});}
    else if(message.method==='activity_details')held.push(message);else original(message);
  };
  const n=id=>app.window.document.querySelector('[data-activity="'+id+'"]');
  const waitFor=async predicate=>{const end=Date.now()+2000;while(!predicate()){if(Date.now()>end)throw Error('Controlled Activity request did not arrive');await new Promise(resolve=>setTimeout(resolve,10));}};
  const detail=message=>({...message.params,value:{key:message.params.key,activity_revision:message.params.activity_revision,summary:entries.find(entry=>entry.key===message.params.key),source:{kind:'live'},result:null,entry:{},recovery_result:null,readings:[],events:[],native_diagnostics:{metadata:null,reason:null,segments:[]}}});
  try{
    await app.run('openActivity()');await waitFor(()=>held.length===1);
    n('statusFilter').value='in_progress';n('statusFilter').dispatchEvent(new app.window.Event('change'));await waitFor(()=>held.length===2);
    assert.match(n('list').textContent,/Preparing/);assert.match(n('list').textContent,/No terminal result yet/);assert.doesNotMatch(n('list').textContent,/Original error/);
    const first=app.window.document.querySelector('[data-activity-inspect]');first.focus();first.click();assert.equal(n('follow').checked,false);assert.equal(app.window.document.activeElement.dataset.activityInspect,'preparing');
    await waitFor(()=>held.length===3);const current=held.at(-1);send(current,detail(current));await waitFor(()=>!!app.run('activityPanel.details'));
    for(const pending of held.slice(0,-1))send(pending,detail(pending));await new Promise(resolve=>setImmediate(resolve));assert.equal(app.run('activityPanel.details.key'),'preparing');
    assert.match(n('list').textContent,/Reported reading count unavailable/);assert.doesNotMatch(n('list').textContent,/(?:null|0) reported readings/);
    assert.match(n('details').textContent,/Reported reading count unavailable/);assert.match(n('details').textContent,/0 available readings in this table/);
    await app.run('activityPanel.copy()');const missingCopy=app.messages.findLast(message=>message.method==='copy_summary').params.text;
    assert.match(missingCopy,/Reported reading count unavailable/);assert.doesNotMatch(missingCopy,/(?:null|0) reported readings/);
    n('statusFilter').value='attention';n('statusFilter').dispatchEvent(new app.window.Event('change'));
    assert.match(n('list').querySelector('[data-activity-key="failed"]').textContent,/0 reported readings/);
    app.window.document.querySelector('[data-activity-inspect="failed"]').click();await waitFor(()=>held.length===4);send(held.at(-1),detail(held.at(-1)));await waitFor(()=>app.run('activityPanel.details?.key')==='failed');
    assert.match(n('details').textContent,/0 reported readings/);assert.doesNotMatch(n('details').textContent,/Reported reading count unavailable/);
    await app.run('activityPanel.copy()');const zeroCopy=app.messages.findLast(message=>message.method==='copy_summary').params.text;
    assert.match(zeroCopy,/0 reported readings/);assert.match(zeroCopy,/0 available readings in this table/);assert.doesNotMatch(zeroCopy,/Reported reading count unavailable/);
    app.window.document.querySelector('[data-activity-inspect="excluded"]').click();await waitFor(()=>held.length===5);send(held.at(-1),detail(held.at(-1)));await waitFor(()=>app.run('activityPanel.details?.key')==='excluded');
    assert.equal(app.window.document.querySelectorAll('[data-activity-jump]').length,0);
    const summary=app.run('activityPanel.summaryLines().join("\\n")');assert.match(summary,/2 reported readings/);assert.match(summary,/0 available readings/);assert.doesNotMatch(summary,/2 retained readings/);
    assert.match(n('details').textContent,/Complete outcome reason/);assert.match(n('details').textContent,/Source bytes are not accepted/);
    n('statusFilter').value='all';n('statusFilter').dispatchEvent(new app.window.Event('change'));
    app.window.document.querySelector('[data-activity-inspect="single"]').click();await waitFor(()=>held.length===6);
    const single=detail(held.at(-1));single.value.readings=[{key:'A',reading_ref:{operation_id:'operation-A',observation_id:'A'},annotation:{applied:{name:'Sample A',notes:'Retained notes'}},observation:{...copy(app.run('state.rows[0].obs')),provenance:'simulated'},coordinate_reasons:{}}];
    send(held.at(-1),single);await waitFor(()=>app.run('activityPanel.details?.key')==='single');
    assert.match(n('details').textContent,/1 available reading in this table/);assert.doesNotMatch(n('details').textContent,/1 available readings/);
    await app.run('activityPanel.copy()');const singleCopy=app.messages.findLast(message=>message.method==='copy_summary').params.text;
    assert.match(singleCopy,/1 available reading in this table/);assert.doesNotMatch(singleCopy,/1 available readings/);
    app.run("datasetBinding={...datasetBinding,roster_revision:'new-roster'};render()");assert.equal(n('copy').disabled,true,'An old detail cannot copy during roster refresh');assert.equal(n('details').textContent,'');
    await waitFor(()=>held.length===7);const oldSource=held.at(-1);
    app.run("datasetBinding={...datasetBinding,id:'other-source',generation:7,roster_revision:'other-roster'};render()");await waitFor(()=>held.length===8);
    send(oldSource,detail(oldSource));await new Promise(resolve=>setImmediate(resolve));assert.equal(app.run('activityPanel.details'),null);
    app.window.document.getElementById('dismissActivity').click();for(const pending of held.slice(6))send(pending,detail(pending));await new Promise(resolve=>setImmediate(resolve));
    assert.equal(app.run('activityPanel.details'),null);assert.equal(n('copy').disabled,true);
  }finally{app.run('activityPanel.close()');await app.run('writes');app.close();}
});
