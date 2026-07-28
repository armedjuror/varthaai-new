/**
 * Common Alert Modal System
 * Replaces all alert(), confirm(), showAlert(), and showNotification() calls
 */

// Alert Modal HTML (will be injected into body)
const ALERT_MODAL_HTML = `
<div class="modal fade" id="commonAlertModal" tabindex="-1" aria-labelledby="commonAlertModalLabel" aria-hidden="true" data-bs-backdrop="static" data-bs-keyboard="false">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content">
      <div class="modal-header" id="alertModalHeader">
        <h5 class="modal-title" id="alertModalTitle">
          <i class="fas fa-info-circle me-2" id="alertModalIcon"></i>
          <span id="alertModalTitleText">Alert</span>
        </h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close" id="alertModalCloseBtn"></button>
      </div>
      <div class="modal-body" id="alertModalBody">
        <p id="alertModalMessage"></p>
      </div>
      <div class="modal-footer" id="alertModalFooter">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal" id="alertModalCancelBtn" style="display: none;">Cancel</button>
        <button type="button" class="btn btn-primary" data-bs-dismiss="modal" id="alertModalOkBtn">OK</button>
      </div>
    </div>
  </div>
</div>
`;

// Initialize modal on page load
let alertModalInitialized = false;

function initializeAlertModal() {
  if (alertModalInitialized) return;
  
  // Inject modal HTML if it doesn't exist
  if (!document.getElementById('commonAlertModal')) {
    document.body.insertAdjacentHTML('beforeend', ALERT_MODAL_HTML);
  }
  
  alertModalInitialized = true;
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeAlertModal);
} else {
  initializeAlertModal();
}

/**
 * Show alert modal
 * @param {string} message - The message to display
 * @param {string} type - Type of alert: 'success', 'error'/'danger', 'warning', 'info' (default)
 * @param {string} title - Optional custom title
 * @param {function} onConfirm - Optional callback when OK is clicked
 * @param {function} onCancel - Optional callback when Cancel is clicked (only for confirm dialogs)
 * @returns {Promise} - Resolves when OK is clicked, rejects when Cancel is clicked
 */
function showAlertModal(message, type = 'info', title = null, onConfirm = null, onCancel = null) {
  return new Promise((resolve, reject) => {
    initializeAlertModal();
    
    const modal = document.getElementById('commonAlertModal');
    const modalTitle = document.getElementById('alertModalTitleText');
    const modalMessage = document.getElementById('alertModalMessage');
    const modalHeader = document.getElementById('alertModalHeader');
    const modalIcon = document.getElementById('alertModalIcon');
    const modalOkBtn = document.getElementById('alertModalOkBtn');
    const modalCancelBtn = document.getElementById('alertModalCancelBtn');
    
    // Set message
    modalMessage.textContent = message;
    
    // Set title
    if (title) {
      modalTitle.textContent = title;
    } else {
      // Default titles based on type
      const titles = {
        'success': 'Success',
        'error': 'Error',
        'danger': 'Error',
        'warning': 'Warning',
        'info': 'Information'
      };
      modalTitle.textContent = titles[type] || 'Alert';
    }
    
    // Set type styling
    const typeClasses = {
      'success': 'alert-success',
      'error': 'alert-danger',
      'danger': 'alert-danger',
      'warning': 'alert-warning',
      'info': 'alert-info'
    };
    
    const iconClasses = {
      'success': 'fa-check-circle text-success',
      'error': 'fa-exclamation-circle text-danger',
      'danger': 'fa-exclamation-circle text-danger',
      'warning': 'fa-exclamation-triangle text-warning',
      'info': 'fa-info-circle text-info'
    };
    
    // Remove previous type classes
    modalHeader.classList.remove('alert-success', 'alert-danger', 'alert-warning', 'alert-info', 'bg-success', 'bg-danger', 'bg-warning', 'bg-info', 'text-white');
    
    // Add new type class
    const typeClass = typeClasses[type] || 'alert-info';
    modalHeader.classList.add(typeClass, 'bg-' + type.replace('error', 'danger'));
    if (type === 'success' || type === 'error' || type === 'danger' || type === 'warning') {
      modalHeader.classList.add('text-white');
    }
    
    // Set icon
    modalIcon.className = 'fas me-2 ' + (iconClasses[type] || iconClasses['info']);
    
    // Show/hide cancel button based on whether onCancel is provided
    if (onCancel) {
      modalCancelBtn.style.display = 'inline-block';
    } else {
      modalCancelBtn.style.display = 'none';
    }
    
    // Set up event listeners
    const handleConfirm = () => {
      if (onConfirm) {
        onConfirm();
      }
      resolve(true);
    };
    
    const handleCancel = () => {
      if (onCancel) {
        onCancel();
      }
      reject(false);
    };
    
    // Remove previous listeners
    const newOkBtn = modalOkBtn.cloneNode(true);
    modalOkBtn.parentNode.replaceChild(newOkBtn, modalOkBtn);
    newOkBtn.addEventListener('click', handleConfirm);
    
    if (onCancel) {
      const newCancelBtn = modalCancelBtn.cloneNode(true);
      modalCancelBtn.parentNode.replaceChild(newCancelBtn, modalCancelBtn);
      newCancelBtn.addEventListener('click', handleCancel);
    }
    
    // Show modal using Bootstrap (reuse existing instance to avoid backdrop leaks)
    const bsModal = bootstrap.Modal.getInstance(modal) || new bootstrap.Modal(modal);
    bsModal.show();
    
    // Clean up on modal hidden
    modal.addEventListener('hidden.bs.modal', function cleanup() {
      modal.removeEventListener('hidden.bs.modal', cleanup);
      // Reset to default state
      modalHeader.classList.remove('alert-success', 'alert-danger', 'alert-warning', 'alert-info', 'bg-success', 'bg-danger', 'bg-warning', 'bg-info', 'text-white');
      modalHeader.classList.add('alert-info');
    }, { once: true });
  });
}

/**
 * Replacement for alert() - shows info modal
 */
function alert(message) {
  return showAlertModal(message, 'info');
}

/**
 * Replacement for confirm() - shows confirmation dialog
 */
function confirm(message) {
  return new Promise((resolve, reject) => {
    showAlertModal(
      message,
      'warning',
      'Confirm',
      () => resolve(true),
      () => reject(false)
    );
  });
}

// Make functions globally available
window.showAlertModal = showAlertModal;
window.alert = alert;
window.confirm = confirm;

// For jQuery compatibility (if jQuery is available)
if (typeof jQuery !== 'undefined') {
  jQuery.showAlertModal = showAlertModal;
  jQuery.alert = alert;
  jQuery.confirm = confirm;
}

