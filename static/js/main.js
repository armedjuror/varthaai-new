let products = {}
let order = {items: [], coupon: null, total: 0, saleTotal: 0, saleDiscount:0, couponDiscount:0, deliveryCharge: 0, final: 0}

// Functions

// Track source (UTM parameters and referrals)
function trackSource() {
    const urlParams = new URLSearchParams(window.location.search);
    let source = urlParams.get('utm_source');
    let referral = urlParams.get('ref');
    
    if (source || referral) {
        $('#loader').fadeIn()
        $.ajax({
            url: '/api/track-source/',
            type: 'POST',
            data: {
                source: source,
                referral: referral
            },
            success: function(response) {
                console.log('Source tracked successfully');
                
                // If it's a referral, store it
                if (referral) {
                    localStorage.setItem('referral', referral);
                }
                $('#loader').fadeOut()
            },
            error: function(xhr, status, error) {
                console.error('Error tracking source:', error);
                $('#loader').fadeOut()
            }
        });
    }
}

// Load flavors from database

function showCouponMessage(element, message, type) {
  element.removeClass('text-success text-danger');
  element.addClass(type === 'success' ? 'text-success' : 'text-danger');
  element.text(message);
}

function renderFlavorsHome(){
  const flavorSelect = $('#flavor');
  const flavorTabs = $('#flavor-tabs');
  flavorTabs.empty()
  flavorSelect.empty();
  flavorSelect.append('<option value="" selected disabled>Select a flavour</option>');
  if (Object.keys(products).length) {
    let i=0;
    for (const productId in products){
      let flavor = products[productId];
      flavorSelect.append(`<option value="${flavor.id}">${flavor.name}</option>`);
      if(!i){
        flavorTabs.append(`<div class="flavor-tab active" data-flavor="${flavor.id}"><span>${flavor.name}</span></div>`)
        document.getElementById('active-product-name').innerText = flavor.name;
        document.getElementById('active-product-tagline').innerText = flavor.description;
        document.getElementById('active-ingredients').innerText = flavor.ingredients;
        document.getElementById('active-product-image').src = flavor.image;
        const active_nutrition = $('#active-nutrition');
        active_nutrition.empty()
        Object.keys(flavor.nutrition_fact).forEach(function(key, i){
          active_nutrition.append(`<div class="nutrition-item"><span class="value">${flavor.nutrition_fact[key]}</span><span class="label">${key}</span></div>`);
        });
      }else{
        flavorTabs.append(`<div class="flavor-tab" data-flavor="${flavor.id}"><span>${flavor.name}</span></div>`)
      }
      i++;
    }
  }else{
    flavorSelect.empty();
    flavorSelect.append('<option value="" selected disabled>Select a flavour</option>');
  }
  flavorSelect.trigger('change');
}

async function loadProducts() {
    $('#loader').fadeIn()
    return $.ajax({
        url: '/api/flavors/',
        type: 'GET',
        dataType: 'json',
        success: function(data) {
            if (data.flavors && data.flavors.length > 0) {
              data.flavors.forEach(function (flavor) {
                products[flavor.id] = flavor;
              });
            }
            console.log("H", products)
            $('#loader').fadeOut()
        },
        error: function(xhr, status, error) {
            console.error('Error loading flavors:', error);
            $('#loader').fadeOut()
        }
    });
}

// Load reviews
function loadReviews() {
    $('#loader').fadeIn()
    $.ajax({
        url: '/api/reviews/',
        type: 'GET',
        dataType: 'json',
        success: function(data) {
            const reviewsContainer = $('.reviews-slider');
            reviewsContainer.empty();
            
            if (data.reviews && data.reviews.length > 0) {
                data.reviews.forEach(function(review) {
                    let starsHtml = '';
                    for (let i = 1; i <= 5; i++) {
                        if (i <= review.rating) {
                            starsHtml += '<i class="fas fa-star"></i>';
                        } else {
                            starsHtml += '<i class="far fa-star"></i>';
                        }
                    }
                    
                    const reviewHtml = `
                        <div class="review-card">
                            <div class="review-header">
                                <div class="reviewer-info">
                                    <div class="reviewer-name mb-1">${review.name}</div>
                                    <div class="reviewer-designation mb-1">${review.designation}</div>
                                    <div class="review-date">${review.date}</div>
                                </div>
                                <div class="review-rating">
                                    ${starsHtml}
                                </div>
                            </div>
                            <div class="review-content">
                                "${review.review}"
                            </div>
                        </div>
                    `;
                    
                    reviewsContainer.append(reviewHtml);
                });
                
                // Initialize slider (can use slick carousel here)
                $('.reviews-slider').slick({
                    slidesToShow: 3,
                    slidesToScroll: 1,
                    autoplay: true,
                    autoplaySpeed: 1000,
                    responsive: [
                        {
                            breakpoint: 992,
                            settings: {
                                slidesToShow: 2
                            }
                        },
                        {
                            breakpoint: 576,
                            settings: {
                                slidesToShow: 1
                            }
                        }
                        
                    ]
                });
                $('.slick-arrow').addClass('d-none');
                $('#loader').fadeOut()

            } else {
                $('.review-empty').removeClass('d-none');
                $('#loader').fadeOut()
            }
        },
        error: function(xhr, status, error) {
            console.error('Error loading reviews:', error);
            $('.review-empty').removeClass('d-none');
            $('#loader').fadeOut()
        }
    });
}

