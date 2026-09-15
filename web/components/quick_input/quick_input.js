(() => {
  const host = window.parent;
  const doc = host.document;
  // Streamlit replaces the bridge iframe when its arguments change. Listeners
  // created in the old frame must be rebound even if its parent object survived.
  const previous = host.__dndQuickInput;
  const saved = previous?.snapshot();
  previous?.destroy();
  const selector = ".st-key-page2_chat_input textarea";
  const menuId = "dnd-quick-input-menu";
  const styleId = "dnd-quick-input-style";
  const style = doc.getElementById(styleId) || doc.createElement("style");
  style.id = styleId;
  style.textContent = `
    #${menuId}{position:fixed;z-index:10000;box-sizing:border-box;overflow-y:auto;
      background:#18202d;color:#e5e7eb;border:1px solid #475569;border-radius:10px;
      padding:6px;box-shadow:0 8px 24px #0005;font:14px/1.5 sans-serif;}
    #${menuId} [role=option]{display:flex;align-items:center;justify-content:space-between;
      gap:12px;padding:9px 12px;border-radius:6px;cursor:pointer;white-space:pre-wrap;overflow-wrap:anywhere;}
    #${menuId} [aria-selected=true]{background:#334155;}
    #${menuId} small{color:#aab8cc;flex-shrink:0;}
    div[data-testid="stVerticalBlock"].st-key-page2_chat_canvas.dnd-quick-input-open,
    .st-key-page2_chat_canvas.dnd-quick-input-open{
      padding-bottom:calc(14px + var(--dnd-quick-input-height)) !important;}
  `;
  if (!style.isConnected) doc.head.appendChild(style);
  let config = saved?.config || { options: [], scope: "", showSpace: false };
  let active = saved?.active || null;
  let menu = null;
  let matches = [];
  let selected = saved?.selected || 0;
  let composing = saved?.composing || false;
  let compositionSettling = false;
  let compositionRevision = 0;
  let compositionBefore = null;
  let inserting = false;
  let pendingInput = null;
  const listen = (name, fn) => doc.addEventListener(name, fn, true);
  const inputTarget = (event) => event.target?.matches?.(selector);

  function hideMenu() {
    const canvas = active?.input.closest(".st-key-page2_chat_canvas");
    canvas?.classList.remove("dnd-quick-input-open");
    canvas?.style.removeProperty("--dnd-quick-input-height");
    if (active?.input) {
      for (const attr of ["aria-controls", "aria-expanded", "aria-activedescendant", "aria-autocomplete"])
        active.input.removeAttribute(attr);
    }
    menu?.remove();
    menu = null;
  }

  function close() {
    hideMenu();
    active = null;
    matches = [];
  }

  function position() {
    if (!active || !menu) return;
    if (!active.input.isConnected || active.input.disabled) { close(); return; }
    const viewport = host.visualViewport;
    const viewportBottom = (viewport?.offsetTop || 0) + (viewport?.height || host.innerHeight);
    const desiredHeight = Math.min(240, Math.max(48, menu.scrollHeight + 2),
      Math.max(48, (viewport?.height || host.innerHeight) * 0.35));
    const canvas = active.input.closest(".st-key-page2_chat_canvas");
    if (canvas) {
      canvas.style.setProperty("--dnd-quick-input-height", `${desiredHeight + 6}px`);
      canvas.classList.add("dnd-quick-input-open");
    }
    const inputBox = active.input.closest('[data-testid="stChatInput"]') || active.input;
    const rect = inputBox.getBoundingClientRect();
    // Leave the menu below the composer, including on a short viewport.
    menu.style.left = `${Math.max(8, Math.min(rect.left, host.innerWidth - 168))}px`;
    menu.style.top = `${rect.bottom + 6}px`;
    menu.style.width = `${Math.max(160, Math.min(rect.width, host.innerWidth - 16))}px`;
    menu.style.maxHeight = `${Math.max(48, Math.min(desiredHeight, viewportBottom - rect.bottom - 14))}px`;
  }

  function highlight() {
    if (!menu || !active) return;
    [...menu.querySelectorAll('[role="option"]')].forEach((row, index) => {
      row.setAttribute("aria-selected", String(index === selected));
    });
    const row = menu.querySelector(`[id="${menuId}-${selected}"]`);
    if (row) {
      active.input.setAttribute("aria-activedescendant", row.id);
      if (row.offsetTop < menu.scrollTop) menu.scrollTop = row.offsetTop;
      else if (row.offsetTop + row.offsetHeight > menu.scrollTop + menu.clientHeight)
        menu.scrollTop = row.offsetTop + row.offsetHeight - menu.clientHeight;
    } else active.input.removeAttribute("aria-activedescendant");
  }

  function refresh() {
    if (!active) return;
    const { input, start, prefix, suffix } = active;
    const end = input.selectionStart;
    const token = input.value.slice(start, end);
    if (!input.isConnected || input.disabled || input.selectionEnd !== end || end <= start ||
        input.value.slice(0, start) !== prefix || input.value.slice(end) !== suffix ||
        !/^[\/／][^\s\/／]*$/u.test(token)) { close(); return; }
    active.end = end;
    const query = token.slice(1).toLocaleLowerCase();
    matches = config.options.filter(item => item.title.toLocaleLowerCase().includes(query));
    selected = Math.min(selected, Math.max(0, matches.length - 1));
    // Keep tracking the slash token so Backspace can reveal matches again, but
    // do not reserve space or show an empty dropdown when nothing matches.
    if (!matches.length) {
      hideMenu();
      return;
    }
    if (!menu) {
      menu = doc.createElement("div");
      menu.id = menuId;
      menu.setAttribute("role", "listbox");
      menu.setAttribute("aria-label", "快捷输入");
      // Keep the textarea selection and focus when a mouse or touch picks a row.
      menu.addEventListener("pointerdown", event => event.preventDefault());
      doc.body.appendChild(menu);
    }
    menu.replaceChildren();
    matches.forEach((item, index) => {
      const row = doc.createElement("div");
      row.id = `${menuId}-${index}`;
      row.setAttribute("role", "option");
      const title = doc.createElement("span");
      title.textContent = item.label || item.title;
      row.appendChild(title);
      if (config.showSpace && item.space) {
        const badge = doc.createElement("small");
        badge.textContent = item.space;
        row.appendChild(badge);
      }
      row.addEventListener("pointermove", () => { selected = index; highlight(); });
      row.addEventListener("click", () => choose(index));
      menu.appendChild(row);
    });
    input.setAttribute("aria-controls", menuId);
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-autocomplete", "list");
    position();
    highlight();
  }

  function isOperation(item) {
    const label = String(item?.label || item?.title || "").trim();
    return item?.kind === "command" || /(?:（操作）|\(操作\))$/u.test(label);
  }

  function submitOperation(input, expectedValue, attempts = 0) {
    if (!input.isConnected || input.value !== expectedValue) return;
    const button = doc.querySelector(
      ".st-key-page2_chat_input [data-testid='stChatInputSubmitButton']"
    );
    if (button && !button.disabled && button.getAttribute("aria-disabled") !== "true") {
      button.click();
      return;
    }
    // Give React time to enable the submit button after receiving the input event.
    if (attempts < 4) {
      host.requestAnimationFrame(() => submitOperation(input, expectedValue, attempts + 1));
    }
  }

  function choose(index) {
    if (composing || !active) return;
    const item = matches[index];
    if (!item) return;
    const { input, start, end } = active;
    const operation = isOperation(item);
    const next = operation
      ? item.content
      : input.value.slice(0, start) + item.content + input.value.slice(end);
    const caret = operation ? next.length : start + item.content.length;
    close();
    inserting = true;
    // Use the native setter so Streamlit's React-controlled draft receives the change.
    const setter = Object.getOwnPropertyDescriptor(host.HTMLTextAreaElement.prototype, "value").set;
    setter.call(input, next);
    input.focus();
    input.setSelectionRange(caret, caret);
    input.dispatchEvent(new host.Event("input", { bubbles: true }));
    inserting = false;
    host.requestAnimationFrame(() => {
      if (input.isConnected && input.value === next && doc.activeElement === input)
        input.setSelectionRange(caret, caret);
      if (operation) submitOperation(input, next);
    });
  }

  function onBeforeInput(event) {
    if (!inputTarget(event) || inserting) return;
    pendingInput = { input: event.target, start: event.target.selectionStart,
      end: event.target.selectionEnd, value: event.target.value, type: event.inputType };
    if (["insertFromPaste", "insertFromDrop"].includes(event.inputType)) close();
  }

  function onInput(event) {
    if (!inputTarget(event) || inserting) return;
    const before = pendingInput;
    pendingInput = null;
    if (composing || event.isComposing) return;
    // Some IMEs emit selectionchange after compositionend but before this final
    // input event. Once the committed value arrives, refresh() can safely adopt
    // the new caret position and normal selection tracking can resume.
    if (compositionSettling) {
      compositionSettling = false;
      compositionRevision += 1;
    }
    const input = event.target;
    if (event.inputType === "insertText" && /^[\/／]$/.test(event.data || "") &&
        before?.input === input && before.type === "insertText") {
      close();
      selected = 0;
      active = { input, start: before.start, end: input.selectionStart,
        prefix: before.value.slice(0, before.start), suffix: before.value.slice(before.end) };
      refresh();
    } else if (active) {
      if (["insertFromPaste", "insertFromDrop", "historyUndo", "historyRedo"].includes(event.inputType)) close();
      else { selected = 0; refresh(); }
    }
  }

  function onCompositionStart(event) {
    if (!inputTarget(event)) return;
    compositionSettling = false;
    compositionRevision += 1;
    composing = true;
    compositionBefore = { input: event.target, start: event.target.selectionStart,
      end: event.target.selectionEnd, value: event.target.value };
  }
  function onCompositionEnd(event) {
    if (!inputTarget(event)) return;
    composing = false;
    compositionSettling = true;
    const revision = ++compositionRevision;
    const before = compositionBefore;
    compositionBefore = null;
    if (/^[\/／]$/.test(event.data || "") && before) {
      close();
      selected = 0;
      active = { input: before.input, start: before.start, end: before.input.selectionStart,
        prefix: before.value.slice(0, before.start), suffix: before.value.slice(before.end) };
    }
    // Chinese search becomes final after the browser's composition input event.
    host.setTimeout(() => {
      if (revision !== compositionRevision) return;
      compositionSettling = false;
      if (active) refresh();
    }, 0);
  }
  function onPointerDown(event) {
    if (active && !menu?.contains(event.target)) close();
  }
  function onFocus(event) {
    if (active && event.target !== active.input && !menu?.contains(event.target)) close();
  }
  function onSelection() {
    if (active && !composing && !compositionSettling &&
        (active.input.selectionStart !== active.end ||
        active.input.selectionEnd !== active.end)) close();
  }
  function handleKey(event) {
    if (!active || event.target !== active.input || composing || event.isComposing || event.keyCode === 229)
      return false;
    if (!["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key) ||
        event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return false;
    if (event.key !== "Escape" && (!menu || !matches.length)) return false;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.key === "Escape") close();
    else if (event.key === "Enter") choose(selected);
    else if (matches.length) {
      selected = (selected + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length;
      highlight();
    }
    return true;
  }

  const handlers = { beforeinput: onBeforeInput, input: onInput, compositionstart: onCompositionStart,
    compositionend: onCompositionEnd, pointerdown: onPointerDown, focusin: onFocus,
    selectionchange: onSelection, scroll: position };
  Object.entries(handlers).forEach(([name, fn]) => listen(name, fn));
  host.addEventListener("resize", position);
  host.visualViewport?.addEventListener("resize", position);
  host.visualViewport?.addEventListener("scroll", position);
  const observer = new host.MutationObserver(() => {
    if (active && !active.input.isConnected) close();
    // Navigation/logout removes the composer; discard candidate contents and handlers.
    if (!doc.querySelector(selector)) destroy();
  });
  observer.observe(doc.body, { childList: true, subtree: true });
  function destroy() {
    close();
    config = { options: [], scope: "", showSpace: false };
    Object.entries(handlers).forEach(([name, fn]) => doc.removeEventListener(name, fn, true));
    host.removeEventListener("resize", position);
    host.visualViewport?.removeEventListener("resize", position);
    host.visualViewport?.removeEventListener("scroll", position);
    observer.disconnect();
    delete host.__dndQuickInput;
  }
  host.__dndQuickInput = {
    handleKey,
    destroy,
    snapshot: () => ({ config, active, selected, composing }),
    update(next) {
      if (next.scope !== config.scope) close();
      config = next;
      if (active) refresh();
    },
  };
})();
