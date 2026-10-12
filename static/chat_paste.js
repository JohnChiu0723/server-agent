(function () {
  function getDoc() {
    let w = window;
    for (let depth = 0; depth < 5; depth++) {
      try {
        if (!w.parent || w.parent === w) break;
        const parentDoc = w.parent.document;
        if (parentDoc && parentDoc.querySelector('[data-testid="stApp"]')) {
          return parentDoc;
        }
        w = w.parent;
      } catch (e) {
        break;
      }
    }
    try {
      return window.parent.document;
    } catch (e) {
      return document;
    }
  }

  const doc = getDoc();

  function sendValue(value) {
    window.parent.postMessage(
      {
        isStreamlitMessage: true,
        type: "streamlit:setComponentValue",
        value: value,
      },
      "*"
    );
  }

  function toast(msg) {
    let t = doc.getElementById("grv-paste-toast");
    if (!t) {
      t = doc.createElement("div");
      t.id = "grv-paste-toast";
      t.style.cssText =
        "position:fixed;bottom:88px;left:50%;transform:translateX(-50%);" +
        "background:rgba(29,29,31,.9);color:#fff;font:500 12px/1.4 -apple-system,sans-serif;" +
        "padding:8px 14px;border-radius:20px;z-index:99999;opacity:0;transition:opacity .2s;" +
        "pointer-events:none;white-space:nowrap;";
      doc.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = "1";
    clearTimeout(t.__hide);
    t.__hide = setTimeout(function () {
      t.style.opacity = "0";
    }, 2000);
  }

  function chatInputRoot() {
    return doc.querySelector('[data-testid="stChatInput"]');
  }

  function chatInputFocused() {
    const root = chatInputRoot();
    return !!(root && root.matches(":focus-within"));
  }

  function inChatInput(el) {
    if (!el || !el.closest) return false;
    return !!el.closest('[data-testid="stChatInput"]');
  }

  function shouldHandlePaste(e) {
    if (inChatInput(e.target)) return true;
    if (chatInputFocused()) return true;
    const active = doc.activeElement;
    return inChatInput(active);
  }

  function handleImageBlob(blob, name) {
    if (!blob) return;
    const reader = new FileReader();
    reader.onload = function () {
      sendValue({
        data_url: reader.result,
        name: name || "pasted.png",
        mime: blob.type || "image/png",
      });
      toast("已貼上圖片，按 Enter 送出");
    };
    reader.readAsDataURL(blob);
  }

  function handleImagePaste(e) {
    if (!shouldHandlePaste(e)) return;

    const items = e.clipboardData ? e.clipboardData.items : null;
    if (!items || !items.length) return;

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (!item.type || item.type.indexOf("image") === -1) continue;

      e.preventDefault();
      e.stopPropagation();

      const file = item.getAsFile();
      if (!file) continue;

      handleImageBlob(file, file.name || "pasted.png");
      return;
    }

    // 僅在 WebView 完全沒有 clipboardData.items 時才走 Clipboard API
    if (!items || items.length === 0) {
      setTimeout(tryClipboardRead, 0);
    }
  }

  async function tryClipboardRead() {
    if (!chatInputFocused()) return;
    if (!navigator.clipboard || !navigator.clipboard.read) return;
    try {
      const items = await navigator.clipboard.read();
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const imgType = item.types.find(function (t) {
          return t.indexOf("image/") === 0;
        });
        if (!imgType) continue;
        const blob = await item.getType(imgType);
        handleImageBlob(blob, "pasted.png");
        return;
      }
    } catch (err) {
      /* 權限或 WebView 不支援時忽略，改靠 paste 事件 */
    }
  }

  function bindPasteCapture() {
    if (doc.__grvPasteCaptureBound) return;
    doc.__grvPasteCaptureBound = true;
    doc.addEventListener("paste", handleImagePaste, true);
  }

  function bindChatInputDirect() {
    const root = chatInputRoot();
    if (!root || root.__grvPasteDirectBound) return;
    root.__grvPasteDirectBound = true;
    root.addEventListener("paste", handleImagePaste, true);
  }

  function init() {
    bindPasteCapture();
    bindChatInputDirect();

    if (!doc.__grvPasteObserver) {
      doc.__grvPasteObserver = new MutationObserver(function () {
        bindChatInputDirect();
      });
      doc.__grvPasteObserver.observe(doc.body, {
        childList: true,
        subtree: true,
      });
    }

    window.parent.postMessage(
      {
        isStreamlitMessage: true,
        type: "streamlit:setFrameHeight",
        height: 1,
      },
      "*"
    );
  }

  init();
})();
