const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/searchable-select.js'), 'utf8');

// Just enough DOM for the combobox: elements with attributes and bubbling events,
// mutation records for observed subtrees, and a <select> whose value follows its
// options the way a browser's does.
class FakeEvent {
  constructor(type, init = {}) {
    Object.assign(this, { bubbles: false }, init, { type, defaultPrevented: false, stopped: false });
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}

class Element {
  constructor(page, tagName, properties = {}) {
    this.page = page;
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this.listeners = new Map();
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    Object.assign(this, properties);
  }
  get id() { return this.getAttribute('id') || ''; }
  set id(value) { this.setAttribute('id', value); }
  get className() { return this.getAttribute('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get htmlFor() { return this.getAttribute('for') || ''; }
  set htmlFor(value) { this.setAttribute('for', value); }
  set tabIndex(value) { this.setAttribute('tabindex', value); }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) { this.toggleAttribute('hidden', value); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(value) { this.toggleAttribute('disabled', value); }
  get required() { return this.hasAttribute('required'); }
  set required(value) { this.toggleAttribute('required', value); }
  get classList() {
    const names = () => this.className.split(' ').filter(Boolean);
    return {
      add: name => { if (!names().includes(name)) this.className = [...names(), name].join(' '); },
      remove: name => { if (names().includes(name)) this.className = names().filter(other => other !== name).join(' '); },
      contains: name => names().includes(name),
    };
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    this.page.mutated(this);
  }
  removeAttribute(name) { if (this.attributes.delete(name)) this.page.mutated(this); }
  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, '');
    else this.removeAttribute(name);
  }
  append(...nodes) { this.insert(this.children.length, nodes); }
  replaceChildren(...nodes) {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
    this.append(...nodes);
  }
  before(...nodes) { this.parentElement.insert(this.parentElement.children.indexOf(this), nodes); }
  after(...nodes) { this.parentElement.insert(this.parentElement.children.indexOf(this) + 1, nodes); }
  insert(index, nodes) {
    nodes.forEach(node => node.remove());
    this.children.splice(index, 0, ...nodes);
    nodes.forEach(node => { node.parentElement = this; });
    this.page.mutated(this);
  }
  remove() {
    if (!this.parentElement) return;
    this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
    this.page.mutated(this.parentElement);
    this.parentElement = null;
  }
  contains(node) {
    for (let current = node; current; current = current.parentElement) if (current === this) return true;
    return false;
  }
  * descendants() {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }
  // Compound selectors only: a tag, classes, [attribute] or [attribute="value"], :not(...).
  matches(selector) {
    const negated = [];
    const compound = selector.replace(/:not\(([^)]*)\)/g, (_, inner) => {
      negated.push(inner);
      return '';
    });
    const tag = compound.match(/^\w*/)[0];
    const classes = [...compound.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
    const attributes = [...compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return (!tag || this.tagName === tag.toUpperCase()) &&
      classes.every(name => this.classList.contains(name)) &&
      attributes.every(([, name, value]) => (value === undefined ? this.hasAttribute(name) : this.getAttribute(name) === value)) &&
      negated.every(inner => !this.matches(inner));
  }
  querySelectorAll(selector) { return [...this.descendants()].filter(node => node.matches(selector)); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    if (listeners.includes(listener)) listeners.splice(listeners.indexOf(listener), 1);
  }
  dispatchEvent(event) {
    event.target ??= this;
    for (let node = this; node && !event.stopped; node = event.bubbles ? node.parentElement : null) {
      [...(node.listeners.get(event.type) || [])].forEach(listener => listener(event));
    }
    return !event.defaultPrevented;
  }
  focus() {
    const { document } = this.page;
    if (this.disabled || document.activeElement === this) return;
    const previous = document.activeElement;
    document.activeElement = this;
    previous?.dispatchEvent(new FakeEvent('blur', { relatedTarget: this }));
    this.dispatchEvent(new FakeEvent('focus'));
  }
  blur() {
    if (this.page.document.activeElement !== this) return;
    this.page.document.activeElement = null;
    this.dispatchEvent(new FakeEvent('blur', { relatedTarget: null }));
  }
  select() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 100, bottom: 140, left: 20, width: 300, height: 40 }; }
}

class Select extends Element {
  get options() { return this.children.flatMap(child => child.tagName === 'OPTGROUP' ? child.children : [child]); }
  get selectedIndex() { return this.options.findIndex(option => option.selected); }
  get value() { return this.options[this.selectedIndex]?.value ?? ''; }
  set value(value) { this.options.forEach(option => { option.isSelected = option.value === value; }); }
  get labels() { return this.page.document.querySelectorAll('label').filter(label => label.htmlFor === this.id); }
  get multiple() { return this.hasAttribute('multiple'); }
  get size() { return Number(this.getAttribute('size')) || 0; }
  get form() { return null; }
}

