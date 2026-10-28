import nominalGuides from '../../../src/color_measurement/desktop_assets/nominal-chart-guides.json' with {type:'json'};
// Canonical chart identities stay unchanged when the operator rotates the guide.
const guides = new WeakMap();
const DENSE_CELLS = 48;

/** Location illustration only; never an expected or measured scientific color. */
export function nominalChartColor(view, index) {
  const guide = nominalGuides.guides[view?.definition?.kind];
  if (!guide || guide.rows !== view.definition.rows || guide.columns !== view.definition.columns) return null;
  const cell = guide.cells[index - 1];
  return cell?.index === index ? cell.rgb8.slice() : null;
}

function coordinateParts(cell) {
  const parts = /^([A-Z]+)([1-9][0-9]*)$/.exec(cell.coordinate);
  const column = parts?.[1].split('').reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
  if (!parts || Number(parts[2]) !== cell.row || column !== cell.column) {
    throw Error('Chart coordinates do not match their canonical geometry.');
  }
  return parts.slice(1);
}

/** Return clockwise display positions while retaining each canonical cell identity. */
export function displayGrid(view) {
  const rows = view?.definition?.rows, columns = view?.definition?.columns;
  if (!Number.isSafeInteger(rows) || !Number.isSafeInteger(columns) || rows < 1 || columns < 1 ||
      rows * columns > 140 || ![0, 90, 180, 270].includes(view.orientation) ||
      !Array.isArray(view.cells) || view.cells.length !== rows * columns) {
    throw Error('Chart guide geometry is unavailable. Refresh the chart.');
  }
  const seen = new Set();
  const cells = view.cells.map(cell => {
    if (!cell || !Number.isSafeInteger(cell.row) || !Number.isSafeInteger(cell.column) ||
        cell.row < 1 || cell.row > rows || cell.column < 1 || cell.column > columns ||
        cell.index !== (cell.row - 1) * columns + cell.column || seen.has(cell.index) ||
        typeof cell.coordinate !== 'string' || !cell.coordinate || typeof cell.measured !== 'boolean' ||
        !Number.isSafeInteger(cell.attempt_count) || cell.attempt_count < 0) {
      throw Error('Chart guide cells do not match their canonical geometry.');
    }
    coordinateParts(cell);
    seen.add(cell.index);
    let row = cell.row, column = cell.column;
    if (view.orientation === 90) [row, column] = [cell.column, rows - cell.row + 1];
    if (view.orientation === 180) [row, column] = [rows - cell.row + 1, columns - cell.column + 1];
    if (view.orientation === 270) [row, column] = [columns - cell.column + 1, cell.row];
    return {...cell, display_row: row, display_column: column};
  });
  cells.sort((a, b) => a.display_row - b.display_row || a.display_column - b.display_column);
  return {rows: view.orientation % 180 ? columns : rows,
    columns: view.orientation % 180 ? rows : columns, cells};
}

function text(node, value) {
  if (node.textContent !== value) node.textContent = value;
}

/** Keep full coordinates readable on dense boards, with a separate preview marker. */
export function labelChartCell(button, cell, kind) {
  let label = button.querySelector('.chart-cell-label');
  if (!label) {
    const document = button.ownerDocument;
    label = document.createElement('span'); label.className = 'chart-cell-label';
    const coordinate = document.createElement('span'), marker = document.createElement('span');
    coordinate.className = 'chart-cell-coordinate'; marker.className = 'chart-cell-marker';
    label.append(coordinate, marker); button.replaceChildren(label);
  }
  text(label.firstElementChild, cell.coordinate);
  text(label.lastElementChild, cell.measured ? ' ✓' : kind === 'expected' ? ' ○' : kind === 'nominal' ? ' ◇' : '');
}

function moveFocus(state, index, focus = true) {
  state.activeCell = index;
  for (const [key, button] of state.buttons) button.tabIndex = key === index ? 0 : -1;
  if (focus) state.buttons.get(index).focus();
}

function keydown(state, event) {
  const button = event.target.closest('[data-chart-cell]');
  if (!button || !state.grid.contains(button) || event.altKey || event.metaKey) return;
  const index = Number(button.dataset.chartCell), cells = state.display.cells;
  const position = cells.findIndex(cell => cell.index === index);
  const row = Math.floor(position / state.display.columns), column = position % state.display.columns;
  let target = position;
  if (event.key === 'ArrowLeft' && !event.ctrlKey) target = column > 0 ? position - 1 : position;
  else if (event.key === 'ArrowRight' && !event.ctrlKey) target = column + 1 < state.display.columns ? position + 1 : position;
  else if (event.key === 'ArrowUp' && !event.ctrlKey) target = row > 0 ? position - state.display.columns : position;
  else if (event.key === 'ArrowDown' && !event.ctrlKey) target = row + 1 < state.display.rows ? position + state.display.columns : position;
  else if (event.key === 'Home') target = event.ctrlKey ? 0 : row * state.display.columns;
  else if (event.key === 'End') target = event.ctrlKey ? cells.length - 1 : (row + 1) * state.display.columns - 1;
  else return;
  event.preventDefault();
  moveFocus(state, cells[target].index);
}

