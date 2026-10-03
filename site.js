(() => {
  const header = document.querySelector(".topbar");
  if (!header) return;

  const root = document.documentElement;
  let offset = -1;
  const activeTarget = () => {
    try {
      return document.getElementById(decodeURIComponent(location.hash.slice(1)));
    } catch {
      return null;
    }
  };
  const measure = () => {
    const position = getComputedStyle(header).position;
    const next = position === "sticky" || position === "fixed"
      ? header.getBoundingClientRect().height : 0;
    if (next === offset) return false;
    offset = next;
    root.style.setProperty("--anchor-header-offset", String(offset) + "px");
    return true;
  };
  const revealCoveredTarget = () => {
    const target = activeTarget();
    if (!target || offset <= 0) return;
    const top = target.getBoundingClientRect().top;
    if (top >= -1 && top < offset) {
      target.scrollIntoView({block: "start", behavior: "instant"});
    }
  };
  const update = () => {
    if (measure()) revealCoveredTarget();
  };

  measure();
  requestAnimationFrame(update);
  if ("ResizeObserver" in window) new ResizeObserver(update).observe(header);
  window.addEventListener("resize", update);
  // Let the browser restore its saved position before checking the fragment.
  window.addEventListener("pageshow", event => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      update();
      if (event.persisted) {
        revealCoveredTarget();
      } else {
        const target = activeTarget();
        if (target) target.scrollIntoView({block: "start", behavior: "instant"});
      }
    }));
  });
  window.addEventListener("hashchange", () => requestAnimationFrame(revealCoveredTarget));
  if (document.fonts) document.fonts.ready.then(update);
})();
