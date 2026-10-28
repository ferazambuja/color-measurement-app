import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { collectWorkflowFields, renderWorkflowFields } from './renderer/workflow-fields.js';

const field = (key, type, extra = {}) => ({key, type, label: key, required: false,
  default: null, help: `Help for ${key}`, ...extra});
const numeric = {minimum: 0, maximum: 100, exclusive_minimum: false, unit: 'mm'};
function workspace(fields, overrides = {}) {
  const dom = new JSDOM('<div id="fields"></div>');
  const container = dom.window.document.getElementById('fields');
  const workflow = {layout_fields: fields, layout_keys: fields.map(item => item.key),
    required_layout_keys: fields.filter(item => item.required).map(item => item.key), ...overrides};
  const control = key => [...container.querySelectorAll('[data-workflow-field]')]
    .find(item => item.dataset.workflowField === key);
  return {dom, container, workflow, control,
    render: values => renderWorkflowFields(container, workflow, values),
    collect: () => collectWorkflowFields(container, workflow)};
}

test('optional blanks omit advertised defaults while explicit false and zero remain typed', () => {
  const app = workspace([field('width', 'number', {...numeric, default: 10}),
    field('calibrate', 'boolean', {default: false}),
    field('direction', 'choice', {choices: ['forward', 'reverse'], default: 'forward'})]);
  try {
    app.render(); assert.deepEqual(app.collect(), {});
    app.control('width').value = '0'; app.control('calibrate').value = 'false';
    app.control('direction').value = 'reverse';
    assert.deepEqual(app.collect(), {width: 0, calibrate: false, direction: 'reverse'});
    app.control('width').value = ''; app.control('calibrate').value = '';
    app.control('direction').value = ''; assert.deepEqual(app.collect(), {});
  } finally { app.dom.window.close(); }
});

test('numeric bounds reject fractional integers and exclusive endpoints with focused feedback', () => {
  const app = workspace([field('count', 'integer', {...numeric, required: true, minimum: 1}),
    field('width', 'number', {...numeric, exclusive_minimum: true})]);
  try {
    app.render(); assert.throws(app.collect, /Enter count/);
    assert.equal(app.dom.window.document.activeElement, app.control('count'));
    app.control('count').value = '1.5'; assert.throws(app.collect, /whole number/);
    app.control('count').value = '101'; assert.throws(app.collect, /at most 100/);
    app.control('count').value = '2'; app.control('width').value = '0';
    assert.throws(app.collect, /greater than 0/);
    app.control('width').value = '0.25';
    assert.deepEqual(app.collect(), {count: 2, width: 0.25});
    assert.equal(app.control('width').hasAttribute('aria-invalid'), false);
  } finally { app.dom.window.close(); }
});

test('integer tokens reject rounded fractions and retain exact safe bounds and integral spellings', () => {
  const app = workspace([field('count', 'integer', {...numeric,
    minimum: -Number.MAX_SAFE_INTEGER, maximum: Number.MAX_SAFE_INTEGER}),
    field('width', 'number', numeric)]);
  try {
    app.render(); app.control('width').value = '0.125';
    for (const input of ['1.0000000000000001', '9007199254740990.5',
      '-9007199254740990.5', '90071992547409905e-1', '1.50000000000000001e1']) {
      assert.ok(Number.isSafeInteger(Number(input)), 'The original token rounds to a safe integer');
      app.control('count').value = input;
      assert.throws(app.collect, /whole number/);
      assert.equal(app.control('count').getAttribute('aria-invalid'), 'true');
      assert.equal(app.dom.window.document.activeElement, app.control('count'));
    }
    for (const input of ['0', '1.0', '1e0', '1.5e1', '.5e1',
      '9007199254740991', '-9007199254740991']) {
      app.control('count').value = input;
      assert.deepEqual(app.collect(), {count: Number(input), width: 0.125});
    }
    app.control('count').value = '9007199254740992';
    assert.throws(app.collect, /whole number/);
  } finally { app.dom.window.close(); }
});

