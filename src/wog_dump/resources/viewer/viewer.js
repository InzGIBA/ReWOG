/* WOG Dump scene viewer: renders one exported GLB with the part list
 * from the scene manifest, optional disassembly states, animation
 * playback and part visibility tools. Configuration comes from the
 * #wog-config JSON block the generator embeds next to this script. */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

const config = JSON.parse(
  document.getElementById("wog-config").textContent
);

const I18N = {
  en: {
    loading: "loading…",
    markers: "markers",
    wireframe: "wireframe",
    rotate: "rotate",
    screenshot: "screenshot",
    clear: "clear",
    search: "search parts",
    hide: "hide",
    isolate: "isolate",
    showAll: "show all",
    parts: "parts",
    state: "state",
    explode: "explode",
    play: "play",
    pause: "pause",
    assembled: "assembled",
    "field-strip": "field strip",
    exploded: "exploded",
    partsState: "parts grid",
    group: "group",
    size: "size",
    triangles: "triangles",
    noParts: "no matching parts",
  },
  ru: {
    loading: "загрузка…",
    markers: "маркеры",
    wireframe: "сетка",
    rotate: "вращение",
    screenshot: "снимок",
    clear: "сброс",
    search: "поиск деталей",
    hide: "скрыть",
    isolate: "только это",
    showAll: "показать всё",
    parts: "детали",
    state: "состояние",
    explode: "разнести",
    play: "пуск",
    pause: "пауза",
    assembled: "собрано",
    "field-strip": "разборка",
    exploded: "разнесено",
    partsState: "сетка деталей",
    group: "группа",
    size: "размер",
    triangles: "треугольников",
    noParts: "нет совпадений",
  },
};

let lang = (navigator.language || "en").toLowerCase().startsWith("ru")
  ? "ru"
  : "en";
try {
  const stored = localStorage.getItem("wog-viewer-lang");
  if (stored === "ru" || stored === "en") lang = stored;
} catch (error) {
  /* file:// origins may block storage; the default stays */
}

function t(key) {
  return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
}

const canvas = document.getElementById("viewport");
const renderer = new THREE.WebGLRenderer({
  canvas: canvas,
  antialias: true,
  preserveDrawingBuffer: true,
});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(
  50,
  window.innerWidth / window.innerHeight,
  0.001,
  1000
);
camera.position.set(0.8, 0.5, 1.2);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.autoRotate = false;
controls.autoRotateSpeed = 2.0;

function themeColors() {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  return dark
    ? { background: 0x16181d, text: "#d8dde6", accent: "#4a9eff" }
    : { background: 0xf2f4f8, text: "#26292f", accent: "#1a6fd4" };
}

let colors = themeColors();
scene.background = new THREE.Color(colors.background);

const ambient = new THREE.AmbientLight(0xffffff, 0.6);
scene.add(ambient);
const key = new THREE.DirectionalLight(0xffffff, 1.2);
key.position.set(2, 3, 2);
scene.add(key);

const partNames = new Set(
  (config.manifest.parts || []).concat(config.manifest.field_strip || [])
);
const annotations = config.manifest.annotations || [];
let markersVisible = true;
const markers = [];
const nameToMeshes = new Map();
const originalEmissive = new Map();
let selected = null;
let triangleCount = 0;

function collectNames(root) {
  root.traverse(function (object) {
    if (object.isMesh && object.name) {
      const list = nameToMeshes.get(object.name) || [];
      list.push(object);
      nameToMeshes.set(object.name, list);
    }
  });
}

function meshesForPart(name) {
  const exact = nameToMeshes.get(name) || [];
  if (exact.length) return exact;
  const meshes = [];
  nameToMeshes.forEach(function (list, meshName) {
    if (meshName.indexOf(name) === 0) meshes.push.apply(meshes, list);
  });
  return meshes;
}

function setHighlight(name) {
  nameToMeshes.forEach(function (list) {
    list.forEach(function (mesh) {
      const materials = Array.isArray(mesh.material)
        ? mesh.material
        : [mesh.material];
      materials.forEach(function (material) {
        const key = material.uuid;
        if (originalEmissive.has(key)) {
          material.emissive.setHex(originalEmissive.get(key).hex);
          material.emissiveIntensity = originalEmissive.get(key).intensity;
        }
      });
    });
  });
  originalEmissive.clear();
  selected = name;
  if (!name) {
    refreshList();
    return;
  }
  meshesForPart(name).forEach(function (mesh) {
    const materials = Array.isArray(mesh.material)
      ? mesh.material
      : [mesh.material];
    materials.forEach(function (material) {
      if (!material.emissive) return;
      originalEmissive.set(material.uuid, {
        hex: material.emissive.getHex(),
        intensity: material.emissiveIntensity,
      });
      material.emissive.setHex(0x2a6fb8);
      material.emissiveIntensity = 0.9;
    });
  });
  refreshList();
}

