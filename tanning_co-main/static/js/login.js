(function () {
  var form = document.getElementById('loginForm');
  var err = document.getElementById('loginError');
  var btn = document.getElementById('loginBtn');

  fetch('/api/auth/status').then(function (r) { return r.json(); }).then(function (s) {
    if (s.signed_in) { window.location.href = '/'; return; }
    if (s.setup_needed) document.getElementById('setupNotice').classList.remove('hidden');
  }).catch(function () {});

  document.getElementById('username').focus();

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    err.textContent = '';
    btn.disabled = true;
    btn.textContent = 'Signing in…';
    try {
      var res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: JSON.stringify({
          username: document.getElementById('username').value.trim(),
          password: document.getElementById('password').value
        })
      });
      if (res.ok) { window.location.href = '/'; return; }
      var data = await res.json().catch(function () { return {}; });
      err.textContent = data.detail || 'Sign in failed';
      document.getElementById('password').value = '';
      document.getElementById('password').focus();
    } catch (ex) {
      err.textContent = 'Could not reach the server. Check the internet connection.';
    }
    btn.disabled = false;
    btn.textContent = 'Sign in';
  });
})();
