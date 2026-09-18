const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/verification-resend.js'), 'utf8');

// The countdown only rewrites its note and enables a button, so two stubs do.
function createPage(secondsLeft) {
  const note = { textContent: '', dataset: { resendLabel: 'You can ask for a new code in 0:00.' } };
  const button = { disabled: true };
  const group = {
    dataset: { resendCountdown: new Date(Date.now() + secondsLeft * 1000).toISOString() },
    querySelector: selector => ({ '[data-resend-note]': note, '[data-resend-button]': button })[selector],
  };
  const page = { note, button, group, tick: null };
  const context = vm.createContext({
    Date,
    document: { querySelectorAll: () => [group] },
    setInterval: fn => { page.tick = fn; return 1; },
  });
  vm.runInContext(script, context);
  return page;
}

test('counts the wait down while the button stays disabled', () => {
  const page = createPage(95);

  assert.equal(page.note.textContent, 'You can ask for a new code in 1:35.');
  assert.equal(page.button.disabled, true);

  page.group.dataset.resendCountdown = new Date(Date.now() + 9000).toISOString();
  page.tick();

  assert.equal(page.note.textContent, 'You can ask for a new code in 0:09.');
});

test('enables the button once the wait is over', () => {
  const page = createPage(-1);

  assert.equal(page.button.disabled, false);
  assert.equal(page.note.textContent, '');
});
