/* ============================================================
   MT Evaluation -- rating UI client logic
   Multi-segment-per-page layout: left = source/target/reference,
   right = Likert circles per criterion, full-width span annotation
   below each segment.
   ============================================================ */

(function () {
  "use strict";

  const CAMPAIGN    = window.CAMPAIGN || {};
  const SEGMENTS    = window.SEGMENTS || [];
  const CRITERIA    = window.CRITERIA || [];
  const SCALE       = window.SCALE || {};
  const EXISTING    = window.EXISTING || {};
  const SUBMIT_URL  = window.SUBMIT_URL || "";
  const ENABLE_SPANS = window.ENABLE_SPANS !== false;
  const SPAN_SCOPE  = window.SPAN_SCOPE || "target";   // "target" or "both"
  const PER_PAGE    = Math.max(1, parseInt(window.PER_PAGE, 10) || 3);

  const NUM_PAGES = Math.max(1, Math.ceil(SEGMENTS.length / PER_PAGE));

  /* ---- per-segment state (one entry per global segment index) ---- */
  const state = {
    currentPage: 0,
    reviewMode: false,   // false during first-time annotation; true after Finish/Review
    // active error type is tracked per segment index so each card is independent
    activeErrorType: SEGMENTS.map(() => (CRITERIA[0] ? CRITERIA[0].id : null)),
    segmentStartTs: SEGMENTS.map(() => 0),
    ratings: SEGMENTS.map(seg => {
      const ex = EXISTING[seg.id];
      function normIncoming(spansObj) {
        const out = {};
        CRITERIA.forEach(c => {
          const arr = (spansObj && spansObj[c.id]) || [];
          out[c.id] = arr.map(sp => {
            if (sp.length >= 3) return [sp[0], sp[1], sp[2]];
            return [sp[0], sp[1], "target"];
          });
        });
        return out;
      }
      if (ex) {
        return {
          scores: { ...ex.scores },
          spans:  normIncoming(ex.spans),
          comments: ex.comments || "",
          saved: true,
          dirty: false,
        };
      }
      return {
        scores: Object.fromEntries(CRITERIA.map(c => [c.id, null])),
        spans:  Object.fromEntries(CRITERIA.map(c => [c.id, []])),
        comments: "",
        saved: false,
        dirty: false,
      };
    }),
  };

  // cards[globalIdx] = the rendered <article> element currently on screen, or null
  let cards = [];

  /* ===================== utilities ===================== */

  const $  = (id) => document.getElementById(id);
  const showScreen = (id) => {
    document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
    $(id).classList.add("active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function hexToRgba(hex, a) {
    const h = hex.replace("#", "");
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  function arraysEqual(a, b) {
    if (a === b) return true;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function normalizeSpans(spans) {
    if (!spans || spans.length === 0) return [];
    const sorted = spans.slice().sort((a, b) => a[0] - b[0]);
    const merged = [sorted[0].slice()];
    for (let i = 1; i < sorted.length; i++) {
      const last = merged[merged.length - 1];
      const cur = sorted[i];
      if (cur[0] <= last[1]) last[1] = Math.max(last[1], cur[1]);
      else merged.push(cur.slice());
    }
    return merged;
  }

  function normalizeSpansForPane(spans, pane) {
    const mine = spans.filter(sp => (sp[2] || "target") === pane).map(sp => [sp[0], sp[1]]);
    const others = spans.filter(sp => (sp[2] || "target") !== pane);
    const merged = normalizeSpans(mine).map(([s, e]) => [s, e, pane]);
    return others.concat(merged);
  }

  function isComplete(r) {
    return CRITERIA.every(c => r.scores[c.id] != null);
  }

  function pageBounds(page) {
    const start = page * PER_PAGE;
    const end = Math.min(start + PER_PAGE, SEGMENTS.length);
    return { start, end };
  }

  function currentPageComplete() {
    const { start, end } = pageBounds(state.currentPage);
    for (let idx = start; idx < end; idx++) {
      if (!isComplete(state.ratings[idx])) return false;
    }
    return true;
  }

  function refreshNextEnabled() {
    const btn = $("btn-next");
    if (!btn) return;
    const complete = currentPageComplete();
    btn.disabled = !complete;
    btn.title = complete ? "" : "Rate every criterion on every segment of this page before continuing.";
  }

  /* ===================== per-card builders ===================== */

  // Build the Likert circle row + criteria list for one segment card.
  function buildCriteriaUIFor(card, idx) {
    const container = card.querySelector(".criteria-list");
    container.innerHTML = "";
    CRITERIA.forEach((c) => {
      const div = document.createElement("div");
      div.className = "criterion";
      div.dataset.criterion = c.id;
      div.innerHTML =
        '<div class="criterion-header">' +
          '<p class="criterion-title">' +
            '<span class="criterion-swatch" style="background:' + c.color + '" aria-hidden="true"></span>' +
            escapeHtml(c.name) +
          '</p>' +
        '</div>' +
        '<div class="likert-circle-row" role="radiogroup" aria-label="' + escapeHtml(c.name) + ' rating">' +
          [1,2,3,4,5].map(v =>
            '<button type="button" class="likert-circle" data-value="' + v + '" role="radio" aria-checked="false" ' +
              'aria-label="' + v + ' — ' + escapeHtml(SCALE[v] || "") + '" title="' + v + ' — ' + escapeHtml(SCALE[v] || "") + '">' +
              v +
            '</button>'
          ).join("") +
        '</div>';
      container.appendChild(div);
    });

    container.addEventListener("click", (e) => {
      const btn = e.target.closest(".likert-circle");
      if (!btn) return;
      const crit = btn.closest(".criterion").dataset.criterion;
      const val = parseInt(btn.dataset.value, 10);
      const r = state.ratings[idx];
      if (r.scores[crit] !== val) {
        r.scores[crit] = val;
        r.dirty = true;
      }
      refreshCriteriaUIFor(card, idx);
      if (ENABLE_SPANS) refreshSpanLockFor(card, idx);
      updateGlobalProgress();
      refreshNextEnabled();
    });
  }

  function refreshCriteriaUIFor(card, idx) {
    const r = state.ratings[idx];
    card.querySelectorAll(".criterion").forEach(critEl => {
      const cid = critEl.dataset.criterion;
      const selected = r.scores[cid];
      critEl.classList.toggle("rated", selected != null);
      critEl.querySelectorAll(".likert-circle").forEach(btn => {
        const v = parseInt(btn.dataset.value, 10);
        const isSel = v === selected;
        btn.classList.toggle("selected", isSel);
        btn.setAttribute("aria-checked", isSel ? "true" : "false");
      });
    });
    card.classList.toggle("segment-complete", isComplete(r));
  }

  /* ===================== span annotation (per card) ===================== */

  function paneText(idx, pane) {
    const seg = SEGMENTS[idx];
    return (pane === "source" ? seg.source : seg.target) || "";
  }

  function paneEl(card, pane) {
    return pane === "source"
      ? card.querySelector(".span-source")
      : card.querySelector(".span-target-main");
  }

  function buildErrorTypePickerFor(card, idx) {
    const picker = card.querySelector(".error-type-picker");
    picker.innerHTML = "";
    CRITERIA.forEach(c => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "error-type-pill";
      btn.dataset.errorType = c.id;
      btn.style.color = c.color;
      btn.innerHTML =
        '<span class="error-type-swatch" aria-hidden="true"></span>' +
        '<span class="error-type-name">' + escapeHtml(c.name) + '</span>' +
        '<span class="error-type-count" data-count-for="' + c.id + '">0</span>';
      picker.appendChild(btn);
    });
    picker.addEventListener("click", (e) => {
      const btn = e.target.closest(".error-type-pill");
      if (!btn) return;
      state.activeErrorType[idx] = btn.dataset.errorType;
      refreshErrorTypePickerFor(card, idx);
      setActiveSelectionColorFor(card, idx);
    });
  }

  function refreshErrorTypePickerFor(card, idx) {
    const active = state.activeErrorType[idx];
    card.querySelectorAll(".error-type-pill").forEach(btn => {
      btn.classList.toggle("selected", btn.dataset.errorType === active);
    });
    const r = state.ratings[idx];
    if (!r) return;
    CRITERIA.forEach(c => {
      const n = (r.spans[c.id] || []).length;
      const el = card.querySelector('[data-count-for="' + c.id + '"]');
      if (el) el.textContent = n;
    });
    setActiveSelectionColorFor(card, idx);
  }

  function setActiveSelectionColorFor(card, idx) {
    const c = CRITERIA.find(c => c.id === state.activeErrorType[idx]);
    if (!c) return;
    const col = hexToRgba(c.color, 0.28);
    const tgt = paneEl(card, "target");
    if (tgt) tgt.style.setProperty("--active-select-bg", col);
    const src = paneEl(card, "source");
    if (src) src.style.setProperty("--active-select-bg", col);
  }

  function renderPaneFor(card, idx, pane) {
    const el = paneEl(card, pane);
    if (!el) return;
    const r = state.ratings[idx];
    const text = paneText(idx, pane);
    if (!text) { el.innerHTML = ""; return; }

    function styleForTypes(typeIds) {
      if (typeIds.length === 0) return { bg: "", shadow: "" };
      const firstHex = CRITERIA.find(c => c.id === typeIds[0]).color;
      const bg = hexToRgba(firstHex, 0.14);
      const shadows = typeIds.map((id, i) => {
        const hex = CRITERIA.find(c => c.id === id).color;
        const offset = -2 - i * 3;
        return "inset 0 " + offset + "px 0 -1px " + hex;
      }).join(", ");
      return { bg, shadow: shadows };
    }

    const charTypes = new Array(text.length);
    for (let i = 0; i < text.length; i++) charTypes[i] = [];
    CRITERIA.forEach(c => {
      const spans = r.spans[c.id] || [];
      spans.forEach(sp => {
        if ((sp[2] || "target") !== pane) return;
        const s = sp[0], e = sp[1];
        for (let i = s; i < e && i < text.length; i++) {
          if (!charTypes[i].includes(c.id)) charTypes[i].push(c.id);
        }
      });
    });

    let html = "";
    let i = 0;
    while (i < text.length) {
      const types = charTypes[i];
      let j = i + 1;
      while (j < text.length && arraysEqual(charTypes[j], types)) j++;
      const chunk = text.slice(i, j);
      const style = styleForTypes(types);
      const cls = types.length ? "char in-span" : "char";
      let inline = "";
      if (types.length) inline = 'style="--span-bg:' + style.bg + '; --span-underlines:' + style.shadow + '"';
      const dataTypes = types.length ? ' data-types="' + types.join(",") + '"' : "";
      html += '<span class="' + cls + '" data-start="' + i + '" data-end="' + j + '"' +
              dataTypes + " " + inline + ">" + escapeHtml(chunk) + "</span>";
      i = j;
    }
    el.innerHTML = html;
  }

  function renderSpanTargetFor(card, idx) {
    renderPaneFor(card, idx, "target");
    if (SPAN_SCOPE === "both") renderPaneFor(card, idx, "source");
  }

  function selectionPointToOffset(card, idx, node, offsetInNode, pane) {
    if (!node) return null;
    const el = paneEl(card, pane);
    let charSpan = node;
    if (charSpan.nodeType === 3) charSpan = charSpan.parentNode;
    while (charSpan && charSpan !== el && !(charSpan.classList && charSpan.classList.contains("char"))) {
      charSpan = charSpan.parentNode;
    }
    if (!charSpan || charSpan === el) {
      if (offsetInNode <= 0) return 0;
      return paneText(idx, pane).length;
    }
    const chunkStart = parseInt(charSpan.dataset.start, 10);
    return chunkStart + Math.max(0, Math.min(offsetInNode, charSpan.textContent.length));
  }

  function pointToOffset(card, idx, clientX, clientY, pane) {
    let range = null;
    if (document.caretRangeFromPoint) {
      range = document.caretRangeFromPoint(clientX, clientY);
    } else if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(clientX, clientY);
      if (pos) {
        range = document.createRange();
        range.setStart(pos.offsetNode, pos.offset);
        range.collapse(true);
      }
    }
    if (!range) return null;
    const el = paneEl(card, pane);
    if (!el.contains(range.startContainer)) return null;
    return selectionPointToOffset(card, idx, range.startContainer, range.startOffset, pane);
  }

  function expandRangeToWords(start, end, text) {
    while (start > 0 && /\S/.test(text[start - 1])) start--;
    while (end < text.length && /\S/.test(text[end])) end++;
    return [start, end];
  }

  function addSpan(card, idx, errorType, start, end, pane) {
    if (start >= end) return;
    const r = state.ratings[idx];
    const list = r.spans[errorType] || [];
    list.push([start, end, pane]);
    r.spans[errorType] = normalizeSpansForPane(list, pane);
    r.dirty = true;
    renderSpanTargetFor(card, idx);
    refreshErrorTypePickerFor(card, idx);
    refreshSpanSummaryFor(card, idx);
  }

  function removeSpanAt(card, idx, offset, pane) {
    const r = state.ratings[idx];
    const t = state.activeErrorType[idx];
    const before = r.spans[t] || [];
    const after = [];
    let changed = false;
    before.forEach(sp => {
      const spPane = sp[2] || "target";
      if (spPane === pane && offset >= sp[0] && offset < sp[1]) { changed = true; return; }
      after.push(sp);
    });
    if (changed) {
      r.spans[t] = after;
      r.dirty = true;
      renderSpanTargetFor(card, idx);
      refreshErrorTypePickerFor(card, idx);
      refreshSpanSummaryFor(card, idx);
    }
    return changed;
  }

  function refreshSpanSummaryFor(card, idx) {
    const r = state.ratings[idx];
    if (!r) return;
    const sum = card.querySelector(".span-summary");
    const parts = [];
    CRITERIA.forEach(c => {
      const n = (r.spans[c.id] || []).length;
      if (n > 0) {
        parts.push(
          '<span class="sum-pill" style="color:' + c.color + '">' +
          '<span class="sum-dot" aria-hidden="true"></span>' +
          escapeHtml(c.name) + ': ' + n + '</span>'
        );
      }
    });
    sum.innerHTML = parts.length
      ? parts.join(" ")
      : '<span style="color:var(--text-faint)">No error spans marked yet (optional).</span>';
  }

  function refreshSpanLockFor(card, idx) {
    const r = state.ratings[idx];
    const allRated = isComplete(r);
    const section = card.querySelector(".span-section");
    if (!section) return;
    section.classList.toggle("locked", !allRated);
    let hint = section.querySelector(".span-locked-hint");
    if (!allRated) {
      if (!hint) {
        hint = document.createElement("div");
        hint.className = "span-locked-hint";
        hint.textContent = "Finish rating all criteria for this segment to start marking error spans.";
        const help = section.querySelector(".span-help");
        if (help) help.after(hint);
        else section.querySelector(".span-section-header").after(hint);
      }
    } else if (hint) {
      hint.remove();
    }
  }

  function captureNativeSelection(card, idx, clickOffset, pane) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return false;
    const el = paneEl(card, pane);
    const range = sel.getRangeAt(0);
    if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) return false;
    let startOff = selectionPointToOffset(card, idx, range.startContainer, range.startOffset, pane);
    let endOff   = selectionPointToOffset(card, idx, range.endContainer,   range.endOffset, pane);
    if (startOff == null || endOff == null) return false;
    if (startOff > endOff) [startOff, endOff] = [endOff, startOff];
    const text = paneText(idx, pane);
    if (startOff === endOff) {
      if (clickOffset != null) {
        if (removeSpanAt(card, idx, clickOffset, pane)) return true;
        const [ws, we] = expandRangeToWords(clickOffset, clickOffset + 1, text);
        if (ws < we) addSpan(card, idx, state.activeErrorType[idx], ws, we, pane);
        return true;
      }
      return false;
    }
    addSpan(card, idx, state.activeErrorType[idx], startOff, endOff, pane);
    sel.removeAllRanges();
    return true;
  }

  function wirePaneFor(card, idx, pane) {
    const el = paneEl(card, pane);
    if (!el) return;
    function handleEnd(clickOffset) {
      const section = card.querySelector(".span-section");
      if (section && section.classList.contains("locked")) return;
      setTimeout(() => captureNativeSelection(card, idx, clickOffset, pane), 0);
    }
    el.addEventListener("mouseup", (e) => {
      handleEnd(pointToOffset(card, idx, e.clientX, e.clientY, pane));
    });
    el.addEventListener("touchend", (e) => {
      const t = e.changedTouches[0];
      handleEnd(t ? pointToOffset(card, idx, t.clientX, t.clientY, pane) : null);
    });
  }

  function initSpanInteractionsFor(card, idx) {
    wirePaneFor(card, idx, "target");
    if (SPAN_SCOPE === "both") wirePaneFor(card, idx, "source");

    card.querySelector(".span-clear-current").addEventListener("click", () => {
      const r = state.ratings[idx];
      if (!r) return;
      const t = state.activeErrorType[idx];
      if ((r.spans[t] || []).length === 0) return;
      if (!confirm("Remove all spans of this error type for this segment?")) return;
      r.spans[t] = [];
      r.dirty = true;
      renderSpanTargetFor(card, idx);
      refreshErrorTypePickerFor(card, idx);
      refreshSpanSummaryFor(card, idx);
    });

    card.querySelector(".toggle-span-help").addEventListener("click", () => {
      const body = card.querySelector(".span-help");
      const btn = card.querySelector(".toggle-span-help");
      const collapsed = body.classList.toggle("collapsed");
      btn.textContent = collapsed ? "Show help" : "Hide help";
    });
  }

  /* ===================== render a card ===================== */

  function buildCard(idx) {
    const tpl = $("segment-template");
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.dataset.segIndex = idx;

    const seg = SEGMENTS[idx];

    // header
    card.querySelector(".segment-number").textContent = "Segment " + (idx + 1);
    const pairText = (CAMPAIGN.source_language || "") + " → " + (CAMPAIGN.target_language || "");
    const pairChip = card.querySelector(".meta-pair");
    const domainChip = card.querySelector(".meta-domain");
    const sysChip = card.querySelector(".meta-system");
    pairChip.textContent = seg.pair || pairText;
    domainChip.textContent = seg.domain || "";
    sysChip.textContent = seg.system || "";
    pairChip.style.display  = (pairChip.textContent.trim() === "→") ? "none" : "";
    domainChip.style.display = seg.domain ? "" : "none";
    sysChip.style.display    = seg.system ? "" : "none";

    // left: texts
    card.querySelector(".text-source").textContent = seg.source;
    card.querySelector(".text-target").textContent = seg.target;
    card.querySelector(".text-reference").textContent = seg.reference || "(no reference provided)";

    // right: criteria circles
    buildCriteriaUIFor(card, idx);

    // span section
    const spanSection = card.querySelector(".span-section");
    if (ENABLE_SPANS) {
      if (SPAN_SCOPE === "both") {
        const srcWrap = card.querySelector(".span-source-wrap");
        if (srcWrap) srcWrap.hidden = false;
      }
      buildErrorTypePickerFor(card, idx);
      initSpanInteractionsFor(card, idx);
    } else if (spanSection) {
      spanSection.remove();
    }

    // comments
    const ta = card.querySelector(".seg-comments");
    ta.value = state.ratings[idx].comments;
    ta.addEventListener("input", (e) => {
      const r = state.ratings[idx];
      if (r.comments !== e.target.value) {
        r.comments = e.target.value;
        r.dirty = true;
      }
    });

    // initial paint
    refreshCriteriaUIFor(card, idx);
    if (ENABLE_SPANS) {
      renderSpanTargetFor(card, idx);
      refreshErrorTypePickerFor(card, idx);
      refreshSpanSummaryFor(card, idx);
      refreshSpanLockFor(card, idx);
    }
    refreshSavedBadge(card, idx);

    state.segmentStartTs[idx] = Date.now();
    return card;
  }

  function refreshSavedBadge(card, idx) {
    const badge = card.querySelector(".segment-saved-badge");
    if (!badge) return;
    const r = state.ratings[idx];
    if (r.saved && !r.dirty) {
      badge.textContent = "✓ Saved";
      badge.className = "segment-saved-badge saved";
    } else if (isComplete(r)) {
      badge.textContent = "Ready to save";
      badge.className = "segment-saved-badge ready";
    } else {
      badge.textContent = "";
      badge.className = "segment-saved-badge";
    }
  }

  /* ===================== page rendering ===================== */

  function renderPage(page) {
    state.currentPage = page;
    const stack = $("segment-stack");
    stack.innerHTML = "";
    cards = new Array(SEGMENTS.length).fill(null);

    const { start, end } = pageBounds(page);
    for (let idx = start; idx < end; idx++) {
      const card = buildCard(idx);
      cards[idx] = card;
      stack.appendChild(card);
    }

    $("page-current").textContent = page + 1;
    $("page-total").textContent = NUM_PAGES;
    $("btn-prev").disabled = page === 0;
    $("btn-next").textContent = (page === NUM_PAGES - 1) ? "Finish →" : "Save & next page →";

    refreshNextEnabled();
    updateGlobalProgress();
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function updateGlobalProgress() {
    const ratedCount = state.ratings.filter(isComplete).length;
    $("rated-count").textContent = ratedCount;
    const pct = Math.round((ratedCount / SEGMENTS.length) * 100);
    $("progress-fill").style.width = pct + "%";
    // refresh saved badges for visible cards
    const { start, end } = pageBounds(state.currentPage);
    for (let idx = start; idx < end; idx++) {
      if (cards[idx]) refreshSavedBadge(cards[idx], idx);
    }
  }

  /* ===================== save ===================== */

  async function saveSegment(idx, silent) {
    const r = state.ratings[idx];
    if (!isComplete(r)) return { ok: false, reason: "incomplete", idx };
    if (!r.dirty && r.saved) return { ok: true, skipped: true, idx };

    const seg = SEGMENTS[idx];
    const payload = {
      segment_id: seg.id,
      scores: r.scores,
      spans: r.spans,
      comments: r.comments,
      time_spent_seconds: Math.round((Date.now() - (state.segmentStartTs[idx] || Date.now())) / 1000),
    };

    try {
      const resp = await fetch(SUBMIT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || "Server error");
      r.saved = true;
      r.dirty = false;
      if (cards[idx]) refreshSavedBadge(cards[idx], idx);
      return { ok: true, idx };
    } catch (err) {
      return { ok: false, reason: err.message, idx };
    }
  }

  // Save all complete + dirty segments on the current page.
  async function saveCurrentPage(silent) {
    const { start, end } = pageBounds(state.currentPage);
    const status = $("save-status");
    if (!silent) {
      status.textContent = "Saving…";
      status.className = "save-status";
    }
    const results = [];
    for (let idx = start; idx < end; idx++) {
      const res = await saveSegment(idx, true);
      results.push(res);
    }
    const failed = results.filter(x => !x.ok && x.reason !== "incomplete");
    const savedAny = results.some(x => x.ok && !x.skipped);
    if (failed.length) {
      status.textContent = "Could not save some segments: " + failed[0].reason + ". Please try again.";
      status.className = "save-status error";
      return { ok: false };
    }
    if (!silent) {
      status.textContent = savedAny ? "✓ Saved" : "✓ Up to date";
      status.className = "save-status success";
    }
    return { ok: true };
  }

  /* ===================== navigation ===================== */

  $("btn-prev").addEventListener("click", async () => {
    if (state.currentPage === 0) return;
    await saveCurrentPage(true);
    renderPage(state.currentPage - 1);
  });

  $("btn-next").addEventListener("click", async () => {
    const btn = $("btn-next");
    if (!currentPageComplete()) {
      const status = $("save-status");
      status.textContent = "Please rate every criterion on every segment of this page before continuing.";
      status.className = "save-status error";
      return;
    }
    btn.disabled = true;
    const result = await saveCurrentPage(false);
    btn.disabled = !currentPageComplete();
    if (!result.ok) return;
    if (state.currentPage < NUM_PAGES - 1) {
      setTimeout(() => renderPage(state.currentPage + 1), 250);
    } else {
      finishSession();
    }
  });

  /* ===================== jump modal ===================== */

  $("btn-jump").addEventListener("click", openJumpModal);
  $("jump-close").addEventListener("click", () => $("jump-modal").hidden = true);
  $("jump-modal").addEventListener("click", (e) => {
    if (e.target === $("jump-modal")) $("jump-modal").hidden = true;
  });

  function openJumpModal() {
    const list = $("jump-list");
    list.innerHTML = "";

    // First-time annotation: only let the annotator jump within the current page.
    // Review mode (after finishing or via the Review button): show all segments.
    const { start: pgStart, end: pgEnd } = pageBounds(state.currentPage);
    const showAll = state.reviewMode;
    const indices = [];
    if (showAll) {
      for (let i = 0; i < SEGMENTS.length; i++) indices.push(i);
    } else {
      for (let i = pgStart; i < pgEnd; i++) indices.push(i);
    }

    const titleEl = $("jump-modal").querySelector(".modal-header h3");
    if (titleEl) {
      titleEl.textContent = showAll ? "Jump to segment" : "Jump to segment on this page";
    }

    indices.forEach((i) => {
      const seg = SEGMENTS[i];
      const r = state.ratings[i];
      const div = document.createElement("div");
      div.className = "jump-item";
      const status = isComplete(r) ? "complete" : "incomplete";
      const statusLabel = isComplete(r) ? "Rated" : "Not rated";
      const pageNo = Math.floor(i / PER_PAGE) + 1;
      const pageBadge = showAll ? '<span class="jump-item-page">p.' + pageNo + '</span>' : '';
      div.innerHTML =
        '<span class="jump-item-num">#' + (i + 1) + '</span>' +
        '<span class="jump-item-text">' + escapeHtml(seg.target.slice(0, 80)) + '</span>' +
        pageBadge +
        '<span class="jump-item-status ' + status + '">' + statusLabel + '</span>';
      div.addEventListener("click", async () => {
        // In review mode, jumping is always allowed.
        // In first-pass mode, the list only contains current-page segments,
        // so a save is unnecessary (we're staying on the same page).
        $("jump-modal").hidden = true;
        const targetPage = Math.floor(i / PER_PAGE);
        if (targetPage !== state.currentPage) {
          if (state.reviewMode) {
            // saveCurrentPage will no-op for segments that aren't dirty
            await saveCurrentPage(true);
          }
          renderPage(targetPage);
        }
        const card = cards[i];
        if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      list.appendChild(div);
    });
    $("jump-modal").hidden = false;
  }

  /* ===================== finish ===================== */

  function finishSession() {
    state.reviewMode = true;
    $("progress-fill").style.width = "100%";
    showScreen("screen-done");
  }

  $("btn-review").addEventListener("click", () => {
    state.reviewMode = true;
    showScreen("screen-rate");
    openJumpModal();
  });

  $("toggle-instructions").addEventListener("click", () => {
    const body = $("instructions-body");
    const btn = $("toggle-instructions");
    const collapsed = body.classList.toggle("collapsed");
    btn.textContent = collapsed ? "Show" : "Hide";
  });

  /* ===================== unload warning ===================== */

  window.addEventListener("beforeunload", (e) => {
    const anyDirty = state.ratings.some(r => r.dirty);
    if (anyDirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  /* ===================== init ===================== */

  // Start on the page containing the first incomplete segment, if any.
  // If every segment was already rated in a prior visit, enter review mode.
  const firstIncomplete = state.ratings.findIndex(r => !isComplete(r));
  if (firstIncomplete === -1) state.reviewMode = true;
  const startSeg = firstIncomplete === -1 ? 0 : firstIncomplete;
  const startPage = Math.floor(startSeg / PER_PAGE);
  renderPage(startPage);
})();
