/*
 * Quick View modal — desktop/tablet only (the trigger button itself is
 * hidden on touch layouts via CSS, so this never intercepts a mobile tap).
 * Fetches a lightweight product JSON and renders it inside a shared modal;
 * "Add to Cart" reuses the existing .js-cart-form delegated handler in
 * cart.js, so no separate cart-submit logic is needed here.
 */
(function () {
  "use strict";

  var overlay = document.getElementById("quick-view-overlay");
  var body = document.getElementById("quick-view-body");
  var closeBtn = document.getElementById("quick-view-close");
  if (!overlay || !body || !closeBtn) return;

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function render(product, productId) {
    var priceHtml =
      '<span class="price">₹' + Math.round(product.price) + "</span>" +
      (product.original_price && product.original_price > product.price
        ? ' <span class="price--old">₹' + Math.round(product.original_price) + "</span>"
        : "");

    var actionHtml;
    if (!product.in_stock) {
      actionHtml = '<button class="btn btn-secondary btn-block" disabled>Out of Stock</button>';
    } else if (product.is_personalizable) {
      actionHtml =
        '<a class="btn btn-primary btn-block" href="' + product.url + '">Personalize &amp; Add to Cart</a>' +
        '<p class="field-hint">This product is personalized — customize it on its own page.</p>';
    } else {
      actionHtml =
        '<form class="js-cart-form" action="/api/cart/add" method="post" data-success-message="Added to cart!">' +
        '<input type="hidden" name="product_id" value="' + productId + '">' +
        '<input type="hidden" name="quantity" value="1">' +
        '<button type="submit" class="btn btn-primary btn-block">Add to Cart</button>' +
        "</form>";
    }

    body.innerHTML =
      '<div class="quick-view-modal__media">' +
      (product.image
        ? '<img src="' + product.image + '" alt="' + escapeHtml(product.name) + '">'
        : '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:3rem;">🎁</div>') +
      "</div>" +
      '<div class="quick-view-modal__info">' +
      '<h2>' + escapeHtml(product.name) + "</h2>" +
      '<div class="product-card__price" style="margin:8px 0;">' + priceHtml + "</div>" +
      (product.description ? '<p class="text-muted">' + escapeHtml(product.description) + "</p>" : "") +
      '<div style="margin:14px 0;">' + actionHtml + "</div>" +
      '<a href="' + product.url + '" class="text-muted" style="font-size:0.85rem;">View full details →</a>' +
      "</div>";
  }

  function openQuickView(slug, productId) {
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
    body.innerHTML = '<p class="text-muted" style="padding:40px;text-align:center;">Loading…</p>';

    fetch("/api/products/" + encodeURIComponent(slug) + "/quick-view", { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("Not found");
        return res.json();
      })
      .then(function (product) {
        render(product, productId);
      })
      .catch(function () {
        body.innerHTML = '<p class="text-muted" style="padding:40px;text-align:center;">Couldn\'t load this product.</p>';
      });
  }

  function closeQuickView() {
    overlay.hidden = true;
    document.body.style.overflow = "";
    body.innerHTML = "";
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest(".js-quick-view");
    if (trigger) {
      event.preventDefault();
      event.stopPropagation();
      var card = trigger.closest(".product-card");
      var heart = card ? card.querySelector(".js-wishlist-toggle") : null;
      var productId = heart ? heart.getAttribute("data-product-id") : "";
      openQuickView(trigger.getAttribute("data-quick-view-slug"), productId);
      return;
    }
    if (event.target === overlay || event.target.closest("#quick-view-close")) {
      closeQuickView();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !overlay.hidden) closeQuickView();
  });
})();