// Load latest blogs
function loadLatestBlogs() {
    $.ajax({
        url: '/api/blogs/?limit=3',
        type: 'GET',
        dataType: 'json',
        success: function(response) {
            const blogContainer = $('#blog-posts');
            const blogSection = $('.blog-section');
            
            if (response.success && response.data && response.data.length > 0) {
                blogContainer.empty();
                blogSection.show(); // Show the section if blogs exist
                
                response.data.forEach(function(blog) {
                    const imageHtml = blog.featured_image 
                        ? `<img src="${blog.featured_image}" alt="${blog.title}" class="img-fluid rounded mb-3" style="height: 200px; object-fit: cover; width: 100%;">`
                        : '';
                    
                    const date = new Date(blog.published_at || blog.created_at);
                    const formattedDate = date.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
                    
                    // Parse markdown excerpt or generate from content
                    let excerpt = blog.excerpt || '';
                    if (!excerpt && blog.content) {
                        // Strip markdown and get first 150 chars
                        const plainText = blog.content.replace(/[#*`_~\[\]()]/g, '').replace(/\n/g, ' ').trim();
                        excerpt = plainText.substring(0, 150);
                        if (plainText.length > 150) excerpt += '...';
                    }
                    
                    const blogHtml = `
                        <div class="col-md-4">
                            <div class="blog-card h-100" style="border: 1px solid #eee; border-radius: 10px; overflow: hidden; transition: transform 0.3s ease;">
                                ${imageHtml}
                                <div class="p-3">
                                    <h5 class="mb-2"><a href="/blog-detail/?slug=${blog.slug}" style="color: inherit; text-decoration: none;">${blog.title}</a></h5>
                                    <p class="text-muted small mb-2">${formattedDate}</p>
                                    <p class="mb-3">${excerpt}</p>
                                    <a href="/blog-detail/?slug=${blog.slug}" class="btn btn-sm btn-outline-primary">Read More</a>
                                </div>
                            </div>
                        </div>
                    `;
                    
                    blogContainer.append(blogHtml);
                });
            } else {
                // Hide the entire blog section if no blogs
                blogSection.hide();
            }
        },
        error: function(xhr, status, error) {
            console.error('Error loading blogs:', error);
            // Hide section on error too
            $('.blog-section').hide();
        }
    });
}

function addOrderItem(productId, quantity){
    // let flavorId = $('#flavor').val();
    // let quantity;
    // let quantityVal = $('#quantity').val();
    // let customQuantity = $('#custom-quantity').val();
    // if (quantityVal == 'custom')quantity = parseInt(customQuantity);
    quantity = parseInt(quantity);
    let product = products[productId];
    console.log(product)
    order.items.push({
        'id': productId,
        'name': product.name,
        'price_per_kg': product.price_per_kg,
        'sale_price_per_kg': product.sale_price_per_kg,
        'image': product.image,
        'quantity': quantity
    });
    // $('#flavor').val("");
    // $('#flavor').trigger("change");
    // $('#quantity').val(100);
    // $('#quantity').trigger("change");
    return processOrder().then(order=>
      Promise.resolve(order)
    );
}

function removeOrderItem(index){
    order.items.splice(index, 1);
    return processOrder().then(order=>
      Promise.resolve(order)
    );
}

function processOrder(){
    order.total = 0;
    order.saleTotal = 0;
    order.saleDiscount = 0;
    for(let i=0; i< order.items.length; i++){
        item = order.items[i];
        order.items[i].price = item.price_per_kg * item.quantity/1000;
        order.items[i].salePrice = (item.sale_price_per_kg * item.quantity) /1000;
        order.total += order.items[i].price;
        order.saleTotal += order.items[i].salePrice;
    }
    order.saleDiscount = order.total - order.saleTotal;
    if (!order.coupon)order.couponDiscount = 0;
    if (order.saleTotal < 300){
      order.deliveryCharge = 50;
    }else{
      order.deliveryCharge = 0;
    }
    order.final = Math.round(order.saleTotal - order.couponDiscount + order.deliveryCharge);
    return Promise.resolve(order);
}

function renderOrderForm(){
    $('#invoice-items').empty()
    if (!order.items.length){
        const row = document.createElement('tr');
        row.id = `invoice-row`;
        row.innerHTML = `<td colspan="4" class="text-center">No Item Added!</td>`
        $('#invoice-items').append(row)
    }
    for (let i=0; i< order.items.length; i++){
        let item = order.items[i];
        const row = document.createElement('tr');
        row.id = `invoice-row-${i}`;
        row.innerHTML = `
            <td class="ps-3">
                <span class="fw-medium">${item.flavor_name}</span>
            </td>
            <td>${item.quantity}g</td>
            <td>₹${item.salePrice}<span class="original-price">₹${item.price}</span></td>
            <td class="text-end pe-3">
            <button type="button" class="btn btn-sm btn-outline-danger delete-invoice-item" data-item-index="${i}">
            <i class="fa fa-trash"></i>
            </button></td>
        `;
        $('#invoice-items').append(row)
    }
    $('#total-price').text(`₹${order.total}`);
    if (order.saleDiscount){
        $('#sale-discount-container').removeClass('d-none');
        $('#sale-discount').text(`-₹${order.saleDiscount}`);
    }else{
        $('#sale-discount-container').addClass('d-none');
    }
    if (order.coupon){
        $('#coupon-discount-container').removeClass('d-none');
        $('#coupon-discount').text(`-₹${order.couponDiscount}`);
    }else{
        $('#coupon-discount-container').addClass('d-none');
    }
    $('#final-price').text(`₹${order.saleTotal}`);
    
}

function validateCoupon(couponCode) {
    if (!couponCode) {
        order.coupon = null;
        // $('#coupon-message').removeClass('valid-coupon').addClass('invalid-coupon').text('Please enter a coupon code');
        // $('#desktop-coupon-message').removeClass('valid-coupon').addClass('invalid-coupon').text('Please enter a coupon code');
        // $('#mobile-coupon-message').removeClass('valid-coupon').addClass('invalid-coupon').text('Please enter a coupon code');
        return Promise.resolve({
            success: false,
            discount_amount: 0,
            message: 'Please enter a coupon code'
        });
    }
    $('#loader').fadeIn()
    return $.ajax({
        url: '/api/validate-coupon/',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            coupon_code: couponCode,
            order: order
        }),
        dataType: 'json'
    }).then(function(response) {
        console.log(response)
        $('#loader').fadeOut()
        if (response.success) {
            // $('#desktop-coupon-message').removeClass('invalid-coupon').addClass('valid-coupon').text(response.message);
            // $('#mobile-coupon-message').removeClass('invalid-coupon').addClass('valid-coupon').text(response.message);
            return {
                success: true,
                discount_amount: response.data.discount_amount,
                message: response.message
            };
        } else {
            // Show error message
            // $('#desktop-coupon-message').removeClass('valid-coupon').addClass('invalid-coupon').text(response.message);
            // $('#mobile-coupon-message').removeClass('valid-coupon').addClass('invalid-coupon').text(response.message);
            order.coupon = null;
            order['couponDiscount'] = 0;
            return {
                success: false,
                discount_amount: 0,
                message: response.message
            };
        }
    }).catch(function(xhr, status, error) {
        console.error('Error validating coupon:', error);
        // $('#coupon-message').removeClass('valid-coupon').addClass('invalid-coupon').text('Error validating coupon. Please try again.');
        $('#loader').fadeOut()
        return {
            success: false,
            discount_amount: 0,
            message: 'Error validating coupon. Please try again.'
        };
    });
}

