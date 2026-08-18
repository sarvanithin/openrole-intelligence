const pageSize = 50;
let offset = 0;
let total = 0;
let searchTimer;
let latestRequest = 0;

function cell(text) {
  const node = document.createElement("td");
  node.textContent = text;
  return node;
}

async function loadCompanies() {
  const requestId = ++latestRequest;
  const query = document.querySelector("#company-query").value.trim();
  const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
  if (query) params.set("q", query);
  const response = await fetch(`/api/companies?${params}`);
  if (!response.ok) throw new Error("Unable to load company data");
  const data = await response.json();
  if (requestId !== latestRequest) return;
  total = data.total;
  const covered = data.items.filter((company) => company.source_last_success_at).length;
  const rows = data.items.map((company) => {
    const row = document.createElement("tr");
    const name = document.createElement("a");
    name.href = `/?company=${encodeURIComponent(company.slug)}#jobs`;
    name.textContent = company.name;
    const nameCell = document.createElement("td");
    nameCell.append(name);
    row.append(
      nameCell,
      cell(Number(company.active_jobs).toLocaleString()),
      cell(company.source_last_success_at ? new Date(company.source_last_success_at).toLocaleString() : "—"),
      cell(company.source_last_success_at ? "Successfully covered" : company.coverage_disposition.replaceAll("_", " ")),
    );
    return row;
  });
  document.querySelector("#company-list").replaceChildren(...rows);
  document.querySelector("#company-total").textContent = total.toLocaleString();
  document.querySelector("#company-covered").textContent = String(covered);
  const start = total ? offset + 1 : 0;
  document.querySelector("#company-range").textContent = `${start}–${Math.min(offset + data.count, total)} of ${total.toLocaleString()}`;
  document.querySelector("#previous-page").disabled = offset === 0;
  document.querySelector("#next-page").disabled = offset + data.count >= total;
}

document.querySelector("#company-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  offset = 0;
  await loadCompanies();
});
document.querySelector("#company-query").addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(async () => {
    offset = 0;
    try {
      await loadCompanies();
    } catch (error) {
      document.querySelector("#company-range").textContent = error.message;
    }
  }, 180);
});
document.querySelector("#previous-page").addEventListener("click", async () => {
  offset = Math.max(0, offset - pageSize);
  await loadCompanies();
});
document.querySelector("#next-page").addEventListener("click", async () => {
  offset += pageSize;
  await loadCompanies();
});
loadCompanies().catch((error) => {
  document.querySelector("#company-range").textContent = error.message;
});
