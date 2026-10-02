/* Motion from the ABDM V4 reference, with native links and reduced-motion support. */
(() => {
  const hero = document.querySelector(".sbx-hero");
  if (!hero) return;

  const wheel = hero.querySelector(".sbx-hero-wheel");
  const quickNav = document.querySelector(".sbx-quick-nav");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const desktop = window.matchMedia("(min-width: 761px)");
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  let frame;
  let lastY = window.scrollY;
  let showNav = false;

  document.documentElement.classList.add("marketing-smooth-scroll");

  const updateScroll = () => {
    frame = undefined;
    const y = window.scrollY;
    if (Math.abs(y - lastY) > 6) {
      showNav = y > 140 && y < lastY;
      lastY = y;
    }
    if (y <= 140) showNav = false;
    if (quickNav) {
      // A scrolling page must not hide the link a keyboard user is using.
      const visible =
        desktop.matches &&
        (showNav || quickNav.contains(document.activeElement));
      quickNav.classList.toggle("is-visible", visible);
      quickNav.inert = !visible;
      quickNav.setAttribute("aria-hidden", String(!visible));
    }
    if (!wheel) return;
    const progress = reducedMotion.matches
      ? 0
      : Math.max(0, Math.min(1, y / (hero.offsetHeight * 0.85)));
    const dx =
      hero.offsetWidth / 2 - (wheel.offsetLeft + wheel.offsetWidth / 2);
    const dy =
      hero.offsetHeight / 2 - (wheel.offsetTop + wheel.offsetHeight / 2);
    wheel.style.setProperty("--wheel-x", `${dx * progress}px`);
    wheel.style.setProperty("--wheel-y", `${dy * progress}px`);
    wheel.style.setProperty("--wheel-scale", String(1 + progress * 0.5));
    wheel.style.setProperty("--wheel-alpha", String(0.11 + progress * 0.22));
  };
  const scheduleScroll = () => {
    if (frame === undefined) frame = window.requestAnimationFrame(updateScroll);
  };
  window.addEventListener("scroll", scheduleScroll, { passive: true });
  window.addEventListener("resize", scheduleScroll);
  window.addEventListener("pageshow", scheduleScroll);
  desktop.addEventListener("change", scheduleScroll);
  reducedMotion.addEventListener("change", scheduleScroll);
  quickNav?.addEventListener("focusout", scheduleScroll);
  if ("ResizeObserver" in window)
    new ResizeObserver(scheduleScroll).observe(hero);
  scheduleScroll();

  document.querySelectorAll(".sbx-magnetic").forEach((button) => {
    const reset = () => {
      button.style.removeProperty("--magnetic-x");
      button.style.removeProperty("--magnetic-y");
    };
    button.addEventListener("pointermove", (event) => {
      if (reducedMotion.matches || !finePointer.matches) return;
      const rect = button.getBoundingClientRect();
      button.style.setProperty(
        "--magnetic-x",
        `${(event.clientX - rect.left - rect.width / 2) * 0.18}px`,
      );
      button.style.setProperty(
        "--magnetic-y",
        `${(event.clientY - rect.top - rect.height / 2) * 0.3}px`,
      );
    });
    button.addEventListener("pointerleave", reset);
    button.addEventListener("pointercancel", reset);
    button.addEventListener("blur", reset);
    reducedMotion.addEventListener("change", reset);
    finePointer.addEventListener("change", reset);
  });

  // Retain real documentation links while reproducing the persistent hover state.
  const milestones = [...document.querySelectorAll(".sbx-milestone")];
  const selectMilestone = (selected) =>
    milestones.forEach((milestone, index) => {
      milestone.classList.toggle("is-active", index === selected);
      milestone.classList.toggle("is-complete", index <= selected);
    });
  milestones.forEach((milestone, index) => {
    milestone.addEventListener("pointerenter", () => selectMilestone(index));
    milestone.addEventListener("focus", () => selectMilestone(index));
  });
  selectMilestone(0);

  // Native details work without JavaScript; enforce the same one-open step on
  // browsers that do not yet support named details groups.
  const steps = [...document.querySelectorAll(".sbx-journey-step")];
  steps.forEach((step) =>
    step.addEventListener("toggle", () => {
      if (step.open)
        steps.forEach((other) => {
          if (other !== step) other.open = false;
        });
    }),
  );

  window.addEventListener("pagehide", () => {
    window.cancelAnimationFrame(frame);
    frame = undefined;
  });
})();
