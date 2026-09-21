const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Like Django's CheckboxSelectMultiple, individual choices have valid native
// validity even when a required group has no selected option.
function createPage({ autoApprove = false, approvedUpdate = false, draft = true, submit = true, currentMilestoneCode = '' } = {}) {
  const listeners = new Map();
  const tasks = [];
  const controls = [];
  const groups = [];
  const choices = [];
  const testingDates = [];
  const submitLabel = { textContent: 'Original submit label' };
  const selectionSummary = { textContent: '' };
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
    dataset: { autoApprove: String(autoApprove), approvedUpdates: String(approvedUpdate), currentMilestoneCode },
    parentElement: null,
    closest: () => null,
    matches: () => false,
    querySelector(selector) {
      const name = selector.match(/^\[name="([^"]+)"\]$/)?.[1];
      if (name) return controls.find(input => input.name === name) || null;
      return {
        '[data-request-submit]': submit ? button : null,
        '[data-submit-reason]': reason,
        '[data-submit-missing]': jump,
        '[data-submit-label]': submitLabel,
        '[data-milestone-selection-summary]': selectionSummary,
        '[name="intent"][value="draft"]:not(:disabled)': draft ? {} : null,
      }[selector] || null;
    },
    querySelectorAll(selector) {
      if (selector === 'input, select, textarea') return controls;
      if (selector === '[data-required-checkbox-group]') return groups;
      if (selector === '[name="additional_reviews"]') return choices;
      if (selector === '[data-testing-dates]' || selector === '[data-milestone-dates]') return testingDates;
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
  function date(name, { value = '', min = '', max = '', required = false, disabled = false, parent = form } = {}) {
    const input = {
      name, value, min, max, required, disabled, parentElement: parent,
      get validity() {
        return { valid: this.disabled || ((!this.required || Boolean(this.value))
          && (!this.value || ((!this.min || this.value >= this.min) && (!this.max || this.value <= this.max)))) };
      },
      matches(selector) {
        return selector === ':disabled' ? this.disabled : selector.includes('input');
      },
      closest(selector) {
        if (selector === '[hidden]') return parent.hidden ? parent : null;
        return ['[data-review-form]', 'form'].includes(selector) ? form : null;
      },
    };
    controls.push(input);
    return input;
  }
  function milestoneDates(id, { startValue = '', endValue = '', max = '2026-09-18' } = {}) {
    const fields = {
      dataset: { milestoneDates: id },
      hidden: true,
      parentElement: form,
      querySelector: selector => ({ '[data-testing-start]': start, '[data-testing-end]': end })[selector] || null,
      querySelectorAll: () => [start, end],
    };
    const start = date(`milestone_${id}_start_date`, { value: startValue, max, disabled: true, parent: fields });
    const end = date(`milestone_${id}_end_date`, { value: endValue, max, disabled: true, parent: fields });
    testingDates.push(fields);
    return { fields, start, end };
  }
  function milestone(code, id, requires = '') {
    const choice = {
      checked: false,
      disabled: false,
      dataset: { milestoneCode: code, reviewId: id, requires },
      closest: selector => ['[data-review-form]', 'form'].includes(selector) ? form : null,
    };
    choices.push(choice);
    return choice;
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
    form, button, reason, jump, group, date, document, window, milestone, milestoneDates, submitLabel, selectionSummary,
    initialize: () => fire('DOMContentLoaded'),
    change: input => fire('change', input),
    input: input => fire('input', input),
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
  // Complete says nothing: the enabled button is the message.
  assert.equal(page.reason.textContent, '');
  assert.equal(page.jump.hidden, true);
});

test('hidden and disabled required groups do not block submission', () => {
  const page = createPage();
  const conditional = page.group('conditional', { hidden: true });
  page.group('disabled', { disabled: true });
  page.initialize();
  assert.equal(page.button.disabled, false);
  assert.equal(page.reason.textContent, '');

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
  const role = page.group('uhi_role');
  page.initialize();
  assert.equal(page.reason.textContent, '1 field needs attention before you can submit your update.');

  role.input.checked = true;
  page.change(role.input);
  assert.equal(page.reason.textContent, '');
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

test('initializing sandbox dates preserves today as the earliest demo date', () => {
  const today = '2026-09-18';
  for (const endValue of ['', '2026-09-17', today]) {
    const page = createPage();
    page.date('start_date', { value: '2026-09-16', max: today });
    const end = page.date('end_date', { value: endValue, max: today });
    const demo = page.date('tentative_demo_date', { min: today });
    page.initialize();

    assert.equal(end.min, '2026-09-16');
    assert.equal(demo.min, today, `demo minimum after loading end date ${endValue || '(empty)'}`);
  }
});

test('editing and clearing the sandbox end date never allows a past demo date', () => {
  const today = '2026-09-18';
  const page = createPage();
  page.date('start_date', { value: '2026-09-16', max: today });
  const end = page.date('end_date', { value: '2026-09-17', max: today });
  const demo = page.date('tentative_demo_date', { min: today });
  page.initialize();

  for (const fire of [page.input, page.change]) {
    for (const value of ['2026-09-20', '2026-09-17', today, '']) {
      end.value = value;
      fire(end);
      assert.equal(demo.min, value === '2026-09-20' ? value : today);
    }
  }
});

test('sandbox end date follows the start date while retaining its latest allowed date', () => {
  const today = '2026-09-18';
  const page = createPage();
  const start = page.date('start_date', { value: '2026-09-16', max: today });
  const end = page.date('end_date', { value: '2026-09-17', max: today });
  const demo = page.date('tentative_demo_date', { min: today });
  page.initialize();

  for (const value of [today, '2026-09-15', '']) {
    start.value = value;
    page.change(start);
    assert.equal(end.min, value);
    assert.equal(end.max, today);
    assert.equal(start.max, today);
    assert.equal(demo.min, today);
  }
});

const EXPIRED = 'This certificate has expired. Submit a renewed WASA certificate.';

// The expiry's field, as far as the flag reaches into it: the error box the
// server prints after a refused submission, and the spans inside it.
function expiryField(page, expiry, { printed = false } = {}) {
  const byId = new Map();
  const attributes = new Map();
  const node = tagName => ({
    tagName, id: '', className: '', textContent: '', children: [], parentElement: null,
    append(child) {
      child.parentElement = this;
      this.children.push(child);
      if (child.id) byId.set(child.id, child);
    },
    remove() {
      this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      if (this.id) byId.delete(this.id);
    },
  });
  const field = node('DIV');
  field.querySelectorAll = selector => {
    const found = [];
    const walk = parent => parent.children.forEach(child => {
      if (selector === '.ui-error' && child.className === 'ui-error') found.push(child);
      walk(child);
    });
    walk(field);
    return found;
  };
  const closest = expiry.closest;
  Object.assign(expiry, {
    id: 'id_wasa_valid_until',
    // What the form renders: the wording clean() refuses an expired date with.
    dataset: { expiredMessage: EXPIRED },
    closest: selector => (selector === '.ui-field' ? field : closest(selector)),
    getAttribute: name => (attributes.has(name) ? attributes.get(name) : null),
    setAttribute: (name, value) => attributes.set(name, String(value)),
    removeAttribute: name => attributes.delete(name),
  });
  page.document.createElement = tagName => node(tagName.toUpperCase());
  const getElementById = page.document.getElementById;
  page.document.getElementById = id => byId.get(id) || getElementById(id);
  if (printed) {
    const box = page.document.createElement('div');
    box.id = 'id_wasa_valid_until_errors';
    field.append(box);
    const error = page.document.createElement('span');
    error.className = 'ui-error';
    error.textContent = EXPIRED;
    box.append(error);
    expiry.setAttribute('aria-invalid', 'true');
    expiry.setAttribute('aria-describedby', box.id);
  }
  return { errors: () => field.querySelectorAll('.ui-error').map(error => error.textContent) };
}

test('an expired WASA certificate is refused beside its field, however the date arrived', () => {
  const today = '2026-09-18';
  const page = createPage();
  const audit = page.date('wasa_date', { value: '2026-09-16', max: today });
  // The server floors the expiry at today.
  const expiry = page.date('wasa_valid_until', { value: '2027-09-15', min: today, required: true });
  const field = expiryField(page, expiry);
  page.initialize();

  assert.equal(expiry.min, today);
  assert.deepEqual(field.errors(), []);
  assert.equal(page.button.disabled, false);

  // No audit date lowers the floor: the audit itself is capped at today.
  for (const value of ['2024-01-05', '']) {
    audit.value = value;
    page.change(audit);
    assert.equal(expiry.min, today);
  }

  // Typed, picked, or filled in by the certificate reader, which announces its
  // values with the same change event.
  expiry.value = '2025-03-01';
  page.change(expiry);
  assert.deepEqual(field.errors(), [EXPIRED]);
  assert.equal(expiry.getAttribute('aria-invalid'), 'true');
  assert.equal(expiry.getAttribute('aria-describedby'), 'id_wasa_valid_until_errors');
  assert.equal(page.button.disabled, true);
  assert.equal(page.reason.textContent, '1 field needs attention before you can request review.');

  // Said once, however many times the field changes.
  page.change(expiry);
  page.input(expiry);
  assert.deepEqual(field.errors(), [EXPIRED]);

  // A certificate that is still current clears it.
  expiry.value = '2027-01-01';
  page.change(expiry);
  assert.deepEqual(field.errors(), []);
  assert.equal(expiry.getAttribute('aria-invalid'), null);
  assert.equal(expiry.getAttribute('aria-describedby'), '');
  assert.equal(page.button.disabled, false);

  // Expiring today is still current.
  expiry.value = today;
  page.change(expiry);
  assert.deepEqual(field.errors(), []);
});

test('a refused submission keeps one copy of the expiry error, then lets it go', () => {
  const today = '2026-09-18';
  const page = createPage();
  const expiry = page.date('wasa_valid_until', { value: '2025-03-01', min: today });
  const field = expiryField(page, expiry, { printed: true });
  page.initialize();

  assert.deepEqual(field.errors(), [EXPIRED]);

  expiry.value = '2027-01-01';
  page.change(expiry);
  assert.deepEqual(field.errors(), []);
  assert.equal(expiry.getAttribute('aria-invalid'), null);
});

test('a certificate taken from the product is never flagged', () => {
  const today = '2026-09-18';
  const page = createPage();
  const expiry = page.date('wasa_valid_until', { value: '2025-03-01', min: today });
  const field = expiryField(page, expiry);
  page.initialize();
  assert.deepEqual(field.errors(), [EXPIRED]);

  // Choosing the product's certificate disables the upload's own fields.
  expiry.disabled = true;
  page.change(expiry);
  assert.deepEqual(field.errors(), []);
});

test('milestone selection includes prerequisites and names exactly what will be submitted', () => {
  const page = createPage({ currentMilestoneCode: 'M4' });
  const m1 = page.milestone('M1', '1');
  const m2 = page.milestone('M2', '2', '1');
  page.initialize();
  assert.equal(page.submitLabel.textContent, 'Submit M4');
  assert.equal(page.selectionSummary.textContent, 'Only M4 will be submitted.');

  m2.checked = true;
  page.change(m2);
  assert.equal(m1.checked, true);
  assert.equal(page.submitLabel.textContent, 'Submit 3 milestones');
  assert.equal(page.selectionSummary.textContent, 'M4, M1, M2 will be submitted together.');

  m1.checked = false;
  page.change(m1);
  assert.equal(m2.checked, false);
  assert.equal(page.submitLabel.textContent, 'Submit M4');
});

test('milestone selection is restored with its prerequisites after a form error', () => {
  const page = createPage({ currentMilestoneCode: 'M4' });
  const m1 = page.milestone('M1', '1');
  const m2 = page.milestone('M2', '2', '1');
  const m3 = page.milestone('M3', '3', '1 2');
  m3.checked = true;
  page.initialize();
  assert.equal(m1.checked, true);
  assert.equal(m2.checked, true);
  assert.equal(page.submitLabel.textContent, 'Submit 4 milestones');

  m2.checked = false;
  page.change(m2);
  assert.equal(m1.checked, true);
  assert.equal(m3.checked, false);
  assert.equal(page.submitLabel.textContent, 'Submit M4 + M1');
});

test('participation keeps its own submit label', () => {
  const page = createPage({ currentMilestoneCode: 'UHI1', autoApprove: true });
  page.initialize();
  assert.equal(page.submitLabel.textContent, 'Original submit label');
});

test('selecting an extra milestone requires its own dates and preserves them when deselected', () => {
  const page = createPage({ currentMilestoneCode: 'M1' });
  page.date('start_date', { value: '2026-09-01', max: '2026-09-18' });
  page.date('end_date', { value: '2026-09-05', max: '2026-09-18' });
  const m2 = page.milestone('M2', '2');
  const dates = page.milestoneDates('2');
  page.initialize();
  assert.equal(dates.fields.hidden, true);
  assert.equal(dates.start.disabled, true);
  assert.equal(dates.end.required, false);
  assert.equal(page.button.disabled, false);

  m2.checked = true;
  page.change(m2);
  assert.equal(dates.fields.hidden, false);
  assert.equal(dates.start.disabled, false);
  assert.equal(dates.start.required, true);
  assert.equal(dates.end.required, true);
  assert.equal(dates.start.value, '', 'the primary start date must not be copied');
  assert.equal(dates.end.value, '', 'the primary end date must not be copied');
  assert.equal(page.button.disabled, true);
  assert.match(page.reason.textContent, /^2 fields need attention/);

  dates.start.value = '2026-09-10';
  dates.end.value = '2026-09-12';
  page.input(dates.start);
  assert.equal(page.button.disabled, false);
  m2.checked = false;
  page.change(m2);
  assert.equal(dates.fields.hidden, true);
  for (const input of [dates.start, dates.end]) {
    assert.equal(input.disabled, true);
    assert.equal(input.required, false);
  }
  assert.equal(dates.start.value, '2026-09-10');
  assert.equal(dates.end.value, '2026-09-12');
  assert.equal(page.button.disabled, false);

  m2.checked = true;
  page.change(m2);
  assert.equal(dates.start.value, '2026-09-10');
  assert.equal(dates.end.value, '2026-09-12');
  assert.equal(page.button.disabled, false);
});

test('auto-selected prerequisite milestones require dates and deselected dependants release them', () => {
  const page = createPage({ currentMilestoneCode: 'M1' });
  const m2 = page.milestone('M2', '2');
  const m3 = page.milestone('M3', '3', '2');
  const dates2 = page.milestoneDates('2');
  const dates3 = page.milestoneDates('3');
  m3.checked = true;
  page.initialize();
  assert.equal(m2.checked, true);
  for (const dates of [dates2, dates3]) {
    assert.equal(dates.fields.hidden, false);
    assert.equal(dates.start.required, true);
    assert.equal(dates.end.disabled, false);
  }
  assert.equal(page.button.disabled, true);
  assert.match(page.reason.textContent, /^4 fields need attention/);

  dates2.start.value = '2026-09-10';
  dates2.end.value = '2026-09-12';
  page.change(dates2.end);
  assert.equal(page.button.disabled, true);
  assert.match(page.reason.textContent, /^2 fields need attention/);

  m2.checked = false;
  page.change(m2);
  assert.equal(m3.checked, false);
  for (const dates of [dates2, dates3]) {
    assert.equal(dates.fields.hidden, true);
    assert.equal(dates.start.required, false);
    assert.equal(dates.end.disabled, true);
  }
  assert.equal(page.button.disabled, false);
});

test('each selected milestone constrains its end date using only its own start date', () => {
  const page = createPage({ currentMilestoneCode: 'M1' });
  const primaryStart = page.date('start_date', { value: '2026-09-01', max: '2026-09-18' });
  const primaryEnd = page.date('end_date', { value: '2026-09-05', max: '2026-09-18' });
  const demo = page.date('tentative_demo_date', { min: '2026-09-18' });
  page.milestone('M2', '2').checked = true;
  page.milestone('M3', '3').checked = true;
  const dates2 = page.milestoneDates('2', { startValue: '2026-09-10', endValue: '2026-09-12' });
  const dates3 = page.milestoneDates('3', { startValue: '2026-09-15', endValue: '2026-09-14' });
  page.initialize();
  assert.equal(primaryEnd.min, '2026-09-01');
  assert.equal(dates2.end.min, '2026-09-10');
  assert.equal(dates3.end.min, '2026-09-15');
  assert.equal(dates3.end.max, '2026-09-18');
  assert.equal(page.button.disabled, true, 'M3 end precedes its own start');

  dates3.end.value = '2026-09-16';
  page.input(dates3.end);
  assert.equal(page.button.disabled, false);
  primaryStart.value = '2026-09-02';
  page.change(primaryStart);
  assert.equal(dates2.end.min, '2026-09-10');
  assert.equal(dates3.end.min, '2026-09-15');
  dates2.start.value = '';
  page.input(dates2.start);
  assert.equal(dates2.end.min, '');
  assert.equal(dates3.end.min, '2026-09-15');
  assert.equal(demo.min, '2026-09-18');
  assert.equal(page.button.disabled, true, 'M2 still needs its own start date');
});
