const pageSize = 50;
let offset = 0;
let total = 0;

function cell(text) {
  const node = document.createElement("td");
  node.textContent = text;
  return node;
}

async function loadEmployers() {
  const query = document.querySelector("#employer-query").value.trim();
  const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
  if (query) params.set("q", query);
  const response = await fetch(`/api/h1b-employers?${params}`);
  if (!response.ok) throw new Error("Unable to load H-1B employer data");
  const data = await response.json();
  total = data.total;
  const rows = data.items.map((employer) => {
    const row = document.createElement("tr");
    const source = document.createElement("a");
    source.href = employer.source_url;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.textContent = "DOL disclosure";
    const sourceCell = document.createElement("td");
    sourceCell.append(source);
    row.append(
      cell(employer.employer_name),
      cell(String(employer.fiscal_year)),
      cell(Number(employer.lca_worker_positions).toLocaleString()),
      sourceCell,
    );
    return row;
  });
  document.querySelector("#employer-list").replaceChildren(...rows);
  document.querySelector("#employer-total").textContent = Number(data.summary.employers).toLocaleString();
  document.querySelector("#employer-year").textContent = data.summary.latest_fiscal_year || "Awaiting import";
  const start = total ? offset + 1 : 0;
  document.querySelector("#employer-range").textContent = `${start}–${Math.min(offset + data.count, total)} of ${total.toLocaleString()}`;
  document.querySelector("#previous-page").disabled = offset === 0;
  document.querySelector("#next-page").disabled = offset + data.count >= total;
}

async function loadExactCompanyCoverage() {
  const response = await fetch("/api/coverage");
  if (!response.ok) throw new Error("Unable to load exact H-1B company coverage");
  const coverage = await response.json();
  document.querySelector("#exact-h1b-companies").textContent = Number(
    coverage.exact_h1b_companies,
  ).toLocaleString();
  document.querySelector("#exact-h1b-sites").textContent = Number(
    coverage.exact_h1b_with_verified_discovery_seeds,
  ).toLocaleString();
  document.querySelector("#exact-h1b-feeds").textContent = Number(
    coverage.exact_h1b_with_successful_sources,
  ).toLocaleString();
}

document.querySelector("#employer-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  offset = 0;
  await loadEmployers();
});
document.querySelector("#previous-page").addEventListener("click", async () => {
  offset = Math.max(0, offset - pageSize);
  await loadEmployers();
});
document.querySelector("#next-page").addEventListener("click", async () => {
  offset += pageSize;
  await loadEmployers();
});
Promise.all([loadEmployers(), loadExactCompanyCoverage()]).catch((error) => {
  document.querySelector("#employer-range").textContent = error.message;
});
