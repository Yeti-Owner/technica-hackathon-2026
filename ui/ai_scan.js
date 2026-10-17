// Passive AI-image marker. Dormant until Python calls __aiConfigure.
(() => {
  if (window.__aiConfigure) return;
  let active = false;
  let configured = false;
  let attempts = 0;
  let flushAttempts = 0;
  let cfg = {minSize: 80, prefetchScreens: 1.5};
  let order = 0;
  const records = new WeakMap();
  const images = new Set();
  let intersection = null;
  let mutation = null;
  let flushTimer = 0;
  let retryTimer = 0;
  let sweepTimer = 0;
  let raf = 0;
  let pending = [];
  const frameId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + '-' + Math.random().toString(16).slice(2);

  function absolute(img) {
    const value = img.currentSrc || img.src || '';
    if (!value) return '';
    try { return new URL(value, location.href).href; } catch (error) { return value; }
  }

  function box(img) {
    const rect = img.getBoundingClientRect();
    return {w: rect.width, h: rect.height, rect};
  }

  function qualifies(img) {
    const {w, h, rect} = box(img);
    if (w < cfg.minSize || h < cfg.minSize) return false;
    const margin = Math.ceil(cfg.prefetchScreens * innerHeight);
    return rect.bottom > 0 && rect.top < innerHeight + margin && rect.right > 0 && rect.left < innerWidth;
  }

  function record(img) {
    let item = records.get(img);
    if (!item) {
      item = {order: order++, src: '', reported: '', banner: null, timer: 0,
              outline: img.style.outline || '', offset: img.style.outlineOffset || ''};
      records.set(img, item);
      images.add(img);
      if (intersection) intersection.observe(img);
    }
    return item;
  }

  function cleanup(img) {
    const item = records.get(img);
    if (!item) return;
    if (item.banner) item.banner.remove();
    if (item.timer) clearInterval(item.timer);
    img.style.outline = item.outline;
    img.style.outlineOffset = item.offset;
    img.classList.remove('ai-mark-outline');
    delete img.dataset.aiVerdict;
    item.banner = null;
    item.timer = 0;
    item.reported = '';
  }

  function forget(img) {
    cleanup(img);
    if (intersection) intersection.unobserve(img);
    records.delete(img);
    images.delete(img);
  }

  function maybeReport(img) {
    if (!active || !img.isConnected) return;
    const src = absolute(img);
    if (!src) return;
    const item = record(img);
    if (item.src !== src) {
      cleanup(img);
      item.src = src;
    }
    if (item.reported === src) return;
    if (!qualifies(img)) return;
    const {w, h} = box(img);
    item.reported = src;
    pending.push({url: src, w: Math.round(w), h: Math.round(h), order: item.order});
    scheduleFlush();
  }

  function scheduleFlush() {
    if (flushTimer) return;
    flushTimer = setTimeout(flush, 300);
  }

  function flush() {
    flushTimer = 0;
    if (!active || !pending.length) return;
    const report = window.pyAIReport;
    if (typeof report !== 'function') { if (!retryTimer) retryTimer = setTimeout(() => { retryTimer = 0; flush(); }, 250); return; }
    try {
      report(JSON.stringify({type: 'candidates', frameId, frameUrl: location.href, items: JSON.parse(JSON.stringify(pending))}));
      pending = [];
      attempts = 0;
      flushAttempts = 0;
    } catch (error) {
      flushAttempts += 1;
      if (!retryTimer) retryTimer = setTimeout(() => { retryTimer = 0; flush(); }, flushAttempts > 40 ? 2000 : 250);
    }
  }

  function sweep() {
    if (!active) return;
    for (const img of Array.from(images)) {
      if (!img.isConnected) { forget(img); continue; }
      maybeReport(img);
    }
  }

  function scan(root) {
    if (!active) return;
    const scope = root && root.querySelectorAll ? root : document;
    if (scope.nodeType === 1 && scope.tagName === 'IMG') maybeReport(scope);
    for (const img of scope.querySelectorAll('img')) maybeReport(img);
  }

  function rebuild() {
    if (intersection) intersection.disconnect();
    intersection = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) maybeReport(entry.target);
    }, {rootMargin: `0px 0px ${Math.ceil(cfg.prefetchScreens * innerHeight)}px 0px`});
    for (const img of images) intersection.observe(img);
  }

  function positionAll() {
    for (const img of Array.from(images)) {
      const item = records.get(img);
      if (!item || !item.banner) continue;
      if (!img.isConnected) { forget(img); continue; }
      const rect = img.getBoundingClientRect();
      item.banner.style.left = `${rect.left + scrollX}px`;
      item.banner.style.top = `${rect.top + scrollY}px`;
    }
  }

  function install() {
    if (!document.documentElement) return;
    if (!mutation) {
      mutation = new MutationObserver(entries => {
        for (const entry of entries) {
          if (entry.type === 'attributes' && entry.target.tagName === 'IMG') {
            const img = entry.target;
            const item = records.get(img);
            if (item && item.src !== absolute(img)) cleanup(img);
            maybeReport(img);
          } else {
            for (const node of entry.addedNodes) if (node.nodeType === 1) scan(node);
            for (const node of entry.removedNodes) {
              if (node.nodeType !== 1) continue;
              if (node.tagName === 'IMG') forget(node);
              else if (node.querySelectorAll) for (const img of node.querySelectorAll('img')) forget(img);
            }
          }
        }
        sweep();
      });
      mutation.observe(document.documentElement, {
        childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'srcset']
      });
    }
    if (!intersection) rebuild();
    scan(document);
  }

  // REDESIGN POINT: replace __aiMark and ai_mark.css to completely change how AI images are marked.
  function __aiMark(img, score) {
    const item = record(img);
    if (item.banner) item.banner.remove();
    if (item.timer) clearInterval(item.timer);
    img.style.outline = '6px solid #ff1744';
    img.style.outlineOffset = '-1px';
    img.classList.add('ai-mark-outline');
    img.dataset.aiVerdict = String(score);
    const banner = document.createElement('div');
    banner.className = 'ai-mark-banner';
    banner.textContent = `AI GENERATED · ${score}`;
    banner.style.position = 'absolute';
    banner.style.background = '#ff1744';
    banner.style.color = '#fff';
    banner.style.fontWeight = '700';
    banner.style.textTransform = 'uppercase';
    banner.style.fontSize = '18px';
    banner.style.padding = '6px 10px';
    banner.style.zIndex = '2147483000';
    banner.style.pointerEvents = 'none';
    document.documentElement.append(banner);
    item.banner = banner;
    const place = () => {
      if (!banner.isConnected || !img.isConnected) return;
      const rect = img.getBoundingClientRect();
      banner.style.left = `${rect.left + scrollX}px`;
      banner.style.top = `${rect.top + scrollY}px`;
    };
    place();
    item.timer = setInterval(place, 1000);
    positionAll();
  }

  window.__aiConfigure = next => {
    configured = true;
    if (!next || next.enabled === false) {
      active = false;
      for (const img of Array.from(images)) forget(img);
      pending = [];
      if (mutation) { mutation.disconnect(); mutation = null; }
      if (intersection) { intersection.disconnect(); intersection = null; }
      return;
    }
    cfg = {
      minSize: Number(next.minSize) || 80,
      prefetchScreens: Number(next.prefetchScreens) || 0
    };
    active = true;
    install();
    rebuild();
    sweep();
  };

  window.__aiApply = (url, score) => {
    if (!active) return;
    for (const img of Array.from(images)) {
      if (absolute(img) === url) __aiMark(img, score);
    }
  };

  addEventListener('scroll', () => {
    if (!active) return;
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; positionAll(); });
    if (!sweepTimer) sweepTimer = setTimeout(() => { sweepTimer = 0; sweep(); }, 250);
  }, {passive: true});

  addEventListener('resize', () => {
    if (!active) return;
    rebuild();
    if (!sweepTimer) sweepTimer = setTimeout(() => { sweepTimer = 0; sweep(); }, 250);
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; positionAll(); });
  }, {passive: true});

  function askConfig() {
    if (active || configured || attempts >= 40) return;
    attempts += 1;
    const report = window.pyAIReport;
    if (typeof report === 'function') {
      try {
        report(JSON.stringify({type: 'needConfig', frameId, frameUrl: location.href}));
        return;
      } catch (error) {}
    }
    setTimeout(askConfig, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { if (active) install(); }, {once: true});
  }
  setTimeout(askConfig, 250);
})();
