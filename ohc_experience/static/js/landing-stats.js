/* Placeholder figures and simulated updates from the design, pending the stats API. */
(() => {
  const panel = document.querySelector(".sbx-stats");
  if (!panel) return;

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const formatter = new Intl.NumberFormat("en-IN");
  const rows = [...panel.querySelectorAll(".sbx-stat-value")].map((element) => {
    const accessibleValue = element.previousElementSibling;
    return {
      element,
      accessibleValue,
      value: Number(accessibleValue.textContent.replace(/\D/g, "")),
    };
  });
  const entranceDigits = rows.flatMap((row, rowIndex) =>
    [...row.element.querySelectorAll(".sbx-digit")].map(
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
  let flipTimer;
  let observer;

  const clearFlips = () => {
    window.clearTimeout(flipTimer);
    flipTimer = undefined;
    panel.querySelectorAll(".sbx-digit--flipping").forEach((element) => {
      element.classList.remove("sbx-digit--flipping");
    });
  };

  const pause = () => {
    window.clearInterval(timer);
    timer = undefined;
    clearFlips();
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

  const updateValue = (row, value) => {
    if (row.value === value) return;
    const previousDigits = String(row.value);
    const nextDigits = String(value);
    const formatted = formatter.format(value);
    const characters = [...formatted];

    // A carry can add a digit and move the Indian-grouping separators.
    if (row.element.children.length !== characters.length) {
      row.element.replaceChildren(
        ...characters.map((character) => {
          const element = document.createElement("span");
          element.className =
            character === "," ? "sbx-digit-separator" : "sbx-digit";
          return element;
        }),
      );
    }

    let digitIndex = 0;
    characters.forEach((character, index) => {
      const element = row.element.children[index];
      if (character === ",") return;
      const previousIndex =
        previousDigits.length - nextDigits.length + digitIndex;
      element.textContent = character;
      element.classList.toggle(
        "sbx-digit--flipping",
        previousDigits[previousIndex] !== character,
      );
      digitIndex += 1;
    });
    row.value = value;
    // This stays readable to assistive technology without repeated live announcements.
    row.accessibleValue.textContent = formatted;
  };

  const demoTick = () => {
    const chance = Math.random();
    clearFlips();
    updateValue(rows[0], rows[0].value + 1 + Math.floor(Math.random() * 12));
    updateValue(rows[1], rows[1].value + (chance < 0.35 ? 1 : 0));
    updateValue(rows[2], rows[2].value + (chance < 0.12 ? 1 : 0));
    // Successful integrators stays at the reference's 604 placeholder.
    flipTimer = window.setTimeout(clearFlips, 420);
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
    if (timer !== undefined) return;
    if (entranceFinished) {
      timer = window.setInterval(demoTick, 1600);
      return;
    }
    renderEntrance();
    timer = window.setInterval(() => {
      frame += 1;
      renderEntrance();
      if (frame > 40) {
        pause();
        settleEntrance();
        resume();
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
