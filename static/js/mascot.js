/* Varthaai mascot: the chip from mascot.png, rebuilt so it can emote.
 *
 *   <vt-mascot emotion="happy" track wave></vt-mascot>
 *
 * The body is the real chip photo (static/images/mascot/body.webp, with the
 * painted face and limbs removed); eyes, mouth, arms and legs are vectors
 * drawn to match the original artwork, so every emotion stays on-model.
 *
 * Attributes
 *   emotion   happy | excited | surprised | love | wink | sad | sleepy |
 *             laughing | thinking | ouch            (default: happy)
 *   pose      rest | wave | up | down | chin        (default: from emotion)
 *   track     pupils follow the pointer
 *   wave      wave hello once when it first appears
 *   doze      fall asleep after 25 s without pointer movement
 *   static    no idle motion (blink still runs unless reduced motion)
 *
 * Methods
 *   emote(name, ms?)   switch emotion; with ms, return to the base emotion after
 *   hop()  wave()  squash()
 *
 * Also exposes window.VTMascot.svg(emotion, pose) for static exports.
 */
(function () {
  'use strict';
  if (window.VTMascot) return;

  var script = document.currentScript;
  var BODY_URL = (script && script.dataset.body) ||
    (script ? new URL('../images/mascot/body.webp', script.src).href : '/static/images/mascot/body.webp');
  var REDUCE = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var INK = '#5A2C10';      // limbs, closed eyes, brows
  var MOUTH = '#C41E2A';
  var MOUTH_DEEP = '#8F1219';
  var EYE = '#F6F7F9';
  var EYE_SHADE = '#D9DCE2';
  var PUPIL = '#5B2A10';
  var BLUSH = '#FF7A59';

  var L = { x: 180, y: 172 }, R = { x: 271, y: 166 };

  /* ── Face parts ─────────────────────────────────────────────────── */
  function openEye(c, rad, pr, dx, dy, side) {
    rad = rad || 40; pr = pr || 13; dx = dx == null ? 14 : dx; dy = dy == null ? 6 : dy;
    return '<g class="vtm-eye vtm-eye-' + side + '">' +
      '<circle cx="' + c.x + '" cy="' + c.y + '" r="' + rad + '" fill="' + EYE_SHADE + '"/>' +
      '<circle cx="' + (c.x + 3) + '" cy="' + (c.y - 3) + '" r="' + (rad - 4) + '" fill="' + EYE + '"/>' +
      '<g class="vtm-pupil" style="--px:0px;--py:0px">' +
      '<circle cx="' + (c.x + dx) + '" cy="' + (c.y + dy) + '" r="' + pr + '" fill="' + PUPIL + '"/>' +
      '<circle cx="' + (c.x + dx + pr * 0.38) + '" cy="' + (c.y + dy - pr * 0.4) + '" r="' + Math.max(2.5, pr * 0.3) + '" fill="#fff"/>' +
      '</g></g>';
  }
  function arcEye(c, up) { // closed eye: up = happy ^ , down = sleepy/wink ‿
    var w = 30, h = up ? -34 : 22;
    return '<path d="M' + (c.x - w) + ' ' + (c.y + 6) + ' Q' + c.x + ' ' + (c.y + 6 + h) + ' ' + (c.x + w) + ' ' + (c.y + 6) +
      '" fill="none" stroke="' + INK + '" stroke-width="9" stroke-linecap="round"/>';
  }
  function squeezeEye(c, dir) { // > or <
    var s = dir, x0 = c.x - 22 * s, x1 = c.x + 20 * s;
    return '<path d="M' + x0 + ' ' + (c.y - 20) + ' L' + x1 + ' ' + c.y + ' L' + x0 + ' ' + (c.y + 20) +
      '" fill="none" stroke="' + INK + '" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>';
  }
  function heartEye(c) {
    var x = c.x, y = c.y - 4, s = 1.15;
    return '<path class="vtm-heart-eye" d="M' + x + ' ' + (y + 30 * s) + ' C' + (x - 46 * s) + ' ' + (y + 2 * s) + ' ' +
      (x - 30 * s) + ' ' + (y - 34 * s) + ' ' + x + ' ' + (y - 14 * s) + ' C' + (x + 30 * s) + ' ' + (y - 34 * s) + ' ' +
      (x + 46 * s) + ' ' + (y + 2 * s) + ' ' + x + ' ' + (y + 30 * s) + 'Z" fill="#E0283A" stroke="#A3121F" stroke-width="3"/>' +
      '<ellipse cx="' + (x - 14) + '" cy="' + (y - 12) + '" rx="7" ry="5" fill="#fff" opacity=".7"/>';
  }
  function halfEye(c) { // sleepy: lid over the top half
    return '<g><circle cx="' + c.x + '" cy="' + c.y + '" r="36" fill="' + EYE_SHADE + '"/>' +
      '<circle cx="' + c.x + '" cy="' + (c.y + 18) + '" r="10" fill="' + PUPIL + '"/>' +
      '<path d="M' + (c.x - 38) + ' ' + (c.y + 4) + ' A38 38 0 0 1 ' + (c.x + 38) + ' ' + (c.y + 4) + ' Z" fill="#E7B913"/>' +
      '<path d="M' + (c.x - 38) + ' ' + (c.y + 4) + ' L' + (c.x + 38) + ' ' + (c.y + 4) + '" stroke="' + INK + '" stroke-width="7" stroke-linecap="round"/></g>';
  }
  function brow(x1, y1, x2, y2) {
    return '<path d="M' + x1 + ' ' + y1 + ' L' + x2 + ' ' + y2 + '" stroke="' + INK + '" stroke-width="8" stroke-linecap="round"/>';
  }

  var MOUTHS = {
    grin: // the original D-shaped grin with two teeth
      '<path d="M192 222 L290 222 Q292 272 241 272 Q190 272 192 222Z" fill="' + MOUTH + '"/>' +
      '<path d="M262 222 L290 222 Q292 262 262 270Z" fill="#D9232D"/>' +
      '<ellipse cx="241" cy="263" rx="28" ry="7" fill="' + MOUTH_DEEP + '" opacity=".45"/>' +
      '<rect x="228" y="222" width="19" height="10" rx="1.5" fill="#fff"/>' +
      '<rect x="233" y="261" width="22" height="9" rx="2" fill="#fff"/>',
    open: // laughing / excited: wide open with tongue
      '<path d="M184 214 L298 214 Q300 298 241 298 Q182 298 184 214Z" fill="' + MOUTH_DEEP + '"/>' +
      '<path d="M206 286 Q241 252 276 286 Q262 298 241 298 Q220 298 206 286Z" fill="#F0737A"/>' +
      '<rect x="210" y="214" width="62" height="12" rx="3" fill="#fff"/>',
    smile:
      '<path d="M206 236 Q241 270 276 236 Q262 262 241 262 Q220 262 206 236Z" fill="' + MOUTH + '" stroke="' + MOUTH_DEEP + '" stroke-width="3" stroke-linejoin="round"/>',
    o:
      '<ellipse cx="238" cy="252" rx="17" ry="21" fill="' + MOUTH_DEEP + '"/>' +
      '<ellipse cx="238" cy="258" rx="10" ry="11" fill="' + MOUTH + '"/>',
    small_o:
      '<ellipse cx="240" cy="252" rx="9" ry="10" fill="' + MOUTH_DEEP + '"/>',
    frown:
      '<path d="M206 266 Q241 232 276 266" fill="none" stroke="' + MOUTH_DEEP + '" stroke-width="10" stroke-linecap="round"/>',
    flat:
      '<path d="M214 254 Q240 250 270 240" fill="none" stroke="' + MOUTH_DEEP + '" stroke-width="9" stroke-linecap="round"/>',
    wobble:
      '<path d="M198 252 q11 -12 22 0 t22 0 t22 0 t22 0" fill="none" stroke="' + MOUTH_DEEP + '" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>',
  };

  function blush(op) {
    return '<ellipse cx="138" cy="228" rx="22" ry="12" fill="' + BLUSH + '" opacity="' + (op || 0.35) + '"/>' +
      '<ellipse cx="318" cy="220" rx="22" ry="12" fill="' + BLUSH + '" opacity="' + (op || 0.35) + '"/>';
  }

  var FACES = {
    happy: { pose: 'rest', eyes: function () { return openEye(L, 40, 13, 14, 6, 'l') + openEye(R, 40, 13, 10, 6, 'r'); }, mouth: 'grin', blink: true },
    excited: { pose: 'up', eyes: function () { return arcEye(L, true) + arcEye(R, true); }, mouth: 'open', extra: blush(0.45) },
    surprised: {
      pose: 'up', blink: true,
      eyes: function () { return openEye(L, 45, 8, 0, 0, 'l') + openEye(R, 45, 8, 0, 0, 'r') + brow(150, 112, 200, 104) + brow(244, 98, 296, 106); },
      mouth: 'o',
      extra: '<path class="vtm-sweat" d="M330 120 q10 18 0 26 q-10 -8 0 -26z" fill="#8FD3F4" stroke="#4BA3CF" stroke-width="2"/>',
    },
    love: {
      pose: 'up', eyes: function () { return heartEye(L) + heartEye(R); }, mouth: 'smile', extra: blush(0.5) +
        '<g class="vtm-float-hearts"><path d="M400 70 c-8 -10 -22 -2 -14 9 l14 13 l14 -13 c8 -11 -6 -19 -14 -9z" fill="#E0283A"/>' +
        '<path d="M430 120 c-5 -6 -14 -1 -9 6 l9 8 l9 -8 c5 -7 -4 -12 -9 -6z" fill="#E0283A" opacity=".8"/></g>',
    },
    wink: { pose: 'wave', eyes: function () { return openEye(L, 40, 13, 14, 6, 'l') + arcEye(R, false); }, mouth: 'smile', extra: blush(0.3) },
    sad: {
      pose: 'down', blink: true,
      eyes: function () { return openEye(L, 40, 12, 0, 16, 'l') + openEye(R, 40, 12, -2, 16, 'r') + brow(146, 126, 202, 112) + brow(244, 106, 300, 118); },
      mouth: 'frown',
      extra: '<path class="vtm-tear" d="M158 214 q10 18 0 26 q-10 -8 0 -26z" fill="#8FD3F4" stroke="#4BA3CF" stroke-width="2"/>',
    },
    sleepy: {
      pose: 'down', eyes: function () { return halfEye(L) + halfEye(R); }, mouth: 'small_o',
      extra: '<g class="vtm-zzz" font-family="Baloo 2, system-ui, sans-serif" font-weight="800" fill="' + INK + '">' +
        '<text x="350" y="110" font-size="34">z</text><text x="380" y="78" font-size="44">z</text><text x="414" y="40" font-size="54">Z</text></g>',
    },
    laughing: {
      pose: 'up', eyes: function () { return squeezeEye(L, 1) + squeezeEye(R, -1); }, mouth: 'open', extra: blush(0.5) +
        '<path d="M126 196 q8 14 0 22 q-8 -8 0 -22z" fill="#8FD3F4"/><path d="M324 188 q8 14 0 22 q-8 -8 0 -22z" fill="#8FD3F4"/>',
    },
    thinking: {
      pose: 'chin', blink: true,
      eyes: function () { return openEye(L, 40, 12, -12, -14, 'l') + openEye(R, 40, 12, -14, -16, 'r') + brow(148, 118, 204, 116) + brow(240, 96, 298, 108); },
      mouth: 'flat',
    },
    ouch: { pose: 'up', eyes: function () { return squeezeEye(L, 1) + squeezeEye(R, -1); }, mouth: 'wobble', extra: blush(0.3) },
  };

  /* ── Limbs ──────────────────────────────────────────────────────── */
  function hand(x, y, rot) {
    return '<g transform="translate(' + x + ' ' + y + ') rotate(' + (rot || 0) + ')">' +
      '<ellipse cx="0" cy="0" rx="14" ry="10" fill="' + INK + '"/><ellipse cx="-9" cy="-8" rx="5" ry="7" fill="' + INK + '"/></g>';
  }
  function limb(d) {
    return '<path d="' + d + '" fill="none" stroke="' + INK + '" stroke-width="7.5" stroke-linecap="round" stroke-linejoin="round"/>';
  }
  var POSES = {
    rest: {
      l: limb('M64 214 C28 236 20 300 52 330 C76 344 104 336 120 326') + hand(128, 324, 20),
      r: limb('M372 170 C384 206 404 262 440 284 C452 270 452 226 458 198') + hand(466, 190, -100),
    },
    wave: {
      l: limb('M64 214 C28 236 20 300 52 330 C76 344 104 336 120 326') + hand(128, 324, 20),
      r: limb('M372 170 C400 150 420 110 432 74') + hand(436, 62, -70),
    },
    up: {
      l: limb('M64 214 C40 190 28 140 40 98') + hand(42, 86, -110),
      r: limb('M372 170 C400 150 420 110 432 74') + hand(436, 62, -70),
    },
    down: {
      l: limb('M64 222 C50 262 46 304 50 342') + hand(50, 352, 90),
      r: limb('M372 176 C388 220 394 270 396 330') + hand(396, 340, 90),
    },
    chin: {
      l: limb('M64 214 C28 236 20 300 52 330 C76 344 104 336 120 326') + hand(128, 324, 20),
      r: limb('M372 170 C412 236 372 300 306 300') + hand(298, 296, 200),
      front: true,
    },
  };
  var LEGS = limb('M198 348 C188 380 172 420 158 455') + limb('M272 344 C282 376 292 410 302 442');

  function svg(emotion, pose, opts) {
    var f = FACES[emotion] || FACES.happy;
    var p = POSES[pose || f.pose] || POSES.rest;
    var id = (opts && opts.label) || ('Varthaai mascot, ' + (FACES[emotion] ? emotion : 'happy'));
    return '<svg class="vtm-svg" viewBox="0 0 500 470" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + id + '">' +
      '<ellipse class="vtm-shadow" cx="230" cy="458" rx="120" ry="10" fill="rgba(46,42,28,.18)"/>' +
      '<g class="vtm-figure">' +
      '<g class="vtm-legs">' + LEGS + '</g>' +
      '<g class="vtm-arm vtm-arm-l">' + p.l + '</g>' +
      (p.front ? '' : '<g class="vtm-arm vtm-arm-r">' + p.r + '</g>') +
      '<g class="vtm-body"><image href="' + BODY_URL + '" x="56" y="8" width="326" height="345" preserveAspectRatio="none"/></g>' +
      (p.front ? '<g class="vtm-arm vtm-arm-r">' + p.r + '</g>' : '') +
      '<g class="vtm-face">' +
      '<g class="vtm-eyes' + (f.blink ? ' vtm-blinkable' : '') + '">' + f.eyes() + '</g>' +
      '<g class="vtm-mouth">' + MOUTHS[f.mouth] + '</g>' +
      (f.extra || '') +
      '</g></g></svg>';
  }

  /* ── Styles (injected once) ─────────────────────────────────────── */
  var css = [
    'vt-mascot{display:inline-block;position:relative;line-height:0;-webkit-tap-highlight-color:transparent}',
    'vt-mascot .vtm-svg{width:100%;height:auto;overflow:visible;display:block}',
    'vt-mascot .vtm-figure{transform-box:view-box;transform-origin:230px 455px}',
    'vt-mascot:not([static]) .vtm-figure{animation:vtmBob 2.6s ease-in-out infinite}',
    'vt-mascot .vtm-shadow{transform-box:view-box;transform-origin:230px 458px}',
    'vt-mascot:not([static]) .vtm-shadow{animation:vtmShadow 2.6s ease-in-out infinite}',
    'vt-mascot .vtm-blinkable .vtm-eye{transform-box:fill-box;transform-origin:center}',
    'vt-mascot.is-blink .vtm-blinkable .vtm-eye{transform:scaleY(.08)}',
    'vt-mascot .vtm-eye{transition:transform .08s ease-in}',
    'vt-mascot .vtm-pupil{transform:translate(var(--px),var(--py));transition:transform .18s ease-out}',
    'vt-mascot .vtm-arm-r{transform-box:view-box;transform-origin:372px 170px}',
    'vt-mascot .vtm-arm-l{transform-box:view-box;transform-origin:64px 214px}',
    'vt-mascot.is-waving .vtm-arm-r{animation:vtmWave .5s ease-in-out 3}',
    'vt-mascot.is-hop .vtm-figure{animation:vtmHop .6s cubic-bezier(.3,1.4,.5,1)}',
    'vt-mascot.is-squash .vtm-figure{animation:vtmSquash .45s ease-out}',
    'vt-mascot.is-swap .vtm-face{animation:vtmPop .3s ease-out}',
    'vt-mascot .vtm-zzz text{animation:vtmZ 2.4s ease-in-out infinite;opacity:0}',
    'vt-mascot .vtm-zzz text:nth-child(2){animation-delay:.6s}vt-mascot .vtm-zzz text:nth-child(3){animation-delay:1.2s}',
    'vt-mascot .vtm-float-hearts{animation:vtmFloat 1.8s ease-in-out infinite}',
    'vt-mascot .vtm-tear,vt-mascot .vtm-sweat{animation:vtmDrip 1.6s ease-in infinite}',
    'vt-mascot[emotion="laughing"]:not([static]) .vtm-figure{animation:vtmShake .35s ease-in-out infinite}',
    'vt-mascot[emotion="sleepy"]:not([static]) .vtm-figure{animation:vtmBreath 3.2s ease-in-out infinite}',
    'vt-mascot[emotion="excited"]:not([static]) .vtm-figure,vt-mascot[emotion="love"]:not([static]) .vtm-figure{animation:vtmBounce .7s ease-in-out infinite}',
    '@keyframes vtmBob{50%{transform:translateY(-8px) rotate(-1.5deg)}}',
    '@keyframes vtmShadow{50%{transform:scaleX(.9);opacity:.8}}',
    '@keyframes vtmWave{50%{transform:rotate(-18deg)}}',
    '@keyframes vtmHop{0%{transform:scale(1.08,.9)}35%{transform:translateY(-70px) scale(.95,1.06)}70%{transform:translateY(0) scale(1.08,.92)}100%{transform:none}}',
    '@keyframes vtmSquash{0%{transform:scale(1.14,.84)}55%{transform:scale(.96,1.05)}100%{transform:none}}',
    '@keyframes vtmPop{0%{transform:scale(.9);opacity:.4}100%{transform:none;opacity:1}}',
    '@keyframes vtmZ{0%{opacity:0;transform:translate(0,10px)}30%{opacity:1}100%{opacity:0;transform:translate(14px,-18px)}}',
    '@keyframes vtmFloat{50%{transform:translateY(-12px)}}',
    '@keyframes vtmDrip{0%{transform:translateY(0);opacity:1}100%{transform:translateY(24px);opacity:0}}',
    '@keyframes vtmShake{50%{transform:rotate(3deg) translateY(-4px)}}',
    '@keyframes vtmBreath{50%{transform:scale(1.03,.97)}}',
    '@keyframes vtmBounce{50%{transform:translateY(-14px)}}',
    '@media (prefers-reduced-motion:reduce){vt-mascot *{animation:none!important;transition:none!important}}',
  ].join('\n');
  var style = document.createElement('style');
  style.id = 'vt-mascot-css';
  style.textContent = css;
  document.head.appendChild(style);

  /* ── Pointer (shared by every mascot on the page) ───────────────── */
  var pointer = { x: -1, y: -1, last: Date.now() };
  var instances = new Set();
  window.addEventListener('pointermove', function (e) {
    pointer.x = e.clientX; pointer.y = e.clientY; pointer.last = Date.now();
    instances.forEach(function (m) { m._track(); m._wake(); });
  }, { passive: true });

  /* ── Element ────────────────────────────────────────────────────── */
  var Base = window.HTMLElement;
  class VtMascot extends Base {
    static get observedAttributes() { return ['emotion', 'pose']; }
    constructor() {
      super();
      this._base = null;
      this._timer = 0;
      this._blinkT = 0;
      this._asleep = false;
    }
    connectedCallback() {
      if (!this._base) this._base = this.getAttribute('emotion') || 'happy';
      if (!this.hasAttribute('aria-hidden') && !this.hasAttribute('role')) this.setAttribute('aria-hidden', 'true');
      this._render(false);
      instances.add(this);
      if (!REDUCE) this._scheduleBlink();
      if (this.hasAttribute('wave') && !REDUCE) setTimeout(this.wave.bind(this), 700);
      if (this.hasAttribute('doze')) this._dozeTimer = setInterval(this._checkDoze.bind(this), 2000);
    }
    disconnectedCallback() {
      instances.delete(this);
      clearTimeout(this._blinkT); clearInterval(this._dozeTimer); clearTimeout(this._timer);
    }
    attributeChangedCallback(name, oldV, newV) {
      if (oldV !== newV && this.isConnected) this._render(true);
    }
    get emotion() { return this.getAttribute('emotion') || 'happy'; }
    set emotion(v) { this.setAttribute('emotion', v); }

    _render(animate) {
      this.innerHTML = svg(this.emotion, this.getAttribute('pose'));
      if (animate && !REDUCE) { this.classList.remove('is-swap'); void this.offsetWidth; this.classList.add('is-swap'); }
      this._track();
    }
    _scheduleBlink() {
      var self = this;
      this._blinkT = setTimeout(function () {
        self.classList.add('is-blink');
        setTimeout(function () { self.classList.remove('is-blink'); }, 120);
        self._scheduleBlink();
      }, 2200 + Math.random() * 3200);
    }
    _track() {
      if (!this.hasAttribute('track') || pointer.x < 0) return;
      var r = this.getBoundingClientRect();
      if (!r.width) return;
      var cx = r.left + r.width * 0.45, cy = r.top + r.height * 0.36;
      var dx = pointer.x - cx, dy = pointer.y - cy, d = Math.hypot(dx, dy) || 1;
      var k = Math.min(1, d / 300) * 9; // px in svg units, scaled by the svg itself
      var px = (dx / d) * k * 1.4;  // svg user units
      var py = (dy / d) * k;
      this.querySelectorAll('.vtm-pupil').forEach(function (p) {
        p.style.setProperty('--px', px.toFixed(1) + 'px');
        p.style.setProperty('--py', py.toFixed(1) + 'px');
      });
    }
    _checkDoze() {
      if (!this._asleep && Date.now() - pointer.last > 25000 && this.emotion === this._base) {
        this._asleep = true; this.emotion = 'sleepy';
      }
    }
    _wake() {
      if (this._asleep) { this._asleep = false; this.emote('surprised', 900); }
    }
    emote(name, ms) {
      clearTimeout(this._timer);
      if (!ms) this._base = name;
      this.emotion = name;
      var self = this;
      if (ms) this._timer = setTimeout(function () { self.emotion = self._base || 'happy'; }, ms);
      return this;
    }
    _flash(cls, ms) {
      if (REDUCE) return this;
      var self = this;
      this.classList.remove(cls); void this.offsetWidth; this.classList.add(cls);
      setTimeout(function () { self.classList.remove(cls); }, ms);
      return this;
    }
    hop() { return this._flash('is-hop', 650); }
    squash() { return this._flash('is-squash', 480); }
    wave() {
      var self = this, prev = this.getAttribute('pose');
      if (!prev) this.setAttribute('pose', 'wave');
      this._flash('is-waving', 1550);
      setTimeout(function () { if (!prev) self.removeAttribute('pose'); }, 1600);
      return this;
    }
  }
  if (!customElements.get('vt-mascot')) customElements.define('vt-mascot', VtMascot);

  window.VTMascot = { svg: svg, emotions: Object.keys(FACES), poses: Object.keys(POSES) };
})();
