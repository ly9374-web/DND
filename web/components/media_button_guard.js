<script>
(() => {
  const host = window.parent, doc = host.document;
  if (!host.__dndMediaButtonGuard) {
    const pending = new Map();
    let renderedTokens = '';
    const root = '.st-key-page2_media_controls';
    const container = '[data-testid="stButton"], [data-testid="stDownloadButton"]';
    const identity = button => {
      const wrapper = button.closest(container);
      if (!wrapper) return null;
      const keyed = wrapper.closest('[class*="st-key-"]');
      const key = keyed && keyed.matches(root) ? null :
        keyed && Array.from(keyed.classList).find(name => name.startsWith('st-key-'));
      const mediaDialog = key && /^(st-key-(confirm_delete|cancel_delete)_page2_|st-key-page2_(save_video_story|url_rename_save))/.test(key);
      if (!wrapper.closest(root) && !mediaDialog) return null;
      return key || 'media:' + button.textContent.trim();
    };
    const paint = () => {
      for (const button of doc.querySelectorAll('[data-testid="stButton"] button, [data-testid="stDownloadButton"] button')) {
        const key = identity(button);
        if (!key) continue;
        const entry = pending.get(key);
        if (entry) {
          button.setAttribute('data-media-click-locked', 'true');
          button.setAttribute('aria-disabled', 'true');
        } else if (button.hasAttribute('data-media-click-locked')) {
          button.removeAttribute('data-media-click-locked');
          button.removeAttribute('aria-disabled');
        }
      }
    };
    const release = () => {
      const tokens = [...doc.querySelectorAll('[data-media-render-ack]')]
        .map(node => node.getAttribute('data-media-render-ack')).join(':');
      if (tokens && tokens !== renderedTokens) {
        renderedTokens = tokens;
        for (const entry of pending.values()) entry.ack = true;
      }
      const now = Date.now();
      for (const [key, entry] of pending) {
        if ((entry.ack || entry.download) && now >= entry.until) pending.delete(key);
      }
      paint();
    };
    doc.addEventListener('click', event => {
      const button = event.target.closest?.('button');
      if (!button) return;
      const key = identity(button);
      if (!key || button.disabled) return;
      release();
      if (pending.has(key)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      const download = !!button.closest('[data-testid="stDownloadButton"]');
      const delay = download ? 1200 : 600;
      pending.set(key, {ack: false, download, until: Date.now() + delay});
      // Leave the first event intact so React can submit it, then display the lock.
      host.queueMicrotask(paint);
      host.setTimeout(release, delay);
    }, true);
    const style = doc.createElement('style');
    style.textContent = 'button[data-media-click-locked] {opacity:.5;cursor:wait;}';
    doc.head.appendChild(style);
    const observer = new host.MutationObserver(release);
    observer.observe(doc.body, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['data-media-render-ack'],
    });
    host.__dndMediaButtonGuard = {observer};
    release();
  }
})();
</script>
