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
  const bulkForms = [];
  const document = {
    addEventListener: on,
    querySelectorAll: selector => selector === '[data-product-bulk-form]' ? bulkForms : [],
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
    bulkForms,
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

test('both bulk decisions require the shared note', () => {
  const browser = page();
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
    querySelector: selector => selector === '[name="note"]' ? note : null,
  };
  const button = { form, value: 'reject' };
  const target = {
    closest: selector => selector === '[data-product-bulk-form] [data-bulk-submit]'
      ? button : null,
  };
  assert.equal(browser.fire('click', target).prevented, true);
  assert.equal(note.required, true);
  assert.match(note.validityMessage, /Enter the review/);
  button.value = 'approve';
  assert.equal(browser.fire('click', target).prevented, true);
  assert.equal(note.required, true);
  assert.match(note.validityMessage, /Enter the review/);
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

function decisionForm({ action = 'approve', held = [], reasons = [] } = {}) {
  const choices = ['approve', 'reject'].map(value => ({
    value,
    checked: value === action,
    disabled: held.includes(value),
  }));
  const button = { disabled: false, textContent: '' };
  const controls = { hidden: false };
  const selects = reasons.map(value => ({ value }));
  const form = {
    querySelector: selector => ({
      '[name="action"]:checked': choices.find(choice => choice.checked),
      '[data-bulk-submit]': button,
    })[selector] ?? null,
    querySelectorAll: selector => ({
      '[data-reject-controls]': [controls],
      '[data-bulk-reason]': selects,
    })[selector] ?? [],
  };
  const target = { closest: selector => selector === '[data-product-bulk-form]' ? form : null };
  return {
    button,
    controls,
    form,
    selects,
    target,
    choose(value) {
      for (const choice of choices) choice.checked = choice.value === value;
    },
  };
}

test('accepting asks for no reason, and the button says so', () => {
  const browser = page();
  const { button, controls, form } = decisionForm({ reasons: [''] });
  browser.bulkForms.push(form);
  browser.fire('DOMContentLoaded');
  assert.equal(controls.hidden, true);
  assert.equal(button.textContent, 'Accept all');
  assert.equal(button.disabled, false);
});

test('rejecting shows the reasons and waits for one from every list', () => {
  const browser = page();
  const page_ = decisionForm({ reasons: ['', ''] });
  browser.bulkForms.push(page_.form);
  page_.choose('reject');
  browser.fire('change', page_.target);
  assert.equal(page_.controls.hidden, false);
  assert.equal(page_.button.textContent, 'Reject all');
  assert.equal(page_.button.disabled, true);
  page_.selects[0].value = 'Incomplete documentation';
  browser.fire('change', page_.target);
  assert.equal(page_.button.disabled, true);
  page_.selects[1].value = 'Wrong website address';
  browser.fire('change', page_.target);
  assert.equal(page_.button.disabled, false);
});

test('switching back to accept needs no reason again', () => {
  const browser = page();
  const page_ = decisionForm({ action: 'reject', reasons: [''] });
  page_.choose('approve');
  browser.fire('change', page_.target);
  assert.equal(page_.controls.hidden, true);
  assert.equal(page_.button.disabled, false);
});

test('a form that lists no reasons leaves rejecting to the note', () => {
  const browser = page();
  const { button, target } = decisionForm({ action: 'reject' });
  browser.fire('change', target);
  assert.equal(button.disabled, false);
});

test('a reason does not release a decision that something else holds', () => {
  const browser = page();
  const { button, target } = decisionForm({ action: 'reject', held: ['reject'], reasons: ['Other'] });
  browser.fire('change', target);
  assert.equal(button.disabled, true);
});
