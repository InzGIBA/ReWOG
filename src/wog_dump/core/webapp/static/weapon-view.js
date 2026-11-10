/* Weapon page: three.js viewer from the vendored runtime + stage buttons. */
const TOKEN = location.pathname.split("/")[2];
const WEAPON = location.pathname.split("/").pop();
const BASE = `/t/${TOKEN}`;

document.getElementById("meta").textContent = WEAPON;

document.getElementById("import-map").textContent = JSON.stringify({
  imports: {
    three: `${BASE}/vendor/three.module.min.js`,
    "three/addons/": `${BASE}/vendor/addons/`,
  },
});

async function fetchMeta() {
  const response = await fetch(`${BASE}/api/weapons/${WEAPON}`);
  if (!response.ok) {
    document.getElementById("meta").textContent = "unknown weapon";
    return;
  }
  const meta = await response.json();
  const bits = [WEAPON, meta.scene ? "scene ✓" : "no scene"];
  if (meta.manifest && meta.manifest.meshes) {
    bits.push(`${meta.manifest.meshes} meshes`);
    bits.push(`${meta.manifest.parts.length} parts`);
  }
  document.getElementById("meta").textContent = bits.join(" · ");
  if (meta.has_viewer) {
    document.getElementById("viewerlink").innerHTML =
      `<a href="${BASE}/data/scenes/${WEAPON}/viewer.html">open full viewer</a>`;
  }
}
fetchMeta();

function runStage(stage, weapon) {
  const state = document.getElementById("state").value;
  const body = { stage: stage, weapon: weapon };
  if (stage === "build-scene" && state) body.state = state;
  fetch(`${BASE}/api/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (r) => {
    if (!r.ok) {
      logLine(`rejected: ${(await r.json()).detail}`);
      return;
    }
    logLine(`started ${stage}`);
    const source = new EventSource(`${BASE}/api/events`);
    source.addEventListener("done", () => {
      source.close();
      logLine("finished");
      fetchMeta();
    });
    source.onmessage = (event) => logLine(event.data);
  });
}

function logLine(text) {
  const log = document.getElementById("log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

/* 3D view when the GLB exists */
(async function renderModel() {
  const glbUrl = `${BASE}/data/scenes/${WEAPON}/${WEAPON}.glb`;
  const head = await fetch(glbUrl, { method: "HEAD" });
  if (!head.ok) return;

  const THREE = await import("three");
  const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");

  const canvas = document.getElementById("viewport");
  const renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
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
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(2, 3, 2);
  scene.add(key);

  new GLTFLoader().load(glbUrl, (gltf) => {
    scene.add(gltf.scene);
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    controls.target.copy(center);
    camera.position.copy(
      center.clone().add(new THREE.Vector3(size * 0.8, size * 0.5, size))
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
})();
