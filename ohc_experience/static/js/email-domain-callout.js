// Integrators are expected to sign in with an email address on their own domain.
// This only advises: nothing is blocked here or on the server, and the reviewer decides.
(() => {
  // Where a personal address is normal, so a sole proprietor is never nudged about one.
  const consumerProviders = new Set([
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.in', 'yahoo.co.in', 'ymail.com',
    'rediffmail.com', 'hotmail.com', 'outlook.com', 'live.com', 'msn.com', 'aol.com',
    'icloud.com', 'me.com', 'proton.me', 'protonmail.com', 'zoho.com', 'yandex.com',
  ]);

  const value = (form, name) => form.elements.namedItem(name)?.value.trim().toLowerCase() || '';

  const emailDomain = email => (email.includes('@') ? email.split('@').pop() : '');

  const websiteDomain = website => website
    .replace(/^[a-z]+:\/\//, '')
    .split('/')[0]
    .split(':')[0]
    .replace(/^www\./, '');

  // A subdomain still belongs to the organisation: mail.acme.in matches acme.in.
  const sameOrganisation = (email, website) =>
    email === website || email.endsWith(`.${website}`) || website.endsWith(`.${email}`);

  function update(callout) {
    const form = callout.closest('form');
    // The type of entity is asked as organisation_type at signup, entity_type on the form.
    const entityType = value(form, 'organisation_type') || value(form, 'entity_type');
    const email = emailDomain(callout.dataset.email?.toLowerCase() || value(form, 'email'));
    const website = websiteDomain(value(form, 'website'));

    if (entityType === 'sole_proprietor' || !email) {
      // A sole proprietorship is the person, so a personal address is expected.
      callout.hidden = true;
    } else if (website) {
      callout.hidden = sameOrganisation(email, website);
    } else {
      // With no website to check against, only a personal address is worth raising.
      callout.hidden = !consumerProviders.has(email);
    }
  }

  const updateAll = () =>
    document.querySelectorAll('[data-email-domain-callout]').forEach(update);

  // Form media runs again whenever HTMX swaps a form in, so listen only once.
  if (!window.emailDomainCalloutListening) {
    window.emailDomainCalloutListening = true;
    document.addEventListener('input', updateAll);
    document.addEventListener('change', updateAll);
    document.addEventListener('DOMContentLoaded', updateAll);
  }
  updateAll();
})();
