/* Varthaai home v2: WebGL scenes (hero + scroll story).
 *
 * Progressive enhancement only. The page is complete without this file; it
 * upgrades the hero poster and the story cards to live 3D on devices that can
 * afford it, and stays out of the way everywhere else (reduced motion, no
 * WebGL, Save-Data, very low memory).
 */
import * as THREE from 'three';

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
  r.toneMappingExposure = 1.05;
  r.setClearColor(0x000000, 0);
  return r;
}

function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0xfff6dc, 0xb0c462, 1.6));
  const key = new THREE.DirectionalLight(0xfff1cc, 2.4);
  key.position.set(3, 4, 5);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xe9c202, 1.4);
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
  const assets = { pouch: tex(d.pouch), chip: tex(d.chip), mascot: tex(d.mascot) };
  manager.onLoad = () => onReady(assets);
  manager.onError = (url) => console.warn('[v2] texture failed', url);
}

/* Stand-up pouch: a subdivided box, pillowed across its width and crimped
 * flat at the top seal. Front and back carry the real label art. */
function makePouch(assets) {
  const W = 1, H = 1.43, D = 0.34;
  const g = new THREE.BoxGeometry(W, H, D, 28, 36, 6);
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const nx = pos.getX(i) / (W / 2), ny = pos.getY(i) / (H / 2);
    let f = Math.sqrt(Math.max(0, 1 - Math.pow(Math.abs(nx), 5)));
    if (ny > 0.8) f *= Math.max(0, 1 - (ny - 0.8) / 0.11);
    if (ny < -0.85) f = Math.max(f, 0.55 * (1 - Math.pow(Math.abs(nx), 6)));
    pos.setZ(i, pos.getZ(i) * Math.max(f, 0.03));
  }
  g.computeVertexNormals();
  const label = new THREE.MeshPhysicalMaterial({ map: assets.pouch, roughness: 0.42, clearcoat: 0.55, clearcoatRoughness: 0.35 });
  const side = new THREE.MeshStandardMaterial({ color: 0xe6dfc4, roughness: 0.6 });
  return new THREE.Mesh(g, [side, side, side, side, label, label]);
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
function initHero(assets) {
  const canvas = document.getElementById('hero-canvas');
  const bubble = document.getElementById('mascot-bubble');
  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
  const tweens = createTweens();
  const dpr = adaptiveDPR(renderer);
  addLights(scene);

  const rig = new THREE.Group();
  scene.add(rig);

  const pouchPivot = new THREE.Group();
  pouchPivot.position.set(0.32, 0.02, 0);
  rig.add(pouchPivot);
  const pouch = makePouch(assets);
  pouchPivot.add(pouch);
  const shadow = softShadow(1.6, 0.45);
  shadow.position.set(0.32, -0.8, 0);
  rig.add(shadow);

  // Mascot: the real artwork as a cut-out standee that reacts to the visitor.
  const mascotPivot = new THREE.Group();
  mascotPivot.position.set(-0.78, -0.38, 0.45);
  rig.add(mascotPivot);
  const mascot = new THREE.Mesh(
    new THREE.PlaneGeometry(0.8, 0.736),
    new THREE.MeshBasicMaterial({ map: assets.mascot, transparent: true, alphaTest: 0.05, side: THREE.DoubleSide, toneMapped: false }),
  );
  mascot.position.y = 0.368; // pivot at the feet, so squash and hop read right
  mascotPivot.add(mascot);
  const mShadow = softShadow(0.7, 0.35);
  mShadow.position.set(-0.78, -0.375, 0.45);
  rig.add(mShadow);

  // Chips drifting around the pouch.
  const N = coarse ? 18 : 28;
  const chips = makeChipMesh(assets, N);
  rig.add(chips);
  const orbit = Array.from({ length: N }, (_, i) => ({
    a: (i / N) * Math.PI * 2 + rand() * 0.4,
    rx: 0.78 + rand() * 0.32,
    rz: 0.55 + rand() * 0.35,
    y: -0.55 + rand() * 1.2,
    speed: 0.12 + rand() * 0.12,
    bob: rand() * Math.PI * 2,
    spin: new THREE.Vector3(rand() * 2 - 1, rand() * 2 - 1, rand() * 2 - 1).multiplyScalar(1.4),
    rot0: new THREE.Euler(rand() * 6, rand() * 6, rand() * 6),
    s: 0.55 + rand() * 0.38,
    pop: 0,
  }));
  const dummy = new THREE.Object3D();

  // Layout: fit the pouch + mascot into whatever box the stage has.
  function fit() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const needH = 2.05, needW = 3.0;
    const dist = Math.max(needH / 2 / t, needW / 2 / (t * camera.aspect));
    camera.position.set(0, 0.1, dist);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(fit).observe(canvas);
  fit();

  // Input: gaze (anywhere over the hero), drag to spin the pouch, tap the mascot.
  const pointer = new THREE.Vector2(0, 0);
  const gaze = new THREE.Vector2(0, 0);
  const hero = heroStage.closest('.hero') || heroStage;
  hero.addEventListener('pointermove', (e) => {
    const r = heroStage.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1));
    if (!coarse) heroStage.classList.toggle('is-over-mascot', hitMascot(e));
  });

  const ray = new THREE.Raycaster();
  function hitMascot(e) {
    const r = canvas.getBoundingClientRect();
    const v = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1));
    ray.setFromCamera(v, camera);
    return ray.intersectObject(mascot).length > 0;
  }

  let spin = 0, spinVel = 0, dragging = false, lastX = 0, downX = 0, downT = 0;
  canvas.style.pointerEvents = 'auto';
  heroStage.addEventListener('pointerdown', (e) => {
    dragging = true; lastX = downX = e.clientX; downT = performance.now();
  });
  window.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX;
    lastX = e.clientX;
    spinVel = dx * 0.012;
    spin += spinVel;
  }, { passive: true });
  window.addEventListener('pointerup', (e) => {
    if (!dragging) return;
    dragging = false;
    const moved = Math.abs(e.clientX - downX);
    if (moved < 6 && performance.now() - downT < 400) {
      if (hitMascot(e)) pokeMascot();
      else if (e.target.closest && e.target.closest('#hero-stage')) wiggleChips();
    }
  });

  // Speech bubble, pinned above the mascot's head in screen space.
  const lines = [
    'Hey! That tickles.',
    'Fried in pure coconut oil. Every single time.',
    'Nendran banana, turmeric, salt. That\'s me.',
    'Drag the pouch around. Go on.',
    'Tap Order now and I\'ll hop in.',
  ];
  let lineIdx = 0, bubbleTimer;
  function say(text, ms = 2600) {
    bubble.textContent = text;
    bubble.classList.add('is-on');
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubble.classList.remove('is-on'), ms);
  }
  const head = new THREE.Vector3();
  function placeBubble() {
    mascot.getWorldPosition(head);
    head.y += 0.42;
    head.project(camera);
    bubble.style.left = ((head.x + 1) / 2) * canvas.clientWidth + 'px';
    bubble.style.top = ((1 - head.y) / 2) * canvas.clientHeight + 'px';
  }

  // Mascot behaviours.
  const m = { squash: 0, hop: 0, flinch: 0, wave: 0 };
  function pokeMascot() {
    crunch();
    if (navigator.vibrate) navigator.vibrate(15);
    say(lines[lineIdx++ % lines.length]);
    tweens.add(520, (t) => { m.flinch = Math.sin(t * Math.PI) * (1 - t * 0.3); });
  }
  function hopMascot() {
    tweens.add(620, (t) => { m.hop = Math.sin(t * Math.PI); m.squash = Math.sin(t * Math.PI * 2) * 0.12; }, { ease: (x) => x });
  }
  function wiggleChips() {
    orbit.forEach((o) => { o.pop = 1; });
    tweens.add(700, (t) => orbit.forEach((o) => { o.pop = 1 - t; }));
  }
  document.querySelectorAll('[data-hop]').forEach((el) => {
    el.addEventListener('mouseenter', hopMascot);
    el.addEventListener('focus', hopMascot);
  });

  // Intro: pouch drops in, chips pop out one by one, mascot waves hello.
  let intro = { pouch: 0, chips: 0 };
  function playIntro() {
    tweens.add(900, (t) => { intro.pouch = t; }, { ease: easeOutBack });
    tweens.add(1400, (t) => { intro.chips = t; }, { delay: 250 });
    tweens.add(1600, (t) => { m.wave = Math.sin(t * Math.PI * 4) * (1 - t); }, { delay: 600, ease: (x) => x });
    setTimeout(() => say('Hi! I\'m a Varthaai chip. Tap me.', 3200), 1100);
  }

  // Loop.
  let running = false, raf = 0, last = performance.now(), introPlayed = false;
  const clock = { t: 0 };
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    clock.t += dt;
    const t = clock.t;
    tweens.tick(now);
    dpr(now);

    gaze.lerp(pointer, 0.06);
    rig.rotation.y = gaze.x * 0.18;
    rig.rotation.x = -gaze.y * 0.06;

    // Pouch: drop-in, idle sway, drag spin with inertia.
    if (!dragging) { spinVel *= 0.94; spin += spinVel; spin *= 0.985; }
    const ip = intro.pouch;
    pouchPivot.position.y = 0.02 + (1 - ip) * 1.6 + Math.sin(t * 1.1) * 0.035;
    pouchPivot.rotation.set(Math.sin(t * 0.7) * 0.04, -0.32 + Math.sin(t * 0.45) * 0.22 + spin, 0.05 + Math.sin(t * 0.9) * 0.02);
    pouchPivot.scale.setScalar(Math.max(0.001, ip));
    shadow.material.opacity = ip * (0.9 - Math.sin(t * 1.1) * 0.08);

    // Mascot: bob, squash, gaze, wave, flinch, hop.
    const bob = Math.sin(t * 2.4) * 0.025;
    mascotPivot.position.y = -0.38 + bob + m.hop * 0.42;
    const sq = 1 + Math.sin(t * 4.8) * 0.015 + m.squash - m.flinch * 0.12;
    mascotPivot.scale.set(1 / Math.sqrt(sq), sq, 1);
    mascotPivot.rotation.y = THREE.MathUtils.clamp(gaze.x * 0.45, -0.38, 0.38);
    mascotPivot.rotation.x = THREE.MathUtils.clamp(-gaze.y * 0.15, -0.15, 0.15);
    mascotPivot.rotation.z = Math.sin(t * 1.3) * 0.04 + m.wave * 0.22 - m.flinch * 0.25;
    mShadow.scale.setScalar(1 - m.hop * 0.4);

    // Chips.
    for (let i = 0; i < N; i++) {
      const o = orbit[i];
      const a = o.a + t * o.speed;
      const appear = easeOutBack(clamp01(intro.chips * 1.6 - i / N * 0.6));
      const burst = 1 + o.pop * 0.35;
      dummy.position.set(
        0.32 + Math.cos(a) * o.rx * burst,
        o.y + Math.sin(t * 1.3 + o.bob) * 0.06 + o.pop * 0.15,
        Math.sin(a) * o.rz * burst,
      );
      dummy.rotation.set(o.rot0.x + t * o.spin.x * 0.5, o.rot0.y + t * o.spin.y * 0.5, o.rot0.z + t * o.spin.z * 0.5);
      dummy.scale.setScalar(o.s * appear);
      dummy.updateMatrix();
      chips.setMatrixAt(i, dummy.matrix);
    }
    chips.instanceMatrix.needsUpdate = true;

    renderer.render(scene, camera);
    if (bubble.classList.contains('is-on')) placeBubble();
  }

  visibilityGate(heroStage, (on) => {
    if (on && !running) { running = true; last = performance.now(); raf = requestAnimationFrame(frame); }
    if (!on && running) { running = false; cancelAnimationFrame(raf); }
  });

  // Hand over from the poster once the first frame is on the canvas.
  renderer.compile(scene, camera);
  requestAnimationFrame(() => {
    heroStage.dataset.mode = '3d';
    if (!introPlayed) { introPlayed = true; playIntro(); }
  });

  canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); cancelAnimationFrame(raf); running = false; heroStage.dataset.mode = 'poster'; });
}

