// Render the current descriptor's typed layout operands without device policies.
const TYPES = new Set(['integer', 'number', 'text', 'boolean', 'choice', 'integer_array']);
let fieldSequence = 0;

function fieldsOf(workflow) {
  if (!workflow || !Array.isArray(workflow.layout_fields) ||
      !Array.isArray(workflow.layout_keys) || !Array.isArray(workflow.required_layout_keys)) {
    throw Error('Workflow field descriptions are unavailable. Refresh the source.');
  }
  const keys = new Set(workflow.layout_keys), required = new Set(workflow.required_layout_keys);
  if (keys.size !== workflow.layout_keys.length || required.size !== workflow.required_layout_keys.length ||
      [...keys, ...required].some(key => typeof key !== 'string' || !key || !keys.has(key))) {
    throw Error('Workflow field descriptions contain invalid keys.');
  }
  const seen = new Set();
  for (const field of workflow.layout_fields) {
    if (!field || typeof field.key !== 'string' || !keys.has(field.key) || seen.has(field.key) ||
        !TYPES.has(field.type) || typeof field.label !== 'string' || !field.label.trim() ||
        typeof field.help !== 'string' || typeof field.required !== 'boolean' ||
        field.required !== required.has(field.key) || !Object.hasOwn(field, 'default')) {
      throw Error('A workflow field has an unsupported or incomplete description.');
    }
    seen.add(field.key);
    if (['integer', 'number', 'integer_array'].includes(field.type) &&
        (!Number.isFinite(field.minimum) || field.maximum !== null &&
         (!Number.isFinite(field.maximum) || field.maximum < field.minimum) ||
         typeof field.exclusive_minimum !== 'boolean' || typeof field.unit !== 'string')) {
      throw Error(`${field.label} has incomplete numeric limits.`);
    }
    if (field.type === 'integer_array' && (!Number.isSafeInteger(field.min_items) || field.min_items < 1 ||
        !Number.isSafeInteger(field.max_items) || field.max_items < field.min_items)) {
      throw Error(`${field.label} has incomplete list limits.`);
    }
    if (field.type === 'text' && (!Number.isSafeInteger(field.max_bytes) || field.max_bytes < 1 ||
        !['utf8', 'ascii_printable'].includes(field.encoding))) {
      throw Error(`${field.label} has incomplete text limits.`);
    }
    if (field.type === 'choice' && (!Array.isArray(field.choices) || !field.choices.length ||
        field.choices.some(choice => typeof choice !== 'string' || !choice) ||
        new Set(field.choices).size !== field.choices.length)) {
      throw Error(`${field.label} has incomplete choices.`);
    }
  }
  if ([...required].some(key => !seen.has(key))) {
    throw Error('A required workflow field has no typed description.');
  }
  return workflow.layout_fields;
}