// Get user data if logged in
function getUserData(userId) {
    $('#loader').fadeIn()
    $.ajax({
        url: '/api/user-data/',
        type: 'POST',
        data: {
            user_id: userId
        },
        dataType: 'json',
        success: function(data) {
            if (data.success) {
                $('#name').val(data.user.name);
                $('#mobile').val(data.user.mobile);
                $('#address').val(data.user.address);
                $('#pincode').val(data.user.pincode);
            }
            $('#loader').fadeOut()
        },
        error: function(xhr, status, error) {
            console.error('Error getting user data:', error);
            $('#loader').fadeOut()
        }
    });
}

// Check user by mobile number and prefill form
function checkUserByMobile(mobile) {
    // Validate mobile number is exactly 10 digits
    if (!mobile || !mobile.match(/^\d{10}$/)) {
        return;
    }
    
    $('#loader').fadeIn()
    $.ajax({
        url: '/api/get-user-by-mobile/',
        type: 'POST',
        data: {
            mobile: mobile
        },
        dataType: 'json',
        success: function(response) {
            $('#loader').fadeOut()
            if (response.success && response.user) {
                console.log(response.user)
                // Prefill form fields if user exists
                if (response.user.name) {
                    $('#name').val(response.user.name);
                }
                if (response.user.address) {
                    $('#address').val(response.user.address);
                }
                if (response.user.pincode) {
                    $('#pincode').val(response.user.pincode);
                }
            }
            // If user not found, do nothing - let user enter details manually
        },
        error: function(xhr, status, error) {
            console.error('Error checking user by mobile:', error);
            $('#loader').fadeOut()
        }
    });
}

