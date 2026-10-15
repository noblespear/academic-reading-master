/*
 * Runtime geometry regression without a browser.
 *
 * This intentionally supplies only the DOM operations used by the shared
 * method_pipeline component. It catches runtime mistakes that `node --check`
 * cannot see (for example spreading a point object) and checks the actual
 * routes generated from compact frozen Chara/parallel/sequential/feedback
 * topology fixtures. Pass a paper-data root as the first argument, or set
 * METHOD_PIPELINE_PAPER_ROOT, when auditing against external source files.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SKILL = path.resolve(__dirname, '..', '..');
const FIXTURE_PATH = path.join(__dirname, 'method-pipeline-routing-fixtures.json');
const EXTERNAL_ROOT = process.env.METHOD_PIPELINE_PAPER_ROOT || process.argv[2] || null;
const COMPONENT = path.join(SKILL, 'assets', 'learning-components.js');
const fixtureDocument = JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8'));
const fixtures = new Map((fixtureDocument.fixtures || []).map(fixture => [fixture.name, fixture]));

class StubElement {
  constructor(tag, className, width) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = Object.create(null);
    this.dataset = Object.create(null);
    this.style = Object.create(null);
    this.textContent = '';
    this._width = width;
    this._events = Object.create(null);
    this.classList = {
      add: (...names) => names.forEach(name => this._addClass(name)),
      remove: (...names) => names.forEach(name => this._removeClass(name)),
      toggle: (name, force) => {
        const present = this._classes().has(name), next = force === undefined ? !present : !!force;
        if (next) this._addClass(name); else this._removeClass(name);
        return next;
      },
      contains: name => this._classes().has(name),
    };
    if (className) this.setAttribute('class', className);
  }

  _classes() { return new Set(String(this.attributes.class || '').split(/\s+/).filter(Boolean)); }
  _writeClasses(classes) { this.attributes.class = Array.from(classes).join(' '); }
  _addClass(name) { const classes = this._classes(); classes.add(name); this._writeClasses(classes); }
  _removeClass(name) { const classes = this._classes(); classes.delete(name); this._writeClasses(classes); }

  append(...items) {
    items.flat(Infinity).forEach(item => {
      if (item === null || item === undefined) return;
      if (typeof item === 'string') { this.textContent += item; return; }
      item.parentNode = this;
      this.children.push(item);
    });
  }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = []; this.textContent = ''; this.append(...items); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'class') this.attributes.class = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_m, letter) => letter.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attributes[name] === undefined ? null : this.attributes[name]; }
  addEventListener(name, handler) { this._events[name] = handler; }
  focus() {}
  get isConnected() { return true; }
  getBoundingClientRect() {
    const width = this._width || Number.parseFloat(String(this.style.width || '')) || 0;
    const height = Number.parseFloat(String(this.style.height || '')) || 0;
    return {left: 0, top: 0, right: width, bottom: height, width, height};
  }
}

function allElements(root) {
  const output = [];
  const visit = node => { output.push(node); node.children.forEach(visit); };
  visit(root);
  return output;
}

function findMethodAid(value) {
  if (!value || typeof value !== 'object') return null;
  if (value.type === 'method_pipeline' && value.config) return value;
  if (Array.isArray(value)) for (const item of value) { const found = findMethodAid(item); if (found) return found; }
  else for (const item of Object.values(value)) { const found = findMethodAid(item); if (found) return found; }
  return null;
}

function loadPaper(name) {
  if (!EXTERNAL_ROOT) {
    const fixture = fixtures.get(name);
    if (!fixture) throw new Error(`${name}: routing fixture missing from ${FIXTURE_PATH}`);
    return JSON.parse(JSON.stringify(fixture));
  }
  const paperPath = path.join(path.resolve(EXTERNAL_ROOT), name, 'paper-data.js');
  const context = {window: {}};
  vm.runInNewContext(fs.readFileSync(paperPath, 'utf8'), context, {filename: paperPath});
  const aid = findMethodAid(context.window.PAPER_DATA);
  if (!aid) throw new Error(`${name}: method_pipeline aid not found in ${paperPath}`);
  return aid;
}

function parsePaths(root) {
  return allElements(root).filter(node => String(node.attributes.class || '').split(/\s+/).includes('method-pipeline-edge'));
}

function segmentsFor(node) {
  const commands = String(node.getAttribute('d') || '').match(/[ML]\s*-?(?:\d+(?:\.\d*)?|\.\d+)\s+-?(?:\d+(?:\.\d*)?|\.\d+)/g) || [];
  const points = commands.map(command => command.slice(1).trim().split(/\s+/).map(Number));
  const segments = [];
  for (let i = 1; i < points.length; i++) {
    const [x1, y1] = points[i - 1], [x2, y2] = points[i];
    if (x1 === x2 && y1 === y2) continue;
    if (x1 !== x2 && y1 !== y2) throw new Error(`${node.dataset.edgeId}: diagonal route segment`);
    segments.push({edge: node.dataset.edgeId, axis: x1 === x2 ? 'v' : 'h', coordinate: x1 === x2 ? x1 : y1,
      start: x1 === x2 ? Math.min(y1, y2) : Math.min(x1, x2), end: x1 === x2 ? Math.max(y1, y2) : Math.max(x1, x2)});
  }
  return segments;
}

function overlappingSegments(paths) {
  const segments = paths.flatMap(segmentsFor), overlaps = [];
  for (let i = 0; i < segments.length; i++) for (let j = i + 1; j < segments.length; j++) {
    const a = segments[i], b = segments[j];
    if (a.axis !== b.axis || Math.abs(a.coordinate - b.coordinate) > 6) continue;
    const length = Math.min(a.end, b.end) - Math.max(a.start, b.start);
    if (length > 12) overlaps.push({first: a.edge, second: b.edge, length, gap: Math.abs(a.coordinate - b.coordinate)});
  }
  return overlaps;
}

function nodePenetrations(root, paths) {
  const nodes = allElements(root).filter(node => String(node.attributes.class || '').split(/\s+/).includes('method-pipeline-node')).map(node => ({
    id: node.dataset.nodeId,
    left: Number.parseFloat(String(node.style.left || '0')),
    top: Number.parseFloat(String(node.style.top || '0')),
    right: Number.parseFloat(String(node.style.left || '0')) + Number.parseFloat(String(node.style.width || '0')),
    bottom: Number.parseFloat(String(node.style.top || '0')) + Number.parseFloat(String(node.style.height || '0')),
  }));
  const penetrations = [];
  for (const pathNode of paths) for (const segment of segmentsFor(pathNode)) {
    for (const node of nodes) {
      if (node.id === pathNode.dataset.from || node.id === pathNode.dataset.to) continue;
      const inside = segment.axis === 'h'
        ? segment.coordinate > node.top + 0.5 && segment.coordinate < node.bottom - 0.5 && segment.end > node.left + 0.5 && segment.start < node.right - 0.5
        : segment.coordinate > node.left + 0.5 && segment.coordinate < node.right - 0.5 && segment.end > node.top + 0.5 && segment.start < node.bottom - 0.5;
      if (inside) penetrations.push({edge: pathNode.dataset.edgeId, node: node.id, axis: segment.axis});
    }
  }
  return penetrations;
}

function renderAt(aid, width) {
  const document = {createElementNS: (_ns, tag) => new StubElement(tag, null, width), createElement: tag => new StubElement(tag, null, width)};
  const window = {};
  const context = {window, document, console, ResizeObserver: undefined};
  const componentSource = fs.readFileSync(COMPONENT, 'utf8');
  vm.runInNewContext(componentSource, context, {filename: COMPONENT});
  const component = window.LearningComponents;
  if (!component || !component.render) throw new Error('LearningComponents.render unavailable');
  const root = new StubElement('section', 'fixture-root', width);
  const host = {
    context: {scope: 'node-runtime'},
    studyState: {},
    catalog: {},
    el: (tag, className, initialText) => {
      const node = new StubElement(tag, className, width);
      if (initialText !== undefined) node.textContent = String(initialText);
      return node;
    },
    button: (label, className, handler) => { const button = new StubElement('button', className, width); button.textContent = label; button._events.click = handler; return button; },
    text: (node, value) => { node.textContent = value === undefined ? '' : String(value); },
    onStudyChange: () => {},
    onLayout: () => {},
    typeset: () => {},
    mathAtom: () => new StubElement('span', null, width),
    renderDocument: () => {},
    sourceButton: null,
  };
  // `render` creates its own root; use its result so the same path is tested.
  const rendered = component.render(aid, host);
  const paths = parsePaths(rendered);
  if (paths.length !== aid.config.edges.length) {
    const renderedClasses = allElements(rendered).map(node => `${node.tagName.toLowerCase()}.${node.attributes.class || ''}:${node.textContent}`).join(' | ');
    throw new Error(`edge count ${paths.length} != ${aid.config.edges.length}; rendered=${renderedClasses}`);
  }
  const overlaps = overlappingSegments(paths);
  if (overlaps.length) throw new Error(`near-collinear overlap: ${JSON.stringify(overlaps)}`);
  const penetrations = nodePenetrations(rendered, paths);
  if (penetrations.length) throw new Error(`route crosses unrelated node: ${JSON.stringify(penetrations)}`);
  return {edgeCount: paths.length, overlaps: overlaps.length, penetrations: penetrations.length};
}

const results = [];
for (const name of ['chara', 'feedback', 'parallel', 'sequential']) {
  const aid = loadPaper(name);
  for (const width of [804, 278, 600, 992, 1024]) results.push({name, width, ...renderAt(aid, width)});
}
process.stdout.write(JSON.stringify({ok: true, results}, null, 2) + '\n');