function focusOn(name) {
  const meshes = meshesForPart(name);
  if (!meshes.length) return;
  const box = new THREE.Box3();
  meshes.forEach(function (mesh) {
    box.expandByObject(mesh);
  });
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length();
  controls.target.copy(center);
  const direction = camera.position
    .clone()
    .sub(controls.target)
    .normalize();
  camera.position.copy(
    center.clone().add(direction.multiplyScalar(Math.max(size, 0.05) * 2.5))
  );
}

function refreshList() {
  document.querySelectorAll("#parts li").forEach(function (item) {
    item.classList.toggle("selected", item.dataset.part === selected);
  });
}

function refreshStats() {
  document.getElementById("stats").textContent =
    triangleCount.toLocaleString() + " " + t("triangles") + ", " +
    nameToMeshes.size + " " + t("parts");
}

const raycaster = new THREE.Raycaster();
let downXY = null;
canvas.addEventListener("pointerdown", function (event) {
  downXY = [event.clientX, event.clientY];
});
canvas.addEventListener("pointerup", function (event) {
  if (!downXY) return;
  const moved =
    Math.abs(event.clientX - downXY[0]) + Math.abs(event.clientY - downXY[1]);
  downXY = null;
  if (moved > 6) return; // orbit drag, not a click
  const click = new THREE.Vector2(
    (event.clientX / window.innerWidth) * 2 - 1,
    -(event.clientY / window.innerHeight) * 2 + 1
  );
  raycaster.setFromCamera(click, camera);
  const hits = raycaster.intersectObjects(scene.children, true);
  if (!hits.length || !hits[0].object.isMesh) return;
  let object = hits[0].object;
  let name = null;
  while (object) {
    if (partNames.has(object.name)) {
      name = object.name;
      break;
    }
    object = object.parent;
  }
  setHighlight(name === selected ? null : name);
  if (name) {
    focusOn(name);
    const annotation = annotations.find(function (a) {
      return a.name === name;
    });
    if (annotation) showCard(annotation);
  } else {
    hideCard();
  }
});

function buildMarkers(gltfScene) {
  if (!annotations.length) return;
  const texture = markerTexture();
  const group = new THREE.Group();
  annotations.forEach(function (annotation) {
    const material = new THREE.SpriteMaterial({
      map: texture,
      color: annotation.group === "field_strip" ? 0xffa726 : 0x4a9eff,
      depthTest: false,
    });
    const sprite = new THREE.Sprite(material);
    sprite.position.set(
      annotation.center[0],
      annotation.center[1],
      annotation.center[2]
    );
    sprite.scale.setScalar(0.02);
    sprite.userData.part = annotation.name;
    sprite.renderOrder = 10;
    group.add(sprite);
    markers.push(sprite);
  });
  gltfScene.add(group);
  const toggle = document.getElementById("markers");
  if (toggle) {
    toggle.style.display = "";
    toggle.addEventListener("click", function () {
      markersVisible = !markersVisible;
      markers.forEach(function (marker) {
        marker.visible = markersVisible;
      });
    });
  }
}

function markerTexture() {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff";
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = "#1a1d23";
  ctx.stroke();
  return new THREE.CanvasTexture(canvas);
}

function showCard(annotation) {
  let card = document.getElementById("card");
  if (!card) {
    card = document.createElement("div");
    card.id = "card";
    document.body.appendChild(card);
  }
  card.style.display = "block";
  card.textContent = "";
  const title = document.createElement("strong");
  title.textContent = annotation.name;
  card.appendChild(title);
  const size = annotation.bbox_size || [0, 0, 0];
  card.appendChild(document.createElement("br"));
  card.appendChild(
    document.createTextNode(t("group") + ": " + annotation.group)
  );
  card.appendChild(document.createElement("br"));
  card.appendChild(
    document.createTextNode(
      t("size") + ": " +
      size.map(function (v) { return (v * 1000).toFixed(0) + " mm"; }).join(" x ")
    )
  );
}

function hideCard() {
  const card = document.getElementById("card");
  if (card) card.style.display = "none";
}

function selectPart(name) {
  const next = selected === name ? null : name;
  setHighlight(next);
  if (next) {
    focusOn(next);
    const annotation = annotations.find(function (a) {
      return a.name === next;
    });
    if (annotation) showCard(annotation);
  } else {
    hideCard();
  }
}

function buildList() {
  const list = document.getElementById("parts");
  const seen = new Set();
  (config.manifest.parts || []).forEach(function (name) {
    if (seen.has(name)) return;
    seen.add(name);
    const item = document.createElement("li");
    item.textContent = name;
    item.dataset.part = name;
    item.addEventListener("click", function () {
      selectPart(name);
    });
    list.appendChild(item);
  });
}

