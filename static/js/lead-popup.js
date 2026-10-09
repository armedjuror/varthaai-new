/* Varthaai Storefront — Lead Capture Popup
 * Content/fields/coupon are admin-configurable (Settings -> Lead Popup).
 * Shows once per browser (localStorage), never to a logged-in customer.
 */

var LEAD_POPUP_DISMISSED_KEY = 'varthaai_lead_popup_dismissed';
var LEAD_POPUP_FIELD_DEFS = []; // [{key, label, type, required, extra}] built from config
var _crunchAudioCtx = null;
var _crunchAudioUrl = ''; // real uploaded sound, set from config — falls back to synthesized

/* Real uploaded sound if the admin has set one; else a synthesized crunch. */
function playCrunchSound() {
  if (_crunchAudioUrl) {
    var el = document.getElementById('lead-popup-crunch-audio');
    if (el) {
      try {
        el.currentTime = 0;
        el.play();
        return;
      } catch (e) {
        // fall through to synthesized sound
      }
    }
  }
  playSynthesizedCrunch();
}

/* Synthesized crunch — filtered noise bursts, no audio file/licensing needed. */
function playSynthesizedCrunch() {
  try {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!_crunchAudioCtx) _crunchAudioCtx = new Ctx();
    var ctx = _crunchAudioCtx;
    if (ctx.state === 'suspended') ctx.resume();

    var duration = 1.0;
    var bufferSize = Math.floor(ctx.sampleRate * duration);
    var buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    var data = buffer.getChannelData(0);

    var pulseCount = 6;
    for (var p = 0; p < pulseCount; p++) {
      var start = Math.floor((p / pulseCount) * bufferSize + Math.random() * 1500);
      var pulseLen = Math.floor(ctx.sampleRate * 0.05);
      for (var i = 0; i < pulseLen && start + i < bufferSize; i++) {
        var decay = Math.exp(-i / (pulseLen * 0.25));
        data[start + i] += (Math.random() * 2 - 1) * decay;
      }
    }

    var source = ctx.createBufferSource();
    source.buffer = buffer;

    var filter = ctx.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = 3200;
    filter.Q.value = 0.6;

    var gain = ctx.createGain();
    gain.gain.value = 0.9;

    source.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    source.start();
  } catch (e) {
    // Web Audio unavailable/blocked — silently skip, this is a delight-only feature.
  }
}

function leadPopupAlreadyHandled() {
  try {
    return localStorage.getItem(LEAD_POPUP_DISMISSED_KEY) === '1';
  } catch (e) {
    return false;
  }
}

function markLeadPopupHandled() {
  try {
    localStorage.setItem(LEAD_POPUP_DISMISSED_KEY, '1');
  } catch (e) {
    // localStorage unavailable (private mode etc) — fine to show again next load
  }
}

function leadPopupEscHtml(str) {
  return $('<div>').text(str == null ? '' : str).html();
}

function slugifyFieldLabel(label) {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'field';
}

function renderLeadPopupFields(config) {
  LEAD_POPUP_FIELD_DEFS = [];
  var html = '';

  if (config.collect_name) {
    LEAD_POPUP_FIELD_DEFS.push({ key: 'name', label: 'Your name', type: 'text', required: false });
  }
  if (config.collect_email) {
    LEAD_POPUP_FIELD_DEFS.push({ key: 'email', label: 'Email', type: 'email', required: !!config.require_email });
  }
  if (config.collect_phone) {
    LEAD_POPUP_FIELD_DEFS.push({ key: 'phone', label: 'Mobile number', type: 'tel', required: !!config.require_phone });
  }
  (config.extra_fields || []).forEach(function (label) {
    LEAD_POPUP_FIELD_DEFS.push({ key: slugifyFieldLabel(label), label: label, type: 'text', required: false, extra: true });
  });

  LEAD_POPUP_FIELD_DEFS.forEach(function (f) {
    html += '<div class="mb-3">' +
      '<label class="form-label" for="lead-popup-field-' + f.key + '">' + leadPopupEscHtml(f.label) +
      (f.required ? ' <span class="text-danger">*</span>' : '') + '</label>' +
      '<input type="' + f.type + '" class="form-control" id="lead-popup-field-' + f.key + '"' +
      (f.required ? ' required' : '') + '>' +
      '</div>';
  });
  $('#lead-popup-fields').html(html);
}

