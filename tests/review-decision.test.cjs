const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createDecision({ decisionBlocked = false, approvalBlocked = false } = {}) {
  const listeners = new Map();
  const button = { disabled: false, textContent: '' };
  const label = { textContent: '' };
  const note = { required: false };
  const actions = ['approve', 'send_back', 'query'].map(value => ({ value, checked: false }));
  const dataset = {};
  if (decisionBlocked) dataset.decisionBlocked = 'true';
  if (approvalBlocked) dataset.approvalBlocked = 'true';
  const form = {
    dataset,
    querySelector(selector) {
      if (selector === '[name="action"]:checked') return actions.find(action => action.checked) || null;
      return {
        '[data-decision-label]': label,
        '[data-decision-submit]': button,
        '[name="note"]': note,
      }[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  const document = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll(selector) { return selector === '[data-decision-form]' ? [form] : []; },
    querySelector() { return null; },
  };
  document.body = document;
  const context = vm.createContext({
    document,
    window: { addEventListener() {}, location: { hash: '' } },
    navigator: {},
    Element: class {},
    queueMicrotask(callback) { callback(); },
    setTimeout() {},
  });
  vm.runInContext(readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'), context);
  const target = { closest: selector => (selector === '[data-decision-form]' || selector === 'form' ? form : null) };
  function fire(name, event = {}) {
    for (const callback of listeners.get(name) || []) callback(event);
  }
  return {
    button,
    initialize: () => fire('DOMContentLoaded'),
    choose(value) {
      for (const action of actions) action.checked = action.value === value;
      fire('change', { target });
    },
  };
}

test('pending prerequisites hold approval and send-back but not a query', () => {
  const page = createDecision({ decisionBlocked: true });
  page.initialize();
  assert.equal(page.button.disabled, true);

  page.choose('send_back');
  assert.equal(page.button.disabled, true);
  assert.equal(page.button.textContent, 'Send back to integrator');

  page.choose('query');
  assert.equal(page.button.disabled, false);
  assert.equal(page.button.textContent, 'Send query');
});

test('unresolved queries hold only approval', () => {
  const page = createDecision({ approvalBlocked: true });
  page.choose('approve');
  assert.equal(page.button.disabled, true);

  page.choose('send_back');
  assert.equal(page.button.disabled, false);
});

test('a decision with nothing pending can be recorded', () => {
  const page = createDecision();
  page.choose('approve');
  assert.equal(page.button.disabled, false);
  assert.equal(page.button.textContent, 'Record approval');
});


// A form that lists reasons offers them in one select; the note is the reviewer's own words.
function createReasons() {
  const listeners = new Map();
  const button = { disabled: false, textContent: '' };
  const label = { textContent: '' };
  const note = { required: false };
  const hint = { textContent: '' };
  const actions = ['approve', 'send_back', 'query'].map(value => ({ value, checked: false }));
  const options = ['', 'Website unreachable or not working', 'Wrong website address', 'Other'].map(value => ({
    value,
    dataset: value === 'Other' ? { noteRequired: '' } : {},
  }));
  const select = {
    options,
    selectedIndex: 0,
    get value() { return options[this.selectedIndex].value; },
  };
  const form = {
    dataset: {},
    querySelector(selector) {
      if (selector === '[name="action"]:checked') return actions.find(action => action.checked) || null;
      return ({
        '[data-decision-label]': label,
        '[data-decision-submit]': button,
        '[name="note"]': note,
        '[data-reason-hint]': hint,
        '[data-reason-select]': select,
      })[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  const document = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll(selector) { return selector === '[data-decision-form]' ? [form] : []; },
    querySelector() { return null; },
  };
  document.body = document;
  const context = vm.createContext({
    document,
    window: { addEventListener() {}, location: { hash: '' } },
    navigator: {},
    Element: class {},
    queueMicrotask(callback) { callback(); },
    setTimeout() {},
  });
  vm.runInContext(readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'), context);
  const target = { closest: selector => (selector === '[data-decision-form]' || selector === 'form' ? form : null) };
  function fire(name) {
    for (const callback of listeners.get(name) || []) callback({ target });
  }
  return {
    button, note, hint,
    choose(value) {
      for (const action of actions) action.checked = action.value === value;
      fire('change');
    },
    pick(value) {
      select.selectedIndex = options.findIndex(option => option.value === value);
      fire('change');
    },
  };
}

test('sending back waits for a reason, which then needs no note', () => {
  const page = createReasons();
  page.choose('send_back');
  assert.equal(page.button.disabled, true);
  assert.match(page.hint.textContent, /Choose a reason/);

  page.pick('Wrong website address');
  assert.equal(page.button.disabled, false);
  assert.equal(page.note.required, false);
  assert.match(page.hint.textContent, /sees this reason above your note/);
});

test('Other asks the reviewer for their own words', () => {
  const page = createReasons();
  page.choose('send_back');
  page.pick('Other');
  assert.equal(page.button.disabled, false);
  assert.equal(page.note.required, true);
});

test('a form with no list to choose from takes the note alone', () => {
  const page = createDecision();
  page.choose('send_back');
  assert.equal(page.button.disabled, false);
});
