const operatorLabels = {
  contains: "含む", not_contains: "含まない", starts: "始まる",
  not_starts: "始まらない", in: "複数選択", not_in: "複数選択以外",
  gte: "以上", lte: "以下",
  unset: "未設定", eq: "等しい", ne: "等しくない", gt: "より大きい",
  ge: "以上(数値)", lt: "より小さい", le: "以下(数値)",
  before: "より前", on_or_after: "以降", after: "より後",
  on_or_before: "以前"
};

function rebuildOperators(condition, keepOp) {
  const identifier = condition.querySelector('select[name="identifier"]');
  const operator = condition.querySelector('select[name="op"]');
  if (!identifier || !operator) return;
  const selected = identifier.options[identifier.selectedIndex];
  const ops = (selected && selected.dataset.ops ? selected.dataset.ops : "").split(",").filter(Boolean);
  const current = keepOp || operator.value;
  operator.replaceChildren(...ops.map(function (op) {
    const option = document.createElement("option");
    option.value = op;
    option.textContent = operatorLabels[op] || op;
    return option;
  }));
  if (current && ops.indexOf(current) >= 0) operator.value = current;
}

function bindFilterCondition(condition) {
  const identifier = condition.querySelector('select[name="identifier"]');
  if (!identifier || identifier.dataset.bound) return;
  identifier.dataset.bound = "1";
  identifier.addEventListener("change", function () {
    rebuildOperators(condition);
    updateFilterMeta();
  });
  const operator = condition.querySelector('select[name="op"]');
  if (operator) operator.addEventListener("change", updateFilterMeta);
  const value = condition.querySelector('input[name="value"]');
  if (value) value.addEventListener("input", updateFilterMeta);
  rebuildOperators(condition, operator ? operator.value : "");
}

function conditions() {
  return Array.from(document.querySelectorAll("#cond-list .filter-condition"));
}

function renumberConditions() {
  conditions().forEach(function (cond, i) {
    const node = cond.querySelector(".cond-node");
    if (node) node.textContent = String(i + 1);
    const andTag = cond.previousElementSibling;
    if (andTag && andTag.classList.contains("and-tag")) {
      andTag.hidden = i === 0;
    }
  });
}

function updateFilterMeta() {
  const list = conditions();
  const empty = document.getElementById("cond-empty");
  const count = document.getElementById("cond-count");
  const summary = document.getElementById("cond-summary");
  if (empty) empty.hidden = list.length > 0;
  if (count) count.textContent = list.length + " 件";
  if (!summary) return;
  if (list.length === 0) {
    summary.innerHTML = '<span style="color:var(--faint)">全件（絞り込みなし）</span>';
    return;
  }
  summary.replaceChildren(...list.map(function (cond, i) {
    const ident = cond.querySelector('select[name="identifier"]');
    const op = cond.querySelector('select[name="op"]');
    const val = cond.querySelector('input[name="value"]');
    const label = ident && ident.selectedOptions[0] ? ident.selectedOptions[0].textContent.replace(/^＋ /, "") : "";
    const opId = op ? op.value : "";
    const div = document.createElement("div");
    const prefix = i === 0 ? "" : '<span class="k">　AND </span>';
    if (opId === "unset") {
      div.innerHTML = prefix + label + " が未設定";
    } else {
      const shown = (opId === "in" || opId === "not_in")
        ? "[" + String((val && val.value) || "").split(",").filter(Boolean).length + " 件]"
        : '"' + ((val && val.value) || "—") + '"';
      div.innerHTML = prefix + label + ' <span class="k">' + (operatorLabels[opId] || opId) + "</span> " + shown;
    }
    return div;
  }));
}

function initFilter() {
  document.querySelectorAll(".filter-condition").forEach(bindFilterCondition);
  const add = document.getElementById("add-filter");
  const tpl = document.getElementById("filter-cond-tpl");
  const list = document.getElementById("cond-list");
  if (add && tpl && list) {
    add.addEventListener("click", function () {
      const frag = tpl.content.cloneNode(true);
      if (list.children.length === 0) {
        const andTag = frag.querySelector(".and-tag");
        if (andTag) andTag.hidden = true;
      }
      list.appendChild(frag);
      const added = list.querySelector(".filter-condition:last-of-type");
      bindFilterCondition(added);
      renumberConditions();
      updateFilterMeta();
    });
  }
  if (list) {
    list.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-remove-cond]");
      if (!btn) return;
      const cond = btn.closest(".filter-condition");
      if (!cond) return;
      const andTag = cond.previousElementSibling;
      if (andTag && andTag.classList.contains("and-tag")) andTag.remove();
      cond.remove();
      renumberConditions();
      updateFilterMeta();
    });
  }
}

function closeOverlays() {
  document.querySelectorAll(".scrim, .drawer, .modal").forEach(function (el) {
    el.hidden = true;
    el.classList.remove("is-open");
  });
  document.body.classList.remove("modal-open");
}

