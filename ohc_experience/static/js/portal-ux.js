// Shared progressive enhancements for forms and HTMX navigation.
(() => {
  const formSelector = 'form[data-review-form], form[data-unsaved-form]';
  const initialValues = new WeakMap();
  const unsavedErrors = new WeakSet();
  const idleStatuses = new WeakMap();
  const requests = new WeakMap();
  const pendingForms = new Map();
  const loads = new WeakMap();
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

  function jumpTo(element) {
    if (!element) return;
    focusElement(element, false);
    element.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    if (element.id) history.replaceState(history.state, '', `#${element.id}`);
    element.closest('.ui-form-section, .ui-form-actions')?.setAttribute('data-flash', '');
  }

  function firstIncomplete(form) {
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
    return [missing, groupChoice, upload?.querySelector('input[type="file"]')].filter(Boolean).sort(
      (left, right) => left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_PRECEDING ? 1 : -1,
    )[0];
  }

  function updateReadiness(form) {
    if (!form?.id) return;
    document.querySelectorAll('[data-readiness-form]').forEach(list => {
      if (list.dataset.readinessForm !== form.id) return;
      list.querySelectorAll('[data-readiness-item]').forEach(item => {
        const inputs = item.dataset.readinessFields.split(',').map(id => document.getElementById(id)).filter(Boolean);
        const filled = inputs.length > 0 && inputs.every(input => !input.disabled && (
          input.matches('[data-required-checkbox-group]') ? !!input.querySelector('input:checked') :
          input.type === 'file' ? input.files.length > 0 || !!input.closest('[data-file-upload]')?.querySelector('[data-existing-file-remove]:not(:checked)') :
            input.type === 'checkbox' || input.type === 'radio' ? input.checked : input.value.trim() !== ''
        ));
        inputs[0]?.closest('.ui-form-section')?.toggleAttribute('data-missing', !filled);
        const indicator = item.querySelector('[data-readiness-indicator]');
        const label = item.querySelector('[data-readiness-label]');
        if (indicator) indicator.className = filled
          ? 'inline-flex size-4 shrink-0 rounded-full border-[1.5px] border-dashed border-primary'
          : 'inline-flex size-4 shrink-0 rounded-full border-[1.5px] border-dashed border-amber-500';
        if (label) {
          label.classList.toggle('text-primary', filled);
          label.classList.toggle('text-amber-700', !filled);
          label.querySelector('.sr-only').textContent = filled ? ': filled, not saved yet' : ': not saved yet';
        }
      });
    });
    document.querySelectorAll('[data-next-step]').forEach(step => {
      if (step.dataset.nextStep !== form.id) return;
      const next = firstIncomplete(form);
      const section = next && (next.closest('.ui-form-section') || next.closest('fieldset'));
      step.toggleAttribute('data-complete', !next);
      if (next) step.querySelector('[data-next-step-name]').textContent = section?.querySelector(':scope > legend')?.textContent.trim() ?? '';
    });
  }

  function initialize() {
    document.querySelectorAll('[data-permission-group]').forEach(group => {
      updatePermissionLocks(group);
      updatePermissionSummary(group);
    });
    document.querySelectorAll('[data-password-toggle][hidden]').forEach(button => { button.hidden = false; });
    document.querySelectorAll('form:has([data-milestone-key])').forEach(form => {
      refreshMilestones(form);
    });
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
      updateReadiness(form);
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

  // The error pages mark their words (layouts/error.html), so a request that
  // fails says what the page would have: why access was refused, that the
  // session expired, that the page is gone.
  function errorPageWords(xhr) {
    const html = xhr?.responseText;
    if (!html) return '';
    const page = new DOMParser().parseFromString(html, 'text/html');
    return [...page.querySelectorAll('[data-error-heading], [data-error-message]')]
      .map(part => part.textContent.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .map(part => (/[.!?]$/.test(part) ? part : `${part}.`))
      .join(' ');
  }

  // Status 0 is a dropped connection; 504 is a server that answered too late.
  // A save that got no answer may have gone through all the same, so its
  // message asks for a check before it is sent again; a load can simply be
  // tried again.
  function reportFailure({ elt, xhr, requestConfig }, status) {
    const loading = (requestConfig?.verb || 'get') === 'get';
    const fields = elt?.closest('form')?.querySelector('input:not([type="hidden"]), select, textarea');
    const kept = !loading && fields ? ' Your entries are still here.' : '';
    const words = status === 0 || status === 504 || status === 429 ? '' : errorPageWords(xhr);
    let message;
    if (status === 0) {
      message = loading
        ? 'The connection was interrupted, so this could not be loaded. Check your connection, then try again.'
        : `The connection was interrupted, so we could not confirm the result.${kept} Check whether it was saved before trying again.`;
    } else if (status === 504) {
      message = loading
        ? 'The server took too long to respond. Try again in a moment.'
        : `The server took too long to respond, so we could not confirm the result.${kept} Check whether it was saved before trying again.`;
    } else if (status === 429) {
      message = `Too many requests.${kept} Wait a moment, then try again.`;
    } else if (words) {
      message = `${words}${kept}`;
    } else if (status >= 500) {
      message = `The server could not complete this request.${kept} Try again in a moment.`;
    } else if (status === 403) {
      message = `This request could not be authorised.${kept} Check your session before trying again.`;
    } else {
      message = `The request could not be completed.${kept}`;
    }
    showError(message);
  }

  // The part of the page a load will replace is marked busy; project.css fades
  // it once the wait is long enough to notice.
  function markLoading(target) {
    if (!target) return () => {};
    loads.set(target, (loads.get(target) || 0) + 1);
    target.setAttribute('data-request-loading', '');
    target.setAttribute('aria-busy', 'true');
    return () => {
      const count = (loads.get(target) || 1) - 1;
      loads.set(target, count);
      if (count) return;
      target.removeAttribute('data-request-loading');
      target.removeAttribute('aria-busy');
    };
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

  // A wildcard tick already grants that action to every category, so the editor
  // shows the column below it as granted and locks it. A locked box sends no
  // value, and the form treats the wildcard tick as the read access instead.
  function updatePermissionLocks(group) {
    const wildcard = group.querySelector('[data-all-categories]');
    if (!wildcard) return;
    ['read', 'write', 'approve'].forEach(action => {
      const locked = Boolean(wildcard.querySelector(`[data-permission-action="${action}"]`)?.checked);
      group.querySelectorAll(`[data-permission-row]:not([data-all-categories]) [data-permission-action="${action}"]`).forEach(input => {
        if (locked && !input.disabled) input.dataset.permissionChoice = String(input.checked);
        if (locked) {
          input.checked = true;
        } else if (input.disabled) {
          input.checked = input.dataset.permissionChoice === 'true';
          delete input.dataset.permissionChoice;
        }
        input.disabled = locked;
        const label = input.closest('label');
        label?.classList.toggle('is-permission-implied', locked);
        const note = label?.querySelector('.sr-only');
        if (!note) return;
        if (!note.dataset.permissionLabel) note.dataset.permissionLabel = note.textContent;
        note.textContent = locked ? `${note.dataset.permissionLabel}, granted by all categories` : note.dataset.permissionLabel;
      });
    });
    // An unlocked column can leave a row with write or approve but no read.
    group.querySelectorAll('[data-permission-row]').forEach(row => {
      const read = row.querySelector('[data-permission-action="read"]');
      const others = [...row.querySelectorAll('[data-permission-action]')].filter(input => input !== read);
      if (read && !read.checked && others.some(input => input.checked)) read.checked = true;
    });
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
    if (group) {
      updatePermissionLocks(group);
      updatePermissionSummary(group);
    }
  });
  // A milestone opens once one of the milestones it builds on is ticked, or all
  // of them for one marked requires-all, like P4; unticking them clears
  // everything after it. One marked stands-alone, like UHI, is only ever
  // suggested them. The form re-checks this on submit.
  const isTicked = (form, key) => [...form.querySelectorAll(`[data-milestone-key="${CSS.escape(key)}"]`)].some(input => input.checked);

  // Approved and under-review milestones are posted as hidden fields, so the
  // boxes a reader can still change are the ones carrying their prerequisite.
  function changeable(group) {
    return [...group.querySelectorAll('[data-milestone-requires]')];
  }

  function hasSelection(group) {
    return [...group.querySelectorAll('[data-milestone-key]')].some(input => input.checked);
  }

  // ABDM and PHR & Health Locker rule each other out. Ticking one clears the
  // other, so the track just acted on is the one that wins.
  function applyExclusivity(input) {
    const group = input.closest('[data-track-excludes]');
    if (!group || !input.checked) return;
    const ruled = group.dataset.trackExcludes;
    const other = input.form.querySelector(`[data-track="${CSS.escape(ruled)}"]`);
    if (!other) return;
    changeable(other).forEach(box => { box.checked = false; });
  }

  function refreshTracks(form) {
    const groups = [...form.querySelectorAll('[data-track-excludes]')];
    const chosen = groups.filter(hasSelection)[0];
    for (const group of groups) {
      const blocked = Boolean(chosen) && group !== chosen;
      for (const input of changeable(group)) {
        input.dataset.trackBlocked = blocked ? 'true' : '';
      }
      const note = group.querySelector('[data-track-blocked]');
      if (note) note.hidden = !blocked;
    }
  }

  function refreshMilestones(form) {
    const gated = [...form.querySelectorAll('[data-milestone-requires]')];
    let cleared = true;
    while (cleared) {
      cleared = false;
      // Clearing a track's last ticked box opens the track it ruled out.
      refreshTracks(form);
      for (const input of gated) {
        const required = (input.dataset.milestoneRequires || '').split(' ').filter(Boolean);
        const standsAlone = 'milestoneStandsAlone' in input.dataset;
        const built = 'milestoneRequiresAll' in input.dataset
          ? required.every(key => isTicked(form, key))
          : required.some(key => isTicked(form, key));
        const met = standsAlone || required.length === 0 || built;
        const open = met && !input.dataset.trackBlocked && !('roleLocked' in input.dataset);
        if (!open && input.checked) {
          input.checked = false;
          cleared = true;
        }
        input.disabled = !open || 'milestoneLocked' in input.dataset;
      }
    }
    // NHCX shows the roles of whichever of ABDM or PHR is chosen, and all before either.
    const chosen = [...form.querySelectorAll('[data-track-excludes]')].find(hasSelection);
    form.querySelectorAll('[data-role-track]').forEach(row => {
      row.hidden = Boolean(chosen) && row.dataset.roleTrack !== chosen.dataset.track;
    });
    // An approved or under-review role posts as a hidden field, without a name here.
    const clear = form.querySelector('[data-nhcx-clear]');
    if (clear) clear.disabled = ![...form.querySelectorAll('input[type="radio"][name][data-milestone-key]')].some(radio => radio.checked);
  }

  // A solution type in NHA's matrix fixes the ABDM or PHR milestones to the
  // ones it requires. Other lets the integrator choose. An optional one, M4, is
  // never fixed: it stays as ticked while the type fixes its track, and clears
  // when the type fixes the other one. The form applies the same rule on submit.
  const requiredBy = (input, type) => (input.dataset.requiredFor || '').split(' ').includes(type.value);
  const readableList = names => names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names.join('');

  function applySolutionType(type) {
    const form = type.form;
    const groups = [...form.querySelectorAll('[data-track-excludes]')];
    const boxes = groups.flatMap(changeable);
    const fixes = boxes.some(input => requiredBy(input, type));
    const label = type.labels[0]?.textContent.trim() || type.value;
    const fixedGroup = groups.find(group => changeable(group).some(input => requiredBy(input, type)));
    for (const input of boxes) {
      if ('milestoneOptional' in input.dataset) {
        if (fixes && input.closest('[data-track-excludes]') !== fixedGroup) input.checked = false;
        continue;
      }
      if (fixes) input.checked = requiredBy(input, type);
      input.toggleAttribute('data-milestone-locked', fixes);
    }
    for (const group of groups) {
      const note = group.querySelector('[data-track-fixed]');
      if (!note) continue;
      const owns = fixes && changeable(group).some(input => input.checked);
      note.textContent = owns ? `· Set by the ${label} solution type.` : '';
      note.hidden = !owns;
    }
    const status = form.querySelector('[data-milestone-status]');
    if (!status) return;
    const codes = boxes.filter(input => fixes && input.checked && requiredBy(input, type)).map(input => input.dataset.milestoneKey.toUpperCase());
    status.textContent = codes.length ? `${readableList(codes)} set by the ${label} solution type.` : '';
  }

  document.addEventListener('change', event => {
    const type = event.target.closest('[data-solution-type]');
    const input = event.target.closest('[data-milestone-key]');
    const form = (type || input)?.form;
    if (!form) return;
    if (type) applySolutionType(type);
    else applyExclusivity(input);
    refreshMilestones(form);
  });
  // NHCX's roles are radios, so a click cannot untick them.
  document.addEventListener('click', event => {
    const clear = event.target.closest('[data-nhcx-clear]');
    if (!clear) return;
    const form = clear.form;
    form.querySelectorAll('input[type="radio"][name][data-milestone-key]').forEach(radio => { radio.checked = false; });
    refreshMilestones(form);
    updateForm(form);
    updateReadiness(form);
  });
  ['input', 'change'].forEach(type => document.addEventListener(type, event => {
    const form = event.target.closest('form');
    updateForm(form);
    updateReadiness(form);
  }));
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
    const continueForm = event.target.closest('[data-continue-form]');
    if (continueForm) {
      event.preventDefault();
      jumpTo(firstIncomplete(document.getElementById(continueForm.dataset.continueForm)) ?? document.getElementById('evidence-actions'));
    }
    // A queried field, or the alert naming it, leads to the query: into its
    // reply box when the viewer can answer, with the query flashed as Continue
    // flashes the section it lands in.
    const queryLink = event.target.closest('[data-query-link]');
    const query = queryLink && document.getElementById(queryLink.hash.slice(1));
    if (query) {
      event.preventDefault();
      if (query.tagName === 'DETAILS') query.open = true;
      jumpTo(query.querySelector('textarea') || query);
      query.setAttribute('data-flash', '');
    }
    const readinessLink = event.target.closest('[data-readiness-label]');
    if (readinessLink) {
      event.preventDefault();
      jumpTo(document.getElementById(readinessLink.hash.slice(1)));
    }
    const jump = event.target.closest('[data-submit-missing]');
    if (jump) {
      const form = jump.closest('form');
      jumpTo(firstIncomplete(form));
    }
  });

  document.addEventListener('animationend', event => {
    if (event.animationName === 'ui-flash') event.target.removeAttribute('data-flash');
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
    const restore = markPending(form || target);
    const settle = requestConfig.verb === 'get' ? markLoading(target) : () => {};
    requests.set(xhr, () => {
      restore();
      settle();
    });
  });

  document.addEventListener('htmx:afterRequest', event => {
    const { xhr, failed } = event.detail;
    requests.get(xhr)?.();
    requests.delete(xhr);
    leaving = false;
    if (failed) reportFailure(event.detail, xhr.status);
  });

  // A dropped connection or a timeout ends with no response, so htmx reports
  // these on their own events rather than as a failed afterRequest.
  document.addEventListener('htmx:sendError', event => reportFailure(event.detail, 0));
  document.addEventListener('htmx:timeout', event => reportFailure(event.detail, 504));

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
