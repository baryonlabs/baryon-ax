(() => {
  const search = document.getElementById("oss-search");
  const status = document.getElementById("oss-status");
  const license = document.getElementById("oss-license");
  const count = document.getElementById("oss-result-count");
  const empty = document.getElementById("oss-empty");
  const groups = [...document.querySelectorAll(".oss-group")];
  const rows = [...document.querySelectorAll(".oss-table tbody tr")];
  if (!rows.length) return;

  const apply = () => {
    const query = (search?.value || "").trim().toLowerCase();
    const statusValue = status?.value || "";
    const licenseValue = license?.value || "";
    let visible = 0;
    rows.forEach((row) => {
      const text = `${row.dataset.name || ""} ${row.textContent || ""}`.toLowerCase();
      const matches =
        (!query || text.includes(query)) &&
        (!statusValue || row.dataset.status === statusValue) &&
        (!licenseValue || row.dataset.licenseFamily === licenseValue);
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    groups.forEach((group) => {
      const hasVisible = [...group.querySelectorAll("tbody tr")].some((row) => !row.hidden);
      group.hidden = !hasVisible;
    });
    if (count) count.textContent = String(visible);
    if (empty) empty.hidden = visible !== 0;
  };

  [search, status, license].forEach((element) => {
    element?.addEventListener("input", apply);
    element?.addEventListener("change", apply);
  });
  apply();
})();