class Option extends Element {
  get selected() { return Boolean(this.isSelected); }
  set selected(value) {
    const group = this.parentElement;
    const select = group.tagName === 'OPTGROUP' ? group.parentElement : group;
    if (value) select.options.forEach(option => { option.isSelected = false; });
    this.isSelected = value;
  }
}

const agencies = [
  ['', 'Select an audit agency'],
  ['Army Cyber Group', 'Army Cyber Group'],
  ['M/s Aujas Cybersecurity Limited', 'M/s Aujas Cybersecurity Limited'],
  ['M/s Certbar Security Private Limited', 'M/s Certbar Security Private Limited'],
  // Legacy values carry trailing spaces the browser trims from the label.
  ['M/s CMS IT Services Pvt. Ltd. ', 'M/s CMS IT Services Pvt. Ltd.'],
  ['M/s TÜV-SÜD South Asia Pvt. Ltd.', 'M/s TÜV-SÜD South Asia Pvt. Ltd.'],
];

function group(label, options, properties = {}) {
  return { label, options, properties };
}

function createPage(choices = agencies, { selected = '', others = [] } = {}) {
  const page = { observers: [], pending: new Set(), timers: [], events: [] };
  page.mutated = node => page.observers.forEach(observer => {
    if (observer.target.contains(node)) page.pending.add(observer);
  });
  const document = new Element(page, '#document', {
    activeElement: null,
    readyState: 'interactive',
    documentElement: { clientHeight: 800, clientWidth: 1200 },
  });
  document.createElement = tagName => new Element(page, tagName);
  page.document = document;
  const body = new Element(page, 'body');
  const label = new Element(page, 'label', { htmlFor: 'id_agency', textContent: 'WASA audit agency name' });
  const wrapper = new Element(page, 'div', { className: 'ui-select-wrapper' });
  const select = new Select(page, 'select', { id: 'id_agency', className: 'ui-select' });
  const outside = new Element(page, 'button');
  select.setAttribute('aria-describedby', 'id_agency_helptext');
  const option = ([value, text, properties = {}]) => new Option(page, 'option', { value, label: text, ...properties });
  choices.forEach(choice => {
    if (Array.isArray(choice)) {
      select.append(option(choice));
      return;
    }
    const optgroup = new Element(page, 'optgroup', { label: choice.label, ...choice.properties });
    optgroup.append(...choice.options.map(option));
    select.append(optgroup);
  });
  select.value = selected;
  wrapper.append(select);
  body.append(label, wrapper, outside);
  others.forEach(({ className = 'ui-select', attributes = {} }) => {
    const other = new Select(page, 'select', { className });
    Object.entries(attributes).forEach(([name, value]) => other.setAttribute(name, value));
    other.append(new Option(page, 'option', { value: 'only', label: 'Only option', isSelected: true }));
    const otherWrapper = new Element(page, 'div', { className: 'ui-select-wrapper' });
    otherWrapper.append(other);
    body.append(otherWrapper);
  });
  document.append(body);
  select.addEventListener('input', () => page.events.push('input'));
  select.addEventListener('change', () => page.events.push('change'));

  vm.runInContext(script, vm.createContext({
    document,
    window: { addEventListener() {}, removeEventListener() {} },
    CSS: { escape: value => value },
    Event: FakeEvent,
    MutationObserver: class {
      constructor(callback) { this.callback = callback; }
      observe(target) {
        this.target = target;
        page.observers.push(this);
      }
    },
    setTimeout(callback, delay) { return page.timers.push({ callback, delay }); },
    clearTimeout(id) { if (page.timers[id - 1]) page.timers[id - 1].callback = () => {}; },
  }));

  const input = wrapper.querySelectorAll('input')[0];
  const listbox = wrapper.querySelectorAll('div[role="listbox"]')[0];
  const optionElements = () => listbox.querySelectorAll('div[role="option"]');
  return Object.assign(page, {
    body, label, wrapper, select, outside, input, listbox,
    popup: listbox.parentElement,
    status: wrapper.querySelectorAll('div[role="status"]')[0],
    optionElements,
    shown: () => optionElements().map(element => element.textContent),
    rows: () => listbox.children.map(element => element.textContent),
    active: () => optionElements().find(element => element.id === input.getAttribute('aria-activedescendant'))?.textContent,
    type(text) {
      input.focus();
      input.value = text;
      input.dispatchEvent(new FakeEvent('input', { bubbles: true }));
    },
    press(key, properties = {}) {
      const event = new FakeEvent('keydown', { bubbles: true, key, ...properties });
      input.dispatchEvent(event);
      return event;
    },
    click(element) {
      const press = new FakeEvent('mousedown', { bubbles: true });
      element.dispatchEvent(press);
      element.dispatchEvent(new FakeEvent('click', { bubbles: true }));
      return press;
    },
    flush() {
      const observers = [...page.pending];
      page.pending.clear();
      observers.forEach(observer => observer.callback([]));
    },
    runTimers() { page.timers.splice(0).forEach(timer => timer.callback()); },
  });
}

