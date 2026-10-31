/* Varthaai home v2: WebGL scenes (hero + scroll story).
 *
 * Progressive enhancement only. The page is complete without this file; it
 * upgrades the hero poster and the story cards to live 3D on devices that can
 * afford it, and stays out of the way everywhere else (reduced motion, no
 * WebGL, Save-Data, very low memory).
 */
import * as THREE from 'three';
import { RoomEnvironment } from 'three-room-env';

const heroStage = document.getElementById('hero-stage');
const storyEl = document.getElementById('about');
if (heroStage) boot();

/* ── Capability gate ─────────────────────────────────────────────────── */
function capable() {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
  const conn = navigator.connection;
  if (conn && (conn.saveData || /(^|-)2g$/.test(conn.effectiveType || ''))) return false;
  if (navigator.deviceMemory && navigator.deviceMemory < 3) return false;
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch (e) { return false; }
}

function boot() {
  if (!capable()) return;
  // Wait for first paint and an idle moment, so the poster stays the LCP element.
  const start = () => { try { run(); } catch (err) { console.warn('[v2] 3D disabled:', err); } };
  if ('requestIdleCallback' in window) requestIdleCallback(start, { timeout: 1500 });
  else setTimeout(start, 300);
}

/* ── Shared helpers ──────────────────────────────────────────────────── */
const coarse = matchMedia('(hover: none)').matches;
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const seg = (p, a, b) => clamp01((p - a) / (b - a));
const lerp = (a, b, t) => a + (b - a) * t;
const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOutBack = (t) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); };
const rand = mulberry32(8);
function mulberry32(a) { return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

function makeRenderer(canvas) {
  const r = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  r.setPixelRatio(Math.min(window.devicePixelRatio || 1, coarse ? 1.5 : 2));
  r.outputColorSpace = THREE.SRGBColorSpace;
  r.toneMapping = THREE.NeutralToneMapping;
  r.toneMappingExposure = 0.92;
  r.setClearColor(0x000000, 0);
  r.shadowMap.enabled = true;
  r.shadowMap.type = THREE.PCFSoftShadowMap;
  return r;
}

/* Soft studio reflections, so the paper and the uruli read as real materials. */
function addEnvironment(renderer, scene, intensity = 0.55) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = intensity;
  pmrem.dispose();
}

/* A key light that casts a soft shadow onto an invisible ground. */
function addShadowGround(scene, y, size = 6, opacity = 0.22) {
  const key = new THREE.DirectionalLight(0xfff3d6, 1.35);
  key.position.set(2.5, 5, 3.5);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.radius = 6;
  key.shadow.bias = -0.0008;
  Object.assign(key.shadow.camera, { left: -2.5, right: 2.5, top: 2.5, bottom: -2.5, near: 0.5, far: 14 });
  scene.add(key);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(size, size), new THREE.ShadowMaterial({ opacity }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = y;
  ground.receiveShadow = true;
  scene.add(ground);
  return { key, ground };
}

function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0xfff6dc, 0x9fb85a, 0.55));
  const key = new THREE.DirectionalLight(0xfff1cc, 0.9);
  key.position.set(-3, 3, 5);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xe9c202, 1.0);
  rim.position.set(-4, 2, -3);
  scene.add(rim);
}

/* Tiny tween runner, so we don't ship an animation library for three tweens. */
function createTweens() {
  const list = [];
  return {
    add(dur, fn, { delay = 0, ease = easeOutCubic } = {}) {
      return new Promise((resolve) => list.push({ t0: performance.now() + delay, dur, fn, ease, resolve }));
    },
    tick(now) {
      for (let i = list.length - 1; i >= 0; i--) {
        const tw = list[i];
        const t = (now - tw.t0) / tw.dur;
        if (t < 0) continue;
        tw.fn(tw.ease(Math.min(1, t)));
        if (t >= 1) { list.splice(i, 1); tw.resolve(); }
      }
    },
    get busy() { return list.length > 0; },
  };
}

/* Render only while on screen and the tab is visible. */
function visibilityGate(el, onChange) {
  let inView = false;
  const sync = () => onChange(inView && !document.hidden);
  new IntersectionObserver((e) => { inView = e[0].isIntersecting; sync(); }, { rootMargin: '120px' }).observe(el);
  document.addEventListener('visibilitychange', sync);
}

/* Drop pixel ratio once if the device can't hold ~40 fps. */
function adaptiveDPR(renderer) {
  let frames = 0, acc = 0, last = 0, done = false;
  return (now) => {
    if (done) return;
    if (last) { acc += now - last; frames++; }
    last = now;
    if (frames === 90) {
      done = true;
      if (acc / frames > 25 && renderer.getPixelRatio() > 1) {
        renderer.setPixelRatio(1);
        renderer.setSize(renderer.domElement.clientWidth, renderer.domElement.clientHeight, false);
      }
    }
  };
}

/* ── Assets ──────────────────────────────────────────────────────────── */
function loadAssets(onReady) {
  const manager = new THREE.LoadingManager();
  const loader = new THREE.TextureLoader(manager);
  const d = heroStage.dataset;
  const tex = (url) => {
    const t = loader.load(url);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  };
  const assets = { front: tex(d.packFront), back: tex(d.packBack), sideA: tex(d.packSideA), sideB: tex(d.packSideB), flap: tex(d.packFlap), chip: tex(d.chip) };
  manager.onLoad = async () => {
    // A designed pack model (static/models/pack.glb) replaces the built-in carton.
    if (d.packModel) {
      try {
        const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
        assets.model = (await new GLTFLoader().loadAsync(d.packModel)).scene;
      } catch (err) { console.warn('[v2] pack model failed, using the built-in carton', err); }
    }
    onReady(assets);
  };
  manager.onError = (url) => console.warn('[v2] texture failed', url);
}

/* The Varthaai carton, built from the print dieline (Chips Box 21.5 x 14.5 x 10 cm)
 * and checked against the 360° video.
 *  - Panels are 14.5 cm (front/back) and 10 cm (sides) wide, 21.5 cm long.
 *  - Front and back stand straight to the side crease at 11.97 cm, then the
 *    board bends in one gradual curve (no hard crease) over its last 9.53 cm
 *    to meet the other panel at the ridge. The curve is solved so its length
 *    is exactly 9.53 cm, so the printed art lands where it does on the box.
 *  - The side gussets fold inward under the ridge, leaving a V recess above
 *    each side wall, folded on the dieline's creases.
 *  - The top flap printed above the front wraps over the ridge and is glued
 *    down the back roof: the tear strip and thumb notch seen in the video.
 * Every surface samples its own panel cut from the dieline. 1 unit = 14.5 cm. */
const CM = 1 / 14.5;
const PACK = {
  W: 14.5 * CM, D: 10 * CM, FLAT: 21.5 * CM,
  WALL: (21.5 - 9.53) * CM,          // straight wall, to the side crease
  CURVE: 9.53 * CM,                  // board length from the crease to the ridge
  APEX: 4.57 * CM,                   // gusset centre crease length (ridge -> inner point)
  FLAP: 7.24 * CM,                   // top flap length down the back roof
  END: 50 * Math.PI / 180,           // curve direction at the ridge, from vertical
};

/* Front/back profile above the wall: a cubic that leaves the wall vertically
 * and reaches z = 0 at the ridge. Returns samples by arc length. */
