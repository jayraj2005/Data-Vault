// DataVault — shared front-end behaviour  v4 "Plain + Professional"
// Accent: muted steel blue #3B5A78

document.addEventListener('DOMContentLoaded', function () {

  /* ── 1. Mobile sidebar toggle ──────────────────────────── */
  var toggleBtn = document.getElementById('sidebarToggle');
  var sidebar   = document.querySelector('.sidebar');
  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener('click', function () {
      sidebar.classList.toggle('show-mobile');
    });
  }

  /* ── 2. Generic confirm-before-submit ──────────────────── */
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      var msg = form.getAttribute('data-confirm');
      if (!window.confirm(msg)) e.preventDefault();
    });
  });

  /* ── 3. Auto-dismiss flash alerts ──────────────────────── */
  document.querySelectorAll('.alert-auto').forEach(function (el, i) {
    setTimeout(function () {
      el.style.transition = 'opacity 0.4s ease, transform 0.4s ease, max-height 0.4s ease';
      el.style.opacity = '0';
      el.style.transform = 'translateY(-6px)';
      el.style.maxHeight = '0';
      el.style.overflow = 'hidden';
      setTimeout(function () { el.remove(); }, 420);
    }, 3500 + i * 300);
  });

  /* ── 4. Mark notifications read when dropdown opened ───── */
  var notifToggle = document.getElementById('notifDropdown');
  var notifForm   = document.getElementById('markReadForm');
  if (notifToggle && notifForm) {
    notifToggle.addEventListener('click', function () {
      var badge = notifToggle.querySelector('.badge-dot');
      if (badge) {
        fetch(notifForm.action, { method: 'POST' })
          .then(function () { badge.remove(); })
          .catch(function () {});
      }
    });
  }

  /* ── 5. Staggered entrance animation for content cards ─── */
  var staggerTargets = document.querySelectorAll(
    '.stat-card, .panel-card, .chart-card, .block-node, .user-activity-card, .dataset-select-card, .sample-card'
  );
  staggerTargets.forEach(function (el, i) {
    el.style.opacity = '0';
    el.style.transform = 'translateY(14px)';
    el.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
    setTimeout(function () {
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    }, 60 + i * 40);
  });

  /* ── 6. Ripple on .btn-brand clicks ─────────────────────── */
  document.querySelectorAll('.btn-brand').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      var ripple = document.createElement('span');
      var rect   = btn.getBoundingClientRect();
      var size   = Math.max(rect.width, rect.height);
      ripple.style.cssText = [
        'position:absolute',
        'border-radius:50%',
        'background:rgba(255,255,255,0.30)',
        'pointer-events:none',
        'width:' + size + 'px',
        'height:' + size + 'px',
        'top:' + (e.clientY - rect.top - size / 2) + 'px',
        'left:' + (e.clientX - rect.left - size / 2) + 'px',
        'transform:scale(0)',
        'animation:ripple 0.55s ease-out forwards'
      ].join(';');
      btn.style.position = 'relative';
      btn.style.overflow = 'hidden';
      btn.appendChild(ripple);
      setTimeout(function () { ripple.remove(); }, 600);
    });
  });

  /* ── 7. Count-up animation for stat values ───────────────── */
  function animateNumber(el, target, duration) {
    var start = 0;
    var step  = 16;
    var steps = Math.ceil(duration / step);
    var inc   = target / steps;
    var current = 0;
    var timer = setInterval(function () {
      current = Math.min(current + inc, target);
      el.textContent = Math.round(current).toLocaleString();
      if (current >= target) clearInterval(timer);
    }, step);
  }

  document.querySelectorAll('.stat-card .stat-value').forEach(function (el) {
    var raw = el.textContent.trim().replace(/,/g, '');
    var num = parseFloat(raw);
    if (!isNaN(num) && num > 0) {
      el.textContent = '0';
      setTimeout(function () {
        animateNumber(el, num, 700);
      }, 200);
    }
  });

  /* ── 8. Sidebar nav-link active highlight on hover ───────── */
  document.querySelectorAll('.sidebar .nav-link').forEach(function (link) {
    link.addEventListener('mouseenter', function () {
      if (!link.classList.contains('active') && !link.classList.contains('danger')) {
        link.style.paddingLeft = '13px';
      }
    });
    link.addEventListener('mouseleave', function () {
      link.style.paddingLeft = '';
    });
  });

  /* ── 9. Alert dismiss on click ──────────────────────────── */
  document.querySelectorAll('.alert .btn-close').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var alert = btn.closest('.alert');
      if (alert) {
        alert.style.transition = 'opacity 0.3s, transform 0.3s';
        alert.style.opacity = '0';
        alert.style.transform = 'translateY(-6px)';
        setTimeout(function () { alert.remove(); }, 320);
      }
    });
  });

  /* ── 10. Hash-chip copy on click ─────────────────────────── */
  document.querySelectorAll('.hash-chip').forEach(function (chip) {
    chip.style.cursor = 'pointer';
    chip.title = 'Click to copy';
    chip.addEventListener('click', function () {
      navigator.clipboard.writeText(chip.textContent.trim()).then(function () {
        var orig = chip.textContent;
        chip.textContent = 'Copied!';
        chip.style.color = 'var(--success)';
        chip.style.borderColor = 'var(--success)';
        setTimeout(function () {
          chip.textContent = orig;
          chip.style.color = '';
          chip.style.borderColor = '';
        }, 1400);
      }).catch(function () {});
    });
  });

  /* ── 11. Topbar scroll shadow ────────────────────────────── */
  var topbar = document.querySelector('.topbar');
  if (topbar) {
    window.addEventListener('scroll', function () {
      if (window.scrollY > 4) {
        topbar.style.boxShadow = '0 4px 16px rgba(15,23,42,0.08)';
      } else {
        topbar.style.boxShadow = '';
      }
    }, { passive: true });
  }

});


// ──────────────────────────────────────────────────────────────────────
// AUTH PAGES — decorative chain-block cycling
// ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {

  var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Chain block cycling (auth aside) ───────────────────── */
  (function () {
    var blocks  = document.querySelectorAll('#chainViz .chain-block');
    if (!blocks.length || prefersReducedMotion) return;
    var current = 0;
    /* Find initially active one */
    blocks.forEach(function (b, i) { if (b.classList.contains('active')) current = i; });

    setInterval(function () {
      blocks[current].classList.remove('active');
      current = (current + 1) % blocks.length;
      blocks[current].classList.add('active');
    }, 1400);
  })();

});