function newGuide(container) {
  const document = container.ownerDocument;
  const section = document.createElement('section'), summary = document.createElement('p');
  const heading = document.createElement('div'), key = document.createElement('details');
  const scroll = document.createElement('div'), board = document.createElement('div');
  const columns = document.createElement('div'), rows = document.createElement('div'), grid = document.createElement('div');
  section.className = 'chart-guide'; summary.className = 'chart-guide-summary';
  heading.className = 'chart-guide-heading'; key.className = 'chart-guide-key';
  const keyTitle = document.createElement('summary'), keyList = document.createElement('ul');
  keyTitle.textContent = 'Key';
  for (const meaning of ['Solid frame: next measurement target', 'Dashed frame: inspected patch (dense boards: on hover or keyboard focus)',
    'Dotted frame: keyboard focus', 'Solid underlined axis label: target column and row',
    'Dashed underlined axis label: inspected column and row', '✓ Kept reading', '○ Reference color preview',
    '◇ Nominal location guide: illustration colors, not expected values', 'Patterned face: kept color preview pending or unavailable',
    '!: latest attempt failed', '×: latest attempt cancelled', '?: needs review', '…: measuring']) {
    const item = document.createElement('li'); item.textContent = meaning; keyList.append(item);
  }
  key.append(keyTitle, keyList); heading.append(summary, key);
  scroll.className = 'chart-guide-scroll'; board.className = 'chart-guide-board';
  columns.className = 'chart-axis-columns'; rows.className = 'chart-axis-rows';
  columns.setAttribute('aria-hidden', 'true'); rows.setAttribute('aria-hidden', 'true');
  grid.className = 'chart-guide-grid';
  Object.assign(section.style, {display: 'flex', flexDirection: 'column', height: '100%',
    minHeight: '0', minWidth: '0', width: '100%', overflow: 'hidden'});
  Object.assign(scroll.style, {flex: '1', minHeight: '0', minWidth: '0', overflow: 'hidden'});
  summary.style.flex = 'none'; summary.setAttribute('role', 'status');
  grid.setAttribute('role', 'grid');
  grid.setAttribute('aria-description', 'Arrow keys move between patches. Enter or Space inspects a patch.');
  board.append(columns, rows, grid); scroll.append(board);
  section.append(heading, scroll); container.replaceChildren(section);
  const state = {section, summary, scroll, board, columns, rows, grid, buttons: new Map(), cells: new Map(),
    activeCell: null, geometry: null, display: null, onInspect: null};
  grid.addEventListener('keydown', event => keydown(state, event));
  grid.addEventListener('focusin', event => {
    const button = event.target.closest('[data-chart-cell]');
    if (button && grid.contains(button)) moveFocus(state, Number(button.dataset.chartCell), false);
  });
  guides.set(container, state);
  return state;
}

