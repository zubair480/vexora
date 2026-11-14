// BeltGuard 3D scene: conveyor CV-102 in the mongomation look (three.js r160, see ../DESIGN.md "3D scene").
// Daylit, procedural, Factory-IO style: ACES tone mapping, RoomEnvironment reflections, drafting-paper floor,
// aluminium frame, safety-yellow guards, graphite ore. Parts rise in on load, then a gentle camera orbit.
// Interface used by app.js (keep it stable):
//   createModel(container, { onStationClick }) -> { apply(state), setActivity(state), setVibration(rms), dispose() }
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

// ---------- geometry constants (metres) ----------
const LEN = 12;                          // tail pulley centre to head pulley centre
const X0 = -LEN / 2, X1 = LEN / 2;
const H_CARRY = 1.25;                    // top of the centre carry roll
const H_RETURN = 0.55;                   // return strand
const PR = (H_CARRY - H_RETURN) / 2;     // pulley radius
const PY = H_RETURN + PR;                // pulley axis height
const TC = 0.23;                         // trough: half-width of the flat centre roll
const TW = 0.42;                         // trough: wing length
const TA = (30 * Math.PI) / 180;         // trough angle
const BWF = 2 * (TC + TW);               // flat belt width
const TRANS = 1.1;                       // trough transition length at each pulley
const RR = 0.06;                         // idler roll radius
const ZS = 0.8;                          // stringer offset from the centre line
const Z_WALK = 2.05;                     // walkway centre line (station rings)
const Z_WORK = 1.32;                     // where the technician stands to work on the belt
const BELT_V = 1.1;                      // belt speed, visual only
const LX = -4.75;                        // loading point under the hopper
const TX = X0 + 0.74 * LEN;              // take-up tower
const TZ = -1.35;
const PX = X1 + 2.1;                     // ore stockpile centre (under the discharge)
const PILE_R = 2.4, PILE_H = 1.6;
const WALK_X0 = -7.2, WALK_X1 = 5.9;     // walkway ends
const SOLDIER_URL = 'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/models/gltf/Soldier.glb';
// tool ids from backend/tool_profiles.json -> the five recognisable models in the tool crib
const TOOL_KIND = {
  drill: 'drill', hammer_drill: 'hammer', impact_wrench: 'impact', impact_wrench_1in: 'impact', demo_tool: 'impact',
  torque_wrench_hydraulic: 'impact', power_jack: 'jack', angle_grinder: 'grinder',
  breaker_hammer: 'breaker', chipping_hammer: 'breaker', needle_scaler: 'breaker', needle_scaler_vr: 'breaker',
};
const CRIB_ORDER = ['drill', 'hammer', 'impact', 'grinder', 'jack', 'breaker'];
// the six tools hanging on the crib wall (ids from tool_profiles.json)
const CRIB_IDS = { drill: 'drill', hammer: 'hammer_drill', impact: 'impact_wrench', grinder: 'angle_grinder', jack: 'power_jack', breaker: 'breaker_hammer' };
function toolKindOf(tool) {
  const id = String((tool && typeof tool === 'object' ? tool.id || tool.tool_id : tool) || '').toLowerCase();
  if (!id) return null;
  if (TOOL_KIND[id]) return TOOL_KIND[id];
  if (/grind/.test(id)) return 'grinder';
  if (/hammer.?drill/.test(id)) return 'hammer';
  if (/break|chip|scal/.test(id)) return 'breaker';
  if (/drill/.test(id)) return 'drill';
  if (/jack|lift/.test(id)) return 'jack';
  return 'impact';
}
const POS_DEFAULT = { S1: 0.08, S2: 0.3, S3: 0.52, S4: 0.74, S5: 0.94 };
const SHORT = { S1: 'Tail pulley', S2: 'Carry idlers', S3: 'Return idlers', S4: 'Take-up', S5: 'Head drive' };

// ---------- palette (mongomation materials + shadcn zinc status colours) ----------
const C = {
  bg: 0xf6f7f9, alu: 0xdfe3e8, aluDark: 0xb4bac2, roller: 0xeef0f3, yellow: 0xf2c230, ink: 0x1b1d21,
  floor: 0xf4f5f7, rubber: 0x2a2d31, graphite: 0x4a4f55,
  zinc300: 0xd4d4d8, zinc400: 0xa1a1aa, zinc500: 0x71717a, zinc900: 0x18181b,
  red: 0xef4444, green: 0x10b981, amber: 0xf59e0b, tool: 0xf97316,
};
const ORE_TONES = [0x3a3f45, 0x41464c, 0x484d53, 0x4f545a, 0x555a60, 0x5b6066, 0x62676d];

const Y_AXIS = new THREE.Vector3(0, 1, 0);
const Z_AXIS = new THREE.Vector3(0, 0, 1);
const X_AXIS = new THREE.Vector3(1, 0, 0);
const ONE = new THREE.Vector3(1, 1, 1);
const sx = (pos) => X0 + pos * LEN;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, k) => a + (b - a) * k;
const ease3 = (k) => 1 - Math.pow(1 - k, 3);
const smooth = (k) => k * k * (3 - 2 * k);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function lerpAngle(a, b, k) {
  let d = ((b - a + Math.PI) % (Math.PI * 2)) - Math.PI;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * k;
}
const troughF = (x) => smooth(clamp(Math.min(x - X0, X1 - x) / TRANS, 0, 1));
const troughY = (z, f) => { const a = Math.abs(z); return a <= TC ? 0 : (a - TC) * Math.tan(TA * f); };
const std = (color, roughness, metalness = 0, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra });

function canvasTex(w, h, draw, { repeat = true, aniso = 8 } = {}) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = aniso;
  return t;
}

// rubber belt with chevron cleats (u runs along the belt, one tile = 1.2 m)
function beltTexture(cleats) {
  return canvasTex(256, 128, (g, w, h) => {
    g.fillStyle = '#2a2d31'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 1600; i += 1) { g.fillStyle = `rgba(255,255,255,${Math.random() * 0.045})`; g.fillRect(Math.random() * w, Math.random() * h, 1, 1); }
    if (cleats) {
      g.lineCap = 'round'; g.lineJoin = 'round';
      for (const x0 of [36, 164]) {
        g.strokeStyle = '#141619'; g.lineWidth = 9;
        g.beginPath(); g.moveTo(x0, 16); g.lineTo(x0 + 30, h / 2); g.lineTo(x0, h - 16); g.stroke();
        g.strokeStyle = 'rgba(255,255,255,0.2)'; g.lineWidth = 2.5;
        g.beginPath(); g.moveTo(x0 + 4, 16); g.lineTo(x0 + 34, h / 2); g.lineTo(x0 + 4, h - 16); g.stroke();
      }
    } else {
      g.fillStyle = 'rgba(255,255,255,0.05)'; g.fillRect(0, 0, 3, h);
    }
    g.fillStyle = 'rgba(255,255,255,0.07)'; g.fillRect(0, 0, w, 3); g.fillRect(0, h - 3, w, 3);
  });
}
function laggingTexture() {
  const t = canvasTex(128, 32, (g, w, h) => {
    g.fillStyle = '#3a3f45'; g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(255,255,255,0.14)'; g.lineWidth = 2;
    for (let x = -h; x < w + h; x += 16) {
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x + h, h); g.stroke();
      g.beginPath(); g.moveTo(x + h, 0); g.lineTo(x, h); g.stroke();
    }
  });
  t.repeat.set(4, 1);
  return t;
}
function hubTexture() {
  return canvasTex(128, 128, (g) => {
    g.fillStyle = '#d3d8de'; g.fillRect(0, 0, 128, 128);
    g.strokeStyle = '#a7aeb6'; g.lineWidth = 5; g.beginPath(); g.arc(64, 64, 54, 0, Math.PI * 2); g.stroke();
    g.fillStyle = '#1b1d21'; g.beginPath(); g.arc(64, 64, 15, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#3a3f45';
    for (let i = 0; i < 6; i += 1) { const a = (i * Math.PI) / 3; g.beginPath(); g.arc(64 + Math.cos(a) * 34, 64 + Math.sin(a) * 34, 5, 0, Math.PI * 2); g.fill(); }
    g.fillStyle = '#f2c230'; g.fillRect(60, 14, 8, 22); // index mark so rotation reads
  }, { repeat: false });
}
// welded wire mesh, 100 mm pitch: mips fade it into a soft yellow veil at distance
function meshTexture() {
  return canvasTex(32, 32, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#f2c230'; g.fillRect(0, 0, w, 3); g.fillRect(0, 0, 3, h);
  });
}
// yellow / ink 45 degree hatch, one tile = 0.6 m
function hatchTexture() {
  return canvasTex(64, 64, (g, w, h) => {
    g.fillStyle = '#f4f5f7'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#f2c230';
    for (let k = -w; k < w * 2; k += 32) { g.beginPath(); g.moveTo(k, 0); g.lineTo(k + 14, 0); g.lineTo(k + 14 - h, h); g.lineTo(k - h, h); g.closePath(); g.fill(); }
  });
}

