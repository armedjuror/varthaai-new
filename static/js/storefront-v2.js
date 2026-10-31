/* Varthaai storefront (v2): behaviour shared by every public page.
 * Grove parallax, reveal-on-scroll, mascot reactions and speech bubbles.
 * Page scripts (home-ui.js, shop) build on window.VT.
 */
(function () {
  'use strict';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  /* Speech bubble next to a mascot. */
  var bubbleTimers = new WeakMap();
  function say(bubble, text, ms) {
    if (!bubble) return;
    bubble.textContent = text;
    bubble.classList.add('is-on');
    clearTimeout(bubbleTimers.get(bubble));
    bubbleTimers.set(bubble, setTimeout(function () { bubble.classList.remove('is-on'); }, ms || 2600));
  }

  /* Make any mascot react: emotion for a moment, plus a hop or squash. */
  function react(target, emotion, ms, motion) {
    var m = typeof target === 'string' ? document.querySelector(target) : target;
    if (!m || typeof m.emote !== 'function') return;
    m.emote(emotion, ms || 1400);
    if (motion === 'hop') m.hop();
    else if (motion === 'squash') m.squash();
  }

  /* Pointer parallax for the illustrated grove (desktop pointers only). */
  function initGrove() {
    var groves = [].slice.call(document.querySelectorAll('.grove'));
    if (!groves.length || reduce || !finePointer) return;
    var raf = 0, mx = 0, my = 0;
    window.addEventListener('pointermove', function (e) {
      mx = (e.clientX / window.innerWidth) * 2 - 1;
      my = (e.clientY / window.innerHeight) * 2 - 1;
      if (raf) return;
      raf = requestAnimationFrame(function () {
        raf = 0;
        groves.forEach(function (g) { g.style.setProperty('--mx', mx.toFixed(3)); g.style.setProperty('--my', my.toFixed(3)); });
      });
    }, { passive: true });
  }

  /* Reveal-on-scroll for .reveal blocks. */
  function initReveal() {
    var els = [].slice.call(document.querySelectorAll('.v2 .reveal'));
    if (!els.length || !('IntersectionObserver' in window) || reduce) return;
    var vh = window.innerHeight;
    els.forEach(function (el) { if (el.getBoundingClientRect().top < vh * 0.92) el.classList.add('in'); });
    document.body.classList.add('reveal-ready');
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    els.forEach(function (el, i) {
      if (!el.classList.contains('in')) { el.style.transitionDelay = (i % 3) * 70 + 'ms'; io.observe(el); }
    });
  }

  /* Page-header mascots: tap for a reaction and a line from data-lines. */
  function initTalkers() {
    [].slice.call(document.querySelectorAll('[data-talk]')).forEach(function (wrap) {
      var m = wrap.querySelector('vt-mascot');
      var bubble = wrap.querySelector('.vt-bubble');
      var lines = [];
      try { lines = JSON.parse(wrap.getAttribute('data-talk') || '[]'); } catch (e) {}
      var moods = ['laughing', 'love', 'wink', 'excited', 'surprised'];
      var i = 0;
      wrap.addEventListener('click', function () {
        react(m, moods[i % moods.length], 1500, 'squash');
        if (lines.length) say(bubble, lines[i % lines.length]);
        if (typeof window.playCrunchSound === 'function') window.playCrunchSound();
        i++;
      });
      if (lines.length && wrap.hasAttribute('data-greet')) setTimeout(function () { say(bubble, wrap.getAttribute('data-greet'), 3200); }, 900);
    });
  }

  /* Lead popup: the mascot laughs when crunched. */
  function initLeadPopup() {
    var btn = document.getElementById('crunch-btn');
    if (btn) btn.addEventListener('click', function () { react('#lead-popup-mascot', 'laughing', 1300); });
  }

  /* Loader: the mascot thinks while you wait. */
  function initLoader() {
    var m = document.querySelector('#loader vt-mascot');
    if (!m) return;
    var moods = ['thinking', 'happy', 'excited'];
    var k = 0;
    setInterval(function () {
      var shown = document.getElementById('loader');
      if (shown && shown.offsetParent !== null) m.emotion = moods[k++ % moods.length];
    }, 1800);
  }

  window.VT = { say: say, react: react, reduceMotion: reduce };

  function init() { initGrove(); initReveal(); initTalkers(); initLeadPopup(); initLoader(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
