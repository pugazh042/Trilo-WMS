// OTP resend timer + basic auth form validation

function qs(sel) {
  return document.querySelector(sel);
}

function postJson(url, payload) {
  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: JSON.stringify(payload || {}),
  }).then(r => r.json().then(j => ({ ok: r.ok, status: r.status, json: j })));
}

function getCookie(name) {
  const v = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith(name + '='));
  return v ? decodeURIComponent(v.split('=')[1]) : '';
}

document.addEventListener('DOMContentLoaded', () => {
  const loginForm = qs('#loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', (e) => {
      const id = qs('#identifier')?.value?.trim();
      const pw = qs('#password')?.value;
      if (!id || !pw) {
        e.preventDefault();
        alert('Please enter your login and password.');
      }
    });
  }

  const otpForm = qs('#otpForm');
  if (otpForm) {
    setupOtpInputs();
    setupResend();
  }

  // Sidebar Toggle Logic
  const sidebarToggle = qs('#sidebarToggle');
  const layout = qs('.layout');
  if (sidebarToggle && layout) {
    const isCollapsed = localStorage.getItem('sidebar_collapsed') === 'true';
    if (isCollapsed) {
      layout.classList.add('sidebar-collapsed');
    }

    sidebarToggle.addEventListener('click', () => {
      layout.classList.toggle('sidebar-collapsed');
      localStorage.setItem('sidebar_collapsed', layout.classList.contains('sidebar-collapsed'));
    });
  }

  // Dropdown toggles
  document.querySelectorAll('.nav-dropdown-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      const parent = this.closest('.nav-dropdown');
      parent.classList.toggle('expanded');
    });
    
    if(btn.classList.contains('active')) {
      btn.closest('.nav-dropdown').classList.add('expanded');
    }
  });
});

function setupOtpInputs() {
  const digits = document.querySelectorAll('.otp-digit');
  const hidden = qs('#otpValue');
  const verifyBtn = qs('#verifyBtn');
  if (!digits.length || !hidden || !verifyBtn) return;

  function sync() {
    const code = [...digits].map(d => (d.value || '').replace(/\D/g, '')).join('');
    hidden.value = code;
    verifyBtn.disabled = code.length !== 6;
  }

  digits.forEach((inp, i) => {
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !inp.value && i > 0) {
        digits[i - 1].focus();
        digits[i - 1].value = '';
        digits[i - 1].classList.remove('filled');
        sync();
      }
    });

    inp.addEventListener('input', (e) => {
      const val = (e.target.value || '').replace(/\D/g, '').slice(0, 1);
      inp.value = val;
      inp.classList.toggle('filled', !!val);
      if (val && i < digits.length - 1) digits[i + 1].focus();
      sync();
    });

    inp.addEventListener('paste', (e) => {
      e.preventDefault();
      const paste = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '').slice(0, 6);
      [...paste].forEach((ch, j) => {
        if (digits[j]) {
          digits[j].value = ch;
          digits[j].classList.add('filled');
        }
      });
      digits[Math.min(5, paste.length - 1)]?.focus();
      sync();
    });
  });

  sync();
  digits[0].focus();
}

function setupResend() {
  const resendBtn = qs('#resendBtn');
  const timerEl = qs('#timer');
  if (!resendBtn || !timerEl) return;

  let seconds = parseInt(timerEl.dataset.remaining || '30', 10);
  if (Number.isNaN(seconds) || seconds < 0) seconds = 30;

  function tick() {
    if (seconds <= 0) {
      resendBtn.disabled = false;
      timerEl.textContent = '';
      return;
    }
    resendBtn.disabled = true;
    timerEl.textContent = `Resend available in ${seconds}s`;
    seconds -= 1;
    setTimeout(tick, 1000);
  }

  tick();

  resendBtn.addEventListener('click', async (e) => {
    e.preventDefault();
    resendBtn.disabled = true;
    const resp = await fetch('/resend-otp/', { method: 'POST', headers: { 'X-CSRFToken': getCookie('csrftoken') } });
    const data = await resp.json().catch(() => ({}));
    seconds = 30;
    tick();
    if (data && data.otp) {
      alert(`New OTP (demo): ${data.otp}`);
    }
  });
}

