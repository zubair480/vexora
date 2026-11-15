Local copies of third-party files, so the demo runs with no internet (downloaded 2026-11-14 with the user's OK).

three/       three.js r160 (npm three@0.160.0, MIT license, Copyright 2010-2023 Three.js Authors)
             build/three.module.js and the add-ons the pages import:
             examples/jsm/controls/OrbitControls.js, environments/RoomEnvironment.js, loaders/GLTFLoader.js,
             utils/SkeletonUtils.js, utils/BufferGeometryUtils.js, renderers/CSS2DRenderer.js, objects/Sky.js
             Source: https://cdn.jsdelivr.net/npm/three@0.160.0/
             Pages map them with: "three": "/vendor/three/build/three.module.js", "three/addons/": "/vendor/three/examples/jsm/"

models/      Soldier.glb, the rigged worker (three.js examples r160, https://github.com/mrdoob/three.js/tree/r160/examples/models/gltf)
             Source: https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/models/gltf/Soldier.glb
             model3d.js loads it from here and falls back to that URL.

fonts/       Geist and Geist Mono variable fonts (Vercel, SIL Open Font License 1.1, https://github.com/vercel/geist-font)
             Source: https://cdn.jsdelivr.net/npm/geist@1.9.2/dist/fonts/
             geist.css declares the 'Geist' and 'Geist Mono' families (weights 100-900).

speech/, vexo/   added by other sessions (not part of this download).
