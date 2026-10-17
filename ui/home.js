/* ===================================================================
   Nod — Interactive Experience & Motion Controller
   Libraries: Lenis Smooth Scroll + GSAP 3 + ScrollTrigger
   Accessibility: WCAG AA, full keyboard navigation, reduced motion
   =================================================================== */

/* ── 1. Bookmarks Population (Playwright Compatible) ─────────────── */
function initBookmarks() {
  const container = document.querySelector('#bookmarks');
  if (!container) return;

  const bookmarks = window.BOOKMARKS || [];
  for (const item of bookmarks) {
    const link = document.createElement('a');
    link.href = item.url;
    link.setAttribute('tabindex', '0');

    const title = document.createElement('strong');
    title.textContent = item.title;

    const desc = document.createElement('span');
    desc.textContent = item.description;

    link.append(title, desc);
    container.append(link);
  }
}

/* ── 2. Theme Toggle (Light / Dark Mode) ─────────────────────────── */
function initThemeToggle() {
  const themeBtn = document.querySelector('#theme');
  if (!themeBtn) return;

  themeBtn.addEventListener('click', (event) => {
    const isDark = document.documentElement.dataset.theme !== 'dark';
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
    event.currentTarget.setAttribute('aria-pressed', String(isDark));
    event.currentTarget.textContent = isDark ? 'Light theme' : 'Dark theme';
    
    // Refresh ScrollTrigger to accommodate any slight theme shifts
    if (window.ScrollTrigger) {
      ScrollTrigger.refresh();
    }
  });
}

/* ── 3. Night Sky Stars & Sparkles Generator ─────────────────────── */
function initNightStars() {
  const container = document.getElementById('night-stars-container');
  if (!container) return;

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const count = 75;

  for (let i = 0; i < count; i++) {
    const star = document.createElement('div');
    star.className = 'star';
    star.style.left = `${(Math.random() * 100).toFixed(2)}%`;
    star.style.top = `${(Math.random() * 100).toFixed(2)}%`;

    const size = (Math.random() * 2.8 + 1).toFixed(1);
    star.style.width = `${size}px`;
    star.style.height = `${size}px`;

    if (!prefersReduced) {
      star.style.animationDelay = `${(Math.random() * 5).toFixed(2)}s`;
      star.style.animationDuration = `${(Math.random() * 3 + 2).toFixed(2)}s`;
    } else {
      star.style.opacity = (Math.random() * 0.6 + 0.2).toFixed(2);
    }

    container.appendChild(star);
  }
}