// Place order
function placeOrder(recaptchaToken) {
    if (!recaptchaToken) {
        showAlertModal('reCAPTCHA verification failed. Please try again.', 'error');
        return;
    }
    if (!validateOrderBeforePayment()) {
      return;
    }
    order.name = $('#name').val()
    order.mobile = $('#mobile').val()
    order.address = $('#address').val()
    order.pincode = $('#pincode').val()
    order.referral = localStorage.getItem('referral') || null
    order.recaptcha_token = recaptchaToken || ''
    $('#loader').fadeIn()
    $.ajax({
        url: '/api/place-order/',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(order),
        dataType: 'json',
        success: function(response) {
            if (response.success) {
                $('#deliveryModal').modal('hide');
                initiateRazorpayPayment(response);
            } else {
                $('#loader').fadeOut()
                showAlertModal('Error: ' + response.message, 'error');
            }
        },
        error: function(xhr, status, error) {
            console.error('Error placing order:', error);
            $('#loader').fadeOut()
            showAlertModal('Error: Could not place order. Please try again.', 'error');
        }
    });
}

function initiateRazorpayPayment(orderData) {
  const options = {
    key: orderData.key_id,
    amount: orderData.amount * 100, // Amount in paise
    currency: 'INR',
    name: 'Varthaai',
    description: "The banana chips everyone's talking about!",
    image: 'https://varthaai.com/images/logo.png',
    order_id: orderData.razorpay_order_id,
    handler: function(response) {
      // Payment successful
      verifyPayment(response, orderData);
    },
    prefill: {
      name: orderData.customer_name,
      email: orderData.customer_email,
      contact: orderData.customer_mobile
    },
    notes: {
      order_id: orderData.order_id
    },
    theme: {
      color: '#86AA4E'
    },
    modal: {
      ondismiss: function() {
        // Payment cancelled
        handlePaymentCancellation(orderData.order_id);
      }
    }
  };

  const rzp = new Razorpay(options);

  rzp.on('payment.failed', function(response) {
    // Payment failed
    handlePaymentFailure(response, orderData);
  });

  rzp.open();
}

function verifyPayment(paymentResponse, orderData) {
  $('#loader').fadeIn();

  $.ajax({
    url: '/api/verify-payment/',
    type: 'POST',
    data: {
      razorpay_payment_id: paymentResponse.razorpay_payment_id,
      razorpay_order_id: paymentResponse.razorpay_order_id,
      razorpay_signature: paymentResponse.razorpay_signature
    },
    dataType: 'json',
    success: function(response) {
      $('#loader').fadeOut();

      if (response.success) {
        // Payment verified successfully
        showPaymentSuccessModal(response, orderData);

        // Reset forms and order
        resetOrderForms();

      } else {
        showAlertModal('Payment verification failed: ' + response.message, 'error');
      }
    },
    error: function(xhr, status, error) {
      $('#loader').fadeOut();
      console.error('Error verifying payment:', error);
      showAlertModal('Error verifying payment. Please contact support with your payment details.', 'error');
    }
  });
}

async function handlePaymentCancellation(orderId) {
  try {
    $('#loader').fadeOut()
    const confirmRetry = await confirm('Payment was cancelled. Would you like to try again?');
    if (confirmRetry) {
      $('#deliveryModal').modal('show');
    } else {
      // Cancel the order
      cancelOrder(orderId);
    }
  } catch {
    // User cancelled
    cancelOrder(orderId);
  }
}

async function handlePaymentFailure(response, orderData) {
  console.error('Payment failed:', response.error);

  let errorMessage = 'Payment failed. ';

  if (response.error && response.error.description) {
    errorMessage += response.error.description;
  } else {
    errorMessage += 'Please try again or contact support.';
  }

  await showAlertModal(errorMessage, 'error');

  // Show retry option
  try {
    const confirmRetry = await confirm('Would you like to retry the payment?');
    if (confirmRetry) {
      initiateRazorpayPayment(orderData);
    } else {
      cancelOrder(orderData.order_id);
    }
  } catch {
    // User cancelled
    cancelOrder(orderData.order_id);
  }
}

