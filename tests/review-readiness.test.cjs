const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Like Django's CheckboxSelectMultiple, individual choices have valid native
// validity even when a required group has no selected option.
function createPage({
  autoApprove = false, approvedUpdate = false, draft = true, submit = true, currentMilestoneCode = '', reducedMotion = false,
} = {}) {
  const listeners = new Map();
  const tasks = [];
  const controls = [];
  const groups = [];
  const readinessLists = [];
  const fieldsets = new Map();
  const choices = [];
  const testingDates = [];
  const steps = [];
  const submitLabel = { textContent: 'Original submit label' };
  const selectionSummary = { textContent: '' };
  const document = {
    activeElement: null,
    hidden: false,
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll(selector) {
      if (selector === '[data-review-form]') return [form];
      if (selector === '[data-next-step]') return steps;
      if (selector === '[data-readiness-form]') return readinessLists;
      return [];
    },
    querySelector() { return null; },
    getElementById(id) {
      if (id === form.id) return form;
      if (id === actions.id) return actions;
      return fieldsets.get(id) || controls.find(input => input.id === id) || null;
    },
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
    id: 'evidence-form',
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
      if (selector === '[data-autofill-target]') return controls.filter(input => input.dataset?.autofillTarget);
      const name = selector.match(/^\[name="([^"]+)"\]$/)?.[1];
      if (name) return controls.filter(input => input.name === name);
      return [];
    },
  };
  // Enough of an element for a jump to land on: it takes focus, remembers how
  // it was scrolled to, and carries the attributes the page flags it with.
  function element(properties) {
    const attributes = new Set();
    return {
      parentElement: form,
      matches: () => false,
      closest: () => null,
      hasAttribute: name => attributes.has(name),
      setAttribute: name => { attributes.add(name); },
      removeAttribute: name => { attributes.delete(name); },
      toggleAttribute(name, force = !attributes.has(name)) {
        if (force) attributes.add(name);
        else attributes.delete(name);
        return force;
      },
      focus() { document.activeElement = this; },
      scrollIntoView(options) { this.scrolled = { ...options }; },
      ...properties,
    };
  }
  // The row of submit buttons, where Continue goes once nothing is missing.
  const actions = element({
    id: 'evidence-actions',
    closest: selector => (selector === '.ui-form-section, .ui-form-actions' ? actions : null),
  });
  // The "Next up" panel beside the form, first drawn by the server.
  function nextStep(name = '') {
    const label = { textContent: name };
    const step = element({
      dataset: { nextStep: form.id },
      label,
      querySelector: selector => (selector === '[data-next-step-name]' ? label : null),
    });
    steps.push(step);
    return step;
  }
  function group(name, { checked = false, hidden = false, disabled = false, legend = name } = {}) {
    // Each group sits in its own form section, headed by a legend.
    const section = element({
      tagName: 'FIELDSET',
      querySelector: selector => (selector === ':scope > legend' ? { textContent: `\n  ${legend}\n` } : null),
    });
    const result = {
      id: `id_${name}`,
      dataset: { requiredCheckboxGroup: name },
      hidden, disabled, parentElement: section,
      querySelectorAll: () => [input],
      querySelector: selector => (selector === 'input:checked' && input.checked ? input : null),
      matches: selector => selector === '[data-required-checkbox-group]',
      closest: selector => (selector === '.ui-form-section' ? section : null),
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
        if (selector === '.ui-form-section' || selector === '.ui-form-section, .ui-form-actions') return section;
        return null;
      },
      focus() { document.activeElement = this; },
      scrollIntoView(options) { this.scrolled = { ...options }; },
      compareDocumentPosition(other) { return this.order > other.order ? 2 : 4; },
    };
    controls.push(input);
    groups.push(result);
    return { group: result, input, section };
  }
  function date(name, {
    value = '', min = '', max = '', required = false, disabled = false, parent = form, dataset = {},
  } = {}) {
    const input = {
      name, value, min, max, required, disabled, parentElement: parent, dataset: { ...dataset },
      get validity() {
        return { valid: this.disabled || ((!this.required || Boolean(this.value))
          && (!this.value || ((!this.min || this.value >= this.min) && (!this.max || this.value <= this.max)))) };
      },
      matches(selector) {
        return selector === ':disabled' ? this.disabled : selector.includes('input');
      },
      closest(selector) {
        if (selector === '[hidden]') return parent.hidden ? parent : null;
        if (selector === '[data-autofilled]') return 'autofilled' in input.dataset ? input : null;
        if (selector === '[data-autofill-target]') return 'autofillTarget' in input.dataset ? input : null;
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
  // The saved-progress checklist. Like Django, it names a checkbox group by the
  // id on the group's fieldset rather than on any one of its boxes.
  function readiness(...items) {
    for (const item of items) fieldsets.set(item.id, item);
    const row = { dataset: { readinessFields: items.map(item => item.id).join(',') }, querySelector: () => null };
    readinessLists.push({
      dataset: { readinessForm: form.id },
      querySelectorAll: selector => (selector === '[data-readiness-item]' ? [row] : []),
    });
  }
  const window = { addEventListener() {}, location: { hash: '' } };
  // Replacing the URL moves its hash without the jump that setting one makes.
  const history = {
    state: null,
    replaceState(state, title, url) {
      this.state = state;
      window.location.hash = new URL(url, 'https://portal.test/').hash;
    },
  };
  const context = vm.createContext({
    document,
    window,
    history,
    matchMedia: query => ({ matches: reducedMotion && query === '(prefers-reduced-motion: reduce)' }),
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
    form, button, reason, jump, actions, group, date, nextStep, readiness, document, window, milestone, milestoneDates,
    submitLabel, selectionSummary,
    initialize: () => fire('DOMContentLoaded'),
    change: input => fire('change', input),
    input: input => fire('input', input),
    clickJump: () => fire('click', { closest: selector => selector === '[data-submit-missing]' ? jump : null }),
    clickContinue: () => fire('click', { closest: selector => selector === '[data-continue-form]' ? continueForm : null }, { preventDefault() {} }),
    // A link in the readiness checklist, to the field it names. Reports whether
    // the page kept the browser from following it.
    clickChecklist(fieldId) {
      let prevented = false;
      const link = { hash: `#${fieldId}` };
      fire('click', { closest: selector => (selector === '[data-readiness-label]' ? link : null) }, {
        preventDefault() { prevented = true; },
      });
      return prevented;
    },
    endAnimation: (target, animationName) => fire('animationend', target, { animationName }),
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
  assert.equal(page.window.location.hash, '#id_uhi_role');

  role.input.checked = true;
  page.change(role.input);
  assert.equal(page.button.disabled, true);
  page.clickJump();
  assert.equal(page.document.activeElement, services.input);
  page.clickContinue();
  assert.equal(page.document.activeElement, services.input);
  assert.equal(page.window.location.hash, '#id_uhi_services');

  services.input.checked = true;
  page.change(services.input);
  assert.equal(page.button.disabled, false);
  // Complete says nothing: the enabled button is the message.
  assert.equal(page.reason.textContent, '');
  assert.equal(page.jump.hidden, true);
});

test('an unsaved UHI section stays marked until each group has a choice', () => {
  const page = createPage({ autoApprove: true });
  const role = page.group('uhi_role');
  const services = page.group('uhi_services');
  page.readiness(role.group, services.group);
  page.initialize();

  page.change(role.input);
  assert.equal(role.section.hasAttribute('data-missing'), true);

  role.input.checked = true;
  page.change(role.input);
  assert.equal(role.section.hasAttribute('data-missing'), true);

  services.input.checked = true;
  page.change(services.input);
  assert.equal(role.section.hasAttribute('data-missing'), false);
});

test('Continue on a finished form goes to the submit buttons', () => {
  const page = createPage({ autoApprove: true });
  page.group('uhi_role', { checked: true });
  page.initialize();

  page.clickContinue();
  assert.equal(page.document.activeElement, page.actions);
  assert.equal(page.window.location.hash, '#evidence-actions');
  assert.equal(page.actions.hasAttribute('data-flash'), true);
});

test('a jump glides to its field unless motion is reduced, and flashes its section once', () => {
  for (const reducedMotion of [false, true]) {
    const page = createPage({ reducedMotion });
    const role = page.group('uhi_role');
    page.initialize();

    page.clickJump();
    assert.deepEqual(role.input.scrolled, { block: 'center', behavior: reducedMotion ? 'instant' : 'smooth' });
    assert.equal(role.section.hasAttribute('data-flash'), true);

    // Only the flash's own animation ending clears it.
    page.endAnimation(role.section, 'ui-fade');
    assert.equal(role.section.hasAttribute('data-flash'), true);
    page.endAnimation(role.section, 'ui-flash');
    assert.equal(role.section.hasAttribute('data-flash'), false);
  }
});

test('a readiness checklist link jumps to its field in place of the browser', () => {
  const page = createPage();
  page.group('uhi_role');
  const services = page.group('uhi_services');
  page.initialize();

  assert.equal(page.clickChecklist('id_uhi_services'), true);
  assert.equal(page.document.activeElement, services.input);
  assert.equal(page.window.location.hash, '#id_uhi_services');
  assert.equal(services.section.hasAttribute('data-flash'), true);
});

test('the next step follows the first section still missing, then gives way to submitting', () => {
  const page = createPage();
  const role = page.group('uhi_role', { legend: 'Role' });
  const services = page.group('uhi_services', { legend: 'Services offered' });
  const step = page.nextStep('Role');
  page.initialize();

  role.input.checked = true;
  page.change(role.input);
  assert.equal(step.label.textContent, 'Services offered');
  assert.equal(step.hasAttribute('data-complete'), false);

  services.input.checked = true;
  page.change(services.input);
  assert.equal(step.hasAttribute('data-complete'), true);

  role.input.checked = false;
  page.change(role.input);
  assert.equal(step.hasAttribute('data-complete'), false);
  assert.equal(step.label.textContent, 'Role');
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
const OVER_VALIDITY = 'A WASA certificate runs for at most a year. The expiry date cannot be more than a year after the audit date.';

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
    // What the form renders: the wording clean() refuses an expired date with,
    // and one that outruns the year its audit buys.
    dataset: { ...expiry.dataset, expiredMessage: EXPIRED, overValidityMessage: OVER_VALIDITY },
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

test('an expiry more than a year after the audit date is refused beside its field', () => {
  const today = '2026-09-18';
  const page = createPage();
  const audit = page.date('wasa_date', {
    value: '2026-09-16', max: today, dataset: { autofillTarget: 'wasa_valid_until', autofillYears: '1' },
  });
  // Read off the certificate, so no audit date rewrites it.
  const expiry = page.date('wasa_valid_until', {
    value: '2027-09-15', min: today, required: true, dataset: { documentRead: 'true' },
  });
  const field = expiryField(page, expiry);
  page.initialize();

  // The ceiling is the audit's anniversary.
  assert.equal(expiry.max, '2027-09-16');
  assert.deepEqual(field.errors(), []);
  assert.equal(page.button.disabled, false);

  // An earlier audit brings the ceiling below the certificate's own expiry.
  audit.value = '2026-09-10';
  page.change(audit);
  assert.equal(expiry.value, '2027-09-15');
  assert.equal(expiry.max, '2027-09-10');
  assert.deepEqual(field.errors(), [OVER_VALIDITY]);
  assert.equal(expiry.getAttribute('aria-invalid'), 'true');
  assert.equal(page.button.disabled, true);

  // Said once, however many times the field changes.
  page.change(audit);
  page.input(expiry);
  assert.deepEqual(field.errors(), [OVER_VALIDITY]);

  // A lapsed expiry says why in the other sentence, never both.
  expiry.value = '2026-01-01';
  page.change(expiry);
  assert.deepEqual(field.errors(), [EXPIRED]);

  expiry.value = '2027-09-10';
  page.change(expiry);
  assert.deepEqual(field.errors(), []);
  assert.equal(expiry.getAttribute('aria-invalid'), null);
  assert.equal(page.button.disabled, false);

  // A 29 February audit ends its year on the 28th.
  audit.value = '2024-02-29';
  page.change(audit);
  assert.equal(expiry.max, '2025-02-28');

  // With no audit date there is no ceiling to outrun.
  audit.value = '';
  page.change(audit);
  assert.equal(expiry.max, '');
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

test('selecting an extra milestone starts it on these dates and preserves its own when deselected', () => {
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
  assert.equal(dates.start.value, '2026-09-01');
  assert.equal(dates.end.value, '2026-09-05');
  assert.equal(page.button.disabled, false);

  for (const [input, value] of [[dates.start, '2026-09-10'], [dates.end, '2026-09-12']]) {
    page.document.activeElement = input;
    input.value = value;
    page.input(input);
    page.change(input);
  }
  page.document.activeElement = null;
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

test("extra milestones' dates follow these dates until the integrator changes them", () => {
  const page = createPage({ currentMilestoneCode: 'M1' });
  const start = page.date('start_date', { max: '2026-09-18' });
  const end = page.date('end_date', { max: '2026-09-18' });
  page.milestone('M2', '2').checked = true;
  page.milestone('M3', '3');
  const dates2 = page.milestoneDates('2');
  const dates3 = page.milestoneDates('3');
  page.initialize();
  assert.equal(dates2.start.value, '');
  assert.equal(page.button.disabled, true);
  assert.match(page.reason.textContent, /^2 fields need attention/);

  start.value = '2026-09-01';
  page.change(start);
  end.value = '2026-09-05';
  page.change(end);
  assert.equal(dates2.start.value, '2026-09-01');
  assert.equal(dates2.end.value, '2026-09-05');
  assert.equal(dates2.end.min, '2026-09-01');
  assert.equal(dates3.start.value, '2026-09-01', 'a milestone not chosen yet is ready once it is');
  assert.equal(page.button.disabled, false);

  page.document.activeElement = dates2.end;
  dates2.end.value = '2026-09-07';
  page.input(dates2.end);
  page.change(dates2.end);
  page.document.activeElement = null;
  start.value = '2026-09-02';
  page.change(start);
  end.value = '2026-09-06';
  page.change(end);
  assert.equal(dates2.start.value, '2026-09-02');
  assert.equal(dates2.end.min, '2026-09-02');
  assert.equal(dates2.end.value, '2026-09-07', 'a date the integrator changed stays theirs');
  assert.equal(dates3.end.value, '2026-09-06');
});

test('a date typed halfway into an extra milestone is never overwritten', () => {
  // Until every segment is filled, a date field's value reads as empty.
  const page = createPage({ currentMilestoneCode: 'M1' });
  page.date('start_date', { value: '2026-09-01', max: '2026-09-18' });
  page.date('end_date', { value: '2026-09-05', max: '2026-09-18' });
  page.milestone('M2', '2').checked = true;
  const dates = page.milestoneDates('2', { startValue: '2026-09-03' });
  page.initialize();
  assert.equal(dates.start.value, '2026-09-03', 'a date that arrived with the page stays');

  page.document.activeElement = dates.start;
  dates.start.value = '';
  page.input(dates.start);
  page.change(dates.start);
  assert.equal(dates.start.value, '');
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
  page.document.activeElement = dates2.start;
  dates2.start.value = '';
  page.input(dates2.start);
  assert.equal(dates2.end.min, '');
  assert.equal(dates3.end.min, '2026-09-15');
  assert.equal(demo.min, '2026-09-18');
  assert.equal(page.button.disabled, true, 'M2 still needs its own start date');
});

test('typing a date never rewrites the earliest date of the field being typed', () => {
  // Chrome rebuilds a date field whenever its min is assigned, even to the value
  // it already holds, and the rebuild wipes a date typed halfway.
  const page = createPage({ currentMilestoneCode: 'M1' });
  page.date('start_date', { value: '2026-09-01', max: '2026-09-18' });
  const end = page.date('end_date', { max: '2026-09-18' });
  const demo = page.date('tentative_demo_date', { min: '2026-09-18' });
  page.milestone('M2', '2').checked = true;
  const dates = page.milestoneDates('2', { startValue: '2026-09-10' });
  page.initialize();

  for (const [field, typed, min] of [
    [end, '2026-09-05', '2026-09-01'],
    [demo, '2026-09-20', '2026-09-18'],
    [dates.end, '2026-09-12', '2026-09-10'],
  ]) {
    let value = field.min;
    let writes = 0;
    Object.defineProperty(field, 'min', { get: () => value, set: next => { writes += 1; value = next; } });
    page.document.activeElement = field;
    // Day and month first. From the year's first digit the date is complete, and
    // each digit fires input and change.
    for (const year of ['0002', '0020', '0202', '2026']) {
      field.value = `${year}${typed.slice(4)}`;
      page.input(field);
      page.change(field);
    }
    assert.equal(field.value, typed);
    assert.equal(field.min, min);
    assert.equal(writes, 0, `${field.name} had its minimum rewritten while it was typed`);
  }
});
