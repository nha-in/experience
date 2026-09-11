const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Like Django's CheckboxSelectMultiple, individual choices have valid native
// validity even when a required group has no selected option.
function createPage({ autoApprove = false, approvedUpdate = false, draft = true, submit = true } = {}) {
  const listeners = new Map();
  const tasks = [];
  const controls = [];
  const groups = [];
  const document = {
    activeElement: null,
    hidden: false,
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll(selector) { return selector === '[data-review-form]' ? [form] : []; },
    querySelector() { return null; },
    getElementById(id) { return id === 'evidence-form' ? form : null; },
  };
  document.body = document;
  const button = { disabled: false };
  const reason = { textContent: '' };
  const jump = { hidden: true, closest: selector => selector === 'form' ? form : null };
  const continueForm = {
    dataset: { continueForm: 'evidence-form' },
    closest: selector => selector === '[data-continue-form]' ? continueForm : null,
  };
  const form = {
    dataset: { autoApprove: String(autoApprove), approvedUpdates: String(approvedUpdate) },
    parentElement: null,
    closest: () => null,
    matches: () => false,
    querySelector(selector) {
      return {
        '[data-request-submit]': submit ? button : null,
        '[data-submit-reason]': reason,
        '[data-submit-missing]': jump,
        '[name="intent"][value="draft"]:not(:disabled)': draft ? {} : null,
      }[selector] || null;
    },
    querySelectorAll(selector) {
      if (selector === 'input, select, textarea') return controls;
      if (selector === '[data-required-checkbox-group]') return groups;
      return [];
    },
  };
  function group(name, { checked = false, hidden = false, disabled = false } = {}) {
    const result = {
      dataset: { requiredCheckboxGroup: name },
      hidden, disabled, parentElement: form,
      querySelectorAll: () => [input],
    };
    const input = {
      id: `id_${name}`, name, checked, disabled: false, validity: { valid: true },
      parentElement: result, order: controls.length,
      matches(selector) {
        if (selector === ':disabled') return this.disabled || result.disabled;
        return selector.includes('input');
      },
      closest(selector) {
        if (selector === '[hidden]') return result.hidden ? result : null;
        if (selector === '[data-review-form]' || selector === 'form') return form;
        return null;
      },
      focus() { document.activeElement = this; },
      scrollIntoView() {},
      compareDocumentPosition(other) { return this.order > other.order ? 2 : 4; },
    };
    controls.push(input);
    groups.push(result);
    return { group: result, input };
  }
  const window = { addEventListener() {}, location: { hash: '' } };
  const context = vm.createContext({
    document,
    window,
    navigator: {},
    Element: class {},
    Node: { DOCUMENT_POSITION_PRECEDING: 2 },
    queueMicrotask(callback) { tasks.push(callback); },
    setTimeout(callback) { tasks.push(callback); },
  });
  for (const filename of ['project.js', 'portal-ux.js']) {
    vm.runInContext(readFileSync(join(__dirname, '../ohc_experience/static/js', filename), 'utf8'), context);
  }
  function fire(name, target = document, event = {}) {
    for (const callback of listeners.get(name) || []) callback({ ...event, target });
    while (tasks.length) tasks.shift()();
  }
  return {
    form, button, reason, jump, group, document, window,
    initialize: () => fire('DOMContentLoaded'),
    change: input => fire('change', input),
    clickJump: () => fire('click', { closest: selector => selector === '[data-submit-missing]' ? jump : null }),
    clickContinue: () => fire('click', { closest: selector => selector === '[data-continue-form]' ? continueForm : null }, { preventDefault() {} }),
  };
}

test('required UHI groups block recording until each group has a choice', () => {
  const page = createPage({ autoApprove: true });
  const role = page.group('uhi_role');
  const services = page.group('uhi_services');
  page.initialize();
  assert.equal(page.button.disabled, true);
  assert.equal(page.reason.textContent, '2 fields need attention before you can record participation.');
  assert.equal(page.jump.hidden, false);
  page.clickJump();
  assert.equal(page.document.activeElement, role.input);

  page.clickContinue();
  assert.equal(page.document.activeElement, role.input);
  assert.equal(page.window.location.hash, 'id_uhi_role');

  role.input.checked = true;
  page.change(role.input);
  assert.equal(page.button.disabled, true);
  page.clickJump();
  assert.equal(page.document.activeElement, services.input);
  page.clickContinue();
  assert.equal(page.document.activeElement, services.input);
  assert.equal(page.window.location.hash, 'id_uhi_services');

  services.input.checked = true;
  page.change(services.input);
  assert.equal(page.button.disabled, false);
  assert.equal(page.reason.textContent, 'All required fields are complete. Ready to record participation.');
  assert.equal(page.jump.hidden, true);
});

test('hidden and disabled required groups do not block submission', () => {
  const page = createPage();
  const conditional = page.group('conditional', { hidden: true });
  page.group('disabled', { disabled: true });
  page.initialize();
  assert.equal(page.button.disabled, false);
  assert.match(page.reason.textContent, /Ready to request review/);

  conditional.group.hidden = false;
  page.change(conditional.input);
  assert.equal(page.button.disabled, true);
  assert.match(page.reason.textContent, /^1 field needs attention/);
  conditional.group.hidden = true;
  page.change(conditional.input);
  assert.equal(page.button.disabled, false);
});

test('approved participation uses update copy', () => {
  const page = createPage({ autoApprove: true, approvedUpdate: true });
  page.group('uhi_role', { checked: true });
  page.initialize();
  assert.equal(page.reason.textContent, 'All required fields are complete. Ready to submit your update.');
});

test('readiness follows conditional visibility updates from the same change event', () => {
  const page = createPage();
  const controller = page.group('controller', { checked: true });
  const conditional = page.group('conditional', { hidden: true });
  page.initialize();
  assert.equal(page.button.disabled, false);
  page.document.addEventListener('change', () => { conditional.group.hidden = false; });
  page.change(controller.input);
  assert.equal(page.button.disabled, true);
  assert.match(page.reason.textContent, /^1 field needs attention/);
});

test('blocked forms keep the server-rendered pending notice', () => {
  const page = createPage({ submit: false });
  page.reason.textContent = 'Required approvals are pending.';
  page.initialize();
  assert.equal(page.reason.textContent, 'Required approvals are pending.');
});
