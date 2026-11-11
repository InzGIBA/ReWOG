(function () {
'use strict';
/* Catalog page: filter, stage table, stage log via SSE. */
const TOKEN = location.pathname.split("/")[2];
const BASE = `/t/${TOKEN}`;
let weapons = [];
let schema = "";

/* Flag columns in table order, with their human titles for tooltips */
const FLAGS = [
  ["has_key", "key"],
  ["downloaded", "downloaded"],
  ["decrypted", "decrypted"],
  ["unpacked", "unpacked"],
  ["scene", "scene"],
];

async function load() {
  const response = await fetch(`${BASE}/api/catalog`);
  const payload = await response.json();
  weapons = payload.weapons;
  schema = payload.schema;
  render();
}

function render() {
  const needle = document.getElementById("filter").value.toLowerCase();
  const body = document.querySelector("#table tbody");
  body.innerHTML = "";
  const visible = weapons.filter((w) =>
    w.name.toLowerCase().includes(needle)
  );
  visible.forEach((w) => {
    const row = document.createElement("tr");
    row.innerHTML =
      `<td><a href="${BASE}/weapon/${w.name}">${w.name}</a></td>` +
      FLAGS.map(
        ([field, title]) =>
          `<td class="cell-flag"><span class="dot ${w[field] ? "dot-ok" : "dot-off"}" ` +
          `title="${title}: ${w[field] ? "done" : "pending"}"></span></td>`
      ).join("") +
      `<td class="cell-size">${(w.size_bytes / 1e6).toFixed(1)} MB</td>`;
    body.appendChild(row);
  });
  if (!visible.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td class="empty" colspan="${1 + FLAGS.length + 1}">no weapons match</td>`;
    body.appendChild(row);
  }
  const stats = document.getElementById("stats");
  stats.textContent =
    `${visible.length}/${weapons.length} weapons` + (schema ? ` · ${schema}` : "");
}

function runStage(stage, weapon, state) {
  const body = { stage: stage };
  if (weapon) body.weapon = weapon;
  if (state) body.state = state;
  fetch(`${BASE}/api/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then(async (r) => {
      if (!r.ok) {
        const detail = (await r.json()).detail || r.statusText;
        logLine(`rejected: ${detail}`);
        return;
      }
      const started = await r.json();
      logLine(`started: ${started.args.join(" ")}`);
      const source = new EventSource(`${BASE}/api/events`);
      source.addEventListener("done", () => {
        source.close();
        logLine("stage finished");
        load();
      });
      source.onmessage = (event) => logLine(event.data);
    });
}

function logLine(text) {
  const log = document.getElementById("log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

/* "/" focuses the filter, terminal style */
document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.target === document.getElementById("filter")) {
    return;
  }
  event.preventDefault();
  document.getElementById("filter").focus();
});

load();
})();
