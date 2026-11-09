(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const form = $("#url-form");
  const input = $("#url-input");
  const fetchButton = $("#fetch-button");
  const exampleButton = $("#example-button");
  const formError = $("#form-error");
  const result = $("#result");
  const deckTitle = $("#deck-title");
  const deckMeta = $("#deck-meta");
  const deckSource = $("#deck-source");
  const deckCover = $("#deck-cover-img");
  const coverFallback = $("#cover-fallback");
  const deckSlideCount = $("#deck-slide-count");
  const formatGrid = $("#format-grid");
  const progressPanel = $("#progress-panel");
  const progressIcon = $("#progress-icon");
  const progressStage = $("#progress-stage");
  const progressDetail = $("#progress-detail");
  const progressPercent = $("#progress-percent");
  const progressFill = $("#progress-fill");
  const progressBar = $("#progress-bar");
  const readyPanel = $("#ready-panel");
  const readyTitle = $("#ready-title");
  const readyDetail = $("#ready-detail");
  const readyFilename = $("#ready-filename");
  const readySize = $("#ready-size");
  const downloadLink = $("#download-link");
  const errorPanel = $("#error-panel");
  const errorMessage = $("#error-message");

  const EXAMPLE = "https://www.slideshare.net/slideshow/diversity-wins-how-inclusion-matters/249457464";
  const FORMAT_META = {
    pdf: { name: "PDF", icon: "#i-file", ready: "Your PDF is ready" },
    pptx: { name: "PowerPoint", icon: "#i-presentation", ready: "Your PowerPoint is ready" },
    zip: { name: "ZIP", icon: "#i-zip", ready: "Your slide images are ready" },
  };

  let state = { deck: null, analyzing: false, building: false, abort: null };

  function showFormError(message) {
    formError.textContent = message;
    formError.hidden = !message;
  }

  function clearPanels() {
    progressPanel.hidden = true;
    errorPanel.hidden = true;
    readyPanel.hidden = true;
    if (state.abort) { try { state.abort.abort(); } catch {} state.abort = null; }
  }

  function setLoading(loading) {
    state.analyzing = loading;
    fetchButton.disabled = loading;
    fetchButton.classList.toggle("is-loading", loading);
  }

  function setCardBusy(format, busy) {
    const card = $(`.format-card[data-format="${format}"]`, formatGrid);
    if (!card) return;
    card.classList.toggle("is-busy", busy);
    const button = $("button[data-download]", card);
    if (button) { button.disabled = busy; button.classList.toggle("is-loading", busy); }
  }

  function resetCards() {
    $$(".format-card", formatGrid).forEach((card) => {
      card.classList.remove("is-busy", "is-done");
      const button = $("button[data-download]", card);
      if (button) { button.disabled = false; button.classList.remove("is-loading"); }
      const chip = $(".ready-chip", card);
      if (chip) chip.hidden = true;
    });
  }

  async function apiJson(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    try { data = await response.json(); } catch {}
    if (!response.ok) {
      throw new Error((data && data.detail) || response.statusText || "Request failed.");
    }
    return data;
  }

  function renderDeck(deck) {
    state.deck = deck;
    result.hidden = false;
    deckTitle.textContent = deck.title || "Presentation";
    const parts = [];
    if (deck.author) parts.push(`By ${deck.author}`);
    if (deck.url) { try { parts.push(new URL(deck.url).hostname.replace(/^www\./, "")); } catch {} }
    deckMeta.textContent = parts.join(" · ");
    deckSource.href = deck.url;
    deckSlideCount.textContent = String(deck.slides || 0);
    coverFallback.hidden = true;
    deckCover.hidden = false;
    deckCover.onload = () => { coverFallback.hidden = true; };
    deckCover.onerror = () => { deckCover.hidden = true; coverFallback.hidden = false; };
    deckCover.src = deck.thumbnail || "";
    resetCards();
    clearPanels();
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function looksLikeSlideShare(value) {
    try {
      const host = new URL(value.startsWith("http") ? value : `https://${value}`).hostname.replace(/^www\./, "");
      return host === "slideshare.net" || host === "m.slideshare.net";
    } catch { return false; }
  }

  async function analyze(url) {
    if (state.analyzing) return;
    if (!looksLikeSlideShare(url)) {
      showFormError("That doesn’t look like a SlideShare link. Try a slideshare.net URL.");
      return;
    }
    showFormError("");
    if (!result.hidden) result.classList.add("is-refreshing");
    setLoading(true);
    clearPanels();
    try {
      const deck = await apiJson("/api/analyze", { method: "POST", body: JSON.stringify({ url }) });
      renderDeck(deck);
    } catch (error) {
      showFormError(error.message);
    } finally {
      result.classList.remove("is-refreshing");
      setLoading(false);
    }
  }

  function updateProgress(format, stage, detail, pct) {
    progressStage.textContent = stage || "Working on it";
    progressDetail.textContent = detail || "";
    progressPercent.textContent = String(Math.max(0, Math.min(100, pct || 0)));
    progressFill.style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
    progressBar.setAttribute("aria-valuenow", String(pct || 0));
    const meta = FORMAT_META[format] || FORMAT_META.pdf;
    progressIcon.className = `format-icon format-${format}`;
    const icon = $("use", progressIcon);
    if (icon) icon.setAttribute("href", meta.icon);
  }

  function filenameFromDisposition(header, fallback) {
    if (!header) return fallback;
    const m = /filename\*?=(?:UTF-8''|\")?([^\";\n]+)/i.exec(header);
    if (!m) return fallback;
    try { return decodeURIComponent(m[1].replace(/\"/g, "").trim()); } catch { return fallback; }
  }

  function formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
    return `${(n / (1024 * 1024)).toFixed(n >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
  }

  async function startBuild(format) {
    if (!state.deck || state.analyzing || state.building) return;
    state.building = true;
    setCardBusy(format, true);
    clearPanels();
    errorPanel.hidden = true;
    updateProgress(format, "Starting", "Reading the presentation", 6);
    progressPanel.hidden = false;
    progressPanel.scrollIntoView({ behavior: "smooth", block: "center" });

    const ctrl = new AbortController();
    state.abort = ctrl;
    try {
      updateProgress(format, "Fetching slides", "Downloading slide images in parallel", 18);
      const res = await fetch("/api/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: state.deck.url, format }),
        signal: ctrl.signal,
      });
      updateProgress(format, "Building file", format === "pdf" ? "Compressing and assembling PDF" : format === "pptx" ? "Assembling PowerPoint" : "Packing images", 72);

      if (!res.ok) {
        let msg = "The file could not be built. Please try again.";
        try { const j = await res.json(); if (j && j.detail) msg = j.detail; } catch {}
        throw new Error(msg);
      }
      const blob = await res.blob();
      updateProgress(format, "Ready", "Preparing download", 100);
      const fallback = `${state.deck.slug || "presentation"}.${format === "zip" ? "zip" : format}`;
      const filename = filenameFromDisposition(res.headers.get("Content-Disposition"), fallback);
      const href = URL.createObjectURL(blob);

      progressPanel.hidden = true;
      readyPanel.hidden = false;
      const meta = FORMAT_META[format] || FORMAT_META.pdf;
      readyTitle.textContent = meta.ready;
      readyDetail.textContent = format === "pdf" ? "Optimized and ready to download." : format === "pptx" ? "Every slide is in place, one high-resolution image per page." : "Original slide images, packed and ready.";
      readyFilename.textContent = filename;
      readySize.textContent = formatBytes(blob.size);
      downloadLink.href = href;
      downloadLink.setAttribute("download", filename);
      const card = $(`.format-card[data-format="${format}"]`, formatGrid);
      if (card) { card.classList.add("is-done"); const chip = $(".ready-chip", card); if (chip) chip.hidden = false; }

      // Auto-trigger download
      const a = document.createElement("a");
      a.href = href; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 60000);
      readyPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (error) {
      if (error && error.name === "AbortError") return;
      progressPanel.hidden = true;
      readyPanel.hidden = true;
      errorPanel.hidden = false;
      errorMessage.textContent = (error && error.message) || "Something went wrong. Please try again.";
      errorPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    } finally {
      state.building = false;
      state.abort = null;
      setCardBusy(format, false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    analyze(input.value.trim());
  });
  exampleButton.addEventListener("click", () => { input.value = EXAMPLE; analyze(EXAMPLE); });
  formatGrid.addEventListener("click", (e) => {
    const button = e.target.closest("[data-download]");
    if (!button) return;
    startBuild(button.dataset.download);
  });
  $("#another-format-button").addEventListener("click", () => { clearPanels(); result.scrollIntoView({ behavior: "smooth", block: "start" }); });
  $("#new-deck-button").addEventListener("click", () => { input.value = ""; input.focus(); result.hidden = true; showFormError(""); clearPanels(); resetCards(); state.deck = null; window.scrollTo({ top: 0, behavior: "smooth" }); });
  $("#retry-button").addEventListener("click", () => { clearPanels(); });
  $("#error-start-over").addEventListener("click", () => { $("#new-deck-button").click(); });
})();
