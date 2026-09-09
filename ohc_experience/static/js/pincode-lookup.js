// PIN-derived location choices are verified again by the server on submission.
(() => {
  // Form media can be evaluated again when HTMX inserts another form.
  if (window.organisationPincodeLookup) {
    window.organisationPincodeLookup();
    return;
  }

  const selector = 'input[data-pincode-lookup]';
  const controllers = new WeakMap();
  const isPincode = value => /^[1-9][0-9]{5}$/.test(value);
  const normalize = value => value.trim().toLocaleLowerCase();
  const matchName = (names, value) => names.find(name => normalize(name) === normalize(value)) || '';
  const uniqueNames = values => [...new Map(values.map(value => [normalize(value), value])).values()]
    .sort((left, right) => left.localeCompare(right));

  function inputsWithin(root) {
    return [
      ...(root.matches?.(selector) ? [root] : []),
      ...(root.querySelectorAll?.(selector) || []),
    ];
  }

  function attach(input) {
    const form = input.closest('form');
    const state = form?.querySelector('[data-lgd-state]');
    const district = form?.querySelector('[data-lgd-district]');
    const field = input.closest('[data-pincode-field]');
    const status = field?.querySelector('[data-pincode-status]');
    const retry = field?.querySelector('[data-pincode-retry]');
    if (!state || !district || !status || !retry) return;

    let lastPincode = input.value.trim();
    let locations = [];
    let timer;
    let request;
    let sequence = 0;
    let updating = false;
    let disposed = false;

    function message(text, canRetry = false) {
      status.textContent = text;
      retry.hidden = !canRetry;
    }

    function setOptions(select, names, placeholder, preferred = '') {
      const selected = matchName(names, preferred) || (names.length === 1 ? names[0] : '');
      const options = [
        ...(names.length === 1 ? [] : [new Option(placeholder, '')]),
        ...names.map(name => new Option(name, name)),
      ];
      select.replaceChildren(...options);
      select.value = selected;
    }

    function clearLocations() {
      locations = [];
      setOptions(state, [], 'Enter a PIN code first');
      setOptions(district, [], 'Enter a PIN code first');
    }

    function notifyChanges() {
      updating = true;
      state.dispatchEvent(new Event('change', { bubbles: true }));
      district.dispatchEvent(new Event('change', { bubbles: true }));
      updating = false;
    }

    function describeSelection() {
      if (!state.value) {
        message('This PIN code covers multiple states. Choose the matching state, then district.');
      } else if (!district.value) {
        message('This PIN code covers multiple districts. Choose the matching district.');
      } else {
        message('State and district matched to this PIN code.');
      }
    }

    function updateDistricts(preferred = '') {
      const names = uniqueNames(locations.filter(location => normalize(location.state) === normalize(state.value))
        .map(location => location.district));
      setOptions(district, names, state.value ? 'Select district' : 'Select a state first', preferred);
      describeSelection();
    }

    function cancel() {
      sequence += 1;
      clearTimeout(timer);
      request?.abort();
      request = null;
      field.removeAttribute('aria-busy');
    }

    async function lookup(pincode, preferred, version) {
      const activeRequest = new AbortController();
      request = activeRequest;
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        activeRequest.abort();
      }, 12000);
      const isCurrent = () => !disposed && input.isConnected && version === sequence && input.value.trim() === pincode;
      try {
        const url = new URL(input.dataset.pincodeLookup, window.location.href);
        url.searchParams.set('pincode', pincode);
        const response = await fetch(url, {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
          signal: activeRequest.signal,
        });
        if (!isCurrent()) return;
        if (response.status === 404) {
          clearLocations();
          message('No LGD location was found for this PIN code. Check the PIN code and try again.');
          notifyChanges();
          return;
        }
        if (response.status === 400) {
          clearLocations();
          message('Enter a valid six-digit PIN code that does not start with zero.');
          notifyChanges();
          return;
        }
        if (!response.ok) throw new Error('Lookup unavailable');
        const result = await response.json();
        if (!isCurrent()) return;
        if (String(result.pincode) !== pincode || !Array.isArray(result.locations) || !result.locations.length
          || result.locations.some(location => typeof location.state !== 'string' || !location.state.trim()
            || typeof location.district !== 'string' || !location.district.trim())) {
          throw new Error('Invalid lookup response');
        }
        locations = result.locations;
        setOptions(state, uniqueNames(locations.map(location => location.state)), 'Select state', preferred.state);
        updateDistricts(preferred.district);
        notifyChanges();
      } catch (error) {
        if (!isCurrent() || (error.name === 'AbortError' && !timedOut)) return;
        message('The LGD lookup is temporarily unavailable. Retry to verify the state and district.', true);
      } finally {
        clearTimeout(timeout);
        if (isCurrent()) {
          request = null;
          field.removeAttribute('aria-busy');
        }
      }
    }

    function schedule(preserve = false, delay = 250) {
      const preferred = preserve ? { state: state.value, district: district.value } : { state: '', district: '' };
      cancel();
      lastPincode = input.value.trim();
      if (!preserve || !isPincode(lastPincode)) clearLocations();
      if (!isPincode(lastPincode)) {
        message(lastPincode
          ? 'Enter a valid six-digit PIN code that does not start with zero.'
          : 'Enter a six-digit PIN code to look up the state and district.');
        return;
      }
      message('Looking up state and district…');
      field.setAttribute('aria-busy', 'true');
      const version = sequence;
      const pincode = lastPincode;
      timer = setTimeout(() => lookup(pincode, preferred, version), delay);
    }

    function inputChanged() {
      if (input.value.trim() !== lastPincode) schedule();
    }

    input.addEventListener('input', inputChanged);
    input.addEventListener('change', inputChanged);
    retry.addEventListener('click', () => schedule(true, 0));
    state.addEventListener('change', () => {
      if (updating || !locations.length) return;
      updateDistricts();
      notifyChanges();
    });
    district.addEventListener('change', () => {
      if (!updating && locations.length) describeSelection();
    });
    controllers.set(input, {
      refresh: inputChanged,
      dispose() {
        disposed = true;
        cancel();
      },
    });
    schedule(true);
  }

  function initialize(root = document) {
    inputsWithin(root).forEach(input => {
      if (controllers.has(input)) controllers.get(input).refresh();
      else attach(input);
    });
  }

  window.organisationPincodeLookup = initialize;
  document.addEventListener('DOMContentLoaded', () => initialize());
  document.addEventListener('htmx:load', event => initialize(event.detail?.elt || event.target));
  document.addEventListener('htmx:afterSwap', event => initialize(event.detail?.target || event.target));
  document.addEventListener('htmx:beforeCleanupElement', event => {
    inputsWithin(event.detail?.elt || event.target).forEach(input => {
      controllers.get(input)?.dispose();
      controllers.delete(input);
    });
  });
  window.addEventListener('pageshow', () => initialize());
  initialize();
})();
