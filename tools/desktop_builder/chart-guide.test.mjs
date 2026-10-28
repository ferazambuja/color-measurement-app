import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { displayGrid, nominalChartColor, renderChartGuide } from './renderer/chart-guide.js';

function view(rows = 2, columns = 3, orientation = 0) {
  return {definition: {name: 'Test chart', rows, columns}, orientation,
    current_cell: 1, inspection_cell: 2, coverage: 1, total: rows * columns,
    cells: Array.from({length: rows * columns}, (_, position) => {
      const row = Math.floor(position / columns) + 1, column = position % columns + 1;
      return {index: position + 1, row, column, coordinate: `${String.fromCharCode(64 + column)}${row}`,
        measured: position === 1, selected_attempt: position === 1 ? 'attempt' : null,
        attempt_count: position === 1 ? 2 : 0};
    })};
}
function workspace() {
  const dom = new JSDOM('<div id="guide" style="height:240px;width:320px"></div>');
  const container = dom.window.document.getElementById('guide'), inspections = [];
  return {dom, container, inspections,
    render: data => renderChartGuide(container, data, {onInspect: index => inspections.push(index)}),
    button: index => container.querySelector(`[data-chart-cell="${index}"]`),
    key(button, key, options = {}) {
      button.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, ...options}));
    }};
}

for (const [orientation, rows, columns, expected, top, side] of [
  [0, 2, 3, [1, 2, 3, 4, 5, 6], ['A', 'B', 'C'], ['1', '2']],
  [90, 3, 2, [4, 1, 5, 2, 6, 3], ['2', '1'], ['A', 'B', 'C']],
  [180, 2, 3, [6, 5, 4, 3, 2, 1], ['C', 'B', 'A'], ['2', '1']],
  [270, 3, 2, [3, 6, 2, 5, 1, 4], ['1', '2'], ['C', 'B', 'A']],
]) test(`${orientation}-degree guide rotates display order without changing canonical identities`, () => {
  const app = workspace(), data = view(2, 3, orientation);
  try {
    const original = structuredClone(data), grid = displayGrid(data);
    assert.equal(grid.rows, rows); assert.equal(grid.columns, columns);
    assert.deepEqual(grid.cells.map(cell => cell.index), expected);
    assert.equal(grid.cells.find(cell => cell.index === 2).coordinate, 'B1');
    assert.deepEqual(data, original);
    app.render(data);
    assert.deepEqual([...app.container.querySelectorAll('button')].map(button => Number(button.dataset.chartCell)), expected);
    const node = app.container.querySelector('[role="grid"]');
    assert.equal(node.getAttribute('aria-rowcount'), String(rows));
    assert.equal(node.getAttribute('aria-colcount'), String(columns));
    assert.deepEqual([...app.container.querySelector('.chart-axis-columns').children].map(label => label.textContent), top);
    assert.deepEqual([...app.container.querySelector('.chart-axis-rows').children].map(label => label.textContent), side);
  } finally { app.dom.window.close(); }
});

test('cell actions inspect only and mark acquisition target separately from inspection', () => {
  const app = workspace(), data = view();
  try {
    app.render(data); app.button(5).click();
    assert.deepEqual(app.inspections, [5]);
    assert.equal(data.current_cell, 1); assert.equal(data.inspection_cell, 2);
    assert.equal(app.button(1).dataset.target, 'true');
    assert.equal(app.button(2).dataset.inspected, 'true');
    assert.match(app.container.querySelector('.chart-guide-summary').textContent, /Target: A1.*Inspecting: B1/);
    assert.match(app.button(2).getAttribute('aria-label'), /Inspected.*Measured.*2 attempts/);
    assert.equal(app.container.querySelectorAll('[tabindex="0"]').length, 1);
  } finally { app.dom.window.close(); }
});

test('source-supplied names supplement coordinates without guessing unknown names', () => {
  const app = workspace(), data = view();
  try {
    data.cells[0].patch_name = 'Dark skin'; data.cells[1].patch_name = 'Light skin';
    app.render(data);
    assert.match(app.container.querySelector('.chart-guide-summary').textContent, /Target: A1 \(Dark skin\).*Inspecting: B1 \(Light skin\)/);
    assert.match(app.button(2).getAttribute('aria-label'), /Patch B1.*Light skin/);
    assert.equal(app.button(3).textContent, 'C1');
  } finally { app.dom.window.close(); }
});