let _profile = null;
function packProfile() {
  if (_profile) return _profile;
  const { WALL, CURVE, D, END } = PACK;
  const make = (Hr) => {
    const h = Hr - WALL;
    const P0 = [WALL, D / 2], P1 = [WALL + 0.5 * h, D / 2];
    const e = 0.42 * Math.hypot(h, D / 2);
    const P3 = [Hr, 0], P2 = [Hr - e * Math.cos(END), e * Math.sin(END)];
    const at = (t) => { const u = 1 - t; const a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
      return [a * P0[0] + b * P1[0] + c * P2[0] + d * P3[0], a * P0[1] + b * P1[1] + c * P2[1] + d * P3[1]]; };
    const N = 240, pts = [at(0)], len = [0];
    for (let i = 1; i <= N; i++) { const p = at(i / N), q = pts[i - 1]; pts.push(p); len.push(len[i - 1] + Math.hypot(p[0] - q[0], p[1] - q[1])); }
    return { pts, len, total: len[N], Hr };
  };
  let lo = WALL + 0.2 * CURVE, hi = WALL + CURVE, c;
  for (let k = 0; k < 40; k++) { const mid = (lo + hi) / 2; c = make(mid); if (c.total > CURVE) hi = mid; else lo = mid; }
  // point on the curve at arc length s (0..CURVE), as [y, z]
  const point = (s) => {
    const { pts, len } = c; if (s <= 0) return pts[0];
    let i = 1; while (i < len.length - 1 && len[i] < s) i++;
    const t = (s - len[i - 1]) / (len[i] - len[i - 1] || 1);
    return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t];
  };
  // panel length L from the base (0..FLAT) -> [y, z] on the front sheet
  const along = (L) => (L <= WALL ? [L, D / 2] : point(Math.min(CURVE, L - WALL)));
  _profile = { ridge: c.Hr, top: c.Hr, wall: WALL, point, along };
  return _profile;
}

function makePack(assets) {
  if (assets.model) return fromModel(assets.model);
  const { W, D, FLAT, WALL, CURVE } = PACK;
  const P = packProfile();
  const group = new THREE.Group();
  const board = (map) => {
    map.anisotropy = 8;
    return new THREE.MeshPhysicalMaterial({ map, roughness: 0.55, sheen: 0.3, sheenRoughness: 0.8, sheenColor: 0xfff4d6, clearcoat: 0.12, clearcoatRoughness: 0.55, side: THREE.DoubleSide });
  };
  const sheetGeo = (sign, from, to, offset, uvV) => {
    const NU = 20, NV = 72, pos = [], uv = [], idx = [];
    for (let j = 0; j <= NV; j++) {
      const L = from + (to - from) * (j / NV);
      const [y, z] = P.along(L);
      for (let i = 0; i <= NU; i++) {
        const u = i / NU;
        const bow = L <= WALL ? 0.01 * Math.sin(Math.PI * u) * Math.sin(Math.PI * Math.min(1, L / WALL)) : 0;
        pos.push((sign > 0 ? u - 0.5 : 0.5 - u) * W, y + (offset ? offset * 0.3 : 0), sign * (z + bow + (offset || 0)));
        uv.push(u, uvV(L));
      }
    }
    for (let j = 0; j < NV; j++) for (let i = 0; i < NU; i++) {
      const a = j * (NU + 1) + i, b = a + 1, c = a + NU + 1, d = c + 1;
      idx.push(a, b, d, a, d, c);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(sign > 0 ? idx : idx.slice().reverse());
    g.computeVertexNormals();
    return g;
  };
  // Front and back: base to ridge, the whole printed panel.
  group.add(new THREE.Mesh(sheetGeo(1, 0, FLAT, 0, (L) => L / FLAT), board(assets.front)));
  group.add(new THREE.Mesh(sheetGeo(-1, 0, FLAT, 0, (L) => L / FLAT), board(assets.back || assets.front)));
  // Top flap: hinged on the front's top edge, folded over the ridge and glued
  // down the back roof (its free edge lowest). Sits a hair above the back board.
  if (assets.flap) {
    const flapGeo = sheetGeo(-1, FLAT - PACK.FLAP, FLAT, 0.0025, (L) => (FLAT - L) / PACK.FLAP);   // fold edge at the ridge, free edge lowest
    // the flap's printed u runs the other way round once folded over
    const uv = flapGeo.attributes.uv; for (let i = 0; i < uv.count; i++) uv.setX(i, 1 - uv.getX(i));
    group.add(new THREE.Mesh(flapGeo, board(assets.flap)));
  }

  // Sides. Below the crease: a flat wall. Above it: the gusset folds inward,
  // two facets per side meeting on the centre crease at an inner point I, with
  // their outer edges following the curved front/back panels.
  const R = [P.ridge, 0];                                    // ridge end, in (y, z)
  // inner point: |I - ridge| = APEX crease, |I - shoulder corner| = diagonal crease
  const diag = Math.hypot(D / 2, PACK.FLAT - PACK.APEX - WALL);
  let yI = WALL, g = 0;
  for (let lo = WALL, hi = P.ridge, k = 0; k < 50; k++) {    // solve for yI, then depth g
    yI = (lo + hi) / 2;
    const g2a = PACK.APEX ** 2 - (P.ridge - yI) ** 2;          // from the centre crease
    const g2b = diag ** 2 - (yI - WALL) ** 2 - (D / 2) ** 2;    // from a diagonal crease
    if (g2a > g2b) hi = yI; else lo = yI;
    g = Math.sqrt(Math.max(0, g2a));
  }
  const yA = PACK.FLAT - PACK.APEX;                          // inner point's place on the flat panel
  function sideGeo(right) {
    const pos = [], uv = [];
    const X = right ? W / 2 : -W / 2, inward = right ? -1 : 1;
    // s: distance along the flat panel from its dieline-left edge (0..D)
    const zOf = (s) => (right ? D / 2 - s : -D / 2 + s);
    const v = (sx, sy, sz, s, flatY) => { pos.push(sx, sy, sz); uv.push(s / D, flatY / FLAT); };
    const tri = (a, b, c) => { v(...a); v(...b); v(...c); };
    // wall
    tri([X, 0, zOf(0), 0, 0], [X, 0, zOf(D), D, 0], [X, WALL, zOf(D), D, WALL]);
    tri([X, 0, zOf(0), 0, 0], [X, WALL, zOf(D), D, WALL], [X, WALL, zOf(0), 0, WALL]);
    // gusset
    const I = [X + inward * g, yI, 0, D / 2, yA];
    const N = 16;
    for (const edge of [0, D]) {                              // each panel edge follows a curved roof
      const sign = zOf(edge) > 0 ? 1 : -1;
      let prev = null;
      for (let k = 0; k <= N; k++) {
        const s = (k / N) * CURVE, [y, z] = P.point(s);
        const cur = [X, y, sign * z, edge, WALL + s];
        if (prev) tri(prev, cur, I);
        prev = cur;
      }
    }
    tri([X, WALL, zOf(0), 0, WALL], [X, WALL, zOf(D), D, WALL], I);   // lower gusset triangle
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.computeVertexNormals();
    return geo;
  }
  group.add(new THREE.Mesh(sideGeo(true), board(assets.sideA || assets.side)));
  group.add(new THREE.Mesh(sideGeo(false), board(assets.sideB || assets.side)));

  // Base.
  const base = new THREE.Mesh(new THREE.PlaneGeometry(W, D), new THREE.MeshStandardMaterial({ color: 0xa8c266, roughness: 0.8 }));
  base.rotation.x = Math.PI / 2;
  group.add(base);

  group.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  group.position.y = -P.top / 2;
  const pivot = new THREE.Group();
  pivot.add(group);
  pivot.userData.height = P.top;
  return pivot;
}

/* Wrap a supplied glTF pack so it behaves like the built-in carton: same
 * height, centred on its pivot, casting shadows. */
function fromModel(model) {
  const obj = model.clone(true);
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3()), centre = box.getCenter(new THREE.Vector3());
  const height = packProfile().top;
  const k = height / Math.max(size.y, 1e-6);
  obj.position.sub(centre).multiplyScalar(k);
  obj.scale.multiplyScalar(k);
  obj.traverse((o) => { if (o.isMesh) { o.castShadow = true; if (o.material) o.material.envMapIntensity = 1; } });
  const pivot = new THREE.Group();
  pivot.add(obj);
  pivot.userData.height = height;
  return pivot;
}

