const $ = (selector) => document.querySelector(selector);
const money = (n) => new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 0
}).format(Number(n || 0));
const number = (n) => new Intl.NumberFormat("en-IN").format(Number(n || 0));
let cachedRoutes = [];
let toastTimer;

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function post(url, body = {}) {
  return requestJSON(url, { method: "POST", body: JSON.stringify(body) });
}

function setMetric(key, value) {
  document.querySelectorAll(`[data-metric="${key}"]`).forEach((node) => {
    if (key === "total_scanned_gmv" || key === "gmv_at_risk") node.textContent = money(value);
    else node.textContent = number(value);
  });
}

function renderTelemetry(routes) {
  const tbody = $("#telemetry-body");
  const query = ($("#routeFilter").value || "").trim().toLowerCase();
  const filtered = routes.filter(r =>
    [r.bank, r.mode, r.route, r.region, r.status].join(" ").toLowerCase().includes(query)
  );
  tbody.replaceChildren();

  if (!filtered.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 9;
    td.className = "empty-state";
    td.textContent = "No routes match this filter.";
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  filtered.forEach(route => {
    const tr = document.createElement("tr");
    const values = [
      route.bank, route.mode, route.route, number(route.volume),
      money(route.gmv), `${Number(route.failure_rate).toFixed(2)}%`,
      `${Number(route.latency_ms).toFixed(0)} ms`
    ];
    values.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 2) td.className = "route-id";
      if (index === 5 && route.failure_rate >= 3) td.className = "danger-text";
      tr.appendChild(td);
    });

    const statusTd = document.createElement("td");
    const status = document.createElement("span");
    status.className = `status status-${route.status}`;
    status.textContent = route.status;
    statusTd.appendChild(status);
    tr.appendChild(statusTd);

    const actionTd = document.createElement("td");
    const button = document.createElement("button");
    button.className = "mini-btn";
    button.textContent = "Simulate reroute";
    button.disabled = route.status === "NOMINAL";
    button.title = route.status === "NOMINAL" ? "Route is nominal" : "Simulate 35% traffic reroute";
    button.addEventListener("click", () => runAction(
      "/api/reroute", { route_id: route.id }, "Reroute simulation complete"
    ));
    actionTd.appendChild(button);
    tr.appendChild(actionTd);
    tbody.appendChild(tr);
  });
  $("#routeCount").textContent = `${filtered.length} of ${routes.length} routes`;
}

function svgLineChart(container, values, color, suffix, label) {
  const width = 620, height = 205, left = 42, right = 16, top = 15, bottom = 29;
  const plotW = width - left - right, plotH = height - top - bottom;
  const nums = values.map(v => Number(v || 0));
  const max = Math.max(...nums, 1) * 1.15;
  const min = Math.min(0, ...nums);
  const range = Math.max(1, max - min);
  const points = nums.map((v, i) => {
    const x = left + (nums.length <= 1 ? plotW / 2 : i * plotW / (nums.length - 1));
    const y = top + plotH - ((v - min) / range) * plotH;
    return [x, y];
  });
  const path = points.map((p, i) => `${i ? "L" : "M"} ${p[0]} ${p[1]}`).join(" ");
  const area = `${path} L ${points.at(-1)?.[0] ?? left} ${top + plotH} L ${points[0]?.[0] ?? left} ${top + plotH} Z`;
  let grid = "";
  for (let i = 0; i < 4; i++) {
    const y = top + i * plotH / 3;
    grid += `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="#22334a" stroke-dasharray="3 5"/>`;
    grid += `<text x="${left-8}" y="${y+3}" text-anchor="end" fill="#7187a5" font-size="9">${(max - i * max / 3).toFixed(1)}${suffix}</text>`;
  }
  const dots = points.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${color}" stroke="#0d1727" stroke-width="2"/>`).join("");
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${label} chart">${grid}<path d="${area}" fill="${color}" opacity=".09"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>${dots}<text x="${left}" y="${height-7}" fill="#7187a5" font-size="9">Oldest</text><text x="${width-right}" y="${height-7}" text-anchor="end" fill="#7187a5" font-size="9">Latest</text></svg>`;
}

