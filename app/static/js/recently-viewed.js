/*
 * Recently Viewed: entirely client-side (localStorage), no DB table, no
 * server-side session tracking. Records a product slug whenever a product
 * page loads (see base.html's data-recently-viewed-slug hook), and renders
 * a small card row into any #recently-viewed-row container found on the
 * current page, fetching live product data (price/stock may have changed)
 * for whatever slugs are stored.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "jjg_recently_viewed";
  var MAX_ITEMS = 10;

  function readList() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      var list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (e) {
      return [];
    }
  }

  function recordView(slug) {
    if (!slug) return;
    var list = readList().filter(function (s) { return s !== slug; });
    list.unshift(slug);
    if (list.length > MAX_ITEMS) list = list.slice(0, MAX_ITEMS);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
    } catch (e) { /* storage full/unavailable — not critical */ }
  }

  function renderRow(container, excludeSlug) {
    var slugs = readList().filter(function (s) { return s !== excludeSlug; });
    if (!slugs.length) return;

    fetch("/api/products/recently-viewed?slugs=" + encodeURIComponent(slugs.join(",")), {
      headers: { Accept: "application/json" },
    })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data || !data.products || !data.products.length) return;

        var section = document.createElement("section");
        section.className = "page-section";
        var heading = document.createElement("h2");
        heading.className = "section-title";
        heading.textContent = "🕐 Recently Viewed";
        var row = document.createElement("div");
        row.className = "recently-viewed-row";

        data.products.forEach(function (p) {
          var card = document.createElement("a");
          card.className = "recently-viewed-card";
          card.href = "/product/" + p.slug;
          card.innerHTML =
            '<div class="recently-viewed-card__img">' +
            (p.image ? '<img src="' + p.image + '" alt="" loading="lazy">' : "") +
            "</div>" +
            '<div class="recently-viewed-card__name"></div>' +
            '<div class="recently-viewed-card__price">₹' + Math.round(p.price) + "</div>";
          card.querySelector(".recently-viewed-card__name").textContent = p.name;
          row.appendChild(card);
        });

        section.appendChild(heading);
        section.appendChild(row);
        container.replaceWith(section);
      })
      .catch(function () { /* recently-viewed is a nicety, fail silently */ });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var slug = document.body.getAttribute("data-recently-viewed-slug");
    if (slug) recordView(slug);

    var container = document.getElementById("recently-viewed-row");
    if (container) renderRow(container, slug || null);
  });
})();
