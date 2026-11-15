// Site analytics for baryon-ax.baryon.ai.
// Cloudflare Web Analytics is loaded separately (cookieless beacon at the end of each page).
// Microsoft Clarity records how visitors scroll and click; GA4 counts traffic sources and UTM campaigns.
// Both only measure this static marketing site. The site has no forms, so no form input is collected.
(function () {
  "use strict";

  var CLARITY_ID = "ze3vvsj84m";
  // Set to the GA4 measurement ID (G-XXXXXXXXXX) to enable Google Analytics.
  // When you do, add Google Analytics to the analytics notice in each page footer.
  var GA4_ID = "";

  // Skip local previews so test visits don't pollute the numbers.
  var host = location.hostname;
  if (host !== "baryon-ax.baryon.ai") return;

  function loadScript(src) {
    var s = document.createElement("script");
    s.async = true;
    s.src = src;
    document.head.appendChild(s);
  }

  if (CLARITY_ID) {
    window.clarity = window.clarity || function () { (window.clarity.q = window.clarity.q || []).push(arguments); };
    loadScript("https://www.clarity.ms/tag/" + CLARITY_ID);
  }

  if (GA4_ID) {
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", GA4_ID);
    loadScript("https://www.googletagmanager.com/gtag/js?id=" + GA4_ID);
  }

  // Send one named event to every installed tool.
  function track(name, params) {
    try {
      if (window.clarity) window.clarity("event", name);
      if (window.gtag && GA4_ID) window.gtag("event", name, params || {});
    } catch (_) { /* analytics must never break the page */ }
  }

  // Clicks that tell us whether a visit went anywhere after the first screen.
  document.addEventListener("click", function (event) {
    var el = event.target.closest && event.target.closest("a, button");
    if (!el) return;
    var lang = document.documentElement.lang || "ko";

    if (el.classList.contains("copy")) {
      var head = el.closest(".code-block");
      var label = head && head.querySelector(".code-head span");
      track("copy_command", { block: label ? label.textContent.trim() : "", lang: lang });
      return;
    }

    var href = el.getAttribute("href") || "";
    if (/github\.com\/baryonlabs\/baryon-ax\/issues/.test(href)) {
      track("support_click", { href: href, lang: lang });
    } else if (/github\.com\/baryonlabs\/baryon-ax/.test(href)) {
      track("github_click", { href: href, lang: lang });
    } else if (href === "#start" && (el.classList.contains("btn") || el.classList.contains("header-cta"))) {
      track("start_cta_click", { place: el.classList.contains("header-cta") ? "header" : "hero", lang: lang });
    }
  }, { passive: true });
})();
