(() => {
  const section = document.getElementById("how");
  if (!section) return;
  const steps = [...section.querySelectorAll("[data-how-step]")];
  const slides = [...section.querySelectorAll("[data-how-visual]")];
  const progress = section.querySelector(".how-progress span");
  if (!steps.length || !slides.length) return;

  const setActive = (value) => {
    steps.forEach((step) => {
      step.classList.toggle("is-active", Number(step.dataset.howStep) === value);
    });
    slides.forEach((slide) => {
      const active = Number(slide.dataset.howVisual) === value;
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", String(!active));
    });
    if (progress) {
      const ratio = steps.length > 1 ? (value - 1) / (steps.length - 1) : 0;
      progress.style.height = `${Math.round(ratio * 100)}%`;
    }
  };

  let ticking = false;
  const update = () => {
    ticking = false;
    const midpoint = window.innerHeight * 0.46;
    let active = 1;
    steps.forEach((step) => {
      const rect = step.getBoundingClientRect();
      if (rect.top <= midpoint && rect.bottom >= midpoint) {
        active = Number(step.dataset.howStep);
      }
    });
    setActive(active);
  };

  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(update);
  };

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });
  setActive(1);
  update();
})();