/* ── 4. Motion Design & Scroll-Driven Experiences ────────────────── */
function initAnimations() {
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) return;

  // Verify GSAP & ScrollTrigger are available
  if (typeof gsap === 'undefined' || typeof ScrollTrigger === 'undefined') {
    console.warn('GSAP/ScrollTrigger not loaded; falling back to static presentation.');
    return;
  }

  gsap.registerPlugin(ScrollTrigger);

  // Initialize Lenis Smooth Scroll if available
  let lenis = null;
  if (typeof Lenis !== 'undefined') {
    lenis = new Lenis({
      duration: 1.15,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      orientation: 'vertical',
      gestureOrientation: 'vertical',
      smoothWheel: true,
      wheelMultiplier: 0.95,
      touchMultiplier: 1.5,
    });

    lenis.on('scroll', ScrollTrigger.update);
    window.lenis = lenis;

    gsap.ticker.add((time) => {
      lenis.raf(time * 1000);
    });

    gsap.ticker.lagSmoothing(0);
  }

  // ── A. Hero Load Entrance Animation ─────────────────────────────
  const heroWords = document.querySelectorAll('.hero-title .word');
  
  if (heroWords.length > 0) {
    gsap.from(heroWords, {
      y: 25,
      opacity: 0.2,
      duration: 0.6,
      stagger: 0.08,
      ease: 'power2.out',
      clearProps: 'all'
    });
  }

  gsap.from('.hero-cloud-back', {
    scale: 0.9,
    y: 20,
    duration: 1.0,
    stagger: 0.1,
    ease: 'power2.out',
    clearProps: 'transform'
  });

  gsap.from('.hero-cloud-front', {
    scale: 0.85,
    y: 25,
    duration: 0.9,
    stagger: 0.1,
    ease: 'power2.out',
    clearProps: 'transform'
  });


  // ── B. Desktop Mouse-Move Parallax for Hero Clouds ───────────────
  if (window.innerWidth > 800) {
    const heroSection = document.querySelector('.hero-section');
    const backClouds = document.querySelectorAll('.hero-cloud-back');
    const frontClouds = document.querySelectorAll('.hero-cloud-front');

    heroSection.addEventListener('mousemove', (e) => {
      const rect = heroSection.getBoundingClientRect();
      const xNorm = (e.clientX - rect.left) / rect.width - 0.5;
      const yNorm = (e.clientY - rect.top) / rect.height - 0.5;

      gsap.to(backClouds, {
        x: xNorm * 35,
        y: yNorm * 25,
        duration: 1.2,
        ease: 'power1.out',
        overwrite: 'auto'
      });

      gsap.to(frontClouds, {
        x: xNorm * 55,
        y: yNorm * 40,
        duration: 0.9,
        ease: 'power1.out',
        overwrite: 'auto'
      });
    });

    heroSection.addEventListener('mouseleave', () => {
      gsap.to([...backClouds, ...frontClouds], {
        x: 0,
        y: 0,
        duration: 1.5,
        ease: 'power2.out'
      });
    });
  }

  // ── C. Scroll Parallax on Ambient Sky Clouds ─────────────────────
  document.querySelectorAll('.sky-cloud').forEach((cloud) => {
    const speed = parseFloat(cloud.dataset.speed) || 0.3;
    gsap.to(cloud, {
      y: () => window.innerHeight * speed * 1.2,
      ease: 'none',
      scrollTrigger: {
        trigger: 'body',
        start: 'top top',
        end: 'bottom bottom',
        scrub: true
      }
    });
  });

  // ── D. Big Cloud Transition (Center Statement) ───────────────────
  const cloudTransSection = document.querySelector('.cloud-transition-section');
  if (cloudTransSection) {
    const curtainLeft = cloudTransSection.querySelector('.curtain-left');
    const curtainRight = cloudTransSection.querySelector('.curtain-right');
    const statementCard = cloudTransSection.querySelector('.cloud-statement-inner');

    const cloudTl = gsap.timeline({
      scrollTrigger: {
        trigger: cloudTransSection,
        start: 'top 85%',
        end: 'bottom 15%',
        scrub: 1.2
      }
    });

    // Clouds sweep inward to embrace center statement, then part away smoothly
    cloudTl
      .fromTo(curtainLeft,
        { xPercent: -50, scale: 0.9 },
        { xPercent: 5, scale: 1.08, duration: 1.2 }
      )
      .fromTo(curtainRight,
        { xPercent: 50, scale: 0.9 },
        { xPercent: -5, scale: 1.08, duration: 1.2 },
        0
      )
      .fromTo(statementCard,
        { opacity: 0.3, scale: 0.92, y: 40 },
        { opacity: 1, scale: 1, y: 0, duration: 1.2 },
        0
      )
      // Parting away phase
      .to(curtainLeft, { xPercent: -45, scale: 1.15, duration: 1.2 })
      .to(curtainRight, { xPercent: 45, scale: 1.15, duration: 1.2 }, '-=1.2')
      .to(statementCard, { opacity: 0.9, y: -20, duration: 1.2 }, '-=1.2');
  }

  // ── E. Horizontal Pinned Scroll Section ─────────────────────────
  const pinSection = document.querySelector('.horizontal-pin-section');
  const track = document.getElementById('features-track');
  const progressBar = document.getElementById('horizontal-progress');

  if (pinSection && track && window.innerWidth > 768) {
    const getScrollDistance = () => track.scrollWidth - window.innerWidth + (window.innerWidth * 0.1);

    const horizontalScrollTrigger = ScrollTrigger.create({
      trigger: pinSection,
      pin: true,
      start: 'top top',
      end: () => `+=${getScrollDistance()}`,
      scrub: 1,
      invalidateOnRefresh: true,
      onUpdate: (self) => {
        if (progressBar) {
          progressBar.style.width = `${(self.progress * 100).toFixed(1)}%`;
        }
      }
    });

    gsap.to(track, {
      x: () => -getScrollDistance(),
      ease: 'none',
      scrollTrigger: {
        trigger: pinSection,
        start: 'top top',
        end: () => `+=${getScrollDistance()}`,
        scrub: 1,
        invalidateOnRefresh: true
      }
    });

    // Keyboard accessibility inside pinned horizontal track
    track.querySelectorAll('.feature-card').forEach((card) => {
      card.addEventListener('focusin', () => {
        const cardRect = card.getBoundingClientRect();
        if (cardRect.left < 50 || cardRect.right > window.innerWidth - 50) {
          card.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
      });
    });
  }

  // ── F. Night Sky Crescent Moon & Team Entrance ───────────────────
  const moon = document.getElementById('night-moon');
  if (moon) {
    gsap.fromTo(moon,
      { y: 60, rotation: -20 },
      {
        y: -50,
        rotation: -5,
        ease: 'none',
        scrollTrigger: {
          trigger: '.night-sky-section',
          start: 'top 80%',
          end: 'bottom bottom',
          scrub: 1.5
        }
      }
    );
  }

  // Founders cards stagger reveal
  gsap.fromTo('.founder-card',
    { opacity: 0, y: 40, scale: 0.96 },
    {
      opacity: 1,
      y: 0,
      scale: 1,
      duration: 0.9,
      stagger: 0.15,
      ease: 'power2.out',
      scrollTrigger: {
        trigger: '.founders-grid',
        start: 'top 85%',
        toggleActions: 'play none none reverse'
      }
    }
  );

  // Closing Callout reveal
  gsap.fromTo('.closing-callout',
    { opacity: 0, y: 30 },
    {
      opacity: 1,
      y: 0,
      duration: 1,
      ease: 'power2.out',
      scrollTrigger: {
        trigger: '.closing-callout',
        start: 'top 85%',
        toggleActions: 'play none none reverse'
      }
    }
  );

  // ── G. Night Clouds Scroll Parallax (Multi-tier Depth) ───────────
  // Back row moves slower, mid row moves moderately, front row moves faster
  if (document.querySelector('.nc-row-back')) {
    gsap.to('.nc-row-back', {
      y: 25,
      ease: 'none',
      scrollTrigger: {
        trigger: '.night-sky-section',
        start: 'top 85%',
        end: 'bottom bottom',
        scrub: 1.8
      }
    });
  }

  if (document.querySelector('.nc-row-mid')) {
    gsap.to('.nc-row-mid', {
      y: 50,
      ease: 'none',
      scrollTrigger: {
        trigger: '.night-sky-section',
        start: 'top 85%',
        end: 'bottom bottom',
        scrub: 1.4
      }
    });
  }

  if (document.querySelector('.nc-row-front')) {
    gsap.to('.nc-row-front', {
      y: 80,
      ease: 'none',
      scrollTrigger: {
        trigger: '.night-sky-section',
        start: 'top 85%',
        end: 'bottom bottom',
        scrub: 1.0
      }
    });
  }

  // Framing corner clouds subtle scroll parallax
  if (document.querySelector('.nc-frame')) {
    gsap.to('.nc-top-left, .nc-bottom-left', {
      y: 35,
      ease: 'none',
      scrollTrigger: {
        trigger: '.night-sky-section',
        start: 'top 85%',
        end: 'bottom bottom',
        scrub: 1.5
      }
    });

    gsap.to('.nc-top-right, .nc-bottom-right', {
      y: -25,
      ease: 'none',
      scrollTrigger: {
        trigger: '.night-sky-section',
        start: 'top 85%',
        end: 'bottom bottom',
        scrub: 1.5
      }
    });
  }
}

