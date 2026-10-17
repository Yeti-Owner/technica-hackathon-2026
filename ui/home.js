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
document.querySelector('#theme').addEventListener('click', event => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  event.currentTarget.setAttribute('aria-pressed', String(dark));
  event.currentTarget.textContent = dark ? 'Light theme' : 'Dark theme';
});