test('the select stays in the form and its label moves to the search box', () => {
  const page = createPage();

  assert.equal(page.wrapper.querySelectorAll('select')[0], page.select);
  assert.equal(page.select.getAttribute('aria-hidden'), 'true');
  assert.equal(page.select.getAttribute('tabindex'), '-1');
  assert.equal(page.select.className, 'ui-select ui-combobox__native');
  assert.equal(page.input.className, 'ui-select ui-combobox__input');
  assert.equal(page.input.getAttribute('role'), 'combobox');
  assert.equal(page.input.getAttribute('aria-describedby'), 'id_agency_helptext');
  assert.equal(page.label.htmlFor, page.input.id);
  assert.equal(page.listbox.getAttribute('aria-labelledby'), page.label.id);
  assert.equal(page.input.value, '');
  assert.equal(page.input.placeholder, 'Select an audit agency');

  // portal-ux.js focuses the select to point at a missing answer.
  page.select.focus();
  assert.equal(page.document.activeElement, page.input);
});

test('every careui select is searchable unless it keeps a plain dropdown', () => {
  const page = createPage(agencies, {
    others: [
      { attributes: { 'data-native-select': '' } },
      { attributes: { multiple: '' } },
      { className: '' },
    ],
  });

  assert.equal(page.body.querySelectorAll('input').length, 1);
  assert.equal(page.wrapper.querySelectorAll('input').length, 1);
});

test('loading more content never enhances a select twice', () => {
  const page = createPage();

  page.document.dispatchEvent(new FakeEvent('htmx:load', { detail: { elt: page.body } }));

  assert.equal(page.wrapper.querySelectorAll('input').length, 1);
});

test('forms hear the chosen value, never the typing', () => {
  const page = createPage();
  const heard = [];
  ['input', 'change'].forEach(type => page.body.addEventListener(type, event => heard.push(`${type} ${event.target.tagName}`)));

  page.type('army');
  page.input.dispatchEvent(new FakeEvent('change', { bubbles: true }));
  assert.deepEqual(heard, []);

  page.press('Enter');
  assert.deepEqual(heard, ['input SELECT', 'change SELECT']);
});

test('the search box is sized by the longest option, as the select was', () => {
  const page = createPage();

  assert.equal(page.input.size, 'M/s Certbar Security Private Limited'.length);
});

test('typing matches every word anywhere in a label, ignoring case and accents', () => {
  const page = createPage();

  page.type('sec');
  assert.deepEqual(page.shown(), ['M/s Certbar Security Private Limited', 'M/s Aujas Cybersecurity Limited']);
  assert.equal(page.active(), 'M/s Certbar Security Private Limited');
  assert.equal(page.input.getAttribute('aria-expanded'), 'true');

  page.type('tuv   SUD');
  assert.deepEqual(page.shown(), ['M/s TÜV-SÜD South Asia Pvt. Ltd.']);

  page.type('pvt ltd');
  assert.deepEqual(page.shown(), ['M/s CMS IT Services Pvt. Ltd.', 'M/s TÜV-SÜD South Asia Pvt. Ltd.']);
  page.runTimers();
  assert.equal(page.status.textContent, '2 matches');

  page.type('not an agency');
  assert.deepEqual(page.shown(), []);
  assert.equal(page.popup.querySelectorAll('p')[0].hidden, false);
  page.runTimers();
  assert.equal(page.status.textContent, 'No matches');
});

test('choosing an option writes its exact value back with input and change events', () => {
  const page = createPage();

  page.type('cms');
  const press = page.click(page.optionElements()[0]);

  assert.equal(press.defaultPrevented, true);
  assert.equal(page.select.value, 'M/s CMS IT Services Pvt. Ltd. ');
  assert.deepEqual(page.events, ['input', 'change']);
  assert.equal(page.input.value, 'M/s CMS IT Services Pvt. Ltd.');
  assert.equal(page.input.getAttribute('aria-expanded'), 'false');
  assert.equal(page.popup.hidden, true);

  page.click(page.input);
  page.click(page.optionElements().find(element => element.textContent === 'M/s CMS IT Services Pvt. Ltd.'));
  assert.deepEqual(page.events, ['input', 'change']);
});

