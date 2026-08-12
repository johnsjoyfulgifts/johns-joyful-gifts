/*
 * Progressive-enhancement cart behaviour.
 * Every form here works as a plain HTML form POST + redirect if JS is
 * disabled or fetch fails; JS just makes it feel instant when available.
 */
(function () {
  "use strict";

  function showToast(message) {
    var existing = document.querySelector(".toast");
    if (existing) existing.remove();
    var toast = document.createElement("div");
    toast.className = "toast";
    toast.setAttribute("role", "status");
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function () {
      toast.remove();
    }, 2200);
  }

  function updateCartBadge(count) {
    var badge = document.querySelector(".icon-link[href='/cart'] .cart-badge");
    var cartLink = document.querySelector(".icon-link[href='/cart']");
    if (!cartLink) return;
    if (count > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "cart-badge";
        cartLink.appendChild(badge);
      }
      badge.textContent = count;
    } else if (badge) {
      badge.remove();
    }
  }

  async function submitCartForm(form) {
    var formData = new FormData(form);
    var action = form.getAttribute("action");
    try {
      var res = await fetch(action, {
        method: "POST",
        body: formData,
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error("Request failed");
      var data = await res.json();
      updateCartBadge(data.cart_count);
      return data;
    } catch (err) {
      return null;
    }
  }

  document.addEventListener("submit", async function (event) {
    var form = event.target;
    if (!form.classList || !form.classList.contains("js-cart-form")) return;
    event.preventDefault();

    var submitBtn = form.querySelector("button[type=submit]");
    if (submitBtn) submitBtn.disabled = true;

    var data = await submitCartForm(form);

    if (submitBtn) submitBtn.disabled = false;

    if (data === null) {
      // Fall back to a normal, reliable full-page submit.
      form.submit();
      return;
    }

    if (form.dataset.reloadOnSuccess === "true") {
      window.location.reload();
      return;
    }

    showToast(form.dataset.successMessage || "Cart updated!");
  });

  // Quantity steppers used on product detail + cart lines.
  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-qty-step]");
    if (!target) return;
    var stepper = target.closest(".qty-stepper");
    var input = stepper.querySelector("input[type=number]");
    if (!input) return;
    var step = parseInt(target.getAttribute("data-qty-step"), 10);
    var max = parseInt(input.getAttribute("max") || "20", 10);
    var min = parseInt(input.getAttribute("min") || "1", 10);
    var next = (parseInt(input.value, 10) || min) + step;
    next = Math.max(min, Math.min(max, next));
    input.value = next;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
})();
