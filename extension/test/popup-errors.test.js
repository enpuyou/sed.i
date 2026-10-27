// Run: node --test extension/test/popup-errors.test.js
// popup.js is a DOM-bound script, so pull parseApiError out of its source.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load(file) {
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(/function parseApiError[\s\S]*?\n}\n/);
  assert.ok(m, 'parseApiError not found in ' + file);
  const ctx = {};
  vm.runInNewContext(m[0] + '\nthis.parseApiError = parseApiError;', ctx);
  return ctx.parseApiError;
}

const files = [
  path.join(__dirname, '../popup/popup.js'),
  path.join(__dirname, '../../safari-extension/sed.i/sed.i Extension/Resources/popup/popup.js'),
];

for (const file of files) {
  test(`duplicate save shows a sentence (${path.basename(path.dirname(path.dirname(file)))})`, () => {
    const parse = load(file);
    // Shape the service worker sends for a 409: error is the bare code.
    assert.strictEqual(
      parse('ALREADY_SAVED', { code: 'ALREADY_SAVED', isArchived: false }),
      'Already in your library.',
    );
    assert.strictEqual(
      parse('ALREADY_SAVED', { code: 'ALREADY_SAVED', isArchived: true }),
      'Already in your library (archived). Restore it from your queue.',
    );
  });

  test('other errors are unchanged', () => {
    const parse = load(file);
    assert.strictEqual(parse(undefined), "Couldn't save. Try again.");
    assert.strictEqual(parse('API error 500: boom'), 'boom');
  });
}
