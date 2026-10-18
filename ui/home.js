/* ===================================================================
   Nod — Interactive Experience & Motion Controller
   Libraries: Lenis Smooth Scroll + GSAP 3 + ScrollTrigger
   Accessibility: WCAG AA, full keyboard navigation, reduced motion
   =================================================================== */


/* ── 2. Theme Toggle (Light / Dark Mode) ─────────────────────────── */
function initThemeToggle() {
  const themeBtn = document.querySelector('#theme');
  if (!themeBtn) return;

  // Sync initial button label and state with current dataset.theme
  const isCurrentlyDark = document.documentElement.dataset.theme === 'dark';
  themeBtn.setAttribute('aria-pressed', String(isCurrentlyDark));
  const textSpan = themeBtn.querySelector('.nav-cloud-text');
  const initialLabel = isCurrentlyDark ? 'Light theme' : 'Dark theme';
  if (textSpan) {
    textSpan.textContent = initialLabel;
  } else {
    themeBtn.textContent = initialLabel;
  }

  themeBtn.addEventListener('click', (event) => {
    const isDark = document.documentElement.dataset.theme !== 'dark';
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
    try {
      localStorage.setItem('nod-theme', isDark ? 'dark' : 'light');
    } catch (e) {}

    event.currentTarget.setAttribute('aria-pressed', String(isDark));
    const label = isDark ? 'Light theme' : 'Dark theme';
    const currentSpan = event.currentTarget.querySelector('.nav-cloud-text');
    if (currentSpan) {
      currentSpan.textContent = label;
    } else {
      event.currentTarget.textContent = label;
    }
    
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
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (!prefersReducedMotion) {
    const heroTl = gsap.timeline({ defaults: { ease: 'power2.out' } });

    // Logo fades and rises in first, then headline, then search bar (staggered ~0.12s)
    heroTl
      .from('.hero-logo-wrapper', {
        opacity: 0,
        y: 26,
        duration: 0.75,
        clearProps: 'opacity'
      })
      .from('.hero-title', {
        opacity: 0,
        y: 20,
        duration: 0.7,
        clearProps: 'opacity'
      }, '-=0.62')
      .from('.hero-search', {
        opacity: 0,
        y: 18,
        duration: 0.7,
        clearProps: 'opacity'
      }, '-=0.58');

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
  }


  // ── B. Desktop Mouse-Move Parallax for Hero Clouds ───────────────
  if (window.innerWidth > 800 && !prefersReducedMotion) {
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

  // ── B2. Subtle Scroll Parallax on Hero Clouds (Layered Depth) ──────
  if (!prefersReducedMotion) {
    document.querySelectorAll('.hero-cloud-back').forEach((cloud) => {
      gsap.to(cloud, {
        y: () => window.innerHeight * 0.18,
        ease: 'none',
        scrollTrigger: {
          trigger: '.hero-section',
          start: 'top top',
          end: 'bottom top',
          scrub: true
        }
      });
    });

    document.querySelectorAll('.hero-cloud-front').forEach((cloud) => {
      gsap.to(cloud, {
        y: () => window.innerHeight * 0.32,
        ease: 'none',
        scrollTrigger: {
          trigger: '.hero-section',
          start: 'top top',
          end: 'bottom top',
          scrub: true
        }
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

/* ── 5. Mobile Navigation Menu Toggle ────────────────────────────── */
function initMobileMenu() {
  const toggleBtn = document.getElementById('mobile-menu-toggle');
  const panel = document.getElementById('mobile-nav-menu');
  if (!toggleBtn || !panel) return;

  function closeMenu() {
    toggleBtn.setAttribute('aria-expanded', 'false');
    panel.hidden = true;
  }

  function openMenu() {
    toggleBtn.setAttribute('aria-expanded', 'true');
    panel.hidden = false;
  }

  toggleBtn.addEventListener('click', () => {
    const isExpanded = toggleBtn.getAttribute('aria-expanded') === 'true';
    if (isExpanded) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  // Escape key closes mobile menu and returns focus
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && toggleBtn.getAttribute('aria-expanded') === 'true') {
      closeMenu();
      toggleBtn.focus();
    }
  });

  // Click outside closes menu
  document.addEventListener('click', (e) => {
    if (toggleBtn.getAttribute('aria-expanded') === 'true' &&
        !panel.contains(e.target) &&
        !toggleBtn.contains(e.target)) {
      closeMenu();
    }
  });

  // Close mobile drawer when any link is clicked
  panel.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => {
      closeMenu();
    });
  });
}

/* ── 6. Navigation Smooth Scrolling (No Active Highlight) ───────── */
function initNavScroll() {
  const navLinks = document.querySelectorAll('.header-nav a[href^="#"], .mobile-nav-panel a[href^="#"]');
  if (!navLinks.length) return;

  navLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      const targetId = link.getAttribute('href');
      if (!targetId || targetId === '#') return;

      const targetEl = document.querySelector(targetId);
      if (!targetEl) return;

      event.preventDefault();

      const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const navHeader = document.querySelector('.site-header');
      const navHeight = navHeader ? navHeader.offsetHeight : 104;
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
    });
  });
}

/* ── DOM Ready Initialization ────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  initNightStars();
  initAnimations();
  initMobileMenu();
  initNavScroll();
});
