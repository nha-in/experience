// Shared progressive enhancements for forms and HTMX navigation.
(() => {
  const formSelector = 'form[data-review-form], form[data-unsaved-form]';
  const initialValues = new WeakMap();
  const unsavedErrors = new WeakSet();
  const idleStatuses = new WeakMap();
  const requests = new WeakMap();
  const pendingForms = new Map();
  let leaving = false;

  const formValues = form => JSON.stringify([...new FormData(form)].filter(([name]) => name !== 'csrfmiddlewaretoken').map(([name, value]) => [
    name, value instanceof File ? (value.name ? [value.name, value.size, value.lastModified] : '') : value,
  ]));
  const isDirty = form => unsavedErrors.has(form) || (initialValues.has(form) && initialValues.get(form) !== formValues(form));
  const dirtyForms = () => [...document.querySelectorAll(formSelector)].filter(isDirty);

  function updateForm(form) {
    if (!form?.matches(formSelector)) return;
    form.querySelectorAll('[data-show-when-dirty]').forEach(control => {
      control.hidden = !isDirty(form);
    });
    const status = form.querySelector('[data-unsaved-status]');
    if (status) {
      if (!idleStatuses.has(status)) idleStatuses.set(status, status.textContent);
      status.textContent = isDirty(form) ? 'You have unsaved changes.' : idleStatuses.get(status);
      status.classList.toggle('text-amber-800', isDirty(form));
    }
  }

  function focusElement(element, scroll = true) {
    if (!element) return;
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    if (!element.matches('input, select, textarea, button, a[href], [tabindex]')) element.tabIndex = -1;
    element.focus({ preventScroll: true });
    if (scroll) element.scrollIntoView({ block: 'center', behavior: 'instant' });
  }

  function initialize() {
    document.querySelectorAll('[data-permission-group]').forEach(updatePermissionSummary);
    document.querySelectorAll('[data-password-toggle][hidden]').forEach(button => { button.hidden = false; });
    document.querySelectorAll('form:has([data-milestone-key])').forEach(refreshMilestones);
    document.querySelectorAll('[data-permission-toggle]').forEach(button => {
      button.hidden = false;
      updatePermissionToggle(button);
    });
    document.querySelectorAll(formSelector).forEach(form => {
      if (!initialValues.has(form)) {
        initialValues.set(form, formValues(form));
        if (form.querySelector('[data-error-summary]')) unsavedErrors.add(form);
      }
      updateForm(form);
    });
    const error = document.querySelector('[data-error-summary]:not([data-error-focused])');
    if (error) {
      error.dataset.errorFocused = 'true';
      focusElement(error);
    }
  }

  function showError(message) {
    const region = document.getElementById('request-feedback');
    if (!region) return;
    region.hidden = false;
    region.querySelector('[data-request-error]').textContent = message;
  }

  function clearError() {
    const region = document.getElementById('request-feedback');
    if (region) region.hidden = true;
  }

  function markPending(element) {
    if (!element) return () => {};
    const buttons = [...element.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]')];
    const state = buttons.map(button => [button, button.getAttribute('aria-disabled')]);
    element.setAttribute('aria-busy', 'true');
    state.forEach(([button]) => {
      if (button.disabled) return;
      button.setAttribute('aria-disabled', 'true');
      button.dataset.requestPending = '';
    });
    return () => {
      element.removeAttribute('aria-busy');
      state.forEach(([button, previous]) => {
        delete button.dataset.requestPending;
        if (previous === null) button.removeAttribute('aria-disabled');
        else button.setAttribute('aria-disabled', previous);
      });
    };
  }

  document.addEventListener('DOMContentLoaded', initialize);
  function updatePermissionSummary(group) {
    const summary = group.querySelector('[data-permission-summary]');
    if (!summary) return;
    const all = group.querySelector('[data-all-categories] [data-permission-action="read"]');
    const count = group.querySelectorAll('[data-permission-action="read"]:checked').length;
    summary.textContent = all?.checked ? 'All categories' : count ? `${count} categor${count === 1 ? 'y' : 'ies'}` : 'No access';
    summary.classList.toggle('has-access', count > 0);
  }

  function updatePermissionToggle(button) {
    const groups = [...button.closest('form').querySelectorAll('[data-permission-group]')];
    const expanded = groups.length > 0 && groups.every(group => group.open);
    button.textContent = expanded ? 'Collapse all' : 'Expand all';
    button.setAttribute('aria-expanded', String(expanded));
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-permission-toggle]');
    if (!button) return;
    const expand = button.getAttribute('aria-expanded') !== 'true';
    button.closest('form').querySelectorAll('[data-permission-group]').forEach(group => { group.open = expand; });
    updatePermissionToggle(button);
  });
  // Only Edge draws a reveal control of its own, so every password field gets one.
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-password-toggle]');
    if (!button) return;
    const input = button.closest('[data-password-field]').querySelector('input');
    const revealing = input.type === 'password';
    input.type = revealing ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(revealing));
    button.setAttribute('aria-label', revealing ? 'Hide password' : 'Show password');
    button.querySelectorAll('[data-password-icon]').forEach(icon => {
      icon.toggleAttribute('hidden', icon.dataset.passwordIcon !== (revealing ? 'hide' : 'show'));
    });
  });
  // Submit as a password field so browsers still offer to save it.
  document.addEventListener('submit', event => {
    event.target.querySelectorAll('[data-password-field] input').forEach(input => { input.type = 'password'; });
  }, true);
  document.addEventListener('toggle', event => {
    if (!event.target.matches('[data-permission-group]')) return;
    event.target.closest('form')?.querySelectorAll('[data-permission-toggle]').forEach(updatePermissionToggle);
  }, true);
  document.addEventListener('change', event => {
    const input = event.target.closest('[data-permission-action]');
    if (!input) return;
    const row = input.closest('[data-permission-row]');
    const read = row.querySelector('[data-permission-action="read"]');
    if (input === read && !read.checked) {
      row.querySelectorAll('[data-permission-action]').forEach(control => { control.checked = false; });
    } else if (input.checked) {
      read.checked = true;
    }
    const group = input.closest('[data-permission-group]');
    if (group) updatePermissionSummary(group);
  });
  // A milestone opens once its prerequisite is ticked; unticking one clears
  // everything after it. The form re-checks this on submit regardless.
  const isTicked = (form, key) => [...form.querySelectorAll(`[data-milestone-key="${CSS.escape(key)}"]`)].some(input => input.checked);

  function refreshMilestones(form) {
    const gated = [...form.querySelectorAll('[data-milestone-requires]')];
    let cleared = true;
    while (cleared) {
      cleared = false;
      for (const input of gated) {
        const required = input.dataset.milestoneRequires;
        const open = !required || isTicked(form, required);
        if (!open && input.checked) {
          input.checked = false;
          cleared = true;
        }
        input.disabled = !open;
      }
    }
  }

  document.addEventListener('change', event => {
    const input = event.target.closest('[data-milestone-key]');
    if (input) refreshMilestones(input.closest('form'));
  });
  ['input', 'change'].forEach(type => document.addEventListener(type, event => updateForm(event.target.closest('form'))));
  window.addEventListener('beforeunload', event => {
    if (!leaving && dirtyForms().length) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('pageshow', () => {
    leaving = false;
    pendingForms.forEach(restore => restore());
    pendingForms.clear();
  });

  // Confirm before HTMX sees the submit event; never strip the submitter's
  // name/value by disabling it before the browser has serialized the form.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (event.defaultPrevented) return;
    if (pendingForms.has(form)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    let message = form.dataset.confirm || '';
    if (dirtyForms().some(dirty => dirty !== form) && !message.toLowerCase().includes('unsaved')) {
      message += `${message ? '\n\n' : ''}You have unsaved changes in another form. Continue without saving those changes?`;
    }
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
  }, true);

  // HTMX's form handler prevents the native submission before it bubbles here.
  // A capture-phase microtask runs too early to distinguish the two paths.
  document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    leaving = true;
    pendingForms.set(event.target, markPending(event.target));
  });

  document.addEventListener('click', event => {
    if (event.target.closest('[data-request-pending]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener('click', event => {
    const dismiss = event.target.closest('[data-dismiss-request]');
    if (dismiss) clearError();
    const errorLink = event.target.closest('[data-error-field]');
    if (errorLink) {
      const form = errorLink.closest('form');
      const control = [...(form?.elements || [])].find(input => input.name === errorLink.dataset.errorField && !input.disabled);
      event.preventDefault();
      focusElement(control || document.getElementById(errorLink.hash.slice(1)));
    }
    const jump = event.target.closest('[data-submit-missing]');
    if (jump) {
      const form = jump.closest('form');
      const available = input => !input.matches(':disabled') && !input.closest('[hidden]');
      const missing = [...form.querySelectorAll('input, select, textarea')].find(input => available(input) && !input.validity.valid);
      const missingGroup = [...form.querySelectorAll('[data-required-checkbox-group]')].find(group => {
        const choices = [...group.querySelectorAll('input[type="checkbox"]')].filter(available);
        return choices.length > 0 && !choices.some(input => input.checked);
      });
      const upload = [...form.querySelectorAll('[data-required-upload]')].find(field =>
        field.querySelector('input[type="file"]') && available(field.querySelector('input[type="file"]')) &&
        !field.querySelector('input[type="file"]')?.files.length &&
        ![...field.querySelectorAll('[data-existing-file-remove]')].some(input => !input.checked));
      const groupChoice = [...(missingGroup?.querySelectorAll('input[type="checkbox"]') || [])].find(available);
      const first = [missing, groupChoice, upload?.querySelector('input[type="file"]')].filter(Boolean).sort(
        (left, right) => left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_PRECEDING ? 1 : -1,
      )[0];
      focusElement(first);
    }
  });

  document.addEventListener('htmx:beforeRequest', event => {
    if (event.defaultPrevented) return;
    const { target, elt, xhr, requestConfig } = event.detail;
    const form = elt.closest('form');
    if (form) {
      pendingForms.get(form)?.();
      pendingForms.delete(form);
    }
    leaving = false;
    if (requestConfig.verb === 'get' && dirtyForms().some(dirty => target.contains(dirty))) {
      if (!window.confirm('You have unsaved changes. Leave this page without saving?')) {
        event.preventDefault();
        return;
      }
    }
    clearError();
    requests.set(xhr, markPending(form || target));
  });

  document.addEventListener('htmx:afterRequest', event => {
    const { xhr, failed } = event.detail;
    requests.get(xhr)?.();
    requests.delete(xhr);
    leaving = false;
    if (failed) {
      const message = xhr.status === 403
        ? 'This request could not be authorised. Check your session before trying again.'
        : xhr.status === 429
          ? 'Too many requests. Wait a moment, then try again.'
          : xhr.status === 0
            ? 'The connection was interrupted. Your entries are still here. Check the connection and try again.'
            : 'The request could not be completed. Your entries are still here. Please try again.';
      showError(message);
    }
  });

  ['htmx:sendError', 'htmx:timeout'].forEach(type => document.addEventListener(type, () => {
    showError('We could not confirm the result. Your entries are still here. Check the connection before trying again.');
  }));

  document.addEventListener('htmx:afterSettle', event => {
    initialize();
    if (event.detail.target?.id !== 'main-content') {
      const target = document.getElementById(event.detail.target?.id);
      if (target && document.activeElement === document.body) {
        focusElement(target.querySelector('button:not([disabled]), input:not([type="hidden"]), select, textarea'), false);
      }
      return;
    }
    const main = document.getElementById('main-content');
    const error = main?.querySelector('[data-error-summary]');
    if (error) return;
    // Keep focus on search/select controls after filtering. When navigation
    // replaces a page, move it to the new heading so keyboard users can continue.
    if (document.activeElement === document.body || !main.contains(document.activeElement)) {
      focusElement(main.querySelector('h1') || main, false);
    }
    const status = document.getElementById('interaction-status');
    if (status) status.textContent = main.querySelector('h1')?.textContent.trim() || document.title;
  });
})();
