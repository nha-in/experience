const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(
  join(__dirname, '../ohc_experience/static/js/callback-url-rules.js'),
  'utf8',
);

// The rules are data on the card, so the check under test is pure: no DOM.
function load() {
  const context = {
    document: { addEventListener() {}, querySelectorAll: () => [] },
    window: {},
  };
  context.window.document = context.document;
  vm.createContext(context);
  vm.runInContext(script, context);
  return context.window.callbackUrlRules;
}

const RULES = [
  ['url', '^https://'],
  ['url', '^(?:[a-z]+://)?[^/:]*(?:/|$)'],
  ['path', '^(/[a-z0-9-]+)*$'],
];

function breaks(url) {
  const { met } = load();
  return RULES.filter(([scope, pattern]) => !met(url, scope, pattern)).length;
}

test('a URL the gateway accepts breaks no rule', () => {
  assert.equal(breaks('https://integrator.example/v3/callback'), 0);
  assert.equal(breaks('https://integrator.example'), 0);
});

test('each thing the gateway refuses breaks exactly one rule', () => {
  assert.equal(breaks('https://integrator.example:8443/callback'), 1);
  assert.equal(breaks('https://integrator.example/Callback'), 1);
  assert.equal(breaks('https://integrator.example/abha_callback'), 1);
  assert.equal(breaks('https://integrator.example/callback.php'), 1);
  assert.equal(breaks('https://integrator.example/callback:9000'), 1);
  assert.equal(breaks('https://integrator.example/callback?retry=1'), 1);
  assert.equal(breaks('https://integrator.example/call back'), 1);
});

test('plain http breaks the https rule alone, not the port rule', () => {
  assert.equal(breaks('http://integrator.example/callback'), 1);
});

test('without a scheme the host cannot be told from the path, so both wait', () => {
  assert.equal(breaks('integrator.example/callback'), 2);
});

test('the host name is not read as the part after it', () => {
  assert.equal(breaks('https://Integrator.Example/callback'), 0);
});
