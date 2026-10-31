/* Varthaai home v2: 2D interactions that run on every device.
 * The WebGL scenes live in home-3d.js and only load on capable devices.
 */
(function () {
  'use strict';

  var root = document.body;
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Flavour tabs (WAI-ARIA tabs pattern) ─────────────────────────── */
  function initTabs() {
    var tabs = [].slice.call(document.querySelectorAll('.flavour-tab'));
    if (!tabs.length) return;
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.classList.toggle('is-active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute('aria-controls'));
        if (panel) { panel.hidden = !on; panel.classList.toggle('is-active', on); if (on) animateRings(panel); }
      });
      if (focus) tab.focus();
      tab.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
      document.dispatchEvent(new CustomEvent('v2:flavour', { detail: { index: tabs.indexOf(tab) } }));
    }
    tabs.forEach(function (t, i) {
      t.addEventListener('click', function () { select(t, false); });
      t.addEventListener('keydown', function (e) {
        var n = null;
        if (e.key === 'ArrowRight') n = tabs[(i + 1) % tabs.length];
        if (e.key === 'ArrowLeft') n = tabs[(i - 1 + tabs.length) % tabs.length];
        if (e.key === 'Home') n = tabs[0];
        if (e.key === 'End') n = tabs[tabs.length - 1];
        if (n) { e.preventDefault(); select(n, true); }
      });
    });
  }

  /* ── Nutrition rings: gram values fill against 100 g; energy is a plain tile ── */
  function prepRings() {
    [].slice.call(document.querySelectorAll('.nutri')).forEach(function (el) {
      var label = (el.dataset.label || '').toLowerCase();
      var raw = String(el.dataset.value || '').toLowerCase();
      var num = parseFloat(raw.replace(/[^0-9.]/g, ''));
      var isEnergy = /kcal|cal|energy/.test(label + ' ' + raw);
      if (isNaN(num) || isEnergy || /mg/.test(raw)) {
        el.classList.add('no-ring');
        var m = String(el.dataset.value).match(/^\s*([\d.,]+)\s*([a-zA-Z%]+)\s*$/);
        var v = el.querySelector('.nutri-val');
        if (m && v) { v.textContent = m[1]; var u = document.createElement('small'); u.textContent = m[2]; v.appendChild(u); }
        return;
      }
      el.dataset.frac = Math.max(0.02, Math.min(num / 100, 1));
    });
  }
  function animateRings(scope) {
    [].slice.call(scope.querySelectorAll('.nutri:not(.no-ring) .fill')).forEach(function (c) {
      var frac = parseFloat(c.closest('.nutri').dataset.frac || 0);
      c.style.strokeDashoffset = 113.1;
      requestAnimationFrame(function () { requestAnimationFrame(function () { c.style.strokeDashoffset = 113.1 * (1 - frac); }); });
    });
  }
  function initRings() {
    prepRings();
    var section = document.getElementById('products');
    if (!section || !('IntersectionObserver' in window)) {
      [].slice.call(document.querySelectorAll('.flavour-panel.is-active')).forEach(animateRings);
      return;
    }
    var io = new IntersectionObserver(function (es) {
      if (es[0].isIntersecting) {
        var p = section.querySelector('.flavour-panel.is-active');
        if (p) animateRings(p);
        io.disconnect();
      }
    }, { threshold: 0.3 });
    io.observe(section);
  }

  /* ── Crunch toy ───────────────────────────────────────────────────── */
  function crunchSound() {
    if (typeof window.playCrunchSound === 'function') window.playCrunchSound();
  }
  function initCrunch() {
    var btn = document.getElementById('chip-btn');
    if (!btn) return;
    var counter = document.getElementById('crunch-n');
    var mascot = document.getElementById('crunch-mascot');
    var crumbs = document.getElementById('crumbs');
    var KEY = 'varthaai_crunches';
    var n = 0;
    try { n = parseInt(localStorage.getItem(KEY) || '0', 10) || 0; } catch (e) {}
    counter.textContent = n;
    var busy = false;
    btn.addEventListener('click', function () {
      if (busy) return;
      busy = true;
      crunchSound();
      if (navigator.vibrate) navigator.vibrate(18);
      btn.classList.add('is-crunched');
      if (window.VT) window.VT.react(mascot, n > 0 && (n + 1) % 10 === 0 ? 'love' : 'laughing', 1100, 'hop');
      n += 1; counter.textContent = n;
      try { localStorage.setItem(KEY, String(n)); } catch (e) {}
      if (!reduceMotion) {
        for (var i = 0; i < 14; i++) {
          var c = document.createElement('span');
          c.className = 'crumb';
          var a = Math.random() * Math.PI * 2, d = 90 + Math.random() * 90;
          c.style.setProperty('--x', Math.cos(a) * d + 'px');
          c.style.setProperty('--y', Math.sin(a) * d + 40 + 'px');
          c.style.setProperty('--rot', (Math.random() * 360) + 'deg');
          var s = 4 + Math.random() * 7; c.style.width = s + 'px'; c.style.height = s + 'px';
          crumbs.appendChild(c);
          setTimeout(function (el) { el.remove(); }, 950, c);
        }
      }
      setTimeout(function () { btn.classList.remove('is-crunched'); }, 900);
      setTimeout(function () { busy = false; }, 1100);
    });
  }

  /* ── Hero mascot: reacts to the visitor and to the 3D pack ───────── */
  function initHeroMascot() {
    var m = document.getElementById('hero-mascot');
    var btn = document.getElementById('hero-mascot-btn');
    var bubble = document.getElementById('mascot-bubble');
    var stage = document.getElementById('hero-stage');
    if (!m || !window.VT) return;
    var VT = window.VT;
    var pokes = [
      ['ouch', 'Ow! I\'m crunchy, not squishy.'],
      ['laughing', 'Haha, that tickles!'],
      ['love', 'Fried in pure coconut oil. Every time.'],
      ['wink', 'Psst. Try the pack, it\'s my house.'],
      ['surprised', 'Nendran banana, turmeric, salt. That\'s all of me.'],
    ];
    var k = 0;
    btn.addEventListener('click', function () {
      var p = pokes[k++ % pokes.length];
      VT.react(m, p[0], 1700, 'squash');
      VT.say(bubble, p[1]);
      if (typeof window.playCrunchSound === 'function') window.playCrunchSound();
      if (navigator.vibrate) navigator.vibrate(15);
    });
    setTimeout(function () { VT.say(bubble, 'Hi! I\'m the Varthaai chip. Tap me.', 3400); }, 1300);
    document.querySelectorAll('[data-hop]').forEach(function (el) {
      var go = function () { VT.react(m, 'excited', 1400, 'hop'); VT.say(bubble, 'Yes! Order now!', 1600); };
      el.addEventListener('mouseenter', go);
      el.addEventListener('focus', go);
    });
    if (stage) stage.addEventListener('v2:pack', function (e) {
      var t = e.detail.type;
      if (t === 'hover') VT.react(m, 'excited', 1200);
      else if (t === 'grab') VT.react(m, 'surprised', 900);
      else if (t === 'spin') { VT.react(m, 'laughing', 1600); VT.say(bubble, 'Wheee! Not too fast!', 1800); }
      else if (t === 'release' && e.detail.speed > 0.15) VT.react(m, 'ouch', 1200, 'squash');
      else if (t === 'tap') { VT.react(m, 'love', 1500, 'hop'); VT.say(bubble, 'That\'s the Varthaai pack. Chips inside!', 2200); }
    });
  }

  /* ── Story mascot: feels each beat of the scroll story ──────────── */
  function initStoryMascot() {
    var story = document.getElementById('about');
    var m = document.getElementById('story-mascot');
    var bubble = document.getElementById('story-bubble');
    if (!story || !m || !window.VT) return;
    var beats = [
      ['thinking', 'A raw Nendran. Long, green, firm.'],
      ['surprised', 'Whoa. Paper-thin!'],
      ['excited', 'Hot coconut oil. My favourite bath.'],
      ['love', 'Salted, cooled, packed. Hi!'],
    ];
    var pin = story.querySelector('.story-pin');
    story.addEventListener('v2:story-complete', function () {
      m.emote('excited');
      m.hop();
      window.VT.burst(pin, { x: 90, y: 22, count: 30 });
      window.VT.say(bubble, 'Ta-da! That\'s how every pack is made.', 3200);
      setTimeout(function () { m.emote('love'); }, 1800);
    });
    story.addEventListener('v2:beat', function (e) {
      var b = beats[e.detail.i];
      if (!b) return;
      m.emote(b[0]);
      m.hop();
      window.VT.say(bubble, b[1], 2600);
    });
  }

  /* ── Reviews marquee: clone cards until the loop is seamless ──────── */
  function initMarquee() {
    var wrap = document.querySelector('.marquee');
    if (!wrap) return;
    var track = wrap.querySelector('.marquee-track');
    var originals = [].slice.call(track.children);
    if (!originals.length || reduceMotion) { track.style.animation = 'none'; return; }
    var guard = 0;
    while (track.scrollWidth < window.innerWidth * 2 && guard++ < 12) {
      originals.forEach(function (li) { var c = li.cloneNode(true); c.setAttribute('aria-hidden', 'true'); track.appendChild(c); });
    }
    // Make the set even so translateX(-50%) lands on an identical frame.
    [].slice.call(track.children).slice(0).forEach(function (li) {
      var c = li.cloneNode(true); c.setAttribute('aria-hidden', 'true'); track.appendChild(c);
    });
    track.style.setProperty('--marquee-dur', Math.max(30, track.scrollWidth / 2 / 40) + 's');
    var resume;
    wrap.addEventListener('touchstart', function () { wrap.classList.add('is-paused'); clearTimeout(resume); }, { passive: true });
    wrap.addEventListener('touchend', function () { resume = setTimeout(function () { wrap.classList.remove('is-paused'); }, 2500); });
  }

  /* ── Origin route: draws with scroll, a pouch rides along ─────────── */
  function initRoute() {
    var route = document.getElementById('route');
    var line = document.getElementById('route-line');
    if (!route || !line) return;
    var pouch = route.querySelector('.route-pouch');
    var svg = route.querySelector('svg');
    var len = line.getTotalLength();
    function update() {
      var r = route.getBoundingClientRect();
      var vh = window.innerHeight;
      var p = reduceMotion ? 1 : Math.min(1, Math.max(0, (vh * 0.9 - r.top) / (vh * 0.6)));
      line.style.setProperty('--draw', String(1 - p));
      var pt = line.getPointAtLength(len * p);
      var sx = svg.clientWidth / 400, sy = svg.clientHeight / 300;
      pouch.style.left = pt.x * sx + 'px';
      pouch.style.top = pt.y * sy + 'px';
      route.classList.toggle('has-pouch', p > 0.02);
    }
    var ticking = false;
    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; requestAnimationFrame(function () { update(); ticking = false; }); }
    }, { passive: true });
    window.addEventListener('resize', update);
    update();
  }

  /* ── Sticky mobile order bar ──────────────────────────────────────── */
  function initStickyBar() {
    var bar = document.getElementById('sticky-order');
    var hero = document.getElementById('top');
    var footer = document.querySelector('.footer');
    if (!bar || !hero || !('IntersectionObserver' in window)) return;
    var heroOut = false, footerIn = false;
    function sync() {
      var on = heroOut && !footerIn;
      bar.classList.toggle('is-visible', on);
      bar.setAttribute('aria-hidden', on ? 'false' : 'true');
      bar.querySelector('a').tabIndex = on ? 0 : -1;
    }
    new IntersectionObserver(function (e) { heroOut = !e[0].isIntersecting; sync(); }, { threshold: 0.05 }).observe(hero);
    if (footer) new IntersectionObserver(function (e) { footerIn = e[0].isIntersecting; sync(); }).observe(footer);
  }

  /* ── Smooth scroll (desktop pointer only; native on touch) ────────── */
  function initSmoothScroll() {
    if (reduceMotion || !window.Lenis || window.matchMedia('(hover: none)').matches) return null;
    var lenis = new window.Lenis({ duration: 1.1, smoothWheel: true });
    if (window.gsap && window.ScrollTrigger) {
      lenis.on('scroll', window.ScrollTrigger.update);
      window.gsap.ticker.add(function (t) { lenis.raf(t * 1000); });
      window.gsap.ticker.lagSmoothing(0);
    } else {
      (function raf(t) { lenis.raf(t); requestAnimationFrame(raf); })(0);
    }
    document.querySelectorAll('a[href^="#"], a[href^="/#"]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var id = a.getAttribute('href').replace(/^\//, '');
        if (id.length < 2) return;
        var target = document.querySelector(id);
        if (target) { e.preventDefault(); lenis.scrollTo(target, { offset: -64 }); }
      });
    });
    return lenis;
  }

  function init() {
    if (window.gsap && window.ScrollTrigger) window.gsap.registerPlugin(window.ScrollTrigger);
    initTabs();
    initRings();
    initCrunch();
    initMarquee();
    initHeroMascot();
    initStoryMascot();
    initRoute();
    initStickyBar();
    window.V2 = { lenis: initSmoothScroll(), reduceMotion: reduceMotion };
    document.dispatchEvent(new Event('v2:ready'));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
