// Renderer acceptance consumes Node test events, not a file-level success count.
// This reporter runs only in software tests; it performs no renderer or UI work.
import path from 'node:path';

export default async function* rendererTestReporter(events) {
  const selector = /--(?:test-(?:name-pattern|skip-pattern|only|shard|rerun-failures)|experimental-test-tag-filter)(?:=|\s|$)/;
  // Node accepts underscores in option names and preserves that spelling in
  // execArgv. Failure replay also reports passes without executing callbacks.
  const selectsCases = argument => selector.test(argument.replaceAll('_', '-'));
  const filtered = process.execArgv.some(selectsCases)
    || selectsCases(process.env.NODE_OPTIONS || '');
  const leaves = [], failures = [];
  let summary, parentPlan = false, unfinished = false;
  for await (const { type, data } of events) {
    if (type === 'test:summary' && !data.file) summary = data;
    // Node reports a parent's subtest plan immediately before that parent's
    // outcome. These common events avoid relying on newer per-test IDs.
    if (type === 'test:plan' && data.nesting > 0) parentPlan = true;
    if (type !== 'test:pass' && type !== 'test:fail') continue;
    const parent = parentPlan;
    parentPlan = false;
    unfinished ||= Boolean(data.skip || data.todo);
    if (type === 'test:fail') {
      failures.push({ name: data.name, reason: data.details.error?.message || 'Test failed' });
      continue;
    }
    // Node may pass an empty test file as one synthetic test at line 1. Suites
    // also pass without executing any leaf. Neither establishes renderer proof.
    if (parent || data.details.type !== 'test'
        || (data.file && path.resolve(data.name) === path.resolve(data.file))
        || data.skip || data.todo) continue;
    leaves.push({ name: data.name, file: data.file, line: data.line, column: data.column });
  }
  const counts = summary?.counts;
  const accepted = !filtered && !unfinished && summary?.success === true
    && counts.failed === 0 && counts.cancelled === 0 && counts.skipped === 0 && counts.todo === 0
    && leaves.length > 0;
  yield JSON.stringify({ format: 'renderer-test-acceptance', accepted,
    named_leaf_cases: leaves, counts, filtered, failures }) + '\n';
  if (!accepted) {
    throw Error('Renderer tests require successful named leaf cases, complete unfiltered execution, '
      + 'and no failed, cancelled, skipped or todo cases');
  }
}