function validateLeadPopupForm() {
  var hasEmailOrPhone = false;
  var valid = true;
  LEAD_POPUP_FIELD_DEFS.forEach(function (f) {
    var val = $('#lead-popup-field-' + f.key).val();
    val = val ? val.trim() : '';
    if (f.required && !val) valid = false;
    if ((f.key === 'email' || f.key === 'phone') && val) hasEmailOrPhone = true;
  });
  if (!valid) {
    showAlertModal('Please fill in the required field(s).', 'warning');
    return false;
  }
  if (!hasEmailOrPhone) {
    showAlertModal('Please enter your email or mobile number.', 'warning');
    return false;
  }
  if ($('#lead-popup-field-phone').length) {
    var phoneVal = $('#lead-popup-field-phone').val().trim();
    if (phoneVal && !/^\d{10}$/.test(phoneVal)) {
      showAlertModal('Please enter a valid 10-digit mobile number.', 'warning');
      return false;
    }
  }
  return true;
}

function submitLeadPopup(recaptchaToken) {
  var payload = { recaptcha_token: recaptchaToken || '' };
  var extra = {};
  LEAD_POPUP_FIELD_DEFS.forEach(function (f) {
    var val = $('#lead-popup-field-' + f.key).val();
    val = val ? val.trim() : '';
    if (!val) return;
    if (f.extra) {
      extra[f.label] = val;
    } else {
      payload[f.key] = val;
    }
  });
  // JSON-encoded: a plain nested object would flatten to extra[key]=val in
  // the form POST, which DRF's form parser won't reassemble into a dict.
  payload.extra = JSON.stringify(extra);

  $.ajax({
    url: '/api/submit-lead/',
    type: 'POST',
    data: payload,
    dataType: 'json',
    success: function (response) {
      if (response.success) {
        markLeadPopupHandled();
        if (typeof window.dataLayer !== 'undefined') {
          window.dataLayer.push({ event: 'generate_lead' });
        }
        $('#lead-popup-form').addClass('d-none');
        $('#lead-popup-success-message').text(response.message || "Thanks! Here's your code:");
        if (response.coupon_code) {
          $('#lead-popup-coupon-code').text(response.coupon_code);
          $('#lead-popup-voucher').removeClass('d-none');
        }
        $('#lead-popup-success').removeClass('d-none');
      } else {
        showAlertModal(response.message || 'Something went wrong. Please try again.', 'error');
      }
    },
    error: function () {
      showAlertModal('Could not submit. Please try again.', 'error');
    }
  });
}

$(function () {
  $('#crunch-btn').on('click', function () {
    playCrunchSound();
    $('#lead-popup-mascot').removeClass('crunching');
    // force reflow so the animation re-triggers on repeated taps
    void $('#lead-popup-mascot')[0].offsetWidth;
    $('#lead-popup-mascot').addClass('crunching');
    if (typeof window.dataLayer !== 'undefined') {
      window.dataLayer.push({ event: 'crunch_sound_played' });
    }
  });

  $('#lead-popup-copy-btn').on('click', function () {
    var code = $('#lead-popup-coupon-code').text();
    if (!code) return;
    var done = function () {
      var $btn = $('#lead-popup-copy-btn');
      $btn.html('<i class="fas fa-check"></i>');
      setTimeout(function () { $btn.html('<i class="fas fa-copy"></i>'); }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(code).then(done).catch(function () {});
    }
  });

  if (STOREFRONT_LOGGED_IN || leadPopupAlreadyHandled()) return;

  $.ajax({
    url: '/api/lead-popup/',
    type: 'GET',
    dataType: 'json',
    success: function (config) {
      if (!config.success || !config.enabled) return;
      if (!config.collect_email && !config.collect_phone) return; // nothing to collect

      renderLeadPopupFields(config);
      $('#lead-popup-headline').text(config.headline);
      $('#lead-popup-body').text(config.body || '');
      $('#lead-popup-submit-btn').text(config.button_text || 'Get My Code');
      $('#leadPopupModal').attr('data-popup-theme', config.theme || 'regular');

      if (config.badge_text) {
        $('#lead-popup-badge-text').text(config.badge_text).removeClass('d-none');
      }

      if (config.crunch_enabled === false) {
        $('.lead-popup-mascot-wrap, #lead-popup-crunch-hint').addClass('d-none');
      } else if (config.crunch_audio_url) {
        _crunchAudioUrl = config.crunch_audio_url;
        $('#lead-popup-crunch-audio').attr('src', config.crunch_audio_url).attr('preload', 'auto');
      }

      var delayMs = Math.max(0, (config.delay_seconds || 8)) * 1000;
      setTimeout(function () {
        if (!leadPopupAlreadyHandled()) {
          $('#leadPopupModal').modal('show');
        }
      }, delayMs);
    }
  });

  $('#leadPopupModal').on('hidden.bs.modal', function () {
    markLeadPopupHandled();
  });
});
