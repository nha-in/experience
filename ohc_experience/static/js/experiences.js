(() => {
  function updateSubmission(form) {
    const button = form.querySelector('[data-request-submit]');
    const reason = form.querySelector('[data-submit-reason]');
    if (!button) return;
    const missingFields = [...form.querySelectorAll('[required]')].some(input => !input.checkValidity());
    const missingFiles = [...form.querySelectorAll('[data-required-upload]')].some(field => {
      const input = field.querySelector('input[type="file"]');
      const retained = [...field.querySelectorAll('[data-existing-file-remove]')].some(checkbox => !checkbox.checked);
      return !input?.files.length && !retained;
    });
    const blocked = form.dataset.reviewBlocked === 'true';
    button.disabled = blocked || missingFields || missingFiles;
    if (reason) reason.textContent = blocked ? 'Required approvals are pending.' : button.disabled ? 'Complete all required fields and documents.' : '';
  }

  function updateDecision(form) {
    const action = form.querySelector('[name="action"]:checked')?.value || 'approve';
    const note = form.querySelector('[name="note"]');
    const labels = { approve: ['Decision note', 'Record approval'], send_back: ['Reason for sending back', 'Send back to integrator'], query: ['Question', 'Send query'] };
    form.querySelector('[data-decision-label]').textContent = labels[action][0];
    form.querySelector('[data-decision-submit]').textContent = labels[action][1];
    note.required = action !== 'approve';
  }

  function initialize(scope = document) {
    scope.querySelectorAll?.('[data-review-form]').forEach(updateSubmission);
    scope.querySelectorAll?.('[data-decision-form]').forEach(updateDecision);
    scope.querySelectorAll?.('[data-revealed-secret]').forEach(secret => {
      setTimeout(() => { if (secret.isConnected) hideSecret(secret.closest('.sb-secret')); }, 30000);
    });
  }

  function hideSecret(root) {
    if (!root) return;
    const masked = document.createElement('span');
    masked.textContent = 'Hidden. Reload to reveal again.';
    root.replaceChildren(masked);
  }

  document.addEventListener('DOMContentLoaded', () => initialize());
  document.addEventListener('htmx:afterSettle', () => initialize());
  document.addEventListener('change', event => {
    const switcher = event.target.closest('[data-product-switch]');
    if (switcher) {
      switcher.form.requestSubmit();
    }
    const decision = event.target.closest('[data-decision-form]');
    if (decision) updateDecision(decision);
    const form = event.target.closest('[data-review-form]');
    if (form) updateSubmission(form);
  });
  document.addEventListener('input', event => {
    const form = event.target.closest('[data-review-form]');
    if (form) updateSubmission(form);
  });
  document.addEventListener('drop', event => {
    const form = event.target.closest('[data-review-form]');
    if (form) setTimeout(() => updateSubmission(form), 0);
  });
  document.addEventListener('click', event => {
    const toggle = event.target.closest('[data-sidebar-toggle]');
    if (toggle) {
      const open = toggle.closest('#portal').classList.toggle('sb-menu-open');
      toggle.setAttribute('aria-expanded', String(open));
    }
    const hide = event.target.closest('[data-hide-secret]');
    if (hide) hideSecret(hide.closest('.sb-secret'));
    const form = event.target.closest('[data-review-form]');
    if (form) setTimeout(() => updateSubmission(form), 0);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) document.querySelectorAll('[data-revealed-secret]').forEach(secret => hideSecret(secret.closest('.sb-secret')));
  });
})();
