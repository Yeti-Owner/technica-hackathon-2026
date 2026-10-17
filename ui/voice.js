// Runs in every document; audio stays exclusively in the Python process.
(() => {
  if (window !== window.top) return;
  let hovered = null;
  let badge;
  let desiredSpeed = 0, speed = 0, lastFrame = 0, heartbeat = 0, animation = 0;
  const active = () => document.visibilityState === 'visible' && document.hasFocus();
  function stop() {
    desiredSpeed = speed = lastFrame = 0;
    cancelAnimationFrame(animation);
    animation = 0;
  }
  function animate(now) {
    animation = 0;
    // No queued catch-up: stop if the controller disconnects or focus is lost.
    if (!active() || now-heartbeat > 250 || !desiredSpeed) { stop(); return; }
    const dt = lastFrame ? Math.min((now-lastFrame)/1000, .04) : 1/60;
    lastFrame = now;
    speed += (desiredSpeed-speed)*(1-Math.exp(-dt/.06));
    scroll(speed*dt);
    animation = requestAnimationFrame(animate);
  }
  addEventListener('blur', stop);
  document.addEventListener('visibilitychange', () => { if (!active()) stop(); });
  document.addEventListener('pointermove', event => { hovered = event.target; }, {passive: true});
  window.__voiceTick = ({speed: nextSpeed, message}) => {
    if (!document.documentElement) return false;
    if (!badge || !badge.isConnected) {
      badge = document.createElement('div');
      badge.id = 'browser-voice-status';
      badge.style.cssText = 'all:initial;position:fixed;bottom:14px;right:14px;z-index:2147483647;pointer-events:none;';
      const shadow = badge.attachShadow({mode: 'open'});
      const label = document.createElement('span');
      label.style.cssText = 'display:block;background:#152438;color:#fff;border:1px solid #62748b;border-radius:18px;padding:9px 14px;font:13px system-ui;box-shadow:0 2px 8px #0004';
      shadow.append(label);
      document.documentElement.append(badge);
    }
    badge.shadowRoot.firstChild.textContent = message;
    const focused = active();
    if (!focused || !nextSpeed) { stop(); return focused; }
    desiredSpeed = nextSpeed;
    heartbeat = performance.now();
    if (!animation) animation = requestAnimationFrame(animate);
    return focused;
  };
  function scroll(pixels) {
    let element = hovered?.isConnected ? hovered : document.activeElement;
    while (element && element !== document.documentElement) {
      const style = getComputedStyle(element);
      const canScroll = /(auto|scroll)/.test(style.overflowY) && element.scrollHeight > element.clientHeight;
      const hasRoom = pixels > 0 ? element.scrollTop + element.clientHeight < element.scrollHeight - 1 : element.scrollTop > 0;
      if (canScroll && hasRoom) {
        element.scrollBy({top: pixels, behavior: 'instant'});
        return;
      }
      element = element.parentElement;
    }
    window.scrollBy({top: pixels, behavior: 'instant'});
  }
})();
