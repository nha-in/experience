/**
 * The (i) buttons beside choices, milestones and tracks
 * (components/info_popover.html).
 *
 * Each panel is a native popover, so the browser draws it in the top layer,
 * closes it on Escape and on a click elsewhere, and keeps only one open. What
 * is left for us: put it under its button and inside the content column, and
 * open it on hover and on focus, which a button alone cannot do.
 *
 * With scripting off the button still opens its panel; the browser places it.
 */
(() => {
  'use strict';

  const GAP = 4;
  const MARGIN = 8;
  const CLOSE_DELAY = 150;
  const hover = window.matchMedia('(hover: hover)');
  const native = typeof HTMLElement.prototype.showPopover === 'function';
  let closing;

  // Without the popover API the panel would sit in the page, always open.
  if (!native) {
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('[data-info-panel]').forEach((panel) => {
        panel.hidden = true;
      });
    });
  }

  const panelOf = (button) => document.getElementById(button.getAttribute('popovertarget'));
  const buttonOf = (panel) => document.querySelector(`[popovertarget="${panel.id}"]`);
  const isOpen = (panel) => (native ? panel.matches(':popover-open') : !panel.hidden);
  const openPanels = () => [...document.querySelectorAll('[data-info-panel]')].filter(isOpen);
  const held = (panel, button) =>
    panel.matches(':hover') ||
    button?.matches(':hover') ||
    panel.contains(document.activeElement) ||
    button === document.activeElement;

  /** Under the button, right edges aligned, inside the content column. */
  function place(button, panel) {
    const anchor = button.getBoundingClientRect();
    const { clientWidth, clientHeight } = document.documentElement;
    // The column, so a panel in the first column does not land on the sidebar.
    const column = (button.closest('main') || document.documentElement).getBoundingClientRect();
    const left = Math.max(column.left, 0) + MARGIN;
    const right = Math.min(column.right, clientWidth) - MARGIN;
    panel.style.left = '0px';
    panel.style.top = '0px';
    const { width, height } = panel.getBoundingClientRect();
    const room = clientHeight - anchor.bottom - GAP - MARGIN;
    const above = height > room && anchor.top - GAP - height > MARGIN;
    panel.style.left = `${Math.round(Math.max(left, Math.min(anchor.right - width, right - width)))}px`;
    panel.style.top = `${Math.round(above ? anchor.top - GAP - height : anchor.bottom + GAP)}px`;
  }

  function open(button) {
    const panel = panelOf(button);
    if (!panel) return;
    clearTimeout(closing);
    if (isOpen(panel)) return;
    if (native) panel.showPopover();
    else panel.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    place(button, panel);
  }

  function close(panel) {
    if (!isOpen(panel)) return;
    if (native) panel.hidePopover();
    else panel.hidden = true;
    buttonOf(panel)?.setAttribute('aria-expanded', 'false');
  }

  /** Close every panel the pointer has left and that holds no focus. */
  function closeIdle() {
    clearTimeout(closing);
    closing = setTimeout(() => {
      openPanels().forEach((panel) => {
        if (!held(panel, buttonOf(panel))) close(panel);
      });
    }, CLOSE_DELAY);
  }

  document.addEventListener('mouseover', (event) => {
    if (!hover.matches) return;
    const button = event.target.closest?.('[data-info-button]');
    if (button) open(button);
  });

  document.addEventListener('mouseout', (event) => {
    if (!hover.matches) return;
    if (event.target.closest?.('[data-info-button], [data-info-panel]')) closeIdle();
  });

  // Tabbing to the button opens its panel; tabbing on closes it, so the panel
  // never covers the control that focus has moved to.
  document.addEventListener('focusin', (event) => {
    const button = event.target.closest?.('[data-info-button]');
    if (button) open(button);
    openPanels().forEach((panel) => {
      if (!held(panel, buttonOf(panel))) close(panel);
    });
  });

  // The button's own click is handled by the browser, which also closes the
  // panel on Escape and on a click elsewhere. Keep our state in step.
  document.addEventListener(
    'toggle',
    (event) => {
      const panel = event.target;
      if (!panel.matches?.('[data-info-panel]')) return;
      const button = buttonOf(panel);
      button?.setAttribute('aria-expanded', String(event.newState === 'open'));
      if (event.newState === 'open' && button) place(button, panel);
    },
    true,
  );

  if (!native) {
    document.addEventListener('click', (event) => {
      const button = event.target.closest?.('[data-info-button]');
      if (button) {
        const panel = panelOf(button);
        if (panel) {
          if (isOpen(panel)) close(panel);
          else open(button);
        }
        return;
      }
      if (!event.target.closest?.('[data-info-panel]')) openPanels().forEach(close);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      openPanels().forEach((panel) => {
        if (panel.contains(document.activeElement)) buttonOf(panel)?.focus();
        close(panel);
      });
    });
  }

  // Scrolling and resizing move the button under an open panel, and an htmx
  // swap can take the button away while its panel is open.
  function follow() {
    openPanels().forEach((panel) => {
      const button = buttonOf(panel);
      if (!button?.isConnected) close(panel);
      else place(button, panel);
    });
  }

  window.addEventListener('scroll', follow, true);
  window.addEventListener('resize', follow);
})();
