const app = document.getElementById("app");
const logoutBtn = document.getElementById("logoutBtn");
const userLabel = document.getElementById("userLabel");
let filter = "firing";
let selectedId = null;
let logoutUrl = "";

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    filter = btn.dataset.filter;
    selectedId = null;
    render();
  });
});

logoutBtn.addEventListener("click", async () => {
  const dest = logoutUrl;
  await fetch("/api/v1/logout", { method: "POST", credentials: "same-origin" });
  selectedId = null;
  logoutUrl = "";
  if (dest) {
    window.location.href = dest;
    return;
  }
  render();
});

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) throw new Error("auth");
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function loginForm(error = "") {
  app.innerHTML = `
    <section class="card">
      <h2>Unlock inbox</h2>
      <p class="muted">On the cluster, OpenShift login is used (Google or htpasswd). This password is only for local access.</p>
      ${error ? `<p class="err">${error}</p>` : ""}
      <form id="loginForm">
        <input type="password" name="password" autocomplete="current-password" placeholder="Password" required/>
        <button class="primary" type="submit">Continue</button>
      </form>
      <p class="muted">Add this page to your iPhone Home Screen from the Share sheet.</p>
    </section>`;
  document.getElementById("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const password = new FormData(e.target).get("password");
    try {
      await api("/api/v1/login", { method: "POST", body: JSON.stringify({ password }) });
      render();
    } catch (err) {
      loginForm("Wrong password");
    }
  });
}

function severityClass(sev) {
  const s = (sev || "none").toLowerCase();
  if (s === "critical" || s === "error") return "badge critical";
  if (s === "warning") return "badge warning";
  return "badge";
}

function prBlock(url, error) {
  if (url) {
    return `<p class="pr"><a class="pr-link" href="${url}" target="_blank" rel="noopener">${url}</a></p>`;
  }
  if (error) {
    return `<p class="err">GitOps PR failed: ${error}</p>`;
  }
  return "";
}

function byPriority(items) {
  const sev = { critical: 0, error: 1, warning: 2, info: 3, none: 4 };
  const risk = { high: 0, medium: 1, low: 2 };
  return [...items].sort((a, b) => {
    const s = (sev[a.severity] ?? 5) - (sev[b.severity] ?? 5);
    if (s) return s;
    const r = (risk[a.risk] ?? 3) - (risk[b.risk] ?? 3);
    if (r) return r;
    const fire = Number(a.status !== "firing") - Number(b.status !== "firing");
    if (fire) return fire;
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  });
}

function cardList(items) {
  if (!items.length) {
    app.innerHTML = `<section class="card"><p class="muted">No alerts in this view.</p></section>`;
    return;
  }
  app.innerHTML = items
    .map(
      (item) => `
      <article class="card" data-id="${item.id}">
        <div><span class="${severityClass(item.severity)}">${item.severity || "none"}</span><span class="badge">${item.status}</span><span class="badge">${item.investigation_status === "running" ? "investigating" : (item.action_type || "acknowledge")}</span></div>
        <h2>${item.alertname || "alert"}</h2>
        <p class="muted">${item.namespace || "-"}</p>
        <p>${item.summary || ""}</p>
        ${item.root_cause ? `<p class="muted">${item.root_cause}</p>` : ""}
        ${prBlock(item.pr_url, item.pr_error)}
      </article>`
    )
    .join("");
  app.querySelectorAll("article.card").forEach((el) => {
    el.addEventListener("click", () => {
      selectedId = Number(el.dataset.id);
      render();
    });
  });
  app.querySelectorAll("a.pr-link").forEach((el) => {
    el.addEventListener("click", (ev) => ev.stopPropagation());
  });
}

