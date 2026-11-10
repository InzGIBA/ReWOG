# Vendor patches

Vendored three.js r170 (MIT, see LICENSE). Pinned, patched locally.

- addons/GLTFLoader.js: the relative import
  `../utils/BufferGeometryUtils.js` became the import-map alias
  `three/addons/utils/BufferGeometryUtils.js`, so the loader works
  from inline data URIs in the single-file HTML viewer.