function showPaymentSuccessModal(verificationResponse, orderData) {
  // Update order ID
  $('#success-order-id').text(verificationResponse.order_id);

  // Update payment ID (truncated for display)
  const paymentId = verificationResponse.payment_id;
  const truncatedPaymentId = paymentId.substring(0, 20) + '...';
  $('#success-payment-id').text(truncatedPaymentId).attr('title', paymentId);

  // Update points earned
  $('#points-earned').text(verificationResponse.points_earned);

  // Show the modal
  $('#orderSuccessModal').modal('show');
}

function cancelOrder(orderId) {
  $('#loader').fadeIn()
  $.ajax({
    url: '/api/cancel-order/',
    type: 'POST',
    data: { order_id: orderId },
    dataType: 'json',
    success: function(response) {
      if (response.success) {
        $('#loader').fadeOut()
      }
    },
    error: function(xhr, status, error) {
      console.error('Error cancelling order:', error);
      $('#loader').fadeOut()
    }
  });
}

function resetOrderForms() {
  // Reset order object
  order = {items: [], coupon: null, total: 0, saleTotal: 0, saleDiscount: 0, couponDiscount: 0};

  // Reset forms
  $('#delivery-form')[0].reset();

  // Clear localStorage
  localStorage.removeItem('applied_coupon');
  localStorage.removeItem('discount_amount');

  // Re-render order form
  processOrder().then(function() {
    renderOrderForm();
  });
  $('#delivery-note').addClass('d-none');
}

function validateOrderBeforePayment() {
  // Check if items exist
  if (!order.items || order.items.length === 0) {
    showAlertModal('Please add items to your order before proceeding.', 'warning');
    return false;
  }

  // Check delivery form
  if (!validateDeliveryForm()) {
    return false;
  }

  // Check total amount
  if (order.saleTotal <= 0) {
    showAlertModal('Invalid order total. Please try again.', 'error');
    return false;
  }

  return true;
}

// Track order
function trackOrder(recaptchaToken) {
    if (!recaptchaToken) {
        showAlertModal('reCAPTCHA verification failed. Please try again.', 'error');
        return;
    }
    if (!validateTrackOrderForm()) {
        return;
    }
    const trackData = {
        mobile: $('#track-mobile').val(),
        order_id: $('#order-id').val(),
        recaptcha_token: recaptchaToken || ''
    };
    $('#loader').fadeIn()
    $.ajax({
        url: '/api/track-order/',
        type: 'POST',
        data: trackData,
        dataType: 'json',
        success: function(response) {
            if (response.success) {
                // Display order status
                $('#order-status-container').removeClass('d-none');
                
                let statusHtml = `
                    <div class="alert alert-info">
                        <strong>Order #${response.order.order_id}</strong><br>
                        Status: <strong>${response.order.status}</strong><br>
                        Ordered on: <strong>${response.order.order_date}</strong><br>
                        Payment Status: <strong>${response.order.payment_status}</strong><br>
                        Items:<br>`;
                for (let i=0; i < response.order.items.length; i++){
                    statusHtml += `<strong>${i+1}. ${response.order.items[i].flavor_name} : ${response.order.items[i].quantity}g</strong><br>`
                }
                statusHtml += `Amount: <strong>₹${response.order.total}</strong><br>
                            Delivery Charge: <strong>₹${response.order.delivery_charge}</strong>
                    </div>
                `;
                
                $('#order-status').html(statusHtml);
                $('#loader').fadeOut()
            } else {
                $('#order-status-container').removeClass('d-none');
                $('#order-status').html(`
                    <div class="alert alert-danger">
                        ${response.message}
                    </div>
                `);
                $('#loader').fadeOut()
            }
        },
        error: function(xhr, status, error) {
            console.error('Error tracking order:', error);
            $('#order-status-container').removeClass('d-none');
            $('#order-status').html(`
                <div class="alert alert-danger">
                    Could not track order. Please try again.
                </div>
            `);
            $('#loader').fadeOut()
        }
    });
}

