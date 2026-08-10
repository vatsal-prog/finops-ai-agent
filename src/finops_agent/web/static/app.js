const money = (n) =>
  Number(n || 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

const moneyExact = (n) =>
  Number(n || 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });

async function fetchOverview() {
  const res = await fetch("/api/overview");
  if (!res.ok) throw new Error(`Overview failed (${res.status})`);
  return res.json();
}

function renderOverview(data) {
  const account = data.account || {};
  const period = account.period || {};
  document.getElementById("account-meta").textContent =
    `${account.name || "account"} · ${account.provider || "cloud"} · ` +
    `${period.start || "?"} → ${period.end || "?"} · total ${moneyExact(data.total_cost_usd)}`;

  document.getElementById("total-cost").textContent = moneyExact(data.total_cost_usd);

  const chart = document.getElementById("service-chart");
  chart.innerHTML = "";
  const services = (data.by_service || []).slice(0, 8);
  const max = Math.max(...services.map((s) => s.cost_usd || 0), 1);
  services.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    const pct = ((s.cost_usd || 0) / max) * 100;
    row.innerHTML = `
      <span class="label">${escapeHtml(s.key)}</span>
      <div class="bar-track"><div class="bar-fill" style="--w:${pct}%; animation-delay:${i * 60}ms"></div></div>
      <span class="value">${money(s.cost_usd)} · ${Number(s.share_pct || 0).toFixed(1)}%</span>
    `;
    chart.appendChild(row);
  });

  const anomalies = data.anomalies || {};
  document.getElementById("anomaly-stat").textContent =
    `${anomalies.count || 0} flagged · baseline ${moneyExact(anomalies.baseline_mean_usd)}/day`;
  const anomalyList = document.getElementById("anomaly-list");
  anomalyList.innerHTML = "";
  (anomalies.items || []).slice(0, 4).forEach((a) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${escapeHtml(a.service || "service")}</strong> on ${escapeHtml(a.date || "")} · z=${a.z_score} · ${moneyExact(a.actual_cost_usd)}`;
    anomalyList.appendChild(li);
  });
  if (!(anomalies.items || []).length) {
    anomalyList.innerHTML = "<li>No anomalies in the current window.</li>";
  }

  const waste = data.underutilized || {};
  document.getElementById("waste-stat").textContent =
    `${waste.count || 0} resources · ~${moneyExact(waste.total_monthly_savings_usd)}/mo`;
  const wasteList = document.getElementById("waste-list");
  wasteList.innerHTML = "";
  (waste.items || []).slice(0, 4).forEach((r) => {
    const li = document.createElement("li");
    const cpu = r.avg_cpu_pct == null ? "n/a" : `${r.avg_cpu_pct}% CPU`;
    li.innerHTML = `<strong>${escapeHtml(r.resource_id)}</strong> · ${escapeHtml(r.service)} · ${cpu} · save ${moneyExact(r.estimated_monthly_savings_usd)}/mo`;
    wasteList.appendChild(li);
  });
  if (!(waste.items || []).length) {
    wasteList.innerHTML = "<li>No underutilized resources found.</li>";
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(text, isError = false) {
  const el = document.getElementById("status");
  el.textContent = text || "";
  el.classList.toggle("error", Boolean(isError));
}

function renderAnswer(trace) {
  const block = document.getElementById("answer-block");
  const answer = document.getElementById("answer");
  const traceEl = document.getElementById("trace");
  block.hidden = false;
  answer.textContent = trace.answer || "(no answer)";
  traceEl.innerHTML = "";
  (trace.tool_steps || []).forEach((step) => {
    const li = document.createElement("li");
    const preview = JSON.stringify(step.result ?? {}, null, 0);
    const short = preview.length > 220 ? `${preview.slice(0, 220)}…` : preview;
    li.innerHTML = `
      <code>${escapeHtml(step.name)}</code>
      <span class="args">args: ${escapeHtml(JSON.stringify(step.arguments || {}))}</span>
      <span class="preview">${escapeHtml(short)}</span>
    `;
    traceEl.appendChild(li);
  });
  if (!(trace.tool_steps || []).length) {
    traceEl.innerHTML = "<li>No tools were called.</li>";
  }
}

async function askQuestion(question) {
  const planner = document.getElementById("planner").value;
  const submit = document.getElementById("ask-submit");
  submit.disabled = true;
  setStatus("Agent is querying MCP tools…");
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, planner, lookback_days: 30 }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Ask failed (${res.status})`);
    renderAnswer(data);
    setStatus(
      `Planner: ${data.planner || planner} · ${data.turns || 0} turn(s) · ${(data.tool_steps || []).length} tool call(s)`
    );
  } catch (err) {
    setStatus(err.message || String(err), true);
  } finally {
    submit.disabled = false;
  }
}

function wireForm() {
  const form = document.getElementById("ask-form");
  const question = document.getElementById("question");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const q = question.value.trim();
    if (!q) return;
    await askQuestion(q);
  });

  document.querySelectorAll("#suggestions [data-q]").forEach((btn) => {
    btn.addEventListener("click", () => {
      question.value = btn.getAttribute("data-q") || "";
      question.focus();
    });
  });
}

async function boot() {
  wireForm();
  try {
    const data = await fetchOverview();
    renderOverview(data);
  } catch (err) {
    document.getElementById("account-meta").textContent = err.message || String(err);
  }
}

boot();