function renderRecommendations(items) {
  const box = $("#recommendations");
  box.replaceChildren();
  items.forEach(item => {
    const card = document.createElement("div");
    card.className = `rec ${item.priority}`;
    const top = document.createElement("div");
    top.className = "rec-top";
    const title = document.createElement("span");
    title.className = "rec-title";
    title.textContent = item.title;
    const priority = document.createElement("span");
    priority.className = "rec-priority";
    priority.textContent = item.priority;
    top.append(title, priority);
    const reason = document.createElement("p");
    reason.textContent = item.reason;
    const action = document.createElement("p");
    action.className = "rec-action";
    action.textContent = `Suggested action: ${item.action}`;
    card.append(top, reason, action);
    box.appendChild(card);
  });
}

function renderEvents(items) {
  const list = $("#event-list");
  list.replaceChildren();
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "empty-state";
    li.textContent = "No events yet.";
    list.appendChild(li);
    return;
  }
  items.slice(0, 12).forEach(event => {
    const li = document.createElement("li");
    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = event.time ? new Date(event.time).toLocaleTimeString() : "";
    const message = document.createElement("span");
    message.textContent = event.message || event.action;
    li.append(time, message);
    list.appendChild(li);
  });
}

function renderMetrics(data) {
  [
    "analyzed_volume", "total_scanned_gmv", "gmv_at_risk",
    "weighted_failure_rate", "degraded_switches", "healthy_switches"
  ].forEach(key => setMetric(key, data[key]));
  $("#modeLabel").textContent = data.simulation_mode || "NOMINAL";
  cachedRoutes = data.routes || [];
  renderTelemetry(cachedRoutes);
  renderRecommendations(data.recommendations || []);
  renderEvents(data.events || []);
  const history = data.history || [];
  svgLineChart($("#failureChart"), history.map(h => h.failure_rate), "#ff647c", "%", "Failure rate");
  svgLineChart($("#latencyChart"), history.map(h => h.latency_ms), "#5bb7ff", "ms", "Latency");
}

async function refreshSwitchGuard() {
  try {
    const data = await requestJSON("/api/switch-metrics");
    renderMetrics(data);
    $("#serverStatus").textContent = "API ONLINE";
    $("#serverStatus").className = "pill pill-green";
    return data;
  } catch (error) {
    $("#serverStatus").textContent = "API ERROR";
    $("#serverStatus").className = "pill";
    toast(`Unable to load metrics: ${error.message}`);
    throw error;
  }
}

async function runAction(url, body, successMessage) {
  const buttons = document.querySelectorAll("button");
  buttons.forEach(b => b.disabled = true);
  try {
    const result = await post(url, body);
    toast(result.message || successMessage);
    await refreshSwitchGuard();
  } catch (error) {
    toast(`Action failed: ${error.message}`);
  } finally {
    buttons.forEach(b => b.disabled = false);
  }
}

$("#sbiBtn").addEventListener("click", () =>
  runAction("/api/inject-failure", { route_id: "sbi-upi-01", severity: "high" }, "SBI UPI degraded"));
$("#hdfcBtn").addEventListener("click", () =>
  runAction("/api/inject-failure", { route_id: "hdfc-nb-01", severity: "critical" }, "HDFC net banking degraded"));
$("#burstBtn").addEventListener("click", () =>
  runAction("/api/peak-burst", {}, "Peak burst simulated"));
$("#resetBtn").addEventListener("click", () =>
  runAction("/api/reset-nominal", {}, "Baseline restored"));
$("#syncBtn").addEventListener("click", async () => {
  try {
    await post("/api/sync-metrics");
    await refreshSwitchGuard();
    toast("Metrics synchronized");
  } catch (error) { toast(error.message); }
});
$("#routeFilter").addEventListener("input", () => renderTelemetry(cachedRoutes));

async function loadExasolStatus() {
  try {
    const data = await requestJSON("/api/exasol-status");
    $("#dbTitle").textContent = data.configured ? "Credentials configured" : "Demo mode · not connected";
    $("#dbMessage").textContent = data.message;
    if (data.configured) $("#dbIndicator").classList.add("connected");
  } catch {
    $("#dbMessage").textContent = "Could not read data platform status.";
  }
}

refreshSwitchGuard().catch(() => {});
loadExasolStatus();
setInterval(() => refreshSwitchGuard().catch(() => {}), 10000);