document.getElementById("search").addEventListener("input", function (event) {
  const query = event.target.value.trim().toLowerCase();
  document.querySelectorAll("#parts li").forEach(function (item) {
    const match = !query || item.dataset.part.toLowerCase().indexOf(query) !== -1;
    item.style.display = match ? "" : "none";
  });
});

/* -- part visibility -------------------------------------------------- */

const hiddenParts = new Set();

function markHidden(name, hidden) {
  if (hidden) hiddenParts.add(name);
  else hiddenParts.delete(name);
  document.querySelectorAll("#parts li").forEach(function (item) {
    if (item.dataset.part === name) {
      item.classList.toggle("hidden-part", hidden);
    }
  });
}

document.getElementById("hide").addEventListener("click", function () {
  if (!selected) return;
  meshesForPart(selected).forEach(function (mesh) {
    mesh.visible = false;
  });
  markHidden(selected, true);
});

document.getElementById("isolate").addEventListener("click", function () {
  if (!selected) return;
  const keep = new Set(meshesForPart(selected));
  nameToMeshes.forEach(function (list, meshName) {
    list.forEach(function (mesh) {
      mesh.visible = keep.has(mesh);
    });
    markHidden(meshName, !keep.has(list[0]));
  });
});

document.getElementById("show-all").addEventListener("click", function () {
  nameToMeshes.forEach(function (list) {
    list.forEach(function (mesh) {
      mesh.visible = true;
    });
  });
  hiddenParts.forEach(function (name) {
    markHidden(name, false);
  });
  hiddenParts.clear();
});

/* -- disassembly states ----------------------------------------------- */

const states = config.states || {};
const stateNames = Object.keys(states);
const nodesByName = new Map();
let currentState = config.manifest.state || "assembled";

function collectNodes(root) {
  root.traverse(function (object) {
    if (!object.name) return;
    const list = nodesByName.get(object.name) || [];
    list.push(object);
    nodesByName.set(object.name, list);
  });
}

function applyState(name) {
  const table = states[name];
  if (!table) return;
  nodesByName.forEach(function (nodes, nodeName) {
    const position = table[nodeName];
    if (!position) return;
    nodes.forEach(function (node) {
      node.position.set(position[0], position[1], position[2]);
    });
  });
  currentState = name;
}

function applyExplode(fraction) {
  const assembled = states.assembled;
  const exploded = states.exploded;
  if (!assembled || !exploded) return;
  nodesByName.forEach(function (nodes, nodeName) {
    const from = assembled[nodeName];
    const to = exploded[nodeName];
    if (!from || !to) return;
    nodes.forEach(function (node) {
      node.position.set(
        from[0] + (to[0] - from[0]) * fraction,
        from[1] + (to[1] - from[1]) * fraction,
        from[2] + (to[2] - from[2]) * fraction
      );
    });
  });
  currentState = "exploded";
}

function stateLabel(name) {
  if (name === "parts") return t("partsState");
  return t(name);
}

function buildStateBar() {
  if (!stateNames.length) return;
  const bar = document.getElementById("statebar");
  const select = document.getElementById("state-select");
  const explode = document.getElementById("explode");
  bar.style.display = "flex";
  stateNames.forEach(function (name) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = stateLabel(name);
    select.appendChild(option);
  });
  select.value = currentState;
  if (currentState === "exploded") explode.value = "100";
  select.addEventListener("change", function () {
    applyState(select.value);
    explode.value = select.value === "exploded" ? "100" : "0";
  });
  const hasExplode = Boolean(states.assembled && states.exploded);
  explode.parentElement.style.display = hasExplode ? "" : "none";
  explode.addEventListener("input", function () {
    applyExplode(parseInt(explode.value, 10) / 100);
    select.value = "exploded";
  });
}

/* -- animation playback ----------------------------------------------- */

const clock = new THREE.Clock();
let mixer = null;
let clips = [];
let currentAction = null;
let playing = false;
let updateTimeline = null;

