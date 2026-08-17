const app = document.getElementById("app");
const logoutBtn = document.getElementById("logoutBtn");
let filter = "firing";
let selectedId = null;

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
  await fetch("/api/v1/logout", { method: "POST" });
  selectedId = null;
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
      <p class="muted">Use the shared app password. Email approval links still work without this.</p>
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

function cardList(items) {
  if (!items.length) {
    app.innerHTML = `<section class="card"><p class="muted">No alerts in this view.</p></section>`;
    return;
  }
  app.innerHTML = items
    .map(
      (item) => `
      <article class="card" data-id="${item.id}">
        <div><span class="badge">${item.severity || "none"}</span><span class="badge">${item.status}</span></div>
        <h2>${item.alertname || "alert"}</h2>
        <p class="muted">${item.namespace || "-"} · ${item.action_type || ""}</p>
        <p>${item.summary || ""}</p>
      </article>`
    )
    .join("");
  app.querySelectorAll("article.card").forEach((el) => {
    el.addEventListener("click", () => {
      selectedId = Number(el.dataset.id);
      render();
    });
  });
}

function detailView(item) {
  const rec = item.recommendation || {};
  const target = rec.target || {};
  app.innerHTML = `
    <p><button class="ghost" id="backBtn">← Inbox</button></p>
    <section class="card">
      <div><span class="badge">${item.severity || "none"}</span><span class="badge">${item.status}</span></div>
      <h2>${item.alertname || "alert"}</h2>
      <p class="muted">${item.namespace || "-"} · updated ${item.updated_at || ""}</p>
      <p><strong>${rec.summary || ""}</strong></p>
      <p class="muted">${rec.root_cause || ""}</p>
      <p><strong>Proposed:</strong> ${rec.action_type || "acknowledge"}</p>
      <p class="muted">${target.kind || ""} ${target.namespace || ""}/${target.name || ""} ${target.replicas != null ? "replicas=" + target.replicas : ""}</p>
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
      <button class="primary" id="approveBtn">Approve</button>
      <button class="secondary" id="rejectBtn">Reject</button>
      <button class="secondary" id="ackBtn">Acknowledge only</button>
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
  }
}

async function render() {
  try {
    const session = await api("/api/v1/session");
    if (!session.authenticated) {
      logoutBtn.hidden = true;
      loginForm();
      return;
    }
    logoutBtn.hidden = false;
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
    cardList(items);
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