test('the keyboard opens at the current choice, and Enter chooses without submitting', () => {
  const page = createPage(agencies, { selected: 'M/s Certbar Security Private Limited' });

  page.press('ArrowDown');
  assert.equal(page.active(), 'M/s Certbar Security Private Limited');
  page.press('ArrowDown');
  page.press('ArrowUp');
  page.press('ArrowUp');
  assert.equal(page.active(), 'M/s Aujas Cybersecurity Limited');
  page.press('PageDown');
  assert.equal(page.active(), 'M/s TÜV-SÜD South Asia Pvt. Ltd.');
  page.press('ArrowDown');
  assert.equal(page.active(), 'Select an audit agency');

  page.press('ArrowDown');
  assert.equal(page.press('Enter').defaultPrevented, true);
  assert.equal(page.select.value, 'Army Cyber Group');
  assert.equal(page.input.value, 'Army Cyber Group');

  page.type('tuv');
  const escape = page.press('Escape');
  assert.equal(escape.defaultPrevented, true);
  assert.equal(escape.stopped, true);
  assert.equal(page.input.value, 'Army Cyber Group');
  assert.equal(page.popup.hidden, true);

  assert.equal(page.press('Enter').defaultPrevented, true);
  assert.equal(page.select.value, 'Army Cyber Group');
});

test('leaving the field changes the choice only when the typed text leaves no doubt', () => {
  const page = createPage(agencies, { selected: 'Army Cyber Group' });

  page.type('limited');
  page.input.blur();
  assert.equal(page.select.value, 'Army Cyber Group');
  assert.equal(page.input.value, 'Army Cyber Group');

  page.type('tuv');
  page.input.blur();
  assert.equal(page.select.value, 'M/s TÜV-SÜD South Asia Pvt. Ltd.');

  page.type('m/s certbar security private limited');
  page.outside.dispatchEvent(new FakeEvent('pointerdown', { bubbles: true }));
  assert.equal(page.select.value, 'M/s Certbar Security Private Limited');

  page.type('');
  page.input.blur();
  assert.equal(page.select.value, '');
  assert.equal(page.input.value, '');

  page.type('pvt ltd');
  page.press('ArrowDown');
  page.press('Tab');
  assert.equal(page.select.value, 'M/s TÜV-SÜD South Asia Pvt. Ltd.');
});

test('it follows what other scripts do to the select', () => {
  const page = createPage();

  page.select.value = 'Army Cyber Group';
  page.select.dispatchEvent(new FakeEvent('change', { bubbles: true }));
  assert.equal(page.input.value, 'Army Cyber Group');

  // organisation-form.js hides an option and moves the value off it without an event.
  page.select.options[1].hidden = true;
  page.select.value = '';
  page.flush();
  assert.equal(page.input.value, '');
  page.click(page.input);
  assert.equal(page.shown().includes('Army Cyber Group'), false);
  page.press('Escape');

  // project.js switches the WASA fields off when the product's certificate is reused.
  page.select.disabled = true;
  page.select.required = true;
  page.flush();
  assert.equal(page.input.disabled, true);
  assert.equal(page.input.getAttribute('aria-required'), 'true');
});

test('grouped options are listed without a heading and disabled ones are skipped', () => {
  const page = createPage([
    ['', 'All items'],
    group('Requests', [['request-address', 'Address change'], ['request-contact', 'Contact change', { disabled: true }]]),
    group('Tracks', [['M1', 'M1'], ['M2', 'M2']]),
    group('Retired', [['M0', 'M0']], { hidden: true }),
  ]);

  page.press('ArrowDown');
  assert.deepEqual(page.rows(), ['All items', 'Address change', 'Contact change', 'M1', 'M2']);
  assert.equal(page.active(), 'All items');
  page.press('ArrowDown');
  page.press('ArrowDown');
  assert.equal(page.active(), 'M1');
  page.press('ArrowUp');
  page.press('ArrowUp');
  page.press('ArrowUp');
  assert.equal(page.active(), 'M2');

  page.type('change');
  assert.deepEqual(page.rows(), ['Address change', 'Contact change']);
  assert.equal(page.optionElements()[1].getAttribute('aria-disabled'), 'true');
  assert.equal(page.active(), 'Address change');
});