// Submit review
function submitReview(recaptchaToken) {
    if (!recaptchaToken) {
        showAlertModal('reCAPTCHA verification failed. Please try again.', 'error');
        return;
    }
    if (!validateReviewForm()) {
        return;
    }
    const reviewData = {
        name: $('#review-name').val(),
        mobile: $('#review-mobile').val(),
        designation: $('#designation').val(),
        rating: $('#rating-value').val(),
        review: $('#review-text').val(),
        recaptcha_token: recaptchaToken || ''
    };
    $('#loader').fadeIn()

    $.ajax({
        url: '/api/submit-review/',
        type: 'POST',
        data: reviewData,
        dataType: 'json',
        success: function(response) {
            if (response.success) {
                // Close review modal
                $('#reviewModal').modal('hide');
                
                // Show alert
                $('#review-points-earned').text(response.points_earned);
                $('#loader').fadeOut()
                $('#reviewSuccessModal').modal('show');
                
                // Reset form
                $('#review-form')[0].reset();
                $('.rating i').removeClass('fas').addClass('far');
                $('#rating-value').val('0');
            } else {
                $('#loader').fadeOut()

                showAlertModal('Error: ' + response.message, 'error');
            }
        },
        error: function(xhr, status, error) {
            console.error('Error submitting review:', error);
            $('#loader').fadeOut()
            showAlertModal('Error: Could not submit review. Please try again.', 'error');
        }
    });
}

// Send OTP
function sendOTP(recaptchaToken) {
    $('#loader').fadeIn()
    const mobile = $('#login-mobile').val();
    
    $.ajax({
        url: '/api/send-otp/',
        type: 'POST',
        data: {
            mobile: mobile,
            recaptcha_token: recaptchaToken || ''
        },
        dataType: 'json',
        success: function(response) {
            if (response.success) {
                // Show OTP input screen
                $('#login-phone-step').hide();
                $('#login-otp-step').show();
                $('#display-mobile').text(mobile);
                $('#loader').fadeOut()
            } else {
                showAlertModal('Error: ' + response.message, 'error');
                $('#loader').fadeOut()
            }
        },
        error: function(xhr, status, error) {
            console.error('Error sending OTP:', error);
            $('#loader').fadeOut()
            showAlertModal('Error: Could not send OTP. Please try again.', 'error');
        }
    });
}

function resendOTP(recaptchaToken) {
  if (!recaptchaToken) {
    showAlertModal('reCAPTCHA verification failed. Please try again.', 'error');
    return;
  }
  $('otp-message').text("OTP resent!")
  sendOTP(recaptchaToken);
}

// Send OTP Start - reCAPTCHA callback
function sendOTPStart(recaptchaToken) {
    if (!recaptchaToken) {
        showAlertModal('reCAPTCHA verification failed. Please try again.', 'error');
        return;
    }
    const mobile = $('#login-mobile').val();
    if (!mobile || !mobile.match(/^\d{10}$/)) {
        showError('#login-mobile', 'Please enter a valid 10-digit mobile number');
        return;
    }
    sendOTP(recaptchaToken);
}

// Verify OTP
function verifyOTP() {
  $('#loader').fadeIn()

  const mobile = $('#login-mobile').val();
    const otp = $('#otp').val();

    $.ajax({
        url: '/api/verify-otp/',
        type: 'POST',
        data: {
            mobile: mobile,
            otp: otp
        },
        dataType: 'json',
        success: function(response) {
            if (response.success) {
                window.location = '/dashboard/';
            } else {
                showAlertModal('Error: ' + response.message, 'error');
                $('#loader').fadeOut()
            }
        },
        error: function(xhr, status, error) {
            console.error('Error verifying OTP:', error);
            showAlertModal('Error: Could not verify OTP. Please try again.', 'error');
            $('#loader').fadeOut()

        }
    });
}

// Update UI for logged in user
function updateLoggedInUI() {
    $('#login-btn').text('Dashboard').attr('href', '/dashboard/');
}

// Form validation functions
function validateProductForm() {
    let isValid = true;
    
    if (!$('#flavor').val()) {
        showError('#flavor', 'Please select a flavor');
        isValid = false;
    } else {
        removeError('#flavor');
    }
    
    if ($('#quantity').val() === 'custom' && (!$('#custom-quantity').val() || $('#custom-quantity').val() < 100)) {
        showError('#custom-quantity', 'Please enter a valid quantity (min 100g)');
        isValid = false;
    } else {
        removeError('#custom-quantity');
    }
    
    return isValid;
}

function validateDeliveryForm() {
    let isValid = true;
    
    if (!$('#name').val()) {
        showError('#name', 'Please enter your name');
        isValid = false;
    } else {
        removeError('#name');
    }
    
    if (!validateMobileNumber($('#mobile').val())) {
        isValid = false;
    }
    
    if (!$('#address').val()) {
        showError('#address', 'Please enter your delivery address');
        isValid = false;
    } else {
        removeError('#address');
    }
    
    if (!$('#pincode').val() || !$('#pincode').val().match(/^\d{6}$/)) {
        showError('#pincode', 'Please enter a valid 6-digit pincode');
        isValid = false;
    } else {
        removeError('#pincode');
    }
    
    return isValid;
}

