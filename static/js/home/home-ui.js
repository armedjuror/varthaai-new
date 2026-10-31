/* Varthaai home v2: 2D interactions that run on every device.
 * The WebGL scenes live in home-3d.js and only load on capable devices.
 */
(function () {
  'use strict';

  var root = document.body;
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Reveal on scroll ─────────────────────────────────────────────── */
  function initReveal() {
    var els = [].slice.call(document.querySelectorAll('.v2 .reveal'));
    if (!('IntersectionObserver' in window) || reduceMotion) return;
    var vh = window.innerHeight;
    // Mark what's already on screen first so nothing above the fold flickers.
    els.forEach(function (el) { if (el.getBoundingClientRect().top < vh * 0.92) el.classList.add('in'); });
    root.classList.add('reveal-ready');
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    els.forEach(function (el, i) {
      if (!el.classList.contains('in')) { el.style.transitionDelay = (i % 3) * 70 + 'ms'; io.observe(el); }
    });
  }

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
      mascot.classList.add('is-cheer');
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
      setTimeout(function () { btn.classList.remove('is-crunched'); mascot.classList.remove('is-cheer'); }, 900);
      setTimeout(function () { busy = false; }, 1100);
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
    initReveal();
    initTabs();
    initRings();
    initCrunch();
    initMarquee();
    initRoute();
    initStickyBar();
    window.V2 = { lenis: initSmoothScroll(), reduceMotion: reduceMotion };
    document.dispatchEvent(new Event('v2:ready'));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
