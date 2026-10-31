/* Varthaai journal: the reading buddy.
 * A mascot on the side that fills a ring as you read, tells you how long is left
 * ("It's just an 8 min read", "Halfway there!", "Just 2 mins more!") and
 * celebrates when you reach the end. Builds on window.VT (storefront-v2.js).
 */
(function () {
  'use strict';

  function init() {
    var buddy = document.getElementById('read-buddy');
    var content = document.querySelector('.blog-content');
    if (!buddy || !content) return;

    var VT = window.VT || {};
    var bubble = document.getElementById('read-bubble');
    var mascot = buddy.querySelector('vt-mascot');
    var bar = buddy.querySelector('.read-ring-bar');
    var btn = buddy.querySelector('.read-buddy-btn');
    var total = Math.max(1, parseInt(buddy.getAttribute('data-minutes'), 10) || 1);

    var progress = 0, scrolled = false, done = false, raf = 0, idleTimer = 0;
    var said = {};   // milestones already announced

    function plural(n) { return n === 1 ? 'min' : 'mins'; }
    function left() { return Math.max(0, Math.ceil(total * (1 - progress) - 0.05)); }
    function say(text, ms) { if (VT.say) VT.say(bubble, text, ms || 3200); }
    function react(emotion, ms, motion) { if (VT.react) VT.react(mascot, emotion, ms, motion); }

    // How far through the article body the bottom of the viewport has travelled.
    function measure() {
      var r = content.getBoundingClientRect();
      var span = Math.max(1, r.height - window.innerHeight * 0.35);
      var seen = window.innerHeight * 0.65 - r.top;
      return Math.min(1, Math.max(0, seen / span));
    }

    function finish() {
      done = true;
      buddy.classList.add('is-done');
      say(total > 1 ? 'You read all ' + total + ' mins! Snack break? 🍌' : 'All done! Snack break? 🍌', 4400);
      if (VT.celebrate) VT.celebrate(buddy, { count: 22, x: 50, y: 30 });
      else react('love', 2600, 'hop');
    }

    function update() {
      raf = 0;
      progress = measure();
      if (bar) bar.style.strokeDashoffset = String(100 - progress * 100);
      if (!scrolled) return;

      var mins = left();
      if (progress >= 0.985) { if (!done) finish(); return; }
      if (done) return;

      // Announce each milestone once, in order of reading.
      if (progress >= 0.25 && !said.q && total >= 4) {
        said.q = true; say('Good going! About ' + mins + ' ' + plural(mins) + ' to go.'); react('happy', 1400);
      }
      if (progress >= 0.5 && !said.half) {
        said.half = true; say(total > 2 ? 'Halfway there! ' + mins + ' ' + plural(mins) + ' more.' : 'Halfway there!', 3000); react('excited', 1600, 'hop');
      }
      if (progress > 0.5 && mins <= 2 && mins >= 1 && total >= 3 && !said['m' + mins]) {
        said['m' + mins] = true; say(mins === 2 ? 'Just 2 mins more!' : 'Just a minute more!', 2800); react(mins === 2 ? 'wink' : 'excited', 1400, mins === 1 ? 'hop' : null);
      }
    }

    function nudgeLater() {
      clearTimeout(idleTimer);
      if (done) return;
      // Reader paused mid-post: doze off, with a gentle note on what's left.
      idleTimer = setTimeout(function () {
        if (done || progress < 0.08) return;
        var mins = left();
        react('sleepy', 4000);
        say('Still with me? Only ' + mins + ' ' + plural(mins) + ' left.', 3600);
      }, 45000);
    }

    window.addEventListener('scroll', function () {
      if (window.scrollY > 40) scrolled = true;
      if (!raf) raf = requestAnimationFrame(update);
      nudgeLater();
    }, { passive: true });
    window.addEventListener('resize', function () { if (!raf) raf = requestAnimationFrame(update); });

    btn.addEventListener('click', function () {
      if (done) { react('love', 1800, 'hop'); say('All read! The chips are a tap away 👇', 2800); return; }
      var mins = left();
      react('wink', 1200, 'hop');
      say(progress < 0.03
        ? "It's just a " + total + ' ' + plural(total) + ' read.'
        : mins <= 1 ? 'Less than a minute left!' : mins + ' ' + plural(mins) + ' left. ' + Math.round(progress * 100) + '% read.');
    });

    // Show up once the page has settled, with the read time.
    buddy.hidden = false;
    update();
    setTimeout(function () {
      buddy.classList.add('is-on');
      setTimeout(function () {
        if (scrolled && progress > 0.2) return;
        say(total === 1 ? "Quick one: it's just a 1 min read." : "It's just a " + total + ' min read. Grab some chips!', 3600);
        if (mascot && mascot.wave) mascot.wave();
      }, 500);
    }, 700);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
