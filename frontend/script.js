/**
 * script.js
 * ---------
 * Client-side controller for NoteTube AI. Handles:
 *   - Submitting the YouTube URL to POST /api/generate
 *   - Loading-state overlay with progress messaging
 *   - A lightweight Markdown -> HTML renderer for the results tabs
 *   - Tab switching, copy-to-clipboard, and file download
 */

(() => {
  "use strict";

  // ---------------------------------------------------------------------
  // Config
  // ---------------------------------------------------------------------
  const API_BASE = window.NOTETUBE_API_BASE || "http://localhost:5000";

  // ---------------------------------------------------------------------
  // DOM references
  // ---------------------------------------------------------------------
  const form = document.getElementById("generate-form");
  const urlInput = document.getElementById("youtube-url");
  const generateBtn = document.getElementById("generate-btn");
  const formHint = document.getElementById("form-hint");

  const resultsSection = document.getElementById("results-section");
  const resultVideoTitle = document.getElementById("result-video-title");

  const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
  const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));

  const panelNotes = document.getElementById("panel-notes");
  const panelQuiz = document.getElementById("panel-quiz");
  const panelInterview = document.getElementById("panel-interview");

  const copyBtn = document.getElementById("copy-btn");
  const copyBtnLabel = document.getElementById("copy-btn-label");
  const downloadDocxBtn = document.getElementById("download-docx-btn");

  const errorBanner = document.getElementById("error-banner");
  const errorMessage = document.getElementById("error-message");
  const errorDismiss = document.getElementById("error-dismiss");

  const loadingOverlay = document.getElementById("loading-overlay");
  const loadingStep = document.getElementById("loading-step");

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------
  let rawMarkdown = "";
  let currentDownloadUrl = "";

  // ---------------------------------------------------------------------
  // Minimal Markdown -> HTML renderer
  // Supports: #/##/### headings, **bold**, `code`, fenced ``` code blocks,
  // - / * bullet lists, 1. numbered lists, pipe tables, paragraphs.
  // Intentionally lightweight — no external dependency, and defensive
  // against malformed input so it never throws.
  // ---------------------------------------------------------------------
  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderInline(text) {
    let safe = escapeHtml(text);
    safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
    safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return safe;
  }

  function renderMarkdown(markdown) {
    if (!markdown || !markdown.trim()) return "";

    const lines = markdown.replace(/\r\n/g, "\n").split("\n");
    let html = "";
    let i = 0;
    let listMode = null; // 'ul' | 'ol' | null

    function closeList() {
      if (listMode) {
        html += listMode === "ul" ? "</ul>" : "</ol>";
        listMode = null;
      }
    }

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        closeList();
        i++;
        continue;
      }

      // Fenced code block
      if (trimmed.startsWith("```")) {
        closeList();
        const codeLines = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) {
          codeLines.push(lines[i]);
          i++;
        }
        i++; // skip closing fence
        html += `<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`;
        continue;
      }

      // Table
      if (trimmed.startsWith("|") && lines[i + 1] && /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim())) {
        closeList();
        const headerCells = trimmed.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
          i++;
        }
        html += "<table><thead><tr>";
        headerCells.forEach((cell) => { html += `<th>${renderInline(cell)}</th>`; });
        html += "</tr></thead><tbody>";
        rows.forEach((row) => {
          html += "<tr>";
          row.forEach((cell) => { html += `<td>${renderInline(cell)}</td>`; });
          html += "</tr>";
        });
        html += "</tbody></table>";
        continue;
      }

      // Headings
      const headingMatch = trimmed.match(/^(#{1,3})\s+(.*)$/);
      if (headingMatch) {
        closeList();
        const level = headingMatch[1].length;
        html += `<h${level}>${renderInline(headingMatch[2])}</h${level}>`;
        i++;
        continue;
      }

      // Bullet list
      const bulletMatch = trimmed.match(/^[-*]\s+(.*)$/);
      if (bulletMatch) {
        if (listMode !== "ul") { closeList(); html += "<ul>"; listMode = "ul"; }
        html += `<li>${renderInline(bulletMatch[1])}</li>`;
        i++;
        continue;
      }

      // Numbered list
      const numberedMatch = trimmed.match(/^\d+[.)]\s+(.*)$/);
      if (numberedMatch) {
        if (listMode !== "ol") { closeList(); html += "<ol>"; listMode = "ol"; }
        html += `<li>${renderInline(numberedMatch[1])}</li>`;
        i++;
        continue;
      }

      // Horizontal rule
      if (/^-{3,}$/.test(trimmed)) {
        closeList();
        i++;
        continue;
      }

      // Paragraph
      closeList();
      html += `<p>${renderInline(trimmed)}</p>`;
      i++;
    }

    closeList();
    return html;
  }

  // ---------------------------------------------------------------------
  // Split the full markdown document into named sections by H1 heading,
  // so we can route content to the right tab.
  // ---------------------------------------------------------------------
  function splitIntoSections(markdown) {
    const sections = {};
    const lines = markdown.replace(/\r\n/g, "\n").split("\n");
    let currentKey = null;
    let buffer = [];

    function flush() {
      if (currentKey) {
        sections[currentKey] = (sections[currentKey] || "") + buffer.join("\n") + "\n";
      }
      buffer = [];
    }

    const keyMap = [
      { key: "summary", pattern: /executive summary/i },
      { key: "notes", pattern: /comprehensive( chapter)? notes/i },
      { key: "takeaways", pattern: /key takeaways/i },
      { key: "mcqs", pattern: /multiple choice questions/i },
      { key: "answers", pattern: /answer key/i },
      { key: "interview", pattern: /interview questions/i },
    ];

    for (const line of lines) {
      const h1Match = line.trim().match(/^#\s+(.*)$/);
      if (h1Match) {
        flush();
        const title = h1Match[1];
        const matched = keyMap.find((k) => k.pattern.test(title));
        currentKey = matched ? matched.key : title.toLowerCase().replace(/\s+/g, "_");
        buffer.push(line);
        continue;
      }
      buffer.push(line);
    }
    flush();

    return sections;
  }

  function renderPanel(panelEl, html, emptyMessage) {
    if (!html || !html.trim()) {
      panelEl.innerHTML = `<div class="empty-panel">${emptyMessage}</div>`;
      return;
    }
    panelEl.innerHTML = `<div class="panel-card">${html}</div>`;
  }

  function populateResultTabs(markdown) {
    const sections = splitIntoSections(markdown);

    const notesMarkdown = [sections.summary, sections.notes, sections.takeaways]
      .filter(Boolean)
      .join("\n");
    const quizMarkdown = [sections.mcqs, sections.answers].filter(Boolean).join("\n");
    const interviewMarkdown = sections.interview || "";

    renderPanel(panelNotes, renderMarkdown(notesMarkdown), "No notes were generated.");
    renderPanel(panelQuiz, renderMarkdown(quizMarkdown), "No quiz questions were generated.");
    renderPanel(panelInterview, renderMarkdown(interviewMarkdown), "No interview questions were generated.");
  }

  // ---------------------------------------------------------------------
  // Tabs
  // ---------------------------------------------------------------------
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;

      tabButtons.forEach((b) => {
        b.classList.toggle("is-active", b === btn);
        b.setAttribute("aria-selected", b === btn ? "true" : "false");
      });
      tabPanels.forEach((panel) => {
        panel.classList.toggle("is-active", panel.dataset.panel === target);
      });
    });
  });

  // ---------------------------------------------------------------------
  // Loading overlay helpers
  // ---------------------------------------------------------------------
  const LOADING_STEPS = [
    "Fetching transcript…",
    "Reading through the lecture…",
    "Drafting your study notes…",
    "Building quiz questions…",
    "Formatting your document…",
  ];

  let loadingStepTimer = null;

  function showLoading() {
    loadingOverlay.hidden = false;
    let idx = 0;
    loadingStep.textContent = LOADING_STEPS[idx];
    loadingStepTimer = setInterval(() => {
      idx = (idx + 1) % LOADING_STEPS.length;
      loadingStep.textContent = LOADING_STEPS[idx];
    }, 2600);
  }

  function hideLoading() {
    loadingOverlay.hidden = true;
    if (loadingStepTimer) {
      clearInterval(loadingStepTimer);
      loadingStepTimer = null;
    }
  }

  // ---------------------------------------------------------------------
  // Error banner helpers
  // ---------------------------------------------------------------------
  function showError(message) {
    errorMessage.textContent = message;
    errorBanner.hidden = false;
  }

  function hideError() {
    errorBanner.hidden = true;
  }

  errorDismiss.addEventListener("click", hideError);

  // ---------------------------------------------------------------------
  // Form submission
  // ---------------------------------------------------------------------
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError();

    const url = urlInput.value.trim();
    if (!url) return;

    generateBtn.disabled = true;
    formHint.textContent = "This can take a minute for longer videos.";
    showLoading();

    try {
      const response = await fetch(`${API_BASE}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      let data;
      try {
        data = await response.json();
      } catch (parseErr) {
        throw new Error("The server returned an unexpected response. Please try again.");
      }

      if (!response.ok || !data.success) {
        throw new Error(data.error || "Something went wrong while generating notes.");
      }

      rawMarkdown = data.markdown_content || "";
      currentDownloadUrl = `${API_BASE}${data.download_url}`;

      resultVideoTitle.textContent = "Your Study Pack";
      populateResultTabs(rawMarkdown);
      resultsSection.hidden = false;
      resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(err.message || "Something went wrong. Please try again.");
    } finally {
      hideLoading();
      generateBtn.disabled = false;
      formHint.textContent = "Works best on videos with captions enabled.";
    }
  });

  // ---------------------------------------------------------------------
  // Copy notes
  // ---------------------------------------------------------------------
  copyBtn.addEventListener("click", async () => {
    if (!rawMarkdown) return;
    try {
      await navigator.clipboard.writeText(rawMarkdown);
      copyBtn.classList.add("is-copied");
      copyBtnLabel.textContent = "Copied!";
      setTimeout(() => {
        copyBtn.classList.remove("is-copied");
        copyBtnLabel.textContent = "Copy Notes";
      }, 1800);
    } catch (err) {
      showError("Couldn't copy to clipboard. Your browser may be blocking clipboard access.");
    }
  });

  // ---------------------------------------------------------------------
  // Download docx
  // ---------------------------------------------------------------------
  downloadDocxBtn.addEventListener("click", () => {
    if (!currentDownloadUrl) {
      showError("No document is ready to download yet.");
      return;
    }
    window.location.href = currentDownloadUrl;
  });

})();
