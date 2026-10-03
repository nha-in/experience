const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// The WASA audit date and the expiry it derives, as the form renders them: the
// expiry is found through the form, and the page finds the audit date the
// moment it loads.
function audit({ expiry = '', dataset = {} } = {}) {
  const target = { value: expiry, dataset };
  const form = { querySelectorAll: () => [target] };
  const source = {
    value: '',
    dataset: { autofillTarget: 'wasa_valid_until', autofillYears: '1' },
  };
  source.closest = selector => {
    if (selector === 'form') return form;
    if (selector === '[data-autofill-target]') return source;
    return null;
  };
  return { source, target };
}

// project.js listens on the document, so loading the page and changing the
// audit date on it are the whole of the interface under test.
function page(source) {
  const listeners = new Map();
  const document = {
    addEventListener: (name, callback) => {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll: selector =>
      selector === '[data-autofill-target]' ? [source] : [],
    querySelector: () => null,
    getElementById: () => null,
  };
  document.body = document;
  vm.runInNewContext(
    readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'),
    {
      document,
      window: { addEventListener: () => {}, location: { hash: '' } },
      navigator: {},
      Element: class {},
      setTimeout() {},
    },
  );
  const fire = name => {
    for (const callback of listeners.get(name) || []) callback({ target: source });
  };
  return { load: () => fire('DOMContentLoaded'), change: () => fire('change') };
}

test('an audit date derives the expiry a year on, ending the day before', () => {
  const { source, target } = audit();
  source.value = '2026-05-10';
  page(source).change();
  assert.equal(target.value, '2027-05-09');
  assert.equal(target.dataset.autofilled, 'true');
});

test('an expiry saved earlier survives the page it is drawn on', () => {
  // A certificate may run for less than the year the arithmetic assumes, and
  // drawing the form again must not quietly extend what it states.
  const { source, target } = audit({ expiry: '2027-03-13' });
  source.value = '2026-09-02';
  page(source).load();
  assert.equal(target.value, '2027-03-13');
});

test('an empty expiry is filled as soon as the page is drawn', () => {
  const { source, target } = audit();
  source.value = '2026-09-02';
  page(source).load();
  assert.equal(target.value, '2027-09-01');
});

test('an expiry saved as its audit date gave it still follows a corrected audit date', () => {
  // A draft saves the derived expiry like any other answer, so only its value
  // says nobody set it by hand.
  const { source, target } = audit({ expiry: '2027-09-01' });
  source.value = '2026-09-02';
  const drawn = page(source);
  drawn.load();
  source.value = '2026-09-10';
  drawn.change();
  assert.equal(target.value, '2027-09-09');
  assert.equal(target.dataset.autofilled, 'true');
});

test('a correction to the audit date keeps the expiry the integrator gave', () => {
  // The certificate may state another period; the page notes the difference
  // beside the field rather than moving their date.
  const { source, target } = audit({ expiry: '2027-03-13' });
  source.value = '2026-05-10';
  page(source).change();
  assert.equal(target.value, '2027-03-13');
});

test('a correction to the audit date moves an expiry it derived', () => {
  const { source, target } = audit({ expiry: '2027-03-13', dataset: { autofilled: 'true' } });
  source.value = '2026-05-10';
  page(source).change();
  assert.equal(target.value, '2027-05-09');
});

test('clearing the audit date clears the expiry that followed it', () => {
  const { source, target } = audit({ expiry: '2027-05-09', dataset: { autofilled: 'true' } });
  page(source).change();
  assert.equal(target.value, '');
  assert.equal(target.dataset.autofilled, 'false');
});

test('a leap day audit still ends on a real date', () => {
  const { source, target } = audit();
  source.value = '2024-02-29';
  page(source).change();
  assert.equal(target.value, '2025-02-28');
});
