/* Weapon page: three.js viewer from the vendored runtime plus the
 * stage buttons with live logs.
 *
 * A module script: the import map in the page resolves "three" and
 * "three/addons/" to the vendored runtime, and the module scope keeps
 * these helpers out of the global namespace. Inline onclick handlers
 * reach runStage and WEAPON through window.
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const TOKEN = location.pathname.split("/")[2];
const WEAPON = location.pathname.split("/").pop();
const BASE = `/t/${TOKEN}`;

const viewport = document.getElementById("viewport");
const renderer = new THREE.WebGLRenderer({ canvas: viewport, antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1d23);
const camera = new THREE.PerspectiveCamera(
  50,
  window.innerWidth / window.innerHeight,
  0.001,
  1000
);
camera.position.set(0.8, 0.5, 1.2);
const controls = new OrbitControls(camera, viewport);
controls.enableDamping = true;

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const key = new THREE.DirectionalLight(0xffffff, 1.2);
key.position.set(2, 3, 2);
scene.add(key);

function fetchMeta() {
  fetch(`${BASE}/api/weapons/${WEAPON}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((meta) => {
      if (!meta) {
        document.getElementById("meta").textContent = "unknown weapon";
        return;
      }
      const bits = [WEAPON, meta.scene ? "scene ok" : "no scene"];
      if (meta.manifest && meta.manifest.meshes) {
        bits.push(meta.manifest.meshes + " meshes");
        bits.push(meta.manifest.parts.length + " parts");
      }
      document.getElementById("meta").textContent = bits.join(" / ");
      document.getElementById("viewerlink").innerHTML =
        '<a href="' + BASE + '/view/' + WEAPON + '">open full viewer</a>';
    });
}

function logLine(text) {
  const log = document.getElementById("log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

function runStage(stage, weapon) {
  const state = document.getElementById("state").value;
  const body = { stage: stage, weapon: weapon };
  if (stage === "build-scene" && state) body.state = state;
  fetch(BASE + "/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) =>
    r.json().then((payload) => {
      if (!r.ok) {
        logLine("rejected: " + payload.detail);
        return;
      }
      logLine("started: " + payload.args.join(" "));
      const source = new EventSource(BASE + "/api/events");
      source.addEventListener("done", () => {
        source.close();
        logLine("finished");
        fetchMeta();
      });
      source.onmessage = (event) => logLine(event.data);
    })
  );
}

window.runStage = runStage;
window.WEAPON = WEAPON;

fetchMeta();

const glbUrl = BASE + "/data/scenes/" + WEAPON + "/" + WEAPON + ".glb";
fetch(glbUrl, { method: "HEAD" }).then((head) => {
  if (!head.ok) return;
  const loader = new GLTFLoader();
  loader.load(
    glbUrl,
    (gltf) => {
      scene.add(gltf.scene);
      const box = new THREE.Box3().setFromObject(gltf.scene);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3()).length();
      controls.target.copy(center);
      camera.position.copy(
        center
          .clone()
          .add(new THREE.Vector3(size * 0.8, size * 0.5, size * 1.2))
      );
    },
    undefined,
    () => logLine("GLB failed to load")
  );
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
