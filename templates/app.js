(function () {
  "use strict";

  var search = document.querySelector("[data-doc-search]");
  var source = document.querySelector("[data-doc-source]");
  var status = document.querySelector("[data-doc-status]");
  var rows = Array.prototype.slice.call(
    document.querySelectorAll("[data-doc-row]")
  );
  var empty = document.querySelector("[data-doc-empty]");

  if (!rows.length) {
    return;
  }

  function value(element) {
    return element ? element.value.trim() : "";
  }

  function apply() {
    var query = value(search).toLowerCase();
    var sourceValue = value(source);
    var statusValue = value(status);
    var shown = 0;

    rows.forEach(function (row) {
      var title = (row.getAttribute("data-title") || "").toLowerCase();
      var matchQuery = !query || title.indexOf(query) !== -1;
      var matchSource =
        !sourceValue || row.getAttribute("data-source") === sourceValue;
      var matchStatus =
        !statusValue || row.getAttribute("data-status") === statusValue;
      var visible = matchQuery && matchSource && matchStatus;
      row.hidden = !visible;
      if (visible) {
        shown += 1;
      }
    });

    if (empty) {
      empty.hidden = shown !== 0;
    }
  }

  [search, source, status].forEach(function (element) {
    if (element) {
      element.addEventListener("input", apply);
      element.addEventListener("change", apply);
    }
  });

  apply();
})();
