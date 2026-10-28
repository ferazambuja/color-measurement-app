import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';
import { DisplayP3ColorSpace, LinearDisplayP3ColorSpace, DisplayP3ColorSpaceImpl, LinearDisplayP3ColorSpaceImpl } from './vendor/ColorSpaces.js';

// Rendering only: Python supplies coordinates, model geometry and encoded colors.
THREE.ColorManagement.define({
  [DisplayP3ColorSpace]: DisplayP3ColorSpaceImpl,
  [LinearDisplayP3ColorSpace]: LinearDisplayP3ColorSpaceImpl,
});
const svgNS = 'http://www.w3.org/2000/svg';
// Both mappings preserve orientation; the Lab swap and b* sign cancel.
const world = (v, lab) => new THREE.Vector3(...(lab ? [v[1], v[0], -v[2]] : [v[0], v[1], v[2]]));
const presets = {oblique: [1.5, 1.1, 1.6], top: [0, 1, 0.0001], front: [0, 0.0001, 1], side: [1, 0.0001, 0]};
const niceStep = span => { const raw = span/4, p = 10**Math.floor(Math.log10(raw || 1)); return [1,2,2.5,5,10].find(n=>n>=raw/p)*p; };

export class ColorSpaceView {
  constructor(container, onInspect, onPreview) {
    this.container = container;
    this.onInspect = onInspect;
    this.onPreview = onPreview;
    this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.domElement.tabIndex = 0;
    this.renderer.domElement.setAttribute('aria-label', '3D color-space view. Drag to orbit, scroll to zoom. Arrow keys rotate. Use camera controls and readings table for keyboard selection.');
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1,1,1,-1,0.01,1000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.minZoom = 0.2;
    this.controls.maxZoom = 12;
    this.cameraPreset = 'oblique';
    this.controls.addEventListener('change', () => this.cameraChanged());
    this.labels = document.createElementNS(svgNS, 'svg');
    this.labels.classList.add('space-labels');
    this.labels.setAttribute('aria-hidden', 'true');
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'space-tooltip';
    this.tooltip.hidden = true;
    container.replaceChildren(this.renderer.domElement, this.labels, this.tooltip);
    this.raycaster = new THREE.Raycaster();
    this.points = [];
    this.axisLabels = [];
    this.axisSegments=[];this.billboards=[];
    this.boundsKey = '';
    this.dataKey = '';
    this.span = 1;
    this.down = null;
    this.contextLost = false;
    const canvas = this.renderer.domElement;
    // Three owns GL loss/restoration. The host redraws current data after its
    // restoration handler has run, reapplying the explicit output color space.
    canvas.addEventListener('webglcontextlost', () => {
      this.contextLost = true;
      this.down = null;
      this.tooltip.hidden = true;
      this.onPreview?.(null);
      this.labels.replaceChildren();
      container.dataset.contextLost = 'true';
      container.dispatchEvent(new CustomEvent('plotcontextchange', {detail: {lost: true}}));
    });
    canvas.addEventListener('webglcontextrestored', () => {
      this.contextLost = false;
      this.dataKey = '';
      container.dataset.contextLost = 'false';
      container.dispatchEvent(new CustomEvent('plotcontextchange', {detail: {lost: false}}));
    });
    this.bindPointerInspection(canvas);
    canvas.addEventListener('keydown', e => {
      const moves = {ArrowLeft:[-.2,0],ArrowRight:[.2,0],ArrowUp:[0,-.15],ArrowDown:[0,.15]};
      if(moves[e.key]) {e.preventDefault();this.nudge(...moves[e.key]);}
    });
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(container);
  }