function openOverlay(selector) {
  const target = document.querySelector(selector);
  if (!target) return;
  closeOverlays();
  const scrim = document.querySelector(".scrim");
  if (scrim) {
    scrim.hidden = false;
    scrim.classList.add("is-open");
  }
  target.hidden = false;
  target.classList.add("is-open");
  document.body.classList.add("modal-open");
  const focusable = target.querySelector("input, textarea, select, button:not([data-close-overlay])");
  if (focusable) focusable.focus();
}

function initOverlays() {
  document.addEventListener("click", function (e) {
    const openBtn = e.target.closest("[data-open]");
    if (openBtn) {
      e.preventDefault();
      e.stopPropagation();
      openOverlay(openBtn.getAttribute("data-open"));
      return;
    }
    if (e.target.closest("[data-close-overlay]") || e.target.classList.contains("scrim")) {
      closeOverlays();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeOverlays();
  });
  document.querySelectorAll("[data-confirm-name]").forEach(function (input) {
    const form = input.closest("form");
    const submit = form && form.querySelector("[data-confirm-submit]");
    if (!submit) return;
    submit.disabled = true;
    input.addEventListener("input", function () {
      submit.disabled = input.value !== input.getAttribute("data-confirm-name");
    });
  });
}

function placeMenu(toggle, menu) {
  const rect = toggle.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.top = `${Math.round(rect.bottom + 4)}px`;
  menu.style.right = `${Math.round(window.innerWidth - rect.right)}px`;
  menu.style.left = "auto";
  menu.style.bottom = "auto";
}

function initMenus() {
  document.addEventListener("click", function (e) {
    const toggle = e.target.closest("[data-menu-toggle]");
    document.querySelectorAll(".menu-wrap").forEach(function (wrap) {
      const menu = wrap.querySelector(".menu");
      if (!menu) return;
      if (toggle && wrap.contains(toggle)) {
        menu.hidden = !menu.hidden;
        if (!menu.hidden) placeMenu(toggle, menu);
      } else if (!wrap.contains(e.target)) {
        menu.hidden = true;
      }
    });
  });
}

function importIsRunning() {
  if (document.body.getAttribute("data-import-job") === "running") return true;
  const progress = document.querySelector("#import-progress[data-job='running']");
  return !!progress;
}

function initTabs() {
  const root = document.querySelector("[data-page='work']");
  if (!root) return;
  document.querySelectorAll(".tab[data-tab]").forEach(function (tab) {
    tab.addEventListener("click", function (e) {
      if (importIsRunning()) return;
      e.preventDefault();
      const name = tab.getAttribute("data-tab");
      document.querySelectorAll(".tab[data-tab]").forEach(function (t) {
        t.classList.toggle("on", t.getAttribute("data-tab") === name);
      });
      document.querySelectorAll("[data-tab-panel]").forEach(function (panel) {
        panel.hidden = panel.getAttribute("data-tab-panel") !== name;
      });
      document.querySelectorAll('input[name="tab"]').forEach(function (input) {
        input.value = name;
      });
      const url = new URL(window.location.href);
      url.searchParams.set("tab", name);
      history.replaceState(null, "", url);
      const labels = { list: "03 一覧", agg: "04 集計", ingest: "02 取り込み", schema: "05 案件情報" };
      const ws = document.querySelector(".workspace");
      if (ws) ws.setAttribute("data-screen-label", labels[name] || "");
      root.setAttribute("data-tab", name);
    });
  });
}

function initCaseRows() {
  document.querySelectorAll(".case-tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      if (e.target.closest("a, button, input, .menu-wrap, [data-stop-row]")) return;
      window.location.href = row.getAttribute("data-href");
    });
  });
}

function initCells() {
  document.querySelectorAll(".cell-form input[name='value']").forEach(function (input) {
    input.addEventListener("blur", function () {
      if (input.value !== (input.getAttribute("data-original") || "")) input.form.submit();
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      }
      if (e.key === "Escape") {
        input.value = input.getAttribute("data-original") || "";
        input.blur();
      }
    });
  });
}

function initToasts() {
  document.querySelectorAll("[data-dismiss-toast]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const toast = btn.closest(".toast");
      if (toast) toast.remove();
    });
  });
  document.querySelectorAll(".toast").forEach(function (toast) {
    const err = toast.classList.contains("err");
    window.setTimeout(function () { toast.remove(); }, err ? 7000 : 4200);
  });
}

