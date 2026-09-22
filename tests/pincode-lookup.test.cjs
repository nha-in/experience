const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/pincode-lookup.js'), 'utf8');

// A small DOM harness keeps the async behavior tests dependency-free.
function createPage(initial = {}) {
  class Element extends EventTarget {
    constructor(value = '') {
      super();
      this.value = value;
      this.attributes = new Map();
      this.children = [];
      this.hidden = false;
      this.textContent = '';
      this.isConnected = true;
    }
    setAttribute(name, value) { this.attributes.set(name, value); }
    removeAttribute(name) { this.attributes.delete(name); }
    replaceChildren(...children) { this.children = children; }
  }
  const input = new Element(initial.pincode || '');
  const state = new Element(initial.state || '');
  const district = new Element(initial.district || '');
  const status = new Element();
  const retry = new Element();
  const field = new Element();
  const form = new Element();
  const document = new Element();
  const window = new Element();
  input.dataset = {
    pincodeLookup: '/organisations/pincode-lookup/',
    offlineLocations: 'pincode_offline_locations',
  };
  // The json_script the widget renders; absent unless a test supplies one.
  const offline = new Element();
  offline.textContent = JSON.stringify(initial.offline || []);
  document.getElementById = id => (initial.offline && id === 'pincode_offline_locations' ? offline : null);
  input.matches = selector => selector === 'input[data-pincode-lookup]';
  input.closest = selector => ({ form, '[data-pincode-field]': field })[selector];
  form.querySelector = selector => ({ '[data-lgd-state]': state, '[data-lgd-district]': district })[selector];
  field.querySelector = selector => ({ '[data-pincode-status]': status, '[data-pincode-retry]': retry })[selector];
  document.querySelectorAll = () => input.isConnected ? [input] : [];
  window.location = { href: 'https://example.test/organisations/profile/' };
  const requests = [];
  const timers = new Map();
  let now = 0;
  let timerId = 0;
  const context = vm.createContext({
    document, window, Event, URL, AbortController,
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    setTimeout(callback, delay) {
      timers.set(++timerId, { callback, due: now + delay });
      return timerId;
    },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
    },
  });
  function runScript() { vm.runInContext(script, context); }
  runScript();
  function tick(duration = 250) {
    now += duration;
    while (true) {
      const next = [...timers].find(([, timer]) => timer.due <= now);
      if (!next) break;
      timers.delete(next[0]);
      next[1].callback();
    }
  }
  function changePincode(value) {
    input.value = value;
    input.dispatchEvent(new Event('input'));
  }
  async function flush() {
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
  }
  async function respond(index, locations, responseStatus = 200) {
    const request = requests[index];
    request.resolve({
      ok: responseStatus === 200,
      status: responseStatus,
      json: async () => ({ pincode: request.url.searchParams.get('pincode'), locations }),
    });
    await flush();
  }
  return { input, state, district, status, retry, field, document, requests, changePincode, tick, respond, flush, runScript };
}

const kerala = { state: 'Kerala', state_code: '32', district: 'Ernakulam', district_code: '595' };
const thrissur = { state: 'Kerala', state_code: '32', district: 'Thrissur', district_code: '594' };
const tamilNadu = { state: 'Tamil Nadu', state_code: '33', district: 'Coimbatore', district_code: '632' };
const values = select => Array.from(select.children, option => option.value);

test('debounces a valid PIN and fills unique names; changing PIN immediately clears locations', async () => {
  const page = createPage();
  page.changePincode('68203');
  page.tick();
  assert.equal(page.requests.length, 0);
  page.changePincode('682030');
  page.tick(249);
  assert.equal(page.requests.length, 0);
  page.tick(1);
  await page.respond(0, [kerala, kerala]);
  assert.equal(page.state.value, 'Kerala');
  assert.equal(page.district.value, 'Ernakulam');
  assert.deepEqual(values(page.state), ['Kerala']);
  assert.deepEqual(values(page.district), ['Ernakulam']);
  assert.equal(page.field.attributes.has('aria-busy'), false);
  page.changePincode('680001');
  assert.equal(page.state.value, '');
  assert.equal(page.district.value, '');
  assert.equal(page.requests.length, 1);
});

