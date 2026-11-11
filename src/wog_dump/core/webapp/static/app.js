(function () {
'use strict';
/* Catalog page: filter, stage table, stage log via SSE. */
const TOKEN = location.pathname.split("/")[2];
const BASE = `/t/${TOKEN}`;
let weapons = [];

async function load() {
  const response = await fetch(`${BASE}/api/catalog`);
  const payload = await response.json();
  weapons = payload.weapons;
  render();
}

function render() {
  const needle = document.getElementById("filter").value.toLowerCase();
  const body = document.querySelector("#table tbody");
  body.innerHTML = "";
  weapons
    .filter((w) => w.name.toLowerCase().includes(needle))
    .forEach((w) => {
      const row = document.createElement("tr");
      const flags = [w.has_key, w.downloaded, w.decrypted, w.unpacked, w.scene];
      row.innerHTML =
        `<td><a href="${BASE}/weapon/${w.name}">${w.name}</a></td>` +
        flags.map((f) => `<td>${f ? "✓" : "—"}</td>`).join("") +
        `<td>${(w.size_bytes / 1e6).toFixed(1)} MB</td>`;
      body.appendChild(row);
    });
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

load();
})();