/** Render structural cells with nominal location faces; scientific colors remain Python-owned. */
export function renderChartGuide(container, view, {onInspect} = {}) {
  const display = displayGrid(view);
  if (typeof onInspect !== 'function' || typeof view.definition.name !== 'string' ||
      !view.definition.name || !display.cells.some(cell => cell.index === view.current_cell) ||
      !display.cells.some(cell => cell.index === view.inspection_cell) ||
      view.total !== display.cells.length || !Number.isSafeInteger(view.coverage) ||
      view.coverage < 0 || view.coverage > view.total) {
    throw Error('Chart guide state or inspection action is unavailable.');
  }
  let state = guides.get(container);
  if (!state || state.section.parentElement !== container) state = newGuide(container);
  state.display = display; state.onInspect = onInspect;
  const document = container.ownerDocument;
  const focused = state.grid.contains(document.activeElement) ? Number(document.activeElement.dataset.chartCell) : null;
  const byIndex = new Map(display.cells.map(cell => [cell.index, cell]));
  if (!byIndex.has(state.activeCell)) state.activeCell = view.inspection_cell;
  const caption = cell => cell.coordinate + (cell.patch_name ? ` (${cell.patch_name})` : '');
  text(state.summary, `${view.definition.name} · Target: ${caption(byIndex.get(view.current_cell))}` +
    ` · Inspecting: ${caption(byIndex.get(view.inspection_cell))}`);
  state.grid.setAttribute('aria-label', `${view.definition.name} patch guide`);
  state.grid.setAttribute('aria-rowcount', String(display.rows));
  state.grid.setAttribute('aria-colcount', String(display.columns));
  const geometry = JSON.stringify([view.orientation, display.rows, display.columns,
    display.cells.map(cell => [cell.index, cell.display_row, cell.display_column])]);
  if (geometry !== state.geometry) {
    state.board.style.setProperty('--chart-columns', display.columns);
    state.board.style.setProperty('--chart-rows', display.rows);
    state.board.dataset.orientation = String(view.orientation);
    // A small fitted face cannot hold a frame, marker and label; dense boards name
    // the target and inspected patch on their axes instead.
    state.board.dataset.dense = String(display.cells.length > DENSE_CELLS);
    const axis = (cells, part) => cells.map(cell => {
      const label = document.createElement('span');
      label.textContent = coordinateParts(cell)[part];
      return label;
    });
    const rotated = view.orientation % 180 !== 0;
    state.columns.replaceChildren(...axis(display.cells.slice(0, display.columns), rotated ? 1 : 0));
    state.rows.replaceChildren(...axis(display.cells.filter(cell => cell.display_column === 1), rotated ? 0 : 1));
    const fragment = document.createDocumentFragment();
    for (let row = 1; row <= display.rows; row++) {
      const line = document.createElement('div');
      line.className = 'chart-guide-row'; line.setAttribute('role', 'row');
      line.setAttribute('aria-rowindex', String(row));
      for (const cell of display.cells.filter(item => item.display_row === row)) {
        let node = state.cells.get(cell.index), button = state.buttons.get(cell.index);
        if (!button) {
          node = document.createElement('div'); node.setAttribute('role', 'gridcell');
          node.style.minWidth = '0';
          button = document.createElement('button'); button.type = 'button';
          button.className = 'chart-guide-cell'; button.dataset.chartCell = String(cell.index);
          Object.assign(button.style, {width: '100%', minWidth: '0', whiteSpace: 'normal', overflowWrap: 'anywhere'});
          button.addEventListener('click', () => {
            moveFocus(state, cell.index);
            state.onInspect(cell.index);
          });
          node.append(button); state.cells.set(cell.index, node); state.buttons.set(cell.index, button);
        }
        node.setAttribute('aria-colindex', String(cell.display_column));
        line.append(node);
      }
      fragment.append(line);
    }
    state.grid.replaceChildren(fragment); state.geometry = geometry;
    for (const index of state.buttons.keys()) if (!byIndex.has(index)) {
      state.buttons.delete(index); state.cells.delete(index);
    }
  }
  for (const cell of display.cells) {
    const button = state.buttons.get(cell.index), target = cell.index === view.current_cell;
    const inspected = cell.index === view.inspection_cell;
    button.dataset.target = String(target); button.dataset.inspected = String(inspected);
    button.dataset.measured = String(cell.measured);
    button.classList.toggle('is-target', target); button.classList.toggle('is-inspected', inspected);
    button.classList.toggle('is-measured', cell.measured);
    const nominal = cell.measured ? null : nominalChartColor(view, cell.index);
    button.style.backgroundColor = nominal ? `rgb(${nominal.join(' ')})` : '';
    button.style.color = '';
    button.dataset.chartColorKind = nominal ? 'nominal' : cell.measured ? 'loading' : 'unavailable';
    const description = nominal ? 'Nominal location guide · supplied layout illustration' :
      cell.measured ? 'Preparing measured color preview' : 'Reference color preview unavailable';
    button.title = caption(cell) + ' · ' + description;
    button.setAttribute('aria-description', description);
    const pending = view.pending_position?.cell === cell.index;
    const latest = cell.latest_attempt?.status || '';
    button.dataset.latestStatus = pending ? 'placement' : latest;
    labelChartCell(button, cell, nominal ? 'nominal' : '');
    const status = pending ? 'Placement needs review.' : {
      started: 'Measuring.', failed: 'Latest attempt failed.', cancelled: 'Latest attempt cancelled.',
      unresolved: 'Latest attempt needs review.',
    }[latest] || '';
    const name = typeof cell.patch_name === 'string' && cell.patch_name.trim() ? cell.patch_name.trim() + '. ' : '';
    button.setAttribute('aria-label', `Patch ${cell.coordinate}. ${target ? 'Current target. ' : ''}` +
      `${name}${inspected ? 'Inspected. ' : ''}${cell.measured ? 'Measured. Selected reading kept' : 'Not measured'}. ` +
      `${status ? status + ' ' : ''}` +
      `${cell.attempt_count} attempt${cell.attempt_count === 1 ? '' : 's'}.`);
    state.cells.get(cell.index).setAttribute('aria-selected', String(inspected));
  }
  for (const [labels, place] of [[state.columns.children, 'display_column'], [state.rows.children, 'display_row']]) {
    [...labels].forEach((label, offset) => {
      label.dataset.target = String(byIndex.get(view.current_cell)[place] === offset + 1);
      label.dataset.inspected = String(byIndex.get(view.inspection_cell)[place] === offset + 1);
    });
  }
  moveFocus(state, focused && byIndex.has(focused) ? focused : state.activeCell, false);
  if (focused && document.activeElement !== state.buttons.get(focused) && byIndex.has(focused)) {
    state.buttons.get(focused).focus({preventScroll: true});
  }
}
