// world3d.js · S9 "world-3d" · a believable piece of Earth around the Vexora site (three.js r160, all procedural).
//
//   import { buildWorld } from './world3d.js';
//   const world = buildWorld({ THREE, scene, renderer, camera, siteRadius: 26, center: new THREE.Vector3(0, 0, -1.5) });
//   each frame: world.update(dt, elapsed);      teardown: world.dispose();
//
// Units are metres; -z is "north" (the far side in model3d.js's default camera). Everything stays outside the fenced
// yard (default x -21..21, z -14..11, the model3d.js fence). buildWorld replaces scene.fog and scene.background with a
// haze matched to a physically based Sky, raises camera.far to 3200 m, and hides model3d.js's old gradient sky dome,
// 280 m gravel plane and grey berm ring if it finds them (pass legacy: false to keep them). No downloads.
//
// Contents: Sky (sun follows the scene's DirectionalLight azimuth, 36 deg up) with a horizon haze band equal to the fog;
// terrain to 2.6 km (site gravel, compacted dirt apron, dry grass, red dirt, rolling hills, far ranges, moving cloud
// shadows on the far ground); an open pit with 8 benches, a ramp and a pit lake; a benched waste-rock dump; a haul road
// with windrows and tyre tracks, an access spur and a light-vehicle track; two power lines with sagging conductors; a
// water tank on a stand; a guyed radio mast with a beacon; a parked ute, containers and HDPE pipe; an overland conveyor
// running off to the horizon; a processing plant (crusher, stockpile, mill, thickeners, silos, stack) with a steam
// plume; eucalyptus and pine stands, scrub, grass tufts and rocks with contact shadows. Life: two haul trucks on a
// pit-to-plant loop (loaded one way, empty back) raising road dust, a light vehicle doing laps with a flashing beacon
// and a stop at the haul-road crossing, drifting clouds, a bird flock and dust over the pit.
import { Sky } from 'three/addons/objects/Sky.js';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';