/* ═══ STORY ════════════════════════════════════════════════════════════ */
function initStory(assets) {
  const canvas = document.getElementById('story-canvas');
  const bar = document.getElementById('story-bar');
  storyEl.dataset.mode = '3d';

  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 60);
  const dpr = adaptiveDPR(renderer);
  addLights(scene);
  const glow = new THREE.PointLight(0xffb84a, 0, 6, 1.6);
  glow.position.set(0, 1.1, 1.2);
  scene.add(glow);

  const world = new THREE.Group();
  scene.add(world);

  /* Banana: a curved tube, tapered to the tips, with Nendran's soft ridges. */
  const curve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-1.25, -0.25, 0), new THREE.Vector3(-0.65, 0.18, 0), new THREE.Vector3(0, 0.32, 0),
    new THREE.Vector3(0.65, 0.18, 0), new THREE.Vector3(1.25, -0.25, 0),
  ]);
  const TS = 72, RS = 30;
  const bGeo = new THREE.TubeGeometry(curve, TS, 0.27, RS, false);
  {
    const pos = bGeo.attributes.position, c = new THREE.Vector3(), v = new THREE.Vector3();
    for (let i = 0; i <= TS; i++) {
      const t = i / TS;
      curve.getPointAt(t, c);
      const taper = Math.max(0.1, Math.pow(Math.sin(Math.PI * t), 0.42));
      for (let j = 0; j <= RS; j++) {
        const k = i * (RS + 1) + j;
        v.fromBufferAttribute(pos, k).sub(c);
        const ridge = 1 + 0.06 * Math.cos((j / RS) * Math.PI * 2 * 5);
        v.multiplyScalar(taper * ridge).add(c);
        pos.setXYZ(k, v.x, v.y, v.z);
      }
    }
    bGeo.computeVertexNormals();
  }
  const banana = new THREE.Group();
  banana.add(new THREE.Mesh(bGeo, new THREE.MeshStandardMaterial({ color: 0x7f9c34, roughness: 0.5 })));
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.05, 0.22, 10), new THREE.MeshStandardMaterial({ color: 0x4a3b22, roughness: 0.8 }));
  stem.position.set(1.3, -0.24, 0); stem.rotation.z = -0.9;
  banana.add(stem);
  world.add(banana);

  /* Uruli: a wide, shallow bell-metal pan, with coconut oil inside. */
  const prof = [
    [0, -0.52], [0.5, -0.52], [0.92, -0.42], [1.18, -0.22], [1.3, 0.02], [1.38, 0.05], [1.36, 0.09],
    [1.24, 0.05], [1.12, -0.16], [0.88, -0.35], [0.48, -0.44], [0, -0.45],
  ].map(([x, y]) => new THREE.Vector2(x, y));
  const uruli = new THREE.Group();
  const pan = new THREE.Mesh(new THREE.LatheGeometry(prof, 64), new THREE.MeshStandardMaterial({ color: 0xa8692f, metalness: 0.45, roughness: 0.35, side: THREE.DoubleSide }));
  uruli.add(pan);
  for (const s of [-1, 1]) {
    const h = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.035, 10, 24), pan.material);
    h.position.set(s * 1.44, 0.05, 0); h.rotation.y = Math.PI / 2;
    uruli.add(h);
  }
  const oilMat = new THREE.MeshStandardMaterial({ color: 0xe0a52a, emissive: 0x7a3f00, emissiveIntensity: 0, roughness: 0.12, metalness: 0.15, transparent: true, opacity: 0.92 });
  const oil = new THREE.Mesh(new THREE.CircleGeometry(1.08, 64), oilMat);
  oil.rotation.x = -Math.PI / 2; oil.position.y = -0.2;
  uruli.add(oil);
  const BUB = 36;
  const bubbles = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshStandardMaterial({ color: 0xfff1b8, roughness: 0.1, transparent: true, opacity: 0.75 }), BUB);
  bubbles.frustumCulled = false;
  uruli.add(bubbles);
  const bubData = Array.from({ length: BUB }, () => ({ r: Math.sqrt(rand()) * 0.95, a: rand() * Math.PI * 2, ph: rand(), s: 0.015 + rand() * 0.03 }));
  world.add(uruli);

  /* Slices → chips. */
  const S = coarse ? 16 : 22;
  const slices = makeChipMesh(assets, S);
  const fry = slices.geometry.getAttribute('fry');
  world.add(slices);
  const sd = Array.from({ length: S }, (_, i) => {
    const r = Math.sqrt(rand()) * 0.85, a = rand() * Math.PI * 2;
    return {
      fanA: (i / (S - 1) - 0.5) * Math.PI * 0.95,
      bowl: new THREE.Vector3(Math.cos(a) * r, -0.16 + rand() * 0.06, Math.sin(a) * r),
      tilt: new THREE.Euler((rand() - 0.5) * 0.5, rand() * 6, (rand() - 0.5) * 0.5),
      swirl: rand() * Math.PI * 2,
      delay: rand() * 0.3,
    };
  });

  /* Pouch for the final beat, and a turmeric-and-salt dusting. */
  const pouch = makePouch(assets);
  const pouchPivot = new THREE.Group();
  pouchPivot.add(pouch);
  world.add(pouchPivot);
  const DUST = coarse ? 160 : 320;
  const dustGeo = new THREE.BufferGeometry();
  const dustPos = new Float32Array(DUST * 3);
  const dustSeed = Array.from({ length: DUST }, () => [rand() * 2 - 1, rand(), rand() * 2 - 1, rand()]);
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
  const dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({ color: 0xe9a516, size: 0.03, transparent: true, opacity: 0, depthWrite: false }));
  dust.frustumCulled = false;
  world.add(dust);

  /* Layout: captions sit left on desktop and at the bottom on phones. */
  let portrait = false;
  function fit() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    portrait = camera.aspect < 0.95;
    const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const needW = portrait ? 3.1 : 3.6, needH = 3.0;
    const dist = Math.max(needH / 2 / t, needW / 2 / (t * camera.aspect));
    camera.position.set(0, 1.0, dist);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    world.position.set(portrait ? 0 : Math.min(1.15, (camera.aspect - 1) * 1.2 + 0.45), portrait ? 0.75 : 0, 0);
    world.scale.setScalar(portrait ? 0.82 : 1);
  }
  new ResizeObserver(fit).observe(canvas);
  fit();

  /* Scroll → progress, keyed to the caption cards so each beat plays while its
   * card is on screen. Phones read a card in the lower part of the screen
   * (the scene sits above it); desktop reads it at the vertical centre. */
  const steps = [...storyEl.querySelectorAll('.story-step')];
  const BEATS = [0.13, 0.38, 0.62, 0.885];
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
  let target = 0, p = 0, active = -1;
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
    if (best !== active) { active = best; steps.forEach((st, i) => st.classList.toggle('is-active', i === best)); }
  }
  window.addEventListener('scroll', readProgress, { passive: true });
  window.addEventListener('resize', () => { measure(); readProgress(); });
  window.addEventListener('load', () => { measure(); readProgress(); });
  measure();
  readProgress();
  p = target;

  // Background mixes in sRGB, so the fry beat lands a rich brown instead of a muddy grey.
  const bgA = [251, 248, 238], bgOil = [44, 26, 12];
  const dummy = new THREE.Object3D(), arc = new THREE.Vector3(), tip = new THREE.Vector3(), mouth = new THREE.Vector3(0, 0.62, 0);
  const qa = new THREE.Quaternion(), qb = new THREE.Quaternion(), faceCam = new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0));
  curve.getPointAt(1, tip);

  let raf = 0, running = false, last = performance.now(), time = 0;
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now; time += dt;
    dpr(now);
    p += (target - p) * Math.min(1, dt * 8);

    // Beat windows across the section's scroll.
    const A = seg(p, 0.0, 0.3);       // banana turns
    const B = seg(p, 0.26, 0.5);      // slice + fan out
    const C = seg(p, 0.45, 0.64);      // into the oil, turning gold
    const D = seg(p, 0.76, 0.97);     // out of the uruli, into the pouch

    // Background: warm and dark only while frying.
    const dark = Math.sin(Math.PI * seg(p, 0.46, 0.8));
    const k = Math.min(1, dark * 1.15);
    storyEl.style.setProperty('--story-bg', `rgb(${bgA.map((c, i) => Math.round(lerp(c, bgOil[i], k))).join(',')})`);
    bar.style.setProperty('--p', p.toFixed(4));

    // Banana.
    const bOut = easeInOut(B);
    banana.visible = B < 1;
    banana.position.set(-bOut * 1.2, 0.05 + Math.sin(time) * 0.04 - bOut * 0.35, 0);
    banana.rotation.set(0.2 + A * 0.15, Math.sin(time * 0.5) * 0.35 - 0.2 + A * 0.5, -0.08);
    banana.scale.setScalar(Math.max(0.001, (0.92 + A * 0.08) * (1 - bOut * 0.75)));
    banana.updateMatrix();

    // Uruli rises for the fry, sinks after.
    const uIn = easeOutCubic(seg(p, 0.38, 0.52)), uOut = easeInOut(seg(p, 0.8, 0.95));
    uruli.visible = uIn > 0 && uOut < 1;
    uruli.position.set(0, -0.45 - (1 - uIn) * 2.6 - uOut * 2.6, 0);
    uruli.scale.setScalar(0.8);
    uruli.rotation.y = time * 0.12;
    oilMat.emissiveIntensity = dark * (0.55 + Math.sin(time * 9) * 0.06);
    glow.intensity = dark * 2.5;

    // Bubbles while frying.
    for (let i = 0; i < BUB; i++) {
      const b = bubData[i];
      const life = (time * 0.9 + b.ph) % 1;
      dummy.position.set(Math.cos(b.a) * b.r, -0.2 + life * 0.08, Math.sin(b.a) * b.r);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.setScalar(b.s * Math.sin(life * Math.PI) * C * (1 - D));
      dummy.updateMatrix();
      bubbles.setMatrixAt(i, dummy.matrix);
    }
    bubbles.instanceMatrix.needsUpdate = true;

    // Slices: tip of the banana → fan in the air → the oil → the pouch.
    for (let i = 0; i < S; i++) {
      const s = sd[i];
      const emerge = easeOutBack(clamp01(B * 1.5 - (i / S) * 0.5));
      const fall = easeInOut(clamp01(C * 1.35 - s.delay));
      const lift = easeInOut(clamp01(D * 1.3 - s.delay * 0.8));

      // fan position
      arc.set(Math.sin(s.fanA) * 1.0 - 0.1, 0.2 + Math.cos(s.fanA) * 0.32, 0.2 + Math.cos(s.fanA) * 0.15);
      dummy.position.copy(tip).applyMatrix4(banana.matrix).lerp(arc, emerge);
      qa.copy(faceCam).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), -s.fanA * 0.6));

      // into the bowl
      if (fall > 0) {
        const bowl = s.bowl.clone().multiplyScalar(0.8);
        bowl.y += uruli.position.y + 0.45 + Math.sin(time * 6 + i) * 0.01 * (1 - D);
        dummy.position.lerp(bowl, fall);
        dummy.position.y += Math.sin(fall * Math.PI) * 0.35;
        qb.setFromEuler(new THREE.Euler(s.tilt.x, s.tilt.y + time * 0.12, s.tilt.z));
        qa.slerp(qb, fall);
      }
      // swirl up into the pouch mouth
      if (lift > 0) {
        const ang = s.swirl + lift * Math.PI * 3;
        const rad = (1 - lift) * 0.9;
        const from = dummy.position.clone();
        const to = new THREE.Vector3(Math.cos(ang) * rad, lerp(from.y, mouth.y, lift) + Math.sin(lift * Math.PI) * 0.4, Math.sin(ang) * rad);
        dummy.position.lerpVectors(from, to, lift);
        qa.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(lift * 6, lift * 4, 0)));
      }
      dummy.quaternion.copy(qa);
      const scale = (B > 0 ? 0.95 * Math.min(1, emerge) : 0) * (1 - Math.pow(lift, 3));
      dummy.scale.setScalar(Math.max(0.0001, scale));
      dummy.updateMatrix();
      slices.setMatrixAt(i, dummy.matrix);
      fry.setX(i, clamp01((C - 0.25 - s.delay * 0.4) * 2.2));
    }
    slices.instanceMatrix.needsUpdate = true;
    fry.needsUpdate = true;

    // Pouch arrives for the last beat, then a little "seal" pulse.
    const pIn = easeOutBack(seg(p, 0.74, 0.86));
    const seal = Math.sin(Math.PI * seg(p, 0.93, 0.99)) * 0.06;
    pouchPivot.visible = pIn > 0.001;
    pouchPivot.position.set(0, -0.15 - (1 - pIn) * 2.2, 0);
    pouchPivot.rotation.set(0.05, -0.3 + Math.sin(time * 0.6) * 0.28 + (1 - pIn) * 1.4, 0);
    pouchPivot.scale.set(pIn * (1 + seal), pIn * (1 - seal * 0.5), pIn);
    mouth.set(0, pouchPivot.position.y + 0.85, 0);

    // Turmeric and salt dust settling over the chips.
    const dIn = Math.sin(Math.PI * seg(p, 0.68, 0.86));
    dust.material.opacity = dIn * 0.85;
    if (dIn > 0) {
      for (let i = 0; i < DUST; i++) {
        const d = dustSeed[i];
        const y = 1.4 - ((time * 0.25 + d[3]) % 1) * 1.7;
        dustPos[i * 3] = d[0] * 1.0; dustPos[i * 3 + 1] = y; dustPos[i * 3 + 2] = d[2] * 0.8;
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