test('known layouts supply only nominal faces and never paint a kept reading', () => {
  const app = workspace();
  try {
    for (const [kind, rows, columns] of [['classic', 4, 6], ['sg', 10, 14]]) {
      const data = view(rows, columns); data.definition.kind = kind;
      const original = structuredClone(data), rgb = nominalChartColor(data, 1);
      assert.equal(rgb.length, 3); assert.ok(rgb.every(value => Number.isInteger(value)));
      app.render(data);
      assert.equal(app.button(1).dataset.chartColorKind, 'nominal');
      assert.equal(app.button(1).textContent, 'A1 ◇');
      assert.match(app.button(1).getAttribute('aria-description'), /Nominal location guide/);
      assert.equal(app.button(1).style.backgroundColor, `rgb(${rgb.join(', ')})`);
      assert.equal(app.button(2).dataset.chartColorKind, 'loading');
      assert.equal(app.button(2).style.backgroundColor, '');
      assert.equal(app.button(2).textContent, 'B1 ✓');
      rgb[0] = -1;
      assert.ok(nominalChartColor(data, 1)[0] >= 0, 'Callers cannot change resource values');
      assert.deepEqual(data, original, 'Presentation must not add nominal data to the chart');
      data.definition.kind = 'custom'; app.render(data);
      assert.equal(nominalChartColor(data, 1), null);
      assert.equal(app.button(1).style.backgroundColor, '');
      data.definition.kind = kind; data.definition.rows = 1;
      assert.equal(nominalChartColor(data, 1), null, 'Dimensions alone cannot select an illustration');
    }
  } finally { app.dom.window.close(); }
});

test('arrow keys follow displayed orientation and leave inspection to activation', () => {
  const app = workspace();
  try {
    for (const [orientation, start, right, down] of [[0, 1, 2, 4], [90, 4, 1, 5],
      [180, 6, 5, 3], [270, 3, 6, 2]]) {
      app.render(view(2, 3, orientation)); app.button(start).focus();
      app.key(app.button(start), 'ArrowRight');
      assert.equal(app.dom.window.document.activeElement, app.button(right));
      assert.equal(app.button(right).tabIndex, 0); assert.equal(app.button(start).tabIndex, -1);
      app.button(start).focus(); app.key(app.button(start), 'ArrowDown');
      assert.equal(app.dom.window.document.activeElement, app.button(down));
      app.button(start).focus(); app.key(app.button(start), 'ArrowLeft');
      assert.equal(app.dom.window.document.activeElement, app.button(start));
      app.key(app.button(start), 'ArrowUp'); assert.equal(app.dom.window.document.activeElement, app.button(start));
    }
    assert.deepEqual(app.inspections, []);
    app.button(2).click(); assert.deepEqual(app.inspections, [2]);
  } finally { app.dom.window.close(); }
});

// jsdom evaluates no CSS layout: this checks the bounded inline structure the
// renderer sets, not fitted geometry, which only a browser can measure.
test('dense 140-cell guide builds bounded inline structure and supplies no invented cell colors', () => {
  const app = workspace();
  try {
    app.render(view(10, 14));
    assert.equal(app.container.querySelectorAll('button').length, 140);
    assert.equal(app.container.querySelectorAll('[role="row"]').length, 10);
    assert.equal(app.container.querySelector('.chart-guide-scroll').style.overflow, 'hidden');
    assert.equal(app.container.querySelector('.chart-guide').style.height, '100%');
    assert.equal(app.container.querySelector('.chart-axis-columns').textContent, 'ABCDEFGHIJKLMN');
    assert.equal(app.container.querySelector('.chart-axis-rows').textContent, '12345678910');
    assert.equal([...app.container.querySelectorAll('button')].every(button => !button.style.backgroundColor && !button.style.color), true);
    assert.equal(app.button(1).style.whiteSpace, 'normal');
    assert.equal(app.button(1).style.width, '100%');
    app.button(140).focus(); app.key(app.button(140), 'Home', {ctrlKey: true});
    assert.equal(app.dom.window.document.activeElement, app.button(1));
    app.key(app.button(1), 'End', {ctrlKey: true});
    assert.equal(app.dom.window.document.activeElement, app.button(140));
  } finally { app.dom.window.close(); }
});