function detailView(item) {
  const rec = item.recommendation || {};
  const target = rec.target || {};
  const steps = rec.how_to_resolve || [];
  const gitops = rec.gitops || {};
  const investigating = rec.investigation_status === "running";
  const findings = rec.investigation || "";
  const stepHtml = steps.length
    ? `<ol class="steps">${steps.map((s) => `<li>${s}</li>`).join("")}</ol>`
    : investigating
      ? `<p class="muted">Waiting for the read-only investigation to finish.</p>`
      : `<p class="muted">No step list yet. Tap Ask Grok again to refresh the recommendation.</p>`;
  const targetTxt = [target.kind, target.namespace && target.name ? `${target.namespace}/${target.name}` : target.name, target.replicas != null ? `replicas=${target.replicas}` : ""]
    .filter(Boolean)
    .join(" ");
  app.innerHTML = `
    <p><button class="ghost" id="backBtn">← Inbox</button></p>
    <section class="card">
      <p class="eyebrow">Alert</p>
      <div><span class="${severityClass(item.severity)}">${item.severity || "none"}</span><span class="badge">${item.status}</span><span class="badge">risk ${rec.risk || "-"}</span></div>
      <h2>${item.alertname || "alert"}</h2>
      <p class="muted">${item.namespace || "-"} · updated ${item.updated_at || ""}</p>
    </section>
    ${
      rec.pr_url || rec.pr_error
        ? `<section class="card">
      <p class="eyebrow">GitOps pull request</p>
      ${prBlock(rec.pr_url, rec.pr_error)}
      <p class="muted">Review and merge in GitHub. The app does not merge or oc apply.</p>
    </section>`
        : ""
    }
    <section class="card">
      <p class="eyebrow">Grok recommendation</p>
      <p><strong>${rec.summary || "No summary"}</strong></p>
      <p>${rec.root_cause || ""}</p>
      ${findings ? `<p class="eyebrow" style="margin-top:16px">Investigation</p><pre class="findings">${findings}</pre>` : ""}
      <p class="eyebrow" style="margin-top:16px">Permanent corrective actions</p>
      ${stepHtml}
    </section>
    <section class="card">
      <p class="eyebrow">Executable action</p>
      <p><strong>${rec.action_type || "acknowledge"}</strong> ${targetTxt}</p>
      <p class="muted">${item.approval_effect || ""}</p>
      ${gitops.rationale ? `<p class="muted">${gitops.rationale}</p>` : ""}
      ${gitops.path ? `<p class="muted">GitOps path: ${gitops.path}</p>` : ""}
      ${item.action_result ? `<p class="ok">${item.action_result}</p>` : ""}
      <div id="actions"></div>
    </section>`;
  document.getElementById("backBtn").addEventListener("click", () => {
    selectedId = null;
    render();
  });
  if (["firing", "acknowledged", "resolved"].includes(item.status)) {
    const actions = document.getElementById("actions");
    actions.innerHTML = `
      <button class="primary" id="approveBtn">Approve executable action</button>
      <button class="secondary" id="rejectBtn">Reject</button>
      <button class="secondary" id="ackBtn">Acknowledge only</button>
      <button class="secondary" id="reanalyzeBtn">Ask Grok again</button>
      <p id="actionErr" class="err"></p>`;
    const run = (path) => async () => {
      try {
        await api(path, { method: "POST", body: "{}" });
        selectedId = item.id;
        render();
      } catch (err) {
        document.getElementById("actionErr").textContent = err.message;
      }
    };
    document.getElementById("approveBtn").onclick = run(`/api/v1/incidents/${item.id}/approve`);
    document.getElementById("rejectBtn").onclick = run(`/api/v1/incidents/${item.id}/reject`);
    document.getElementById("ackBtn").onclick = run(`/api/v1/incidents/${item.id}/acknowledge`);
    document.getElementById("reanalyzeBtn").onclick = run(`/api/v1/incidents/${item.id}/reanalyze`);
  }
  if (investigating) {
    window.setTimeout(() => {
      if (selectedId === item.id) render();
    }, 3000);
  }
}

async function render() {
  try {
    const session = await api("/api/v1/session");
    if (!session.authenticated) {
      logoutBtn.hidden = true;
      userLabel.hidden = true;
      logoutUrl = "";
      loginForm();
      return;
    }
    logoutBtn.hidden = false;
    logoutUrl = session.logout_url || "";
    if (session.user) {
      userLabel.hidden = false;
      userLabel.textContent = session.user;
    } else {
      userLabel.hidden = true;
    }
    if (selectedId) {
      const item = await api(`/api/v1/incidents/${selectedId}`);
      detailView(item);
      return;
    }
    const data = await api("/api/v1/incidents");
    let items = data.incidents || [];
    if (filter === "firing") {
      items = items.filter((i) => i.status === "firing");
    } else if (filter === "approved") {
      items = items.filter((i) => ["approved", "rejected", "acknowledged", "resolved"].includes(i.status));
    }
    cardList(byPriority(items));
  } catch (err) {
    if (err.message === "auth") {
      loginForm();
      return;
    }
    app.innerHTML = `<section class="card"><p class="err">${err.message}</p></section>`;
  }
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(() => {});
}

render();
