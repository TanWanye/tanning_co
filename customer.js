(function () {
  'use strict';
  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  async function post(path, body) {
    var res = await fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
      body: JSON.stringify(body || {})
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      var d = data.detail;
      if (Array.isArray(d)) d = 'Please check you have filled in every box.';
      throw new Error(d || 'Something went wrong. Please try again.');
    }
    return data;
  }

  var panels = ['signup', 'done', 'login', 'account'];
  function show(id) {
    panels.forEach(function (p) { $(p).classList.toggle('hidden', p !== id); });
    var tab = (id === 'signup' || id === 'done') ? 'signup' : 'login';
    document.querySelectorAll('#tabs button').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tab); });
    $('tabs').classList.toggle('hidden', id === 'account');
    window.scrollTo(0, 0);
  }

  document.querySelectorAll('[data-tab], [data-tab-go]').forEach(function (b) {
    b.addEventListener('click', function () { show(b.dataset.tab || b.dataset.tabGo); });
  });

  // ---- Sign up
  var form = $('signupForm');
  var today = new Date();
  form.dob.max = new Date(today.getFullYear() - 18, today.getMonth(), today.getDate()).toISOString().slice(0, 10);

  form.addEventListener('change', function (e) {
    if (e.target.name === 'skin_type') $('skin1Notice').classList.toggle('hidden', e.target.value !== '1');
  });

  function age(dobStr) {
    var b = new Date(dobStr), n = new Date();
    var a = n.getFullYear() - b.getFullYear();
    if (n.getMonth() < b.getMonth() || (n.getMonth() === b.getMonth() && n.getDate() < b.getDate())) a--;
    return a;
  }

  function validate(f) {
    if (f.name.value.trim().length < 2) return ['name', 'Please enter your full name.'];
    var phone = f.phone.value.replace(/[^\d+]/g, '').replace(/^\+44/, '0');
    if (!/^07\d{9}$/.test(phone)) return ['phone', 'Please enter a UK mobile number starting 07.'];
    if (f.email.value && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.value.trim())) return ['email', 'That email address doesn\'t look right.'];
    if (!f.postcode.value.trim()) return ['postcode', 'Please enter your postcode.'];
    if (!f.dob.value) return ['dob', 'Please enter your date of birth.'];
    if (age(f.dob.value) < 18) return ['dob', 'Sorry – by law you must be 18 or over to use a sunbed.'];
    if (!f.shop.value) return [null, 'Please choose your salon.'];
    if (!f.skin_type.value) return [null, 'Please choose your skin type.'];
    if (!f.declaration.checked || !f.terms.checked) return ['declaration', 'Please read and tick both health declaration boxes.'];
    if (f.marketing_email.checked && !f.email.value.trim()) return ['email', 'Add your email address to get offers by email.'];
    if (!/^\d{4,6}$/.test(f.pin.value)) return ['pin', 'Your PIN must be 4 to 6 digits.'];
    if (f.pin.value !== f.pin2.value) return ['pin2', 'The two PINs don\'t match.'];
    return null;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var err = $('signupError');
    err.textContent = '';
    var problem = validate(form);
    if (problem) {
      err.textContent = problem[1];
      if (problem[0] && form[problem[0]]) form[problem[0]].focus();
      return;
    }
    var btn = $('signupBtn');
    btn.disabled = true; btn.textContent = 'Creating your account…';
    try {
      var r = await post('/api/public/register', {
        name: form.name.value.trim(), phone: form.phone.value.trim(), email: form.email.value.trim(),
        postcode: form.postcode.value.trim(), dob: form.dob.value, shop: form.shop.value,
        skin_type: form.skin_type.value, declaration: form.declaration.checked, terms: form.terms.checked,
        marketing_email: form.marketing_email.checked, marketing_sms: form.marketing_sms.checked,
        pin: form.pin.value, website: form.website.value
      });
      $('doneNumber').textContent = '#' + r.customer_number;
      $('doneSkin1').classList.toggle('hidden', !r.skin_type_warning);
      form.reset();
      $('skin1Notice').classList.add('hidden');
      show('done');
    } catch (ex) {
      err.textContent = ex.message;
    }
    btn.disabled = false; btn.textContent = 'Create my account';
  });

  // ---- Log in / account
  async function loadAccount() {
    var res = await fetch('/api/customer/me', { credentials: 'same-origin' });
    if (!res.ok) return false;
    var me = await res.json();
    $('accName').textContent = 'Hi ' + me.name.split(' ')[0] + '!';
    $('accMinutes').textContent = me.remaining_minutes || 0;
    $('accVisits').textContent = me.visits || 0;
    $('accLoyalty').textContent = me.loyalty || 0;
    $('accNumber').textContent = me.customer_number ? '#' + me.customer_number : '–';
    var pkg = $('accPackage');
    if (me.package_type && me.package_expires && new Date(me.package_expires) >= new Date(new Date().toDateString())) {
      pkg.textContent = 'Unlimited ' + me.package_type + ' until ' + new Date(me.package_expires).toLocaleDateString('en-GB');
      pkg.classList.remove('hidden');
    } else { pkg.classList.add('hidden'); }
    var h = await fetch('/api/customer/my-history', { credentials: 'same-origin' });
    var rows = h.ok ? await h.json() : [];
    $('accHistory').innerHTML = rows.length ? rows.map(function (o) {
      return '<div class="history-item"><b>' + esc(o.items || 'Purchase') + '</b><br><small>' +
        esc(new Date(String(o.created_at).replace(' ', 'T') + 'Z').toLocaleDateString('en-GB')) + ' · £' + (Number(o.total) || 0).toFixed(2) + '</small></div>';
    }).join('') : '<p class="hint">Nothing yet.</p>';
    show('account');
    return true;
  }

  $('loginForm').addEventListener('submit', async function (e) {
    e.preventDefault();
    var f = e.target, err = $('loginError'), btn = $('loginBtn');
    err.textContent = '';
    btn.disabled = true;
    try {
      await post('/api/customer/login', { phone: f.phone.value.trim(), pin: f.pin.value });
      f.pin.value = '';
      await loadAccount();
    } catch (ex) {
      err.textContent = ex.message;
      f.pin.value = '';
    }
    btn.disabled = false;
  });

  $('logoutBtn').addEventListener('click', async function () {
    try { await post('/api/customer/logout'); } catch (e) { /* ignore */ }
    show('login');
  });

  // Already logged in (within the last 30 minutes)?
  loadAccount().catch(function () {});
  if (location.hash === '#login') show('login');
})();
