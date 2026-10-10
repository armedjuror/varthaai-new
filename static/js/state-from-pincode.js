/* Prefill a GST state <select> from a 6-digit pincode.
   Reads the prefix tables rendered with {{ pin_state_map|json_script:"pinStateMap" }}.
   Usage: bindStateFromPincode(pincodeInput, stateSelect) — only fills an empty select. */
(function () {
  var map = null;
  function tables() {
    if (map === null) {
      var el = document.getElementById('pinStateMap');
      try { map = el ? JSON.parse(el.textContent) : { p3: {}, p2: {} }; } catch (e) { map = { p3: {}, p2: {} }; }
    }
    return map;
  }
  window.stateFromPincode = function (pin) {
    var digits = String(pin || '').replace(/\D/g, '');
    if (digits.length !== 6) return '';
    var t = tables();
    return t.p3[digits.slice(0, 3)] || t.p2[digits.slice(0, 2)] || '';
  };
  window.bindStateFromPincode = function (pinInput, stateSelect) {
    if (!pinInput || !stateSelect) return;
    var handler = function () {
      var code = window.stateFromPincode(pinInput.value);
      if (code && (!stateSelect.value || stateSelect.getAttribute('data-auto') === '1')) {
        stateSelect.value = code;
        stateSelect.setAttribute('data-auto', '1');
      }
    };
    pinInput.addEventListener('input', handler);
    pinInput.addEventListener('change', handler);
    stateSelect.addEventListener('change', function () { stateSelect.setAttribute('data-auto', '0'); });
  };
})();
