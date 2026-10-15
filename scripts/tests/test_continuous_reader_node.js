/* Focused rendering policy regressions: node scripts/tests/test_continuous_reader_node.js */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assets = path.resolve(__dirname, '../../assets');
class Element {
  constructor(tag, cls, value) {
    this.tagName = tag.toUpperCase(); this.className = cls || ''; this.children = [];
    this.dataset = {}; this.attributes = {}; this.style = {}; this._text = value === undefined ? '' : String(value);
    this.classList = {
      contains: n => this.className.split(/\s+/).includes(n),
      add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).filter(Boolean).concat(names))].join(' '); },
      remove: n => { this.className = this.className.split(/\s+/).filter(x => x !== n).join(' '); },
      toggle: (n, force) => { const on = force === undefined ? !this.classList.contains(n) : force; this.classList[on ? 'add' : 'remove'](n); return on; },
    };
  }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  append(...nodes) { for (const n of nodes) { n.parentNode = this; this.children.push(n); } }
  prepend(...nodes) { for (const n of nodes) n.parentNode = this; this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  get lastChild() { return this.children.at(-1); }
  get childElementCount() { return this.children.length; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  addEventListener() {}
  getBoundingClientRect() { return {width: 700, height: 400}; }
  closest(tag) { for (let n = this; n; n = n.parentNode) if (n.tagName.toLowerCase() === tag) return n; return null; }
}
const el = (tag, cls, text) => new Element(tag, cls, text);
const walk = n => [n, ...n.children.flatMap(walk)];
const withClass = (n, cls) => walk(n).filter(n => n.classList.contains(cls));
const source = fs.readFileSync(path.join(assets, 'reader-v3.js'), 'utf8');
function between(start, end) {
  const a = source.indexOf('  function ' + start), b = source.indexOf(end, a + 1);
  assert(a >= 0 && b > a, start); return source.slice(a, b);
}
let createdSources = 0;
const ids = new Map();
const context = {
  window: {}, document: {querySelectorAll: () => [], createElementNS: (_ns, tag) => el(tag)},
  el, button: (text, cls) => el('button', cls, text),
  inline: (n, s) => { n.textContent = String(s); }, proseInline: (n, s) => { n.textContent = String(s); },
  field: (p, value, id, key, language, cls) => { const n = el('div', 'anchor-field ' + (cls || ''), value); Object.assign(n.dataset, {contentId: id, field: key, language}); p.append(n); return n; },
  sourceButton: ref => { createdSources++; return el('button', 'source-link', 'p.' + ref.page); },
  sourceLinks: refs => { const n = el('details', 'block-sources'); refs.forEach(ref => n.append(context.sourceButton(ref))); return n; },
  safeURL: x => x, safeSVG: () => null, mathAtom: x => el('span', 'math-atom', x),
  scheduleOverlay() {}, typeset() {}, newBlockDraft() {}, originalExplanations() {}, legacyTable: () => false,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  state: {view: 'overview', plainLanguage: false}, bodyFirst: true, version: 'same',
  D: {}, translationJob: {}, VIEW_NAMES: {overview: '概览', explanation: '详解', translation: '翻译'},
  $: id => { if (!ids.has(id)) ids.set(id, el('div')); return ids.get(id); },
  scroll: {querySelector: () => el('div')}, content: el('main'),
};
context.sections = view => view === 'translation' ? context.D.sections : context.D[view].sections;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(assets, 'content-renderer.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(assets, 'learning-components.js'), 'utf8'), context);
vm.runInContext([
  between('displayField(', '  function legacyTable('),
  between('paperRenderContext(', '  state.studyState'),
  between('renderBlock(', '  function learningText('),
  between('renderView(', '  function renderTranslationPanel('),
].join('\n'), context);
const host = {
  ...context, catalog: {aids: []}, studyState: {}, onStudyChange() {}, onLayout() {},
  renderLegacy: context.renderLegacyBlock,
};
context.renderer = context.window.ContentRenderer.create(host);
const ref = [{page: 3, content_id: 'unchanged-target'}];
const figure = {id: 'fig', type: 'figure', src: 'assets/fig.svg', caption_zh: '原有短图题', source_refs: ref};
const note = {id: 'stable-note', type: 'quote', text_zh: '补充观察', source_refs: ref};
const body = {id: 'reasoning', type: 'paragraph', text_zh: '图1的主要推理继续在正文。', source_refs: ref};
const aid = {id: 'pipeline', type: 'method_pipeline', source_refs: ref, evidence_kind: 'paper', config: {
  summary: '流程说明', nodes: ['a', 'b'].map((id, i) => ({id, label: id, summary: id, inputs: id, outputs: id, row: 0, column: i})),
  edges: [{id: 'edge', from: 'a', to: 'b', kind: 'flow', label: '传递', flow: '结果', cause: '下一阶段需要'}],
}, fallback: {version: 1, blocks: [{id: 'fallback', type: 'paragraph', text: '说明', source_refs: ref}]}};
const blocks = [body, figure, note, body, {id: 'table', type: 'table', headers: ['列'], rows: [['值']], source_refs: ref}, {...note, id: 'table-note'},
  {id: 'diagram', type: 'diagram', src: 'assets/diagram.svg', source_refs: ref}, {...note, id: 'diagram-note'},
  {id: 'plain-quote', type: 'quote', text: '普通引用'},
  {id: 'details', type: 'details', title: '扩展', blocks: [{id: 'nested', type: 'paragraph', text: '嵌套', source_refs: ref}]},
  {id: 'aid', type: 'learning_aid', aid, source_refs: ref}];
const sections = [{id: 'section', title_cn: '正文', page: 2, source_refs: ref, blocks}, {id: 'only-refs', title_cn: '另一章', source_refs: ref, blocks: [body]}];
context.D = {overview: {sections}, explanation: {sections}, sections};
const snapshot = JSON.stringify(context.D);
for (const view of ['overview', 'explanation']) {
  context.state.view = view; createdSources = 0; context.renderView();
  assert.equal(createdSources, 0, view + ' must not even create source buttons');
  assert.equal(withClass(context.content, 'source-link').length, 0);
  assert.equal(withClass(context.content, 'page-link').length, 0);
  assert.equal(withClass(context.content, 'chapter-sources').length, 0);
  assert.equal(withClass(context.content, 'figure-note').length, 3);
  assert.equal(withClass(context.content, 'figure-note-row').length, 3);
  assert.equal(withClass(context.content, 'figure-with-note').length, 3);
  const renderedNote = walk(context.content).find(n => n.dataset.contentId === 'stable-note');
  assert(renderedNote); assert.equal(renderedNote.dataset.field, undefined); // frame remains the stable block
  assert(walk(renderedNote).some(n => n.dataset.contentId === 'stable-note' && n.dataset.field === 'text_zh'));
  const ordinary = walk(context.content).find(n => n.dataset.richId === 'plain-quote');
  assert(!ordinary.classList.contains('figure-note'));
  assert(!withClass(context.content, 'content-warning').length, 'valid method_pipeline should render normally');
}
context.state.view = 'translation'; createdSources = 0; context.renderView();
assert(createdSources > 0); assert(withClass(context.content, 'page-link').length > 0);
assert(withClass(context.content, 'chapter-sources').length > 0);
assert.equal(withClass(context.content, 'figure-note').length, 0);
assert.equal(JSON.stringify(context.D), snapshot, 'source metadata and original fields must remain intact');
// Standalone rich content (user notes / answers) keeps default sources and quote semantics.
createdSources = 0; const answer = el('div');
context.renderer.renderDocument(answer, {version: 1, blocks: [figure, note, {id: 'answer-aid', type: 'learning_aid', aid}]}, {scope: 'answer'});
assert(createdSources > 0); assert.equal(withClass(answer, 'figure-note').length, 0);
assert(withClass(answer, 'source-link').length > 0);
// Nested rich document receives paper policy while preserving its stable field path.
const nested = el('div'); createdSources = 0;
context.renderer.renderDocument(nested, {version: 1, blocks: [figure, note]}, {scope: 'paper', anchorId: 'parent', showSourceLinks: false, figureNotes: true});
assert.equal(createdSources, 0); assert.equal(withClass(nested, 'figure-note').length, 1);
assert(walk(nested).some(n => n.dataset.contentId === 'parent' && n.dataset.field === '1.text_zh'));
console.log('PASS: overview/explanation omit source DOM; translation/answers preserve citations; adjacent visual quotes retain anchors and plain prose.');
