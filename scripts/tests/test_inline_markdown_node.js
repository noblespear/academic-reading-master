/* Shared safe-inline regressions, with a text-only DOM and no browser/UI actions.
 * Run: node scripts/tests/test_inline_markdown_node.js
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class TextNode {
  constructor(value) { this.nodeType = 3; this.nodeValue = String(value); this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}
class Element {
  constructor(tag) {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.childNodes = [];
    this.parentNode = null; this.className = ''; this.dataset = {}; this.attributes = {};
    this.classList = {contains: name => this.className.split(/\s+/).includes(name)};
  }
  append(...nodes) {
    for (let node of nodes) {
      if (typeof node === 'string') node = new TextNode(node);
      if (node.parentNode) node.parentNode.childNodes.splice(node.parentNode.childNodes.indexOf(node), 1);
      node.parentNode = this; this.childNodes.push(node);
    }
  }
  set textContent(value) { this.childNodes = []; if (value !== '') this.append(new TextNode(value)); }
  get textContent() { return this.childNodes.map(node => node.textContent).join(''); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener() {}
  matches() { return ['SCRIPT', 'STYLE', 'BUTTON'].includes(this.tagName) || this.attributes['aria-hidden'] === 'true'; }
}
const document = {
  createElement: tag => new Element(tag), createTextNode: value => new TextNode(value),
  createRange: () => ({
    setStart(node, offset) { this.startContainer = node; this.startOffset = offset; },
    setEnd(node, offset) { this.endContainer = node; this.endOffset = offset; },
  }),
};
const file = path.resolve(__dirname, '../../assets/reader-v3.js');
const source = fs.readFileSync(file, 'utf8');
function functionsBetween(start, end) {
  const a = source.indexOf('  function ' + start), b = source.indexOf('  function ' + end, a + 1);
  assert(a >= 0 && b > a, `Cannot find reader helpers: ${start} / ${end}`);
  return source.slice(a, b);
}
const context = {document, URL, location: {href: 'https://reader.test/paper/reader-v3.html'}, state: {view: 'explanation'}, version: 'test-1', sourceView: id => id === 'source' ? 'translation' : undefined};
vm.createContext(context);
// Exercise the shipped helpers directly without running reader application startup.
vm.runInContext([
  functionsBetween('el(', 'button('), functionsBetween('safeURL(', 'safeSVG('),
  functionsBetween('inlineLink(', 'displayField('),
  functionsBetween('canonicalIndex(', 'fragmentFromSelection('),
  functionsBetween('legacyMarkdown(', 'paperAssets('),
  'this.helpers={inline,proseInline,field,legacyMarkdown,canonicalIndex,rangeFor};',
].join('\n'), context, {filename: file});
const {inline, proseInline, field, legacyMarkdown, canonicalIndex, rangeFor} = context.helpers;
function all(root, tag) {
  return root.childNodes.flatMap(node => node.nodeType === 1 ? (node.tagName === tag ? [node] : []).concat(all(node, tag)) : []);
}
function render(text, renderer = inline) { const root = new Element('div'); renderer(root, text); return root; }
function atoms(root) { return all(root, 'SPAN').filter(node => node.classList.contains('math-atom')); }

const literalSamples = [
  '元素S*[j,k] ... map*(j)=k ... S*max[j]',
  '当前 H*、身份缓存 H、map* 与 S*max',
  'α*、β*、S*max、x*y 和 z*w',
  'x*words*tail 和 _x* 以及 2*3*4',
  String.raw`\*literal\*`,
];
const bodyField = (root, text) => field(root, text, 'body', 'text', 'zh', 'prose-field');
const methodField = (root, text) => field(root, text, 'method-node', 'caption', 'zh', 'method-caption');
for (const renderer of [inline, bodyField, methodField, legacyMarkdown]) {
  for (const text of literalSamples) {
    const root = render(text, renderer);
    assert.equal(root.textContent, text);
    assert.equal(canonicalIndex(root).text, text);
    assert.equal(all(root, 'EM').length, 0);
    assert.equal(atoms(root).length, 0, 'Bare symbols must not be guessed into math');
  }
}
for (const [text, expected] of [
  ['*强调*', '强调'], ['*italic*', 'italic'], ['这里*重点*文字', '这里重点文字'],
  ['before *italics* after', 'before italics after'], ['（*重点*）', '（重点）'],
]) {
  const root = render(text);
  assert.equal(root.textContent, expected); assert.equal(all(root, 'EM').length, 1);
}
assert.equal(all(render('**加粗**'), 'STRONG').length, 1);
assert.equal(all(render('* leading* and *trailing *'), 'EM').length, 0);

const formattedMath = render(String.raw`**\(x^* + y\)** and *$z^* + 1$*`);
assert.equal(all(formattedMath, 'STRONG').length, 1);
assert.equal(all(formattedMath, 'EM').length, 1);
assert.deepEqual(atoms(formattedMath).map(node => node.dataset.latex), ['x^* + y', 'z^* + 1']);
assert.equal(canonicalIndex(formattedMath).text, String.raw`\(x^* + y\) and $z^* + 1$`);
const codeAndMath = render(String.raw`*说明 \(S*[j,k]\) 完整*` + ' `S* &lt;s&gt; $x$`');
assert.equal(all(codeAndMath, 'EM').length, 1);
assert.equal(all(codeAndMath, 'CODE')[0].textContent, 'S* &lt;s&gt; $x$');
assert.equal(atoms(codeAndMath).length, 1);
assert.equal(atoms(codeAndMath)[0].dataset.latex, 'S*[j,k]');
for (const text of [String.raw`\(x*y\)`, String.raw`\[x*y\]`, '$x*y$', '$$x*y$$', '$$x*\ny$$']) {
  const root = render(text);
  assert.equal(atoms(root).length, 1); assert.equal(all(root, 'EM').length, 0);
  assert.equal(canonicalIndex(root).text, text);
}
assert.equal(render('$HOME/foo$ and $5 and $10').textContent, '$HOME/foo$ and $5 and $10');

const link = render(String.raw`[说明 \(x*y\)](https://example.test/paper?q=$x$) [来源](#source)`);
assert.equal(all(link, 'A').length, 2); assert.equal(atoms(link).length, 1);
assert.equal(all(link, 'A')[0].href, 'https://example.test/paper?q=$x$');
assert.equal(all(link, 'A')[1].dataset.sourceContentId, 'source');
const hostile = '<img src=x onerror=alert(1)> &lt;script&gt;alert(1)&lt;/script&gt;';
const unsafe = render(hostile + ' [危险](javascript:alert) [危险](data:text/html,test)');
assert.equal(all(unsafe, 'IMG').length + all(unsafe, 'SCRIPT').length + all(unsafe, 'A').length, 0);
assert(unsafe.textContent.startsWith(hostile));

// Entity spelling remains stable for historical anchors, double escaping and code.
const entityText = '&lt;s&gt; &amp;lt;s&amp;gt; &#60;s&#62;';
assert.equal(canonicalIndex(render(entityText)).text, entityText);
assert.equal(all(render('`' + entityText + '`'), 'CODE')[0].textContent, entityText);
const indexed = render(String.raw`🙂前 *重点* \(S*[j,k]\) 后 &lt;s&gt;`);
const expected = String.raw`🙂前 重点 \(S*[j,k]\) 后 &lt;s&gt;`;
assert.equal(canonicalIndex(indexed).text, expected);
const start = expected.indexOf('重点'), end = expected.indexOf(' 后');
const range = rangeFor(indexed, start, end);
assert.equal(range.startContainer.nodeValue, '重点'); assert.equal(range.startOffset, 0);
assert.equal(range.endContainer, indexed);
assert.equal(range.endOffset, indexed.childNodes.indexOf(atoms(indexed)[0]) + 1);
assert.equal(rangeFor(indexed, 0, expected.length).endOffset, indexed.childNodes.at(-1).nodeValue.length);
assert.equal(rangeFor(indexed, -1, 2), null);
console.log(JSON.stringify({ok: true, checks: ['literal symbol stars in prose and legacy answers', 'Chinese and English emphasis', 'protected code and LaTeX inside emphasis', 'safe source and external links', 'HTML and entity literals', 'canonical UTF-16 offsets and ranges']}));