/* ── 5. Navigation Smooth Scrolling & Active Section Highlighting ── */
function initNavScroll() {
  const navLinks = document.querySelectorAll('.header-nav a[href^="#"]');
  if (!navLinks.length) return;

  const getTargetElements = () => {
    return Array.from(navLinks).map(link => {
      const hash = link.getAttribute('href');
      const target = hash ? document.querySelector(hash) : null;
      return { link, hash, target };
    }).filter(item => item.target !== null);
  };

  const navItems = getTargetElements();

  navLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      const targetId = link.getAttribute('href');
      if (!targetId || targetId === '#') return;

      const targetEl = document.querySelector(targetId);
      if (!targetEl) return;

      event.preventDefault();

      const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const navHeader = document.querySelector('.site-header');
      const navHeight = navHeader ? navHeader.offsetHeight : 72;
      const breathingRoom = 24;
      const offset = navHeight + breathingRoom;

      // If ScrollTrigger is active, ensure layout coordinates are fresh
      if (window.ScrollTrigger) {
        ScrollTrigger.refresh();
      }

      // Calculate exact target scroll position in document coordinates
      const currentScroll = window.scrollY || window.pageYOffset || (window.lenis ? window.lenis.scroll : 0);
      const targetTop = Math.max(0, targetEl.getBoundingClientRect().top + currentScroll - offset);

      // Smooth scroll via Lenis if available
      if (window.lenis) {
        if (prefersReduced) {
          window.lenis.scrollTo(targetTop, { immediate: true });
        } else {
          window.lenis.scrollTo(targetTop, { duration: 1.15 });
        }
      } else {
        // Native fallback smooth scroll
        window.scrollTo({
          top: targetTop,
          behavior: prefersReduced ? 'instant' : 'smooth'
        });
      }

      // Update URL hash cleanly without instant browser jump
      try {
        history.pushState(null, '', targetId);
      } catch (err) {
        window.location.hash = targetId;
      }

      // Shift keyboard focus to target section for accessibility (WCAG AA)
      if (!targetEl.hasAttribute('tabindex')) {
        targetEl.setAttribute('tabindex', '-1');
      }
      targetEl.focus({ preventScroll: true });

      // Immediate visual feedback on clicked nav link
      setActiveLink(targetId);
    });
  });

  function setActiveLink(activeHash) {
    navLinks.forEach(link => {
      const href = link.getAttribute('href');
      const isActive = href === activeHash;
      link.classList.toggle('active', isActive);
      if (isActive) {
        link.setAttribute('aria-current', 'page');
      } else {
        link.removeAttribute('aria-current');
      }
    });
  }

  // Active link detection using IntersectionObserver
  if ('IntersectionObserver' in window && navItems.length > 0) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          if (id) {
            setActiveLink(`#${id}`);
          }
        }
      });
    }, {
      rootMargin: '-25% 0px -50% 0px',
      threshold: [0, 0.1, 0.25]
    });

    navItems.forEach(({ target }) => observer.observe(target));
  }

  // Scroll event fallback for continuous active link tracking
  window.addEventListener('scroll', () => {
    const scrollPosition = (window.scrollY || window.pageYOffset) + 160;
    
    for (let i = navItems.length - 1; i >= 0; i--) {
      const { hash, target } = navItems[i];
      const elTop = target.getBoundingClientRect().top + window.pageYOffset;
      if (scrollPosition >= elTop - 60) {
        setActiveLink(hash);
        break;
      }
    }
  }, { passive: true });
}

/* ── DOM Ready Initialization ────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initBookmarks();
  initThemeToggle();
  initNightStars();
  initAnimations();
  initNavScroll();
});
