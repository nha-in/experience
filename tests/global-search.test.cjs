const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/global-search.js'), 'utf8');

// Just enough DOM for the search box: elements with attributes, a dataset that
// reads data-* attributes, bubbling events, templates, and the selectors the
// script uses (lists of a tag, #id, [attribute] and [attribute="value"]).
class FakeEvent {
  constructor(type, init = {}) {
    Object.assign(this, { bubbles: false }, init, { type, defaultPrevented: false, stopped: false });
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}

class Element {
  constructor(tagName, attributes = {}, children = []) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map(Object.entries(attributes));
    this.children = [];
    this.parentElement = null;
    this.listeners = new Map();
    this.clicks = 0;
    this.value = '';
    this.textContent = '';
    this.shown = true;
    this.append(...children);
    const element = this;
    this.dataset = new Proxy({}, {
      get: (_, name) => element.getAttribute(`data-${String(name).replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`) ?? undefined,
    });
  }
  get id() { return this.getAttribute('id') || ''; }
  set id(value) { this.setAttribute('id', value); }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) {
    if (value) this.setAttribute('hidden', '');
    else this.removeAttribute('hidden');
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  getClientRects() { return this.shown ? [{}] : []; }
  append(...nodes) {
    nodes.flatMap(node => (node.fragment ? node.children : [node])).forEach(node => {
      node.parentElement = this;
      this.children.push(node);
    });
  }
  replaceChildren(...nodes) {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
    this.append(...nodes);
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
  matches(selector) {
    return selector.split(',').some(part => {
      const compound = part.trim();
      const tag = compound.match(/^\w*/)[0];
      const id = compound.match(/#([\w-]+)/)?.[1];
      const attributes = [...compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
      return (!tag || this.tagName === tag.toUpperCase()) &&
        (!id || this.id === id) &&
        attributes.every(([, name, value]) => (value === undefined ? this.hasAttribute(name) : this.getAttribute(name) === value));
    });
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
  querySelectorAll(selector) { return [...this.descendants()].filter(node => node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
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
  click() {
    this.clicks += 1;
    this.dispatchEvent(new FakeEvent('click', { bubbles: true }));
  }
  scrollIntoView() {}
}

// A row as the template in components/global_search.html draws it.
const row = url => new Element('a', { href: url, 'data-url': url, role: 'option', 'aria-selected': 'false' }, [
  new Element('strong'),
  new Element('em', { 'data-global-search-text': '' }),
]);

function createPage(typed = 'care') {
  const template = new Element('template', { 'data-global-search-rows': '' });
  template.content = {
    cloneNode: () => ({
      fragment: true,
      children: [
        row('/portal/organizations/?field=name'),
        row('/portal/products/?field=product'),
        row('/portal/support/'),
      ],
    }),
  };
  const input = new Element('input', {
    role: 'combobox',
    'aria-controls': 'global-search-options',
    'aria-expanded': 'false',
    'aria-keyshortcuts': '/',
  });
  const listbox = new Element('div', { id: 'global-search-options', role: 'listbox' });
  const popup = new Element('div', { 'data-global-search-popup': '', hidden: '' }, [listbox]);
  const status = new Element('p', { 'data-global-search-status': '' });
  const form = new Element('form', { 'data-global-search': '' }, [input, popup, template, status]);
  const field = new Element('textarea');
  const elsewhere = new Element('a', { href: '/review/queue/' });
  const body = new Element('body', {}, [form, field, elsewhere]);
  const listeners = new Map();
  const page = { input, popup, listbox, status, form, field, elsewhere, processed: [] };
  const document = {
    readyState: 'complete',
    activeElement: null,
    addEventListener(type, listener) { listeners.set(type, [...(listeners.get(type) || []), listener]); },
    removeEventListener(type, listener) { listeners.set(type, (listeners.get(type) || []).filter(other => other !== listener)); },
    querySelectorAll: selector => body.querySelectorAll(selector),
    getElementById: id => body.querySelector(`#${id}`),
  };
  input.focus = () => {
    document.activeElement = input;
    input.dispatchEvent(new FakeEvent('focus'));
  };
  vm.runInContext(script, vm.createContext({
    document,
    window: { location: { origin: 'http://localhost:8003' }, htmx: { process: element => page.processed.push(element) } },
    URL,
    Event: FakeEvent,
  }));
  page.document = document;
  page.type = value => {
    input.value = value;
    input.dispatchEvent(new FakeEvent('input', { bubbles: true }));
  };
  page.press = key => {
    const event = new FakeEvent('keydown', { key, bubbles: true });
    input.dispatchEvent(event);
    return event;
  };
  // A key pressed with the focus elsewhere on the page.
  page.pressOn = (target, key, init = {}) => {
    const event = new FakeEvent('keydown', { key, target, ...init });
    (listeners.get('keydown') || []).forEach(listener => listener(event));
    return event;
  };
  page.pointerDown = target => {
    const event = new FakeEvent('pointerdown', { target });
    (listeners.get('pointerdown') || []).forEach(listener => listener(event));
  };
  page.rows = () => listbox.children;
  page.selected = () => page.rows().filter(item => item.getAttribute('aria-selected') === 'true');
  if (typed) {
    input.focus();
    page.type(typed);
  }
  return page;
}

test('typing offers a row per field, carrying the text, the first highlighted', () => {
  const page = createPage('care');
  const rows = page.rows();

  assert.equal(page.popup.hidden, false);
  assert.equal(page.input.getAttribute('aria-expanded'), 'true');
  assert.deepEqual(rows.map(item => item.id), ['global-search-options-0', 'global-search-options-1', 'global-search-options-2']);
  assert.deepEqual(rows.map(item => item.getAttribute('href')), [
    '/portal/organizations/?field=name&q=care',
    '/portal/products/?field=product&q=care',
    '/portal/support/?q=care',
  ]);
  assert.deepEqual(rows.map(item => item.querySelector('[data-global-search-text]').textContent), ['care', 'care', 'care']);
  assert.deepEqual(page.selected(), [rows[0]]);
  assert.equal(page.input.getAttribute('aria-activedescendant'), 'global-search-options-0');
  assert.deepEqual(page.processed, [page.listbox], 'htmx boosts the new links');
  assert.equal(page.status.textContent, '3 search options');
});

test('more typing carries the new text into the rows, and clearing it closes them', () => {
  const page = createPage('car');
  page.type('care ');

  assert.equal(page.rows()[0].getAttribute('href'), '/portal/organizations/?field=name&q=care');
  page.type('');
  assert.equal(page.popup.hidden, true);
  assert.equal(page.rows().length, 0);
});

test('up and down move through the rows and wrap around', () => {
  const page = createPage('care');
  const rows = page.rows();

  assert.equal(page.press('ArrowDown').defaultPrevented, true);
  assert.deepEqual(page.selected(), [rows[1]]);
  page.press('ArrowDown');
  page.press('ArrowDown');
  assert.deepEqual(page.selected(), [rows[0]]);
  page.press('ArrowUp');
  assert.deepEqual(page.selected(), [rows[2]]);
});

test('Enter opens the highlighted row', () => {
  const page = createPage('care');
  const rows = page.rows();
  page.press('ArrowDown');

  assert.equal(page.press('Enter').defaultPrevented, true);
  assert.deepEqual(rows.map(item => item.clicks), [0, 1, 0]);
  assert.equal(page.popup.hidden, true);
});

test('Enter with the list closed leaves the search to the server, and with no text does nothing', () => {
  const page = createPage('care');
  page.press('Escape');
  assert.equal(page.press('Enter').defaultPrevented, false, 'the form submits');

  page.type('');
  assert.equal(page.press('Enter').defaultPrevented, true);
});

test('Escape closes the list and stops there, so the phone drawer stays open', () => {
  const page = createPage('care');

  const escape = page.press('Escape');
  assert.equal(escape.defaultPrevented, true);
  assert.equal(escape.stopped, true);
  assert.equal(page.popup.hidden, true);
  assert.equal(page.press('Escape').stopped, false, 'a closed list lets Escape through');
});

test('leaving the box or tapping elsewhere closes the list, and focus brings it back', () => {
  const page = createPage('care');
  page.form.dispatchEvent(new FakeEvent('focusout', { relatedTarget: page.elsewhere }));
  assert.equal(page.popup.hidden, true);

  page.input.focus();
  assert.equal(page.popup.hidden, false);
  page.pointerDown(page.elsewhere);
  assert.equal(page.popup.hidden, true);
});

test('pressing a row keeps focus in the box until the click lands', () => {
  const page = createPage('care');
  const press = new FakeEvent('mousedown', { bubbles: true });

  page.rows()[0].dispatchEvent(press);

  assert.equal(press.defaultPrevented, true);
});

test('"/" puts the cursor in the search box, except in a field or with a modifier', () => {
  const page = createPage('');

  assert.equal(page.pressOn(page.field, '/').defaultPrevented, false, 'typed into the field');
  assert.equal(page.pressOn(page.elsewhere, '/', { ctrlKey: true }).defaultPrevented, false);
  assert.equal(page.document.activeElement, null);

  assert.equal(page.pressOn(page.elsewhere, '/').defaultPrevented, true, 'not typed into the box');
  assert.equal(page.document.activeElement, page.input);

  page.input.shown = false;
  page.document.activeElement = null;
  assert.equal(page.pressOn(page.elsewhere, '/').defaultPrevented, false, 'a hidden box is left alone');
});