test('dense boards name target and inspected patches on their axes', () => {
  const app = workspace(), data = view(10, 14);
  try {
    data.current_cell = 16; data.inspection_cell = 140;
    app.render(data);
    const board = app.container.querySelector('.chart-guide-board');
    const marked = (axis, flag) => [...app.container.querySelector(axis).children]
      .filter(label => label.dataset[flag] === 'true').map(label => label.textContent);
    assert.equal(board.dataset.dense, 'true');
    assert.deepEqual(marked('.chart-axis-columns', 'target'), ['B']);
    assert.deepEqual(marked('.chart-axis-rows', 'target'), ['2']);
    assert.deepEqual(marked('.chart-axis-columns', 'inspected'), ['N']);
    assert.deepEqual(marked('.chart-axis-rows', 'inspected'), ['10']);
    data.orientation = 90; app.render(data);
    assert.deepEqual(marked('.chart-axis-columns', 'target'), ['2']);
    assert.deepEqual(marked('.chart-axis-rows', 'target'), ['B']);
    const classic = workspace();
    try { classic.render(view(4, 6)); assert.equal(classic.container.querySelector('.chart-guide-board').dataset.dense, 'false'); }
    finally { classic.dom.window.close(); }
  } finally { app.dom.window.close(); }
});

test('latest failed repeat and pending placement remain distinct from the selected reading', () => {
  const app = workspace(), data = view();
  try {
    data.cells[1].latest_attempt = {id: 'failed-repeat', status: 'failed', result_status: 'failed'};
    data.cells[2].latest_attempt = {id: 'candidate', status: 'completed', result_status: 'completed'};
    data.pending_position = {cell: 3};
    app.render(data);
    assert.equal(app.button(2).dataset.measured, 'true');
    assert.equal(app.button(2).dataset.latestStatus, 'failed');
    assert.match(app.button(2).getAttribute('aria-label'), /Selected reading kept.*Latest attempt failed/);
    assert.equal(app.button(3).dataset.measured, 'false');
    assert.equal(app.button(3).dataset.latestStatus, 'placement');
    assert.match(app.button(3).getAttribute('aria-label'), /Not measured.*Placement needs review/);
    const original = app.button(2);
    data.cells[1].latest_attempt.status = 'completed'; data.pending_position = null;
    app.render(data);
    assert.equal(app.button(2), original);
    assert.equal(app.button(2).dataset.latestStatus, 'completed');
    assert.doesNotMatch(app.button(2).getAttribute('aria-label'), /failed/);
    assert.equal(app.button(3).dataset.latestStatus, 'completed');
  } finally { app.dom.window.close(); }
});

test('single-patch rotation refreshes axes without replacing its focused control', () => {
  const app = workspace(), data = view(1, 1);
  data.inspection_cell = 1; data.coverage = 0;
  try {
    app.render(data); const original = app.button(1); original.focus();
    assert.equal(app.container.querySelector('.chart-axis-columns').textContent, 'A');
    data.orientation = 90; app.render(data);
    assert.equal(app.container.querySelector('.chart-axis-columns').textContent, '1');
    assert.equal(app.container.querySelector('.chart-axis-rows').textContent, 'A');
    assert.equal(app.button(1), original);
    assert.equal(app.dom.window.document.activeElement, original);
  } finally { app.dom.window.close(); }
});

test('status refresh and rotation preserve cell nodes, focus and the latest inspection callback', () => {
  const app = workspace(), data = view();
  try {
    app.render(data); const original = app.button(5); original.focus();
    app.render(structuredClone(data));
    assert.equal(app.button(5), original); assert.equal(app.dom.window.document.activeElement, original);
    data.inspection_cell = 5; data.cells[4].measured = true; data.cells[4].attempt_count = 1; data.coverage = 2;
    app.render(data); assert.equal(app.button(5), original);
    assert.equal(app.dom.window.document.activeElement, original); assert.equal(original.dataset.inspected, 'true');
    data.orientation = 90; const latest = [];
    renderChartGuide(app.container, data, {onInspect: index => latest.push(index)});
    assert.equal(app.button(5), original); assert.equal(app.dom.window.document.activeElement, original);
    original.click(); assert.deepEqual(latest, [5]); assert.deepEqual(app.inspections, []);
  } finally { app.dom.window.close(); }
});

test('malformed geometry refuses replacement of the displayed guide', () => {
  const app = workspace();
  try {
    app.render(view()); const original = app.button(2);
    const malformed = view(); malformed.cells[5].index = 1;
    assert.throws(() => app.render(malformed), /canonical geometry/);
    assert.equal(app.button(2), original);
    const wrongCoordinate = view(); wrongCoordinate.cells[1].coordinate = 'B99';
    assert.throws(() => app.render(wrongCoordinate), /coordinates do not match/);
    assert.equal(app.button(2), original);
    assert.throws(() => displayGrid(view(11, 14)), /geometry is unavailable/);
    const unsupported = view(); unsupported.orientation = 45;
    assert.throws(() => displayGrid(unsupported), /geometry is unavailable/);
  } finally { app.dom.window.close(); }
});
