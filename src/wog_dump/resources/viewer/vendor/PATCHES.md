# Vendor patches

Vendored three.js r170 (MIT, see LICENSE). Pinned, patched locally.

- addons/loaders/GLTFLoader.js: the relative import
  `../utils/BufferGeometryUtils.js` became the import-map alias
  `three/addons/utils/BufferGeometryUtils.js`, so the loader resolves
  in both the embedded (data URIs) and linked (prefix map) viewers.
