(() => {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    nav.addEventListener("click", (event) => {
      if (event.target instanceof HTMLAnchorElement) {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  document.querySelectorAll(".code-block").forEach((block) => {
    const button = block.querySelector(".copy");
    const code = block.querySelector("pre code");
    if (!button || !code) return;
    button.addEventListener("click", async () => {
      const text = code.textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "복사됨";
      } catch {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        button.textContent = "복사됨";
      }
      window.setTimeout(() => { button.textContent = "복사"; }, 1800);
    });
  });
})();
