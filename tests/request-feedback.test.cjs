const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/portal-ux.js'), 'utf8');

// Just enough DOM for a request's life: elements whose dataset is their data-*
// attributes, and selectors of one compound each, with :not() and :has().
class Element {
  constructor(tagName, attributes = {}, children = []) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this.textContent = '';
    const attribute = name => `data-${name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`;
    this.dataset = new Proxy({}, {
      get: (_, name) => this.getAttribute(attribute(name)) ?? undefined,
      set: (_, name, value) => {
        this.setAttribute(attribute(name), value);
        return true;
      },
      deleteProperty: (_, name) => {
        this.removeAttribute(attribute(name));
        return true;
      },
      has: (_, name) => this.hasAttribute(attribute(name)),
    });
    for (const [name, value] of Object.entries(attributes)) this.setAttribute(name, value);
    children.forEach(child => this.append(child));
  }
  get id() { return this.getAttribute('id') || ''; }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) { this.toggleAttribute('hidden', value); }
  get disabled() { return this.hasAttribute('disabled'); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  toggleAttribute(name, force = !this.hasAttribute(name)) {
    if (force) this.setAttribute(name, '');
    else this.removeAttribute(name);
    return force;
  }
  append(child) {
    child.parentElement = this;
    this.children.push(child);
  }
  * descendants() {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }
  matches(selector) {
    return selector.split(',').some(compound => this.matchesCompound(compound.trim()));
  }
  matchesCompound(selector) {
    const pseudo = [];
    const compound = selector.replace(/:(not|has)\(([^()]*)\)/g, (_, kind, inner) => {
      pseudo.push([kind, inner]);
      return '';
    });
    const tag = compound.match(/^[a-z]*/)[0];
    const attributes = [...compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return (!tag || this.tagName === tag.toUpperCase())
      && attributes.every(([, name, value]) => (value === undefined ? this.hasAttribute(name) : this.getAttribute(name) === value))
      && pseudo.every(([kind, inner]) => (kind === 'not' ? !this.matches(inner) : this.querySelectorAll(inner).length > 0));
  }
  querySelectorAll(selector) { return [...this.descendants()].filter(node => node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
}

// The signed-in shell around one request: the toaster's failure toast, the
// main column a boosted link loads into, and a form that saves.
function createPage() {
  const listeners = new Map();
  const message = new Element('p', { role: 'alert', 'data-request-error': '' });
  const dismiss = new Element('button', { type: 'button', 'data-dismiss-request': '' });
  const toast = new Element('div', { id: 'request-feedback', hidden: '' }, [message, dismiss]);
  const link = new Element('a', { href: '/assess/queue/' });
  const form = new Element('form', { method: 'post' }, [
    new Element('input', { type: 'hidden', name: 'expected' }),
    new Element('input', { type: 'text', name: 'client_id' }),
    new Element('button', { type: 'submit', name: 'intent', value: 'save' }),
  ]);
  const main = new Element('main', { id: 'main-content' }, [toast, link, form]);
  const body = new Element('body', {}, [main]);
  const document = {
    body,
    activeElement: body,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    querySelectorAll: selector => body.querySelectorAll(selector),
    querySelector: selector => body.querySelector(selector),
    getElementById: id => body.querySelector(`[id="${id}"]`),
  };
  const fire = (type, properties = {}) => {
    const event = {
      type,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopImmediatePropagation() {},
      ...properties,
    };
    for (const listener of listeners.get(type) || []) listener(event);
  };
  vm.runInNewContext(script, {
    document,
    window: { addEventListener() {}, confirm: () => true, location: { hash: '' } },
    history: { replaceState() {} },
    matchMedia: () => ({ matches: false }),
    navigator: {},
    CSS: { escape: value => value },
    Node: { DOCUMENT_POSITION_PRECEDING: 2 },
  });
  fire('DOMContentLoaded');

  // One request through htmx's events, from the element that made it to
  // what came back: a status and a body, or no response at all (status 0).
  // Answers whether the part of the page it loads into showed it was busy.
  const send = ({ elt, target = main, verb = 'get', status, response = '' }) => {
    const xhr = { status, responseText: response };
    const requestConfig = { verb };
    fire('htmx:beforeRequest', { detail: { elt, target, xhr, requestConfig } });
    const loading = target.hasAttribute('data-request-loading') && target.getAttribute('aria-busy') === 'true';
    if (status === 0) {
      fire('htmx:afterRequest', { detail: { elt, target, xhr, requestConfig, error: 'htmx:afterRequest' } });
      fire('htmx:sendError', { detail: { elt, target, xhr, requestConfig, error: 'htmx:sendError' } });
    } else {
      fire('htmx:afterRequest', { detail: { elt, target, xhr, requestConfig, failed: status >= 400 } });
    }
    return loading;
  };
  const click = target => fire('click', { target });
  return { main, toast, message, dismiss, link, form, send, click };
}

test('the part of the page a load replaces is marked busy until it answers', () => {
  const page = createPage();
  assert.equal(page.send({ elt: page.link, status: 200 }), true);
  assert.equal(page.main.hasAttribute('data-request-loading'), false);
  assert.equal(page.main.hasAttribute('aria-busy'), false);

  assert.equal(page.send({ elt: page.form, verb: 'post', status: 200 }), false, 'a save keeps the page as it is; its button shows the wait');
});
