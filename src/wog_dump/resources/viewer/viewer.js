/* WOG Dump scene viewer: renders one exported GLB with the part list
 * from the scene manifest. Configuration comes from the #wog-config
 * JSON block the generator embeds next to this script. */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const config = JSON.parse(
  document.getElementById("wog-config").textContent
);

const canvas = document.getElementById("viewport");
const renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
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
const nameToMeshes = new Map();
const originalEmissive = new Map();
let selected = null;

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
  if (name) focusOn(name);
});

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
      const next = selected === name ? null : name;
      setHighlight(next);
      if (next) focusOn(next);
    });
    list.appendChild(item);
  });
}

const gltfLoader = new GLTFLoader();
gltfLoader.load(config.glbUrl, function (gltf) {
  scene.add(gltf.scene);
  collectNames(gltf.scene);
  buildList();

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

  let triangles = 0;
  gltf.scene.traverse(function (object) {
    if (object.isMesh && object.geometry) {
      const index = object.geometry.getIndex();
      triangles += index ? index.count / 3 : object.geometry.getAttribute("position").count / 3;
    }
  });
  document.getElementById("stats").textContent =
    triangles.toLocaleString() + " triangles, " +
    nameToMeshes.size + " parts";
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
});

window.addEventListener("resize", function () {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
animate();
