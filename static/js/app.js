/* app.js — Client-side interactivity */

// ── Auto-build TOC from post headings ────────────
(function buildTOC() {
  const nav = document.getElementById('toc-nav');
  if (!nav) return;
  const content = document.getElementById('post-content');
  if (!content) return;
  const headings = content.querySelectorAll('h2, h3');
  if (headings.length === 0) {
    const sidebar = document.getElementById('toc-sidebar');
    if (sidebar) sidebar.style.display = 'none';
    return;
  }
  headings.forEach((h, i) => {
    if (!h.id) h.id = 'heading-' + i;
    const a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    a.dataset.level = h.tagName[1];
    nav.appendChild(a);
  });

  // Highlight active section on scroll
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      const id = e.target.id;
      const link = nav.querySelector(`a[href="#${id}"]`);
      if (link) link.classList.toggle('active', e.isIntersecting);
    });
  }, { rootMargin: '0px 0px -70% 0px' });
  headings.forEach(h => obs.observe(h));
})();

// ── Animate SEO bars on load ──────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.seo-fill').forEach(bar => {
    const w = bar.style.width;
    bar.style.width = '0%';
    setTimeout(() => { bar.style.width = w; }, 200);
  });
});