function numberValue(field, text) {
  if (field.type !== 'number') {
    // Check exact decimal integrality before binary64 conversion can round a fraction.
    // Integral decimal and exponent spellings retain their existing meaning.
    const spelling = /^[+-]?(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?$/u.exec(text.trim());
    if (!spelling) throw Error(`${field.label} needs a finite whole number.`);
    const coefficient = spelling[1], digits = coefficient.replace('.', '');
    const significant = digits.replace(/0+$/u, '');
    const fractionalPlaces = coefficient.includes('.') ? coefficient.split('.')[1].length : 0;
    if (significant && BigInt(fractionalPlaces) - BigInt(spelling[2] || '0') >
        BigInt(digits.length - significant.length)) {
      throw Error(`${field.label} needs a finite whole number.`);
    }
  }
  const value = Number(text);
  if (!Number.isFinite(value) || field.type !== 'number' && !Number.isSafeInteger(value)) {
    throw Error(`${field.label} needs a finite ${field.type === 'number' ? 'number' : 'whole number'}.`);
  }
  if (value < field.minimum || field.exclusive_minimum && value === field.minimum ||
      field.maximum !== null && value > field.maximum) {
    throw Error(`${field.label} must be ${field.exclusive_minimum ? 'greater than' : 'at least'} ${field.minimum}` +
      (field.maximum === null ? '.' : ` and at most ${field.maximum}.`));
  }
  return value;
}

function valueOf(field, control) {
  const text = control.value;
  if (control.validity.badInput) throw Error(`${field.label} needs a number.`);
  const blank = field.type === 'text' ? text === '' : text.trim() === '';
  if (blank) {
    if (field.required) throw Error(`Enter ${field.label.toLowerCase()}.`);
    return undefined;
  }
  if (field.type === 'boolean') {
    if (!['true', 'false'].includes(text)) throw Error(`Choose Yes or No for ${field.label}.`);
    return text === 'true';
  }
  if (field.type === 'choice') {
    if (!field.choices.includes(text)) throw Error(`Choose an available ${field.label.toLowerCase()}.`);
    return text;
  }
  if (field.type === 'text') {
    if (new TextEncoder().encode(text).length > field.max_bytes) {
      throw Error(`${field.label} exceeds its ${field.max_bytes}-byte text limit.`);
    }
    if (field.encoding === 'ascii_printable' && /[^\x20-\x7e]/u.test(text)) {
      throw Error(`${field.label} accepts printable ASCII text only.`);
    }
    if (field.encoding === 'utf8' && !text.trim()) throw Error(`Enter nonblank ${field.label.toLowerCase()}.`);
    return text;
  }
  if (field.type === 'integer_array') {
    if (!/^[+-]?\d+(?:(?:\s*,\s*|\s+)[+-]?\d+)*$/u.test(text.trim())) {
      throw Error(`${field.label} needs whole numbers separated by commas or spaces.`);
    }
    const values = text.trim().split(/\s*,\s*|\s+/u).map(item => numberValue(field, item));
    if (values.length < field.min_items || values.length > field.max_items) {
      throw Error(`${field.label} needs ${field.min_items} to ${field.max_items} entries.`);
    }
    return values;
  }
  return numberValue(field, text);
}

function defaultLabel(field) {
  if (field.default === null) return 'Leave unset';
  const value = typeof field.default === 'boolean' ? field.default ? 'Yes' : 'No' : field.default;
  return `Use default (${value})`;
}

/** Rebuild only when the caller changes workflows; refreshes need not replace these controls. */
export function renderWorkflowFields(container, workflow, values = {}) {
  const fields = fieldsOf(workflow), document = container.ownerDocument;
  if (!values || typeof values !== 'object' || Array.isArray(values) ||
      Object.keys(values).some(key => !fields.some(field => field.key === key))) {
    throw Error('Layout values do not belong to the selected workflow.');
  }
  const fragment = document.createDocumentFragment();
  for (const field of fields) {
    const label = document.createElement('label'), caption = document.createElement('span');
    caption.id = `workflow-field-label-${++fieldSequence}`;
    caption.textContent = field.label + (field.unit ? ` · ${field.unit}` : '');
    label.append(caption);
    const control = document.createElement(['boolean', 'choice'].includes(field.type) ? 'select' : 'input');
    control.dataset.workflowField = field.key;
    control.required = field.required;
    control.setAttribute('aria-labelledby', caption.id);
    if (control.tagName === 'SELECT') {
      const choices = field.type === 'boolean' ? [['true', 'Yes'], ['false', 'No']] :
        field.choices.map(choice => [choice, choice]);
      for (const [value, text] of [['', field.required ? 'Choose…' : defaultLabel(field)], ...choices]) {
        const option = document.createElement('option');
        option.value = value; option.textContent = text; control.append(option);
      }
    } else {
      control.type = ['integer', 'number'].includes(field.type) ? 'number' : 'text';
      if (control.type === 'number') {
        control.min = String(field.minimum);
        if (field.maximum !== null) control.max = String(field.maximum);
        control.step = field.type === 'integer' ? '1' : 'any';
      }
      if (field.type === 'integer_array') {
        control.inputMode = 'numeric'; control.placeholder = 'Comma or space separated';
      } else if (field.default !== null) control.placeholder = String(field.default);
    }
    if (Object.hasOwn(values, field.key) && values[field.key] !== undefined && values[field.key] !== null) {
      const value = values[field.key];
      if (field.type === 'integer_array' && !Array.isArray(value)) {
        throw Error(`${field.label} needs an integer list.`);
      }
      control.value = field.type === 'integer_array' ? value.join(', ') : String(value);
    }
    const clearError = () => { control.setCustomValidity(''); control.removeAttribute('aria-invalid'); };
    control.addEventListener('input', clearError); control.addEventListener('change', clearError);
    label.append(control);
    if (field.help) {
      const help = document.createElement('span');
      help.id = `workflow-field-help-${fieldSequence}`;
      help.className = 'small muted'; help.textContent = field.help;
      control.setAttribute('aria-describedby', help.id); label.append(help);
    }
    fragment.append(label);
  }
  container.replaceChildren(fragment);
}

/** Read typed operands without filling optional defaults or deriving target geometry. */
export function collectWorkflowFields(container, workflow) {
  const fields = fieldsOf(workflow);
  const controls = [...container.querySelectorAll('[data-workflow-field]')];
  if (controls.length !== fields.length || new Set(controls.map(control => control.dataset.workflowField)).size !== fields.length ||
      controls.some(control => !fields.some(field => field.key === control.dataset.workflowField))) {
    throw Error('Workflow controls changed. Select the workflow again.');
  }
  const entries = [];
  for (const field of fields) {
    const control = controls.find(control => control.dataset.workflowField === field.key);
    control.setCustomValidity(''); control.removeAttribute('aria-invalid');
    try {
      const value = valueOf(field, control);
      if (value !== undefined) entries.push([field.key, value]);
    } catch (error) {
      control.setCustomValidity(error.message); control.setAttribute('aria-invalid', 'true');
      control.focus(); control.reportValidity(); throw error;
    }
  }
  return Object.fromEntries(entries);
}
