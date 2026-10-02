/* Rolls each figure's digits into place as the panel first comes into view. */
(() => {
  const panel = document.querySelector(".sbx-stats");
  if (!panel) return;

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const rows = [...panel.querySelectorAll(".sbx-stat-value")];
  const entranceDigits = rows.flatMap((row, rowIndex) =>
    [...row.querySelectorAll(".sbx-digit")].map(
      (element, digitIndex) => ({
        element,
        value: element.textContent,
        offset: digitIndex * 7 + rowIndex * 3,
        settleAt: 8 + rowIndex * 4 + digitIndex * 2,
      }),
    ),
  );
  let frame = 0;
  let entranceFinished = false;
  let inViewport = false;
  let pageActive = true;
  let timer;
  let observer;

  const pause = () => {
    window.clearInterval(timer);
    timer = undefined;
    entranceDigits.forEach(({ element }) => {
      element.classList.remove("sbx-digit--rolling");
    });
  };

  const settleEntrance = () => {
    entranceFinished = true;
    entranceDigits.forEach(({ element, value }) => {
      element.textContent = value;
      element.classList.remove("sbx-digit--rolling");
    });
  };

  const renderEntrance = () => {
    entranceDigits.forEach(({ element, value, offset, settleAt }) => {
      const rolling = frame < settleAt;
      element.textContent = rolling ? String((offset + frame * 3) % 10) : value;
      element.classList.toggle("sbx-digit--rolling", rolling);
    });
  };

  const resume = () => {
    if (
      !pageActive ||
      document.hidden ||
      !inViewport ||
      reducedMotion.matches
    ) {
      pause();
      if (reducedMotion.matches && !entranceFinished) settleEntrance();
      return;
    }
    if (timer !== undefined || entranceFinished) return;
    renderEntrance();
    timer = window.setInterval(() => {
      frame += 1;
      renderEntrance();
      if (frame > 40) {
        pause();
        settleEntrance();
      }
    }, 90);
  };

  const checkViewport = () => {
    const bounds = panel.getBoundingClientRect();
    inViewport = bounds.bottom > 0 && bounds.top < window.innerHeight;
    resume();
  };

  const observe = () => {
    pageActive = true;
    if ("IntersectionObserver" in window) {
      observer ??= new IntersectionObserver((entries) => {
        inViewport = entries.some((entry) => entry.isIntersecting);
        resume();
      });
      observer.observe(panel);
    } else {
      window.addEventListener("scroll", checkViewport, { passive: true });
      window.addEventListener("resize", checkViewport);
    }
    checkViewport();
  };

  reducedMotion.addEventListener("change", resume);
  document.addEventListener("visibilitychange", resume);
  window.addEventListener("pagehide", () => {
    pageActive = false;
    pause();
    observer?.disconnect();
    window.removeEventListener("scroll", checkViewport);
    window.removeEventListener("resize", checkViewport);
  });
  window.addEventListener("pageshow", observe);
  observe();
})();