test('requires a choice for multiple districts and filters them by the chosen state', async () => {
  const page = createPage();
  page.changePincode('682030');
  page.tick();
  await page.respond(0, [kerala, thrissur, tamilNadu, thrissur]);
  assert.equal(page.state.value, '');
  assert.equal(page.district.value, '');
  page.state.value = 'Kerala';
  page.state.dispatchEvent(new Event('change'));
  assert.equal(page.district.value, '');
  assert.deepEqual(values(page.district), ['', 'Ernakulam', 'Thrissur']);
  assert.match(page.status.textContent, /multiple districts/);
  page.state.value = 'Tamil Nadu';
  page.state.dispatchEvent(new Event('change'));
  assert.equal(page.district.value, 'Coimbatore');
  assert.deepEqual(values(page.district), ['Coimbatore']);
});

test('preserves a valid saved location after verification and rejects an old unmatched district', async () => {
  const saved = createPage({ pincode: '682030', state: 'Kerala', district: 'Thrissur' });
  assert.equal(saved.district.value, 'Thrissur');
  saved.tick();
  await saved.respond(0, [kerala, thrissur]);
  assert.equal(saved.district.value, 'Thrissur');

  const changed = createPage({ pincode: '682030', state: 'Kerala', district: 'Old District' });
  changed.tick();
  await changed.respond(0, [kerala, thrissur]);
  assert.equal(changed.state.value, 'Kerala');
  assert.equal(changed.district.value, '');
});

test('ignores stale requests even if abort is ignored by the transport', async () => {
  const page = createPage();
  page.changePincode('682030');
  page.tick();
  page.changePincode('641001');
  assert.equal(page.requests[0].options.signal.aborted, true);
  page.tick();
  await page.respond(1, [tamilNadu]);
  await page.respond(0, [kerala]);
  assert.equal(page.state.value, 'Tamil Nadu');
  assert.equal(page.district.value, 'Coimbatore');
});

test('offers retry after a network failure and retries the same PIN', async () => {
  const page = createPage();
  page.changePincode('682030');
  page.tick();
  page.requests[0].reject(new TypeError('Network unavailable'));
  await page.flush();
  assert.equal(page.retry.hidden, false);
  assert.match(page.status.textContent, /temporarily unavailable/);
  page.retry.dispatchEvent(new Event('click'));
  page.tick(0);
  assert.equal(page.requests[1].url.searchParams.get('pincode'), '682030');
  await page.respond(1, [kerala]);
  assert.equal(page.retry.hidden, true);
  assert.equal(page.state.value, 'Kerala');
});

test('a failed lookup offers the names the page carries, and retry still verifies', async () => {
  const page = createPage({
    offline: [
      { state: 'Kerala', districts: ['Ernakulam', 'Idukki'] },
      { state: 'Tamil Nadu', districts: ['Coimbatore'] },
    ],
  });
  page.changePincode('682030');
  page.tick();
  page.requests[0].reject(new TypeError('Network unavailable'));
  await page.flush();

  assert.deepEqual(page.state.children.map(option => option.value).filter(Boolean), ['Kerala', 'Tamil Nadu']);
  assert.equal(page.retry.hidden, false);
  assert.match(page.status.textContent, /select manually/);

  page.retry.dispatchEvent(new Event('click'));
  page.tick(0);
  await page.respond(1, [kerala]);
  assert.equal(page.retry.hidden, true);
  assert.equal(page.state.value, 'Kerala');
});

test('shows no-match feedback and never requests an invalid PIN', async () => {
  const page = createPage();
  for (const pincode of ['000000', '68203', '68203x', '6820300']) {
    page.changePincode(pincode);
    page.tick();
  }
  assert.equal(page.requests.length, 0);
  page.changePincode('682030');
  page.tick();
  await page.respond(0, [], 404);
  assert.match(page.status.textContent, /No LGD location/);
  assert.equal(page.state.value, '');
  assert.equal(page.district.value, '');
});

test('duplicate HTMX media evaluation is idempotent and disconnected forms ignore results', async () => {
  const page = createPage({ pincode: '682030' });
  page.runScript();
  const loaded = new Event('htmx:load');
  loaded.detail = { elt: page.input };
  page.document.dispatchEvent(loaded);
  page.tick();
  assert.equal(page.requests.length, 1);
  page.input.isConnected = false;
  await page.respond(0, [kerala]);
  assert.equal(page.state.value, '');
});

test('ignores a result whose JSON finishes after the PIN changes', async () => {
  const page = createPage();
  let resolveJSON;
  page.changePincode('682030');
  page.tick();
  page.requests[0].resolve({ ok: true, status: 200, json: () => new Promise(resolve => { resolveJSON = resolve; }) });
  await page.flush();
  page.changePincode('641001');
  page.tick();
  await page.respond(1, [tamilNadu]);
  resolveJSON({ pincode: '682030', locations: [kerala] });
  await page.flush();
  assert.equal(page.district.value, 'Coimbatore');
});
