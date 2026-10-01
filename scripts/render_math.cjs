// Vendor-independent caller: math input on stdin, strict LaTeX -> MathML on stdout.
const fs = require('node:fs');
const path = require('node:path');
const katex = require(path.join(__dirname, '..', 'assets', 'math', 'katex.min.js'));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const results = input.expressions.map(e => katex.renderToString(e.latex, {
  displayMode: Boolean(e.display), output: 'mathml', throwOnError: true,
  strict: 'error', trust: false, maxExpand: 1000
}));
process.stdout.write(JSON.stringify({ results }));
