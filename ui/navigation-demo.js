document.addEventListener('DOMContentLoaded', () => {
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const status = document.getElementById('demo-status');
  const themeButton = document.getElementById('theme');
  const clickButton = document.getElementById('click-demo');
  const openLink = document.getElementById('open-demo');
  const rightButton = document.getElementById('right-demo');
  const surprise = document.getElementById('demo-surprise');
  let clicks = 0;
  let rightClicks = 0;

  function syncTheme() {
    const dark = document.documentElement.dataset.theme === 'dark';
    themeButton.setAttribute('aria-pressed', String(dark));
    themeButton.querySelector('.nav-cloud-text').textContent = dark ? 'Light theme' : 'Dark theme';
  }
  syncTheme();
  themeButton.addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('nod-theme', theme); } catch (_) {}
    syncTheme();
  });

  function celebrate(cardId) {
    const card = document.getElementById(cardId);
    card.classList.add('is-complete');
    const art = card.querySelector('.demo-art span');
    art.getAnimations().forEach(animation => animation.cancel());
    if (!reducedMotion.matches) {
      art.animate([
        { transform: 'translateY(0) rotate(0) scale(1)' },
        { transform: 'translateY(-12px) rotate(-10deg) scale(1.2)', offset: .45 },
        { transform: 'translateY(0) rotate(0) scale(1)' },
      ], { duration: 650, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
  }
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) document.querySelectorAll('.demo-art span').forEach(art => {
      art.getAnimations().forEach(animation => animation.cancel());
    });
  });

  clickButton.addEventListener('click', () => {
    clicks++;
    document.getElementById('click-result').textContent = clicks === 1 ? 'You did it! One click.' : `You did it! ${clicks} clicks.`;
    status.textContent = `Button activated ${clicks} ${clicks === 1 ? 'time' : 'times'}. Your focus stays here.`;
    celebrate('click-card');
  });
  openLink.addEventListener('click', event => {
    event.preventDefault();
    const opening = surprise.hidden;
    surprise.hidden = !opening;
    openLink.setAttribute('aria-expanded', String(opening));
    status.textContent = opening ? 'Surprise opened. You found a little sunshine!' : 'Surprise closed. Open it again whenever you like.';
    if (opening) celebrate('open-card');
  });
  function rightClick(event) {
    event.preventDefault();
    rightClicks++;
    document.getElementById('right-result').textContent = `Right click received! ${rightClicks === 1 ? 'A new cloud says hello.' : `${rightClicks} and counting.`}`;
    status.textContent = `Right click ${rightClicks} worked. No menu or new page opened.`;
    celebrate('right-card');
  }
  rightButton.addEventListener('contextmenu', rightClick);
  rightButton.addEventListener('keydown', event => {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) rightClick(event);
  });
  rightButton.addEventListener('click', () => {
    status.textContent = 'That was a left click. Try a right click, or press Shift + F10.';
  });
  document.getElementById('reset-demo').addEventListener('click', () => {
    clicks = rightClicks = 0;
    document.querySelectorAll('.demo-card').forEach(card => {
      card.classList.remove('is-complete');
      card.querySelector('.demo-art span').getAnimations().forEach(animation => animation.cancel());
    });
    document.getElementById('click-result').textContent = 'Ready when you are.';
    document.getElementById('right-result').textContent = 'Something extra is waiting.';
    surprise.hidden = true;
    openLink.setAttribute('aria-expanded', 'false');
    status.textContent = 'All reset. Try the controls again.';
  });
});
