// Counts down to when a new code can be asked for, then enables the button.
// The wait itself is enforced server-side; this only keeps the screen honest.
(function () {
  const groups = document.querySelectorAll("[data-resend-countdown]");
  if (!groups.length) return;

  const clock = (seconds) =>
    `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  const tick = () => {
    groups.forEach((group) => {
      const note = group.querySelector("[data-resend-note]");
      const button = group.querySelector("[data-resend-button]");
      const left = Math.round(
        (new Date(group.dataset.resendCountdown) - Date.now()) / 1000,
      );
      if (left > 0) {
        if (note) note.textContent = note.dataset.resendLabel.replace("0:00", clock(left));
        return;
      }
      if (note) note.textContent = "";
      if (button) button.disabled = false;
    });
  };

  tick();
  setInterval(tick, 1000);
})();