function validateTrackOrderForm() {
    let isValid = true;
    
    if (!validateMobileNumber($('#track-mobile').val())) {
        isValid = false;
    }
    
    if (!$('#order-id').val()) {
        showError('#order-id', 'Please enter your order ID');
        isValid = false;
    } else {
        removeError('#order-id');
    }
    
    return isValid;
}

function validateReviewForm() {
    let isValid = true;
    
    if (!$('#review-name').val()) {
        showError('#review-name', 'Please enter your name');
        isValid = false;
    } else {
        removeError('#review-name');
    }
    
    if (!validateMobileNumber($('#review-mobile').val())) {
        isValid = false;
    }
    
    if ($('#rating-value').val() === '0') {
        showError('.rating', 'Please select a rating');
        isValid = false;
    } else {
        removeError('.rating');
    }
    
    if (!$('#review-text').val()) {
        showError('#review-text', 'Please enter your review');
        isValid = false;
    } else {
        removeError('#review-text');
    }
    
    return isValid;
}

function validateMobileNumber(mobile) {
    if (!mobile || !mobile.match(/^\d{10}$/)) {
        showError('#mobile, #track-mobile, #review-mobile, #login-mobile', 'Please enter a valid 10-digit mobile number');
        return false;
    } else {
        removeError('#mobile, #track-mobile, #review-mobile, #login-mobile');
        return true;
    }
}

function showError(selector, message) {
    $(selector).addClass('is-invalid');
    
    // Create error message if it doesn't exist
    if ($(selector).next('.invalid-feedback').length === 0) {
        $(selector).after(`<div class="invalid-feedback">${message}</div>`);
    } else {
        $(selector).next('.invalid-feedback').text(message);
    }
}

function removeError(selector) {
    $(selector).removeClass('is-invalid');
}



const bananaChipFacts = [
    "Banana chips were first created in India as a way to preserve bananas.",
    "Traditional banana chips are deep-fried in coconut oil.",
    "Banana chips can last up to 6 months in airtight containers.",
    "In Kerala, India, banana chips are called 'Upperi'.",
    "Commercial banana chips are made from underripe Nendran bananas.",
    "Banana chips are higher in calories than fresh bananas due to frying.",
    "Healthier versions are dehydrated instead of fried.",
    "A 100g serving of banana chips contains about 520 calories.",
    "Banana chips are rich in potassium and dietary fiber.",
    "In the Philippines, they're known as 'saba chips'.",
    "The yellow color often comes from added turmeric.",
    "Sweet banana chips are coated with honey or sugar syrup.",
    "Indonesian 'kripik pisang' are flavored with chili or caramelized sugar.",
    "The crunch comes from starch crystallizing during frying.",
    "Green bananas make starchier, less sweet chips.",
    "The global banana chips market is worth over $200 million.",
    "Plantain chips are often confused with banana chips.",
    "During WWII, banana chips were popular as non-perishable food.",
    "Freeze-dried banana slices create a lighter, crispier chip.",
    "Banana chips have been included in space mission food supplies."
];

// Function to show a random fact with fade-in effect
function showRandomFact() {
    const factElement = document.getElementById("fact");
    const randomIndex = Math.floor(Math.random() * bananaChipFacts.length);
    
    // Remove the active class
    factElement.classList.remove("active");
    
    // Set timeout to change the fact and add the active class
    setTimeout(() => {
        factElement.textContent = bananaChipFacts[randomIndex];
        factElement.classList.add("active");
    }, 500);
}

// Show first fact immediately
showRandomFact();

// Change fact every 5 seconds
setInterval(showRandomFact, 5000);

$('#login-btn').on('click', function() {
  $('#loginModal').modal('show');
  $('#login-phone-step').show();
  $('#login-otp-step').hide();
});

// Send OTP button

// Resend OTP link

// Verify OTP button
$('#verify-otp-btn').on('click', function() {
  if ($('#otp').val().length === 6) {
    verifyOTP();
  } else {
    showError('#otp', 'Please enter a valid 6-digit OTP');
  }
});

$('#track-order-btn').on('click', function() {
  $('#trackOrderModal').modal('show');
});

// Track Order Submit button - validation happens in trackOrder callback
$('#track-order-submit-btn').on('click', function(e) {
  if (!validateTrackOrderForm()) {
    e.preventDefault();
    e.stopPropagation();
    return false;
  }
});

// reCAPTCHA initialization
let recaptchaWidgets = {};
let recaptchaReady = false;

function onRecaptchaLoad() {
    recaptchaReady = true;
    initializeRecaptcha();
}