// troughed carry strand: flat at the pulleys, full trough after TRANS metres
function carryBeltGeometry() {
  const NX = 120, pos = [], uv = [], idx = [];
  for (let i = 0; i <= NX; i += 1) {
    const x = X0 + (i / NX) * LEN;
    const a = TA * troughF(x);
    const wz = TW * Math.cos(a), wy = TW * Math.sin(a);
    const prof = [[-TC - wz, wy], [-TC, 0], [TC, 0], [TC + wz, wy]];
    const vs = [0, TW / BWF, (TW + 2 * TC) / BWF, 1];
    const u = (x - X0) / 1.2;
    prof.forEach(([z, y], j) => { pos.push(x, H_CARRY + 0.006 + y, z); uv.push(u, vs[j]); });
  }
  for (let i = 0; i < NX; i += 1) {
    for (let j = 0; j < 3; j += 1) {
      const a = i * 4 + j, b = a + 1, c = a + 4, d = a + 5;
      idx.push(a, b, c, b, d, c);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

// lumpy stockpile cone (faceted with flatShading)
function pileGeometry(r, h) {
  const g = new THREE.ConeGeometry(r, h, 44, 8, true);
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i += 1) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const k = (y + h / 2) / h;
    if (k > 0.995 || k < 0.005) continue;
    const a = Math.atan2(z, x);
    const n = 1 + 0.06 * Math.sin(a * 5 + 1.3) + 0.045 * Math.sin(a * 11 + k * 7) + 0.035 * Math.sin(k * 13 + a * 3);
    p.setXYZ(i, x * n, y + Math.sin(a * 7 + k * 9) * 0.025, z * n);
  }
  g.computeVertexNormals();
  g.translate(0, h / 2, 0);
  return g;
}

// shadcn badge styles for the CSS2D labels (scoped to this module)
function injectStyles() {
  // update in place if an older scene already injected it (hot upgrades, side-by-side tests)
  const s = document.getElementById('bg3d-style') || document.createElement('style');
  s.id = 'bg3d-style';
  const sans = 'Geist,"Geist Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif';
  const mono = '"Geist Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace';
  // only small numbered station badges live in 3D (fixed size, so placement needs no layout reads)
  s.textContent = `
.label-layer.bg3d-pre .bg3d-badge,.label-layer.bg3d-pre .bg3d-leader{opacity:0!important;pointer-events:none!important}
.bg3d-badge{position:absolute;left:0;top:0;display:grid;place-items:center;width:24px;height:24px;box-sizing:border-box;background:#FFFFFF;border:1px solid #E4E4E7;border-radius:6px;box-shadow:0 1px 2px 0 rgb(0 0 0 / .06);font:500 12px/1 ${mono};font-variant-numeric:tabular-nums;color:#09090B;pointer-events:auto;cursor:pointer;user-select:none;will-change:transform;transition:opacity .3s ease,background-color .15s ease,border-color .15s ease}
.bg3d-badge:hover{border-color:#A1A1AA}
.bg3d-badge.ok{background:#ECFDF5;border-color:#A7F3D0;color:#047857}
.bg3d-badge.sel{background:#18181B;border-color:#18181B;color:#FAFAFA}
.bg3d-badge.defect{background:#EF4444;border-color:#EF4444;color:#FFFFFF}
.bg3d-badge.defect.sel{box-shadow:0 0 0 2px #FFFFFF,0 0 0 3px #18181B}
.bg3d-leader{position:absolute;left:0;top:0;width:1px;background:#A1A1AA;transform-origin:0 0;pointer-events:none;will-change:transform;transition:opacity .3s ease}
.bg3d-leader.sel{background:#18181B}.bg3d-leader.defect{background:#EF4444}
.bg3d-badge,.bg3d-leader{font-family:${mono}}.bg3d-sans{font-family:${sans}}
.label-layer.bg3d-pre .bg3d-wbadge{opacity:0!important;pointer-events:none!important}
.bg3d-wbadge{position:absolute;left:0;top:0;display:inline-flex;align-items:center;gap:5px;height:22px;padding:0 7px;box-sizing:border-box;background:#FFFFFF;border:1px solid #E4E4E7;border-radius:6px;box-shadow:0 1px 2px 0 rgb(0 0 0 / .06);font:500 11px/1 ${sans};color:#09090B;white-space:nowrap;pointer-events:auto;cursor:pointer;user-select:none;will-change:transform;transition:opacity .3s ease,border-color .15s ease}
.bg3d-wbadge:hover{border-color:#A1A1AA}
.bg3d-wbadge i{width:6px;height:6px;border-radius:50%;background:#A1A1AA;flex:none}
.bg3d-wbadge.action i{background:#F59E0B}.bg3d-wbadge.limit i{background:#EF4444}
.bg3d-wbadge .pts{font-family:${mono};font-variant-numeric:tabular-nums;color:#71717A}
.bg3d-wbadge .sim{font:600 9px/1 ${mono};letter-spacing:.04em;color:#71717A;background:#F4F4F5;border-radius:3px;padding:2px 3px}
.bg3d-wbadge.focus{border-color:#18181B;box-shadow:0 1px 3px 0 rgb(0 0 0 / .12)}`;
  if (!s.parentNode) document.head.appendChild(s);
}

export function createModel(container, { onStationClick, onToolClick, onWorkerClick, soldierUrl } = {}) {
  injectStyles();
  let reduced = false;
  try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { /* old browsers */ }
  const W = () => container.clientWidth || 800;
  const H = () => container.clientHeight || 500;

  // ---------- renderer / scene (mongomation factory3d settings) ----------
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(W(), H(), false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.className = 'gl';
  renderer.domElement.dataset.scene = 'mongomation-v17b';
  Object.assign(renderer.domElement.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block' });
  container.appendChild(renderer.domElement);

  // own screen-space label layer (badges are clamped inside the stage and de-collided every frame)
  const labelLayer = document.createElement('div');
  labelLayer.className = 'label-layer bg3d-pre';
  Object.assign(labelLayer.style, { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '2', overflow: 'hidden' });
  container.appendChild(labelLayer);

  const scene = new THREE.Scene();
  // soft light sky: a gradient dome tone-mapped like the ground, so the fogged horizon meets it seamlessly
  const HORIZON = new THREE.Color().setRGB(1.42, 1.45, 1.5);
  const ZENITH = new THREE.Color().setRGB(0.78, 0.93, 1.22);
  scene.background = new THREE.Color(C.bg);
  scene.fog = new THREE.Fog(HORIZON, 40, 150);
  {
    const geo = new THREE.SphereGeometry(170, 32, 16);
    const pos = geo.attributes.position;
    const cols = new Float32Array(pos.count * 3);
    const c = new THREE.Color();
    for (let i = 0; i < pos.count; i += 1) {
      const k = Math.pow(clamp(pos.getY(i) / 170, 0, 1), 0.55);
      c.copy(HORIZON).lerp(ZENITH, k);
      cols.set([c.r, c.g, c.b], i * 3);
    }
    geo.setAttribute('color', new THREE.BufferAttribute(cols, 3));
    const sky = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide, fog: false, depthWrite: false }));
    sky.renderOrder = -10;
    sky.frustumCulled = false;
    scene.add(sky);
  }
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  pmrem.dispose();

  const camera = new THREE.PerspectiveCamera(32, W() / H(), 0.1, 300);

  scene.add(new THREE.HemisphereLight(0xffffff, 0xdfe3e8, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(0.8 - 13, 24, 11);
  sun.target.position.set(0.8, 0, 0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1536, 1536);
  Object.assign(sun.shadow.camera, { left: -22, right: 22, top: 22, bottom: -22, near: 1, far: 90 });
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  scene.add(sun, sun.target);

  // ---------- materials ----------
  const M = {
    alu: std(C.alu, 0.35, 0.45),
    aluDark: std(C.aluDark, 0.45, 0.4),
    aluPlate: std(C.alu, 0.38, 0.42, { side: THREE.DoubleSide, flatShading: true }),
    roller: std(C.roller, 0.25, 0.55),
    yellow: std(C.yellow, 0.55),
    ink: std(C.ink, 0.6),
    white: std(0xffffff, 0.5),
    grey: std(0xdde0e4, 0.55),
    weight: std(0x41464c, 0.7, 0.2),
    floor: std(C.floor, 0.95),
    rubber: std(C.rubber, 0.85, 0, { side: THREE.DoubleSide }),
    pile: std(C.graphite, 0.95, 0.02, { flatShading: true }),
    lump: std(0xffffff, 0.9, 0.05, { flatShading: true }),
    alarm: std(C.red, 0.45),
    wire: new THREE.MeshStandardMaterial({ map: meshTexture(), transparent: true, depthWrite: false, side: THREE.DoubleSide, roughness: 0.6 }),
    hatch: std(0xffffff, 0.8, 0, { map: hatchTexture(), polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }),
    paint: std(C.yellow, 0.7, 0, { polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }),
  };
  const carryTex = beltTexture(true);
  const retTex = beltTexture(false);
  retTex.repeat.set(LEN / 1.2, 1);
  M.belt = std(0xffffff, 0.75, 0, { map: carryTex, side: THREE.DoubleSide });
  M.beltRet = std(0xffffff, 0.8, 0, { map: retTex, side: THREE.DoubleSide });
  M.pulley = [std(0xffffff, 0.6, 0.15, { map: laggingTexture() }), std(0xffffff, 0.4, 0.4, { map: hubTexture() }), std(0xffffff, 0.4, 0.4, { map: hubTexture() })];

  // ---------- ground: light gravel (texture + low-frequency colour variation, fades into fog) + concrete work pad ----------
  {
    const geo = new THREE.PlaneGeometry(280, 280, 70, 70);
    const pos = geo.attributes.position;
    const cols = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i += 1) {
      const x = pos.getX(i), y = pos.getY(i);
      const n = 0.5 * Math.sin(x * 0.11 + Math.sin(y * 0.07) * 2.1) + 0.35 * Math.sin(y * 0.13 + x * 0.05) + 0.25 * Math.sin((x + y) * 0.31);
      const v = 0.92 + n * 0.08;
      const tint = 0.012 * Math.sin(x * 0.045 + 1.7) * Math.cos(y * 0.05);
      cols.set([v * (0.995 + tint), v, v * (1.012 - tint)], i * 3);
    }
    geo.setAttribute('color', new THREE.BufferAttribute(cols, 3));
    const gravelTex = canvasTex(256, 256, (g, w, h) => {
      g.fillStyle = '#b9bec4'; g.fillRect(0, 0, w, h);
      for (let i = 0; i < 4200; i += 1) {
        const x = Math.random() * w, y = Math.random() * h, r = 0.6 + Math.random() * 1.9;
        const l = (112 + Math.random() * 110) | 0, d = Math.random() < 0.5 ? -3 : 3;
        g.fillStyle = `rgba(${l + d},${l},${l - d},${(0.35 + Math.random() * 0.5).toFixed(2)})`;
        for (const ox of [-w, 0, w]) for (const oy of [-h, 0, h]) {
          if (ox && (x + ox < -4 || x + ox > w + 4)) continue;
          if (oy && (y + oy < -4 || y + oy > h + 4)) continue;
          g.beginPath(); g.arc(x + ox, y + oy, r, 0, Math.PI * 2); g.fill();
        }
      }
    });
    gravelTex.repeat.set(280 / 3.2, 280 / 3.2);
    const ground = new THREE.Mesh(geo, std(0xffffff, 0.97, 0, { map: gravelTex, vertexColors: true }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.03;
    ground.receiveShadow = true;
    scene.add(ground);
    // concrete pad: saw-cut slab joints every 3 m, faint mottling
    const PW = 16.6, PD = 7.4;
    const concTex = canvasTex(256, 256, (g, w, h) => {
      g.fillStyle = '#eceef1'; g.fillRect(0, 0, w, h);
      for (let i = 0; i < 90; i += 1) {
        const x = Math.random() * w, y = Math.random() * h, r = 10 + Math.random() * 40;
        const gr = g.createRadialGradient(x, y, 0, x, y, r);
        gr.addColorStop(0, Math.random() < 0.5 ? 'rgba(120,126,134,0.05)' : 'rgba(255,255,255,0.08)');
        gr.addColorStop(1, 'rgba(0,0,0,0)');
        g.fillStyle = gr; g.fillRect(x - r, y - r, r * 2, r * 2);
      }
      g.fillStyle = 'rgba(27,29,33,0.16)'; g.fillRect(0, 0, w, 2); g.fillRect(0, 0, 2, h);
    });
    concTex.repeat.set(PW / 3, PD / 3);
    const pad = new THREE.Mesh(new THREE.PlaneGeometry(PW, PD), std(0xffffff, 0.9, 0, { map: concTex }));
    pad.rotation.x = -Math.PI / 2;
    pad.position.set(-1.35, 0, 0.0);
    pad.receiveShadow = true;
    scene.add(pad);
    const edge = new THREE.Mesh(new THREE.BoxGeometry(PW + 0.06, 0.06, PD + 0.06), std(0xd9dce0, 0.9));
    edge.position.set(-1.35, -0.031, 0);
    edge.receiveShadow = true;
    scene.add(edge);
  }

  // ---------- builders ----------
  const parts = []; // reveal animation: groups rise out of the floor
  function part(name, delay) {
    const g = new THREE.Group();
    g.name = name;
    g.userData.reveal = { delay, y: 0 };
    scene.add(g);
    parts.push(g);
    return g;
  }
  const tmpO = new THREE.Object3D();
  function builder() {
    const bins = new Map();
    const push = (geo, mat) => { if (!bins.has(mat)) bins.set(mat, []); bins.get(mat).push(geo); return geo; };
    const put = (geo, mat, x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0) => {
      tmpO.position.set(x, y, z); tmpO.rotation.set(rx, ry, rz, 'YXZ'); tmpO.scale.set(1, 1, 1); tmpO.updateMatrix();
      geo.applyMatrix4(tmpO.matrix);
      return push(geo, mat);
    };
    return {
      box: (mat, w, h, d, x, y, z, rx, ry, rz) => put(new THREE.BoxGeometry(w, h, d), mat, x, y, z, rx, ry, rz),
      cyl: (mat, r, h, x, y, z, rx, ry, rz, seg = 16, r2 = r) => put(new THREE.CylinderGeometry(r, r2, h, seg), mat, x, y, z, rx, ry, rz),
      geo: (mat, geo, x, y, z, rx, ry, rz) => put(geo, mat, x, y, z, rx, ry, rz),
      // cylinder whose axis runs along unit vector `dir`, centred at `pos`
      axial: (mat, r, len, pos, dir, seg = 18) => {
        const g = new THREE.CylinderGeometry(r, r, len, seg);
        const q = new THREE.Quaternion().setFromUnitVectors(Y_AXIS, dir.clone().normalize());
        g.applyMatrix4(new THREE.Matrix4().compose(pos, q, ONE));
        return push(g, mat);
      },
      rod: (mat, r, a, b, seg = 8) => {
        const d = new THREE.Vector3().subVectors(b, a);
        const g = new THREE.CylinderGeometry(r, r, d.length(), seg);
        const q = new THREE.Quaternion().setFromUnitVectors(Y_AXIS, d.normalize());
        g.applyMatrix4(new THREE.Matrix4().compose(new THREE.Vector3().addVectors(a, b).multiplyScalar(0.5), q, ONE));
        return push(g, mat);
      },
      // flat mesh-guard panel (yellow frame + wire infill) in the XY plane, rotated about Y
      guard: (w, h, x, y, z, ry = 0) => {
        const pl = new THREE.PlaneGeometry(w, h);
        const uv = pl.attributes.uv;
        for (let i = 0; i < uv.count; i += 1) uv.setXY(i, uv.getX(i) * (w / 0.1), uv.getY(i) * (h / 0.1));
        put(pl, M.wire, x, y, z, 0, ry, 0);
        const fr = 0.035;
        const c = Math.cos(ry), s = Math.sin(ry);
        const at = (lx, ly) => [x + lx * c, y + ly, z - lx * s];
        put(new THREE.BoxGeometry(w + fr, fr, fr), M.yellow, ...at(0, h / 2), 0, ry, 0);
        put(new THREE.BoxGeometry(w + fr, fr, fr), M.yellow, ...at(0, -h / 2), 0, ry, 0);
        put(new THREE.BoxGeometry(fr, h, fr), M.yellow, ...at(-w / 2, 0), 0, ry, 0);
        put(new THREE.BoxGeometry(fr, h, fr), M.yellow, ...at(w / 2, 0), 0, ry, 0);
      },
      build(parent, { cast = true, receive = true } = {}) {
        for (const [mat, list] of bins) {
          const anyFlat = list.some((g) => !g.index);
          const geos = anyFlat ? list.map((g) => (g.index ? g.toNonIndexed() : g)) : list;
          const merged = mergeGeometries(geos, false);
          if (merged) {
            const mesh = new THREE.Mesh(merged, mat);
            mesh.castShadow = cast && mat !== M.wire && mat !== M.paint && mat !== M.hatch;
            mesh.receiveShadow = receive;
            parent.add(mesh);
          } else console.warn('[model3d] merge failed for a material bin');
          for (const g of list) g.dispose();
          if (anyFlat) for (const g of geos) g.dispose();
        }
        bins.clear();
        return parent;
      },
    };
  }

  // floor markings: walkway double lines + hatched keep-out under the discharge
  {
    const g = part('markings', 0.15);
    const b = builder();
    for (const z of [Z_WALK - 0.85, Z_WALK + 0.85]) b.box(M.paint, WALK_X1 - WALK_X0, 0.01, 0.08, (WALK_X0 + WALK_X1) / 2, 0.005, z);
    for (const x of [WALK_X0, WALK_X1]) b.box(M.paint, 0.08, 0.01, 1.78, x, 0.005, Z_WALK);
    b.box(M.paint, 0.08, 0.01, 4.3, -6.95, 0.005, -0.05);
    const hz = new THREE.PlaneGeometry(1.1, 2.4);
    const uv = hz.attributes.uv;
    for (let i = 0; i < uv.count; i += 1) uv.setXY(i, uv.getX(i) * (1.1 / 0.6), uv.getY(i) * (2.4 / 0.6));
    b.geo(M.hatch, hz, X0 - 1.25, 0.004, 0, -Math.PI / 2, 0, 0);
    b.build(g, { cast: false });
  }

  // ---------- conveyor structure ----------
  const rollerDefs = []; // kept for defect highlighting: { kind, x, pos, dir, len, r }
  const ca = Math.cos(TA), sa = Math.sin(TA);
  const carryXs = Array.from({ length: 13 }, (_, i) => -4.8 + i * 0.8);
  const returnXs = [-4.6, -2.2, sx(POS_DEFAULT.S3), 1.9, 4.4];
  const trainX = sx(POS_DEFAULT.S2);

  { // stringers, legs, braces
    const g = part('frame', 0.25);
    const b = builder();
    for (const s of [-1, 1]) {
      b.box(M.alu, LEN - 0.3, 0.12, 0.08, 0, 1.02, s * ZS);
      b.box(M.aluDark, LEN - 0.3, 0.05, 0.12, 0, 1.09, s * (ZS + 0.02));
      b.box(M.aluDark, LEN - 0.5, 0.06, 0.05, 0, 0.3, s * ZS);
    }
    for (let i = 0; i <= 7; i += 1) {
      const x = -5.5 + i * (11 / 7);
      for (const s of [-1, 1]) {
        b.box(M.aluDark, 0.08, 0.96, 0.08, x, 0.48, s * ZS);
        b.cyl(M.ink, 0.07, 0.04, x, 0.02, s * ZS, 0, 0, 0, 12, 0.09);
      }
      b.box(M.aluDark, 0.05, 0.05, ZS * 2, x, 0.2, 0);
    }
    // pulley pedestals and bearing blocks
    for (const x of [X0, X1]) {
      for (const s of [-1, 1]) {
        b.box(M.aluDark, 0.24, PY - 0.1, 0.24, x, (PY - 0.1) / 2, s * (ZS + 0.02));
        b.box(M.ink, 0.28, 0.18, 0.2, x, PY, s * (ZS + 0.02));
        b.cyl(M.ink, 0.07, 0.05, x, 0.025, s * (ZS + 0.02), 0, 0, 0, 12, 0.09);
      }
      b.axial(M.alu, 0.05, ZS * 2 + 0.2, new THREE.Vector3(x, PY, 0), Z_AXIS, 12);
    }
    b.build(g);
  }

  { // carry idler sets (troughed, three rolls) and return idlers
    const g = part('idlers', 0.4);
    const b = builder();
    const wingDir = (s) => new THREE.Vector3(0, sa, ca * s);
    for (const x of carryXs) {
      const impact = x < LX + 0.9;
      const train = Math.abs(x - trainX) < 0.01;
      const rollMat = impact ? M.ink : M.roller;
      const frameMat = train ? M.yellow : M.aluDark;
      const centre = new THREE.Vector3(x, H_CARRY - RR, 0);
      b.axial(rollMat, RR, 2 * TC - 0.02, centre, Z_AXIS);
      const set = [{ pos: centre, dir: Z_AXIS.clone(), len: 2 * TC - 0.02 }];
      for (const s of [-1, 1]) {
        const mz = TC + (TW / 2) * ca + RR * sa, my = (TW / 2) * sa - RR * ca;
        const pos = new THREE.Vector3(x, H_CARRY + my, s * mz);
        b.axial(rollMat, RR, TW - 0.03, pos, wingDir(s));
        set.push({ pos, dir: wingDir(s), len: TW - 0.03 });
        b.box(frameMat, 0.04, 0.38, 0.04, x, H_CARRY - 0.12 + 0.19, s * 0.67);
        b.box(frameMat, 0.035, 0.1, 0.035, x, H_CARRY - 0.08, s * (TC + 0.01));
      }
      b.box(frameMat, 0.06, 0.06, ZS * 2 + 0.06, x, H_CARRY - 0.13, 0);
      if (train) { // self-training idler: vertical edge guide rolls + pivot plate
        for (const s of [-1, 1]) b.cyl(M.roller, 0.04, 0.2, x - 0.02, H_CARRY + 0.33, s * 0.69, 0, 0, 0, 14);
        b.box(M.yellow, 0.3, 0.025, 0.3, x, H_CARRY - 0.17, 0);
      }
      rollerDefs.push({ kind: 'carry', x, set, r: RR });
    }
    for (const x of returnXs) {
      const pos = new THREE.Vector3(x, H_RETURN - 0.006 - 0.055, 0);
      b.axial(M.roller, 0.055, BWF + 0.06, pos, Z_AXIS);
      for (const s of [-1, 1]) {
        b.box(M.aluDark, 0.05, 0.52, 0.05, x, 0.96 - 0.26, s * (ZS - 0.04));
        b.box(M.ink, 0.08, 0.06, 0.06, x, pos.y, s * (BWF / 2 + 0.06));
      }
      rollerDefs.push({ kind: 'return', x, set: [{ pos, dir: Z_AXIS.clone(), len: BWF + 0.06 }], r: 0.055 });
    }
    b.build(g);
  }

  // belt + pulleys (animated parts live in this group)
  const beltG = part('belt', 0.55);
  const pulleys = [];
  {
    const carry = new THREE.Mesh(carryBeltGeometry(), M.belt);
    carry.castShadow = true; carry.receiveShadow = true;
    beltG.add(carry);
    const ret = new THREE.Mesh(new THREE.PlaneGeometry(LEN, BWF - 0.04), M.beltRet);
    ret.rotation.x = -Math.PI / 2;
    ret.position.y = H_RETURN - 0.006;
    ret.castShadow = true; ret.receiveShadow = true;
    beltG.add(ret);
    for (const [x, th] of [[X1, 0], [X0, Math.PI]]) {
      const wrap = new THREE.Mesh(new THREE.CylinderGeometry(PR + 0.008, PR + 0.008, BWF, 36, 1, true, th, Math.PI), M.rubber);
      wrap.rotation.x = Math.PI / 2;
      wrap.position.set(x, PY, 0);
      wrap.castShadow = true;
      beltG.add(wrap);
      const p = new THREE.Mesh(new THREE.CylinderGeometry(PR, PR, BWF + 0.16, 40), M.pulley);
      p.rotation.x = Math.PI / 2;
      p.position.set(x, PY, 0);
      p.castShadow = true; p.receiveShadow = true;
      beltG.add(p);
      pulleys.push(p);
      rollerDefs.push({ kind: 'pulley', x, pulley: true, r: PR });
    }
    // take-up / bend pulley under the return strand
    const tp = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.13, BWF + 0.1, 28), M.pulley);
    tp.rotation.x = Math.PI / 2;
    tp.position.set(TX, H_RETURN - 0.006 - 0.13, 0);
    tp.castShadow = true;
    beltG.add(tp);
    pulleys.push(tp);
  }

  { // pulley guards (tail both sides + end, head walkway side)
    const g = part('guards', 0.7);
    const b = builder();
    const gw = 1.15, gh = 1.3, gy = 0.2 + gh / 2;
    for (const s of [-1, 1]) b.guard(gw, gh, X0 + 0.02, gy, s * 1.0, 0);
    b.guard(2.0, gh, X0 - 0.56, gy, 0, Math.PI / 2);
    b.guard(gw, gh, X1 - 0.02, gy, 1.0, 0);
    for (const [x, z] of [[X0 - 0.56, -1.0], [X0 - 0.56, 1.0], [X0 + 0.6, 1.0], [X0 + 0.6, -1.0], [X1 - 0.6, 1.0], [X1 + 0.56, 1.0]]) {
      b.box(M.yellow, 0.05, gh + 0.2, 0.05, x, (gh + 0.2) / 2, z);
    }
    b.build(g);
  }

  { // loading hopper + skirt boards at the tail (station 1)
    const g = part('hopper', 0.8);
    const b = builder();
    b.geo(M.aluPlate, new THREE.CylinderGeometry(0.86, 0.33, 0.72, 4, 1, true), LX, 2.24, 0, 0, Math.PI / 4, 0);
    const lip = 0.86 * Math.SQRT1_2;
    for (const s of [-1, 1]) {
      b.box(M.aluDark, lip * 2 + 0.06, 0.05, 0.05, LX, 2.6, s * lip);
      b.box(M.aluDark, 0.05, 0.05, lip * 2 + 0.06, LX + s * lip, 2.6, 0);
    }
    for (const s of [-1, 1]) {
      b.box(M.aluPlate, 0.48, 0.42, 0.02, LX, 1.67, s * 0.23);
      b.box(M.aluPlate, 0.02, 0.42, 0.48, LX + s * 0.23, 1.67, 0);
      b.box(M.alu, 1.8, 0.2, 0.025, LX + 0.5, H_CARRY + 0.19, s * 0.34);
      b.box(M.ink, 1.8, 0.05, 0.03, LX + 0.5, H_CARRY + 0.07, s * 0.34);
    }
    for (const dx of [-0.62, 0.62]) {
      for (const s of [-1, 1]) b.box(M.aluDark, 0.07, 2.3, 0.07, LX + dx, 1.15, s * 0.98);
      b.box(M.alu, 0.06, 0.06, 2.02, LX + dx, 2.28, 0);
    }
    for (const s of [-1, 1]) b.box(M.alu, 1.3, 0.06, 0.06, LX, 2.28, s * 0.98);
    for (const s of [-1, 1]) b.box(M.aluDark, 0.05, 0.05, 0.62, LX, 2.28, s * 0.68);
    b.build(g);
  }

  // take-up tower with moving counterweight (station 4)
  const cw = new THREE.Group();
  const cwCable = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 1, 6), M.ink);
  {
    const g = part('takeup', 0.9);
    const b = builder();
    const hh = 2.75, d = 0.34;
    for (const dx of [-d, d]) for (const dz of [-d, d]) {
      b.box(M.aluDark, 0.07, hh, 0.07, TX + dx, hh / 2, TZ + dz);
      b.cyl(M.ink, 0.07, 0.04, TX + dx, 0.02, TZ + dz, 0, 0, 0, 12, 0.09);
    }
    for (const y of [0.12, hh]) {
      for (const dz of [-d, d]) b.box(M.alu, d * 2 + 0.07, 0.06, 0.06, TX, y, TZ + dz);
      for (const dx of [-d, d]) b.box(M.alu, 0.06, 0.06, d * 2 + 0.07, TX + dx, y, TZ);
    }
    b.axial(M.roller, 0.2, 0.06, new THREE.Vector3(TX, hh + 0.12, TZ), X_AXIS, 28);
    b.box(M.yellow, 0.16, 0.22, 0.1, TX, hh + 0.05, TZ);
    b.guard(d * 2, 1.9, TX, 1.1, TZ + d + 0.02, 0);
    b.guard(d * 2, 1.9, TX - d - 0.02, 1.1, TZ, Math.PI / 2);
    b.guard(d * 2, 1.9, TX + d + 0.02, 1.1, TZ, Math.PI / 2);
    b.box(M.alu, 0.06, 0.06, Math.abs(TZ + d) - ZS + 0.05, TX, 0.98, (TZ + d - ZS) / 2);
    b.build(g);
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.46, 0.66, 0.46), M.weight);
    body.castShadow = true;
    const band = new THREE.Mesh(new THREE.BoxGeometry(0.47, 0.08, 0.47), M.yellow);
    band.position.y = 0.18;
    cw.add(body, band);
    cw.position.set(TX, 1.2, TZ);
    g.add(cw, cwCable);
  }

  // drive: motor + right-angle gearbox + coupling guard at the head (station 5)
  const fan = new THREE.Group();
  {
    const g = part('drive', 1.0);
    const b = builder();
    const gz = -1.36;
    b.box(M.grey, 1.55, 0.6, 0.6, X1 - 0.38, 0.3, gz);
    b.box(M.aluDark, 1.6, 0.06, 0.66, X1 - 0.38, 0.63, gz);
    b.box(M.aluDark, 0.56, 0.56, 0.46, X1, PY, gz);
    b.box(M.ink, 0.6, 0.06, 0.5, X1, PY + 0.3, gz);
    b.box(M.yellow, 0.34, 0.34, 0.26, X1, PY, -0.98);
    b.axial(M.alu, 0.05, 0.3, new THREE.Vector3(X1, PY, -0.8), Z_AXIS, 12);
    const mx = X1 - 0.68, my = PY - 0.05;
    b.axial(M.white, 0.21, 0.66, new THREE.Vector3(mx, my, gz), X_AXIS, 28);
    for (let i = 0; i < 7; i += 1) b.axial(M.white, 0.226, 0.018, new THREE.Vector3(mx - 0.27 + i * 0.09, my, gz), X_AXIS, 28);
    b.axial(M.ink, 0.2, 0.14, new THREE.Vector3(mx - 0.4, my, gz), X_AXIS, 28);
    b.box(M.white, 0.2, 0.13, 0.2, mx + 0.08, my + 0.26, gz);
    b.box(M.ink, 0.06, 0.06, 0.06, mx + 0.08, my + 0.26, gz + 0.13);
    b.build(g);
    // cooling fan disc behind the grille (spins with the belt)
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.012, 0.3, 0.05), M.aluDark);
    const blade2 = blade.clone(); blade2.rotation.x = Math.PI / 2;
    fan.add(blade, blade2);
    fan.position.set(mx - 0.48, my, gz);
    g.add(fan);
  }

  { // head hood, discharge chute and stockpile
    const g = part('discharge', 1.1);
    const b = builder();
    const hood = new THREE.CylinderGeometry(PR + 0.27, PR + 0.27, BWF + 0.12, 24, 1, true, Math.PI / 2, Math.PI / 2);
    b.geo(M.aluPlate, hood, X1, PY, 0, Math.PI / 2, 0, 0);
    const ang = Math.atan2(-0.24, 0.5);
    b.box(M.aluPlate, 0.56, 0.02, 1.0, X1 + 0.44, 0.83, 0, 0, 0, ang);
    for (const s of [-1, 1]) {
      b.box(M.aluPlate, 0.56, 0.2, 0.02, X1 + 0.44, 0.92, s * 0.5, 0, 0, ang);
      b.box(M.aluDark, 0.05, 0.72, 0.05, X1 + 0.66, 0.36, s * 0.5);
    }
    b.build(g);
    const pile = new THREE.Mesh(pileGeometry(PILE_R, PILE_H), M.pile);
    pile.position.set(PX, 0, 0);
    pile.castShadow = true; pile.receiveShadow = true;
    g.add(pile);
    // loose lumps scattered on the pile
    const N = 90;
    const lumps = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(0.07, 0), M.lump, N);
    const d = new THREE.Object3D(), col = new THREE.Color();
    for (let i = 0; i < N; i += 1) {
      const a = Math.random() * Math.PI * 2, f = 0.12 + Math.random() * 0.85;
      d.position.set(PX + Math.cos(a) * f * PILE_R, PILE_H * (1 - f) + 0.02, Math.sin(a) * f * PILE_R);
      d.rotation.set(Math.random() * 6, Math.random() * 6, 0);
      d.scale.set(0.7 + Math.random() * 0.9, 0.6 + Math.random() * 0.6, 0.7 + Math.random() * 0.9);
      d.updateMatrix();
      lumps.setMatrixAt(i, d.matrix);
      lumps.setColorAt(i, col.setHex(ORE_TONES[i % ORE_TONES.length]));
    }
    lumps.castShadow = true; lumps.receiveShadow = true;
    g.add(lumps);
  }

  { // far-side handrail + walkway-side pull-wire (emergency stop lanyard)
    const g = part('rails', 1.2);
    const b = builder();
    const zr = -2.05, xa = -5.8, xb = 6.3;
    for (let i = 0; i <= 8; i += 1) b.cyl(M.yellow, 0.025, 1.1, xa + (i * (xb - xa)) / 8, 0.55, zr, 0, 0, 0, 10);
    for (const y of [1.1, 0.6]) b.rod(M.yellow, 0.024, new THREE.Vector3(xa, y, zr), new THREE.Vector3(xb, y, zr), 10);
    b.box(M.yellow, xb - xa, 0.1, 0.02, (xa + xb) / 2, 0.05, zr);
    const zp = 1.0, pa = -3.9, pb = 5.25;
    for (const x of [pa, (pa + pb) / 2 - 1.5, (pa + pb) / 2 + 1.5, pb]) b.box(M.aluDark, 0.04, 1.0, 0.04, x, 0.5, zp);
    b.rod(M.yellow, 0.008, new THREE.Vector3(pa, 0.97, zp), new THREE.Vector3(pb, 0.97, zp), 6);
    for (const x of [pa, pb]) {
      b.box(M.yellow, 0.14, 0.18, 0.1, x, 1.02, zp + 0.02);
      b.cyl(M.alarm, 0.03, 0.04, x, 1.13, zp + 0.02, 0, 0, 0, 12);
    }
    b.build(g);
  }

  { // site props: walkway bollards, spare-idler laydown + tool cart behind the far rail
    const g = part('props', 1.25);
    const b = builder();
    for (const [x, z] of [[WALK_X0 - 0.25, Z_WALK - 0.6], [WALK_X0 - 0.25, Z_WALK + 0.6], [WALK_X1 + 0.25, Z_WALK - 0.6]]) {
      b.cyl(M.yellow, 0.09, 0.9, x, 0.45, z, 0, 0, 0, 16);
      b.cyl(M.ink, 0.092, 0.08, x, 0.62, z, 0, 0, 0, 16);
    }
    const lx = -0.6, lz = -3.05;
    b.box(M.aluDark, 1.2, 0.12, 1.0, lx, 0.06, lz);
    for (let i = 0; i < 4; i += 1) b.axial(M.roller, RR, 0.95, new THREE.Vector3(lx - 0.39 + i * 0.26, 0.12 + RR, lz), Z_AXIS);
    for (let i = 0; i < 3; i += 1) b.axial(M.roller, RR, 0.95, new THREE.Vector3(lx - 0.26 + i * 0.26, 0.12 + RR * 2.75, lz), Z_AXIS);
    const cx = 1.4, cz = -2.95;
    b.box(M.yellow, 0.9, 0.5, 0.55, cx, 0.5, cz);
    b.box(M.ink, 0.92, 0.04, 0.57, cx, 0.77, cz);
    for (let i = 0; i < 3; i += 1) b.box(M.aluDark, 0.84, 0.012, 0.01, cx, 0.36 + i * 0.12, cz + 0.28);
    for (const dx of [-0.36, 0.36]) for (const dz of [-0.2, 0.2]) b.cyl(M.ink, 0.06, 0.05, cx + dx, 0.18, cz + dz, Math.PI / 2, 0, 0, 14);
    for (const dx of [-0.36, 0.36]) b.box(M.aluDark, 0.04, 0.05, 0.4, cx + dx, 0.22, cz);
    b.rod(M.aluDark, 0.015, new THREE.Vector3(cx - 0.45, 0.78, cz - 0.24), new THREE.Vector3(cx - 0.62, 1.02, cz - 0.24));
    b.rod(M.aluDark, 0.015, new THREE.Vector3(cx - 0.45, 0.78, cz + 0.24), new THREE.Vector3(cx - 0.62, 1.02, cz + 0.24));
    b.rod(M.aluDark, 0.015, new THREE.Vector3(cx - 0.62, 1.02, cz - 0.24), new THREE.Vector3(cx - 0.62, 1.02, cz + 0.24));
    b.build(g);
  }

  // ---------- ore on the belt ----------
  const ORE_N = 140;
  const oreGroup = new THREE.Group();
  oreGroup.visible = false;
  scene.add(oreGroup);
  const ore = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(0.06, 0), M.lump, ORE_N);
  ore.castShadow = true;
  ore.receiveShadow = true;
  ore.frustumCulled = false;
  oreGroup.add(ore);
  const pileHeight = (x, z) => Math.max(0, PILE_H * (1 - Math.hypot(x - PX, z) / PILE_R));
  const oreS = Array.from({ length: ORE_N }, (_, i) => {
    const s = 0.65 + Math.random() * 1.0;
    return {
      x: LX + (i / ORE_N) * (X1 - LX), z: (Math.random() - 0.5) * 0.62, s, sy: s * (0.6 + Math.random() * 0.5),
      rx: Math.random() * 6, ry: Math.random() * 6, falling: false, vx: 0, vy: 0, y: H_CARRY,
    };
  });
  {
    const col = new THREE.Color();
    for (let i = 0; i < ORE_N; i += 1) ore.setColorAt(i, col.setHex(ORE_TONES[(i * 3) % ORE_TONES.length]));
  }
  const dummy = new THREE.Object3D();
  function updateOre(dt, v) {
    for (let i = 0; i < ORE_N; i += 1) {
      const o = oreS[i];
      if (!o.falling) {
        o.x += v * dt;
        const f = troughF(o.x);
        o.y = H_CARRY + 0.006 + troughY(o.z * (0.55 + 0.45 * f), f) + 0.04 * o.sy;
        if (o.x > X1 + 0.04) { o.falling = true; o.vx = Math.max(v, 0.5) * 1.45; o.vy = 0.3; }
      } else {
        o.x += o.vx * dt;
        o.vy -= 9.8 * dt;
        o.y += o.vy * dt;
        o.rx += dt * 6;
        if (o.y <= pileHeight(o.x, o.z) + 0.03) {
          o.falling = false;
          o.x = LX - 0.25 + Math.random() * 0.3;
          o.z = (Math.random() - 0.5) * 0.62;
        }
      }
      dummy.position.set(o.x, o.y, o.z * (o.falling ? 1 : 0.55 + 0.45 * troughF(o.x)));
      dummy.rotation.set(o.rx, o.ry, 0);
      dummy.scale.set(o.s, o.sy, o.s);
      dummy.updateMatrix();
      ore.setMatrixAt(i, dummy.matrix);
    }
    ore.instanceMatrix.needsUpdate = true;
  }

  // ---------- power tools: grip at the origin, business end along +Z, up +Y ----------
  function makeTool(kind) {
    const g = new THREE.Group();
    const b = builder();
    const RX = Math.PI / 2, RZ = Math.PI / 2;
    if (kind === 'drill') {
      b.box(M.yellow, 0.07, 0.085, 0.2, 0, 0.075, 0.03);
      b.box(M.ink, 0.072, 0.087, 0.04, 0, 0.075, -0.075);
      b.cyl(M.ink, 0.022, 0.05, 0, 0.075, 0.155, RX);
      b.cyl(M.alu, 0.004, 0.09, 0, 0.075, 0.225, RX, 0, 0, 8);
      b.box(M.ink, 0.045, 0.13, 0.055, 0, -0.01, -0.01, 0.22);
      b.box(M.ink, 0.078, 0.045, 0.11, 0, -0.085, -0.02);
    } else if (kind === 'hammer') {
      b.box(M.yellow, 0.085, 0.1, 0.3, 0, 0.08, 0.06);
      b.box(M.ink, 0.045, 0.17, 0.05, 0, 0.0, -0.11);
      b.box(M.ink, 0.045, 0.04, 0.14, 0, -0.08, -0.05);
      b.cyl(M.ink, 0.017, 0.17, 0.1, 0.08, 0.17, 0, 0, RZ, 10);
      b.cyl(M.ink, 0.026, 0.06, 0, 0.08, 0.24, RX);
      b.cyl(M.alu, 0.006, 0.2, 0, 0.08, 0.37, RX, 0, 0, 8);
    } else if (kind === 'grinder') {
      b.cyl(M.yellow, 0.034, 0.25, 0, 0, 0, RX);
      b.cyl(M.ink, 0.036, 0.05, 0, 0, -0.13, RX);
      b.box(M.ink, 0.06, 0.07, 0.075, 0, 0, 0.16);
      b.cyl(M.roller, 0.064, 0.006, 0, -0.045, 0.175, 0, 0, 0, 28);
      b.box(M.ink, 0.13, 0.012, 0.07, 0, -0.03, 0.15);
      b.cyl(M.ink, 0.014, 0.13, 0.075, 0, 0.14, 0, 0, RZ, 10);
    } else if (kind === 'breaker') {
      b.cyl(M.ink, 0.02, 0.38, 0, 0, 0, 0, 0, RZ, 10);
      for (const s of [-1, 1]) b.box(M.ink, 0.03, 0.09, 0.03, s * 0.12, -0.05, 0);
      b.box(M.yellow, 0.17, 0.23, 0.15, 0, -0.19, 0);
      b.cyl(M.yellow, 0.05, 0.36, 0, -0.48, 0);
      b.cyl(M.ink, 0.056, 0.05, 0, -0.68, 0);
      b.cyl(M.alu, 0.016, 0.25, 0, -0.83, 0, 0, 0, 0, 8, 0.004);
    } else if (kind === 'jack') { // hydraulic belt lifter / bottle jack, carried by its top handle
      b.box(M.ink, 0.05, 0.03, 0.22, 0, 0, 0);
      for (const s of [-1, 1]) b.box(M.ink, 0.03, 0.08, 0.03, 0, -0.05, s * 0.09);
      b.cyl(M.roller, 0.035, 0.1, 0, -0.07, 0, 0, 0, 0, 14);
      b.cyl(M.yellow, 0.06, 0.26, 0, -0.23, 0, 0, 0, 0, 20);
      b.box(M.ink, 0.2, 0.03, 0.16, 0, -0.375, 0);
      b.box(M.alarm, 0.04, 0.05, 0.04, 0.07, -0.3, 0);
      b.rod(M.ink, 0.008, new THREE.Vector3(0.07, -0.29, 0), new THREE.Vector3(0.17, -0.1, 0.02), 6);
    } else { // impact wrench
      b.box(M.yellow, 0.07, 0.09, 0.14, 0, 0.075, 0.02);
      b.cyl(M.ink, 0.03, 0.045, 0, 0.075, 0.11, RX);
      b.box(M.ink, 0.022, 0.022, 0.03, 0, 0.075, 0.145);
      b.cyl(M.roller, 0.018, 0.05, 0, 0.075, 0.18, RX, 0, 0, 12);
      b.box(M.ink, 0.045, 0.13, 0.055, 0, -0.01, -0.01, 0.22);
      b.box(M.ink, 0.078, 0.045, 0.11, 0, -0.085, -0.02);
    }
    b.build(g, { receive: false });
    return g;
  }

  // ---------- tool station beside the middle of the walkway: yellow roll cab with a flat shadow-board tray ----------
  // (close to every station so each walk is short; the worker faces the camera while picking)
  const ISL_X = -1.1, ISL_Z = 3.78, ISL_W = 3.3, STAND_Z = 3.06;
  const CRIB_X = ISL_X, CRIB_Z = ISL_Z, CRIB_W = ISL_W, STAND_X = ISL_X; // legacy names
  const crib = { slots: {}, hits: [] };
  {
    const g = new THREE.Group();
    g.name = 'crib';
    g.position.set(ISL_X, 0, ISL_Z);
    scene.add(g);
    const b = builder();
    const w = ISL_W, d = 0.62, h = 0.92;
    b.box(M.yellow, w, h - 0.08, d, 0, (h - 0.08) / 2 + 0.08, 0);
    for (const s of [-1, 1]) for (let i = 1; i < 6; i += 1) b.box(M.ink, w - 0.12, 0.012, 0.01, 0, 0.12 + i * 0.13, s * (d / 2 + 0.005));
    for (const s of [-1, 1]) for (let i = 0; i < 4; i += 1) b.box(M.alu, 0.5, 0.025, 0.02, -w / 2 + 0.55 + i * 0.73, 0.82, s * (d / 2 + 0.015));
    b.box(M.ink, 0.12, 0.14, 0.04, w / 2 - 0.22, 0.6, d / 2 + 0.02);
    b.box(M.grey, w + 0.04, 0.05, d + 0.04, 0, h + 0.025, 0);
    for (const s of [-1, 1]) b.box(M.yellow, 0.05, 0.09, d + 0.04, s * (w / 2 + 0.02), h + 0.07, 0);
    for (const s of [-1, 1]) for (const sz of [-1, 1]) b.cyl(M.ink, 0.045, 0.05, s * (w / 2 - 0.16), 0.045, sz * (d / 2 - 0.1), Math.PI / 2, 0, 0, 12);
    b.build(g);
    const slotTex = (long) => canvasTex(long ? 160 : 72, 96, (c, cw2, ch) => {
      c.fillStyle = '#f7f8fa'; c.fillRect(0, 0, cw2, ch);
      c.strokeStyle = 'rgba(27,29,33,0.4)'; c.lineWidth = 2; c.setLineDash([5, 4]);
      c.strokeRect(6, 6, cw2 - 12, ch - 12);
    }, { repeat: false });
    const texS = slotTex(false), texL = slotTex(true);
    const hitMat2 = new THREE.MeshBasicMaterial({ visible: false });
    let x = -w / 2 + 0.12;
    for (const kind of CRIB_ORDER) {
      const id = CRIB_IDS[kind];
      const long = kind === 'breaker';
      const sw = long ? 1.0 : 0.46, cx = x + sw / 2;
      x += sw + 0.02;
      const plateMat = std(0xffffff, 0.8, 0, { map: long ? texL : texS, emissive: new THREE.Color(C.tool), emissiveIntensity: 0 });
      const plate = new THREE.Mesh(new THREE.PlaneGeometry(sw - 0.04, d - 0.1), plateMat);
      plate.rotation.x = -Math.PI / 2;
      plate.position.set(cx, h + 0.052, 0);
      g.add(plate);
      const tool = makeTool(kind);
      if (long || kind === 'jack') { tool.rotation.set(0, 0, Math.PI / 2, 'YXZ'); tool.position.set(cx + (long ? 0.47 : 0.19), h + 0.11, 0); }
      else { tool.rotation.set(0, Math.PI / 2, Math.PI / 2, 'YXZ'); tool.position.set(cx - 0.05, h + 0.1, 0.02); }
      tool.scale.setScalar(long ? 1 : 1.25);
      g.add(tool);
      const hit = new THREE.Mesh(new THREE.BoxGeometry(sw, 0.34, d), hitMat2);
      hit.position.set(cx, h + 0.17, 0);
      hit.userData.toolId = id;
      g.add(hit);
      crib.hits.push(hit);
      crib.slots[id] = { id, kind, tool, plateMat, glow: 0, world: new THREE.Vector3(), quat: new THREE.Quaternion(), scale: tool.scale.x, standX: ISL_X + cx };
    }
    g.updateMatrixWorld(true);
    for (const s of Object.values(crib.slots)) { s.tool.getWorldPosition(s.world); s.tool.getWorldQuaternion(s.quat); }
  }
  const slotOf = (kindOrId) => (kindOrId ? crib.slots[CRIB_IDS[kindOrId]] || crib.slots[kindOrId] || null : null);
  const highlightSet = new Set();
  function updateCrib(dt) {
    const pulse = 0.5 + 0.5 * Math.sin(t * 3.2);
    for (const s of Object.values(crib.slots)) {
      let e = 0;
      if (s.glow > 0) { s.glow = Math.max(0, s.glow - dt); e = Math.sin(clamp(1 - s.glow / 1.8, 0, 1) * Math.PI) * 1.5; }
      if (highlightSet.has(s.id) && s.tool.visible) e = Math.max(e, 0.45 + 0.75 * pulse);
      if (s.plateMat.emissiveIntensity !== e) s.plateMat.emissiveIntensity = e;
    }
  }

  let loaderRig = null;
  // ---------- mine-site setting (static): workshop (door open), office, MCC, light towers, haul truck, ute, loader,
  // handrailed walkway, signage, drums/IBC/skip, tyre tracks, fence and a ring of berms ----------
  {
    const g = new THREE.Group();
    g.name = 'site';
    scene.add(g);
    const b = builder();
    const ribTex = canvasTex(96, 16, (c, w, h) => {
      for (let x = 0; x < w; x += 1) {
        const k = 0.5 + 0.5 * Math.cos(((x % 16) / 16) * Math.PI * 2);
        const l = Math.round(196 + 38 * k);
        c.fillStyle = `rgb(${l - 2},${l},${l + 3})`; c.fillRect(x, 0, 1, h);
      }
    });
    const clad = std(0xffffff, 0.5, 0.35, { map: ribTex });
    const roofMat = std(0xc9ced4, 0.55, 0.35, { map: ribTex });
    const dark = std(0x4a4f55, 0.6, 0.2);
    const glass = std(0x3b4652, 0.12, 0.55);
    const lamp = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
    const inner = std(0x5d636a, 0.85, 0.1, { side: THREE.BackSide });
    const slate = std(0x64748b, 0.6, 0.1);
    const drumMat = std(0x41648a, 0.45, 0.35);
    const concrete = std(0xc9ced4, 0.9);
    const fenceMat = new THREE.MeshStandardMaterial({
      map: canvasTex(32, 32, (c, w, h) => { c.clearRect(0, 0, w, h); c.fillStyle = '#8a929b'; c.fillRect(0, 0, w, 2); c.fillRect(0, 0, 2, h); }),
      transparent: true, depthWrite: false, side: THREE.DoubleSide, roughness: 0.7,
    });
    const wallP = (mat, w, h, x, y, z, ry) => {
      const p = new THREE.PlaneGeometry(w, h);
      const uv = p.attributes.uv;
      for (let i = 0; i < uv.count; i += 1) uv.setX(i, uv.getX(i) * w);
      b.geo(mat, p, x, y, z, 0, ry, 0);
    };
    const gable = (SX, SZ, SW, SD, SH, RISE) => { // two sloped corrugated roof planes + gable ends
      const half = SD / 2 + 0.25, slope = Math.hypot(half, RISE), ang = Math.atan2(RISE, half);
      for (const s of [1, -1]) {
        const p = new THREE.PlaneGeometry(SW + 0.4, slope);
        const uv = p.attributes.uv;
        for (let i = 0; i < uv.count; i += 1) uv.setX(i, uv.getX(i) * (SW + 0.4));
        b.geo(roofMat, p, SX, SH + RISE / 2, SZ + s * half / 2, s > 0 ? -Math.PI / 2 + ang : -Math.PI / 2 - ang, 0, 0);
      }
      for (const s of [-1, 1]) {
        const sh = new THREE.Shape();
        sh.moveTo(-SD / 2, 0); sh.lineTo(SD / 2, 0); sh.lineTo(0, RISE * (SD / 2) / half); sh.closePath();
        b.geo(clad, new THREE.ShapeGeometry(sh), SX + s * SW / 2, SH, SZ, 0, s * Math.PI / 2, 0);
      }
    };
    // safety / site signs: canvas boards on two posts (text is part of the world, not UI)
    const signs = [];
    const sign = (band, bandInk, title, lines, x, z, ry, y0 = 1.25, wall = false) => {
      const tex = canvasTex(256, 176, (c, w, h) => {
        c.fillStyle = '#ffffff'; c.fillRect(0, 0, w, h);
        c.fillStyle = band; c.fillRect(0, 0, w, 54);
        c.fillStyle = bandInk; c.font = '700 30px Arial, Helvetica, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
        c.fillText(title, w / 2, 29);
        c.fillStyle = '#111316'; c.font = '600 27px Arial, Helvetica, sans-serif';
        lines.forEach((ln, i) => c.fillText(ln, w / 2, 90 + i * 36));
        c.strokeStyle = '#111316'; c.lineWidth = 6; c.strokeRect(3, 3, w - 6, h - 6);
      }, { repeat: false });
      const m = new THREE.Mesh(new THREE.PlaneGeometry(1.16, 0.8), std(0xffffff, 0.6, 0, { map: tex }));
      const c = Math.cos(ry), s = Math.sin(ry);
      m.position.set(x + s * 0.03, y0 + 0.4, z + c * 0.03);
      m.rotation.y = ry;
      signs.push(m);
      b.box(M.grey, 1.22, 0.86, 0.03, x, y0 + 0.4, z, 0, ry, 0);
      if (!wall) for (const sd of [-0.45, 0.45]) b.box(M.aluDark, 0.05, y0 + 0.75, 0.05, x - c * sd, (y0 + 0.75) / 2, z + s * sd, 0, ry, 0);
    };

    // --- maintenance workshop behind the tail end, roller door open (interior visible) ---
    const SX = -8.0, SZ = -8.6, SW = 7.0, SD = 4.6, SH = 4.0;
    const DX0 = -10.2, DX1 = -7.0, DH = 3.3, zf = SZ + SD / 2;
    const xl = SX - SW / 2, xr = SX + SW / 2;
    wallP(clad, DX0 - xl, SH, (xl + DX0) / 2, SH / 2, zf, 0);
    wallP(clad, xr - DX1, SH, (DX1 + xr) / 2, SH / 2, zf, 0);
    wallP(clad, DX1 - DX0, SH - DH, (DX0 + DX1) / 2, DH + (SH - DH) / 2, zf, 0);
    wallP(clad, SW, SH, SX, SH / 2, SZ - SD / 2, Math.PI);
    wallP(clad, SD, SH, xl, SH / 2, SZ, -Math.PI / 2);
    wallP(clad, SD, SH, xr, SH / 2, SZ, Math.PI / 2);
    gable(SX, SZ, SW, SD, SH, 1.0);
    b.box(inner, SW - 0.1, SH - 0.04, SD - 0.1, SX, SH / 2, SZ);
    for (const [x, z] of [[xl, zf], [xr, zf], [xl, SZ - SD / 2], [xr, SZ - SD / 2]]) b.box(M.white, 0.1, SH, 0.1, x, SH / 2, z);
    for (const x of [DX0, DX1]) b.box(dark, 0.12, DH, 0.12, x, DH / 2, zf + 0.04);
    b.axial(M.grey, 0.26, DX1 - DX0 + 0.3, new THREE.Vector3((DX0 + DX1) / 2, DH + 0.32, zf + 0.2), X_AXIS, 20);
    b.box(M.yellow, DX1 - DX0 + 0.3, 0.1, 0.1, (DX0 + DX1) / 2, DH + 0.02, zf + 0.06);
    // interior: workbench + vise on the back wall, shelving with bins, a lit ceiling lamp
    const bz = SZ - SD / 2 + 0.45;
    b.box(M.aluDark, 2.4, 0.06, 0.7, SX - 1.0, 0.9, bz);
    for (const dx of [-1.1, 1.1]) for (const dz of [-0.3, 0.3]) b.box(dark, 0.06, 0.88, 0.06, SX - 1.0 + dx, 0.44, bz + dz);
    b.box(dark, 0.16, 0.14, 0.12, SX - 1.8, 1.0, bz + 0.25);
    b.box(M.grey, 2.4, 1.2, 0.04, SX - 1.0, 1.65, SZ - SD / 2 + 0.07);
    for (let i = 0; i < 3; i += 1) {
      b.box(M.aluDark, 1.6, 0.04, 0.5, xl + 0.4, 0.5 + i * 0.75, SZ - 0.6 + 0.0, 0, Math.PI / 2, 0);
      for (let k = 0; k < 4; k += 1) b.box(k % 2 ? M.yellow : slate, 0.3, 0.26, 0.4, xl + 0.4, 0.66 + i * 0.75, SZ - 1.2 + k * 0.4);
    }
    for (const dz of [-1.4, 0.2]) b.box(M.aluDark, 0.05, 2.3, 0.05, xl + 0.4, 1.15, SZ + dz - 0.05);
    b.box(lamp, 1.2, 0.05, 0.2, SX - 0.8, SH - 0.35, SZ);
    // outside details: door threshold hatch, flanking bollards, downpipes, wall lights, window band, sign
    const hz = new THREE.PlaneGeometry(DX1 - DX0, 1.0);
    const huv = hz.attributes.uv;
    for (let i = 0; i < huv.count; i += 1) huv.setXY(i, huv.getX(i) * ((DX1 - DX0) / 0.6), huv.getY(i) * (1.0 / 0.6));
    b.geo(M.hatch, hz, (DX0 + DX1) / 2, -0.025, zf + 0.5, -Math.PI / 2, 0, 0);
    for (const x of [DX0 - 0.35, DX1 + 0.35]) {
      b.cyl(M.yellow, 0.08, 1.0, x, 0.5, zf + 0.45, 0, 0, 0, 14);
      b.cyl(M.ink, 0.082, 0.07, x, 0.72, zf + 0.45, 0, 0, 0, 14);
    }
    for (const x of [xl + 0.15, xr - 0.15]) b.cyl(M.grey, 0.05, SH, x, SH / 2, zf + 0.12, 0, 0, 0, 10);
    for (const x of [DX0 + 0.3, DX1 - 0.3]) b.box(lamp, 0.3, 0.08, 0.12, x, DH + 0.6, zf + 0.08);
    b.box(glass, 0.05, 0.6, 3.2, xr + 0.02, 2.7, SZ);
    sign('#18181b', '#ffffff', 'CV-102', ['Maintenance', 'workshop'], (DX1 + xr) / 2, zf + 0.02, 0, 1.55, true);
    // --- site office container (on blocks, steps + rail) ---
    const OX = 1.6, OZ = -8.4, OW = 6.0, OD = 2.4, OH = 2.6, OB = 0.3;
    b.box(M.white, OW, OH, OD, OX, OB + OH / 2, OZ);
    b.box(slate, OW + 0.02, 0.14, OD + 0.02, OX, OB + OH - 0.35, OZ);
    b.box(M.grey, OW + 0.1, 0.1, OD + 0.1, OX, OB + OH + 0.05, OZ);
    for (const dx of [-OW / 2 + 0.3, OW / 2 - 0.3]) for (const dz of [-OD / 2 + 0.3, OD / 2 - 0.3]) b.box(concrete, 0.4, OB, 0.4, OX + dx, OB / 2, OZ + dz);
    for (const dx of [-2.0, -0.6, 2.2]) b.box(glass, 1.0, 0.8, 0.04, OX + dx, OB + 1.55, OZ + OD / 2 + 0.01);
    b.box(dark, 0.9, 2.05, 0.04, OX + 0.85, OB + 1.03, OZ + OD / 2 + 0.02);
    for (let i = 0; i < 2; i += 1) b.box(M.grey, 1.0, 0.15, 0.32, OX + 0.85, 0.075 + i * 0.15, OZ + OD / 2 + 0.62 - i * 0.3);
    for (const sd of [-0.5, 0.5]) b.rod(M.yellow, 0.02, new THREE.Vector3(OX + 0.85 + sd, 0, OZ + OD / 2 + 0.8), new THREE.Vector3(OX + 0.85 + sd, 1.2, OZ + OD / 2 + 0.2));
    b.box(M.grey, 0.75, 0.55, 0.4, OX - OW / 2 - 0.22, OB + 1.8, OZ);
    sign('#18181b', '#ffffff', 'Site office', ['Permit to work', 'sign in here'], OX - 1.3, OZ + OD / 2 + 0.03, 0, OB + 0.35, true);
    // --- MCC / electrical room with a yellow stripe, behind the head end ---
    const MX = 6.6, MZ = -5.3, MW = 4.0, MD = 2.2, MH = 2.7, PL = 0.35;
    b.box(M.grey, MW + 0.3, PL, MD + 0.3, MX, PL / 2, MZ);
    b.box(M.white, MW, MH, MD, MX, PL + MH / 2, MZ);
    b.box(M.yellow, MW + 0.02, 0.22, MD + 0.02, MX, PL + MH - 0.45, MZ);
    b.box(M.grey, MW + 0.14, 0.12, MD + 0.14, MX, PL + MH + 0.06, MZ);
    for (let i = 0; i < 5; i += 1) b.box(dark, 0.6, 0.035, 0.02, MX - 1.25, PL + 1.0 + i * 0.09, MZ + MD / 2 + 0.01);
    b.box(dark, 0.95, 2.05, 0.04, MX + 1.15, PL + 1.03, MZ + MD / 2 + 0.02);
    for (let i = 0; i < 2; i += 1) b.box(M.grey, 1.1, 0.12, 0.3, MX + 1.15, 0.12 + i * 0.12, MZ + MD / 2 + 0.6 - i * 0.28);
    for (const s of [-1, 1]) b.rod(M.yellow, 0.02, new THREE.Vector3(MX + 1.15 + s * 0.6, 0.0, MZ + MD / 2 + 0.75), new THREE.Vector3(MX + 1.15 + s * 0.6, 1.0, MZ + MD / 2 + 0.15));
    b.box(M.grey, 0.8, 0.62, 0.42, MX + MW / 2 + 0.22, PL + 1.7, MZ);
    b.cyl(dark, 0.22, 0.02, MX + MW / 2 + 0.44, PL + 1.7, MZ, 0, 0, Math.PI / 2, 20);
    sign('#f2c230', '#111316', 'Danger', ['High voltage', 'MCC-1'], MX - 0.2, MZ + MD / 2 + 0.03, 0, PL + 0.9, true);
    // cable ladder tray from the MCC to the head drive
    const ty = 2.6, tx = 4.9, tz0 = MZ + MD / 2, tz1 = -1.95, tlen = Math.abs(tz1 - tz0), tzc = (tz0 + tz1) / 2;
    b.box(M.alu, 0.38, 0.05, tlen, tx, ty, tzc);
    for (const s of [-1, 1]) b.box(M.aluDark, 0.03, 0.1, tlen, tx + s * 0.19, ty + 0.04, tzc);
    for (const z of [-3.9, -2.2]) b.box(M.aluDark, 0.06, ty, 0.06, tx + 0.3, ty / 2, z);
    b.rod(M.ink, 0.025, new THREE.Vector3(tx, ty, -1.95), new THREE.Vector3(5.4, 1.2, -1.36), 8);
    // --- mobile light towers, lamps angled down at the conveyor ---
    const tower = (x, z, ry) => {
      const c = Math.cos(ry), s = Math.sin(ry);
      const at = (lx, lz) => [x + lx * c + lz * s, z - lx * s + lz * c];
      b.box(M.yellow, 2.2, 0.75, 1.15, x, 0.85, z, 0, ry, 0);
      for (const sd of [-1, 1]) { const [wx, wz] = at(0.2, sd * 0.62); b.cyl(M.ink, 0.32, 0.18, wx, 0.32, wz, Math.PI / 2, ry, 0, 18); }
      for (const [lx, lz] of [[-1.0, -0.9], [-1.0, 0.9], [1.0, -0.9], [1.0, 0.9]]) { const [ox, oz] = at(lx, lz); b.box(M.ink, 0.12, 0.6, 0.12, ox, 0.3, oz, 0, ry, 0); }
      const [mx, mz] = at(-0.7, 0);
      b.cyl(M.alu, 0.07, 6.6, mx, 4.5, mz, 0, 0, 0, 10);
      b.box(M.ink, 1.6, 0.6, 0.14, mx, 7.7, mz, 0.35, ry + Math.PI / 2, 0);
      for (const [lz, ly] of [[-0.4, 0.15], [0.4, 0.15], [-0.4, -0.15], [0.4, -0.15]]) b.box(lamp, 0.02, 0.22, 0.32, mx + c * 0.09 + s * lz, 7.7 + ly, mz - s * 0.09 + c * lz, 0, ry, 0);
    };
    tower(-12.6, 0.8, -0.1);
    tower(12.4, -3.8, Math.PI + 0.35);
    // --- haul truck parked on the access road (background, left) ---
    {
      const T = new THREE.Group();
      const H = builder();
      H.box(M.ink, 2.0, 0.7, 8.6, 0, 1.5, 0);
      H.box(M.yellow, 5.2, 2.0, 6.2, 0, 3.0, -1.2);
      H.box(M.weight, 4.8, 0.1, 5.8, 0, 4.02, -1.2);
      H.box(M.yellow, 5.2, 0.22, 2.3, 0, 4.15, 2.9);
      H.box(M.yellow, 4.4, 1.4, 0.9, 0, 2.1, 3.9);
      H.box(M.ink, 2.6, 0.9, 0.04, 0, 2.1, 4.36);
      H.box(M.yellow, 1.7, 1.5, 1.8, -1.45, 3.1, 2.95);
      H.box(glass, 1.72, 0.8, 1.6, -1.45, 3.4, 3.0);
      for (const [x, z, wd] of [[-2.2, 2.7, 0.9], [2.2, 2.7, 0.9], [-2.4, -2.5, 1.7], [2.4, -2.5, 1.7]]) {
        H.cyl(M.ink, 1.35, wd, x, 1.35, z, 0, 0, Math.PI / 2, 24);
        H.cyl(M.yellow, 0.7, wd + 0.04, x, 1.35, z, 0, 0, Math.PI / 2, 16);
      }
      for (let i = 0; i < 4; i += 1) H.box(M.yellow, 0.05, 0.05, 0.5, 2.3, 1.0 + i * 0.4, 3.6);
      H.cyl(M.ink, 0.12, 1.4, 1.6, 4.4, 3.6, 0, 0, 0, 10);
      H.build(T);
      T.scale.setScalar(0.72);
      T.position.set(-15.2, 0, -6.8);
      T.rotation.y = 0.55;
      g.add(T);
    }
    // --- white ute beside the office ---
    {
      const U = new THREE.Group();
      const Ub = builder();
      Ub.box(M.white, 1.8, 0.75, 5.0, 0, 0.85, 0);
      Ub.box(M.white, 1.75, 0.8, 1.9, 0, 1.6, 0.55);
      Ub.box(glass, 1.77, 0.5, 1.7, 0, 1.68, 0.6);
      Ub.box(M.aluDark, 1.7, 0.45, 2.0, 0, 1.35, -1.4);
      for (const x of [-0.85, 0.85]) for (const z of [-1.6, 1.5]) Ub.cyl(M.ink, 0.38, 0.28, x, 0.38, z, 0, 0, Math.PI / 2, 18);
      Ub.cyl(std(C.amber, 0.4, 0, { emissive: new THREE.Color(C.amber), emissiveIntensity: 0.6 }), 0.07, 0.1, 0, 2.05, 0.6, 0, 0, 0, 12);
      Ub.rod(M.aluDark, 0.012, new THREE.Vector3(0.75, 1.2, -2.3), new THREE.Vector3(0.75, 3.6, -2.3), 6);
      Ub.box(M.alarm, 0.02, 0.3, 0.45, 0.75, 3.4, -2.07);
      Ub.build(U);
      U.position.set(7.9, 0, -9.5);
      U.rotation.y = Math.PI / 2 + 0.15;
      g.add(U);
    }
    // --- yard props: drums on a spill pallet, IBC tank, skip bin, cable reel, concrete barriers, spare idlers ---
    b.box(M.yellow, 1.3, 0.15, 1.3, -11.9, 0.075, -5.2);
    for (const [dx, dz] of [[-0.3, -0.3], [0.3, -0.3], [-0.3, 0.3], [0.3, 0.3]]) {
      b.cyl(drumMat, 0.28, 0.86, -11.9 + dx, 0.58, -5.2 + dz, 0, 0, 0, 18);
      b.cyl(M.ink, 0.285, 0.03, -11.9 + dx, 1.0, -5.2 + dz, 0, 0, 0, 18);
    }
    b.box(M.white, 1.0, 1.0, 1.15, -12.9, 0.68, -6.9);
    for (const [dx, dz] of [[-0.52, -0.6], [0.52, -0.6], [-0.52, 0.6], [0.52, 0.6]]) b.box(M.aluDark, 0.04, 1.2, 0.04, -12.9 + dx, 0.75, -6.9 + dz);
    b.box(M.aluDark, 1.1, 0.15, 1.25, -12.9, 0.08, -6.9);
    b.box(slate, 3.4, 1.2, 1.7, -2.6, 0.65, -11.6);
    b.box(slate, 3.6, 0.1, 1.9, -2.6, 1.27, -11.6);
    for (const s of [-1, 1]) b.box(slate, 0.1, 1.1, 1.9, -2.6 + s * 1.85, 0.62, -11.6, 0, 0, s * 0.18);
    for (const s of [-1, 1]) b.cyl(M.grey, 0.7, 0.06, 9.1 + s * 0.35, 0.7, -3.0, 0, 0, Math.PI / 2, 24);
    b.cyl(M.ink, 0.36, 0.64, 9.1, 0.7, -3.0, 0, 0, Math.PI / 2, 18);
    for (let i = 0; i < 4; i += 1) {
      b.box(concrete, 0.6, 0.8, 2.0, -19.2 + i * 0.05, 0.4, 0.5 - i * 2.1);
      b.box(concrete, 0.3, 0.2, 2.0, -19.2 + i * 0.05, 0.9, 0.5 - i * 2.1);
    }
    // --- walkway: yellow handrail along the outer walkway line, toe board low so the rings stay visible ---
    const zc = Z_WALK + 1.12;
    const xs = [WALK_X0, -4.6, -2.0, 0.6, 3.2, WALK_X1];
    const gapA = ISL_X - ISL_W / 2 - 0.9, gapB = ISL_X + ISL_W / 2 + 0.9; // opening at the tool station
    for (const [ra, rb] of [[WALK_X0, gapA], [gapB, WALK_X1]]) {
      for (const x of [ra, rb, ...xs.filter((q) => q > ra + 0.3 && q < rb - 0.3)]) b.cyl(M.yellow, 0.03, 1.1, x, 0.55, zc, 0, 0, 0, 10);
      for (const y of [1.1, 0.58]) b.rod(M.yellow, 0.025, new THREE.Vector3(ra, y, zc), new THREE.Vector3(rb, y, zc), 10);
      b.box(M.yellow, rb - ra, 0.1, 0.02, (ra + rb) / 2, 0.05, zc);
    }
    // --- safety signage ---
    sign('#1d4ed8', '#ffffff', 'Mandatory', ['PPE required', 'Hat · Hi-vis · Boots'], WALK_X0 - 0.85, Z_WALK + 0.3, 1.2);
    sign('#f2c230', '#111316', 'Warning', ['Isolate before', 'maintenance'], X0 - 0.4, -2.75, 0.25);
    sign('#f2c230', '#111316', 'Warning', ['Isolate before', 'maintenance'], X1 - 1.2, -2.75, -0.15);
    b.build(g);
    for (const m of signs) { m.castShadow = false; g.add(m); }

    // --- fence + a ring of berms on the horizon (no shadows) ---
    const f = builder();
    const fx0 = -21, fx1 = 21, fz0 = -14, fz1 = 11;
    const fenceRun = (ax, az, bx2, bz2) => {
      const len = Math.hypot(bx2 - ax, bz2 - az), n = Math.max(1, Math.round(len / 3.5)), ry = Math.atan2(-(bz2 - az), bx2 - ax);
      for (let i = 0; i <= n; i += 1) f.cyl(M.aluDark, 0.045, 2.1, lerp(ax, bx2, i / n), 1.05, lerp(az, bz2, i / n), 0, 0, 0, 8);
      for (let i = 0; i < n; i += 1) {
        const seg = len / n, p = new THREE.PlaneGeometry(seg, 1.9);
        const uv = p.attributes.uv;
        for (let k = 0; k < uv.count; k += 1) uv.setXY(k, uv.getX(k) * (seg / 0.1), uv.getY(k) * (1.9 / 0.1));
        f.geo(fenceMat, p, lerp(ax, bx2, (i + 0.5) / n), 1.0, lerp(az, bz2, (i + 0.5) / n), 0, ry, 0);
      }
      f.rod(M.aluDark, 0.025, new THREE.Vector3(ax, 2.0, az), new THREE.Vector3(bx2, 2.0, bz2), 6);
    };
    fenceRun(fx0, fz0, fx1, fz0); fenceRun(fx1, fz0, fx1, fz1); fenceRun(fx1, fz1, fx0, fz1); fenceRun(fx0, fz1, fx0, fz0);
    const bermMat = std(0xa9b1b9, 1.0, 0, { flatShading: true });
    for (let i = 0; i < 14; i += 1) {
      const a = (i / 14) * Math.PI * 2 + 0.2 * Math.sin(i * 7.3);
      const r = 82 + 22 * ((i * 37) % 10) / 10;
      const geo = new THREE.SphereGeometry(1, 14, 7, 0, Math.PI * 2, 0, Math.PI / 2);
      geo.scale(26 + ((i * 13) % 9) * 3, 7 + ((i * 7) % 6) * 1.6, 13 + ((i * 5) % 7) * 2);
      f.geo(bermMat, geo, Math.cos(a) * r, -0.5, Math.sin(a) * r, 0, -a + Math.PI / 2, 0);
    }
    f.build(g, { cast: false, receive: false });

    // --- tyre tracks: transparent tread ribbons on the gravel ---
    const trackTex = canvasTex(64, 128, (c, w, h) => {
      c.clearRect(0, 0, w, h);
      for (const [u0, u1] of [[4, 22], [42, 60]]) {
        c.fillStyle = 'rgba(70,75,82,0.28)'; c.fillRect(u0, 0, u1 - u0, h);
        c.fillStyle = 'rgba(55,60,66,0.35)';
        for (let y = 0; y < h; y += 16) { c.beginPath(); c.moveTo(u0, y); c.lineTo(u1, y + 8); c.lineTo(u1, y + 13); c.lineTo(u0, y + 5); c.fill(); }
      }
    });
    const trackMat = new THREE.MeshStandardMaterial({ map: trackTex, transparent: true, depthWrite: false, roughness: 1, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
    const track = (pts, width) => {
      const curve = new THREE.CatmullRomCurve3(pts.map(([x, z]) => new THREE.Vector3(x, -0.024, z)));
      const N = 80, pos = [], uv = [], idx = [];
      let dist = 0, prev = curve.getPoint(0);
      for (let i = 0; i <= N; i += 1) {
        const p = curve.getPoint(i / N), tg = curve.getTangent(i / N);
        dist += p.distanceTo(prev); prev = p;
        const nx = -tg.z, nz = tg.x, l = Math.hypot(nx, nz) || 1;
        pos.push(p.x + (nx / l) * width / 2, p.y, p.z + (nz / l) * width / 2, p.x - (nx / l) * width / 2, p.y, p.z - (nz / l) * width / 2);
        uv.push(0, dist / 1.6, 1, dist / 1.6);
        if (i < N) { const a = i * 2; idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3); }
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
      geo.setIndex(idx);
      geo.computeVertexNormals();
      const m = new THREE.Mesh(geo, trackMat);
      m.receiveShadow = true;
      m.renderOrder = 1;
      g.add(m);
    };
    track([[19, 7.5], [15.5, 5.2], [12.9, 3.4], [11.2, 1.6], [10.4, -0.6]], 2.4);
    track([[16.5, -6.0], [14.6, -1.5], [13.8, 2.0], [15.5, 6.0], [18.5, 9.0]], 2.4);
    track([[-20.5, -8.4], [-17.5, -7.8], [-15.6, -6.9], [-11.5, -4.4], [-8.6, -3.6]], 3.2);

    // --- wheel loader working the stockpile: drives in, scoops, backs out (animated in the loop) ---
    const loader = new THREE.Group();
    const L = builder();
    L.box(M.yellow, 2.1, 1.25, 2.3, 0, 1.3, -1.35);
    L.box(M.ink, 1.9, 0.06, 1.5, 0, 1.95, -1.5);
    L.box(M.ink, 2.2, 0.55, 0.45, 0, 0.95, -2.6);
    L.box(M.yellow, 1.7, 0.5, 1.9, 0, 1.15, 0.6);
    L.box(glass, 1.45, 1.25, 1.45, 0, 2.55, -0.35);
    L.box(M.yellow, 1.6, 0.12, 1.6, 0, 3.24, -0.35);
    for (const s of [-1, 1]) for (const z of [-1.05, 0.35]) L.box(M.yellow, 0.08, 1.25, 0.08, s * 0.72, 2.55, z);
    for (const s of [-1, 1]) for (const z of [-1.45, 1.05]) {
      L.cyl(M.ink, 0.78, 0.6, s * 1.18, 0.78, z, 0, 0, Math.PI / 2, 24);
      L.cyl(M.yellow, 0.42, 0.62, s * 1.18, 0.78, z, 0, 0, Math.PI / 2, 20);
    }
    L.cyl(M.ink, 0.08, 0.9, 0.55, 2.4, -1.9, 0, 0, 0, 10);
    L.build(loader);
    const arm = new THREE.Group();
    arm.position.set(0, 1.62, 0.45);
    const A = builder();
    for (const s of [-1, 1]) A.box(M.yellow, 0.18, 0.28, 2.3, s * 0.62, -0.17, 1.1, 0.32, 0, 0);
    A.box(M.weight, 2.7, 0.95, 0.1, 0, -1.0, 2.1);
    A.box(M.weight, 2.7, 0.08, 0.95, 0, -1.44, 2.55);
    for (const s of [-1, 1]) A.box(M.weight, 0.08, 0.95, 0.95, s * 1.31, -1.0, 2.55);
    A.box(M.alu, 2.72, 0.05, 0.12, 0, -1.46, 3.03);
    A.build(arm);
    loader.add(arm);
    const beaconMat = std(C.amber, 0.4, 0, { emissive: new THREE.Color(C.amber), emissiveIntensity: 0.6 });
    const beacon = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.12, 12), beaconMat);
    beacon.position.set(0.45, 3.36, -0.6);
    loader.add(beacon);
    loader.scale.setScalar(0.85);
    loader.position.set(12.9, 0, 3.2);
    loader.rotation.y = -Math.PI / 2 - 0.25;
    loaderRig = { root: loader, arm, beaconMat, base: loader.position.clone(), fwd: new THREE.Vector3(Math.sin(loader.rotation.y), 0, Math.cos(loader.rotation.y)) };
    g.add(loader);
  }

  // light dust drifting off the discharge while the belt runs (soft sprites, per-particle fade)
  const DUST_N = 80;
  const dustPos = new Float32Array(DUST_N * 3);
  const dustCol = new Float32Array(DUST_N * 4);
  const dustS = Array.from({ length: DUST_N }, () => ({ age: 9, life: 1, x: 0, y: -10, z: 0, vx: 0, vy: 0, vz: 0 }));
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
  dustGeo.setAttribute('color', new THREE.BufferAttribute(dustCol, 4));
  const dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({
    map: canvasTex(64, 64, (c) => { const gr = c.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, 'rgba(255,255,255,0.85)'); gr.addColorStop(1, 'rgba(255,255,255,0)'); c.fillStyle = gr; c.fillRect(0, 0, 64, 64); }, { repeat: false }),
    color: 0xb3b9c0, size: 0.55, sizeAttenuation: true, transparent: true, depthWrite: false, vertexColors: true,
  }));
  dust.frustumCulled = false;
  scene.add(dust);
  let dustClock = 0, exClock = 0;
  const EXH = new THREE.Vector3(-12.87, 3.67, -5.19); // haul truck exhaust stack
  function updateDust(dt, run) {
    dustClock -= dt;
    exClock -= dt;
    for (let i = 0; i < DUST_N; i += 1) {
      const p = dustS[i];
      p.age += dt;
      const exhaust = i >= 60;
      if (exhaust && p.age >= p.life && revealT > 1.5 && exClock <= 0) {
        exClock = 0.35;
        p.age = 0; p.life = 2.2 + Math.random() * 1.2;
        p.x = EXH.x + (Math.random() - 0.5) * 0.1; p.y = EXH.y; p.z = EXH.z + (Math.random() - 0.5) * 0.1;
        p.vx = 0.08 + Math.random() * 0.1; p.vy = 0.45 + Math.random() * 0.2; p.vz = (Math.random() - 0.5) * 0.12;
      } else if (!exhaust && p.age >= p.life && run > 0.3 && revealT > 1.5 && dustClock <= 0) {
        dustClock = 0.05;
        p.age = 0; p.life = 1.6 + Math.random() * 1.8;
        p.x = X1 + 0.45 + Math.random() * 0.5; p.y = 0.7 + Math.random() * 0.45; p.z = (Math.random() - 0.5) * 0.9;
        p.vx = 0.15 + Math.random() * 0.35; p.vy = 0.1 + Math.random() * 0.22; p.vz = (Math.random() - 0.5) * 0.35;
      }
      const alive = p.age < p.life;
      if (alive) { p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt; }
      const q = alive ? p.age / p.life : 1;
      dustPos[i * 3] = p.x; dustPos[i * 3 + 1] = alive ? p.y : -10; dustPos[i * 3 + 2] = p.z;
      dustCol.set(exhaust ? [0.55, 0.57, 0.6, alive ? Math.sin(q * Math.PI) * 0.35 : 0] : [1, 1, 1, alive ? Math.sin(q * Math.PI) * 0.5 : 0], i * 4);
    }
    dustGeo.attributes.position.needsUpdate = true;
    dustGeo.attributes.color.needsUpdate = true;
  }

  // soft contact shadows under machines and people (cheap radial decals that ground the props)
  const blobMat = new THREE.MeshBasicMaterial({
    map: canvasTex(64, 64, (c) => { const gr = c.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, 'rgba(0,0,0,0.6)'); gr.addColorStop(0.55, 'rgba(0,0,0,0.28)'); gr.addColorStop(1, 'rgba(0,0,0,0)'); c.fillStyle = gr; c.fillRect(0, 0, 64, 64); }, { repeat: false }),
    transparent: true, depthWrite: false, opacity: 0.6, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  const blobGeo = new THREE.PlaneGeometry(1, 1);
  const blob = (x, z, w2, l2, ry = 0, y = -0.02) => {
    const m = new THREE.Mesh(blobGeo, blobMat);
    m.rotation.set(-Math.PI / 2, 0, ry);
    m.position.set(x, y, z);
    m.scale.set(w2, l2, 1);
    m.renderOrder = 1;
    scene.add(m);
    return m;
  };
  blob(-15.2, -6.8, 4.6, 7.6, 0.55);
  blob(7.9, -9.5, 2.4, 5.6, Math.PI / 2 + 0.15);
  blob(-12.6, 0.8, 3.0, 2.0, -0.1);
  blob(12.4, -3.8, 3.0, 2.0, Math.PI + 0.35);
  blob(-11.9, -5.2, 1.7, 1.7);
  blob(-12.9, -6.9, 1.5, 1.6);
  blob(-7.6, -5.5, 0.9, 0.9);
  const techBlob = blob(0, 0, 0.85, 0.85, 0, 0.003);
  const loaderBlob = blob(12.9, 3.2, 3.0, 5.6, -Math.PI / 2 - 0.25);
  function updateLoader(t2) {
    if (!loaderRig) return;
    const ph = (t2 % 13) / 13; // drive in, scoop, back out, lower + wait
    let u = 0, armA = 0;
    if (ph < 0.32) { u = smooth(ph / 0.32); armA = 0; }
    else if (ph < 0.45) { u = 1; armA = -0.55 * smooth((ph - 0.32) / 0.13); }
    else if (ph < 0.77) { u = 1 - smooth((ph - 0.45) / 0.32); armA = -0.55; }
    else { u = 0; armA = -0.55 * (1 - smooth(clamp((ph - 0.77) / 0.12, 0, 1))); }
    loaderRig.root.position.copy(loaderRig.base).addScaledVector(loaderRig.fwd, 2.1 * u);
    loaderRig.arm.rotation.x = armA;
    loaderRig.beaconMat.emissiveIntensity = Math.sin(t2 * 7) > 0 ? 1.1 : 0.2;
    loaderBlob.position.set(loaderRig.root.position.x, -0.02, loaderRig.root.position.z);
  }

  // ---------- stations: thin floor rings + small numbered badges (screen-space, with a thin leader to the ring) ----------
  const stationRoot = part('stations', 1.3);
  const stationObjs = new Map();
  const hitMeshes = [];
  const ringGeo = new THREE.RingGeometry(0.44, 0.475, 72);
  const haloGeo = new THREE.RingGeometry(0.46, 0.5, 72);
  const padGeo = new THREE.CircleGeometry(0.44, 56);
  const hitGeo = new THREE.BoxGeometry(1.9, 2.8, 4.7);
  const hitMat = new THREE.MeshBasicMaterial({ visible: false });
  let hoverId = null;
  function ensureStation(st) {
    let o = stationObjs.get(st.id);
    if (o) return o;
    const g = new THREE.Group();
    stationRoot.add(g);
    const ringMat = new THREE.MeshBasicMaterial({ color: C.zinc300, toneMapped: false, transparent: true, opacity: 1, depthWrite: false });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.014;
    ring.renderOrder = 2;
    const pad = new THREE.Mesh(padGeo, new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false, transparent: true, opacity: 0.85, depthWrite: false }));
    pad.rotation.x = -Math.PI / 2;
    pad.position.y = 0.012;
    pad.renderOrder = 1;
    const halo = new THREE.Mesh(haloGeo, new THREE.MeshBasicMaterial({ color: C.red, toneMapped: false, transparent: true, opacity: 0, depthWrite: false }));
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = 0.013;
    halo.renderOrder = 2;
    halo.visible = false;
    const hit = new THREE.Mesh(hitGeo, hitMat);
    hit.position.set(0, 1.4, 0.55 - Z_WALK);
    hit.userData.stationId = st.id;
    g.add(ring, pad, halo, hit);
    hitMeshes.push(hit);
    const el = document.createElement('div');
    el.className = 'bg3d-badge';
    el.style.display = 'none';
    const leader = document.createElement('i');
    leader.className = 'bg3d-leader';
    leader.style.display = 'none';
    labelLayer.append(leader, el);
    el.addEventListener('pointerdown', (e) => e.stopPropagation());
    el.addEventListener('click', (e) => { e.stopPropagation(); if (onStationClick) onStationClick(st.id); });
    el.addEventListener('pointerenter', () => { hoverId = st.id; });
    el.addEventListener('pointerleave', () => { if (hoverId === st.id) hoverId = null; });
    o = { id: st.id, g, ring, ringMat, pad, halo, hit, el, leader, sel: false, defect: false, insp: false, pri: 2, box: null };
    stationObjs.set(st.id, o);
    return o;
  }
  function updateStations(t) {
    const pulse = 0.5 + 0.5 * Math.sin(t * 4.2);
    for (const o of stationObjs.values()) {
      const hov = hoverId === o.id;
      const col = o.sel ? C.zinc900 : o.defect ? C.red : o.insp ? C.green : hov ? C.zinc500 : C.zinc300;
      o.ringMat.color.setHex(col);
      const sc = o.defect ? 1 + 0.05 * pulse : o.sel ? 1.04 : 1;
      o.ring.scale.set(sc, sc, 1);
      o.ringMat.opacity = o.defect ? 0.65 + 0.35 * pulse : 1;
      o.halo.visible = o.defect;
      if (o.defect) {
        const q = (t * 0.7) % 1;
        o.halo.scale.set(1 + q * 0.7, 1 + q * 0.7, 1);
        o.halo.material.opacity = 0.55 * (1 - q);
      }
    }
  }

  // badges: anchored on the ring, dropped a fixed 30 px below it (clear of the worker) with a thin leader; clamped 6 px inside the stage;
  // a lower-priority badge whose rect would touch another is hidden (checked every frame)
  const BADGE = 24, LIFT = 30, EDGE = 6;
  let stageW = W(), stageH = H();
  const _lp = new THREE.Vector3();
  const chainVisible = (o) => { for (let p = o; p; p = p.parent) if (!p.visible) return false; return true; };
  function placeLabels() {
    const list = [];
    for (const o of stationObjs.values()) {
      o.box = null;
      if (!labelsIn || !chainVisible(o.g)) continue;
      _lp.setFromMatrixPosition(o.g.matrixWorld).project(camera);
      if (_lp.z < -1 || _lp.z > 1) continue;
      const ax = (_lp.x * 0.5 + 0.5) * stageW, ay = (-_lp.y * 0.5 + 0.5) * stageH;
      if (ax < EDGE || ax > stageW - EDGE || ay < EDGE || ay > stageH - EDGE) continue;
      const l = clamp(ax - BADGE / 2, EDGE, stageW - EDGE - BADGE);
      const tp = clamp(ay + LIFT, EDGE, stageH - EDGE - BADGE);
      o.box = { l, t: tp, r: l + BADGE, b: tp + BADGE, ax, ay };
      list.push({ it: o, box: o.box, pri: o.id === hoverId ? 11 : o.pri });
    }
    for (const wb of workerBadges.values()) { // "W-03 · 82 pts" floats above each crew member's head
      wb.box = null;
      if (!labelsIn || !workerVisible(wb.id) || !workerPos(wb.id, _lp)) continue;
      _lp.y = 2.12;
      _lp.project(camera);
      if (_lp.z < -1 || _lp.z > 1) continue;
      const ax = (_lp.x * 0.5 + 0.5) * stageW, ay = (-_lp.y * 0.5 + 0.5) * stageH;
      if (ax < EDGE || ax > stageW - EDGE || ay < EDGE || ay > stageH - EDGE) continue;
      const l = clamp(ax - wb.w / 2, EDGE, stageW - EDGE - wb.w);
      const tp = clamp(ay - wb.h - 4, EDGE, stageH - EDGE - wb.h);
      wb.box = { l, t: tp, r: l + wb.w, b: tp + wb.h };
      list.push({ it: wb, box: wb.box, pri: wb.id === focusWorkerId ? 12 : wb.v === 'limit' ? 7 : wb.tool ? 6 : 5 });
    }
    list.sort((a, b) => b.pri - a.pri);
    const kept = [];
    for (const e of list) {
      const bx = e.box;
      if (kept.some((k) => bx.l < k.r + 4 && bx.r > k.l - 4 && bx.t < k.b + 4 && bx.b > k.t - 4)) { e.it.box = null; continue; }
      kept.push(bx);
    }
    for (const o of stationObjs.values()) {
      const bx = o.box;
      if (!bx) { o.el.style.display = 'none'; o.leader.style.display = 'none'; continue; }
      o.el.style.display = '';
      o.el.style.transform = `translate(${bx.l.toFixed(1)}px,${bx.t.toFixed(1)}px)`;
      const x2 = bx.l + BADGE / 2, y2 = bx.t - 2;
      const dx = x2 - bx.ax, dy = y2 - bx.ay, len = Math.hypot(dx, dy);
      if (len < 4) { o.leader.style.display = 'none'; continue; }
      o.leader.style.display = '';
      o.leader.style.height = `${len.toFixed(1)}px`;
      o.leader.style.transform = `translate(${bx.ax.toFixed(1)}px,${bx.ay.toFixed(1)}px) rotate(${Math.atan2(-dx, dy).toFixed(4)}rad)`;
    }
    for (const wb of workerBadges.values()) {
      if (!wb.box) { wb.el.style.display = 'none'; continue; }
      wb.el.style.display = '';
      wb.el.style.transform = `translate(${wb.box.l.toFixed(1)}px,${wb.box.t.toFixed(1)}px)`;
    }
  }

  // ---------- technician: Soldier.glb (rigged human, Idle/Walk) in a hi-vis vest + white hard hat; simple figure as fallback ----------
  const techRoot = part('tech', 1.45);
  const techBody = new THREE.Group();
  techRoot.add(techBody);
  function buildTech() {
    const T = {
      vest: std(C.tool, 0.6), tape: std(0xeef0f3, 0.3, 0.5), shirt: std(0x3f4650, 0.8),
      trousers: std(0x2b3442, 0.85), boots: std(C.ink, 0.7), skin: std(0xd9b99b, 0.7),
      hand: std(0xd9b99b, 0.7), helmet: std(0xffffff, 0.35), band: std(C.ink, 0.4, 0.2),
    };
    const g = new THREE.Group();
    const add = (geo, mat, x, y, z, parent = g) => {
      const m = new THREE.Mesh(geo, mat);
      m.position.set(x, y, z);
      m.castShadow = true;
      parent.add(m);
      return m;
    };
    const pivot = (x, y, z, parent = g) => { const p = new THREE.Group(); p.position.set(x, y, z); parent.add(p); return p; };
    const hipL = pivot(-0.1, 0.92, 0), hipR = pivot(0.1, 0.92, 0);
    for (const hip of [hipL, hipR]) {
      add(new THREE.CapsuleGeometry(0.075, 0.66, 4, 10), T.trousers, 0, -0.43, 0, hip);
      add(new THREE.BoxGeometry(0.14, 0.09, 0.26), T.boots, 0, -0.875, 0.04, hip);
    }
    const torso = pivot(0, 0, 0);
    torso.scale.set(1, 1, 0.74);
    add(new THREE.CapsuleGeometry(0.2, 0.42, 6, 18), T.vest, 0, 1.2, 0, torso);
    for (const y of [1.07, 1.27]) add(new THREE.CylinderGeometry(0.204, 0.204, 0.045, 24, 1, true), T.tape, 0, y, 0, torso);
    add(new THREE.CylinderGeometry(0.05, 0.055, 0.1, 12), T.skin, 0, 1.64, 0);
    add(new THREE.SphereGeometry(0.11, 20, 16), T.skin, 0, 1.76, 0);
    add(new THREE.SphereGeometry(0.128, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), T.helmet, 0, 1.79, 0);
    add(new THREE.CylinderGeometry(0.16, 0.16, 0.014, 28), T.helmet, 0, 1.79, 0.015);
    add(new THREE.BoxGeometry(0.03, 0.03, 0.22), T.helmet, 0, 1.915, 0);
    const shL = pivot(-0.265, 1.52, 0), shR = pivot(0.265, 1.52, 0);
    const elL = pivot(0, -0.3, 0, shL), elR = pivot(0, -0.3, 0, shR);
    for (const sh of [shL, shR]) add(new THREE.CapsuleGeometry(0.062, 0.2, 4, 10), T.shirt, 0, -0.15, 0, sh);
    for (const el of [elL, elR]) {
      add(new THREE.CapsuleGeometry(0.054, 0.2, 4, 10), T.shirt, 0, -0.13, 0, el);
      add(new THREE.SphereGeometry(0.058, 12, 10), T.hand, 0, -0.29, 0, el);
    }
    const band = add(new THREE.TorusGeometry(0.056, 0.02, 8, 20), T.band, 0, -0.225, 0, elR);
    band.rotation.x = Math.PI / 2;
    add(new THREE.BoxGeometry(0.03, 0.045, 0.06), T.band, 0.065, -0.225, 0, elR);
    const grip = pivot(0, -0.33, 0.04, elR);
    return { g, T, hipL, hipR, shL, shR, elL, elR, grip };
  }
  const tech = buildTech();
  techBody.add(tech.g);

  // the rig ships one textured mesh, so the hi-vis vest (+ two reflective bands) is painted in the shader from the
  // bind-pose position (cm, z up): torso band 99-149 cm, |x| < 19.5 cm
  function hiVisMaterial(orig, vestHex = C.tool) {
    const m = orig.clone();
    m.onBeforeCompile = (sh) => {
      sh.uniforms.uVest = { value: new THREE.Color(vestHex) };
      sh.uniforms.uTape = { value: new THREE.Color(0xe9edf2) };
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\nvarying vec3 vBindPos;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\nvBindPos = position;');
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\nvarying vec3 vBindPos;\nuniform vec3 uVest;\nuniform vec3 uTape;')
        .replace('#include <map_fragment>', `#include <map_fragment>
          float bgH = vBindPos.z, bgX = abs(vBindPos.x);
          float bgVest = step(99.0, bgH) * step(bgH, 149.0) * step(bgX, 19.5);
          float bgTape = bgVest * max(step(111.0, bgH) * step(bgH, 116.0), step(127.0, bgH) * step(bgH, 132.0));
          float bgLum = dot(diffuseColor.rgb, vec3(0.299, 0.587, 0.114));
          vec3 bgSuit = vec3(0.07, 0.085, 0.12) * (0.6 + 1.2 * bgLum);
          bgSuit = mix(bgSuit, vec3(0.035, 0.035, 0.04) * (0.7 + bgLum), step(bgH, 13.0));
          diffuseColor.rgb = mix(bgSuit, uVest, bgVest);
          diffuseColor.rgb = mix(diffuseColor.rgb, uTape, bgTape);`);
    };
    m.customProgramCacheKey = () => 'bg3d-hivis';
    return m;
  }
  let soldier = null;
  let soldierBaseMat = null;
  let soldierFailed = false;
  let worker2 = null;
  let disposed = false;
  const hatMat = std(0xffffff, 0.35);
  function setupSoldier(gltf) {
    const model = gltf.scene;
    const body = model.getObjectByName('vanguard_Mesh');
    const visor = model.getObjectByName('vanguard_visor');
    if (visor) visor.visible = false;
    model.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; } });
    soldierBaseMat = body ? body.material : null;
    if (body && body.material) body.material = hiVisMaterial(body.material);
    const bb = new THREE.Box3().setFromObject(model);
    const h = bb.max.y - bb.min.y;
    model.scale.setScalar(1.75 / (h > 0.5 ? h : 1.83));
    model.rotation.y = Math.PI; // the rig faces -z; the worker group faces +z
    techBody.add(model);
    techBody.updateMatrixWorld(true);
    const find = (name, re) => {
      let f = model.getObjectByName(name);
      if (!f) model.traverse((o) => { if (!f && o.isBone && re.test(o.name)) f = o; });
      return f;
    };
    const bones = {
      head: find('mixamorigHead', /Head$/),
      rArm: find('mixamorigRightArm', /RightArm$/), rFore: find('mixamorigRightForeArm', /RightForeArm$/), rHand: find('mixamorigRightHand', /RightHand$/),
      lArm: find('mixamorigLeftArm', /LeftArm$/), lFore: find('mixamorigLeftForeArm', /LeftForeArm$/),
    };
    if (bones.head) { // white hard hat: socket cancels the rig's 0.01 scale so offsets are metres
      const sock = new THREE.Group();
      bones.head.add(sock);
      const ws = new THREE.Vector3();
      bones.head.getWorldScale(ws);
      sock.scale.setScalar(1 / (ws.x || 1));
      const hat = new THREE.Group();
      const dome = new THREE.Mesh(new THREE.SphereGeometry(0.138, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), hatMat);
      const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.168, 0.168, 0.014, 28), hatMat);
      brim.position.z = 0.02;
      const ridge = new THREE.Mesh(new THREE.BoxGeometry(0.032, 0.03, 0.24), hatMat);
      ridge.position.y = 0.13;
      for (const m of [dome, brim, ridge]) { m.castShadow = true; hat.add(m); }
      hat.position.set(0, 0.155, 0.012);
      sock.add(hat);
    }
    if (bones.rHand) { // Vexo band on the right wrist (socket cancels the rig's 0.01 scale, offsets in metres)
      const strap = new THREE.TorusGeometry(0.045, 0.012, 8, 28);
      strap.rotateX(Math.PI / 2);
      strap.scale(1.12, 1.6, 0.9);
      const pod = new THREE.BoxGeometry(0.034, 0.03, 0.022);
      pod.translate(0, 0, 0.0525);
      const bandGeo = mergeGeometries([strap.toNonIndexed(), pod.toNonIndexed()], false);
      const sock = new THREE.Group();
      bones.rHand.add(sock);
      const ws = new THREE.Vector3();
      bones.rHand.getWorldScale(ws);
      sock.scale.setScalar(1 / (ws.x || 1));
      const band = new THREE.Mesh(bandGeo, std(0x15171a, 0.4, 0.2));
      band.position.y = -0.012;
      band.castShadow = true;
      sock.add(band);
    }
    const mixer = new THREE.AnimationMixer(model);
    const clip = (re, i) => gltf.animations.find((a) => re.test(a.name)) || gltf.animations[i];
    const idle = mixer.clipAction(clip(/idle/i, 0));
    const walk = mixer.clipAction(clip(/walk/i, 3));
    idle.play();
    walk.play();
    walk.setEffectiveWeight(0);
    const run = mixer.clipAction(clip(/run/i, 1));
    run.play();
    run.setEffectiveWeight(0);
    soldier = { model, mixer, idle, walk, run, bones, clips: { idle: clip(/idle/i, 0), walk: clip(/walk/i, 3) } };
    try { // a second worker in hi-vis idling by the workshop door
      const m2 = cloneSkinned(model);
      const holder = new THREE.Group();
      holder.add(m2);
      holder.position.set(-7.6, 0, -5.5);
      holder.rotation.y = 0.65;
      scene.add(holder);
      const mixer2 = new THREE.AnimationMixer(m2);
      const a2 = mixer2.clipAction(clip(/idle/i, 0));
      a2.play();
      a2.time = 0.9;
      worker2 = { holder, mixer: mixer2 };
    } catch (err) { console.warn('[model3d] second worker skipped', err); }
    if (fleetData.length) syncFleet(); // the crew may have arrived before the model
    tech.g.visible = false;
  }
  // a vendored copy can be used offline: createModel(..., { soldierUrl }) or window.BELTGUARD_SOLDIER_URL; default = three.js CDN
  const soldierSrc = soldierUrl || (typeof window !== 'undefined' && window.BELTGUARD_SOLDIER_URL) || SOLDIER_URL;
  new GLTFLoader().load(soldierSrc, (gltf) => {
    if (disposed) return;
    try { setupSoldier(gltf); } catch (err) { soldierFailed = true; console.warn('[model3d] worker model setup failed; keeping the simple figure', err); if (fleetData.length) syncFleet(); }
  }, undefined, (err) => { soldierFailed = true; console.warn('[model3d] worker model unavailable; keeping the simple figure', err); if (fleetData.length) syncFleet(); });

  // exposure dial on the floor around the technician's feet (0..400 pts) + tint disc
  const dialTrack = new THREE.Mesh(new THREE.RingGeometry(0.46, 0.5, 72), new THREE.MeshBasicMaterial({ color: 0xe4e4e7, toneMapped: false, transparent: true, opacity: 0.9, depthWrite: false }));
  const dialArcMat = new THREE.MeshBasicMaterial({ color: C.zinc500, toneMapped: false, transparent: true, opacity: 1, depthWrite: false, side: THREE.DoubleSide });
  const dialArc = new THREE.Mesh(new THREE.RingGeometry(0.455, 0.505, 72, 1, Math.PI / 2, 0.001), dialArcMat);
  const tintDisc = new THREE.Mesh(new THREE.CircleGeometry(0.455, 56), new THREE.MeshBasicMaterial({ color: C.amber, toneMapped: false, transparent: true, opacity: 0, depthWrite: false }));
  for (const [m, y, ro] of [[dialTrack, 0.016, 3], [dialArc, 0.017, 4], [tintDisc, 0.015, 3]]) {
    m.rotation.x = -Math.PI / 2;
    m.position.y = y;
    m.renderOrder = ro;
    techRoot.add(m);
  }

  const techPos = new THREE.Vector3(X0 - 0.4, 0, Z_WALK);
  const techVel = new THREE.Vector3();
  let techTargetX = sx(POS_DEFAULT.S1);
  let workX = null; // x of the station being worked (selected, else the defect station while a tool is out)
  let heading = Math.PI / 2;
  let walkPhase = 0;
  let actState = 'idle';
  let toolActive = false;
  let vibRms = 0;
  let exposurePts = 0;
  let exposureStatus = 'ok';
  let dialF = -1;
  let techPlaced = false;
  let workK = 0;
  const isWorking = () => toolActive || actState === 'tool_active' || actState === 'candidate';
  const exposureColor = () => (exposureStatus === 'above_limit' ? C.red : exposureStatus === 'above_action' ? C.amber : C.zinc500);

  function updateExposure() {
    const f = clamp(exposurePts / 400, 0, 1);
    const col = exposureColor();
    dialArcMat.color.setHex(col);
    if (Math.abs(f - dialF) > 0.003) {
      dialF = f;
      dialArc.geometry.dispose();
      dialArc.geometry = new THREE.RingGeometry(0.455, 0.505, 72, 1, Math.PI / 2, Math.max(0.001, -Math.PI * 2 * f));
    }
    const warn = exposureStatus === 'above_action' || exposureStatus === 'above_limit';
    tintDisc.material.color.setHex(col);
    tintDisc.material.opacity = exposureStatus === 'above_limit' ? 0.22 : warn ? 0.14 : 0;
    tech.T.hand.emissive.setHex(warn ? col : 0x000000);
    tech.T.hand.emissiveIntensity = exposureStatus === 'above_limit' ? 0.6 : warn ? 0.3 : 0;
  }

  // world-space bone aim (independent of the rig's bone roll): rotate `bone` so its +Y points along `dir`
  const _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion(), _qc = new THREE.Quaternion(), QI = new THREE.Quaternion();
  const _va = new THREE.Vector3(), _vb = new THREE.Vector3();
  function aimBone(bone, dir, w) {
    if (!bone || !bone.parent) return;
    bone.getWorldQuaternion(_qa);
    _va.set(0, 1, 0).applyQuaternion(_qa);
    _qb.setFromUnitVectors(_va, _vb.copy(dir).normalize());
    if (w < 1) _qb.slerp(QI, 1 - w);
    _qa.premultiply(_qb);
    bone.parent.getWorldQuaternion(_qc).invert();
    bone.quaternion.copy(_qc.multiply(_qa));
  }
  const _fw = new THREE.Vector3(), _rt = new THREE.Vector3(), _dn = new THREE.Vector3(0, -1, 0), _dir = new THREE.Vector3();
  function poseTool(w) { poseToolFor(soldier.bones, heading, w, toolKindNow === 'breaker'); }
  function poseToolFor(B, hd, w, low) { // two-handed hold in front of the chest (lower for the breaker)
    _fw.set(Math.sin(hd), 0, Math.cos(hd));
    _rt.set(-Math.cos(hd), 0, Math.sin(hd));
    aimBone(B.rArm, _dir.copy(_fw).multiplyScalar(0.5).addScaledVector(_dn, low ? 0.95 : 0.8).addScaledVector(_rt, 0.12), w);
    aimBone(B.rFore, _dir.copy(_fw).multiplyScalar(0.95).addScaledVector(_dn, low ? 0.45 : 0.1).addScaledVector(_rt, -0.25), w);
    aimBone(B.lArm, _dir.copy(_fw).multiplyScalar(0.5).addScaledVector(_dn, low ? 0.95 : 0.8).addScaledVector(_rt, -0.12), w);
    aimBone(B.lFore, _dir.copy(_fw).multiplyScalar(0.9).addScaledVector(_dn, low ? 0.45 : 0.15).addScaledVector(_rt, 0.4), w);
  }

  // the checked-out tool rides in the right hand; it jitters while a tool session is active
  const handTool = new THREE.Group();
  handTool.rotation.order = 'YXZ';
  handTool.visible = false;
  scene.add(handTool);
  const handTools = {};
  let toolKindNow = null;
  let handPop = 1;
  const _h = new THREE.Vector3(), _e = new THREE.Vector3();
  let handHidden = false;
  function setActiveTool(kind, { glow = true, pop = true } = {}) {
    if (kind === toolKindNow) return;
    const prev = slotOf(toolKindNow);
    if (prev) prev.tool.visible = true;
    toolKindNow = kind;
    handTool.clear();
    if (!kind) return;
    if (!handTools[kind]) handTools[kind] = makeTool(kind);
    handTool.add(handTools[kind]);
    handPop = pop ? 0 : 1;
    const slot = slotOf(kind);
    if (slot) { slot.tool.visible = false; if (glow && revealT > 1.0) slot.glow = 1.8; }
  }
  function palmWorld(out) {
    if (soldier && soldier.bones.rHand && soldier.bones.rFore) {
      soldier.bones.rHand.getWorldPosition(out);
      soldier.bones.rFore.getWorldPosition(_e);
      return out.addScaledVector(_e.subVectors(out, _e).normalize(), 0.075);
    }
    return tech.grip.getWorldPosition(out);
  }
  function placeHandTool(dt, working) {
    if (!toolKindNow || revealT < 1.6 || handHidden) { handTool.visible = false; return; }
    handTool.visible = true;
    palmWorld(_h);
    handTool.position.copy(_h);
    const breaker = toolKindNow === 'breaker';
    handTool.rotation.set(breaker ? lerp(-1.1, 0, workK) : lerp(0, 0.12, workK), heading, 0);
    if (working) {
      const a = clamp(vibRms / 0.35, 0.3, 1.4) * workK;
      handTool.rotation.x += (Math.random() - 0.5) * 0.09 * a;
      handTool.rotation.z = (Math.random() - 0.5) * 0.09 * a;
      handTool.position.x += (Math.random() - 0.5) * 0.01 * a;
      handTool.position.y += (Math.random() - 0.5) * 0.01 * a;
      handTool.position.z += (Math.random() - 0.5) * 0.01 * a;
    }
    handPop = Math.min(1, handPop + dt * 4);
    handTool.scale.setScalar(1.15 * (0.2 + 0.8 * ease3(handPop)));
  }

  // ---------- tool-checkout choreography (driven by the page: walkToCrib, pickTool, walkToStation, returnTool) ----------
  let route = [], routeFace = Math.PI, routeStation = false, routeDone = null;
  let hold = null, holdActive = null;
  let reach = null, flyer = null, mover = null, faceT = 0;
  let scriptBusy = 0, scriptEndAt = -1e9;
  let lastDesired = null, desiredAt = 0;
  let queue = Promise.resolve();
  const flyers = {};
  const angDiff = (a, b) => Math.atan2(Math.sin(a - b), Math.cos(a - b));
  const _sh = new THREE.Vector3(), _qh = new THREE.Quaternion(), _eh = new THREE.Euler(0, 0, 0, 'YXZ');
  function poseReach(k) {
    const B = soldier.bones;
    if (!B.rArm || !reach) return;
    B.rArm.getWorldPosition(_sh);
    _dir.subVectors(reach.target, _sh);
    aimBone(B.rArm, _dir, k);
    aimBone(B.rFore, _dir, k);
  }
  // demo pacing: every scripted move finishes within maxT; brisk walk (about 3 m/s nominal), long paths blend faster
  function walkTo(pts, face, station, maxT = 2.2, waitFace = true) {
    return new Promise((resolve) => {
      if (routeDone) routeDone(false);
      const P = [{ x: techPos.x, z: techPos.z }, ...pts.map((q) => ({ x: q.x, z: q.z }))];
      const cum = [0];
      for (let i = 1; i < P.length; i += 1) cum.push(cum[i - 1] + Math.hypot(P[i].x - P[i - 1].x, P[i].z - P[i - 1].z));
      const L = cum[cum.length - 1];
      mover = { P, cum, L, T: L < 0.03 ? 0 : clamp(L / 3.0, 0.25, maxT), t: 0, face, station: !!station, early: !waitFace, resolve };
      route = P.slice(1);
      routeFace = face;
      routeStation = !!station;
      hold = null;
      faceT = 0;
      routeDone = waitFace ? resolve : null;
    });
  }
  const pathToCrib = (sx2) => [{ x: clamp(sx2, ISL_X - ISL_W / 2 + 0.3, ISL_X + ISL_W / 2 - 0.3), z: STAND_Z }];
  const pathToStation = (x) => [{ x, z: Z_WORK }];
  function flyerFor(kind) {
    if (!flyers[kind]) { flyers[kind] = new THREE.Group(); flyers[kind].add(makeTool(kind)); flyers[kind].visible = false; scene.add(flyers[kind]); }
    return flyers[kind];
  }
  function reachTo(slot, mode, kind) {
    return new Promise((resolve) => { reach = { slot, mode, kind, t: 0, k: 0, flying: false, landed: false, resolve, target: slot.world.clone() }; });
  }
  function startFly() {
    const r = reach;
    r.flying = true;
    flyer = flyerFor(r.kind);
    flyer.visible = true;
    _eh.set(0, heading, 0);
    _qh.setFromEuler(_eh);
    if (r.mode === 'pick') {
      r.slot.tool.visible = false;
      r.slot.glow = 1.8;
      r.qa = r.slot.quat.clone(); r.qb = _qh.clone(); r.sa = r.slot.scale; r.sb = 1.15;
    } else {
      handHidden = true;
      r.qa = _qh.clone(); r.qb = r.slot.quat.clone(); r.sa = 1.15; r.sb = r.slot.scale;
    }
  }
  function land() {
    const r = reach;
    r.landed = true;
    if (flyer) flyer.visible = false;
    if (r.mode === 'pick') {
      setActiveTool(r.kind, { glow: false, pop: false });
    } else {
      handHidden = false;
      toolKindNow = null;
      handTool.clear();
      r.slot.tool.visible = true;
    }
  }
  function updateReach(dt) {
    if (!reach) return;
    const r = reach;
    r.t += dt;
    const A = 0.25, Bd = 0.25, Cd = 0.25; // pick/return in 0.75 s
    if (r.t < A) r.k = smooth(r.t / A);
    else if (r.t < A + Bd) {
      r.k = 1;
      if (!r.flying) startFly();
      const u = smooth((r.t - A) / Bd);
      palmWorld(_h);
      if (r.mode === 'pick') flyer.position.lerpVectors(r.slot.world, _h, u);
      else flyer.position.lerpVectors(_h, r.slot.world, u);
      flyer.quaternion.slerpQuaternions(r.qa, r.qb, u);
      flyer.scale.setScalar(lerp(r.sa, r.sb, u));
    } else if (r.t < A + Bd + Cd) {
      if (!r.flying) startFly();
      if (!r.landed) land();
      r.k = 1 - smooth((r.t - A - Bd) / Cd);
    } else {
      if (!r.flying) startFly();
      if (!r.landed) land();
      reach = null;
      r.resolve(true);
    }
  }
  function snapScripts() { // timeout or dispose: finish whatever is in flight at its end state
    if (mover && mover.early) { const r = mover.resolve; mover.resolve = null; if (r) r(false); }
    mover = null;
    if (route.length) {
      const wp = route[route.length - 1];
      techPos.set(wp.x, 0, wp.z);
      techVel.set(0, 0, 0);
      heading = routeFace;
      hold = { x: wp.x, z: wp.z, face: routeFace, station: routeStation };
      holdActive = activeId;
      route = [];
    }
    if (routeDone) { const r = routeDone; routeDone = null; r(false); }
    if (reach) {
      if (!reach.flying) startFly();
      if (!reach.landed) land();
      const r = reach;
      reach = null;
      r.resolve(false);
    }
  }
  function enqueue(fn, timeoutMs = 15000) {
    const run = () => new Promise((resolve) => {
      scriptBusy += 1;
      let done = false;
      const finish = (v) => {
        if (done) return;
        done = true;
        scriptBusy = Math.max(0, scriptBusy - 1);
        scriptEndAt = performance.now();
        resolve(v);
      };
      const to = setTimeout(() => { snapScripts(); finish(false); }, timeoutMs);
      Promise.resolve().then(fn).then(
        (v) => { clearTimeout(to); finish(v !== false); },
        (err) => { clearTimeout(to); console.warn('[model3d] scripted move failed', err); snapScripts(); finish(false); },
      );
    });
    const pr = queue.then(run, run);
    queue = pr.catch(() => {});
    return pr;
  }

  function updateTech(dt, t) {
    const working = isWorking();
    const atCrib = techPos.z > 2.75;
    // tool out at the job: at the belt, tool held ready
    const ready = !working && !!toolKindNow && !atCrib && (hold ? hold.station : true);
    let face = Math.PI, speed = 0;
    if (mover) { // scripted move: time-bounded travel along the path
      mover.t += dt;
      const k = mover.T > 0 ? Math.min(1, mover.t / mover.T) : 1;
      const s = mover.L * (0.7 * k + 0.3 * smooth(k));
      let i = 1;
      while (i < mover.cum.length - 1 && mover.cum[i] < s) i += 1;
      const pa = mover.P[i - 1], pb = mover.P[i], segL = Math.max(1e-6, mover.cum[i] - mover.cum[i - 1]);
      const u = clamp((s - mover.cum[i - 1]) / segL, 0, 1);
      const nx = lerp(pa.x, pb.x, u), nz = lerp(pa.z, pb.z, u), idt = 1 / Math.max(dt, 1e-4);
      techVel.set((nx - techPos.x) * idt, 0, (nz - techPos.z) * idt);
      techPos.x = nx;
      techPos.z = nz;
      speed = Math.hypot(techVel.x, techVel.z);
      if (k >= 1) {
        hold = { x: nx, z: nz, face: mover.face, station: mover.station };
        holdActive = activeId;
        face = mover.face;
        const early = mover.early ? mover.resolve : null;
        mover = null;
        route = [];
        techVel.set(0, 0, 0);
        speed = 0;
        faceT = 0;
        if (early) early(true);
      }
    } else {
      let tx = techTargetX, tz = Z_WALK;
      if (hold) { tx = hold.x; tz = hold.z; face = hold.face; }
      else if (working || ready) tz = Z_WORK;
      else if (actState === 'walking') tx += Math.sin(t * 0.45) * 0.9;
      const dx = tx - techPos.x, dz = tz - techPos.z;
      const dist = Math.hypot(dx, dz);
      const want = Math.min(1.8, dist * 2.2);
      const a = Math.min(1, dt * 4);
      techVel.x += ((dist > 1e-3 ? (dx / dist) * want : 0) - techVel.x) * a;
      techVel.z += ((dist > 1e-3 ? (dz / dist) * want : 0) - techVel.z) * a;
      techPos.x += techVel.x * dt;
      techPos.z += techVel.z * dt;
      speed = Math.hypot(techVel.x, techVel.z);
    }
    const faceWant = speed > 0.14 ? Math.atan2(techVel.x, techVel.z) : face;
    if (routeDone && !mover && hold) { // arrived: square up quickly, then resolve
      faceT += dt;
      if (Math.abs(angDiff(heading, hold.face)) < 0.15 || faceT > 0.3) { heading = hold.face; const r = routeDone; routeDone = null; r(true); }
    }
    heading = lerpAngle(heading, faceWant, Math.min(1, dt * (mover || routeDone ? 14 : 6)));
    techBody.position.set(techPos.x, 0, techPos.z);
    techBody.rotation.y = heading;
    workK += ((working ? 1 : ready ? 0.55 : 0) - workK) * Math.min(1, dt * 4);
    techBlob.position.set(techPos.x, 0.003, techPos.z);
    techBlob.visible = revealT > 1.6;
    const stride = clamp(speed / 1.1, 0, 1);
    if (soldier) {
      const moveW = clamp(speed / 0.9, 0, 1), runW = soldier.run ? clamp((speed - 2.5) / 1.1, 0, 1) : 0;
      soldier.walk.setEffectiveWeight(moveW * (1 - runW));
      if (soldier.run) soldier.run.setEffectiveWeight(moveW * runW);
      soldier.idle.setEffectiveWeight(1 - moveW);
      soldier.walk.timeScale = clamp(speed / 1.15, 0.6, 2.6); // cadence follows speed so the feet don't slide
      if (soldier.run) soldier.run.timeScale = clamp(speed / 3.4, 0.8, 1.9);
      soldier.mixer.update(dt);
      if (workK > 0.01) poseTool(workK);
      if (reach && reach.k > 0.001) poseReach(reach.k);
    } else { // simple figure: procedural walk cycle + tool pose
      walkPhase += dt * (2.2 + 3.4 * stride) * stride * Math.PI;
      const sw = Math.sin(walkPhase) * stride;
      const k = Math.min(1, dt * 10);
      tech.hipL.rotation.x = lerp(tech.hipL.rotation.x, sw * 0.5, k);
      tech.hipR.rotation.x = lerp(tech.hipR.rotation.x, -sw * 0.5, k);
      const gesture = !working && actState === 'gesture';
      tech.shL.rotation.x = lerp(tech.shL.rotation.x, working ? -0.95 : gesture ? -1.9 : -sw * 0.4, k);
      tech.elL.rotation.x = lerp(tech.elL.rotation.x, working ? -0.85 : gesture ? -0.4 : -0.15, k);
      tech.shR.rotation.x = lerp(tech.shR.rotation.x, working ? -1.25 : sw * 0.4, k);
      tech.elR.rotation.x = lerp(tech.elR.rotation.x, working ? -0.35 : -0.15, k);
      if (reach) tech.shR.rotation.x = lerp(tech.shR.rotation.x, -1.5 * reach.k, 0.5);
      tech.g.position.y = Math.abs(Math.sin(walkPhase)) * 0.04 * stride + (stride < 0.05 ? 0.004 * Math.sin(t * 1.7) : 0);
    }
    placeHandTool(dt, working);
    for (const m of [dialTrack, dialArc, tintDisc]) { m.position.x = techPos.x; m.position.z = techPos.z; }
  }

  // floor ripples while a tool session runs: amplitude follows the live wrist rms, colour follows the exposure state
  const rippleGeo = new THREE.RingGeometry(0.92, 1.0, 72);
  const ripples = Array.from({ length: 14 }, () => {
    const m = new THREE.Mesh(rippleGeo, new THREE.MeshBasicMaterial({ color: C.zinc500, toneMapped: false, transparent: true, opacity: 0, depthWrite: false }));
    m.rotation.x = -Math.PI / 2;
    m.position.y = 0.018;
    m.renderOrder = 5;
    m.visible = false;
    scene.add(m);
    return { m, age: 9, life: 1.5, amp: 0 };
  });
  let rippleClock = 0;
  function updateRipples(dt) {
    const active = isWorking() && revealT > 1.6;
    const amp = clamp(vibRms / 0.45, 0.2, 1);
    rippleClock -= dt;
    if (active && rippleClock <= 0) {
      const cand = actState === 'candidate' && !toolActive;
      rippleClock = cand ? 0.42 : 0.24;
      const r = ripples.find((x) => x.age >= x.life);
      if (r) {
        r.age = 0;
        r.amp = amp * (cand ? 0.55 : 1);
        const px2 = handTool.visible ? handTool.position.x : techPos.x + Math.sin(heading) * 0.42;
        const pz2 = handTool.visible ? handTool.position.z : techPos.z + Math.cos(heading) * 0.42;
        r.m.position.x = px2;
        r.m.position.z = pz2;
        r.m.material.color.setHex(exposureColor());
        r.m.visible = true;
      }
    }
    for (const r of ripples) {
      if (r.age >= r.life) { r.m.visible = false; continue; }
      r.age += dt;
      const q = r.age / r.life;
      const s = 0.22 + q * (0.6 + 1.5 * r.amp);
      r.m.scale.set(s, s, 1);
      r.m.material.opacity = (1 - q) * (0.3 + 0.5 * r.amp);
    }
  }

  // ---------- defects: the faulty component turns red (no 3D text; details live in the UI cards) ----------
  const marks = new Map();
  function highlight(group, comp, st) {
    const x = sx(st.pos);
    const kind = /return/.test(comp) ? 'return' : /pulley|drum|lagging/.test(comp) ? 'pulley' : /idler|roller|roll\b|bearing/.test(comp) ? 'carry'
      : /take.?up|tension|counterweight/.test(comp) ? 'takeup' : /motor|gear|drive|coupling/.test(comp) ? 'drive'
        : /skirt|chute|hopper|load/.test(comp) ? 'chute' : null;
    const add = (geo, pos, quat) => {
      const m = new THREE.Mesh(geo, M.alarm);
      m.position.copy(pos);
      if (quat) m.quaternion.copy(quat);
      group.add(m);
    };
    if (kind === 'carry' || kind === 'return' || kind === 'pulley') {
      const want = kind === 'pulley' ? (/tail/.test(comp) ? X0 : /head|drive/.test(comp) ? X1 : x) : x;
      let best = null;
      for (const r of rollerDefs) if (r.kind === kind && (!best || Math.abs(r.x - want) < Math.abs(best.x - want))) best = r;
      if (!best) return;
      if (best.pulley) {
        for (const s of [-1, 1]) add(new THREE.CylinderGeometry(PR + 0.03, PR + 0.03, 0.05, 40), new THREE.Vector3(best.x, PY, s * (BWF / 2 + 0.1)), new THREE.Quaternion().setFromUnitVectors(Y_AXIS, Z_AXIS));
      } else {
        for (const p of best.set) add(new THREE.CylinderGeometry(best.r * 1.12, best.r * 1.12, p.len * 1.01, 20), p.pos, new THREE.Quaternion().setFromUnitVectors(Y_AXIS, p.dir.clone().normalize()));
      }
    } else if (kind === 'takeup') {
      add(new THREE.BoxGeometry(0.5, 0.7, 0.5), cw.position.clone());
    } else if (kind === 'drive') {
      add(new THREE.BoxGeometry(0.6, 0.6, 0.5), new THREE.Vector3(X1, PY, -1.36));
    } else if (kind === 'chute') {
      for (const s of [-1, 1]) add(new THREE.BoxGeometry(1.82, 0.22, 0.035), new THREE.Vector3(LX + 0.5, H_CARRY + 0.19, s * 0.34));
    } else if (/belt|splice|track|edge|cover/.test(comp)) {
      add(new THREE.BoxGeometry(0.16, 0.02, 2 * TC), new THREE.Vector3(x, H_CARRY + 0.02, 0));
    }
  }
  function syncDefects(defects, stations) {
    const want = new Map();
    for (const d of defects) if (d && d.station_id) want.set(d.station_id, d);
    for (const [sid, mk] of marks) {
      if (!want.has(sid)) {
        for (const m of mk.group.children) m.geometry.dispose();
        scene.remove(mk.group);
        marks.delete(sid);
      }
    }
    for (const [sid, d] of want) {
      const st = stations.find((x) => x.id === sid);
      if (!st) continue;
      const comp = String(d.component || '').toLowerCase();
      const key = `${comp}|${st.pos}`;
      let mk = marks.get(sid);
      if (mk && mk.key === key) continue;
      if (mk) { for (const m of mk.group.children) m.geometry.dispose(); scene.remove(mk.group); }
      mk = { group: new THREE.Group(), key };
      highlight(mk.group, comp, st);
      scene.add(mk.group);
      marks.set(sid, mk);
    }
  }

  // ---------- state ----------
  let activeId = null;
  function apply(s) {
    if (!s) return;
    const list = Array.isArray(s.stations) && s.stations.length ? s.stations : Object.keys(POS_DEFAULT).map((id, i) => ({ id, number: i + 1, name: SHORT[id], status: 'ok' }));
    const stations = list.map((st, i) => ({
      ...st,
      number: st.number ?? i + 1,
      pos: typeof st.pos === 'number' ? st.pos : (POS_DEFAULT[st.id] ?? 0.08 + i * 0.215),
    }));
    const defectAt = new Set((s.defects || []).filter(Boolean).map((d) => d.station_id));
    for (const st of stations) {
      const o = ensureStation(st);
      const x = sx(st.pos);
      o.g.position.set(x, 0, Z_WALK);
      o.sel = s.active_station ? s.active_station === st.id : st.status === 'active';
      o.defect = defectAt.has(st.id) || st.status === 'defect';
      o.insp = st.status === 'inspected';
      const cls = `${o.sel ? ' sel' : ''}${o.defect ? ' defect' : ''}${!o.sel && !o.defect && o.insp ? ' ok' : ''}`;
      o.el.className = `bg3d-badge${cls}`;
      o.leader.className = `bg3d-leader${o.defect ? ' defect' : o.sel ? ' sel' : ''}`;
      o.el.textContent = String(st.number);
      o.el.title = `${st.number} · ${st.name || SHORT[st.id] || st.id}`;
      o.pri = o.defect ? 10 : o.sel ? 8 : o.insp ? 3 : 2;
      if (o.sel) techTargetX = x;
    }
    activeId = s.active_station || null;
    if (focusFollows && activeId && activeId !== focusId) focusId = activeId;
    syncDefects(s.defects || [], stations);
    toolActive = (s.tool_sessions || []).some((ts) => ts && ts.status === 'active');
    // a checkout is the server's snapshot.assignment (app3.js); older snapshots only carry active_tool
    const checked = 'assignment' in s ? (s.assignment && s.assignment.tool) || null : s.active_tool || null;
    // the page animates checkouts (pickTool/returnTool); the hand only follows the server on first load, or when no
    // scripted move ran for 8 s and the server's tool has differed for 4 s (so a page call always wins the race)
    const desired = toolKindOf(checked), nowMs = performance.now();
    if (desired !== lastDesired) { lastDesired = desired; desiredAt = nowMs; }
    if (!techPlaced) setActiveTool(desired, { glow: false, pop: false });
    else if (!scriptBusy && !reach && nowMs - scriptEndAt > 8000 && nowMs - desiredAt > 4000) setActiveTool(desired);
    workX = null;
    for (const st of stations) if (stationObjs.get(st.id) && stationObjs.get(st.id).sel) workX = sx(st.pos);
    if (workX == null && toolKindNow) { const d0 = stations.find((st) => defectAt.has(st.id)); if (d0) workX = sx(d0.pos); }
    if (workX != null) techTargetX = workX;
    if (!techPlaced) { // first snapshot (page load): put the worker where the state says, no walk-in
      techPlaced = true;
      techPos.set(techTargetX, 0, toolKindNow ? Z_WORK : Z_WALK);
      heading = Math.PI;
    }
    if (hold && !scriptBusy && activeId !== holdActive) hold = null; // a new station choice releases a scripted hold
    const ex = s.exposure || {};
    exposurePts = Number(ex.total_points) || 0;
    exposureStatus = ex.status === 'above_limit' || ex.status === 'above_action' ? ex.status : exposurePts >= 400 ? 'above_limit' : exposurePts >= 100 ? 'above_action' : 'ok';
    updateExposure();
  }

  // ---------- crew: the other banded workers from /api/fleet (setFleet); W-01 stays the operator-driven main worker ----------
  const CREW_TINTS = [[0xd4e83b, 0xf2c230], [0xf97316, 0x3b82f6], [0xc6e33a, 0x22c55e], [0xfb923c, 0xf2c230], [0xe4f04a, 0xffffff], [0xf97316, 0x22c55e]];
  const CREW_OFF = [-0.95, 0.95, -1.9, 1.9, 0.5, -0.5];
  const CRIB_SPOTS = [[ISL_X - ISL_W / 2 - 0.55, 3.2], [ISL_X + ISL_W / 2 + 0.55, 3.2], [ISL_X - 0.95, 2.45], [ISL_X + 0.95, 2.45], [ISL_X - ISL_W / 2 - 0.55, 2.5], [ISL_X + ISL_W / 2 + 0.55, 2.5]];
  const crew = new Map();
  const workerBadges = new Map();
  const workerHits = [];
  const workerHitGeo = new THREE.CylinderGeometry(0.36, 0.36, 1.9, 10);
  const hatMatCache = {};
  const _wp = new THREE.Vector3(), _nh = new THREE.Vector3(), _ne = new THREE.Vector3();
  let fleetData = [];
  let focusWorkerId = null;
  {
    const h = new THREE.Mesh(workerHitGeo, hitMat);
    h.position.y = 0.95;
    h.userData.workerId = 'W-01';
    techBody.add(h);
    workerHits.push(h);
  }
  function createNpc(id, slot) {
    const [vest, hat] = CREW_TINTS[slot % CREW_TINTS.length];
    const root = new THREE.Group();
    root.visible = false;
    scene.add(root);
    root.rotation.order = 'YXZ';
    const n = { id, slot, root, fallK: 0, model: null, mixer: null, idle: null, walk: null, bones: null, fallback: null, tools: {},
      toolGroup: new THREE.Group(), toolKind: null, pos: new THREE.Vector3(), vel: new THREE.Vector3(), heading: Math.PI, workK: 0, phase: 0, data: null };
    if (soldier) {
      const m = cloneSkinned(soldier.model);
      if (!hatMatCache[hat]) hatMatCache[hat] = std(hat, 0.35);
      const vestMat = soldierBaseMat ? hiVisMaterial(soldierBaseMat, vest) : null;
      m.traverse((o) => {
        if (o.isMesh) o.castShadow = false; // crew rely on their contact shadow: keeps the shadow pass cheap
        if (o.isSkinnedMesh && o.name === 'vanguard_Mesh' && vestMat) o.material = vestMat;
        else if (o.isMesh && o.material === hatMat) o.material = hatMatCache[hat];
      });
      root.add(m);
      n.model = m;
      n.mixer = new THREE.AnimationMixer(m);
      n.idle = n.mixer.clipAction(soldier.clips.idle);
      n.walk = n.mixer.clipAction(soldier.clips.walk);
      n.idle.play();
      n.walk.play();
      n.walk.setEffectiveWeight(0);
      n.idle.time = (slot * 0.37) % 1.9;
      const find = (nm) => { let f = null; m.traverse((o) => { if (!f && o.name === nm) f = o; }); return f; };
      n.bones = { rArm: find('mixamorigRightArm'), rFore: find('mixamorigRightForeArm'), rHand: find('mixamorigRightHand'), lArm: find('mixamorigLeftArm'), lFore: find('mixamorigLeftForeArm') };
    } else {
      n.fallback = buildTech();
      n.fallback.T.vest.color.setHex(vest);
      n.fallback.T.helmet.color.setHex(hat);
      root.add(n.fallback.g);
    }
    n.toolGroup.rotation.order = 'YXZ';
    n.toolGroup.visible = false;
    scene.add(n.toolGroup);
    n.hit = new THREE.Mesh(workerHitGeo, hitMat);
    n.hit.position.y = 0.95;
    n.hit.userData.workerId = id;
    root.add(n.hit);
    workerHits.push(n.hit);
    n.blob = blob(0, 0, 0.85, 0.85, 0, 0.003);
    return n;
  }
  function disposeNpc(n) {
    scene.remove(n.root, n.toolGroup, n.blob);
    const i = workerHits.indexOf(n.hit);
    if (i >= 0) workerHits.splice(i, 1);
  }
  function npcTarget(n, w) {
    const where = (w.walk && w.walk.to) || w.station_id;
    if (!where || where === 'CRIB') { const [x, z] = CRIB_SPOTS[n.slot % CRIB_SPOTS.length]; return { x, z, face: Math.atan2(ISL_X - x, ISL_Z - z), crib: true }; }
    const o = stationObjs.get(where);
    const x = clamp((o ? o.g.position.x : sx(POS_DEFAULT[where] ?? 0.5)) + CREW_OFF[n.slot % CREW_OFF.length], -6.9, 5.6); // keep off the tail guard and the ore pile
    const atWork = w.state === 'tool_active' || (!!w.assignment && w.state !== 'walking');
    return { x, z: atWork ? Z_WORK : Z_WALK, face: Math.PI, crib: false };
  }
  function normCrew(w) {
    const tool = (w.assignment && w.assignment.tool) || w.tool || null;
    const pts = Number(w.points ?? w.points_today ?? (w.exposure && w.exposure.total_points) ?? (w.verdict && w.verdict.total_points)) || 0;
    const vr = typeof w.verdict === 'string' ? w.verdict : (w.verdict && w.verdict.state) || w.status || null;
    const v = vr === 'limit' || vr === 'above_limit' ? 'limit' : vr === 'action' || vr === 'above_action' ? 'action'
      : vr ? 'within' : pts >= 400 ? 'limit' : pts >= 100 ? 'action' : 'within';
    return {
      id: String(w.id || w.worker_id || ''), name: w.name || w.id, is_simulated: Boolean(w.is_simulated ?? w.simulated ?? (w.source === 'sim')),
      station_id: w.station_id ?? w.active_station ?? w.station ?? null, walk: w.walk && typeof w.walk === 'object' ? w.walk : null,
      assignment: tool ? { tool } : null, state: w.state || w.activity || 'idle', points: pts, verdict: v,
      motion_rms: w.motion_rms, fall: Boolean(w.fall ?? w.possible_fall), placeholder: !!w.placeholder,
    };
  }
  function syncFleet() {
    const others = fleetData.filter((w) => w && w.id && w.id !== 'W-01').slice(0, 6);
    const seen = new Set();
    others.forEach((w, i) => {
      let n = crew.get(w.id);
      if (!n) {
        if (!soldier && !soldierFailed) return; // spawn once the rigged model (or its failure) is known
        n = createNpc(w.id, i);
        crew.set(w.id, n);
        const tg = npcTarget(n, w);
        n.pos.set(tg.x, 0, tg.z);
        n.heading = tg.face;
      }
      n.data = w;
      seen.add(w.id);
      const kind = toolKindOf(w.assignment && w.assignment.tool);
      if (kind !== n.toolKind) {
        n.toolKind = kind;
        n.toolGroup.clear();
        if (kind) { if (!n.tools[kind]) n.tools[kind] = makeTool(kind); n.toolGroup.add(n.tools[kind]); }
      }
    });
    for (const [id, n] of crew) if (!seen.has(id)) { disposeNpc(n); crew.delete(id); }
    if (worker2) worker2.holder.visible = crew.size === 0;
    syncWorkerBadges();
  }
  function badgeData(w) {
    const pts = Number(w.points) || 0;
    return { id: w.id, pts: `${pts < 10 ? pts.toFixed(1) : Math.round(pts)} pts`, v: w.fall ? 'limit' : w.verdict, sim: !!w.is_simulated, name: w.name || w.id, fall: !!w.fall };
  }
  function syncWorkerBadges() {
    const ids = new Set();
    for (const w of fleetData) {
      if (!w || !w.id || (w.id !== 'W-01' && !crew.has(w.id))) continue;
      ids.add(w.id);
      let b = workerBadges.get(w.id);
      if (!b) {
        const el = document.createElement('div');
        el.className = 'bg3d-wbadge';
        el.innerHTML = '<i></i><span class="id"></span><span class="pts"></span><span class="sim">SIM</span>';
        el.style.display = 'none';
        const wid = w.id;
        el.addEventListener('pointerdown', (e) => e.stopPropagation());
        el.addEventListener('click', (e) => { e.stopPropagation(); if (onWorkerClick) onWorkerClick(wid); });
        labelLayer.appendChild(el);
        b = { id: w.id, el, idEl: el.querySelector('.id'), ptsEl: el.querySelector('.pts'), simEl: el.querySelector('.sim'), key: '', w: 84, h: 22, box: null, v: 'within', tool: false };
        workerBadges.set(w.id, b);
      }
      const d = badgeData(w);
      b.v = d.v;
      b.tool = w.state === 'tool_active';
      const focus = focusWorkerId === w.id;
      const key = `${d.pts}|${d.v}|${d.sim}|${focus}|${d.fall}`;
      if (key !== b.key) {
        b.key = key;
        b.idEl.textContent = d.id;
        b.ptsEl.textContent = d.pts;
        b.simEl.style.display = d.sim ? '' : 'none';
        b.el.className = `bg3d-wbadge${d.v === 'action' ? ' action' : d.v === 'limit' ? ' limit' : ''}${focus ? ' focus' : ''}`;
        b.el.title = `${d.name} · ${d.sim ? 'simulated band' : 'band 07'}${d.fall ? ' · possible fall' : ''}`;
        const shown = b.el.style.display;
        b.el.style.display = '';
        b.w = b.el.offsetWidth || 84;
        b.h = b.el.offsetHeight || 22;
        b.el.style.display = shown;
      }
    }
    for (const [id, b] of workerBadges) if (!ids.has(id)) { b.el.remove(); workerBadges.delete(id); }
  }
  function workerPos(id, out) {
    if (id === 'W-01') return out.set(techPos.x, 0, techPos.z);
    const n = crew.get(id);
    return n ? out.set(n.pos.x, 0, n.pos.z) : null;
  }
  const workerVisible = (id) => (id === 'W-01' ? chainVisible(techBody) : !!(crew.get(id) && crew.get(id).root.visible));
  function npcPalm(n, out) {
    if (n.bones && n.bones.rHand && n.bones.rFore) {
      n.bones.rHand.getWorldPosition(out);
      n.bones.rFore.getWorldPosition(_ne);
      return out.addScaledVector(_ne.subVectors(out, _ne).normalize(), 0.075);
    }
    if (n.fallback) return n.fallback.grip.getWorldPosition(out);
    return out.set(n.pos.x, 0.85, n.pos.z);
  }
  function updateFleet(dt) {
    for (const n of crew.values()) {
      const w = n.data || {};
      const tg = w.fall ? { x: n.pos.x, z: n.pos.z, face: n.heading, crib: false } : npcTarget(n, w);
      const dx = tg.x - n.pos.x, dz = tg.z - n.pos.z, dist = Math.hypot(dx, dz);
      const want = Math.min(1.9, dist * 2.2), a = Math.min(1, dt * 4);
      n.vel.x += ((dist > 1e-3 ? (dx / dist) * want : 0) - n.vel.x) * a;
      n.vel.z += ((dist > 1e-3 ? (dz / dist) * want : 0) - n.vel.z) * a;
      n.pos.x += n.vel.x * dt;
      n.pos.z += n.vel.z * dt;
      const speed = Math.hypot(n.vel.x, n.vel.z);
      n.heading = lerpAngle(n.heading, speed > 0.14 ? Math.atan2(n.vel.x, n.vel.z) : tg.face, Math.min(1, dt * 5));
      n.root.visible = revealT > 1.45;
      n.fallK += ((w.fall ? 1 : 0) - n.fallK) * Math.min(1, dt * 3);
      n.root.position.set(n.pos.x, 0.13 * n.fallK, n.pos.z);
      n.root.rotation.y = n.heading;
      n.root.rotation.x = (Math.PI / 2) * n.fallK;
      const here = dist < 0.45;
      const working = w.state === 'tool_active' && here && !tg.crib && !w.fall;
      const ready = !!n.toolKind && here && !tg.crib && !working;
      n.workK += ((working ? 1 : ready ? 0.5 : 0) - n.workK) * Math.min(1, dt * 4);
      if (n.mixer) {
        const ww = clamp(speed / 0.9, 0, 1);
        n.walk.setEffectiveWeight(ww);
        n.idle.setEffectiveWeight(1 - ww);
        n.walk.timeScale = clamp(speed / 1.15, 0.6, 1.8);
        n.mixer.update(dt);
        if (n.workK > 0.01 && n.bones && n.bones.rArm) poseToolFor(n.bones, n.heading, n.workK, n.toolKind === 'breaker');
      } else if (n.fallback) {
        const f = n.fallback, k = Math.min(1, dt * 10), stride = clamp(speed / 1.1, 0, 1);
        n.phase += dt * (2.2 + 3.4 * stride) * stride * Math.PI;
        const sw = Math.sin(n.phase) * stride;
        f.hipL.rotation.x = lerp(f.hipL.rotation.x, sw * 0.5, k);
        f.hipR.rotation.x = lerp(f.hipR.rotation.x, -sw * 0.5, k);
        f.shR.rotation.x = lerp(f.shR.rotation.x, working ? -1.25 : sw * 0.4, k);
        f.shL.rotation.x = lerp(f.shL.rotation.x, working ? -0.95 : -sw * 0.4, k);
      }
      if (n.toolKind && n.root.visible) {
        npcPalm(n, _nh);
        n.toolGroup.visible = true;
        n.toolGroup.position.copy(_nh);
        n.toolGroup.rotation.set(n.toolKind === 'breaker' ? lerp(-1.1, 0, n.workK) : lerp(0, 0.12, n.workK), n.heading, 0);
        if (working) {
          const amp = clamp((Number(w.motion_rms) || 0.3) / 0.35, 0.3, 1.4);
          n.toolGroup.rotation.x += (Math.random() - 0.5) * 0.09 * amp;
          n.toolGroup.rotation.z = (Math.random() - 0.5) * 0.09 * amp;
          n.toolGroup.position.x += (Math.random() - 0.5) * 0.01 * amp;
          n.toolGroup.position.y += (Math.random() - 0.5) * 0.01 * amp;
        }
        n.toolGroup.scale.setScalar(1.15);
      } else n.toolGroup.visible = false;
      n.blob.visible = n.root.visible;
      n.blob.position.set(n.pos.x, 0.003, n.pos.z);
    }
  }

  // ---------- camera: intro fly-in, then at rest; eases toward the worker during tool use or the guided-flow station ----------
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.minDistance = 6;
  controls.maxDistance = 46;
  controls.minPolarAngle = 0.3;
  controls.maxPolarAngle = Math.PI * 0.46;
  let userT = 0;
  controls.addEventListener('start', () => { userT = 1e9; });
  controls.addEventListener('end', () => { userT = 8; });
  const target0 = new THREE.Vector3(1.3, 0.55, 0.85);
  const targetSq = new THREE.Vector3(1.6, 0.5, 0.9);
  const look = target0.clone();
  const lookGoal = new THREE.Vector3();
  const camGoal = new THREE.Vector3();
  const lastFocus = new THREE.Vector3();
  let lastFocusR = 0.6;
  let focusId = null; // set by focusStation()
  let focusFollows = false; // the focus was the active station, so it follows later station changes
  let focusK = 0;
  function goal(dt = 0) {
    const aspect = camera.aspect || 1.6;
    // wide stage: low three-quarter view; square/portrait stage: more diagonal and a little higher (measured fit constants)
    const w = clamp((1.8 - aspect) / 0.7, 0, 1);
    let R = clamp(Math.max(lerp(31.5, 25, w) / aspect, 16), 16, 36);
    const az = lerp(0.6, 0.92, w);
    const el = lerp(0.5, 0.62, w);
    lookGoal.lerpVectors(target0, targetSq, w);
    const fo = focusId ? stationObjs.get(focusId) : null;
    let fx = null;
    const scripted = scriptBusy > 0 || performance.now() - scriptEndAt < 4000;
    if (scripted) { // keep the worker and where he is going (tool wall or station) in frame
      const dest = route.length ? route[route.length - 1] : hold || { x: techPos.x, z: techPos.z };
      const toCrib = dest.z > 2.75;
      const px2 = dest.x, pz2 = toCrib ? ISL_Z - 0.15 : dest.z - 0.3;
      const gap = Math.hypot(px2 - techPos.x, pz2 - techPos.z);
      fx = px2;
      lastFocus.set((techPos.x + px2) / 2, 1.1, (techPos.z + pz2) / 2);
      lastFocusR = clamp(0.48 + gap * 0.028, 0.48, 0.8);
    } else if (focusWorkerId && workerPos(focusWorkerId, _wp)) { fx = _wp.x; lastFocus.set(_wp.x, 1.0, _wp.z - 0.3); lastFocusR = 0.5; }
    else if (workK > 0.05) { fx = techPos.x; lastFocus.set(techPos.x, 1.0, techPos.z - 0.45); lastFocusR = 0.55; }
    else if (fo && focusId !== activeId) { fx = fo.g.position.x; lastFocus.set(fx, 0.9, 0.9); lastFocusR = 0.62; }
    else if (activeId) { fx = techPos.x; lastFocus.set(techPos.x, 1.0, techPos.z - 0.6); lastFocusR = fo ? 0.62 : 0.74; }
    focusK += ((fx != null ? 1 : 0) - focusK) * Math.min(1, dt * 4.5);
    if (focusK > 0.001) {
      lookGoal.lerp(lastFocus, focusK);
      R = lerp(R, Math.max(9, R * lastFocusR), focusK);
    }
    camGoal.set(lookGoal.x + R * Math.sin(az) * Math.cos(el), lookGoal.y + R * Math.sin(el), lookGoal.z + R * Math.cos(az) * Math.cos(el));
  }
  goal();
  camera.position.copy(camGoal);
  if (!reduced) { camera.position.sub(lookGoal).multiplyScalar(1.7).add(lookGoal); camera.position.y += 6; }
  camera.lookAt(look);
  controls.target.copy(look);
  function updateCamera(dt) {
    goal(dt);
    if (userT > 0) { userT -= dt; controls.update(); return; }
    const k = 1 - Math.pow(revealT < 2.6 ? 0.22 : 0.02, dt);
    camera.position.lerp(camGoal, k);
    look.lerp(lookGoal, k);
    camera.lookAt(look);
    controls.target.copy(look);
  }

  // ---------- interaction: click a station (3D) ----------
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let downAt = null;
  const pick = (e) => {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(hitMeshes, false)[0];
    return hit ? hit.object.userData.stationId : null;
  };
  const pickWallTool = (e) => {
    if (!onToolClick) return null;
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    for (const h of ray.intersectObjects(crib.hits, false)) {
      const s = crib.slots[h.object.userData.toolId];
      if (s && s.tool.visible) return s.id;
    }
    return null;
  };
  const pickWorker = (e) => {
    if (!onWorkerClick || !workerHits.length) return null;
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    for (const h of ray.intersectObjects(workerHits, false)) if (chainVisible(h.object)) return h.object.userData.workerId;
    return null;
  };
  const onDown = (e) => { downAt = [e.clientX, e.clientY]; };
  const onUp = (e) => {
    if (!downAt) return;
    const moved = Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]);
    downAt = null;
    if (moved > 5) return;
    const tid = pickWallTool(e);
    if (tid) { onToolClick(tid); return; }
    const wid = pickWorker(e);
    if (wid) { onWorkerClick(wid); return; }
    const id = pick(e);
    if (id && onStationClick) onStationClick(id);
  };
  let hoverT = 0;
  const onMove = (e) => {
    if (downAt || performance.now() - hoverT < 50) return;
    hoverT = performance.now();
    const tid = pickWallTool(e);
    const wid = tid ? null : pickWorker(e);
    const id = tid || wid ? null : pick(e);
    hoverId = id;
    renderer.domElement.style.cursor = id || tid || wid ? 'pointer' : '';
  };
  const onLeave = () => { hoverId = null; renderer.domElement.style.cursor = ''; };
  renderer.domElement.addEventListener('pointerdown', onDown);
  renderer.domElement.addEventListener('pointerup', onUp);
  renderer.domElement.addEventListener('pointermove', onMove);
  renderer.domElement.addEventListener('pointerleave', onLeave);

  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    stageW = w;
    stageH = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  // ---------- loop ----------
  const clock = new THREE.Clock();
  let t = 0, revealT = reduced ? 99 : 0, raf = 0, beltRun = 0, labelsIn = false;
  for (const p of parts) { p.visible = false; p.position.y = p.userData.reveal.y - 4; }
  function updateReveal() {
    for (const p of parts) {
      const R = p.userData.reveal;
      const k = clamp((revealT - R.delay) / 0.7, 0, 1);
      p.visible = k > 0;
      p.position.y = R.y - 4 * (1 - ease3(k));
    }
    oreGroup.visible = revealT > 1.3;
    for (const mk of marks.values()) mk.group.visible = revealT > 1.5;
    if (!labelsIn && revealT > 1.9) { labelsIn = true; labelLayer.classList.remove('bg3d-pre'); }
  }
  if (reduced) updateReveal();
  let lastFrameAt = 0;
  function frame() {
    raf = requestAnimationFrame(frame);
    if (document.hidden) return; // hidden tabs cost nothing
    // calm scene (intro done, no orbiting, no tool session): ~30 fps is plenty and halves GPU load on a shared iGPU
    const now = performance.now();
    const busy = revealT < 3.2 || userT > 0 || workK > 0.02 || isWorking() || scriptBusy > 0 || !!reach || route.length > 0;
    if (!busy && now - lastFrameAt < 30) return;
    lastFrameAt = now;
    tick(clock.getDelta());
    renderer.render(scene, camera);
    placeLabels();
  }
  function tick(rawDt) { // one simulation step (also driven by the debug step() for tests in hidden tabs)
    const dt = Math.min(rawDt, 0.05);
    t += dt;
    revealT += Math.min(rawDt, 0.5);
    if (revealT < 3) updateReveal();
    // the belt is isolated (stopped) while a tool is in use on it
    beltRun += ((isWorking() ? 0 : 1) * (revealT > 1.2 ? 1 : 0) - beltRun) * Math.min(1, dt * 1.3);
    const v = BELT_V * beltRun;
    carryTex.offset.x -= (v / 1.2) * dt;
    retTex.offset.x += (v / 1.2) * dt;
    for (const p of pulleys) p.rotation.y -= (v / (p === pulleys[2] ? 0.13 : PR)) * dt;
    fan.rotation.x += 30 * beltRun * dt;
    for (let left = dt; left > 1e-4; left -= 0.025) updateOre(Math.min(0.025, left), v);
    updateDust(dt, beltRun);
    cw.position.y = 1.2 + 0.05 * Math.sin(t * 0.8) * beltRun - 0.06 * (1 - beltRun);
    const top = 2.75 + 0.12 - 0.2, bot = cw.position.y + 0.33;
    cwCable.scale.y = Math.max(0.01, top - bot);
    cwCable.position.set(TX, (top + bot) / 2, TZ);
    updateTech(dt, t);
    updateReach(dt);
    updateFleet(dt);
    if (worker2) worker2.mixer.update(dt);
    updateLoader(t);
    updateRipples(dt);
    updateStations(t);
    updateCrib(dt);
    updateCamera(dt);
  }
  frame();

  // debug handle (inspection only, like mongomation's host.__f3d)
  container.__bg3d = {
    renderer, scene, camera,
    get soldier() { return !!soldier; },
    get tool() { return toolKindNow; },
    get crew() { return [...crew.values()].map((n) => ({ id: n.id, x: +n.pos.x.toFixed(2), z: +n.pos.z.toFixed(2), tool: n.toolKind, state: n.data && n.data.state, visible: n.root.visible })); },
    get worker() { return { x: +techPos.x.toFixed(2), z: +techPos.z.toFixed(2), heading: +heading.toFixed(2), route: route.length, hold: hold && { ...hold }, busy: scriptBusy, reach: !!reach }; },
    step(sec = 1) { const n = Math.round(sec * 30); for (let i = 0; i < n; i += 1) tick(1 / 30); renderer.render(scene, camera); placeLabels(); },
    bench(n = 20) {
      const gl = renderer.getContext();
      const t0 = performance.now();
      for (let i = 0; i < n; i += 1) renderer.render(scene, camera);
      gl.finish();
      return { msPerFrame: +((performance.now() - t0) / n).toFixed(2), calls: renderer.info.render.calls, triangles: renderer.info.render.triangles };
    },
  };

  return {
    apply,
    setActivity(state) { actState = state || 'idle'; },
    setVibration(rms) { vibRms = Number(rms) || 0; },
    // optional hook used by the guided flow: closer shot of a station (step 1 or no id = whole line)
    focusStation(id, step) {
      focusId = id && step !== 1 ? String(id) : null;
      focusFollows = !!focusId && focusId === activeId;
      userT = Math.min(userT, 0.5); // hand the camera back from any manual orbit
    },
    // crew from /api/fleet: W-02..W-05 (simulated) appear as rigged workers with tools and badges; W-01 stays the main worker
    setFleet(workers) {
      fleetData = (Array.isArray(workers) ? workers : []).filter(Boolean).map(normCrew).filter((w) => w.id && !w.placeholder);
      syncFleet();
    },
    focusWorker(id) {
      focusWorkerId = id ? String(id) : null;
      userT = Math.min(userT, 0.5);
      syncWorkerBadges();
    },
    // tool-checkout choreography (all return Promises that resolve when the move has finished)
    walkToCrib() {
      return enqueue(async () => {
        const slot = slotOf(toolKindNow);
        await walkTo(pathToCrib(slot ? slot.standX : ISL_X), 0, false, 1.7); // <= 2.0 s incl. squaring up
        return true;
      });
    },
    highlightTools(ids) {
      highlightSet.clear();
      for (const id of ids || []) { const s = slotOf(toolKindOf(id)); if (s) highlightSet.add(s.id); }
    },
    pickTool(toolId) {
      return enqueue(async () => {
        const kind = toolKindOf(toolId);
        const slot = slotOf(kind);
        if (!kind || !slot) return false;
        if (toolKindNow === kind) { highlightSet.clear(); return true; } // already in hand (a page load snapped it)
        if (toolKindNow) setActiveTool(null, { glow: false });
        const near = Math.abs(techPos.z - STAND_Z) < 0.45 && Math.abs(techPos.x - slot.standX) < 2.4;
        if (near) await Promise.all([walkTo(pathToCrib(slot.standX), 0, false, 0.3, false), reachTo(slot, 'pick', kind)]); // <= 0.8 s at the station
        else { await walkTo(pathToCrib(slot.standX), 0, false, 1.7, false); await reachTo(slot, 'pick', kind); }
        highlightSet.clear();
        return true;
      });
    },
    walkToStation(stationId) {
      return enqueue(async () => {
        const o = stationObjs.get(String(stationId));
        if (!o) return false;
        const x = o.g.position.x;
        workX = x;
        techTargetX = x;
        await walkTo(pathToStation(x), Math.PI, true, 2.2); // <= 2.5 s to any station
        return true;
      });
    },
    returnTool() {
      return enqueue(async () => {
        if (!toolKindNow) return true;
        const kind = toolKindNow, slot = slotOf(kind);
        if (!slot) { setActiveTool(null); return true; }
        await walkTo(pathToCrib(slot.standX), 0, false, 1.15, false);
        await reachTo(slot, 'return', kind); // <= 2.0 s in total
        return true;
      });
    },
    dispose() {
      snapScripts();
      disposed = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener('pointerdown', onDown);
      renderer.domElement.removeEventListener('pointerup', onUp);
      renderer.domElement.removeEventListener('pointermove', onMove);
      renderer.domElement.removeEventListener('pointerleave', onLeave);
      renderer.dispose();
      renderer.domElement.remove();
      labelLayer.remove();
      delete container.__bg3d;
    },
  };
}
