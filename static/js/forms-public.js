/* Public form pages (/f/<id>/): progress, field states, chip rating, mascot
 * reactions (Varthaai forms only), submit state, and the thanks-page
 * celebration. No dependencies; works with or without the mascot. */
(function () {
  'use strict';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var body = document.body;
  var mascot = document.getElementById('fx-mascot');
  var bubble = document.getElementById('fx-bubble');

  /* ── mascot helpers (no-ops without the mascot) ─────────────────────── */
  var bubbleTimer = 0;
  function say(text, ms) {
    if (!bubble) return;
    bubble.textContent = text;
    bubble.classList.add('is-on');
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(function () { bubble.classList.remove('is-on'); }, ms || 3000);
  }
  function feel(emotion, ms, motion) {
    if (!mascot || typeof mascot.emote !== 'function') return;
    mascot.emote(emotion, ms);
    if (motion === 'hop' && mascot.hop) mascot.hop();
    if (motion === 'wave' && mascot.wave) mascot.wave();
  }

  /* ── confetti: brand colours, plus real chips on Varthaai forms ─────── */
  var COLORS = ['#86AA4E', '#E9C202', '#D7262B', '#B0C462', '#BB710B'];
  function confetti(originEl, count) {
    if (reduce || !document.body.animate) return;
    var r = originEl.getBoundingClientRect();
    var ox = r.left + r.width / 2, oy = r.top + r.height * 0.45;
    var chips = body.classList.contains('fx--house');
    for (var i = 0; i < (count || 34); i++) {
      var c = document.createElement('span');
      var chip = chips && i % 4 === 0;
      c.className = 'fx-confetti' + (chip ? ' is-chip' : '');
      if (!chip) c.style.background = COLORS[i % COLORS.length];
      body.appendChild(c);
      var a = -Math.PI / 2 + (Math.random() - 0.5) * 2.4, v = 160 + Math.random() * 220;
      var dx = Math.cos(a) * v, dy = Math.sin(a) * v, rot = (Math.random() - 0.5) * 1000;
      var anim = c.animate([
        { transform: 'translate(' + ox + 'px,' + oy + 'px) rotate(0deg)', opacity: 1 },
        { transform: 'translate(' + (ox + dx) + 'px,' + (oy + dy) + 'px) rotate(' + rot / 2 + 'deg)', opacity: 1, offset: 0.45 },
        { transform: 'translate(' + (ox + dx * 1.3) + 'px,' + (oy + dy + 420) + 'px) rotate(' + rot + 'deg)', opacity: 0 }
      ], { duration: 1500 + Math.random() * 900, easing: 'cubic-bezier(.2,.7,.4,1)' });
      anim.onfinish = (function (el) { return function () { el.remove(); }; })(c);
    }
  }

  /* ═══ FORM PAGE ═════════════════════════════════════════════════════ */
  function initForm() {
    var form = document.getElementById('fx-form');
    if (!form) return;
    var fields = [].slice.call(form.querySelectorAll('.fx-field'));
    var bar = document.getElementById('fx-progress-bar');
    var progress = document.getElementById('fx-progress');
    var submit = document.getElementById('fx-submit');
    var announcedReady = false, cheered = {};

    function filled(f) {
      var type = f.getAttribute('data-type');
      if (type === 'checkbox') return f.querySelector('input').checked;
      if (type === 'rating') return !!f.querySelector('input:checked');
      var el = f.querySelector('input:not([type=hidden]), select, textarea');
      if (!el || !el.value.trim()) return false;
      return el.checkValidity ? el.checkValidity() : true;
    }

    function refresh(changed) {
      var done = 0, reqLeft = 0;
      fields.forEach(function (f) {
        var ok = filled(f);
        f.classList.toggle('is-done', ok);
        if (ok) { done++; f.classList.remove('has-error'); }
        else if (f.hasAttribute('data-required')) reqLeft++;
      });
      var pct = fields.length ? Math.round(done / fields.length * 100) : 0;
      if (bar) bar.style.width = pct + '%';
      if (progress) { progress.setAttribute('aria-valuenow', pct); progress.classList.toggle('is-full', pct === 100); }

      if (!changed) return;
      // Mascot cheers at milestones, once each.
      if (reqLeft === 0 && !announcedReady) {
        announcedReady = true;
        submit.classList.add('is-ready');
        feel('excited', 1800, 'hop'); say('All set! Hit submit 🎉', 3200);
      } else if (pct >= 50 && pct < 100 && !cheered.half && fields.length > 3) {
        cheered.half = true; feel('happy', 1400, 'hop'); say('Halfway there!', 2200);
      }
    }

    form.addEventListener('focusin', function (e) {
      var f = e.target.closest('.fx-field');
      if (f) f.classList.add('is-focus');
      if (f && !f._seen) { f._seen = true; feel('thinking', 1100); }
    });
    form.addEventListener('focusout', function (e) {
      var f = e.target.closest('.fx-field');
      if (f) f.classList.remove('is-focus');
      refresh(true);
    });
    form.addEventListener('change', function () { refresh(true); });
    form.addEventListener('input', function (e) {
      var f = e.target.closest('.fx-field');
      if (f && f.classList.contains('has-error') && filled(f)) f.classList.remove('has-error');
    });

    initRatings(form);

    form.addEventListener('submit', function () {
      if (submit.classList.contains('is-busy')) return;
      submit.classList.add('is-busy');
      submit.querySelector('.fx-submit-label').textContent = 'Sending…';
      feel('surprised', 2000, 'hop');
    });
    // Back/forward cache: never come back to a stuck "Sending…" button.
    window.addEventListener('pageshow', function () {
      submit.classList.remove('is-busy');
      submit.querySelector('.fx-submit-label').textContent = 'Submit';
    });

    refresh(false);

    // Server sent errors back: point at the first one.
    var firstErr = form.querySelector('.fx-field.has-error') || document.querySelector('.fx-alert');
    if (firstErr) {
      setTimeout(function () {
        firstErr.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
        var el = firstErr.querySelector && firstErr.querySelector('input:not([type=radio]):not([type=checkbox]), select, textarea');
        if (el) el.focus({ preventScroll: true });
      }, 450);
      feel('sad', 2600); say('A couple of answers need a fix.', 3400);
    } else if (mascot) {
      var n = fields.length;
      setTimeout(function () {
        feel('happy', 1500, 'wave');
        say(n <= 1 ? 'Hi! Just one quick question.' : 'Hi! Just ' + n + ' quick questions.', 3200);
      }, 700);
    }

    var lines = ['Take your time!', 'Every answer helps us 💛', 'Psst… I’m crunchy.', 'Almost there?'];
    var k = 0, btn = document.getElementById('fx-mascot-btn');
    if (btn) btn.addEventListener('click', function () { feel(['laughing', 'wink', 'love', 'happy'][k % 4], 1400, 'hop'); say(lines[k++ % lines.length], 2200); });
  }

  /* Rating: light up every chip up to the chosen one, preview on hover. */
  var SAYS = ['', 'Oh no. Tell us what went wrong.', 'Could be better.', 'Okay, noted.', 'Nice! Glad you liked it.', 'Loved it! 💛'];
  var MOODS = ['', 'sad', 'thinking', 'happy', 'excited', 'love'];
  function initRatings(form) {
    [].forEach.call(form.querySelectorAll('.fx-rating'), function (group) {
      var labels = [].slice.call(group.querySelectorAll('.fx-rate'));
      var caption = group.parentNode.querySelector('.fx-rate-say');
      function light(n) { labels.forEach(function (l, i) { l.classList.toggle('is-lit', i < n); }); }
      function chosen() { var c = group.querySelector('input:checked'); return c ? +c.value : 0; }
      labels.forEach(function (l, i) {
        l.addEventListener('mouseenter', function () { light(i + 1); });
        l.addEventListener('mouseleave', function () { light(chosen()); });
        l.querySelector('input').addEventListener('change', function () {
          var n = i + 1;
          light(n);
          labels.forEach(function (x) { x.classList.remove('is-pick'); });
          void l.offsetWidth; l.classList.add('is-pick');
          if (caption) caption.textContent = SAYS[n];
          feel(MOODS[n], 2200, n >= 4 ? 'hop' : null);
          if (navigator.vibrate && n >= 4) try { navigator.vibrate(12); } catch (e) {}
        });
      });
      var c = chosen();
      light(c);
      if (c && caption) caption.textContent = SAYS[c];
    });
  }

  /* ═══ THANKS PAGE ═══════════════════════════════════════════════════ */
  function initDone() {
    var done = document.getElementById('fx-done');
    if (!done) return;
    var stage = done.querySelector('.fx-stage');
    setTimeout(function () {
      confetti(stage, 38);
      feel('excited', 0, 'hop');
      if (mascot && mascot.emote) setTimeout(function () { mascot.emote('love'); }, 1700);
    }, reduce ? 0 : 450);

    var copy = done.querySelector('.fx-copy');
    if (copy) copy.addEventListener('click', function () {
      var code = copy.getAttribute('data-copy');
      function ok() {
        copy.classList.add('is-copied');
        copy.querySelector('span').textContent = 'Copied!';
        feel('wink', 1400, 'hop');
        setTimeout(function () { copy.classList.remove('is-copied'); copy.querySelector('span').textContent = 'Copy'; }, 2000);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(code).then(ok, function () {});
      else {
        var t = document.createElement('textarea'); t.value = code; document.body.appendChild(t); t.select();
        try { document.execCommand('copy'); ok(); } catch (e) {} t.remove();
      }
    });
  }

  function init() { initForm(); initDone(); }
  // Wait for the mascot element (if any) so its methods exist.
  var start = function () { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init(); };
  if (mascot && window.customElements && !customElements.get('vt-mascot')) customElements.whenDefined('vt-mascot').then(start);
  else start();
})();
