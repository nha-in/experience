const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function page() {
  const listeners = new Map();
  const targets = new Map();
  const on = (name, callback) => {
    if (!listeners.has(name)) listeners.set(name, []);
    listeners.get(name).push(callback);
  };
  const document = {
    addEventListener: on,
    querySelectorAll: () => [],
    querySelector: () => null,
    getElementById: id => targets.get(id),
  };
  document.body = document;
  const window = { addEventListener: on, location: { hash: '' } };
  vm.runInNewContext(
    readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'),
    { document, window, navigator: {}, Element: class {}, setTimeout() {} },
  );
  return {
    window,
    targets,
    fire(name, target, extra = {}) {
      const event = {
        target,
        prevented: false,
        preventDefault() { this.prevented = true; },
        stopPropagation() {},
        ...extra,
      };
      for (const callback of listeners.get(name) || []) callback(event);
      return event;
    },
  };
}

test('direct and HTMX product links reveal the requested collapsed evidence', () => {
  const browser = page();
  // A row outside the Approved section, so it has no wrapper to open.
  const panel = { open: false, closest: () => null };
  let scrolls = 0;
  browser.targets.set('review-12', {
    closest: () => panel,
    scrollIntoView() { scrolls++; },
  });
  browser.window.location.hash = '#review-12';
  browser.fire('DOMContentLoaded');
  assert.equal(panel.open, true);
  panel.open = false;
  browser.fire('htmx:afterSettle');
  assert.equal(panel.open, true);
  assert.equal(scrolls, 2);
});

test('clicking the current fragment reopens the row, including nested queries', () => {
  const browser = page();
  const panel = { open: false, closest: () => null };
  browser.targets.set('queries-12', {
    closest: () => panel,
    scrollIntoView() {},
  });
  const link = { getAttribute: () => '#queries-12' };
  browser.fire('click', {
    closest: selector => selector === 'a[href^="#"]' ? link : null,
  });
  assert.equal(panel.open, true);
});

test('a row filed under Approved reviews opens that section as well', () => {
  const browser = page();
  const approved = { open: false };
  const panel = { open: false, closest: () => approved };
  browser.targets.set('review-12', {
    closest: () => panel,
    scrollIntoView() {},
  });
  browser.window.location.hash = '#review-12';
  browser.fire('DOMContentLoaded');
  assert.equal(approved.open, true);
  assert.equal(panel.open, true);
});

test('both bulk decisions open and require the shared note', () => {
  const browser = page();
  const notePanel = { open: false };
  const note = {
    value: '',
    required: false,
    minLength: 10,
    validityMessage: '',
    focus() {},
    reportValidity() {},
    setCustomValidity(message) { this.validityMessage = message; },
  };
  const form = {
    querySelector: selector => selector === '[name="note"]' ? note : notePanel,
  };
  const button = { form, value: 'reject' };
  const target = {
    closest: selector => selector === '[data-product-bulk-form] button[name="action"]'
      ? button : null,
  };
  assert.equal(browser.fire('click', target).prevented, true);
  assert.equal(notePanel.open, true);
  assert.equal(note.required, true);
  assert.match(note.validityMessage, /shared decision note/);
  notePanel.open = false;
  button.value = 'approve';
  assert.equal(browser.fire('click', target).prevented, true);
  assert.equal(notePanel.open, true);
  assert.equal(note.required, true);
  assert.match(note.validityMessage, /shared decision note/);
  note.value = 'Too short';
  assert.equal(browser.fire('click', target).prevented, true);
  assert.match(note.validityMessage, /at least 10 characters/);
  note.value = 'Every milestone matches the submitted evidence.';
  assert.equal(browser.fire('click', target).prevented, false);
  assert.equal(note.validityMessage, '');
  button.value = 'reject';
  note.value = 'Correct the submitted documents.';
  assert.equal(browser.fire('click', target).prevented, false);
});