// ---------- deterministic helpers ----------
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, k) => a + (b - a) * k;
const sstep = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash2(ix, iy) {
  let h = (Math.imul(ix, 374761393) + Math.imul(iy, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
function vnoise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y);
  const fx = x - ix, fy = y - iy;
  const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
  const a = hash2(ix, iy), b = hash2(ix + 1, iy), c = hash2(ix, iy + 1), d = hash2(ix + 1, iy + 1);
  return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
}
function fbm(x, y, oct = 4) {
  let s = 0, amp = 0.5, f = 1, norm = 0;
  for (let i = 0; i < oct; i += 1) { s += amp * vnoise(x * f + i * 17.13, y * f - i * 9.71); norm += amp; amp *= 0.5; f *= 2.03; }
  return s / norm;
}
function angDiff(a, b) { let d = (a - b) % (Math.PI * 2); if (d > Math.PI) d -= Math.PI * 2; if (d < -Math.PI) d += Math.PI * 2; return d; }

// ---------- paths (roads, lanes, lines): evenly spaced samples + chunked nearest-point queries ----------
function samplePath(THREE, pts, { closed = false, step = 4, cx = 0, cz = 0 } = {}) {
  const curve = new THREE.CatmullRomCurve3(pts.map(([x, z]) => new THREE.Vector3(cx + x, 0, cz + z)), closed, 'centripetal');
  const len = curve.getLength();
  const segs = Math.max(4, Math.ceil(len / step));
  const sp = curve.getSpacedPoints(segs);
  const N = sp.length;
  const x = new Float32Array(N), z = new Float32Array(N), tx = new Float32Array(N), tz = new Float32Array(N);
  for (let i = 0; i < N; i += 1) { x[i] = sp[i].x; z[i] = sp[i].z; }
  for (let i = 0; i < N; i += 1) {
    const a = closed ? (i - 1 + segs) % segs : Math.max(0, i - 1);
    const b = closed ? (i + 1) % segs : Math.min(N - 1, i + 1);
    const dx = x[b] - x[a], dz = z[b] - z[a], l = Math.hypot(dx, dz) || 1;
    tx[i] = dx / l; tz[i] = dz / l;
  }
  const path = { x, z, tx, tz, y: new Float32Array(N), n: N, len, closed, chunks: [] };
  const C = 8, PAD = 70;
  for (let i = 0; i < N - 1; i += C) {
    const j = Math.min(N - 1, i + C);
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (let k = i; k <= j; k += 1) { x0 = Math.min(x0, x[k]); x1 = Math.max(x1, x[k]); z0 = Math.min(z0, z[k]); z1 = Math.max(z1, z[k]); }
    path.chunks.push([i, j, x0 - PAD, x1 + PAD, z0 - PAD, z1 + PAD]);
  }
  return path;
}
const NEAR = { d: 0, i: 0, t: 0 };
function nearest(path, px, pz, maxD) { // maxD must stay below the chunk PAD (70 m)
  let best = maxD * maxD, bi = -1, bt = 0;
  const { x, z } = path;
  for (const c of path.chunks) {
    if (px < c[2] || px > c[3] || pz < c[4] || pz > c[5]) continue;
    for (let k = c[0]; k < c[1]; k += 1) {
      const ax = x[k], az = z[k], dx = x[k + 1] - ax, dz = z[k + 1] - az;
      const l2 = dx * dx + dz * dz || 1;
      let t = ((px - ax) * dx + (pz - az) * dz) / l2;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const ex = ax + dx * t - px, ez = az + dz * t - pz, d2 = ex * ex + ez * ez;
      if (d2 < best) { best = d2; bi = k; bt = t; }
    }
  }
  if (bi < 0) return null;
  NEAR.d = Math.sqrt(best); NEAR.i = bi; NEAR.t = bt;
  return NEAR;
}
function pathAt(path, s, out) {
  const L = path.len;
  if (path.closed) { s %= L; if (s < 0) s += L; } else s = clamp(s, 0, L);
  const f = (s / L) * (path.n - 1);
  const i = Math.min(path.n - 2, Math.floor(f)), t = f - i;
  out.x = lerp(path.x[i], path.x[i + 1], t);
  out.z = lerp(path.z[i], path.z[i + 1], t);
  out.y = lerp(path.y[i], path.y[i + 1], t);
  let tx = lerp(path.tx[i], path.tx[i + 1], t), tz = lerp(path.tz[i], path.tz[i + 1], t);
  const l = Math.hypot(tx, tz) || 1;
  out.tx = tx / l; out.tz = tz / l;
  out.slope = (path.y[i + 1] - path.y[i]) / (L / (path.n - 1));
  return out;
}
// trapezoidal speed profile: distance covered after t seconds on a leg of duration T and length L
function trap(t, T, L, acc = 6) {
  const a = Math.min(acc, T / 3), v = L / (T - a);
  if (t <= 0) return 0;
  if (t >= T) return L;
  if (t < a) return (0.5 * v * t * t) / a;
  if (t > T - a) { const r = T - t; return L - (0.5 * v * r * r) / a; }
  return 0.5 * v * a + v * (t - a);
}

function canvasTexture(THREE, w, h, draw, repeat = true) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// ---------- the world ----------
// buildWorld returns at once: the sky, haze and ground the default camera sees are built synchronously, the rest
// (pit, roads, props, vegetation, vehicles) follows in small chunks between frames so page load never freezes.
// If any later chunk throws, the world removes itself and the old backdrop comes back.
export function buildWorld(opts = {}) {
  const api = {
    group: null,
    info: { building: true },
    ready: null,
    update() {},
    dispose() { api.update = () => {}; if (api._kill) api._kill(); if (api._dispose) api._dispose(); api._dispose = null; },
  };
  api.ready = buildInto(api, opts)
    .then(() => { api.info.building = false; return api; })
    .catch((err) => { console.warn('[world3d] build failed, restoring the previous backdrop', err); api.dispose(); return api; });
  return api;
}

async function buildInto(api, { THREE, scene, renderer, camera, siteRadius = 26, center = null, yard = null, legacy = true } = {}) {
  const T0 = performance.now();
  let dead = false;
  api._kill = () => { dead = true; };
  let chunkStart = T0, maxChunk = 0;
  const tick = () => { // yield to the page between chunks (frames keep rendering while the world fills in)
    maxChunk = Math.max(maxChunk, performance.now() - chunkStart);
    return new Promise((r) => setTimeout(() => { chunkStart = performance.now(); r(); }, 0));
  };
  const marks = [];
  const mark = (n) => marks.push(`${n} ${Math.round(performance.now() - T0)}`);
  const cx = center ? center.x : 0, cz = center ? center.z : -1.5;
  const Y = yard || { x0: cx - 21, x1: cx + 21, z0: cz - 12.5, z1: cz + 12.5 };
  const YG = -0.05; // yard ground: 2 cm under model3d.js's gravel plane (-0.03), so that plane wins wherever it is kept
  const camT = { x: 1.3, z: 0.85 }; // model3d.js camera target
  const TREE_R = Math.max(48, siteRadius + 20); // keep trees clear of every orbit position (OrbitControls maxDistance 46)

  const group = new THREE.Group();
  group.name = 'world3d';
  const owned = new Set(); // geometries / materials / textures to dispose
  const own = (o) => { owned.add(o); return o; };
  const colr = (hex) => new THREE.Color(hex);
  const tmpO = new THREE.Object3D();
  const tmpC = new THREE.Color();
  const PT = { x: 0, y: 0, z: 0, tx: 1, tz: 0, slope: 0 };

  // ---------- legacy background from model3d.js (sky dome, 280 m gravel plane, berm ring) ----------
  // (applied together with the new fog/background in one go, once the new ground exists, so nothing flashes)
  const hidden = [];
  const hideLegacy = () => {
    if (!legacy) return;
    scene.traverse((o) => {
      if (!o.isMesh || group.getObjectById(o.id)) return;
      const g = o.geometry, m = o.material, p = g && g.parameters;
      const dome = g && g.type === 'SphereGeometry' && p && p.radius >= 120 && m && m.side === THREE.BackSide;
      const plane = g && g.type === 'PlaneGeometry' && p && p.width >= 200 && p.height >= 200;
      const berm = m && m.isMeshStandardMaterial && m.flatShading && m.color && m.color.getHex() === 0xa9b1b9;
      if (dome || plane || berm) { hidden.push([o, o.visible]); o.visible = false; }
    });
  };

  // ---------- sun, sky, haze ----------
  let sunLight = null;
  scene.traverse((o) => { if (!sunLight && o.isDirectionalLight) sunLight = o; });
  const lightDir = sunLight ? sunLight.position.clone().sub(sunLight.target.position).normalize() : new THREE.Vector3(-13, 24, 11).normalize();
  const sunAz = Math.atan2(lightDir.z, lightDir.x);
  const SUN_EL = THREE.MathUtils.degToRad(36);
  const sunDir = new THREE.Vector3(Math.cos(sunAz) * Math.cos(SUN_EL), Math.sin(SUN_EL), Math.sin(sunAz) * Math.cos(SUN_EL));
  const shDir = [-lightDir.x, -lightDir.z];
  { const l = Math.hypot(shDir[0], shDir[1]) || 1; shDir[0] /= l; shDir[1] /= l; }
  const shLen = 1 / Math.max(0.35, Math.tan(Math.asin(clamp(lightDir.y, -1, 1)))); // shadow length per metre of height

  const HAZE = colr('#d5d9db');
  const prev = { fog: null, background: null, far: camera.far };
  let applied = false;
  const applyToScene = () => { // swap the backdrop: hide the old one, set haze + far plane, show the world
    hideLegacy();
    prev.fog = scene.fog; prev.background = scene.background; prev.far = camera.far;
    scene.fog = new THREE.FogExp2(HAZE.getHex(), 0.0007);
    scene.background = HAZE.clone();
    if (camera.far < 3200) { camera.far = 3200; camera.updateProjectionMatrix(); }
    scene.add(group);
    applied = true;
  };
  api.group = group;
  api._dispose = () => {
    scene.remove(group);
    for (const o of owned) if (o && o.dispose) o.dispose();
    owned.clear();
    for (const [o, v] of hidden) o.visible = v;
    hidden.length = 0;
    if (applied) {
      scene.fog = prev.fog;
      scene.background = prev.background;
      if (camera.far !== prev.far) { camera.far = prev.far; camera.updateProjectionMatrix(); }
      applied = false;
    }
  };

  const sky = new Sky();
  sky.name = 'world3d-sky';
  sky.scale.setScalar(20000);
  sky.frustumCulled = false;
  sky.renderOrder = 1e6; // last of the opaque pass: only the pixels nothing else covered run the scattering shader
  {
    const su = sky.material.uniforms;
    su.turbidity.value = 6.5;
    su.rayleigh.value = 1.35;
    su.mieCoefficient.value = 0.0055;
    su.mieDirectionalG.value = 0.8;
    su.sunPosition.value.copy(sunDir);
    const hz = HAZE.clone().convertLinearToSRGB();
    su.uGain = { value: 0.52 };
    su.uHaze = { value: new THREE.Vector3(hz.r, hz.g, hz.b) };
    su.uHazeLo = { value: -0.02 };
    su.uHazeHi = { value: 0.2 };
    const A = 'gl_FragColor = vec4( retColor, 1.0 );', B = '#include <colorspace_fragment>';
    const fs = sky.material.fragmentShader;
    if (fs.includes(A) && fs.includes(B)) {
      sky.material.fragmentShader = 'uniform float uGain;\nuniform vec3 uHaze;\nuniform float uHazeLo;\nuniform float uHazeHi;\n' + fs
        .replace(A, 'gl_FragColor = vec4( retColor * uGain, 1.0 );')
        .replace(B, B + '\n\t\t\tgl_FragColor.rgb = mix( uHaze, gl_FragColor.rgb, smoothstep( uHazeLo, uHazeHi, direction.y ) );');
      sky.material.needsUpdate = true;
    } else {
      console.warn('[world3d] Sky shader layout changed; haze band not applied');
    }
  }
  own(sky.geometry); own(sky.material);
  group.add(sky);
  mark('sky');

  // ---------- layout ----------
  const PIT = { x: cx - 416, z: cz - 240, a: 150, b: 105, rot: 0.5, benches: 8, bh: 11 };
  const DUMP = { x: cx - 100, z: cz - 385, a: 135, b: 88, rot: -0.3, lifts: 3, lh: 14 };
  const PLANT = { x: cx + 300, z: cz - 300 };
  const qEll = (E, x, z) => {
    const dx = x - E.x, dz = z - E.z, c = Math.cos(E.rot), s = Math.sin(E.rot);
    const u = dx * c + dz * s, v = -dx * s + dz * c;
    return Math.sqrt((u / E.a) * (u / E.a) + (v / E.b) * (v / E.b));
  };
  PIT.wob = [0.07, 3, 1.2, 0.045, 5, 0.3, 0.03, 7, 2.2];
  DUMP.wob = [0.09, 2, 0.7, 0.05, 5, 2.1, 0.035, 9, 0.4];
  const wob = (E, th) => { const w = E.wob; return w ? 1 + w[0] * Math.sin(w[1] * th + w[2]) + w[3] * Math.sin(w[4] * th + w[5]) + w[6] * Math.sin(w[7] * th + w[8]) : 1; };
  const ellXZ = (E, sc, th) => {
    const k = sc * wob(E, th), c = Math.cos(E.rot), s = Math.sin(E.rot), u = E.a * k * Math.cos(th), v = E.b * k * Math.sin(th);
    return [E.x + u * c - v * s, E.z + u * s + v * c];
  };
  const qShape = (E, x, z) => { // like qEll, but against the wobbly outline
    const dx = x - E.x, dz = z - E.z, c = Math.cos(E.rot), s = Math.sin(E.rot);
    const u = (dx * c + dz * s) / E.a, v = (-dx * s + dz * c) / E.b;
    return Math.sqrt(u * u + v * v) / wob(E, Math.atan2(v, u));
  };
  const yardDist = (x, z) => Math.hypot(Math.max(Y.x0 - x, 0, x - Y.x1), Math.max(Y.z0 - z, 0, z - Y.z1));
  const flatMask = (x, z) => sstep(1.3, 1.75, qShape(PIT, x, z)) * sstep(1.05, 1.5, qShape(DUMP, x, z)) * sstep(150, 230, Math.hypot(x - PLANT.x, z - PLANT.z));
  function rawHeight(x, z) {
    const r = Math.hypot(x - cx, z - cz);
    const m = flatMask(x, z);
    let h = YG;
    const u = sstep(8, 110, yardDist(x, z)) * m;
    if (u > 0) h += u * (fbm(x * 0.0105 + 3.1, z * 0.0105 - 1.7, 3) - 0.5) * 7;
    const kMid = sstep(260, 650, r) * m;
    if (kMid > 0) h += kMid * Math.max(0, fbm(x * 0.0042 + 11.3, z * 0.0042 - 4.2, 4) - 0.36) * 170;
    const kFar = sstep(950, 1750, r);
    if (kFar > 0) {
      const ridge = 1 - Math.abs(2 * vnoise(x * 0.0031 + 2.1, z * 0.0031 + 7.7) - 1);
      h += kFar * (Math.max(0, fbm(x * 0.0015 - 7.1, z * 0.0015 + 2.9, 4) - 0.28) * 430 + ridge * ridge * 40);
    }
    return h;
  }

  // roads (relative control points); heights = terrain smoothed along the road
  const P = (pts, opt) => samplePath(THREE, pts, { cx, cz, ...opt });
  const haul = P([[-272, -153], [-226, -128], [-172, -100], [-112, -68], [-64, -46], [-26, -37], [18, -37], [60, -43], [108, -62], [158, -96], [206, -148], [246, -202], [268, -238]], { step: 4 });
  const spur = P([[-64, -46], [-46, -33], [-34, -18], [-29, -5], [-26, 2.5], [-21.6, 3.5]], { step: 3 });
  const lvTrack = P([[0, -58], [44, -52], [68, -26], [72, 10], [58, 42], [20, 56], [-24, 56], [-60, 42], [-72, 8], [-64, -26], [-38, -50]], { step: 3, closed: true });
  const fenceTrack = (() => { // dirt track 5.5 m outside the fence, rounded corners
    const o = 5.5, rc = 7, pts = [];
    const x0 = Y.x0 - o - cx, x1 = Y.x1 + o - cx, z0 = Y.z0 - o - cz, z1 = Y.z1 + o - cz;
    const corner = (px, pz, a0) => { for (let k = 0; k <= 3; k += 1) { const a = a0 + (k / 3) * (Math.PI / 2); pts.push([px + Math.cos(a) * rc, pz + Math.sin(a) * rc]); } };
    corner(x1 - rc, z0 + rc, -Math.PI / 2); corner(x1 - rc, z1 - rc, 0); corner(x0 + rc, z1 - rc, Math.PI / 2); corner(x0 + rc, z0 + rc, Math.PI);
    return P(pts, { step: 2.5, closed: true });
  })();
  const ROADS = [
    { path: haul, hw: 7.5, yOff: 0 },
    { path: spur, hw: 4.2, yOff: 0.04 },
    { path: lvTrack, hw: 2.8, yOff: 0.08 },
    { path: fenceTrack, hw: 1.9, yOff: 0.1 },
  ];
  for (const R of ROADS) {
    const p = R.path, raw = new Float32Array(p.n);
    for (let i = 0; i < p.n; i += 1) raw[i] = rawHeight(p.x[i], p.z[i]);
    const W = Math.round(28 / (p.len / (p.n - 1)));
    for (let i = 0; i < p.n; i += 1) {
      let s = 0, n = 0;
      for (let k = -W; k <= W; k += 1) {
        let j = i + k;
        if (p.closed) j = (j + p.n - 1) % (p.n - 1); else if (j < 0 || j >= p.n) continue;
        s += raw[j]; n += 1;
      }
      p.y[i] = s / n;
    }
  }
  const roadY = (R, nn) => lerp(R.path.y[nn.i], R.path.y[nn.i + 1], nn.t) + R.yOff;
  // final ground height (roads graded in, pit carved) + distance to the nearest road edge
  const HQ = { h: 0, edge: 1e9 };
  function ground(x, z) {
    let h = rawHeight(x, z), edge = 1e9;
    for (const R of ROADS) {
      const nn = nearest(R.path, x, z, R.hw + 24);
      if (!nn) continue;
      edge = Math.min(edge, nn.d - R.hw);
      const w = 1 - sstep(R.hw + 1.5, R.hw + 24, nn.d);
      if (w > 0) h = lerp(h, roadY(R, nn) - 0.22, w);
    }
    const qp = qShape(PIT, x, z);
    if (qp < 1.34) h -= 1.6 * (1 - sstep(1.27, 1.34, qp));
    if (qp < 1.0) h = Math.min(h, lerp(PIT.floorY - 12, h, sstep(0.965, 1.0, qp)));
    HQ.h = h; HQ.edge = edge;
    return HQ;
  }
  const heightAt = (x, z) => ground(x, z).h;
  mark('roads-graded');
  PIT.floorY = YG + 0.12 - PIT.benches * PIT.bh;

  const corridors = []; // [path, halfWidth] kept clear of trees
  const blockedSpots = []; // [x, z, r]

  // ---------- terrain ----------
  const CT = {
    gravel: colr('#d2d5d8'), dirt: colr('#b19c7e'), straw: colr('#ae9a6b'), strawLight: colr('#bfac80'), olive: colr('#857f52'),
    red: colr('#a97a5b'), disturbed: colr('#b19f86'), hill: colr('#77734c'), hillDark: colr('#5f5d3f'),
  };
  const grainTex = own((() => { // tileable gravel/soil grain, written straight into pixels (fast)
    const W = 256, c = document.createElement('canvas');
    c.width = W; c.height = W;
    const g = c.getContext('2d'), img = g.createImageData(W, W), d = img.data, R = rng(11);
    const tvn = (x, y, P) => {
      const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
      const i0 = ((ix % P) + P) % P, i1 = (i0 + 1) % P, j0 = ((iy % P) + P) % P, j1 = (j0 + 1) % P;
      const A = hash2(i0, j0), B = hash2(i1, j0), C = hash2(i0, j1), D = hash2(i1, j1);
      return A + (B - A) * ux + (C - A) * uy + (A - B - C + D) * ux * uy;
    };
    for (let y = 0; y < W; y += 1) {
      for (let x = 0; x < W; x += 1) {
        const peb = tvn(x / 4, y / 4, 64), grit = tvn(x / 1.6, y / 1.6, 160), big = tvn(x / 16, y / 16, 16);
        let v = 0.8 + (big - 0.5) * 0.1 + (grit - 0.5) * 0.16 + (R() - 0.5) * 0.12;
        if (peb > 0.72) v += (peb - 0.72) * 0.9; else if (peb < 0.25) v -= (0.25 - peb) * 0.7;
        const l = Math.max(0, Math.min(255, Math.round(v * 255))), k = (y * W + x) * 4;
        d[k] = l; d[k + 1] = l; d[k + 2] = l; d[k + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  })());
  mark('grainTex');
  await tick(); if (dead) return;
  grainTex.anisotropy = renderer.capabilities.getMaxAnisotropy ? Math.min(8, renderer.capabilities.getMaxAnisotropy()) : 4;
  const U = { time: { value: 0 } };
  const terrainMat = own(new THREE.MeshStandardMaterial({ color: 0xffffff, map: grainTex, vertexColors: true, roughness: 1, metalness: 0 }));
  terrainMat.onBeforeCompile = (sh) => {
    sh.uniforms.uTime = U.time;
    sh.uniforms.uYard = { value: new THREE.Vector4(Y.x0, Y.x1, Y.z0, Y.z1) };
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec2 vWXZ;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvWXZ = ( modelMatrix * vec4( transformed, 1.0 ) ).xz;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
varying vec2 vWXZ;
uniform float uTime;
uniform vec4 uYard;
float wHash( vec2 p ) { p = fract( p * vec2( 123.34, 456.21 ) ); p += dot( p, p + 45.32 ); return fract( p.x * p.y ); }
float wNoise( vec2 p ) { vec2 i = floor( p ), f = fract( p ); vec2 u = f * f * ( 3.0 - 2.0 * f );
  return mix( mix( wHash( i ), wHash( i + vec2( 1.0, 0.0 ) ), u.x ), mix( wHash( i + vec2( 0.0, 1.0 ) ), wHash( i + vec2( 1.0, 1.0 ) ), u.x ), u.y ); }`)
      .replace('#include <map_fragment>', `float wEnvK = 1.0;
#ifdef USE_MAP
  vec4 wG1 = texture2D( map, vMapUv );
  vec4 wG2 = texture2D( map, vMapUv * 0.161 + vec2( 0.37, 0.11 ) );
  diffuseColor *= mix( wG1, wG2, 0.42 );
#endif
  {
    vec2 cp = ( vWXZ - vec2( 5.0, -2.2 ) * uTime ) * 0.0031;
    float cn = wNoise( cp ) * 0.6 + wNoise( cp * 2.3 + 7.1 ) * 0.3 + wNoise( cp * 5.1 - 3.3 ) * 0.1;
    vec2 yd = max( max( vec2( uYard.x, uYard.z ) - vWXZ, vWXZ - vec2( uYard.y, uYard.w ) ), 0.0 );
    float cs = smoothstep( 0.55, 0.7, cn ) * smoothstep( 30.0, 90.0, length( yd ) );
    diffuseColor.rgb *= 1.0 - 0.3 * cs;
    wEnvK = mix( 1.0, 0.45, smoothstep( 1.0, 12.0, length( yd ) ) );
  }`)
      .replace('#include <lights_fragment_maps>', THREE.ShaderChunk.lights_fragment_maps.replace(/iblIrradiance \+= getIBLIrradiance\( ([^)]*) \);/, 'iblIrradiance += getIBLIrradiance( $1 ) * wEnvK;'));
  };
  {
    const radii = [0];
    for (let r = 3; r < 2650; r = r < 66 ? r + 3 : r * 1.06) radii.push(r);
    const SEG = 160, NR = radii.length;
    const nV = 1 + (NR - 1) * SEG;
    const pos = new Float32Array(nV * 3), uv = new Float32Array(nV * 2), col = new Float32Array(nV * 3);
    const put = (vi, x, z) => {
      const q = ground(x, z), h = q.h;
      pos[vi * 3] = x; pos[vi * 3 + 1] = h; pos[vi * 3 + 2] = z;
      uv[vi * 2] = x / 2.4; uv[vi * 2 + 1] = z / 2.4;
      // colour: dry grass / olive / red dirt, hills, graded pads, road shoulders, yard gravel + dirt apron
      const g1 = fbm(x * 0.022 + 4.4, z * 0.022 - 8.1, 3), g2 = vnoise(x * 0.11 + 1.3, z * 0.11 + 5.9);
      tmpC.copy(CT.straw).lerp(CT.olive, sstep(0.42, 0.68, g1));
      tmpC.lerp(CT.strawLight, sstep(0.6, 0.85, g2) * 0.55);
      tmpC.lerp(CT.red, sstep(0.6, 0.74, fbm(x * 0.016 - 9.2, z * 0.016 + 4.7, 3)) * 0.75);
      const hk = sstep(4, 60, h);
      if (hk > 0) tmpC.lerp(CT.hill, hk * 0.6).lerp(CT.hillDark, hk * sstep(0.5, 0.7, g1) * 0.5);
      tmpC.lerp(CT.disturbed, 1 - flatMask(x, z));
      tmpC.lerp(CT.dirt, (1 - sstep(0.5, 9, q.edge)) * 0.85);
      const d = yardDist(x, z) + (g2 - 0.5) * 4;
      tmpC.lerp(CT.dirt, 1 - sstep(3, 13, d));
      tmpC.lerp(CT.gravel, 1 - sstep(-1.2, 1.4, d));
      col[vi * 3] = tmpC.r; col[vi * 3 + 1] = tmpC.g; col[vi * 3 + 2] = tmpC.b;
    };
    mark('terrain-setup');
    put(0, cx, cz);
    for (let i = 1; i < NR; i += 1) {
      for (let j = 0; j < SEG; j += 1) {
        const a = (j / SEG) * Math.PI * 2;
        put(1 + (i - 1) * SEG + j, cx + Math.cos(a) * radii[i], cz + Math.sin(a) * radii[i]);
      }
      if (i === Math.floor(NR / 2)) { await tick(); if (dead) return; }
    }
    mark('terrain-verts');
    // inner disc (yard + 75 m) receives the sun's shadows; the outer ring skips the shadow lookups (it is outside the shadow box anyway)
    const idxIn = [], idxOut = [];
    for (let j = 0; j < SEG; j += 1) idxIn.push(0, 1 + ((j + 1) % SEG), 1 + j);
    for (let i = 1; i < NR - 1; i += 1) {
      const a0 = 1 + (i - 1) * SEG, b0 = 1 + i * SEG, idx = radii[i + 1] <= 75 ? idxIn : idxOut;
      for (let j = 0; j < SEG; j += 1) {
        const j1 = (j + 1) % SEG;
        idx.push(a0 + j, a0 + j1, b0 + j, a0 + j1, b0 + j1, b0 + j);
      }
    }
    const full = new THREE.BufferGeometry();
    full.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    full.setIndex(idxIn.concat(idxOut));
    full.computeVertexNormals();
    const shared = { position: full.attributes.position, normal: full.attributes.normal, uv: new THREE.BufferAttribute(uv, 2), color: new THREE.BufferAttribute(col, 3) };
    for (const [name, idx, rs] of [['world3d-terrain-near', idxIn, true], ['world3d-terrain', idxOut, false]]) {
      const geo = own(new THREE.BufferGeometry());
      for (const k of Object.keys(shared)) geo.setAttribute(k, shared[k]);
      geo.setIndex(idx);
      geo.computeBoundingSphere();
      const terrain = new THREE.Mesh(geo, terrainMat);
      terrain.name = name;
      terrain.receiveShadow = rs;
      group.add(terrain);
    }
  }
  applyToScene();
  mark('terrain');
  await tick(); if (dead) return;

  // ---------- open pit (benches, ramp, lake) and waste dump: lathe strips on ellipses ----------
  const ringMat = own(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, metalness: 0, envMapIntensity: 0.5, flatShading: true, side: THREE.DoubleSide }));
  function lathe(E, prof, NT, colourAt) {
    const pos = [], col = [], idx = [];
    let base = 0;
    for (let i = 0; i < prof.length - 1; i += 1) {
      const A = prof[i], B = prof[i + 1];
      for (let j = 0; j <= NT; j += 1) {
        const th = (j / NT) * Math.PI * 2;
        for (const Pp of [A, B]) {
          const [x, z] = ellXZ(E, Pp[0], th);
          pos.push(x, Pp[1], z);
          colourAt(B, th, Pp === A, tmpC);
          col.push(tmpC.r, tmpC.g, tmpC.b);
        }
      }
      for (let j = 0; j < NT; j += 1) {
        const a = base + j * 2;
        idx.push(a, a + 1, a + 2, a + 2, a + 1, a + 3);
      }
      base += (NT + 1) * 2;
    }
    const g = own(new THREE.BufferGeometry());
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }
  {
    const prof = [[1.32, YG - 2.4, 'apron'], [1.32, YG + 0.12, 'apron'], [1.0, YG + 0.12, 'apron']];
    let s = 1.0, y = YG + 0.12;
    for (let k = 0; k < PIT.benches; k += 1) {
      s -= 0.034; y -= PIT.bh; prof.push([s, y, 'face', k]);
      if (k < PIT.benches - 1) { s -= 0.058; prof.push([s, y, 'berm', k]); }
    }
    prof.push([0.15, y, 'floor'], [0.15, y - 0.4, 'floor'], [0, y - 0.4, 'water']);
    // the haul road meets the rim here; the ramp spirals down from it
    const dx = haul.x[0] - PIT.x, dz = haul.z[0] - PIT.z, c = Math.cos(PIT.rot), sn = Math.sin(PIT.rot);
    const th0 = Math.atan2((-dx * sn + dz * c) / PIT.b, (dx * c + dz * sn) / PIT.a);
    const strata = ['#946a50', '#a5845e', '#8a8179', '#9d7056', '#978b7a', '#85634e', '#a1896a', '#807972'].map((h) => colr(h));
    const berm = colr('#b4a38b'), ramp = colr('#c2b195'), floor = colr('#8f8373'), water = colr('#557b77'), apron = colr('#a99780');
    const geo = lathe(PIT, prof, 120, (B, th, isA, out) => {
      const kind = B[2], k = B[3] || 0;
      const rk = th0 + 0.2 + k * 0.43;
      const onRamp = Math.abs(angDiff(th, rk)) < 0.12;
      if (kind === 'apron') out.copy(apron).multiplyScalar(0.96 + 0.08 * vnoise(th * 14, 3.3));
      else if (kind === 'face') { out.copy(onRamp ? ramp : strata[k % 8]).multiplyScalar(0.9 + 0.2 * vnoise(th * 9, k * 3.1) - k * 0.012); }
      else if (kind === 'berm') out.copy(onRamp ? ramp : berm).multiplyScalar(0.97 - k * 0.022 + 0.06 * vnoise(th * 11, k + 0.5));
      else if (kind === 'water') out.copy(water);
      else out.copy(floor).multiplyScalar(0.95 + 0.1 * vnoise(th * 7, 9.1));
    });
    const pit = new THREE.Mesh(geo, ringMat);
    pit.name = 'world3d-pit';
    group.add(pit);
  }
  {
    const prof = [[1.0, YG - 0.9, 'base']];
    let s = 1.0, y = YG - 0.9;
    const FACE = DUMP.lh / Math.tan(THREE.MathUtils.degToRad(36)) / DUMP.a;
    for (let k = 0; k < DUMP.lifts; k += 1) {
      s -= FACE; y += DUMP.lh + (k === 0 ? 0.9 : 0); prof.push([s, y, 'face', k]);
      if (k < DUMP.lifts - 1) { s -= 0.07; prof.push([s, y, 'berm', k]); }
    }
    prof.push([0, y + 0.6, 'top']);
    const face = colr('#7d746a'), bermC = colr('#91877a'), top = colr('#978b7b');
    const geo = lathe(DUMP, prof, 110, (B, th, isA, out) => {
      const kind = B[2], k = B[3] || 0;
      if (kind === 'face') out.copy(face).multiplyScalar(0.86 + 0.24 * vnoise(th * 46, k * 2.7) + 0.04 * k);
      else if (kind === 'berm') out.copy(bermC).multiplyScalar(0.95 + 0.08 * vnoise(th * 13, k));
      else out.copy(top).multiplyScalar(0.94 + 0.1 * vnoise(th * 8, 4.4));
    });
    const dump = new THREE.Mesh(geo, ringMat);
    dump.name = 'world3d-dump';
    group.add(dump);
  }
  mark('pit+dump');
  await tick(); if (dead) return;

  // ---------- road ribbons (haul road with windrows, access spur, LV track) ----------
  {
    const roadTex = own(canvasTexture(THREE, 128, 512, (g, w, h) => {
      g.fillStyle = '#e6e6e6'; g.fillRect(0, 0, w, h);
      const R = rng(23);
      for (let i = 0; i < 2600; i += 1) {
        const l = (165 + R() * 90) | 0;
        g.fillStyle = `rgba(${l},${l},${l},${(0.2 + R() * 0.5).toFixed(2)})`;
        g.fillRect(R() * w, R() * h, 1 + R() * 2, 1 + R() * 2);
      }
      for (const u of [0.09, 0.44, 0.56, 0.91]) {
        const x0 = (u - 0.065) * w, x1 = (u + 0.065) * w;
        g.fillStyle = 'rgba(96,92,86,0.16)'; g.fillRect(x0, 0, x1 - x0, h);
        g.fillStyle = 'rgba(80,76,70,0.13)';
        for (let y = 0; y < h; y += 12) { g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y + 5); g.lineTo(x1, y + 9); g.lineTo(x0, y + 4); g.fill(); }
      }
      g.fillStyle = 'rgba(255,255,255,0.12)'; g.fillRect(w * 0.47, 0, w * 0.06, h);
    }));
    roadTex.wrapS = THREE.ClampToEdgeWrapping;
    roadTex.anisotropy = grainTex.anisotropy;
    const roadMat = own(new THREE.MeshStandardMaterial({ map: roadTex, vertexColors: true, roughness: 1, metalness: 0, envMapIntensity: 0.55, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
    const RC = { road: colr('#bca98c'), edge: colr('#ae9a7e'), wind: colr('#9a8770'), windBase: colr('#a69279'), track: colr('#b4a286') };
    const pos = [], uv = [], col = [], idx = [];
    const ribbon = (R, prof, windK) => {
      const p = R.path, base = pos.length / 3, m = prof.length;
      for (let i = 0; i < p.n; i += 1) {
        const nx = -p.tz[i], nz = p.tx[i], wk = windK ? windK(i) : 1;
        const shade = 0.95 + 0.1 * vnoise(i * 0.21, R.hw);
        for (const e of prof) {
          pos.push(p.x[i] + nx * e.o, p.y[i] + R.yOff + (e.w ? lerp(-0.25, e.dy, wk) : e.dy), p.z[i] + nz * e.o);
          uv.push(e.u, (i * (p.len / (p.n - 1))) / 14);
          col.push(e.c.r * shade, e.c.g * shade, e.c.b * shade);
        }
        if (i < p.n - 1) {
          for (let j = 0; j < m - 1; j += 1) {
            const a = base + i * m + j, b = a + m;
            idx.push(a, a + 1, b, a + 1, b + 1, b);
          }
        }
      }
    };
    const crossK = (path, others) => (i) => {
      let d = 1e9;
      for (const o of others) { const nn = nearest(o, path.x[i], path.z[i], 30); if (nn) d = Math.min(d, nn.d); }
      return sstep(7, 16, d);
    };
    const hw = 7.5;
    ribbon(ROADS[0], [
      { o: -(hw + 3.6), dy: -0.6, u: 0, c: RC.windBase }, { o: -(hw + 1.8), dy: 1.0, u: 0, c: RC.wind, w: 1 },
      { o: -(hw + 0.3), dy: 0.05, u: 0, c: RC.edge }, { o: -hw * 0.5, dy: 0.13, u: 0.25, c: RC.road }, { o: 0, dy: 0.17, u: 0.5, c: RC.road },
      { o: hw * 0.5, dy: 0.13, u: 0.75, c: RC.road }, { o: hw + 0.3, dy: 0.05, u: 1, c: RC.edge },
      { o: hw + 1.8, dy: 1.0, u: 1, c: RC.wind, w: 1 }, { o: hw + 3.6, dy: -0.6, u: 1, c: RC.windBase },
    ], crossK(haul, [spur, lvTrack]));
    const sw = 4.2;
    ribbon(ROADS[1], [
      { o: -(sw + 2.4), dy: -0.5, u: 0, c: RC.windBase }, { o: -(sw + 1.2), dy: 0.6, u: 0, c: RC.wind, w: 1 },
      { o: -(sw + 0.2), dy: 0.04, u: 0.05, c: RC.edge }, { o: 0, dy: 0.12, u: 0.5, c: RC.road }, { o: sw + 0.2, dy: 0.04, u: 0.95, c: RC.edge },
      { o: sw + 1.2, dy: 0.6, u: 1, c: RC.wind, w: 1 }, { o: sw + 2.4, dy: -0.5, u: 1, c: RC.windBase },
    ], (i) => sstep(10, 20, Math.min(i * (spur.len / (spur.n - 1)), (spur.n - 1 - i) * (spur.len / (spur.n - 1)), (() => { const nn = nearest(lvTrack, spur.x[i], spur.z[i], 30); return nn ? nn.d + 4 : 1e9; })())));
    const tw = 2.8;
    ribbon(ROADS[2], [
      { o: -(tw + 1.4), dy: -0.2, u: 0.3, c: CT.dirt }, { o: -tw, dy: 0.04, u: 0.36, c: RC.track }, { o: 0, dy: 0.08, u: 0.5, c: RC.track },
      { o: tw, dy: 0.04, u: 0.64, c: RC.track }, { o: tw + 1.4, dy: -0.2, u: 0.7, c: CT.dirt },
    ]);
    const fw = 1.9;
    ribbon(ROADS[3], [
      { o: -(fw + 1.1), dy: -0.12, u: 0.38, c: CT.dirt }, { o: -fw, dy: 0.02, u: 0.4, c: RC.track }, { o: 0, dy: 0.05, u: 0.5, c: RC.track },
      { o: fw, dy: 0.02, u: 0.6, c: RC.track }, { o: fw + 1.1, dy: -0.12, u: 0.62, c: CT.dirt },
    ]);
    const geo = own(new THREE.BufferGeometry());
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const roads = new THREE.Mesh(geo, roadMat);
    roads.name = 'world3d-roads';
    group.add(roads);
    for (const R of ROADS) corridors.push([R.path, R.hw + 4]);
  }
  mark('roads');
  await tick(); if (dead) return;

  // ---------- static props: one merged, vertex-coloured mesh per material ----------
  const bins = { paint: [], metal: [], matte: [], glass: [] };
  const add = (key, geo, hex, x, y, z, rx = 0, ry = 0, rz = 0, sx = 1, sy = 1, sz = 1) => {
    let g = geo.index ? geo.toNonIndexed() : geo;
    if (g !== geo) geo.dispose();
    if (g.attributes.uv) g.deleteAttribute('uv');
    if (g.attributes.uv1) g.deleteAttribute('uv1');
    tmpO.position.set(x, y, z); tmpO.rotation.set(rx, ry, rz, 'YXZ'); tmpO.scale.set(sx, sy, sz); tmpO.updateMatrix();
    g.applyMatrix4(tmpO.matrix);
    tmpC.set(hex);
    const n = g.attributes.position.count, c = new Float32Array(n * 3);
    for (let i = 0; i < n; i += 1) { c[i * 3] = tmpC.r; c[i * 3 + 1] = tmpC.g; c[i * 3 + 2] = tmpC.b; }
    g.setAttribute('color', new THREE.BufferAttribute(c, 3));
    g.clearGroups();
    bins[key].push(g);
    return g;
  };
  const box = (key, hex, w, h, d, x, y, z, rx, ry, rz) => add(key, new THREE.BoxGeometry(w, h, d), hex, x, y, z, rx, ry, rz);
  const cyl = (key, hex, r, h, x, y, z, rx, ry, rz, seg = 12, r2 = r, open = false) => add(key, new THREE.CylinderGeometry(r, r2, h, seg, 1, open), hex, x, y, z, rx, ry, rz);
  const QA = new THREE.Quaternion(), V1 = new THREE.Vector3(), V2 = new THREE.Vector3(), UP = new THREE.Vector3(0, 1, 0), M4 = new THREE.Matrix4(), ONE = new THREE.Vector3(1, 1, 1);
  const rod = (key, hex, ax, ay, az, bx, by, bz, r = 0.05, seg = 5) => {
    V1.set(bx - ax, by - ay, bz - az);
    const len = V1.length();
    let g = new THREE.CylinderGeometry(r, r, len, seg, 1, true);
    QA.setFromUnitVectors(UP, V1.normalize());
    g.applyMatrix4(M4.compose(V2.set((ax + bx) / 2, (ay + by) / 2, (az + bz) / 2), QA, ONE));
    return add(key, g, hex, 0, 0, 0);
  };
  const shadows = []; // [x, y, z, along, across, angle]
  const shadowAt = (x, z, w, h, l = 0) => {
    const off = l * shLen * 0.5;
    shadows.push([x + shDir[0] * off, heightAt(x + shDir[0] * off, z + shDir[1] * off), z + shDir[1] * off, w + l * shLen, w, Math.atan2(-shDir[1], shDir[0])]);
  };

  // water tank on a steel stand (NW of the yard)
  {
    const [x, z] = [cx - 31, cz - 21], y = heightAt(x, z), S = '#8c939a';
    for (const [lx, lz] of [[-1.3, -1.3], [1.3, -1.3], [-1.3, 1.3], [1.3, 1.3]]) box('metal', S, 0.16, 6.1, 0.16, x + lx, y + 3.0, z + lz);
    for (const yy of [2.0, 4.1]) {
      for (const [ax, az, bx, bz] of [[-1.3, -1.3, 1.3, -1.3], [1.3, -1.3, 1.3, 1.3], [1.3, 1.3, -1.3, 1.3], [-1.3, 1.3, -1.3, -1.3]]) {
        rod('metal', S, x + ax, y + yy, z + az, x + bx, y + yy, z + bz, 0.04);
        rod('metal', S, x + ax, y + yy - 2.0, z + az, x + bx, y + yy, z + bz, 0.03);
      }
    }
    box('metal', '#7c838a', 3.5, 0.16, 3.5, x, y + 6.1, z);
    cyl('metal', '#c9ced2', 1.6, 3.1, x, y + 7.75, z, 0, 0, 0, 18);
    add('metal', new THREE.ConeGeometry(1.66, 0.55, 18), '#b8bec4', x, y + 9.57, z);
    for (const [ax, az, bx, bz] of [[-1.7, -1.7, 1.7, -1.7], [1.7, -1.7, 1.7, 1.7], [1.7, 1.7, -1.7, 1.7], [-1.7, 1.7, -1.7, -1.7]]) rod('metal', '#f2c230', x + ax, y + 7.1, z + az, x + bx, y + 7.1, z + bz, 0.03);
    for (const lz of [-0.22, 0.22]) rod('metal', S, x + 1.85, y, z + lz, x + 1.85, y + 7.1, z + lz, 0.025);
    rod('matte', '#3c4046', x - 0.9, y + 6.2, z + 0.9, x - 0.9, y + 0.1, z + 0.9, 0.08);
    shadowAt(x, z, 4.4, 10, 9);
    blockedSpots.push([x, z, 7]);
  }
  // guyed radio mast with antennas and a beacon (NE of the yard)
  const mastTop = new THREE.Vector3();
  const guyLines = [];
  {
    const [x, z] = [cx + 42, cz - 27], y = heightAt(x, z), H = 32, NSEG = 10;
    const legA = [Math.PI / 2, Math.PI / 2 + (2 * Math.PI) / 3, Math.PI / 2 + (4 * Math.PI) / 3];
    const legAt = (k, hh) => { const r = lerp(0.75, 0.3, hh / H); return [x + Math.cos(legA[k]) * r, y + hh, z + Math.sin(legA[k]) * r]; };
    for (let sI = 0; sI < NSEG; sI += 1) {
      const h0 = (sI / NSEG) * H, h1 = ((sI + 1) / NSEG) * H;
      const hex = sI >= NSEG - 4 ? (sI % 2 ? '#e9e9e9' : '#c2493d') : '#a4abb2';
      for (let k = 0; k < 3; k += 1) {
        const a0 = legAt(k, h0), a1 = legAt(k, h1), b0 = legAt((k + 1) % 3, h0), b1 = legAt((k + 1) % 3, h1);
        rod('metal', hex, ...a0, ...a1, 0.05);
        rod('metal', hex, ...a0, ...b1, 0.022, 4);
        rod('metal', hex, ...a1, ...b1, 0.022, 4);
      }
    }
    box('metal', '#d7dadd', 0.35, 1.7, 0.14, x + 0.5, y + 28.5, z, 0, 0.3, 0);
    box('metal', '#d7dadd', 0.35, 1.7, 0.14, x - 0.45, y + 28.5, z + 0.3, 0, -1.2, 0);
    cyl('metal', '#e4e6e8', 0.65, 0.22, x - 0.1, y + 24, z - 0.75, Math.PI / 2, 0.4, 0, 16);
    box('paint', '#e3e5e7', 2.6, 2.3, 2.1, x + 3.2, y + 1.15, z + 2.2);
    box('matte', '#6f767d', 2.75, 0.12, 2.25, x + 3.2, y + 2.36, z + 2.2);
    mastTop.set(x, y + H + 0.35, z);
    for (let k = 0; k < 3; k += 1) {
      const ax = x + Math.cos(legA[k] + 0.3) * 19, az = z + Math.sin(legA[k] + 0.3) * 19, ay = heightAt(ax, az) + 0.2;
      for (const hh of [15, 29]) { const p = legAt(k, hh); guyLines.push([p[0], p[1], p[2], ax, ay, az]); }
      box('matte', '#8d8f91', 0.8, 0.5, 0.8, ax, ay, az);
    }
    shadowAt(x + 3.2, z + 2.2, 3.4, 2.3, 2.3);
    blockedSpots.push([x, z, 22]);
  }
  // power lines: wooden poles, crossarms, insulators, three sagging conductors
  const conductors = [];
  const poleLine = (pts, spacing, endTransformer) => {
    const path = P(pts, { step: 3 });
    const n = Math.max(2, Math.round(path.len / spacing));
    let prevTops = null;
    for (let i = 0; i <= n; i += 1) {
      let s = (i / n) * path.len;
      for (let tries = 0; tries < 6; tries += 1) { // nudge poles off roads
        pathAt(path, s, PT);
        let onRoad = false;
        for (const R of ROADS) { const nn = nearest(R.path, PT.x, PT.z, R.hw + 5); if (nn) onRoad = true; }
        if (!onRoad) break;
        s = clamp(s + (i === n ? -6 : 6), 0, path.len);
      }
      pathAt(path, s, PT);
      const x = PT.x, z = PT.z, y = heightAt(x, z), ry = Math.atan2(-PT.tx, -PT.tz);
      cyl('matte', '#6e5b47', 0.13, 11.2, x, y + 5.5, z, 0, 0, 0, 7, 0.17);
      box('matte', '#5c4b3a', 2.7, 0.13, 0.13, x, y + 10.35, z, 0, ry, 0);
      const ax = -PT.tz, az = PT.tx;
      const tops = [];
      for (const o of [-1.15, 0, 1.15]) {
        const ox = x + ax * o, oz = z + az * o;
        cyl('paint', '#d6dade', 0.06, 0.28, ox, y + (o === 0 ? 10.95 : 10.56), oz, 0, 0, 0, 6);
        tops.push([ox, y + (o === 0 ? 11.08 : 10.69), oz]);
      }
      if (endTransformer && i === n) {
        cyl('metal', '#a7aeb5', 0.42, 1.1, x + ax * 0.2 - PT.tx * 0.5, y + 7.4, z + az * 0.2 - PT.tz * 0.5, 0, 0, 0, 12);
        box('metal', '#8e959c', 0.9, 0.12, 0.5, x, y + 6.8, z, 0, ry, 0);
      }
      if (prevTops) for (let k = 0; k < 3; k += 1) conductors.push([prevTops[k], tops[k]]);
      prevTops = tops;
      shadowAt(x, z, 0.9, 11, 2.0);
      blockedSpots.push([x, z, 3]);
    }
    corridors.push([path, 9]);
  };
  poleLine([[880, 70], [520, 22], [240, -6], [110, -20], [44, -19], [27, -16]], 44, true);
  poleLine([[-26, -18], [-58, -58], [-120, -84], [-180, -114], [-246, -150]], 44, true);
  // parked ute + containers + HDPE pipe stack (west of the yard)
  const lvKit = () => {
    const k = [];
    const B = (hex, w, h, d, x, y, z) => k.push([new THREE.BoxGeometry(w, h, d), hex, x, y, z, 0]);
    B('#eef0f2', 1.86, 0.78, 5.1, 0, 0.92, 0);
    B('#eef0f2', 1.8, 0.82, 2.0, 0, 1.72, 0.35);
    B('#3b4652', 1.83, 0.5, 1.78, 0, 1.8, 0.42);
    B('#9aa1a8', 1.76, 0.42, 2.0, 0, 1.5, -1.55);
    B('#26292d', 1.92, 0.26, 0.26, 0, 0.78, 2.56);
    B('#26292d', 1.25, 0.1, 0.22, 0, 2.18, 0.65);
    for (const sx of [-0.86, 0.86]) for (const sz of [-1.65, 1.62]) k.push([new THREE.CylinderGeometry(0.41, 0.41, 0.3, 14), '#202326', sx, 0.41, sz, 1]);
    k.push([new THREE.CylinderGeometry(0.014, 0.014, 3.6, 4), '#d9d9d9', 0.8, 3.3, -2.4, 0]);
    B('#f97316', 0.02, 0.32, 0.46, 0.8, 4.92, -2.16);
    return k;
  };
  const kitGeo = (kit, key = null) => {
    const parts = kit.map(([g, hex, x, y, z, rotZ]) => {
      let gg = g.index ? g.toNonIndexed() : g;
      if (gg !== g) g.dispose();
      if (gg.attributes.uv) gg.deleteAttribute('uv');
      tmpO.position.set(x, y, z); tmpO.rotation.set(0, 0, rotZ ? Math.PI / 2 : 0); tmpO.scale.set(1, 1, 1); tmpO.updateMatrix();
      gg.applyMatrix4(tmpO.matrix);
      tmpC.set(hex);
      const n = gg.attributes.position.count, c = new Float32Array(n * 3);
      for (let i = 0; i < n; i += 1) { c[i * 3] = tmpC.r; c[i * 3 + 1] = tmpC.g; c[i * 3 + 2] = tmpC.b; }
      gg.setAttribute('color', new THREE.BufferAttribute(c, 3));
      gg.clearGroups();
      return gg;
    });
    const m = own(mergeGeometries(parts, false));
    for (const p of parts) p.dispose();
    return m;
  };
  const vehMat = own(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.15 }));
  const lvGeo = kitGeo(lvKit());
  {
    const [x, z] = [cx - 29.5, cz + 7], y = heightAt(x, z);
    const ute = new THREE.Mesh(lvGeo, vehMat);
    ute.position.set(x, y, z);
    ute.rotation.y = Math.PI + 0.25;
    ute.name = 'world3d-parked-ute';
    group.add(ute);
    shadows.push([x, y, z, 5.6, 2.5, Math.PI / 2 - 0.25]);
    blockedSpots.push([x, z, 5]);
    const [kx, kz] = [cx - 37, cz + 15], ky = heightAt(kx, kz);
    box('paint', '#99503d', 6.06, 2.59, 2.44, kx, ky + 1.28, kz, 0, 0.1, 0);
    box('paint', '#3f5d7f', 6.06, 2.59, 2.44, kx + 0.6, ky + 3.87, kz + 0.2, 0, 0.16, 0);
    shadowAt(kx, kz, 6.6, 5, 4);
    blockedSpots.push([kx, kz, 7]);
    const [px, pz] = [cx - 41, cz - 6], py = heightAt(px, pz);
    let row = 0;
    for (const [nn, yy] of [[4, 0.4], [3, 1.09], [2, 1.78]]) {
      for (let i = 0; i < nn; i += 1) cyl('matte', '#222428', 0.4, 12, px + (i - (nn - 1) / 2) * 0.8, py + yy, pz, 0, 0.05, Math.PI / 2, 12);
      row += 1;
    }
    shadowAt(px, pz, 12.5, 3.5, 1);
    blockedSpots.push([px, pz, 8]);
  }
  mark('props-small');
  // overland conveyor from the plant stockpile to the horizon: trestles + covered belt
  {
    const path = P([[320, -325], [520, -470], [800, -660], [1100, -860], [1420, -1070]], { step: 6 });
    const STEP = 21, n = Math.floor(path.len / STEP);
    const tops = [];
    for (let i = 0; i <= n; i += 1) {
      pathAt(path, i * STEP, PT);
      const gy = heightAt(PT.x, PT.z), s = i * STEP;
      tops.push([PT.x, Math.max(gy + 5.2, YG + 27 - s * 0.13), PT.z, PT.tx, PT.tz, gy]);
    }
    for (let i = 0; i <= n; i += 1) { // smooth the belt line over the terrain
      const a = tops[Math.max(0, i - 1)], b = tops[Math.min(n, i + 1)];
      tops[i][1] = Math.max(tops[i][5] + 4.4, (a[1] + tops[i][1] * 2 + b[1]) / 4);
    }
    for (let i = 0; i <= n; i += 1) {
      const [x, y, z, tx, tz, gy] = tops[i];
      const nx = -tz, nz = tx;
      for (const sd of [-1, 1]) rod('metal', '#9aa3ab', x + nx * sd * 1.55, gy, z + nz * sd * 1.55, x + nx * sd * 1.25, y - 0.6, z + nz * sd * 1.25, 0.09, 5);
      rod('metal', '#9aa3ab', x + nx * 1.5, gy + 0.3, z + nz * 1.5, x - nx * 1.3, y - 0.8, z - nz * 1.3, 0.05, 4);
      rod('metal', '#9aa3ab', x - nx * 1.4, y - 0.6, z - nz * 1.4, x + nx * 1.4, y - 0.6, z + nz * 1.4, 0.07, 4);
      if (i < n) {
        const [x2, y2, z2] = tops[i + 1];
        const len = Math.hypot(x2 - x, y2 - y, z2 - z), ry = Math.atan2(x2 - x, z2 - z), rx = -Math.atan2(y2 - y, Math.hypot(x2 - x, z2 - z));
        box('metal', '#b9c0c7', 2.6, 0.9, len + 0.05, (x + x2) / 2, (y + y2) / 2 - 0.1, (z + z2) / 2, rx, ry, 0);
        add('metal', new THREE.CylinderGeometry(1.2, 1.2, len + 0.05, 8, 1, true, Math.PI / 2, Math.PI), '#d5d9dd', (x + x2) / 2, (y + y2) / 2 + 0.35, (z + z2) / 2, rx + Math.PI / 2, ry, 0);
      }
    }
    corridors.push([path, 12]);
  }
  mark('conveyor');
  await tick(); if (dead) return;
  // processing plant: crusher, stockpile + stacker gallery, mill, thickeners, silos, stack (steam plume in update)
  const stackTop = new THREE.Vector3();
  {
    const at = (rx, rz) => { const x = PLANT.x + rx, z = PLANT.z + rz; return [x, heightAt(x, z), z]; };
    let [x, y, z] = at(-32, 46);
    box('paint', '#dcdfe2', 20, 18, 22, x, y + 9, z, 0, 0.6, 0);
    box('matte', '#5d636a', 9, 5, 0.4, x + 6.5, y + 3, z + 7.2, 0, 0.6, 0);
    box('paint', '#f2c230', 8, 3, 6, x + 4, y + 19, z + 3, 0, 0.6, 0);
    shadowAt(x, z, 24, 18, 18);
    [x, y, z] = at(22, -24);
    add('matte', new THREE.ConeGeometry(36, 24, 22, 1), '#4c5157', x, y + 12, z);
    const [gx, gy, gz] = at(-26, 40);
    rod('metal', '#c3c9cf', gx, gy + 17, gz, x, y + 25, z, 1.3, 6);
    for (const k of [0.3, 0.55, 0.8]) { const px = lerp(gx, x, k), pz = lerp(gz, z, k), py = lerp(gy + 17, y + 25, k); rod('metal', '#9aa3ab', px, heightAt(px, pz), pz, px, py - 1, pz, 0.35, 6); }
    [x, y, z] = at(45, 66);
    box('paint', '#e1e4e7', 62, 26, 30, x, y + 13, z, 0, 0.35, 0);
    box('metal', '#b7bec5', 63, 1.2, 31, x, y + 26.6, z, 0, 0.35, 0);
    box('glass', '#4a5866', 50, 1.6, 30.4, x, y + 20, z, 0, 0.35, 0);
    box('paint', '#d3d7db', 26, 13, 20, x - 30, y + 6.5, z - 18, 0, 0.35, 0);
    shadowAt(x, z, 60, 26, 26);
    for (const [rx, rz] of [[100, 22], [118, 72]]) {
      [x, y, z] = at(rx, rz);
      cyl('paint', '#cfd3d7', 17, 4.6, x, y + 2.3, z, 0, 0, 0, 28);
      cyl('matte', '#62767a', 16.3, 0.2, x, y + 4.62, z, 0, 0, 0, 28);
      box('metal', '#a2a9b0', 34, 0.8, 1.2, x, y + 5.4, z, 0, 0.5, 0);
      shadowAt(x, z, 36, 4.6, 4);
    }
    for (let i = 0; i < 3; i += 1) {
      [x, y, z] = at(-14 + i * 11, 104 + i * 3);
      cyl('paint', '#d9dcdf', 4.6, 17, x, y + 8.5, z, 0, 0, 0, 16);
      add('paint', new THREE.ConeGeometry(4.7, 2.4, 16), '#c4c9ce', x, y + 18.2, z);
      shadowAt(x, z, 9.5, 18, 18);
    }
    [x, y, z] = at(70, 104);
    cyl('paint', '#dadada', 1.5, 58, x, y + 29, z, 0, 0, 0, 14, 2.1);
    cyl('paint', '#c2493d', 1.52, 4, x, y + 54, z, 0, 0, 0, 14);
    cyl('paint', '#c2493d', 1.6, 3, x, y + 46.5, z, 0, 0, 0, 14);
    stackTop.set(x, y + 58.5, z);
    shadowAt(x, z, 5, 58, 20);
  }
  // pit equipment: shovel on the floor, a truck on a bench (tiny at 450 m, but it reads as a working mine)
  {
    const [ex, ez] = ellXZ(PIT, 0.24, 2.6), ey = PIT.floorY;
    box('paint', '#f2c230', 6, 3.4, 8, ex, ey + 3.4, ez, 0, 0.8, 0);
    box('matte', '#2c2f33', 6.5, 1.6, 8.5, ex, ey + 0.8, ez, 0, 0.8, 0);
    rod('paint', '#f2c230', ex, ey + 5, ez, ex + Math.sin(0.8) * 11, ey + 9, ez + Math.cos(0.8) * 11, 0.6, 6);
    const [tx, tz] = ellXZ(PIT, 0.3, 2.25);
    box('paint', '#f2c230', 6.4, 3.5, 10, tx, ey + 2.4, tz, 0, -0.4, 0);
    const [bx, bz] = ellXZ(PIT, 0.62, 4.1);
    box('paint', '#f2c230', 6.2, 3.5, 10, bx, YG + 0.12 - 4 * PIT.bh + 2.4, bz, 0, 1.2, 0);
  }
  mark('plant+pit');
  const propMats = {
    paint: own(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6, metalness: 0.08 })),
    metal: own(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.45, metalness: 0.5 })),
    matte: own(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.92, metalness: 0 })),
    glass: own(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.18, metalness: 0.6 })),
  };
  for (const key of Object.keys(bins)) {
    if (!bins[key].length) continue;
    const g = own(mergeGeometries(bins[key], false));
    for (const p of bins[key]) p.dispose();
    const m = new THREE.Mesh(g, propMats[key]);
    m.name = `world3d-props-${key}`;
    group.add(m);
  }
  // wires: conductors (catenaries) + mast guys
  {
    const pts = [];
    for (const [a, b] of conductors) {
      const d = Math.hypot(b[0] - a[0], b[2] - a[2]), sag = 0.6 + d * 0.022;
      let px = a[0], py = a[1], pz = a[2];
      for (let k = 1; k <= 10; k += 1) {
        const t = k / 10, x = lerp(a[0], b[0], t), z = lerp(a[2], b[2], t), y = lerp(a[1], b[1], t) - sag * 4 * t * (1 - t);
        pts.push(px, py, pz, x, y, z);
        px = x; py = y; pz = z;
      }
    }
    for (const g of guyLines) pts.push(...g);
    const geo = own(new THREE.BufferGeometry());
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const wireMat = own(new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uColor: { value: colr('#3a3f44') }, uOpacity: { value: 0.8 } }]),
      vertexShader: `varying float vDist;
#include <fog_pars_vertex>
void main() {
  vec4 mvPosition = modelViewMatrix * vec4( position, 1.0 );
  gl_Position = projectionMatrix * mvPosition;
  vDist = -mvPosition.z;
  #include <fog_vertex>
}`,
      fragmentShader: `uniform vec3 uColor;
uniform float uOpacity;
varying float vDist;
#include <fog_pars_fragment>
void main() {
  float a = uOpacity * ( 1.0 - smoothstep( 60.0, 300.0, vDist ) );
  if ( a < 0.01 ) discard;
  gl_FragColor = vec4( uColor, a );
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #include <fog_fragment>
}`,
      transparent: true, depthWrite: false, fog: true,
    }));
    const wires = new THREE.LineSegments(geo, wireMat);
    wires.name = 'world3d-wires';
    group.add(wires);
  }
  mark('props');
  await tick(); if (dead) return;

  // ---------- vegetation, rocks (instanced; trees in three distance bands so far stands cost almost nothing) ----------
  const blob = (detail, seed, jitter) => {
    let g = new THREE.IcosahedronGeometry(1, detail);
    g.deleteAttribute('normal'); g.deleteAttribute('uv');
    const m = mergeVertices(g); g.dispose(); g = m;
    const p = g.attributes.position, R = rng(seed);
    for (let i = 0; i < p.count; i += 1) { const k = 1 + (R() - 0.5) * jitter; p.setXYZ(i, p.getX(i) * k, p.getY(i) * k, p.getZ(i) * k); }
    g.computeVertexNormals();
    return g;
  };
  const octa = (seed, jitter) => {
    let g = new THREE.OctahedronGeometry(1, 0);
    g.deleteAttribute('normal'); g.deleteAttribute('uv');
    const m = mergeVertices(g); g.dispose(); g = m;
    const p = g.attributes.position, R = rng(seed);
    for (let i = 0; i < p.count; i += 1) { const k = 1 + (R() - 0.5) * jitter; p.setXYZ(i, p.getX(i) * k, p.getY(i) * k, p.getZ(i) * k); }
    g.computeVertexNormals();
    return g;
  };
  const aoColours = (g, y0, y1, lo = 0.62) => {
    const p = g.attributes.position, c = new Float32Array(p.count * 3);
    for (let i = 0; i < p.count; i += 1) { const k = lo + (1 - lo) * sstep(y0, y1, p.getY(i)); c[i * 3] = k; c[i * 3 + 1] = k; c[i * 3 + 2] = k; }
    g.setAttribute('color', new THREE.BufferAttribute(c, 3));
    return g;
  };
  // soft foliage shading: bend each normal away from the crown centre so clumps read as one leafy volume
  const crownNormals = (g, ox, oy, oz, k = 0.65) => {
    const p = g.attributes.position, n = g.attributes.normal, a = new THREE.Vector3(), b = new THREE.Vector3();
    for (let i = 0; i < p.count; i += 1) {
      a.set(p.getX(i) - ox, (p.getY(i) - oy) * 1.4, p.getZ(i) - oz).normalize();
      b.set(n.getX(i), n.getY(i), n.getZ(i)).lerp(a, k).normalize();
      n.setXYZ(i, b.x, b.y, b.z);
    }
    return g;
  };
  const clumps = (list, seed, jitter, detail = 0) => {
    const parts = list.map(([x, y, z, r, ry], i) => blob(detail, seed + i, jitter).scale(r, ry, r).translate(x, y, z));
    const g = mergeGeometries(parts, false);
    for (const p of parts) p.dispose();
    return g;
  };
  const blocked = (x, z, yardClear, roadClear, corridorClear = 0) => {
    if (yardDist(x, z) < yardClear) return true;
    if (qShape(PIT, x, z) < 1.4 || qShape(DUMP, x, z) < 1.12 || Math.hypot(x - PLANT.x, z - PLANT.z) < 175) return true;
    for (const [bx, bz, br] of blockedSpots) if ((x - bx) * (x - bx) + (z - bz) * (z - bz) < br * br) return true;
    for (const R of ROADS) if (nearest(R.path, x, z, R.hw + roadClear)) return true;
    if (corridorClear) for (const [path, w] of corridors) if (nearest(path, x, z, w * corridorClear)) return true;
    return false;
  };
  const inst = (geo, mat, list, name) => {
    const m = new THREE.InstancedMesh(geo, mat, Math.max(1, list.length));
    m.count = list.length;
    for (let i = 0; i < list.length; i += 1) { m.setMatrixAt(i, list[i].m); if (list[i].c) m.setColorAt(i, list[i].c); }
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
    m.name = name;
    m.computeBoundingSphere();
    group.add(m);
    return m;
  };
  const mtx = (x, y, z, ry, sx, sy, sz, rx = 0, rz = 0) => {
    tmpO.position.set(x, y, z); tmpO.rotation.set(rx, ry, rz, 'YXZ'); tmpO.scale.set(sx, sy, sz); tmpO.updateMatrix();
    return tmpO.matrix.clone();
  };
  const pick = (R, arr) => colr(arr[Math.floor(R() * arr.length)]);
  {
    // trunks in tree units (height 1): near trees get three limbs into the crown
    const trunkNear = (() => {
      const parts = [new THREE.CylinderGeometry(0.0075, 0.014, 0.7, 6, 1, true).translate(0, 0.35, 0)];
      for (const [x, y, z] of [[0.13, 0.74, 0.05], [-0.12, 0.76, -0.07], [0.03, 0.72, 0.14]]) {
        const d = new THREE.Vector3(x, y - 0.46, z), len = d.length();
        const g = new THREE.CylinderGeometry(0.0035, 0.006, len, 4, 1, true);
        g.applyMatrix4(new THREE.Matrix4().compose(new THREE.Vector3(x / 2, 0.46 + (y - 0.46) / 2, z / 2), new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize()), new THREE.Vector3(1, 1, 1)));
        parts.push(g);
      }
      for (const p of parts) p.deleteAttribute('uv');
      const g = mergeGeometries(parts, false);
      for (const p of parts) p.dispose();
      return g;
    })();
    const trunkSimple = new THREE.CylinderGeometry(0.008, 0.014, 0.62, 5, 1, true).translate(0, 0.31, 0);
    // crowns: canopy space is height 1 (scaled by 0.62 H), width 1 (scaled by W)
    const eucNear = crownNormals(aoColours(clumps([[0, 0.7, 0, 0.22, 0.16], [0.28, 0.62, 0.08, 0.2, 0.15], [-0.26, 0.64, -0.1, 0.21, 0.15], [0.06, 0.48, 0.28, 0.19, 0.14], [-0.12, 0.88, 0.05, 0.18, 0.13], [0.18, 0.84, -0.2, 0.17, 0.13], [-0.32, 0.44, 0.2, 0.17, 0.13], [0.36, 0.42, -0.18, 0.16, 0.12], [-0.06, 0.5, -0.32, 0.18, 0.13], [0.12, 0.3, 0.06, 0.15, 0.11], [-0.2, 0.76, 0.26, 0.16, 0.12]], 40, 0.45), 0.2, 1.0, 0.68), 0, 0.6, 0);
    const eucMid = crownNormals(aoColours(clumps([[0, 0.62, 0, 0.42, 0.3], [0.3, 0.48, 0.12, 0.3, 0.22], [-0.28, 0.52, -0.15, 0.3, 0.22]], 60, 0.38), 0.25, 1.0, 0.68), 0, 0.58, 0);
    const far = (() => {
      const g = blob(0, 7, 0.3).scale(0.5, 0.46, 0.5).translate(0, 0.5, 0);
      const p = g.attributes.position;
      for (let i = 0; i < p.count; i += 1) if (p.getY(i) < 0.12) p.setY(i, 0.12);
      g.computeVertexNormals();
      return crownNormals(aoColours(g, 0.1, 1.0, 0.6), 0, 0.5, 0, 0.5);
    })();
    const pine = (() => {
      const parts = [[0.44, 0.55, 0.3], [0.33, 0.46, 0.56], [0.21, 0.36, 0.8]].map(([r, h, y], i) => {
        const g = new THREE.ConeGeometry(r, h, 7, 1, true);
        g.deleteAttribute('uv');
        return g.rotateY(i * 0.45).translate(0, y, 0);
      });
      const g = mergeGeometries(parts, false);
      for (const p of parts) p.dispose();
      return aoColours(g, 0.0, 1.0, 0.55);
    })();
    const bushNear = (() => {
      const a = blob(0, 77, 0.4), b = octa(78, 0.35).scale(0.7, 0.7, 0.7).translate(0.55, -0.1, 0.25);
      const g = mergeGeometries([a, b], false); a.dispose(); b.dispose();
      return crownNormals(aoColours(g, -0.8, 0.9, 0.55), 0.2, -0.2, 0.1, 0.5);
    })();
    const bushFar = aoColours(octa(79, 0.3), -0.8, 0.9, 0.6);
    const rockGeo = blob(0, 91, 0.6);
    const tuft = (() => {
      const pos = [], col = [], nrm = [], R = rng(5);
      for (let i = 0; i < 5; i += 1) {
        const a = (i / 5) * Math.PI * 2 + R() * 0.8, lean = 0.1 + R() * 0.2, h = 0.45 + R() * 0.4, w = 0.04 + R() * 0.02;
        const bx = Math.cos(a) * 0.04, bz = Math.sin(a) * 0.04, px = -Math.sin(a) * w, pz = Math.cos(a) * w, tx = Math.cos(a) * lean, tz = Math.sin(a) * lean;
        const n = new THREE.Vector3(Math.cos(a) * 0.3, 1, Math.sin(a) * 0.3).normalize();
        // both windings so each blade is front-facing from either side with the same up-ish normal
        pos.push(bx - px, 0, bz - pz, bx + px, 0, bz + pz, tx, h, tz, bx + px, 0, bz + pz, bx - px, 0, bz - pz, tx, h, tz);
        for (let k = 0; k < 2; k += 1) { col.push(0.62, 0.62, 0.62, 0.62, 0.62, 0.62, 1, 1, 1); for (let j = 0; j < 3; j += 1) nrm.push(n.x, n.y, n.z); }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute('normal', new THREE.Float32BufferAttribute(nrm, 3));
      g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
      return g;
    })();
    for (const g of [trunkNear, trunkSimple, eucNear, eucMid, far, pine, bushNear, bushFar, rockGeo, tuft]) own(g);
    const foliage = own(new THREE.MeshLambertMaterial({ vertexColors: true }));
    const barkMat = own(new THREE.MeshLambertMaterial({ color: 0xffffff }));
    const rockMat = own(new THREE.MeshLambertMaterial({ color: 0xffffff, flatShading: true }));

    const R = rng(1234);
    const L = { trunkN: [], trunkS: [], eucN: [], eucM: [], far: [], pine: [], bushN: [], bushF: [], tuft: [], rock: [] };
    const EUC_C = ['#9aa483', '#8f9b7a', '#a6ac8c', '#95a07f', '#adb092', '#88947a'];
    const PINE_C = ['#62735a', '#5a6c52', '#6a7a5e', '#56664f'];
    const BARK_C = ['#d9d3c5', '#c9c1b2', '#bdb5a6', '#e2ddd2'];
    const addTree = (x, z, Hh, isPine) => {
      const y = heightAt(x, z), r = Math.hypot(x - cx, z - cz), ry = R() * 6.28;
      if (r > 620) {
        const W = Hh * (isPine ? 0.34 : 0.55);
        L.far.push({ m: mtx(x, y - 0.24 * Hh, z, ry, W, Hh * 1.05, W), c: pick(R, isPine ? PINE_C : EUC_C) });
        return;
      }
      if (isPine) {
        const W = Hh * (0.3 + R() * 0.08);
        L.trunkS.push({ m: mtx(x, y - 0.3, z, ry, Hh, Hh * 0.75, Hh), c: pick(R, ['#6d5a48', '#5f4f40', '#77624e']) });
        L.pine.push({ m: mtx(x, y + Hh * 0.2, z, ry, W, Hh * 0.82, W), c: pick(R, PINE_C) });
      } else {
        const W = Hh * (0.42 + R() * 0.2), lean = (R() - 0.5) * 0.08, near = r < 210;
        (near ? L.trunkN : L.trunkS).push({ m: mtx(x, y - 0.3, z, ry, Hh, Hh, Hh, lean, (R() - 0.5) * 0.08), c: pick(R, BARK_C) });
        (near ? L.eucN : L.eucM).push({ m: mtx(x + lean * Hh * 0.5, y + Hh * 0.4, z, R() * 6.28, W, Hh * 0.62, W * (0.8 + R() * 0.3)), c: pick(R, EUC_C) });
      }
      if (r < 330) shadowAt(x, z, Hh * 0.45, Hh, Hh * 0.75);
    };
    // eucalyptus: clumps everywhere, a denser belt just outside the cleared ring round the site
    for (let tries = 0, n = 0; n < 420 && tries < 40000; tries += 1) {
      const a = R() * Math.PI * 2, rr = TREE_R + Math.pow(R(), 1.5) * 1250;
      const x = cx + Math.cos(a) * rr, z = cz + Math.sin(a) * rr;
      const dens = sstep(0.45, 0.7, fbm(x * 0.007 + 31, z * 0.007 - 17, 3));
      const belt = (1 - sstep(TREE_R + 10, TREE_R + 90, rr)) * 0.6 * sstep(0.3, 0.55, vnoise(x * 0.04, z * 0.04));
      if (rr > 620 || R() > Math.max(dens, belt)) continue; // far trees come as stands below
      if (Math.hypot(x - camT.x, z - camT.z) < TREE_R || blocked(x, z, 22, 6, 1)) continue;
      addTree(x, z, 10 + R() * 12, false); n += 1;
    }
    mark('veg-euc');
    await tick(); if (dead) return;
    // far away: whole stands of trees (clusters read as woodland on the hills; lone blobs read as balloons)
    for (let tries = 0, stands = 0; stands < 46 && tries < 4000; tries += 1) {
      const a = R() * Math.PI * 2, rr = 640 + R() * 760, x0 = cx + Math.cos(a) * rr, z0 = cz + Math.sin(a) * rr;
      if (fbm(x0 * 0.007 + 31, z0 * 0.007 - 17, 3) < 0.5 || blocked(x0, z0, 22, 10, 1)) continue;
      const n = 5 + Math.floor(R() * 8), spread = 14 + R() * 22;
      for (let k = 0; k < n; k += 1) {
        const x = x0 + (R() - 0.5) * 2 * spread, z = z0 + (R() - 0.5) * 2 * spread;
        if (!blocked(x, z, 22, 6, 1)) addTree(x, z, 11 + R() * 10, R() < 0.12);
      }
      stands += 1;
    }
    // a pine plantation block on the west slopes + a few strays
    {
      const bx = cx - 560, bz = cz + 70, ang = 0.35, ca = Math.cos(ang), sa = Math.sin(ang);
      for (let i = -9; i <= 9; i += 1) {
        for (let j = -5; j <= 5; j += 1) {
          if (R() < 0.12) continue;
          const lx = i * 12 + (R() - 0.5) * 3, lz = j * 12 + (R() - 0.5) * 3;
          const x = bx + lx * ca - lz * sa, z = bz + lx * sa + lz * ca;
          if (blocked(x, z, 22, 6, 1)) continue;
          addTree(x, z, 13 + R() * 6, true);
        }
      }
      for (let k = 0, tries = 0; k < 40 && tries < 3000; tries += 1) {
        const a = R() * Math.PI * 2, rr = TREE_R + 30 + R() * 700, x = cx + Math.cos(a) * rr, z = cz + Math.sin(a) * rr;
        if (Math.hypot(x - camT.x, z - camT.z) < TREE_R || blocked(x, z, 22, 6, 1)) continue;
        addTree(x, z, 11 + R() * 7, true); k += 1;
      }
    }
    mark('veg-pine');
    await tick(); if (dead) return;
    // scrub bushes
    const BUSH_C = ['#7f8460', '#8a8b66', '#939a74', '#9a8f6c', '#77805d', '#8c9671', '#a0957a'];
    for (let tries = 0, n = 0; n < 1100 && tries < 30000; tries += 1) {
      const a = R() * Math.PI * 2, rr = siteRadius * 0.6 + Math.pow(R(), 1.6) * 600;
      const x = cx + Math.cos(a) * rr, z = cz + Math.sin(a) * rr;
      if (R() > 0.35 + 0.65 * sstep(0.4, 0.62, fbm(x * 0.02 - 5, z * 0.02 + 3, 2))) continue;
      if (blocked(x, z, 4, 1.5)) continue;
      const s = (rr < 60 ? 0.35 : 0.45) + R() * R() * (rr < 60 ? 0.75 : 1.2), y = heightAt(x, z);
      (rr < 140 ? L.bushN : L.bushF).push({ m: mtx(x, y - 0.12 * s, z, R() * 6.28, s * (0.9 + R() * 0.5), s * (0.55 + R() * 0.3), s * (0.9 + R() * 0.5)), c: pick(R, BUSH_C) });
      if (s > 1.0 && rr < 200) shadowAt(x, z, s * 2.0, s, s * 0.5);
      n += 1;
    }
    mark('veg-bush');
    await tick(); if (dead) return;
    // dry grass tufts: dense just outside the fence and along shoulders, scattered further out
    const TUFT_C = ['#dccb98', '#d2bf8a', '#c8b781', '#bdb282', '#b4ad80', '#e0d1a6'];
    for (let tries = 0; L.tuft.length < 3000 && tries < 40000; tries += 1) {
      let x, z;
      if (R() < 0.72) {
        const w = Y.x1 - Y.x0, d = Y.z1 - Y.z0, side = R() * (2 * w + 2 * d), out = 0.8 + Math.pow(R(), 1.9) * 34;
        if (side < w) { x = Y.x0 + side; z = Y.z0 - out; } else if (side < 2 * w) { x = Y.x0 + side - w; z = Y.z1 + out; }
        else if (side < 2 * w + d) { x = Y.x0 - out; z = Y.z0 + side - 2 * w; } else { x = Y.x1 + out; z = Y.z0 + side - 2 * w - d; }
        x += (R() - 0.5) * 4; z += (R() - 0.5) * 4;
      } else {
        const a = R() * Math.PI * 2, rr = siteRadius + Math.pow(R(), 1.3) * 220;
        x = cx + Math.cos(a) * rr; z = cz + Math.sin(a) * rr;
      }
      if (blocked(x, z, 0.8, 0.6)) continue;
      const s = 0.42 + R() * 0.55;
      L.tuft.push({ m: mtx(x, heightAt(x, z) - 0.03, z, R() * 6.28, s * (0.8 + R() * 0.6), s, s * (0.8 + R() * 0.6)), c: pick(R, TUFT_C) });
    }
    mark('veg-tuft');
    await tick(); if (dead) return;
    // grey-green tussocks and small stones in the strip the default camera sees beyond the fence
    const TUS_C = ['#a9a77c', '#9da078', '#b5ad83', '#94996f', '#bdb38a'];
    for (let tries = 0, n = 0; n < 520 && tries < 8000; tries += 1) {
      const w = Y.x1 - Y.x0, d = Y.z1 - Y.z0, side = R() * (2 * w + 2 * d), out = 1.2 + Math.pow(R(), 1.4) * 30;
      let x, z;
      if (side < w) { x = Y.x0 + side; z = Y.z0 - out; } else if (side < 2 * w) { x = Y.x0 + side - w; z = Y.z1 + out; }
      else if (side < 2 * w + d) { x = Y.x0 - out; z = Y.z0 + side - 2 * w; } else { x = Y.x1 + out; z = Y.z0 + side - 2 * w - d; }
      if (blocked(x, z, 1.2, 0.8)) continue;
      const s = 0.22 + R() * 0.3, y = heightAt(x, z);
      if (R() < 0.7) L.bushN.push({ m: mtx(x, y - 0.05, z, R() * 6.28, s * (1 + R() * 0.4), s * (0.5 + R() * 0.25), s * (1 + R() * 0.4)), c: pick(R, TUS_C) });
      else L.rock.push({ m: mtx(x, y - 0.04, z, R() * 6.28, s * 0.6, s * 0.35, s * 0.5, (R() - 0.5) * 0.4, (R() - 0.5) * 0.4), c: pick(R, ['#a59a8c', '#958b7e', '#b3a899', '#8b8279']) });
      n += 1;
    }
    // rocks and boulders
    const ROCK_C = ['#a59a8c', '#958b7e', '#b3a899', '#8b8279', '#a8927e'];
    for (let tries = 0; L.rock.length < 260 && tries < 8000; tries += 1) {
      const a = R() * Math.PI * 2, rr = siteRadius + Math.pow(R(), 1.4) * 380;
      const x = cx + Math.cos(a) * rr, z = cz + Math.sin(a) * rr;
      if (blocked(x, z, 2, 0.5)) continue;
      const s = 0.25 + R() * R() * 1.5, y = heightAt(x, z);
      L.rock.push({ m: mtx(x, y - s * 0.25, z, R() * 6.28, s * (1 + R() * 0.6), s * (0.5 + R() * 0.4), s * (0.9 + R() * 0.5), (R() - 0.5) * 0.3, (R() - 0.5) * 0.3), c: pick(R, ROCK_C) });
    }
    mark('veg-rocks');
    inst(trunkNear, barkMat, L.trunkN, 'world3d-trunks-near');
    inst(trunkSimple, barkMat, L.trunkS, 'world3d-trunks');
    inst(eucNear, foliage, L.eucN, 'world3d-eucalyptus-near');
    inst(eucMid, foliage, L.eucM, 'world3d-eucalyptus');
    inst(pine, foliage, L.pine, 'world3d-pines');
    inst(far, foliage, L.far, 'world3d-trees-far');
    inst(bushNear, foliage, L.bushN, 'world3d-scrub-near');
    inst(bushFar, foliage, L.bushF, 'world3d-scrub');
    inst(tuft, foliage, L.tuft, 'world3d-grass');
    inst(rockGeo, rockMat, L.rock, 'world3d-rocks');
    mark('vegetation');
  }
  // soft contact shadows (trees, props) and the shared blob material used under vehicles
  const blobTex = own(canvasTexture(THREE, 64, 64, (g, w, h) => {
    const gr = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
    gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.55, 'rgba(255,255,255,0.55)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
  }, false));
  const blobMat = own(new THREE.MeshBasicMaterial({ color: 0x2c2a22, map: blobTex, transparent: true, opacity: 0.3, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 }));
  const blobGeo = own(new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2));
  inst(blobGeo, blobMat, shadows.map(([x, y, z, l, w, a]) => ({ m: mtx(x, y + 0.06, z, a, l, 1, w) })), 'world3d-contact-shadows');

  // ---------- vehicles ----------
  const beaconMats = [];
  const beacon = (hex) => { const m = own(new THREE.MeshBasicMaterial({ color: hex, toneMapped: false })); beaconMats.push(m); return m; };
  const beaconGeo = own(new THREE.CylinderGeometry(0.11, 0.11, 0.16, 10));
  const truckGeo = kitGeo((() => {
    const k = [], Yc = '#f2c230', D = '#2c2f33', T = '#1d1f22', G = '#3d4752';
    const B = (hex, w, h, d, x, y, z) => k.push([new THREE.BoxGeometry(w, h, d), hex, x, y, z, 0]);
    B(D, 2.4, 1.0, 10.6, 0, 1.75, 0);
    B(Yc, 6.4, 0.3, 2.2, 0, 3.15, 4.3);
    B(Yc, 4.2, 1.6, 2.4, 0.4, 2.35, 4.6);
    B(D, 3.0, 1.2, 0.12, 0.4, 2.3, 5.85);
    B(Yc, 2.0, 1.7, 2.0, -2.1, 4.15, 4.25);
    B(G, 2.04, 0.75, 1.7, -2.1, 4.45, 4.3);
    B(Yc, 6.6, 0.3, 8.0, 0, 2.9, -1.3);
    B(Yc, 0.3, 2.2, 8.0, -3.15, 4.0, -1.3);
    B(Yc, 0.3, 2.2, 8.0, 3.15, 4.0, -1.3);
    B(Yc, 6.6, 2.9, 0.3, 0, 4.35, 2.7);
    B(Yc, 6.6, 0.25, 2.6, 0, 5.75, 3.9);
    B(Yc, 6.6, 0.6, 0.3, 0, 3.3, -5.3);
    for (let i = 0; i < 4; i += 1) B(D, 0.6, 0.06, 0.3, -2.95, 1.0 + i * 0.45, 5.6);
    for (const sx of [-1, 1]) {
      k.push([new THREE.CylinderGeometry(1.45, 1.45, 1.0, 16), T, sx * 2.7, 1.45, 3.4, 1]);
      k.push([new THREE.CylinderGeometry(0.7, 0.7, 1.04, 10), D, sx * 2.7, 1.45, 3.4, 1]);
      for (const o of [2.05, 3.05]) k.push([new THREE.CylinderGeometry(1.45, 1.45, 0.95, 16), T, sx * o, 1.45, -2.9, 1]);
    }
    return k;
  })());
  const oreGeo = own(blob(1, 61, 0.35).scale(2.9, 1.05, 3.6));
  const oreMat = own(new THREE.MeshStandardMaterial({ color: 0x4f545a, roughness: 0.95, metalness: 0.02, flatShading: true }));
  const vehShadow = (w, l) => { const m = new THREE.Mesh(blobGeo, blobMat); m.scale.set(w, 1, l); m.position.y = 0.08; m.renderOrder = 1; return m; };
  const lanePath = (road, off, reverse) => {
    const pts = [];
    for (let i = 0; i < road.n; i += 3) pts.push([road.x[i] - road.tz[i] * off - cx, road.z[i] + road.tx[i] * off - cz]);
    const L = road.n - 1;
    pts.push([road.x[L] - road.tz[L] * off - cx, road.z[L] + road.tx[L] * off - cz]);
    if (reverse) pts.reverse();
    const p = P(pts, { step: 2 });
    for (let i = 0; i < p.n; i += 1) { const nn = nearest(road, p.x[i], p.z[i], 30); p.y[i] = nn ? lerp(road.y[nn.i], road.y[nn.i + 1], nn.t) + 0.12 : heightAt(p.x[i], p.z[i]); }
    return p;
  };
  const outLane = lanePath(haul, -3.6, false), inLane = lanePath(haul, 3.6, true);
  const V_LOADED = 8.5, V_EMPTY = 11.5, T_OUT = outLane.len / V_LOADED + 3, T_IN = inLane.len / V_EMPTY + 3, T_DUMP = 9, T_LOAD = 12;
  const T_CYCLE = T_OUT + T_DUMP + T_IN + T_LOAD;
  const trucks = [0, 0.52].map((phase, i) => {
    const g = new THREE.Group();
    g.name = `world3d-haul-truck-${i + 1}`;
    const body = new THREE.Mesh(truckGeo, vehMat);
    const ore = new THREE.Mesh(oreGeo, oreMat);
    ore.position.set(0, 3.55, -1.4);
    const bm = new THREE.Mesh(beaconGeo, beacon(0xffa31a));
    bm.position.set(-2.1, 5.12, 4.4);
    g.add(body, ore, bm, vehShadow(7.8, 12.5));
    group.add(g);
    return { g, ore, bm, phase, last: new THREE.Vector3(), speed: 0, emitAcc: 0 };
  });
  const lv = (() => {
    const g = new THREE.Group();
    g.name = 'world3d-light-vehicle';
    const body = new THREE.Mesh(lvGeo, vehMat);
    const bm = new THREE.Mesh(beaconGeo, beacon(0xffa31a));
    bm.position.set(0, 2.32, 0.65);
    g.add(body, bm, vehShadow(2.6, 5.6));
    group.add(g);
    return { g, bm, last: new THREE.Vector3(), speed: 0, emitAcc: 0 };
  })();
  for (let i = 0; i < lvTrack.n; i += 1) lvTrack.y[i] += 0.1;
  // the LV stops 9 m before it crosses the haul road
  let lvStopS = 0;
  for (let i = 0; i < lvTrack.n; i += 1) { const nn = nearest(haul, lvTrack.x[i], lvTrack.z[i], 9); if (nn && nn.d < 9) { lvStopS = (i / (lvTrack.n - 1)) * lvTrack.len - 9 - 7.5; break; } }
  const LV_V = 8.0, LV_STOP = 7, LV_DRIVE = lvTrack.len / LV_V + 4, LV_CYCLE = LV_DRIVE + LV_STOP;

  // ---------- particles: road dust, pit haze, stack steam ----------
  const particles = (N, hex) => {
    const g = own(new THREE.BufferGeometry());
    const P3 = new Float32Array(N * 3), S = new Float32Array(N), Al = new Float32Array(N);
    g.setAttribute('position', new THREE.BufferAttribute(P3, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aSize', new THREE.BufferAttribute(S, 1).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aAlpha', new THREE.BufferAttribute(Al, 1).setUsage(THREE.DynamicDrawUsage));
    const m = own(new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uColor: { value: colr(hex) }, uScale: { value: 800 } }]),
      vertexShader: `attribute float aSize;
attribute float aAlpha;
uniform float uScale;
varying float vAlpha;
#include <fog_pars_vertex>
void main() {
  vec4 mvPosition = modelViewMatrix * vec4( position, 1.0 );
  gl_Position = projectionMatrix * mvPosition;
  gl_PointSize = min( 700.0, aSize * uScale / max( 1.0, -mvPosition.z ) );
  vAlpha = aAlpha;
  #include <fog_vertex>
}`,
      fragmentShader: `uniform vec3 uColor;
varying float vAlpha;
#include <fog_pars_fragment>
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = dot( c, c ) * 4.0;
  float a = vAlpha * ( 1.0 - d ) * ( 1.0 - d );
  if ( d > 1.0 || a < 0.003 ) discard;
  gl_FragColor = vec4( uColor, a );
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #include <fog_fragment>
}`,
      transparent: true, depthWrite: false, fog: true,
    }));
    const pts = new THREE.Points(g, m);
    pts.frustumCulled = false;
    pts.renderOrder = 2;
    group.add(pts);
    return { g, m, P3, S, Al, N, vel: new Float32Array(N * 3), age: new Float32Array(N).fill(1e9), life: new Float32Array(N).fill(1), s0: new Float32Array(N), s1: new Float32Array(N), a0: new Float32Array(N), next: 0 };
  };
  const dust = particles(220, '#d2bf9f');
  dust.grow = 0.5;
  const steam = particles(90, '#f7f7f5');
  steam.m.toneMapped = false; // keep the plume white against the pale sky
  steam.grow = 1;
  const emit = (ps, x, y, z, vx, vy, vz, life, s0, s1, a0) => {
    const i = ps.next; ps.next = (ps.next + 1) % ps.N;
    ps.P3[i * 3] = x; ps.P3[i * 3 + 1] = y; ps.P3[i * 3 + 2] = z;
    ps.vel[i * 3] = vx; ps.vel[i * 3 + 1] = vy; ps.vel[i * 3 + 2] = vz;
    ps.age[i] = 0; ps.life[i] = life; ps.s0[i] = s0; ps.s1[i] = s1; ps.a0[i] = a0;
  };
  const WIND = [2.6, 0, -1.1];
  const stepParticles = (ps, dt, drag) => {
    for (let i = 0; i < ps.N; i += 1) {
      if (ps.age[i] > ps.life[i]) { ps.Al[i] = 0; continue; }
      ps.age[i] += dt;
      const k = ps.age[i] / ps.life[i];
      const j = i * 3;
      ps.vel[j] += (WIND[0] - ps.vel[j]) * drag * dt;
      ps.vel[j + 2] += (WIND[2] - ps.vel[j + 2]) * drag * dt;
      ps.vel[j + 1] *= 1 - 0.25 * dt;
      ps.P3[j] += ps.vel[j] * dt; ps.P3[j + 1] += ps.vel[j + 1] * dt; ps.P3[j + 2] += ps.vel[j + 2] * dt;
      ps.S[i] = lerp(ps.s0[i], ps.s1[i], Math.pow(k, ps.grow || 0.5));
      ps.Al[i] = k >= 1 ? 0 : ps.a0[i] * Math.min(1, ps.age[i] * 2.5) * Math.pow(1 - k, 1.4);
    }
    ps.g.attributes.position.needsUpdate = true;
    ps.g.attributes.aSize.needsUpdate = true;
    ps.g.attributes.aAlpha.needsUpdate = true;
  };

  // ---------- clouds (low-poly, drifting) and a bird flock ----------
  const cloudMeshes = [];
  const clouds = [];
  {
    const sunH = new THREE.Vector3(sunDir.x, 0.4, sunDir.z).normalize();
    const shapes = [3, 8].map((seed) => {
      const R = rng(seed), parts = [];
      for (let i = 0; i < 7; i += 1) {
        const g = blob(1, seed * 10 + i, 0.18);
        g.scale(0.45 + R() * 0.45, 0.3 + R() * 0.3, 0.4 + R() * 0.35).translate((R() - 0.5) * 1.9, R() * 0.22, (R() - 0.5) * 1.0);
        parts.push(g);
      }
      const g = own(mergeGeometries(parts, false));
      for (const p of parts) p.dispose();
      const p = g.attributes.position;
      for (let i = 0; i < p.count; i += 1) if (p.getY(i) < 0.05) p.setY(i, 0.05 + (p.getY(i) - 0.05) * 0.22);
      g.computeVertexNormals();
      const n = g.attributes.normal, c = new Float32Array(p.count * 3);
      const top = colr('#ffffff'), bot = colr('#c3cbd4'), side = new THREE.Color();
      for (let i = 0; i < p.count; i += 1) {
        const ny = n.getY(i), lit = Math.max(0, n.getX(i) * sunH.x + n.getY(i) * sunH.y + n.getZ(i) * sunH.z);
        side.copy(bot).lerp(top, sstep(-0.45, 0.75, ny) * 0.75 + lit * 0.25).lerp(HAZE, 0.28);
        c[i * 3] = side.r; c[i * 3 + 1] = side.g; c[i * 3 + 2] = side.b;
      }
      g.setAttribute('color', new THREE.BufferAttribute(c, 3));
      return g;
    });
    const cloudMat = own(new THREE.MeshBasicMaterial({ vertexColors: true, fog: false, toneMapped: false }));
    const R = rng(99);
    shapes.forEach((geo, si) => {
      const n = 6;
      const m = new THREE.InstancedMesh(geo, cloudMat, n);
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      m.frustumCulled = false;
      m.name = `world3d-clouds-${si}`;
      group.add(m);
      cloudMeshes.push(m);
      for (let i = 0; i < n; i += 1) {
        const a = R() * Math.PI * 2, rr = 900 + R() * 1500;
        clouds.push({ m, i, x: cx + Math.cos(a) * rr, z: cz + Math.sin(a) * rr, y: 250 + R() * 170, ry: R() * 6.28, sx: 170 + R() * 160, sy: 80 + R() * 60, sz: 120 + R() * 110 });
      }
    });
  }
  const birdGeo = own(new THREE.BufferGeometry());
  birdGeo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0.28, 0, 0, -0.22, -0.78, 0.2, -0.06, 0, 0, 0.28, 0.78, 0.2, -0.06, 0, 0, -0.22], 3));
  birdGeo.computeVertexNormals();
  const birdMesh = new THREE.InstancedMesh(birdGeo, own(new THREE.MeshBasicMaterial({ color: 0x2f3033, side: THREE.DoubleSide })), 15);
  birdMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  birdMesh.frustumCulled = false;
  birdMesh.name = 'world3d-birds';
  group.add(birdMesh);
  const birds = [];
  { const R = rng(4242); for (let i = 0; i < 15; i += 1) birds.push({ ox: (R() - 0.5) * 22, oy: (R() - 0.5) * 5, oz: (R() - 0.5) * 18, ph: R() * 6.28, sp: 6.5 + R() * 2, s: 1.0 + R() * 0.5 }); }

  for (const o of group.children) { if (o.geometry) owned.add(o.geometry); if (o.material) owned.add(o.material); }

  // ---------- frame update ----------
  let time = 0, beaconT = 0;
  const fwd = new THREE.Vector3();
  const sizeV = new THREE.Vector2();
  const placeVehicle = (v, p, ry, pitch) => {
    v.g.position.set(p.x, p.y, p.z);
    v.g.rotation.set(pitch, ry, 0, 'YXZ');
  };
  const emitDust = (v, dt, rearZ, halfW, rate) => {
    const sp = v.speed;
    if (sp < 2.5) return;
    v.emitAcc += dt * rate * Math.min(1, sp / 9);
    const ry = v.g.rotation.y, s = Math.sin(ry), c = Math.cos(ry);
    while (v.emitAcc >= 1) {
      v.emitAcc -= 1;
      const side = Math.random() < 0.5 ? -1 : 1;
      const lx = side * halfW * (0.7 + Math.random() * 0.3), lz = rearZ;
      const x = v.g.position.x + lx * c + lz * s, z = v.g.position.z - lx * s + lz * c;
      emit(dust, x, v.g.position.y + 0.7, z, -s * sp * 0.15 + (Math.random() - 0.5), 0.5 + Math.random() * 0.6, -c * sp * 0.15 + (Math.random() - 0.5), 4.5 + Math.random() * 2.5, 2.2 * halfW, 6 + halfW * 2.2, 0.16 + Math.random() * 0.06);
    }
  };
  let pitHazeAcc = 0, steamAcc = 0;
  function update(dt = 0.016) {
    dt = Math.min(Math.max(dt, 0), 0.1);
    time += dt;
    U.time.value = time;
    // haul trucks: out loaded -> dump at the crusher -> back empty -> load at the pit (deterministic from time)
    for (const tk of trucks) {
      const tau = (time + tk.phase * T_CYCLE) % T_CYCLE;
      let ry, pitch = 0, loaded = true;
      if (tau < T_OUT) {
        pathAt(outLane, trap(tau, T_OUT, outLane.len, 7), PT);
        ry = Math.atan2(PT.tx, PT.tz); pitch = -Math.atan(PT.slope);
      } else if (tau < T_OUT + T_DUMP) {
        const k = (tau - T_OUT) / T_DUMP, a = pathAt(outLane, outLane.len, { ...PT }), b = pathAt(inLane, 0, { ...PT });
        const kk = sstep(0.35, 1, k);
        PT.x = lerp(a.x, b.x, kk); PT.z = lerp(a.z, b.z, kk); PT.y = lerp(a.y, b.y, kk);
        ry = Math.atan2(a.tx, a.tz) + Math.PI * kk; loaded = k < 0.3;
      } else if (tau < T_OUT + T_DUMP + T_IN) {
        pathAt(inLane, trap(tau - T_OUT - T_DUMP, T_IN, inLane.len, 7), PT);
        ry = Math.atan2(PT.tx, PT.tz); pitch = -Math.atan(PT.slope); loaded = false;
      } else {
        const k = (tau - T_OUT - T_DUMP - T_IN) / T_LOAD, a = pathAt(inLane, inLane.len, { ...PT }), b = pathAt(outLane, 0, { ...PT });
        const kk = sstep(0, 0.6, k);
        PT.x = lerp(a.x, b.x, kk); PT.z = lerp(a.z, b.z, kk); PT.y = lerp(a.y, b.y, kk);
        ry = Math.atan2(a.tx, a.tz) + Math.PI * kk; loaded = k > 0.75;
      }
      placeVehicle(tk, PT, ry, pitch);
      tk.speed = dt > 0 ? tk.last.distanceTo(tk.g.position) / dt : 0;
      tk.last.copy(tk.g.position);
      tk.ore.visible = loaded;
      emitDust(tk, dt, -5.2, 3.0, 16);
    }
    // light vehicle: laps of the perimeter track with a stop before the haul-road crossing
    {
      const tau = time % LV_CYCLE;
      const s = tau < LV_STOP ? lvStopS : lvStopS + trap(tau - LV_STOP, LV_DRIVE, lvTrack.len, 4);
      pathAt(lvTrack, s, PT);
      placeVehicle(lv, PT, Math.atan2(PT.tx, PT.tz), -Math.atan(PT.slope));
      lv.speed = dt > 0 ? lv.last.distanceTo(lv.g.position) / dt : 0;
      lv.last.copy(lv.g.position);
      emitDust(lv, dt, -2.6, 0.9, 7);
    }
    // beacons: double-flash amber on vehicles, slow red on the mast
    beaconT += dt;
    const fl = (beaconT * 1.3) % 1, on = fl < 0.08 || (fl > 0.16 && fl < 0.24);
    for (const tk of trucks) tk.bm.material.color.setHex(on ? 0xffb020 : 0x5a3a10);
    lv.bm.material.color.setHex(((beaconT * 1.3 + 0.5) % 1) < 0.1 ? 0xffb020 : 0x5a3a10);
    mastBeacon.material.color.setHex((beaconT % 1.6) < 0.7 ? 0xff2a1a : 0x401010);
    // dust over the pit floor, steam off the stack
    pitHazeAcc += dt;
    if (pitHazeAcc > 1.6) {
      pitHazeAcc = 0;
      const [x, z] = ellXZ(PIT, Math.random() * 0.5, Math.random() * Math.PI * 2);
      emit(dust, x, PIT.floorY + 6 + Math.random() * 10, z, WIND[0] * 0.5, 0.9, WIND[2] * 0.5, 16, 28, 75, 0.1);
    }
    steamAcc += dt * 5.5;
    while (steamAcc >= 1) {
      steamAcc -= 1;
      emit(steam, stackTop.x + (Math.random() - 0.5), stackTop.y, stackTop.z + (Math.random() - 0.5), (Math.random() - 0.5) * 0.6, 2.6 + Math.random(), (Math.random() - 0.5) * 0.6, 12 + Math.random() * 4, 2.5, 30, 0.34);
    }
    renderer.getDrawingBufferSize(sizeV);
    const scale = sizeV.y / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
    dust.m.uniforms.uScale.value = scale;
    steam.m.uniforms.uScale.value = scale;
    stepParticles(dust, dt, 0.6);
    stepParticles(steam, dt, 0.55);
    // clouds drift with the wind and wrap round
    for (const c of clouds) {
      c.x += WIND[0] * 2.2 * dt; c.z += WIND[2] * 2.2 * dt;
      const dx = c.x - cx, dz = c.z - cz;
      if (dx * dx + dz * dz > 2500 * 2500) { c.x = cx - dx * 0.98; c.z = cz - dz * 0.98; }
      tmpO.position.set(c.x, c.y, c.z); tmpO.rotation.set(0, c.ry, 0); tmpO.scale.set(c.sx, c.sy, c.sz); tmpO.updateMatrix();
      c.m.setMatrixAt(c.i, tmpO.matrix);
    }
    for (const m of cloudMeshes) m.instanceMatrix.needsUpdate = true;
    // birds: a loose flock wheeling over the far side of the site
    {
      const t = time;
      const fx = cx - 30 + 140 * Math.sin(t * 0.042), fz = cz - 90 + 100 * Math.sin(t * 0.031 + 1.3), fy = 34 + 6 * Math.sin(t * 0.11);
      const vx = 140 * 0.042 * Math.cos(t * 0.042), vz = 100 * 0.031 * Math.cos(t * 0.031 + 1.3);
      const hd = Math.atan2(vx, vz), ch = Math.cos(hd), shh = Math.sin(hd);
      for (let i = 0; i < birds.length; i += 1) {
        const b = birds[i];
        const wob = Math.sin(t * 0.7 + b.ph) * 1.5;
        const x = fx + b.ox * ch + b.oz * shh + wob, z = fz - b.ox * shh + b.oz * ch, y = fy + b.oy + Math.sin(t * 0.9 + b.ph) * 0.8;
        const glide = sstep(0.55, 0.8, Math.sin(t * 0.37 + b.ph * 2));
        const flap = lerp(Math.sin(t * b.sp + b.ph), 0.35, glide);
        tmpO.position.set(x, y, z); tmpO.rotation.set(0, hd + Math.sin(t * 0.5 + b.ph) * 0.15, 0); tmpO.scale.set(b.s, b.s * flap, b.s); tmpO.updateMatrix();
        birdMesh.setMatrixAt(i, tmpO.matrix);
      }
      birdMesh.instanceMatrix.needsUpdate = true;
    }
  }
  const mastBeacon = new THREE.Mesh(own(new THREE.SphereGeometry(0.22, 10, 8)), beacon(0xff2a1a));
  mastBeacon.position.copy(mastTop);
  group.add(mastBeacon);
  update(0);
  mark('life');

  // the group's children were registered for disposal before the chunks finished; register the late ones too
  for (const o of group.children) { if (o.geometry) owned.add(o.geometry); if (o.material) owned.add(o.material); }
  Object.assign(api.info, {
    buildMs: Math.round(performance.now() - T0),
    marks,
    hiddenLegacy: hidden.map(([o]) => o.name || o.geometry.type),
    drawObjects: group.children.length,
    maxChunkMs: Math.round(Math.max(maxChunk, performance.now() - chunkStart)),
  });
  api.update = (dt) => update(dt);
}