test('UTF-8 text limits count bytes and ASCII text preserves meaningful leading spaces', () => {
  const app = workspace([field('stimulus', 'text', {required: true, max_bytes: 4, encoding: 'utf8'}),
    field('pass', 'text', {max_bytes: 2, encoding: 'ascii_printable'})]);
  try {
    app.render(); app.control('stimulus').value = 'éé'; app.control('pass').value = ' A';
    assert.deepEqual(app.collect(), {stimulus: 'éé', pass: ' A'});
    app.control('stimulus').value = 'ééa'; assert.throws(app.collect, /4-byte/);
    app.control('stimulus').value = '😀'; assert.deepEqual(app.collect(), {stimulus: '😀', pass: ' A'});
    app.control('pass').value = 'é'; assert.throws(app.collect, /printable ASCII/);
    app.control('pass').value = ''; app.control('stimulus').value = '   ';
    assert.throws(app.collect, /nonblank/);
  } finally { app.dom.window.close(); }
});

test('required and unknown metadata fail before replacing existing controls', () => {
  const app = workspace([field('count', 'integer', numeric)]);
  try {
    app.render(); const original = app.control('count'); original.value = '7'; original.focus();
    app.workflow.required_layout_keys.push('missing'); app.workflow.layout_keys.push('missing');
    assert.throws(app.render, /required workflow field has no typed description/);
    assert.equal(app.control('count'), original);
    app.workflow.required_layout_keys = []; app.workflow.layout_keys = ['count'];
    app.workflow.layout_fields[0].type = 'invented'; assert.throws(app.render, /unsupported/);
    app.workflow.layout_fields[0] = {...field('count', 'integer', numeric), id: 'count'};
    delete app.workflow.layout_fields[0].key; assert.throws(app.render, /unsupported/);
    assert.equal(original.value, '7'); assert.equal(app.dom.window.document.activeElement, original);
  } finally { app.dom.window.close(); }
});

test('sheet lists accept commas or spaces and retain Python ownership of coupled geometry', () => {
  const app = workspace([field('sheets', 'integer_array', {...numeric, required: true,
    minimum: 1, min_items: 1, max_items: 3})]);
  try {
    app.render({sheets: [2, 3]}); assert.deepEqual(app.collect(), {sheets: [2, 3]});
    app.control('sheets').value = '2  3, 4'; assert.deepEqual(app.collect(), {sheets: [2, 3, 4]});
    for (const input of ['2,,3', '2,', '2.5', '2;3']) {
      app.control('sheets').value = input; assert.throws(app.collect, /whole numbers separated/);
    }
    app.control('sheets').value = '1 2 3 4'; assert.throws(app.collect, /1 to 3 entries/);
    app.control('sheets').value = '0'; assert.throws(app.collect, /at least 1/);
  } finally { app.dom.window.close(); }
});

test('optional sheet lists omit blanks and collection leaves entered text and focus intact', () => {
  const app = workspace([field('sheets', 'integer_array', {...numeric, minimum: 1, min_items: 1, max_items: 3})]);
  try {
    app.render(); assert.deepEqual(app.collect(), {});
    const control = app.control('sheets'); control.value = '2,  3'; control.focus();
    app.collect(); assert.equal(app.control('sheets'), control);
    assert.equal(control.value, '2,  3'); assert.equal(app.dom.window.document.activeElement, control);
    const label = control.closest('label'); assert.match(label.textContent, /sheets · mm/);
    assert.equal(app.dom.window.document.getElementById(control.getAttribute('aria-labelledby')).textContent,
      'sheets · mm');
    assert.equal(app.dom.window.document.getElementById(control.getAttribute('aria-describedby')).textContent,
      'Help for sheets');
  } finally { app.dom.window.close(); }
});
