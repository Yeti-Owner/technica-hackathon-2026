// Page-side code only. No Python bindings are exposed to untrusted websites.
(() => {
  function install() {
    if (document.getElementById('browser-starter-style')) return;
    const style = document.createElement('style');
    style.id = 'browser-starter-style';
    style.textContent = window.__starterCSS;
    (document.head || document.documentElement).append(style);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once: true});
  else install();
})();