function initializeRecaptcha() {
    if (typeof grecaptcha === 'undefined' || !grecaptcha.render) {
        console.warn('reCAPTCHA not loaded yet');
        return;
    }

    // Render reCAPTCHA when modals are shown
    $('#deliveryModal').on('shown.bs.modal', function() {
        if (!recaptchaWidgets['confirm-order-btn']) {
            try {
                recaptchaWidgets['confirm-order-btn'] = grecaptcha.render('confirm-order-btn', {
                    'sitekey': '6Le6wbcrAAAAACAS_jUK2UM_vaNbbk2g2Xf5kvdC',
                    'callback': placeOrder,
                    'size': 'invisible'
                });
            } catch(e) {
                console.error('Error rendering reCAPTCHA for confirm-order-btn:', e);
            }
        }
    });

    $('#trackOrderModal').on('shown.bs.modal', function() {
        if (!recaptchaWidgets['track-order-submit-btn']) {
            try {
                recaptchaWidgets['track-order-submit-btn'] = grecaptcha.render('track-order-submit-btn', {
                    'sitekey': '6Le6wbcrAAAAACAS_jUK2UM_vaNbbk2g2Xf5kvdC',
                    'callback': trackOrder,
                    'size': 'invisible'
                });
            } catch(e) {
                console.error('Error rendering reCAPTCHA for track-order-submit-btn:', e);
            }
        }
    });

    $('#loginModal').on('shown.bs.modal', function() {
        if (!recaptchaWidgets['send-otp-btn']) {
            try {
                recaptchaWidgets['send-otp-btn'] = grecaptcha.render('send-otp-btn', {
                    'sitekey': '6Le6wbcrAAAAACAS_jUK2UM_vaNbbk2g2Xf5kvdC',
                    'callback': sendOTPStart,
                    'size': 'invisible'
                });
            } catch(e) {
                console.error('Error rendering reCAPTCHA for resend-otp:', e);
            }
        }
        if (!recaptchaWidgets['resend-otp']) {
          try {
            recaptchaWidgets['resend-otp'] = grecaptcha.render('resend-otp', {
              'sitekey': '6Le6wbcrAAAAACAS_jUK2UM_vaNbbk2g2Xf5kvdC',
              'callback': sendOTP,
              'size': 'invisible'
            });
          } catch(e) {
            console.error('Error rendering reCAPTCHA for send-otp-btn:', e);
          }
        }
    });

    $('#reviewModal').on('shown.bs.modal', function() {
        if (!recaptchaWidgets['submit-review-btn']) {
            try {
                recaptchaWidgets['submit-review-btn'] = grecaptcha.render('submit-review-btn', {
                    'sitekey': '6Le6wbcrAAAAACAS_jUK2UM_vaNbbk2g2Xf5kvdC',
                    'callback': submitReview,
                    'size': 'invisible'
                });
            } catch(e) {
                console.error('Error rendering reCAPTCHA for submit-review-btn:', e);
            }
        }
    });


}

// Try to initialize if reCAPTCHA is already loaded
$(document).ready(function() {
    // Check if reCAPTCHA is already loaded
    if (typeof grecaptcha !== 'undefined' && grecaptcha.render) {
        onRecaptchaLoad();
    }
});

// Update button click handlers to execute reCAPTCHA
$('#confirm-order-btn').on('click', function(e) {
    if (!validateOrderBeforePayment()) {
        e.preventDefault();
        return false;
    }
    if (recaptchaWidgets['confirm-order-btn']) {
        grecaptcha.execute(recaptchaWidgets['confirm-order-btn']);
    }
});

$('#track-order-submit-btn').on('click', function(e) {
    if (!validateTrackOrderForm()) {
        e.preventDefault();
        e.stopPropagation();
        return false;
    }
    if (recaptchaWidgets['track-order-submit-btn']) {
        grecaptcha.execute(recaptchaWidgets['track-order-submit-btn']);
    }
});

$('#send-otp-btn').on('click', function(e) {
    const mobile = $('#login-mobile').val();
    if (!mobile || !mobile.match(/^\d{10}$/)) {
        showError('#login-mobile', 'Please enter a valid 10-digit mobile number');
        e.preventDefault();
        return false;
    }
    if (recaptchaWidgets['send-otp-btn']) {
        grecaptcha.execute(recaptchaWidgets['send-otp-btn']);
    }
});

$('#submit-review-btn').on('click', function(e) {
    if (!validateReviewForm()) {
        e.preventDefault();
        return false;
    }
    if (recaptchaWidgets['submit-review-btn']) {
        grecaptcha.execute(recaptchaWidgets['submit-review-btn']);
    }
});
