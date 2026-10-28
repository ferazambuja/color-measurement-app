// Software presentation completion uses the page's monotonic clock, not optical onset.
export function drawPresentation(surface, pending, {signal}) {
  return new Promise((resolve, reject) => {
    const view=surface.ownerDocument.defaultView;
    let frame=null,timer=null,settled=false;
    const finish=(error,stamp)=>{
      if(settled)return;settled=true;
      if(frame!==null)view.cancelAnimationFrame(frame);
      view.clearTimeout(timer);signal.removeEventListener('abort',abort);
      if(error){surface.hidden=true;reject(error);}else resolve(stamp);
    };
    const abort=()=>finish(Error('Presentation cancelled before software draw completion.'));
    if(signal.aborted){abort();return;}
    if(!['black','white'].includes(pending.phase)||!Array.isArray(pending.rgb)||
       pending.rgb.length!==3||pending.rgb.some(value=>value!==(pending.phase==='white'?1:0))||
       !Number.isFinite(pending.deadline_ms)||pending.deadline_ms<=0||pending.minimum_delay_ms!==0||
       typeof pending.stimulus_id!=='string'||typeof pending.display_id!=='string'){
      finish(Error('Controlled presentation fields are unavailable or inconsistent.'));return;
    }
    signal.addEventListener('abort',abort,{once:true});
    if(surface.ownerDocument.visibilityState==='hidden'){abort();return;}
    timer=view.setTimeout(()=>finish(Error('Software draw completion exceeded the presentation deadline.')),pending.deadline_ms);
    surface.style.backgroundColor=pending.phase==='white'?'rgb(255, 255, 255)':'rgb(0, 0, 0)';
    surface.hidden=false;surface.querySelector('button')?.focus();
    // A frame applies the surface; the following callback observes that completed
    // browser rendering turn. This is a software receipt, not display/optical proof.
    frame=view.requestAnimationFrame(()=>{
      frame=view.requestAnimationFrame(()=>{
        if(signal.aborted||surface.ownerDocument.visibilityState==='hidden'){abort();return;}
        finish(null,{phase:pending.phase,stimulus_id:pending.stimulus_id,
          display_id:pending.display_id,presented_at_seconds:view.performance.now()/1000,
          source:'client_draw_completed'});
      });
    });
  });
}
