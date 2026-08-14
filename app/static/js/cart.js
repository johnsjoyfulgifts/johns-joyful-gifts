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

  // ---- Wishlist ----
  function updateWishlistBadge(count) {
    var link = document.getElementById("wishlist-icon-link");
    if (!link) return;
    var badge = link.querySelector(".cart-badge");
    if (count > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "cart-badge";
        link.appendChild(badge);
      }
      badge.textContent = count;
    } else if (badge) {
      badge.remove();
    }
  }

  function setHeartState(btn, active) {
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    btn.textContent = active ? "❤️" : "🤍";
  }

  function initWishlistHearts() {
    var hearts = document.querySelectorAll(".js-wishlist-toggle");
    if (!hearts.length && !document.getElementById("wishlist-icon-link")) return;
    fetch("/api/wishlist/ids", { headers: { Accept: "application/json" } })
      .then(function (res) { return res.ok ? res.json() : { ids: [] }; })
      .then(function (data) {
        var ids = data.ids || [];
        updateWishlistBadge(ids.length);
        hearts.forEach(function (btn) {
          var pid = parseInt(btn.getAttribute("data-product-id"), 10);
          if (ids.indexOf(pid) !== -1) setHeartState(btn, true);
        });
      })
      .catch(function () { /* silent: wishlist state is a convenience, not required */ });
  }

  document.addEventListener("click", async function (event) {
    var btn = event.target.closest(".js-wishlist-toggle");
    if (!btn) return;
    event.preventDefault();
    event.stopPropagation();
    if (btn.disabled) return;
    btn.disabled = true;

    try {
      var res = await fetch("/api/wishlist/toggle", {
        method: "POST",
        body: new URLSearchParams({ product_id: btn.getAttribute("data-product-id") }),
        headers: { Accept: "application/json" },
      });
      if (res.status === 401) {
        window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
        return;
      }
      if (!res.ok) throw new Error("Request failed");
      var data = await res.json();
      var allSameProduct = document.querySelectorAll(
        '.js-wishlist-toggle[data-product-id="' + btn.getAttribute("data-product-id") + '"]'
      );
      allSameProduct.forEach(function (other) { setHeartState(other, data.in_wishlist); });
      showToast(data.in_wishlist ? "Saved to wishlist" : "Removed from wishlist");
      // Badge count is the customer's total wishlist size, not derivable from
      // hearts visible on this page — always refetch it fresh from the server.
      fetch("/api/wishlist/ids", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : { ids: [] }; })
        .then(function (d) { updateWishlistBadge((d.ids || []).length); })
        .catch(function () {});
    } catch (err) {
      showToast("Something went wrong. Please try again.");
    } finally {
      btn.disabled = false;
    }
  });

  document.addEventListener("DOMContentLoaded", initWishlistHearts);
  if (document.readyState !== "loading") initWishlistHearts();

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