  bindPointerInspection(canvas) {
    const preview=hit=>{
      this.tooltip.hidden=!hit;this.tooltip.textContent=hit?.userData.label||'';
      canvas.style.cursor=hit?'pointer':'grab';this.onPreview?.(hit?.userData.id||null);
    };
    canvas.addEventListener('pointerdown', e => {
      preview(null);
      if(this.down){this.down.moved=true;return;}
      if(e.button!==0||e.isPrimary===false||e.ctrlKey||e.shiftKey||e.altKey||e.metaKey)return;
      this.down={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false};
    });
    canvas.addEventListener('pointerup', e => {
      if(!this.down)return;
      if(e.pointerId!==this.down.id){this.down.moved=true;return;}
      const down=this.down;this.down=null;
      if (!down.moved && e.button===0 && Math.hypot(e.clientX-down.x,e.clientY-down.y)<5) {
        const hit = this.pick(e);
        if (hit) this.onInspect(hit.userData.id);
      }
    });
    canvas.addEventListener('pointermove', e => {
      if(this.down){if(e.pointerId!==this.down.id||Math.hypot(e.clientX-this.down.x,e.clientY-this.down.y)>=5)this.down.moved=true;return;}
      if(!e.buttons&&e.pointerType!=='touch')preview(this.pick(e));
    });
    canvas.addEventListener('pointerleave', () => {if(this.down)this.down.moved=true;preview(null);});
    canvas.addEventListener('pointercancel', () => {this.down=null;preview(null);});
  }

  setPreview(id) {
    const point=this.points.find(point=>point.userData.id===id);
    this.tooltip.hidden=!point;this.tooltip.textContent=point?.userData.label||'';
    if(this.previewId===id)return;
    this.previewId=id;
    this.points.forEach(item=>item.scale.setScalar(item===point?1.35:1));
    if(this.previewRing){this.previewRing.visible=!!point;if(point)this.previewRing.position.copy(point.position);}
    this.draw();
  }

