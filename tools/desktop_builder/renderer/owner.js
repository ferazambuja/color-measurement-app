// Request IDs identify bridge returns. Mutation UUIDs identify owner effects.
const pending = new Map();
window.ownerReply = reply => {
  const waiter = pending.get(reply.id);
  if (!waiter) return;
  pending.delete(reply.id); clearTimeout(waiter.timer);
  reply.ok ? waiter.resolve(reply.value) : waiter.reject(Error(reply.error));
};
export async function call(method, params = {}) {
  if (window.ownerTransport && !window.ownerTransport.isReady) {
    let timer;
    try {
      await Promise.race([window.ownerTransport.ready, new Promise((_, reject) => {
        timer = setTimeout(() => reject(Error('The native host did not become ready. Reload the workspace.')), 10000);
      })]);
    } finally { clearTimeout(timer); }
  }
  return new Promise((resolve, reject) => {
    if (!window.ownerTransport) {
      reject(Error('Open this workspace in the installed native host.')); return;
    }
    const id = crypto.randomUUID();
    const interactive=['create_batch','another_folder','review','preload_demo','open_saved','open_record','export_record','retry_record_export','export_analysis','export_batch_mat','retry_analysis_export','stage_chart_target','choose_chart_source','choose_direct_port','choose_science_material','choose_build_report','choose_correction','choose_current_correction'].includes(method);
    const timer = interactive ? null : setTimeout(() => {
      pending.delete(id);const error=Error('The action has not been confirmed. Use Reload or Close to check its result before making another change.');error.ownerReplyDelayed=true;reject(error);
    }, 30000);
    pending.set(id, {resolve, reject, timer});
    window.ownerTransport.postMessage({id, method, params});
  });
}

export async function command(snapshot, action, payload) {
  const params = {command_id:crypto.randomUUID(), generation:snapshot.generation,
    batch_id:snapshot.batch_id, action, payload};
  try { return await call('command', params); }
  catch (error) {
    // Re-send the identical canonical UUID/content. The owner returns the ledger
    // outcome if admitted, or admits once if the original message never arrived.
    // Failure of reconciliation propagates; callers must stop subsequent writes.
    try { return await call('command', params); }
    catch(error) { error.commandParams=params; throw error; }
  }
}
