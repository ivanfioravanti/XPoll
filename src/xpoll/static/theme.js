// Runs before first paint (not deferred) so a saved theme never flashes the default one.
(() => {
  try {
    const saved = localStorage.getItem("xpoll-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.dataset.theme = saved;
      const meta = document.querySelector('meta[name="color-scheme"]');
      if (meta) meta.content = saved;
    }
  } catch (_) {
    /* storage blocked: keep the poll's default theme */
  }
})();