function buildAnimationBar(gltf) {
  if (!gltf.animations || !gltf.animations.length) return;
  mixer = new THREE.AnimationMixer(gltf.scene);
  clips = gltf.animations.map(function (clip) {
    return { clip: clip, action: mixer.clipAction(clip) };
  });
  const bar = document.getElementById("animbar");
  const select = document.getElementById("clip-select");
  const playButton = document.getElementById("play");
  const timeline = document.getElementById("timeline");
  const timeLabel = document.getElementById("time-label");
  const speed = document.getElementById("speed");
  bar.style.display = "flex";
  if (!stateNames.length) bar.classList.add("no-states");
  clips.forEach(function (entry, index) {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = entry.clip.name || "clip " + (index + 1);
    select.appendChild(option);
  });

  function selectClip(index) {
    if (currentAction) currentAction.stop();
    currentAction = clips[index].action;
    currentAction.reset().play();
    currentAction.paused = !playing;
    timeline.max = String(clips[index].clip.duration);
    timeline.value = "0";
  }

  select.addEventListener("change", function () {
    selectClip(parseInt(select.value, 10));
  });
  playButton.addEventListener("click", function () {
    playing = !playing;
    if (currentAction) currentAction.paused = !playing;
    playButton.textContent = playing ? t("pause") : t("play");
  });
  timeline.addEventListener("input", function () {
    if (!currentAction) return;
    currentAction.time = parseFloat(timeline.value);
    mixer.update(0);
    timeLabel.textContent = currentAction.time.toFixed(2) + "s";
  });
  speed.addEventListener("change", function () {
    mixer.timeScale = parseFloat(speed.value);
  });
  selectClip(0);
  playButton.textContent = t("play");
  updateTimeline = function () {
    if (!currentAction) return;
    if (playing) timeline.value = String(currentAction.time);
    timeLabel.textContent = currentAction.time.toFixed(2) + "s";
  };
}

/* -- screenshot -------------------------------------------------------- */

document.getElementById("screenshot").addEventListener("click", function () {
  renderer.render(scene, camera);
  const link = document.createElement("a");
  link.download =
    (config.manifest.weapon || "scene") + "-" + currentState + ".png";
  link.href = renderer.domElement.toDataURL("image/png");
  link.click();
});

/* -- localization ------------------------------------------------------ */

function applyI18n() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach(function (element) {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
  document.getElementById("lang").textContent = lang === "ru" ? "EN" : "RU";
  const select = document.getElementById("state-select");
  Array.prototype.forEach.call(select.options, function (option) {
    option.textContent = stateLabel(option.value);
  });
  const playButton = document.getElementById("play");
  playButton.textContent = playing ? t("pause") : t("play");
  refreshStats();
  if (selected) {
    const annotation = annotations.find(function (a) {
      return a.name === selected;
    });
    if (annotation) showCard(annotation);
  }
}

document.getElementById("lang").addEventListener("click", function () {
  lang = lang === "ru" ? "en" : "ru";
  try {
    localStorage.setItem("wog-viewer-lang", lang);
  } catch (error) {
    /* storage unavailable: language applies to this page only */
  }
  applyI18n();
});

/* -- scene loading ------------------------------------------------------ */

const gltfLoader = new GLTFLoader();
gltfLoader.setMeshoptDecoder(MeshoptDecoder);
gltfLoader.load(config.glbUrl, function (gltf) {
  scene.add(gltf.scene);
  collectNames(gltf.scene);
  collectNodes(gltf.scene);
  buildList();
  buildMarkers(gltf.scene);
  buildStateBar();
  buildAnimationBar(gltf);
  if (states[currentState]) applyState(currentState);

  const cameras = [];
  gltf.scene.traverse(function (object) {
    if (object.isCamera) cameras.push(object);
  });
  if (cameras.length) {
    cameras[0].position.length &&
      camera.position.copy(cameras[0].position);
    const target = new THREE.Vector3();
    const box = new THREE.Box3().setFromObject(gltf.scene);
    box.getCenter(target);
    controls.target.copy(target);
  } else {
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const center = box.getCenter(new THREE.Vector3());
    controls.target.copy(center);
    const size = box.getSize(new THREE.Vector3()).length();
    camera.position.copy(
      center.clone().add(new THREE.Vector3(size, size * 0.6, size * 1.2))
    );
  }

  gltf.scene.traverse(function (object) {
    if (object.isMesh && object.geometry) {
      const index = object.geometry.getIndex();
      triangleCount += index
        ? index.count / 3
        : object.geometry.getAttribute("position").count / 3;
    }
  });
  applyI18n();
});

document.getElementById("wireframe").addEventListener("click", function () {
  scene.traverse(function (object) {
    if (!object.isMesh) return;
    const materials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    materials.forEach(function (material) {
      material.wireframe = !material.wireframe;
    });
  });
});

document.getElementById("autorotate").addEventListener("click", function () {
  controls.autoRotate = !controls.autoRotate;
});

document.getElementById("clear").addEventListener("click", function () {
  setHighlight(null);
  hideCard();
});

window.addEventListener("resize", function () {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

applyI18n();

function animate() {
  requestAnimationFrame(animate);
  const delta = clock.getDelta();
  if (mixer) {
    mixer.update(delta);
    if (updateTimeline) updateTimeline();
  }
  controls.update();
  renderer.render(scene, camera);
}
animate();