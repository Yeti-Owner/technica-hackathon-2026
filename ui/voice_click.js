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
})();