/* A banana chip: a thin wavy disc with a slight cup, its caps textured from
 * a real product photo. A per-instance `fry` attribute blends raw slice to
 * golden chip in the shader. */
function makeChipMesh(assets, count) {
  const R = 0.16;
  const g = new THREE.CylinderGeometry(R, R, 0.014, 40, 1);
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const a = Math.atan2(z, x);
    const edge = 1 + 0.05 * Math.sin(5 * a) + 0.03 * Math.sin(9 * a + 1.3);
    pos.setX(i, x * edge); pos.setZ(i, z * edge);
    pos.setY(i, pos.getY(i) + 0.05 * ((x * x + z * z) / (R * R)) + 0.012 * Math.sin(3 * a));
  }
  g.computeVertexNormals();
  g.setAttribute('fry', new THREE.InstancedBufferAttribute(new Float32Array(count).fill(1), 1));

  const cap = new THREE.MeshStandardMaterial({ map: assets.chip, roughness: 0.55 });
  cap.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float fry;\nvarying float vFry;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvFry = fry;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vFry;')
      .replace('#include <map_fragment>', `
        #ifdef USE_MAP
          vec4 sampledDiffuseColor = texture2D( map, vMapUv );
          float lum = dot( sampledDiffuseColor.rgb, vec3( 0.299, 0.587, 0.114 ) );
          vec3 raw = vec3( 0.95, 0.92, 0.80 ) * ( 0.72 + 0.4 * lum );
          sampledDiffuseColor.rgb = mix( raw, sampledDiffuseColor.rgb, vFry );
          diffuseColor *= sampledDiffuseColor;
        #endif`);
  };
  const side = new THREE.MeshStandardMaterial({ color: 0xc98a10, roughness: 0.7 });
  const mesh = new THREE.InstancedMesh(g, [side, cap, cap], count);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.frustumCulled = false;
  return mesh;
}

function softShadow(size, opacity) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d');
  const grd = ctx.createRadialGradient(64, 64, 4, 64, 64, 64);
  grd.addColorStop(0, `rgba(46,42,28,${opacity})`);
  grd.addColorStop(1, 'rgba(46,42,28,0)');
  ctx.fillStyle = grd;
  ctx.fillRect(0, 0, 128, 128);
  const m = new THREE.Mesh(
    new THREE.PlaneGeometry(size, size * 0.5),
    new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false, toneMapped: false }),
  );
  m.rotation.x = -Math.PI / 2;
  return m;
}

function crunch() { if (typeof window.playCrunchSound === 'function') window.playCrunchSound(); }

/* ── Main ─────────────────────────────────────────────────────────────── */
function run() {
  loadAssets((assets) => {
    initHero(assets);
    if (storyEl) initStory(assets);
  });
}

/* ═══ HERO ═════════════════════════════════════════════════════════════ */
/* The carton stands in the grove; chips drift around it. The mascot is a DOM
 * component beside the canvas; this scene tells it what's happening through
 * `v2:pack` events (hover, grab, spin, release, tap). */
function initHero(assets) {
  const canvas = document.getElementById('hero-canvas');
  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
  const tweens = createTweens();
  const dpr = adaptiveDPR(renderer);
  addEnvironment(renderer, scene, 0.32);
  addLights(scene);

  const rig = new THREE.Group();
  scene.add(rig);

  const pack = makePack(assets);
  const H = pack.userData.height;
  const packHome = new THREE.Vector3(0.28, 0.02, 0);
  pack.position.copy(packHome);
  rig.add(pack);
  pack.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  const { ground } = addShadowGround(scene, packHome.y - H / 2 - 0.002, 6, 0.24);
  const contact = softShadow(1.5, 0.32);
  contact.position.set(packHome.x, packHome.y - H / 2 + 0.002, 0.02);
  rig.add(contact);

  // Chips drifting around the carton.
  const N = coarse ? 12 : 18;
  const chips = makeChipMesh(assets, N);
  rig.add(chips);
  const orbit = Array.from({ length: N }, (_, i) => ({
    a: (i / N) * Math.PI * 2 + rand() * 0.4,
    rx: 0.92 + rand() * 0.3,
    rz: 0.5 + rand() * 0.3,
    y: -0.5 + rand() * 1.15,
    speed: 0.1 + rand() * 0.12,
    bob: rand() * Math.PI * 2,
    spin: new THREE.Vector3(rand() * 2 - 1, rand() * 2 - 1, rand() * 2 - 1).multiplyScalar(1.4),
    rot0: new THREE.Euler(rand() * 6, rand() * 6, rand() * 6),
    s: 0.45 + rand() * 0.3,
    pop: 0,
  }));
  const dummy = new THREE.Object3D();

  function fit() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const needH = 2.0, needW = 2.5;
    const dist = Math.max(needH / 2 / t, needW / 2 / (t * camera.aspect));
    // Mascot sits at the stage's left on wide screens, so nudge the scene right.
    rig.position.x = camera.aspect > 1.05 ? 0.2 : 0.05;
    camera.position.set(0, 0.42, dist);
    camera.lookAt(0, -0.04, 0);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(fit).observe(canvas);
  fit();

  const emit = (type, extra) => heroStage.dispatchEvent(new CustomEvent('v2:pack', { detail: Object.assign({ type }, extra) }));

  // Input: gaze parallax, hover/grab the carton, drag to spin with inertia.
  const pointer = new THREE.Vector2(0, 0), gaze = new THREE.Vector2(0, 0);
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const hero = heroStage.closest('.hero') || heroStage;
  function overPack(e) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1));
    ray.setFromCamera(ndc, camera);
    return ray.intersectObject(pack, true).length > 0;
  }
  let hovering = false;
  hero.addEventListener('pointermove', (e) => {
    const r = heroStage.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1));
    if (coarse || dragging) return;
    const over = overPack(e);
    if (over !== hovering) { hovering = over; heroStage.classList.toggle('is-over-pack', over); emit(over ? 'hover' : 'leave'); }
  });

  let spin = 0, spinVel = 0, dragging = false, lastX = 0, downX = 0, downT = 0, spun = 0, lift = 0;
  canvas.addEventListener('pointerdown', (e) => {
    if (!overPack(e)) return;
    dragging = true; lastX = downX = e.clientX; downT = performance.now(); spun = 0;
    heroStage.classList.add('is-grabbing');
    emit('grab');
  });
  window.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX;
    lastX = e.clientX;
    spinVel = dx * 0.012;
    spin += spinVel;
    spun += Math.abs(dx);
    if (spun > 260) { spun = -1e9; emit('spin'); }
  }, { passive: true });
  window.addEventListener('pointerup', (e) => {
    if (!dragging) return;
    dragging = false;
    heroStage.classList.remove('is-grabbing');
    const tap = Math.abs(e.clientX - downX) < 6 && performance.now() - downT < 350;
    emit(tap ? 'tap' : 'release', { speed: Math.abs(spinVel) });
    if (tap) { burst(); tweens.add(520, (t) => { lift = Math.sin(t * Math.PI); }, { ease: (x) => x }); spinVel = 0.22; }
  });
  function burst() {
    orbit.forEach((o) => { o.pop = 1; });
    tweens.add(800, (t) => orbit.forEach((o) => { o.pop = 1 - t; }));
  }
  heroStage.addEventListener('v2:burst', burst);

  // Intro: the carton drops in with a little settle, then the chips pop out.
  const intro = { pack: 0, chips: 0 };
  function playIntro() {
    tweens.add(950, (t) => { intro.pack = t; }, { ease: easeOutBack });
    tweens.add(1400, (t) => { intro.chips = t; }, { delay: 300 });
  }

  let running = false, raf = 0, last = performance.now(), t = 0;
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now; t += dt;
    tweens.tick(now);
    dpr(now);

    gaze.lerp(pointer, 0.06);
    rig.rotation.y = gaze.x * 0.16;
    rig.rotation.x = -gaze.y * 0.05;

    if (!dragging) { spinVel *= 0.95; spin += spinVel; spin *= 0.988; }
    const ip = intro.pack;
    const hoverLift = hovering || dragging ? 0.05 : 0;
    pack.userData.lift = THREE.MathUtils.lerp(pack.userData.lift || 0, hoverLift, 0.12);
    pack.position.y = packHome.y + (1 - ip) * 1.6 + pack.userData.lift + lift * 0.18 + Math.sin(t * 1.1) * 0.012;
    pack.rotation.set(Math.sin(t * 0.7) * 0.02 - lift * 0.08, -0.42 + Math.sin(t * 0.4) * 0.2 + spin, Math.sin(t * 0.9) * 0.012);
    pack.scale.setScalar(Math.max(0.001, ip));
    const air = pack.position.y - packHome.y;
    contact.material.opacity = ip * Math.max(0, 1 - air * 3);
    contact.scale.setScalar(1 + air * 0.8);
    ground.material.opacity = 0.24 * ip;

    for (let i = 0; i < N; i++) {
      const o = orbit[i];
      const a = o.a + t * o.speed;
      const appear = easeOutBack(clamp01(intro.chips * 1.6 - (i / N) * 0.6));
      const burstK = 1 + o.pop * 0.45;
      dummy.position.set(
        packHome.x + Math.cos(a) * o.rx * burstK,
        o.y + Math.sin(t * 1.3 + o.bob) * 0.06 + o.pop * 0.22,
        Math.sin(a) * o.rz * burstK,
      );
      dummy.rotation.set(o.rot0.x + t * o.spin.x * 0.5 + o.pop * 3, o.rot0.y + t * o.spin.y * 0.5, o.rot0.z + t * o.spin.z * 0.5);
      dummy.scale.setScalar(o.s * appear);
      dummy.updateMatrix();
      chips.setMatrixAt(i, dummy.matrix);
    }
    chips.instanceMatrix.needsUpdate = true;
    renderer.render(scene, camera);
  }

  visibilityGate(heroStage, (on) => {
    if (on && !running) { running = true; last = performance.now(); raf = requestAnimationFrame(frame); }
    if (!on && running) { running = false; cancelAnimationFrame(raf); }
  });

  renderer.compile(scene, camera);
  requestAnimationFrame(() => { heroStage.dataset.mode = '3d'; playIntro(); emit('ready'); });
  canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); cancelAnimationFrame(raf); running = false; heroStage.dataset.mode = 'poster'; });
}

