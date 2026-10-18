// Focus identity only: no audio, page controls, or visual overlay.
(() => {
  const documentId = `${Date.now()}-${Math.random()}`;
  let revision = 0;
  let nextId = 0;
  const ids = new WeakMap();
  const changed = () => { revision++; };
  document.addEventListener('focusin', changed, true);
  document.addEventListener('focusout', changed, true);
  addEventListener('blur', changed);
  window.__voiceFocus = () => {
    let element = document.activeElement;
    while (element?.shadowRoot?.activeElement) element = element.shadowRoot.activeElement;
    const usable = element && !['BODY', 'HTML', 'IFRAME', 'FRAME'].includes(element.tagName)
      && !element.matches(':disabled, [aria-disabled="true"]');
    if (element && !ids.has(element)) ids.set(element, ++nextId);
    return {key: `${documentId}:${revision}:${ids.get(element) || 0}`, active: document.hasFocus(), element: usable ? element : null};
  };

  // Keyboard backup: ; = left click, ' = right click on focused element.
  document.addEventListener('keydown', e => {
    if (e.key !== ';' && e.key !== "'") return;
    const el = document.activeElement;
    if (!el || ['BODY', 'HTML', 'IFRAME', 'FRAME'].includes(el.tagName)) return;
    // Don't intercept when typing in a text field.
    if (el.matches('input, textarea, select, [contenteditable="true"]')) return;
    e.preventDefault();
    if (e.key === ';') {
      el.click();
    } else {
      el.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true}));
    }
  }, true);
})();
