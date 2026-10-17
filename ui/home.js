/* ── Bookmarks ──────────────────────────────────────── */
for (const bookmark of window.BOOKMARKS || []) {
  const link = document.createElement('a');
  link.href = bookmark.url;
  const title = document.createElement('strong');
  title.textContent = bookmark.title;
  const description = document.createElement('span');
  description.textContent = bookmark.description;
  link.append(title, description);
  document.querySelector('#bookmarks').append(link);
}

/* ── Theme toggle ──────────────────────────────────── */
document.querySelector('#theme').addEventListener('click', event => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  event.currentTarget.setAttribute('aria-pressed', String(dark));
  event.currentTarget.textContent = dark ? 'Light theme' : 'Dark theme';
});

/* ── Scroll reveal ─────────────────────────────────── */
(function initReveal() {
  const revealEls = document.querySelectorAll('.reveal');
  if (!revealEls.length) return;

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) {
    revealEls.forEach(el => el.classList.add('visible'));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });

  revealEls.forEach(el => observer.observe(el));
})();

/* ── Generate stars for bottom section ─────────────── */
(function initStars() {
  const container = document.getElementById('stars-container');
  if (!container) return;

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const count = 40;

  for (let i = 0; i < count; i++) {
    const star = document.createElement('div');
    star.className = 'star';
    star.style.left = Math.random() * 100 + '%';
    star.style.top = Math.random() * 100 + '%';
    star.style.width = (Math.random() * 2 + 1.5) + 'px';
    star.style.height = star.style.width;
    if (!prefersReduced) {
      star.style.animationDelay = (Math.random() * 5) + 's';
      star.style.animationDuration = (Math.random() * 3 + 2) + 's';
    } else {
      star.style.opacity = (Math.random() * 0.4 + 0.1).toString();
    }
    container.appendChild(star);
  }
})();