/* ═══ STORY ════════════════════════════════════════════════════════════ */
/* How the chips are made, scrubbed by scroll and synced to the caption cards:
 *   1  a raw Nendran is peeled, strips curling back from the stem end
 *   2  it is pushed through a slicer held over the uruli, and each slice
 *      drops (real gravity, in real time) into the coconut oil and floats
 *   3  the slices fry pale -> gold under bubbling oil, stirred with a jhara
 *   4  they are lifted out and into the pack
 * The background (gradient, doodle pattern, steam, embers) is CSS, driven by
 * the same progress through custom properties on the pinned stage. */
function initStory(assets) {
  const canvas = document.getElementById('story-canvas');
  const bar = document.getElementById('story-bar');
  const pin = storyEl.querySelector('.story-pin');
  storyEl.dataset.mode = '3d';

  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 60);
  const dpr = adaptiveDPR(renderer);
  addEnvironment(renderer, scene, 0.5);
  addLights(scene);
  const fire = new THREE.PointLight(0xffa640, 0, 5, 1.6);
  scene.add(fire);

  const world = new THREE.Group();
  scene.add(world);

  const BLADE_Y = 0.42;          // slicer blade height
  const URULI_Y = -0.78;         // uruli rim reference
  const URULI_S = 0.85;
  const OIL_LOCAL = -0.2;        // oil surface inside the uruli (local units)

  /* ── 1. Banana: flesh + four peel strips + stem ─────────────────── */
  const curve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-1.2, -0.22, 0), new THREE.Vector3(-0.62, 0.17, 0), new THREE.Vector3(0, 0.3, 0),
    new THREE.Vector3(0.62, 0.17, 0), new THREE.Vector3(1.2, -0.22, 0),
  ]);
  const TS = 64, RS = 32, R0 = 0.26;
  const taper = (t) => Math.max(0.12, Math.pow(Math.sin(Math.PI * t), 0.42));
  const frames = curve.computeFrenetFrames(TS, false);
  const cAt = (i) => curve.getPointAt(i / TS);
  const banana = new THREE.Group();
  world.add(banana);

  // flesh (cream, slightly smaller than the skin)
  const flesh = new THREE.Mesh(new THREE.TubeGeometry(curve, TS, R0 * 0.94, RS, false), new THREE.MeshStandardMaterial({ color: 0xEDE2B8, roughness: 0.75 }));
  {
    const pos = flesh.geometry.attributes.position, v = new THREE.Vector3();
    for (let i = 0; i <= TS; i++) {
      const c = cAt(i), k = taper(i / TS);
      for (let j = 0; j <= RS; j++) { const n = i * (RS + 1) + j; v.fromBufferAttribute(pos, n).sub(c).multiplyScalar(k).add(c); pos.setXYZ(n, v.x, v.y, v.z); }
    }
    flesh.geometry.computeVertexNormals();
  }
  banana.add(flesh);

  // skin as four strips; base positions stored so the peel can be recomputed each frame
  const STRIPS = 4, SJ = 9, GAP = 0.04;
  const skinMat = new THREE.MeshStandardMaterial({ color: 0x7F9C34, roughness: 0.55, side: THREE.DoubleSide, transparent: true });
  const strips = [];
  for (let k = 0; k < STRIPS; k++) {
    const a0 = (k / STRIPS) * Math.PI * 2 + GAP, a1 = ((k + 1) / STRIPS) * Math.PI * 2 - GAP;
    const pos = new Float32Array((TS + 1) * SJ * 3), idx = [];
    for (let i = 0; i < TS; i++) for (let j = 0; j < SJ - 1; j++) {
      const a = i * SJ + j, b = a + 1, c = a + SJ, d = c + 1; idx.push(a, c, b, b, c, d);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setIndex(idx);
    const m = new THREE.Mesh(g, skinMat);
    banana.add(m);
    strips.push({ g, pos, a0, a1 });
  }
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.05, 0.22, 10), new THREE.MeshStandardMaterial({ color: 0x4A3B22, roughness: 0.8 }));
  stem.position.set(1.27, -0.24, 0); stem.rotation.z = -0.9;
  banana.add(stem);

  const tmpC = new THREE.Vector3(), tmpR = new THREE.Vector3(), tmpH = new THREE.Vector3(), tmpD = new THREE.Vector3();
  let lastPeel = -1;
  function layoutPeel(peel) {
    if (Math.abs(peel - lastPeel) < 1e-4) return;
    lastPeel = peel;
    const hinge = Math.round((1 - peel * 0.92) * TS);          // strips come away from the stem end (t = 1)
    const hc = cAt(hinge), T = frames.tangents[Math.min(hinge, TS)];
    const segLen = curve.getLength() / TS;
    for (const s of strips) {
      for (let i = 0; i <= TS; i++) {
        const t = i / TS, r = R0 * taper(t) * 1.02;
        const c = i <= hinge ? cAt(i) : hc;
        for (let j = 0; j < SJ; j++) {
          const ang = s.a0 + (s.a1 - s.a0) * (j / (SJ - 1));
          const fi = Math.min(i, hinge);
          tmpR.copy(frames.normals[fi]).multiplyScalar(Math.cos(ang)).addScaledVector(frames.binormals[fi], Math.sin(ang));
          if (i <= hinge) {
            tmpC.copy(cAt(i)).addScaledVector(tmpR, r);
          } else {
            // curl outward and back from the hinge
            const L = (i - hinge) * segLen;
            const th = Math.min(2.5, 0.7 + L * 2.6);
            tmpD.copy(T).multiplyScalar(Math.cos(th)).addScaledVector(tmpR, Math.sin(th));
            tmpH.copy(hc).addScaledVector(tmpR, R0 * taper(hinge / TS) * 1.02);
            tmpC.copy(tmpH).addScaledVector(tmpD, L * 0.9);
          }
          s.pos.set([tmpC.x, tmpC.y, tmpC.z], (i * SJ + j) * 3);
        }
      }
      s.g.attributes.position.needsUpdate = true;
      s.g.computeVertexNormals();
      s.g.computeBoundingSphere();
    }
  }
  layoutPeel(0);

  /* ── 2. Upright peeled banana + slicer ──────────────────────────── */
  const uprightGeo = new THREE.CylinderGeometry(0.19, 0.22, 1, 28, 1);
  uprightGeo.translate(0, 0.5, 0);
  const upright = new THREE.Mesh(uprightGeo, flesh.material);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.19, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2), flesh.material);
  upright.add(cap);
  world.add(upright);

  const slicer = new THREE.Group();
  const wood = new THREE.MeshStandardMaterial({ color: 0xB98552, roughness: 0.62 });
  const steel = new THREE.MeshStandardMaterial({ color: 0xDCE0E4, metalness: 0.9, roughness: 0.22 });
  const boardL = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.05, 0.62), wood); boardL.position.x = -0.42;
  const boardR = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.05, 0.62), wood); boardR.position.x = 0.42;
  const rail1 = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.05, 0.12), wood); rail1.position.set(0, 0, 0.25);
  const rail2 = rail1.clone(); rail2.position.z = -0.25;
  const blade = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.012, 0.42), steel); blade.position.set(0.02, 0.012, 0); blade.rotation.y = 0.35;
  const handle = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.5, 12), new THREE.MeshStandardMaterial({ color: 0x6B4426, roughness: 0.7 }));
  handle.rotation.z = Math.PI / 2; handle.position.x = 0.98;
  slicer.add(boardL, boardR, rail1, rail2, blade, handle);
  slicer.position.y = BLADE_Y;
  world.add(slicer);

  /* ── 3. Uruli, oil, bubbles, ripples, jhara ─────────────────────── */
  const prof = [
    [0, -0.52], [0.5, -0.52], [0.92, -0.42], [1.18, -0.22], [1.3, 0.02], [1.38, 0.05], [1.36, 0.09],
    [1.24, 0.05], [1.12, -0.16], [0.88, -0.35], [0.48, -0.44], [0, -0.45],
  ].map(([x, y]) => new THREE.Vector2(x, y));
  const uruli = new THREE.Group();
  const panMat = new THREE.MeshStandardMaterial({ color: 0xA8692F, metalness: 0.55, roughness: 0.32, side: THREE.DoubleSide });
  uruli.add(new THREE.Mesh(new THREE.LatheGeometry(prof, 72), panMat));
  for (const sx of [-1, 1]) {
    const h = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.035, 10, 24), panMat);
    h.position.set(sx * 1.44, 0.05, 0); h.rotation.y = Math.PI / 2; uruli.add(h);
  }
  const oilMat = new THREE.MeshPhysicalMaterial({ color: 0xD99A22, emissive: 0x6A3300, emissiveIntensity: 0.15, roughness: 0.08, metalness: 0.1, clearcoat: 1, clearcoatRoughness: 0.05, transparent: true, opacity: 0.9 });
  const oilGeo = new THREE.CircleGeometry(1.1, 96, 0, Math.PI * 2);
  const oil = new THREE.Mesh(oilGeo, oilMat);
  oil.rotation.x = -Math.PI / 2; oil.position.y = OIL_LOCAL;
  uruli.add(oil);
  uruli.scale.setScalar(URULI_S);
  world.add(uruli);
  const oilBase = Float32Array.from(oilGeo.attributes.position.array);

  /* Aduppu: the Kerala three-stone hearth, firewood between the stones and
   * live flames licking the bottom of the uruli. Follows the uruli. */
  const hearth = new THREE.Group();
  const URULI_BOTTOM = -0.52 * URULI_S;                     // below the uruli's origin
  const laterite = new THREE.MeshStandardMaterial({ color: 0x9A4F2E, roughness: 0.95 });
  for (let k = 0; k < 3; k++) {
    const a = (k / 3) * Math.PI * 2 + Math.PI / 6;
    const stone = new THREE.Mesh(new THREE.DodecahedronGeometry(0.2, 1), laterite);
    stone.scale.set(1.1, 1.45, 0.95);
    stone.position.set(Math.cos(a) * 0.62, URULI_BOTTOM - 0.24, Math.sin(a) * 0.5);
    stone.rotation.set(rand(), rand() * 6, rand());
    stone.castShadow = true;
    hearth.add(stone);
  }
  const logWood = new THREE.MeshStandardMaterial({ color: 0x5B3A22, roughness: 0.9 });
  const char = new THREE.MeshStandardMaterial({ color: 0x2A1A10, roughness: 1, emissive: 0xFF5A10, emissiveIntensity: 0.6 });
  for (let k = 0; k < 4; k++) {
    const a = -Math.PI / 2 + (k - 1.5) * 0.35;
    const log = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.055, 0.9, 10), k % 2 ? logWood : char);
    log.rotation.set(Math.PI / 2 - 0.15, 0, a + Math.PI / 2);
    log.position.set(Math.cos(a) * 0.35, URULI_BOTTOM - 0.4, Math.sin(a) * -0.35 + 0.15);
    hearth.add(log);
  }
  // flames: additive cones that flicker, an inner gold set and an outer orange set
  const flameGeo = new THREE.ConeGeometry(0.09, 0.42, 10, 1, true);
  flameGeo.translate(0, 0.21, 0);
  const flameMat = (c, o) => new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: o, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, toneMapped: false });
  const flames = [];
  for (let k = 0; k < 11; k++) {
    const inner = k >= 7;
    const m = new THREE.Mesh(flameGeo, flameMat(inner ? 0xFFD35A : 0xFF7A1A, inner ? 0.75 : 0.6));
    const a = rand() * Math.PI * 2, r = inner ? rand() * 0.15 : 0.12 + rand() * 0.28;
    m.position.set(Math.cos(a) * r, URULI_BOTTOM - 0.46, Math.sin(a) * r * 0.8);
    flames.push({ m, base: inner ? 0.75 : 1, ph: rand() * 10, sp: 7 + rand() * 6 });
    hearth.add(m);
  }
  // soft glow under the pan
  const glowTex = (() => { const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d');
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64); r.addColorStop(0, 'rgba(255,170,60,1)'); r.addColorStop(0.4, 'rgba(255,110,20,.45)'); r.addColorStop(1, 'rgba(255,90,10,0)');
    g.fillStyle = r; g.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(c); })();
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, toneMapped: false }));
  glow.scale.set(1.9, 1.1, 1);
  glow.position.set(0, URULI_BOTTOM - 0.28, 0.1);
  hearth.add(glow);
  const embers = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 6, 4), new THREE.MeshBasicMaterial({ color: 0xFFB040, toneMapped: false }), 16);
  embers.frustumCulled = false;
  hearth.add(embers);
  const emberSeed = Array.from({ length: 16 }, () => [rand() * 2 - 1, rand(), rand() * 2 - 1, 0.008 + rand() * 0.01]);
  const fireLight = new THREE.PointLight(0xFF8A2A, 0, 3, 1.4);
  fireLight.position.set(0, URULI_BOTTOM - 0.3, 0.3);
  hearth.add(fireLight);
  world.add(hearth);

  const BUB = coarse ? 60 : 110;
  const bubbles = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshStandardMaterial({ color: 0xFFF2C2, roughness: 0.05, metalness: 0.1, transparent: true, opacity: 0.8 }), BUB);
  bubbles.frustumCulled = false;
  world.add(bubbles);
  const bub = Array.from({ length: BUB }, () => ({ chip: Math.floor(rand() * 1e6), ox: (rand() - 0.5) * 0.36, oz: (rand() - 0.5) * 0.36, ph: rand(), s: 0.012 + rand() * 0.022, sp: 0.8 + rand() * 1.4 }));

  const RIP = 8;
  const ripples = Array.from({ length: RIP }, () => {
    const m = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 40), new THREE.MeshBasicMaterial({ color: 0xFFF0C0, transparent: true, opacity: 0, depthWrite: false }));
    m.rotation.x = -Math.PI / 2; m.visible = false; world.add(m);
    return { m, t: 1 };
  });
  let ripI = 0;
  const ripple = (x, y, z) => { const r = ripples[ripI++ % RIP]; r.m.position.set(x, y + 0.004, z); r.t = 0; r.m.visible = true; };

  // jhara: perforated steel disc on a long handle
  const holes = document.createElement('canvas'); holes.width = holes.height = 256;
  { const c = holes.getContext('2d'); c.fillStyle = '#fff'; c.beginPath(); c.arc(128, 128, 126, 0, Math.PI * 2); c.fill();
    c.globalCompositeOperation = 'destination-out';
    for (let y = 22; y < 240; y += 22) for (let x = 22; x < 240; x += 22) { if ((x - 128) ** 2 + (y - 128) ** 2 < 104 ** 2) { c.beginPath(); c.arc(x + ((y / 22) % 2) * 11, y, 6, 0, Math.PI * 2); c.fill(); } } }
  const holeTex = new THREE.CanvasTexture(holes);
  const jhara = new THREE.Group();
  const disc = new THREE.Mesh(new THREE.CircleGeometry(0.3, 48), new THREE.MeshStandardMaterial({ color: 0xD6DADF, metalness: 0.9, roughness: 0.25, alphaMap: holeTex, alphaTest: 0.5, side: THREE.DoubleSide }));
  disc.rotation.x = -Math.PI / 2;
  const stick = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.022, 1.6, 10), steel);
  stick.rotation.z = -1.0; stick.position.set(0.62, 0.42, 0);
  jhara.add(disc, stick);
  world.add(jhara);

  /* ── Chips: one instanced set from slice to pack ───────────────── */
  const S = coarse ? 18 : 26;
  const chips = makeChipMesh(assets, S);
  const fry = chips.geometry.getAttribute('fry');
  world.add(chips);
  const SLICE_A = 0.33, SLICE_B = 0.53, FALL_G = 3.4;
  const cs = Array.from({ length: S }, (_, i) => ({
    t: SLICE_A + (i / S) * (SLICE_B - SLICE_A),
    state: 0,                         // 0 waiting, 1 falling, 2 floating
    p: new THREE.Vector3(), v: new THREE.Vector3(), rot: new THREE.Euler(), spin: new THREE.Vector3(),
    r: 0.15 + Math.sqrt(rand()) * 0.62, a: rand() * Math.PI * 2, bob: rand() * 6, flat: new THREE.Euler((rand() - 0.5) * 0.18, rand() * 6, (rand() - 0.5) * 0.18),
    lift: 0.76 + (i / S) * 0.11,
  }));

  /* ── 4. Pack + turmeric/salt dust ───────────────────────────────── */
  const pack = makePack(assets);
  world.add(pack);
  const DUST = coarse ? 160 : 320;
  const dustGeo = new THREE.BufferGeometry();
  const dustPos = new Float32Array(DUST * 3);
  const dustSeed = Array.from({ length: DUST }, () => [rand() * 2 - 1, rand(), rand() * 2 - 1, rand()]);
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
  const dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({ color: 0xE9A516, size: 0.03, transparent: true, opacity: 0, depthWrite: false }));
  dust.frustumCulled = false;
  world.add(dust);

  /* Layout: captions left on desktop, at the bottom on phones. */
  let portrait = false;
  function fit() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    portrait = camera.aspect < 0.95;
    const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const needW = portrait ? 3.2 : 3.9, needH = 3.7;
    const dist = Math.max(needH / 2 / t, needW / 2 / (t * camera.aspect));
    camera.position.set(0, 1.3, dist);
    camera.lookAt(0, -0.22, 0);
    camera.updateProjectionMatrix();
    world.position.set(portrait ? 0 : Math.min(1.15, (camera.aspect - 1) * 1.2 + 0.45), portrait ? 0.7 : 0.05, 0);
    world.scale.setScalar(portrait ? 0.8 : 1);
  }
  new ResizeObserver(fit).observe(canvas);
  fit();

  /* Scroll -> progress, keyed to the caption cards. */
  const steps = [...storyEl.querySelectorAll('.story-step')];
  const BEATS = [0.13, 0.4, 0.62, 0.885];
  let anchors = [];
  function measure() {
    const vh = window.innerHeight, y = window.scrollY;
    anchors = steps.map((st) => {
      const card = st.querySelector('.step-card').getBoundingClientRect();
      return portrait ? card.top + y - vh * 0.6 : card.top + y + card.height / 2 - vh / 2;
    });
    const r = storyEl.getBoundingClientRect();
    anchors.start = r.top + y;
    anchors.end = r.top + y + r.height - vh;
  }
  let target = 0, p = 0, active = -1, completed = false;
  function readProgress() {
    if (!anchors.length) measure();
    const y = window.scrollY;
    const xs = [anchors.start, ...anchors, anchors.end];
    const ys = [0, ...BEATS, 1];
    let v = 0;
    if (y >= xs[xs.length - 1]) v = 1;
    else for (let i = 0; i < xs.length - 1; i++) {
      if (y < xs[i + 1]) { v = ys[i] + (ys[i + 1] - ys[i]) * clamp01((y - xs[i]) / Math.max(1, xs[i + 1] - xs[i])); break; }
    }
    target = clamp01(v);
    let best = 0;
    BEATS.forEach((b, i) => { if (Math.abs(target - b) < Math.abs(target - BEATS[best])) best = i; });
    if (best !== active) {
      active = best;
      steps.forEach((st, i) => st.classList.toggle('is-active', i === best));
      storyEl.dispatchEvent(new CustomEvent('v2:beat', { detail: { i: best } }));
    }
    if (!completed && target > 0.965) { completed = true; storyEl.dispatchEvent(new CustomEvent('v2:story-complete')); }
    if (completed && target < 0.9) completed = false;
  }
  window.addEventListener('scroll', readProgress, { passive: true });
  window.addEventListener('resize', () => { measure(); readProgress(); });
  window.addEventListener('load', () => { measure(); readProgress(); });
  measure();
  readProgress();
  p = target;

  /* Background palette per beat (sRGB), interpolated and handed to CSS. */
  const PAL = [
    { at: 0.0, base: [246, 241, 220], a: [176, 196, 98, 0.55], b: [255, 214, 92, 0.32], steam: 0, ember: 0, pat: 0.7, vig: 0.05 },
    { at: 0.38, base: [244, 236, 205], a: [233, 194, 2, 0.32], b: [176, 196, 98, 0.4], steam: 0.25, ember: 0, pat: 0.6, vig: 0.08 },
    { at: 0.6, base: [44, 26, 12], a: [255, 136, 36, 0.55], b: [255, 196, 70, 0.3], steam: 1, ember: 1, pat: 0.16, vig: 0.45 },
    { at: 0.72, base: [52, 31, 14], a: [255, 150, 40, 0.5], b: [255, 210, 90, 0.3], steam: 0.9, ember: 0.8, pat: 0.18, vig: 0.4 },
    { at: 0.9, base: [251, 243, 214], a: [233, 194, 2, 0.45], b: [134, 170, 78, 0.35], steam: 0, ember: 0, pat: 0.6, vig: 0.05 },
  ];
  const mix = (x, y, k) => x + (y - x) * k;
  function palette(q) {
    let i = 0; while (i < PAL.length - 2 && q > PAL[i + 1].at) i++;
    const A = PAL[i], B = PAL[i + 1], k = easeInOut(clamp01((q - A.at) / (B.at - A.at)));
    const rgb = (u, v) => u.map((c, n) => (n < 3 ? Math.round(mix(c, v[n], k)) : mix(c, v[n], k).toFixed(3)));
    const st = pin.style;
    st.setProperty('--sbase', `rgb(${rgb(A.base, B.base).join(',')})`);
    st.setProperty('--sga', `rgba(${rgb(A.a, B.a).join(',')})`);
    st.setProperty('--sgb', `rgba(${rgb(A.b, B.b).join(',')})`);
    for (const key of ['steam', 'ember', 'pat', 'vig']) st.setProperty('--' + key, mix(A[key], B[key], k).toFixed(3));
    return mix(A.ember, B.ember, k);
  }

  const dummy = new THREE.Object3D(), mouth = new THREE.Vector3(), q1 = new THREE.Quaternion(), q2 = new THREE.Quaternion();
  let raf = 0, running = false, last = performance.now(), time = 0;

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.12, (now - last) / 1000);   // real time, even on slow devices
    last = now; time += dt;
    dpr(now);
    p += (target - p) * Math.min(1, dt * 8);
    bar.style.setProperty('--p', p.toFixed(4));
    const heat = palette(p);

    // Banana: turn, peel, strips fall away, then stand up into the slicer.
    const peel = easeInOut(seg(p, 0.06, 0.24));
    const drop = seg(p, 0.23, 0.31);
    const stand = easeInOut(seg(p, 0.26, 0.34));
    layoutPeel(peel);
    banana.visible = stand < 1;
    banana.position.set(-0.15 * stand, 0.32 + stand * (BLADE_Y + 0.45 - 0.32) + Math.sin(time) * 0.03 * (1 - stand), 0);
    banana.rotation.set(0.25 * (1 - stand), (Math.sin(time * 0.45) * 0.35 - 0.15) * (1 - stand), -Math.PI / 2 * stand * 0.98);
    banana.scale.setScalar(0.78 - stand * 0.14);
    skinMat.opacity = 1 - drop;
    strips.forEach((s, k) => { banana.children[k + 1].position.set(0, -drop * 1.8, (k % 2 ? 1 : -1) * drop * 0.6); });

    // Upright flesh shortens as it is sliced.
    const sliceT = seg(p, SLICE_A, SLICE_B);
    upright.visible = stand >= 1 && sliceT < 0.999;
    upright.position.set(0, BLADE_Y + 0.03, 0);
    upright.scale.set(1, Math.max(0.02, 0.95 * (1 - sliceT)), 1);
    cap.scale.set(1, 1 / Math.max(0.02, 0.95 * (1 - sliceT)), 1);
    cap.position.y = 1;
    // a little push-and-saw motion while slicing
    upright.position.x = sliceT > 0 && sliceT < 1 ? Math.sin(time * 14) * 0.012 : 0;

    // Slicer slides in over the uruli, leaves after the slicing.
    const slIn = easeOutCubic(seg(p, 0.26, 0.34)), slOut = easeInOut(seg(p, 0.55, 0.62));
    slicer.visible = slIn > 0 && slOut < 1;
    slicer.position.set(-2.6 * (1 - slIn) + 2.6 * slOut, BLADE_Y, 0);
    slicer.rotation.set(0, 0, 0.04);

    // Uruli rises in as the banana stands up, sinks before the seal.
    const uIn = easeOutCubic(seg(p, 0.2, 0.33)), uOut = easeInOut(seg(p, 0.86, 0.96));
    uruli.visible = uIn > 0 && uOut < 1;
    uruli.position.set(0, URULI_Y - (1 - uIn) * 2.6 - uOut * 2.8, 0);
    uruli.rotation.y = time * 0.05;
    const oilY = uruli.position.y + OIL_LOCAL * URULI_S;
    hearth.visible = uruli.visible;
    hearth.position.copy(uruli.position);
    const frying = clamp01(seg(p, 0.34, 0.5) * 0.6 + seg(p, 0.5, 0.6) * 0.4) * (1 - seg(p, 0.8, 0.9));
    oilMat.emissiveIntensity = 0.15 + heat * 0.45 + Math.sin(time * 9) * 0.03 * frying;
    fire.position.set(world.position.x, oilY + 1.1, 1.4);
    fire.intensity = heat * 1.8;
    // the stove is lit as soon as the uruli is; it roars while frying
    const blaze = 0.45 + 0.55 * Math.max(frying, heat);
    for (const f of flames) {
      const n = 0.75 + 0.25 * Math.sin(time * f.sp + f.ph) + 0.12 * Math.sin(time * f.sp * 2.3 + f.ph * 1.7);
      f.m.scale.set(1 - 0.15 * Math.sin(time * f.sp * 1.3 + f.ph), f.base * n * (0.6 + blaze * 0.7), 1);
      f.m.rotation.z = Math.sin(time * 3 + f.ph) * 0.12;
    }
    glow.material.opacity = 0.55 + 0.4 * blaze + Math.sin(time * 11) * 0.05;
    fireLight.intensity = (2.2 + Math.sin(time * 13) * 0.4) * blaze;
    for (let i = 0; i < emberSeed.length; i++) {
      const e = emberSeed[i], life = (time * 0.55 + e[1]) % 1;
      dummy.position.set(e[0] * 0.35 + Math.sin(time * 2 + i) * 0.04, URULI_BOTTOM - 0.4 + life * 0.5, e[2] * 0.25 + 0.15);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.setScalar(e[3] * Math.sin(life * Math.PI) * blaze);
      dummy.updateMatrix();
      embers.setMatrixAt(i, dummy.matrix);
    }
    embers.instanceMatrix.needsUpdate = true;
    // oil surface shimmer
    { const pos = oilGeo.attributes.position; const amp = 0.006 + frying * 0.012;
      for (let i = 0; i < pos.count; i++) { const x = oilBase[i * 3], y = oilBase[i * 3 + 1];
        pos.setZ(i, Math.sin(x * 9 + time * 3) * amp + Math.cos(y * 11 - time * 2.4) * amp); }
      pos.needsUpdate = true; }

    // Jhara: dips and stirs during the fry, then lifts away.
    const jIn = easeOutCubic(seg(p, 0.52, 0.58)), jOut = easeInOut(seg(p, 0.76, 0.84));
    jhara.visible = jIn > 0 && jOut < 1;
    const stirA = time * 1.1;
    jhara.position.set(Math.cos(stirA) * 0.45 + (1 - jIn) * 2.4 + jOut * 0.4, oilY + 0.02 + (1 - jIn) * 1.2 + jOut * 1.4 + Math.max(0, Math.sin(time * 2.2)) * 0.06, Math.sin(stirA) * 0.3);
    jhara.rotation.set(0, -stirA * 0.3, 0.12);
    const stir = jIn * (1 - jOut);

    // Ripples.
    for (const r of ripples) {
      if (!r.m.visible) continue;
      r.t += dt / 0.8;
      if (r.t >= 1) { r.m.visible = false; continue; }
      r.m.scale.setScalar(0.06 + r.t * 0.32);
      r.m.material.opacity = 0.55 * (1 - r.t);
    }

    // Pack rises for the seal.
    const pIn = easeOutBack(seg(p, 0.76, 0.87));
    const seal = Math.sin(Math.PI * seg(p, 0.93, 0.99)) * 0.06;
    pack.visible = pIn > 0.001;
    pack.position.set(0, -0.05 - (1 - pIn) * 2.4, -0.15);
    pack.rotation.set(0.04, -0.32 + Math.sin(time * 0.6) * 0.25 + (1 - pIn) * 1.4, 0);
    pack.scale.set(pIn * (1 + seal), pIn * (1 - seal * 0.5), pIn);
    mouth.set(0, pack.position.y + (pack.userData.height || 1.3) * 0.5 * pIn, -0.15);

    // Chips.
    for (let i = 0; i < S; i++) {
      const c = cs[i];
      // spawn / un-spawn with the scroll
      if (p >= c.t && c.state === 0) {
        c.state = 1;
        c.p.set((rand() - 0.5) * 0.1, BLADE_Y - 0.04, (rand() - 0.5) * 0.1);
        c.v.set(Math.cos(c.a) * c.r * 0.55, -0.15, Math.sin(c.a) * c.r * 0.55);
        c.rot.set(Math.PI / 2 * 0.2, rand() * 6, 0);
        c.spin.set((rand() - 0.5) * 9, (rand() - 0.5) * 5, (rand() - 0.5) * 9);
      } else if (p < c.t - 0.004 && c.state !== 0) c.state = 0;

      const lift = easeInOut(seg(p, c.lift, c.lift + 0.06));
      let scale = 0.92;
      if (c.state === 0) { scale = 0; dummy.position.set(0, BLADE_Y, 0); }
      else if (c.state === 1) {
        c.v.y -= FALL_G * dt;
        c.p.addScaledVector(c.v, dt);
        c.rot.x += c.spin.x * dt; c.rot.y += c.spin.y * dt; c.rot.z += c.spin.z * dt;
        if (c.p.y <= oilY + 0.006) { c.state = 2; c.p.y = oilY + 0.006; ripple(c.p.x, oilY, c.p.z); c.a = Math.atan2(c.p.z, c.p.x); c.r = Math.min(0.78, Math.hypot(c.p.x, c.p.z)); }
        dummy.position.copy(c.p);
        dummy.rotation.copy(c.rot);
      }
      if (c.state === 2) {
        // float: slow convection swirl, faster while the jhara stirs
        c.a += dt * (0.12 + stir * 0.9);
        const y = oilY + 0.008 + Math.sin(time * 2.2 + c.bob) * 0.006;
        dummy.position.set(Math.cos(c.a) * c.r, y, Math.sin(c.a) * c.r);
        dummy.rotation.set(c.flat.x + Math.sin(time * 1.7 + c.bob) * 0.06, c.flat.y + c.a, c.flat.z);
      }
      if (c.state === 2 && lift > 0) {
        // out of the oil, up and into the pack
        const from = dummy.position.clone();
        const ang = c.a + lift * Math.PI * 2.2, rad = (1 - lift) * c.r;
        const to = new THREE.Vector3(mouth.x + Math.cos(ang) * rad, mouth.y, mouth.z + Math.sin(ang) * rad);
        dummy.position.lerpVectors(from, to, lift);
        dummy.position.y += Math.sin(lift * Math.PI) * 0.9;
        q1.copy(dummy.quaternion.setFromEuler(dummy.rotation));
        q2.setFromEuler(new THREE.Euler(lift * 7, lift * 4, lift * 2));
        dummy.quaternion.copy(q1).multiply(q2);
        dummy.rotation.setFromQuaternion(dummy.quaternion);
        scale *= 1 - Math.pow(lift, 3);
      }
      dummy.scale.setScalar(Math.max(0.0001, scale));
      dummy.updateMatrix();
      chips.setMatrixAt(i, dummy.matrix);
      // raw -> golden through the fry
      fry.setX(i, clamp01((p - 0.5 - (i / S) * 0.06) / 0.14));
    }
    chips.instanceMatrix.needsUpdate = true;
    fry.needsUpdate = true;

    // Bubbles cluster around the floating chips.
    for (let i = 0; i < BUB; i++) {
      const b = bub[i], c = cs[b.chip % S];
      const life = (time * b.sp + b.ph) % 1;
      const on = c.state === 2 && uruli.visible ? frying : 0;
      const cx = Math.cos(c.a) * c.r + b.ox, cz = Math.sin(c.a) * c.r + b.oz;
      dummy.position.set(cx, oilY + 0.004 + life * 0.03, cz);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.setScalar(b.s * Math.sin(life * Math.PI) * on);
      dummy.updateMatrix();
      bubbles.setMatrixAt(i, dummy.matrix);
    }
    bubbles.instanceMatrix.needsUpdate = true;

    // Turmeric and salt settle over the chips as they leave the oil.
    const dIn = Math.sin(Math.PI * seg(p, 0.7, 0.86));
    dust.material.opacity = dIn * 0.85;
    if (dIn > 0) {
      for (let i = 0; i < DUST; i++) {
        const d = dustSeed[i];
        dustPos[i * 3] = d[0] * 0.9; dustPos[i * 3 + 1] = oilY + 1.6 - ((time * 0.25 + d[3]) % 1) * 1.6; dustPos[i * 3 + 2] = d[2] * 0.7;
      }
      dustGeo.attributes.position.needsUpdate = true;
    }

    renderer.render(scene, camera);
  }

  visibilityGate(storyEl, (on) => {
    if (on && !running) { running = true; last = performance.now(); readProgress(); raf = requestAnimationFrame(frame); }
    if (!on && running) { running = false; cancelAnimationFrame(raf); }
  });
  canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); cancelAnimationFrame(raf); running = false; storyEl.dataset.mode = 'static'; });
}
