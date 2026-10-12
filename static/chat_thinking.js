(function initGrvChatThinking() {
  "use strict";

  const WIN = window.parent && window.parent.document ? window.parent : window;
  const DOC = WIN.document;

  if (WIN.__grvChatThinkingReady) {
    return;
  }
  WIN.__grvChatThinkingReady = true;

  const THINK_OPEN = "<" + "think" + ">";
  const THINK_CLOSE = "</" + "think" + ">";

  const BLOCK_RES = [
    /<think>([\s\S]*?)<\/redacted_thinking>/gi,
    new RegExp(THINK_OPEN + "([\\s\\S]*?)" + THINK_CLOSE, "gi"),
  ];

  const OPEN_RES = [
    /<think>([\s\S]*)$/i,
    new RegExp(THINK_OPEN + "([\\s\\S]*)$", "i"),
  ];

  const TAG_RES = [
    /<\/?redacted_thinking>/gi,
    new RegExp("<\\/?" + "think" + ">", "gi"),
  ];

  function debounce(fn, ms) {
    let t = null;
    return function () {
      clearTimeout(t);
      const args = arguments;
      const ctx = this;
      t = setTimeout(function () {
        fn.apply(ctx, args);
      }, ms);
    };
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function hasThinkingMarkup(text) {
    if (!text) {
      return false;
    }
    return (
      /<\s*redacted_thinking\b/i.test(text) ||
      new RegExp("<\\s*" + "think" + "\\b", "i").test(text)
    );
  }

  function extractThinking(raw) {
    let text = String(raw || "");
    const thoughts = [];
    let streaming = false;

    BLOCK_RES.forEach(function (re) {
      re.lastIndex = 0;
      text = text.replace(re, function (_, body) {
        if (body && body.trim()) {
          thoughts.push(body.trim());
        }
        return "";
      });
    });

    OPEN_RES.forEach(function (re) {
      const m = text.match(re);
      if (m && m[1] !== undefined) {
        const partial = m[1].trim();
        if (partial) {
          thoughts.push(partial);
        }
        text = text.replace(re, "");
        streaming = true;
      }
    });

    TAG_RES.forEach(function (re) {
      text = text.replace(re, "");
    });

    return {
      thoughts: thoughts,
      text: text.replace(/\n{3,}/g, "\n\n").trim(),
      streaming: streaming,
    };
  }

  function isAssistantMessage(messageEl) {
    return Boolean(
      messageEl.querySelector('[data-testid="chatAvatarIcon-assistant"]')
    );
  }

  function fingerprint(thoughts, streaming) {
    return thoughts.join("\x1e") + (streaming ? "|stream" : "");
  }

  function buildShell(thoughts, streaming, open) {
    const shell = DOC.createElement("div");
    shell.className = "grv-think-shell";
    shell.dataset.open = open ? "1" : "0";
    shell.dataset.streaming = streaming ? "1" : "0";
    shell.dataset.fp = fingerprint(thoughts, streaming);

    const header = DOC.createElement("button");
    header.type = "button";
    header.className = "grv-think-header";
    header.innerHTML =
      '<span class="grv-think-spinner" aria-hidden="true"></span>' +
      '<span class="grv-think-title">AI 正在思考...</span>' +
      '<span class="grv-think-chevron" aria-hidden="true">▸</span>';

    const body = DOC.createElement("div");
    body.className = "grv-think-body";
    body.textContent = thoughts.join("\n\n— — —\n\n");

    header.addEventListener("click", function () {
      const next = shell.dataset.open === "1" ? "0" : "1";
      shell.dataset.open = next;
      header.setAttribute("aria-expanded", next === "1" ? "true" : "false");
    });
    header.setAttribute("aria-expanded", open ? "true" : "false");

    shell.appendChild(header);
    shell.appendChild(body);
    return shell;
  }

  function renderCleanAnswer(contentEl, cleanedText) {
    if (!cleanedText) {
      contentEl.innerHTML = "";
      return;
    }
    const parts = cleanedText.split(/\n{2,}/).filter(Boolean);
    contentEl.innerHTML = parts
      .map(function (p) {
        return "<p>" + escapeHtml(p).replace(/\n/g, "<br>") + "</p>";
      })
      .join("");
  }

  function scrubParagraphs(contentEl) {
    contentEl.querySelectorAll("p, pre, li, span").forEach(function (node) {
      const t = node.textContent || "";
      if (!hasThinkingMarkup(t)) {
        return;
      }
      const parsed = extractThinking(t);
      if (parsed.text) {
        node.textContent = parsed.text;
      } else {
        node.remove();
      }
    });
  }

  function processMessage(messageEl) {
    if (!isAssistantMessage(messageEl)) {
      return;
    }

    const contentEl = messageEl.querySelector('[data-testid="stChatMessageContent"]');
    if (!contentEl) {
      return;
    }

    const raw = contentEl.textContent || "";
    if (!hasThinkingMarkup(raw)) {
      return;
    }

    const parsed = extractThinking(raw);
    if (!parsed.thoughts.length) {
      return;
    }

    const fp = fingerprint(parsed.thoughts, parsed.streaming);
    let shell = messageEl.querySelector(":scope > .grv-think-shell");

    if (shell && shell.dataset.fp === fp) {
      return;
    }

    if (shell) {
      shell.remove();
    }

    shell = buildShell(parsed.thoughts, parsed.streaming, false);
    messageEl.insertBefore(shell, contentEl);

    scrubParagraphs(contentEl);
    const remaining = (contentEl.textContent || "").trim();
    const finalText = parsed.text || remaining;
    if (hasThinkingMarkup(finalText)) {
      renderCleanAnswer(contentEl, extractThinking(finalText).text);
    } else if (hasThinkingMarkup(contentEl.textContent || "")) {
      renderCleanAnswer(contentEl, parsed.text);
    } else if (parsed.text && parsed.text !== remaining) {
      renderCleanAnswer(contentEl, parsed.text);
    }
  }

  function scanAll() {
    DOC.querySelectorAll('[data-testid="stChatMessage"]').forEach(processMessage);
  }

  const runScan = debounce(scanAll, 60);

  const observer = new MutationObserver(runScan);
  observer.observe(DOC.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  runScan();
  setInterval(runScan, 800);
})();
