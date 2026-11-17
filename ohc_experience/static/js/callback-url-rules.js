// The callback URL card ticks the gateway's rules as the integrator types and
// holds Save until they all pass. The rules come from the card's own list, so
// what a person reads and what the browser checks cannot drift.
// `clean_callback_url` still rules on the server, where the other writers are.
(() => {
  'use strict';

  function met(value, scope, pattern) {
    const path = value.replace(/^https?:\/\/[^/]*/, '');
    return new RegExp(pattern).test(scope === 'url' ? value : path);
  }

  function refresh(card) {
    const input = card.querySelector('input[name="callback_url"]');
    const list = card.querySelector('[data-callback-rules]');
    if (!input || !list) return;
    // A trailing slash is dropped on save, so it never holds Save back.
    const url = input.value.trim().replace(/\/+$/, '');
    let failing = 0;
    list.querySelectorAll('[data-rule-pattern]').forEach(rule => {
      const passes = met(url, rule.dataset.ruleScope, rule.dataset.rulePattern);
      if (url && !passes) failing += 1;
      rule.dataset.met = url && passes ? 'true' : '';
      const mark = rule.firstElementChild;
      if (mark) mark.textContent = url && passes ? '✓' : '·';
    });
    const button = card.querySelector('[data-callback-save]');
    // An empty box clears the URL, which is allowed: only a typed one can block.
    if (button) button.disabled = failing > 0;
  }

  function initialize(root) {
    (root || document).querySelectorAll('[data-callback-card]').forEach(card => {
      card.addEventListener('input', () => refresh(card));
      refresh(card);
    });
  }

  document.addEventListener('DOMContentLoaded', () => initialize());
  document.addEventListener('htmx:load', event => initialize(event.target));
  window.callbackUrlRules = { met, refresh };
})();
