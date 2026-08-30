const tierNames = {
  A: "Job-specific offer detected",
  B: "Strong employer history",
  C: "Employer history",
  D: "Insufficient evidence",
  E: "Sponsorship unavailable",
};

const reasonNames = {
  current_posting_negative_language_detected: "Current posting contains a negative sponsorship signal",
  current_posting_positive_language_detected: "Current posting contains a job-specific sponsorship offer",
  recent_uscis_initial_approval_history: "Recent USCIS employer approval history",
  recent_dol_lca_worker_position_history: "Recent DOL LCA employer history",
  no_high_confidence_employer_evidence_match: "No high-confidence employer evidence match",
  no_recent_official_sponsorship_history: "No recent official sponsorship history",
  ambiguous_sponsorship_language_requires_review: "Ambiguous sponsorship language requires review",
  conflicting_sponsorship_language_requires_review: "Conflicting sponsorship language requires review",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function relativeTime(value) {
  if (!value) return "Not yet verified";
  const diff = Date.now() - new Date(value).getTime();
  const hours = Math.max(0, Math.round(diff / 3_600_000));
  if (hours < 1) return "Verified just now";
  if (hours < 24) return `Verified ${hours}h ago`;
  const days = Math.round(hours / 24);
  return `Verified ${days}d ago`;
}

function jobDateLabel(job) {
  const date = new Date(job.display_date);
  const value = Number.isNaN(date.getTime())
    ? job.display_date
    : new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(date);
  return job.date_provenance === "source_opened_at"
    ? `Opened ${value}`
    : `First observed ${value}`;
}

function jobCard(job) {
  const tier = job.sponsorship_tier;
  const reason = job.sponsorship_reasons.map((item) => reasonNames[item] || item.replaceAll("_", " "))[0];
  const card = element("article", "job-card");
  const role = element("div");
  role.append(element("span", "company", job.company_name));
  const heading = element("h3");
  const link = element("a", "", job.title);
  link.href = job.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  heading.append(link);
  role.append(heading);
  const meta = element("div", "meta");
  meta.append(
    element("span", "", job.location || "Location not listed"),
    element("span", "", jobDateLabel(job)),
    element("span", "", job.ats_type || "employer site"),
  );
  role.append(meta);

  const evidence = element("div", "evidence");
  const badge = element("span", `tier tier-${tier}`);
  badge.append(element("b", "", tier), document.createTextNode(tierNames[tier] || "Evidence"));
  evidence.append(badge, element("p", "", reason || "Assessment pending"));
  if (job.sponsorship_excerpt) {
    const details = element("details");
    details.append(
      element("summary", "", "Why this tier?"),
      element("p", "", `“${job.sponsorship_excerpt}”`),
      element("small", "", job.sponsorship_rule_version),
    );
    evidence.append(details);
  }
  card.append(role, evidence);
  return card;
}

async function loadStats() {
  const response = await fetch("/api/stats");
  if (!response.ok) throw new Error("Unable to load platform statistics");
  const stats = await response.json();
  document.querySelector("#stat-jobs").textContent = Number(stats.active_jobs).toLocaleString();
  document.querySelector("#stat-companies").textContent = Number(stats.companies).toLocaleString();
  document.querySelector("#stat-fetching-companies").textContent = Number(
    stats.companies_with_current_job_fetches,
  ).toLocaleString();
  document.querySelector("#stat-h1b").textContent = Number(stats.h1b_employers).toLocaleString();
  document.querySelector("#stat-evidence").textContent = Number(stats.jobs_with_evidence).toLocaleString();
  document.querySelector("#stat-freshness").textContent = stats.last_verified_at
    ? relativeTime(stats.last_verified_at).replace("Verified ", "")
    : "Awaiting sync";
  if (stats.demo_mode) {
    document.querySelector("#source-status").lastChild.textContent = " Synthetic demo data";
    document.querySelector("#explorer-kicker").textContent = "Demo explorer";
  }
}

let latestJobsRequest = 0;

function replaceSearchUrl(company, filters) {
  const params = new URLSearchParams();
  if (filters.query) params.set("q", filters.query);
  if (company) params.set("company", company);
  if (filters.location) params.set("location", filters.location);
  if (filters.tier) params.set("tier", filters.tier);
  if (filters.openedWithin) params.set("opened_within_days", filters.openedWithin);
  if (filters.verifiedWithin) {
    params.set("verified_within_hours", filters.verifiedWithin);
  }
  if (filters.sort !== "newest") params.set("sort", filters.sort);
  const search = params.toString();
  history.replaceState(null, "", search ? `/?${search}#jobs` : "/");
}

function loadSearchInputs() {
  const params = new URLSearchParams(window.location.search);
  document.querySelector("#query").value = params.get("q") || "";
  document.querySelector("#location").value = params.get("location") || "";
  const tier = params.get("tier") || "";
  if (["", "A", "B", "C", "D", "E"].includes(tier.toUpperCase())) {
    document.querySelector("#tier").value = tier.toUpperCase();
  }
  const openedWithin = params.get("opened_within_days") || "";
  if (["", "1", "7", "30"].includes(openedWithin)) {
    document.querySelector("#opened-within").value = openedWithin;
  }
  const verifiedWithin = params.get("verified_within_hours") || "";
  if (["", "6", "24", "72"].includes(verifiedWithin)) {
    document.querySelector("#verified-within").value = verifiedWithin;
  }
  const sort = params.get("sort") || "newest";
  if (["newest", "verified"].includes(sort)) {
    document.querySelector("#sort").value = sort;
  }
  return params.get("company") || "";
}

async function loadJobs(company = "") {
  const requestId = ++latestJobsRequest;
  const params = new URLSearchParams();
  const query = document.querySelector("#query").value.trim();
  const location = document.querySelector("#location").value.trim();
  const tier = document.querySelector("#tier").value;
  const openedWithin = document.querySelector("#opened-within").value;
  const verifiedWithin = document.querySelector("#verified-within").value;
  const sort = document.querySelector("#sort").value;
  const filters = { query, location, tier, openedWithin, verifiedWithin, sort };
  replaceSearchUrl(company, filters);
  if (query) params.set("q", query);
  if (company) params.set("company", company);
  if (location) params.set("location", location);
  if (tier) params.set("tier", tier);
  if (openedWithin) params.set("opened_within_days", openedWithin);
  if (verifiedWithin) params.set("verified_within_hours", verifiedWithin);
  if (sort !== "newest") params.set("sort", sort);

  const list = document.querySelector("#job-list");
  const empty = document.querySelector("#empty-state");
  const loading = element("div", "empty");
  loading.append(element("span", "", "Loading current openings…"));
  empty.hidden = true;
  list.replaceChildren(loading);
  const response = await fetch(`/api/jobs?${params}`);
  if (!response.ok) throw new Error("Unable to load jobs");
  const data = await response.json();
  if (requestId !== latestJobsRequest) return;
  document.querySelector("#result-count").textContent = `${data.count} role${data.count === 1 ? "" : "s"} shown`;
  list.replaceChildren(...data.items.map(jobCard));
  empty.hidden = data.items.length !== 0;
}

document.querySelector("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await loadJobs();
  document.querySelector("#jobs").scrollIntoView({ behavior: "smooth" });
});

document.querySelector("#clear-filters").addEventListener("click", async () => {
  document.querySelector("#search-form").reset();
  try {
    await loadJobs();
    document.querySelector("#jobs").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    document.querySelector("#result-count").textContent = error.message;
  }
});

const initialCompany = loadSearchInputs();
Promise.all([loadStats(), loadJobs(initialCompany)]).catch((error) => {
  document.querySelector("#result-count").textContent = error.message;
});