  pick(event) {
    if(this.contextLost)return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    this.raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,1-(event.clientY-rect.top)/rect.height*2),this.camera);
    // Nearest visible marker wins. Ideal reference wire/surface is see-through.
    return this.raycaster.intersectObjects(this.points,false)[0]?.object || null;
  }

  setOutput(requested) {
    let actual = requested;
    try {
      this.renderer.outputColorSpace = requested === 'display-p3' ? DisplayP3ColorSpace : THREE.SRGBColorSpace;
      if(this.renderer.getContext().drawingBufferColorSpace !== requested) actual = 'srgb';
    } catch {actual = 'srgb';}
    this.renderer.outputColorSpace = actual === 'display-p3' ? DisplayP3ColorSpace : THREE.SRGBColorSpace;
    THREE.ColorManagement.workingColorSpace = actual === 'display-p3' ? LinearDisplayP3ColorSpace : THREE.LinearSRGBColorSpace;
    this.actualTarget = actual;
    this.container.dataset.outputColorSpace = this.renderer.getContext().drawingBufferColorSpace;
    this.container.dataset.previewTarget = requested;
    return actual;
  }

  color(rgb) {
    // Python values are encoded. Decode once with an explicit source space.
    return new THREE.Color().setRGB(...rgb,this.actualTarget === 'display-p3' ? DisplayP3ColorSpace : THREE.SRGBColorSpace);
  }

  clear() {
    const geometries = new Set(), materials = new Set();
    this.scene.traverse(o => {if(o.geometry)geometries.add(o.geometry);if(o.material)materials.add(o.material);});
    geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
    this.scene.clear(); this.points=[]; this.axisLabels=[];this.axisSegments=[];this.billboards=[]; this.tooltip.hidden=true;this.previewRing=null;this.previewId=null;
  }

  line(vectors, color, dashed=false, colors=null) {
    if(colors&&colors.length!==vectors.length)throw Error('Reference wire colors do not match its geometry.');
    const geometry = new THREE.BufferGeometry().setFromPoints(vectors);
    if(colors) {
      geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors.flatMap(rgb=>this.color(rgb).toArray()),3));
    }
    const properties = {color:colors?0xffffff:color,vertexColors:!!colors,transparent:true,opacity:1,depthWrite:false};
    const material = dashed ? new THREE.LineDashedMaterial({...properties,dashSize:this.span/35,gapSize:this.span/55}) : new THREE.LineBasicMaterial(properties);
    const object = new THREE.LineSegments(geometry,material);
    object.computeLineDistances(); object.renderOrder=1; this.scene.add(object);
  }

  update(input) {
    if(this.contextLost)return this.actualTarget;
    const key = JSON.stringify(input);
    if(key === this.dataKey) {this.resize();return this.actualTarget;}
    this.dataKey = '';
    const target = this.setOutput(input.target);
    this.clear();
    const lab = input.lab;
    const vectors = [...input.rows.map(r=>r.values),...input.shells.flatMap(s=>s.points)];
    const low = [0,1,2].map(i=>Math.min(0,...vectors.map(v=>v[i])));
    const high = [0,1,2].map(i=>Math.max(1,...vectors.map(v=>v[i])));
    const box = new THREE.Box3();
    [...vectors,[0,0,0],...low.map((v,i)=>low.map((_,j)=>i===j?v:0)),...high.map((v,i)=>high.map((_,j)=>i===j?v:0))].forEach(v=>box.expandByPoint(world(v,lab)));
    // Frame returned geometry and axes; the context grid can extend to the viewport edges.
    // One camera scale preserves equal units across all three scientific axes.
    const baseAxes=lab?[1,2]:[0,2];
    this.framePoints=[...vectors,[0,0,0],...low.map((v,i)=>low.map((_,j)=>i===j?v:0)),...high.map((v,i)=>high.map((_,j)=>i===j?v:0))];
    this.framePoints=this.framePoints.map(p=>world(p,lab));
    this.span = Math.max(1,box.getSize(new THREE.Vector3()).length());
    const boundsKey = JSON.stringify([lab,low,high,input.whiteKey]);
    if(boundsKey !== this.boundsKey) {
      const hadBounds=!!this.boundsKey;
      const direction=hadBounds?this.camera.position.clone().sub(this.controls.target).normalize():null;
      this.boundsKey=boundsKey; this.center=box.getCenter(new THREE.Vector3());
      this.controls.target.copy(this.center);
      if(hadBounds&&this.cameraPreset==='custom'){
        this.camera.position.copy(this.center).addScaledVector(direction,this.span*3);
        this.camera.lookAt(this.center);this.controls.update();
      }else this.preset(hadBounds?this.cameraPreset:presets[input.cameraPreset]?input.cameraPreset:'oblique');
    }
    const names = lab?['L*','a*','b*']:['X','Y','Z'];
    names.forEach((name,i)=>{
      const a=[0,0,0],b=[0,0,0];a[i]=low[i];b[i]=high[i];
      this.line([world(a,lab),world(b,lab)],0xaaaaaa);
      this.axisSegments[i]=[world(a,lab),world(b,lab)];
      this.axisLabels.push({position:world(b,lab),text:name,axis:i});
      const step=niceStep(high[i]-low[i]);
      for(let value=Math.ceil(low[i]/step)*step;value<=high[i]+step*1e-9;value+=step) {
        const p=[0,0,0];p[i]=value;
        const q=[...p];q[(i+1)%3]+=this.span/90;
        this.line([world(p,lab),world(q,lab)],0x888888);
        this.axisLabels.push({position:world(p,lab),text:Number(value.toPrecision(4)).toString(),tick:true,axis:i});
      }
    });
    // Neutral grid on Y=0 / L*=0, with no independent axis normalization.
    for(const i of baseAxes) {
      const j=baseAxes.find(n=>n!==i),step=niceStep(high[i]-low[i]);
      for(let value=Math.ceil(low[i]/step)*step;value<=high[i]+step*1e-9;value+=step) {
        const a=[0,0,0],b=[0,0,0];a[i]=b[i]=value;a[j]=low[j];b[j]=high[j];
        this.line([world(a,lab),world(b,lab)],0x383838);
      }
    }
    let surfaceCount=0;
    input.shells.forEach(shell=>{
      if(!shell.points.length)return;
      const vertices=shell.lines.flat(),wireColors=shell.colors[target];
      this.line(vertices.map(index=>world(shell.points[index],lab)),shell.primary?0xdddddd:0x888888,!shell.primary,wireColors?vertices.map(index=>wireColors[index]):null);
      if(!input.surface || !shell.primary || !shell.colors[target])return;
      const positions=shell.points.flatMap(v=>world(v,lab).toArray());
      const colors=shell.colors[target].flatMap(rgb=>this.color(rgb).toArray());
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
      geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
      geometry.setIndex(shell.triangles.flat());
      const material=new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,transparent:true,opacity:.5,depthWrite:false});
      const mesh=new THREE.Mesh(geometry,material);mesh.renderOrder=0;this.scene.add(mesh);surfaceCount++;
    });
    const radius=this.span/115, sphere=new THREE.SphereGeometry(radius,12,8);
    this.previewRing=new THREE.Mesh(new THREE.TorusGeometry(radius*2,radius*.12,6,32),new THREE.MeshBasicMaterial({color:0xcccccc,transparent:true,depthWrite:false}));
    this.previewRing.visible=false;this.previewRing.renderOrder=12;this.scene.add(this.previewRing);this.billboards.push(this.previewRing);
    input.rows.forEach(row=>{
      const rgb=row.colors[target];
      const material=new THREE.MeshBasicMaterial({color:rgb?this.color(rgb):0x888888,transparent:true,opacity:1,depthWrite:true});
      const point=new THREE.Mesh(sphere,material);point.position.copy(world(row.values,lab));point.renderOrder=10;
      point.userData={id:row.id,label:row.label};this.scene.add(point);this.points.push(point);
      if(row.outside) {
        const r=radius*2.1;
        const diamond=new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-r,0,0),new THREE.Vector3(0,r,0),new THREE.Vector3(r,0,0),new THREE.Vector3(0,-r,0)]),new THREE.LineBasicMaterial({color:0xffffff,transparent:true,depthWrite:false}));
        diamond.position.copy(point.position);diamond.renderOrder=11;this.scene.add(diamond);this.billboards.push(diamond);
      }
      if(row.selected) {
        const ring=new THREE.Mesh(new THREE.TorusGeometry(radius*1.5,radius*.12,6,32),new THREE.MeshBasicMaterial({color:0xffffff,transparent:true,depthWrite:false}));
        ring.position.copy(point.position);ring.renderOrder=11;this.scene.add(ring);this.billboards.push(ring);
        this.axisLabels.push({position:point.position.clone(),text:'#'+row.number,selected:true});
      }
    });
    this.container.dataset.camera='orthographic';this.container.dataset.axisScale='equal';
    this.container.dataset.pointCount=String(input.rows.length);
    this.container.dataset.referenceMode=surfaceCount?'translucent ideal surface':'colored outline';
    this.resize(); this.dataKey=key; return target;
  }

  preset(name) {
    if(!presets[name])return;
    this.cameraPreset=name;
    const direction=new THREE.Vector3(...presets[name]).normalize();
    this.camera.position.copy(this.controls.target).addScaledVector(direction,this.span*3);
    this.camera.up.set(0,1,0);this.camera.lookAt(this.controls.target);
    this.controls.update();this.resize();
    this.markCamera(name);
  }
  markCamera(name) {this.cameraPreset=name;this.container.dispatchEvent(new CustomEvent('plotcamerachange',{detail:{preset:name}}));}
  cameraChanged() {
    if(this.cameraPreset!=='custom'){
      const direction=this.camera.position.clone().sub(this.controls.target).normalize();
      if(direction.distanceTo(new THREE.Vector3(...presets[this.cameraPreset]).normalize())>1e-7)this.markCamera('custom');
    }
    this.resize();
  }
  nudge(theta, phi) {
    const spherical=new THREE.Spherical().setFromVector3(this.camera.position.clone().sub(this.controls.target));
    spherical.theta+=theta;spherical.phi=Math.max(.001,Math.min(Math.PI-.001,spherical.phi+phi));
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(spherical));this.controls.update();this.markCamera('custom');this.resize();
  }
  zoom(factor) {this.camera.zoom=Math.max(.2,Math.min(12,this.camera.zoom*factor));this.camera.updateProjectionMatrix();this.draw();}
  reset() {this.camera.zoom=1;this.controls.target.copy(this.center);this.preset('oblique');this.resize();}
  resize() {
    if(this.contextLost)return;
    const width=this.container.clientWidth,height=this.container.clientHeight;
    if(!width || !height)return;
    this.renderer.setSize(width,height,false);
    const aspect=width/height;
    const inverse=this.camera.quaternion.clone().invert();
    const extents=(this.framePoints||[]).map(p=>p.clone().sub(this.center).applyQuaternion(inverse));
    const xs=extents.map(p=>p.x),ys=extents.map(p=>p.y);
    const left=xs.length?Math.min(...xs):0,right=xs.length?Math.max(...xs):0;
    const bottom=ys.length?Math.min(...ys):0,top=ys.length?Math.max(...ys):0;
    const x=Math.max(this.span/100,(right-left)/2),y=Math.max(this.span/100,(top-bottom)/2);
    const centerX=(right+left)/2,centerY=(top+bottom)/2;
    // Pixel margins keep markers, labels and the compact white key in view.
    const verticalMargin=Math.min(80,height*.3),horizontalMargin=Math.min(64,width*.2);
    const half=Math.max(y*height/(height-verticalMargin),x*width/(width-horizontalMargin)/aspect);
    this.camera.left=centerX-half*aspect;this.camera.right=centerX+half*aspect;this.camera.top=centerY+half;this.camera.bottom=centerY-half;
    this.camera.near=this.span/1000;this.camera.far=this.span*15;this.camera.updateProjectionMatrix();this.draw();
  }
  draw() {
    if(this.contextLost || !this.container.clientWidth || !this.container.clientHeight)return;
    this.billboards.forEach(o=>o.quaternion.copy(this.camera.quaternion));
    this.renderer.render(this.scene,this.camera);
    const width=this.container.clientWidth,height=this.container.clientHeight;
    this.labels.setAttribute('viewBox',`0 0 ${width} ${height}`);this.labels.replaceChildren();
    const collapsed=new Set(this.axisSegments.flatMap(([a,b],i)=>{
      const p=a.clone().project(this.camera),q=b.clone().project(this.camera);
      return Math.hypot((p.x-q.x)*width/2,(p.y-q.y)*height/2)<36?[i]:[];
    }));
    const occupied=[];
    // Inspection and axis names stay ahead of optional ticks in tight views.
    const priority=label=>label.selected?0:label.tick?2:1;
    [...this.axisLabels].sort((a,b)=>priority(a)-priority(b)).forEach(label=>{
      if(collapsed.has(label.axis))return;
      const p=label.position.clone().project(this.camera);
      if(Math.abs(p.x)>1 || Math.abs(p.y)>1 || Math.abs(p.z)>1)return;
      const text=document.createElementNS(svgNS,'text');
      text.setAttribute('x',String((p.x+1)*width/2+7));text.setAttribute('y',String((1-p.y)*height/2+(label.tick?14:-7)));
      text.textContent=label.text;if(label.selected)text.classList.add('selected-label');this.labels.append(text);
      const box=text.getBBox();
      if(occupied.some(other=>box.x<other.x+other.width+4&&box.x+box.width+4>other.x&&box.y<other.y+other.height+4&&box.y+box.height+4>other.y))text.remove();
      else occupied.push(box);
    });
  }
}