function initCreatePreview() {
  const form = document.getElementById("create-form");
  const input = document.getElementById("logformat-input");
  if (!form || !input) return;
  const previewUrl = form.getAttribute("action").replace(/\/cases$/, "/cases/preview-format");
  const fieldsEl = document.getElementById("format-fields");
  const regexEl = document.getElementById("format-regex");
  const preview = document.getElementById("format-preview");
  const countEl = document.getElementById("format-count");
  const errEl = document.getElementById("format-error");
  const foot = document.getElementById("format-foot-msg");
  const submit = document.getElementById("create-submit");
  let timer = 0;
  const typeLabel = { string: "文字列", number: "数値", datetime: "日時" };

  function render(data) {
    if (!data.ok) {
      preview.hidden = true;
      errEl.hidden = false;
      errEl.textContent = data.message || "LogFormat に誤りがあります";
      foot.innerHTML = '<span style="color:var(--danger)">LogFormat に誤りがあります</span>';
      if (submit) submit.disabled = true;
      return;
    }
    errEl.hidden = true;
    preview.hidden = false;
    countEl.textContent = String(data.fields.length);
    fieldsEl.replaceChildren(...data.fields.map(function (f, i) {
      const row = document.createElement("div");
      row.className = "fmt-field";
      row.innerHTML =
        '<span class="idx">' + (i + 1) + "</span>" +
        '<span class="nm">' + f.identifier + "</span>" +
        '<span class="tag type-' + f.field_type + '">' + (typeLabel[f.field_type] || f.field_type) + "</span>" +
        '<span class="sp">' + f.specifier + (f.time_axis ? ' <span class="tag axis">軸</span>' : "") + "</span>";
      return row;
    }));
    regexEl.textContent = data.regex || "";
    foot.innerHTML = data.fields.length + " 列で登録できます";
    if (submit) submit.disabled = false;
  }

  function refresh() {
    const body = new URLSearchParams();
    body.set("logformat", input.value);
    fetch(previewUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString()
    }).then(function (r) { return r.json(); }).then(render).catch(function () {
      preview.hidden = true;
    });
  }

  input.addEventListener("input", function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 280);
  });
  document.querySelectorAll("[data-preset]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      input.value = btn.getAttribute("data-preset") || "";
      refresh();
    });
  });
}

function initBucketLabel() {
  document.querySelectorAll(".bucket-toggle input").forEach(function (input) {
    const label = input.parentElement.querySelector("[data-bucket-label]");
    if (!label) return;
    input.addEventListener("change", function () {
      label.textContent = input.checked ? "使用中" : "使わない";
    });
  });
}

function paintImportProgress(box, data) {
  const done = data.done || 0;
  const total = data.total || 0;
  const heading = box.querySelector("div");
  if (heading) {
    let label = "完了";
    if (data.job === "running") {
      label = data.current ? "処理中: " + data.current : "処理中";
    } else if (data.failed) {
      label = "失敗";
    }
    heading.textContent = done + " / " + total + " " + label;
  }
  const list = box.querySelector("ul");
  if (list) {
    list.replaceChildren();
    (data.files || []).forEach(function (file) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "mono";
      name.textContent = file.name || "";
      li.appendChild(name);
      li.appendChild(document.createTextNode(" — " + (file.status || "")));
      if (file.status === "done") {
        li.appendChild(document.createTextNode(
          "（読込 " + (file.read_count || 0) +
          " / 登録 " + (file.inserted_count || 0) +
          " / 破棄 " + (file.discarded_count || 0) +
          " / 重複 " + (file.duplicate_count || 0) + "）"
        ));
      } else if (file.error) {
        li.appendChild(document.createTextNode(" " + file.error));
      }
      list.appendChild(li);
    });
  }
  if (data.job) box.setAttribute("data-job", data.job);
}

function initImportProgress() {
  const box = document.getElementById("import-progress");
  if (!box) return;
  const url = box.getAttribute("data-status-url");
  if (!url || box.getAttribute("data-job") !== "running") return;
  let reloading = false;
  const timer = setInterval(function () {
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data) return;
        paintImportProgress(box, data);
        if (data.job === "finished" && !reloading) {
          reloading = true;
          clearInterval(timer);
          window.location.reload();
        }
      })
      .catch(function () {});
  }, 2000);
}

function initSelectedFiles() {
  const input = document.querySelector('.drop input[name="files"]');
  const list = document.getElementById("selected-files");
  const hint = document.querySelector(".drop .s");
  if (!input || !list) return;
  const defaultHint = hint ? hint.textContent : "";

  function paint() {
    const files = Array.from(input.files || []);
    list.replaceChildren();
    list.hidden = files.length === 0;
    files.forEach(function (file) {
      const row = document.createElement("div");
      row.className = "file-item";
      const name = document.createElement("span");
      name.className = "nm";
      name.textContent = file.name;
      row.appendChild(name);
      list.appendChild(row);
    });
    if (hint) {
      hint.textContent = files.length
        ? files.length + " ファイルを選択中"
        : defaultHint;
    }
  }

  input.addEventListener("change", paint);
  const drop = input.closest(".drop");
  if (!drop) return;
  drop.addEventListener("dragover", function (event) {
    event.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", function () {
    drop.classList.remove("over");
  });
  drop.addEventListener("drop", function (event) {
    event.preventDefault();
    drop.classList.remove("over");
    if (!event.dataTransfer || !event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    paint();
  });
}

document.addEventListener("DOMContentLoaded", function () {
  initFilter();
  initOverlays();
  initMenus();
  initTabs();
  initCaseRows();
  initCells();
  initToasts();
  initCreatePreview();
  initBucketLabel();
  initImportProgress();
  initSelectedFiles();
});
