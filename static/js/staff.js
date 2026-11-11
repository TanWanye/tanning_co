/* The Tanning Co. Coventry - staff bed board */
(function () {
  'use strict';

  var SITES = {
    Wyken: { address: '50 Torcross Ave, CV2 3NQ', phone: '024 7665 9966', hours: 'Mon–Sat 9–9 · Sun 10–3' },
    Earlsdon: { address: '116 Albany Rd, CV5 6NG', phone: '024 7667 4922', hours: 'Mon–Fri 9–9 · Sat 9–7 · Sun 10–3' }
  };
  var SKIN = {
    '1': 'Type 1 – always burns, never tans',
    '2': 'Type 2 – burns easily, tans minimally',
    '3': 'Type 3 – sometimes burns, tans gradually',
    '4': 'Type 4 – rarely burns, tans well',
    '5': 'Type 5 – very rarely burns, tans deeply',
    '6': 'Type 6 – never burns'
  };

  var state = {
    me: null,
    rooms: [],
    roomsKey: '',
    site: readSite(),
    products: [],
    customer: null,
    searchTimer: null
  };

  // ------------------------------------------------------------------ helpers
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function $(id) { return document.getElementById(id); }
  function money(n) { return '£' + (Number(n) || 0).toFixed(2); }
  function utc(ts) { return ts ? new Date(String(ts).replace(' ', 'T') + 'Z') : null; }
  function fmtDate(ts) { var d = utc(ts); return d ? d.toLocaleDateString('en-GB') : ''; }
  function fmtDateTime(ts) { var d = utc(ts); return d ? d.toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' }) : ''; }
  function shortBed(name) { return String(name).replace(/^(Wyken|Earlsdon)\s+/, ''); }
  function initials(name) { return String(name || '?').replace(/[^A-Za-z\s'-]/g, '').trim().split(/\s+/).filter(Boolean).map(function (p) { return p[0]; }).slice(0, 2).join('').toUpperCase() || '?'; }
  function isAdmin() { return state.me && state.me.role === 'admin'; }
  function readSite() { try { return localStorage.getItem('ttc_site') || 'all'; } catch (e) { return 'all'; } }
  function saveSite(s) { try { localStorage.setItem('ttc_site', s); } catch (e) { /* ignore */ } }
  function shopForNew() { return state.site === 'all' ? null : state.site; }

  async function api(path, opts) {
    opts = opts || {};
    var headers = { 'X-Requested-With': 'fetch' };
    var body;
    if (opts.form) { body = opts.form; }
    else if (opts.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body); }
    var res = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers: headers, body: body, credentials: 'same-origin' });
    if (res.status === 401) { window.location.href = '/login'; throw new Error('Please sign in again'); }
    var data = await res.json().catch(function () { return null; });
    if (!res.ok) {
      var d = data && data.detail;
      if (Array.isArray(d)) d = 'Please check the form – something is missing or invalid';
      throw new Error(d || 'Something went wrong (' + res.status + ')');
    }
    return data;
  }

  function toast(msg, isError) {
    var t = document.createElement('div');
    t.className = 'toast' + (isError ? ' error' : '');
    t.textContent = msg;
    $('toasts').appendChild(t);
    setTimeout(function () { t.remove(); }, isError ? 6000 : 3500);
  }
  function fail(e) { toast(e.message || String(e), true); }

  // ------------------------------------------------------------------ modals
  function openModal(title, html, opts) {
    opts = opts || {};
    var bd = document.createElement('div');
    bd.className = 'modal-backdrop';
    bd.innerHTML = '<div class="modal' + (opts.wide ? ' wide' : '') + '" role="dialog" aria-modal="true">' +
      '<div class="modal-head"><h2>' + esc(title) + '</h2><button class="modal-close" data-action="close-modal" aria-label="Close">×</button></div>' +
      '<div class="modal-body">' + html + '</div></div>';
    bd.addEventListener('mousedown', function (e) { if (e.target === bd) closeModal(); });
    $('modalRoot').appendChild(bd);
    var first = bd.querySelector('input:not([type=hidden]):not([type=checkbox]), select, textarea');
    if (first && !opts.noFocus) setTimeout(function () { first.focus(); }, 30);
    return bd;
  }
  function closeModal() { var root = $('modalRoot'); if (root.lastElementChild) root.lastElementChild.remove(); }
  function closeAllModals() { $('modalRoot').innerHTML = ''; }
  function topModal() { return $('modalRoot').lastElementChild; }

  function confirmBox(title, message, okLabel, danger) {
    return new Promise(function (resolve) {
      var m = openModal(title, '<p>' + message + '</p><div class="form-actions">' +
        '<button class="btn btn-ghost" data-confirm="no">Cancel</button>' +
        '<button class="btn' + (danger ? ' btn-danger' : '') + '" data-confirm="yes">' + esc(okLabel || 'OK') + '</button></div>', { noFocus: true });
      m.addEventListener('click', function (e) {
        var b = e.target.closest('[data-confirm]');
        if (!b && !e.target.closest('[data-action=close-modal]')) return;
        e.stopPropagation();
        closeModal();
        resolve(!!b && b.dataset.confirm === 'yes');
      });
      m.querySelector('[data-confirm=yes]').focus();
    });
  }

  // ------------------------------------------------------------------ board
  async function loadRooms(force) {
    try {
      var rooms = await api('/api/rooms');
      var key = JSON.stringify(rooms);
      if (!force && key === state.roomsKey) return;
      state.rooms = rooms; state.roomsKey = key;
      renderBoard();
    } catch (e) { /* keep last view; connection blips are common */ }
  }

  function visibleSites() { return state.site === 'all' ? ['Wyken', 'Earlsdon'] : [state.site]; }

  function renderBoard() {
    document.querySelectorAll('#siteSwitch button').forEach(function (b) {
      b.classList.toggle('active', b.dataset.site === state.site);
    });
    $('board').innerHTML = visibleSites().map(function (site) {
      var beds = state.rooms.filter(function (r) { return r.shop === site; });
      var c = { available: 0, occupied: 0, cleaning: 0 };
      beds.forEach(function (b) { c[b.status] = (c[b.status] || 0) + 1; });
      var info = SITES[site];
      return '<div class="site">' +
        '<div class="site-head"><h2>' + esc(site) + '</h2><div class="counts">' +
          '<span><i class="dot free"></i><b>' + c.available + '</b> free</span>' +
          '<span><i class="dot busy"></i><b>' + c.occupied + '</b> in use</span>' +
          '<span><i class="dot clean"></i><b>' + c.cleaning + '</b> cleaning</span></div></div>' +
        '<div class="site-info">' + esc(info.address) + ' · ' + esc(info.phone) + ' · ' + esc(info.hours) + '</div>' +
        '<div class="beds">' + beds.map(bedTile).join('') + '</div></div>';
    }).join('');
    tick();
  }

  function bedTile(r) {
    var label = { available: 'Free', occupied: 'In use', cleaning: 'Cleaning' }[r.status] || r.status;
    var body = '', actions = '';
    if (r.status === 'occupied') {
      var start = utc(r.started_at);
      var end = start ? start.getTime() + (r.remaining_minutes || 0) * 60000 : Date.now();
      body = '<div class="bed-who" title="' + esc(r.customer_name) + '">' + esc(r.customer_name || 'Customer') + '</div>' +
        '<div class="countdown" data-end="' + end + '">--:--</div>';
      actions = '<button class="btn btn-sm" data-action="finish" data-room="' + r.id + '">Finish</button>' +
        '<button class="btn btn-sm btn-ghost" data-action="waitlist" data-room="' + r.id + '">Waiting list</button>';
    } else if (r.status === 'cleaning') {
      body = '<div class="bed-free-note">Needs cleaning</div>';
      actions = '<button class="btn btn-sm" data-action="clean" data-room="' + r.id + '">Mark clean</button>' +
        '<button class="btn btn-sm btn-ghost" data-action="waitlist" data-room="' + r.id + '">Waiting list</button>';
    } else {
      body = '<div class="bed-free-note">Ready</div>';
      actions = '<button class="btn btn-sm" data-action="start-bed" data-room="' + r.id + '">Start session</button>';
    }
    return '<div class="bed ' + esc(r.status) + '">' +
      '<div class="bed-top"><div class="bed-name">' + esc(shortBed(r.name)) + '</div>' +
      '<span class="pill ' + esc(r.status) + '">' + esc(label) + '</span></div>' +
      body + '<div class="bed-actions">' + actions + '</div></div>';
  }

  function tick() {
    var now = new Date();
    $('clock').textContent = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    document.querySelectorAll('.countdown[data-end]').forEach(function (el) {
      var left = Math.round((Number(el.dataset.end) - now.getTime()) / 1000);
      var bed = el.closest('.bed');
      var pill = bed.querySelector('.pill');
      if (left <= 0) {
        el.textContent = 'Time up';
        bed.classList.add('timeup');
        pill.className = 'pill timeup'; pill.textContent = 'Time up';
      } else {
        var m = Math.floor(left / 60), s = left % 60;
        el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
        bed.classList.remove('timeup');
      }
    });
  }

  // ------------------------------------------------------------------ side panels
  async function loadQueue() {
    try {
      var q = await api('/api/queue');
      $('queueCount').textContent = q.length;
      $('queueList').innerHTML = q.length ? q.map(function (item) {
        return '<div class="list-item"><div class="row"><b>' + esc(item.customer_name) + '</b><span class="muted small">' + money(item.total) + '</span></div>' +
          '<div class="muted small">' + esc(item.items) + '</div>' +
          '<div class="actions"><button class="btn btn-sm" data-action="queue-assign" data-id="' + item.id + '">Assign bed</button>' +
          '<button class="btn btn-sm btn-danger" data-action="queue-remove" data-id="' + item.id + '">Remove</button></div></div>';
      }).join('') : '<div class="empty">No one waiting</div>';
    } catch (e) { /* ignore */ }
  }

  async function loadSignups() {
    try {
      var list = await api('/api/customers/recent-signups');
      $('signupCount').textContent = list.length;
      $('signupHelp').classList.toggle('hidden', !list.length);
      $('signupList').innerHTML = list.length ? list.map(function (c) {
        return '<div class="list-item"><div class="row"><b>' + esc(c.name) + '</b><span class="muted small">#' + esc(c.customer_number || c.id) + '</span></div>' +
          '<div class="muted small">' + esc(c.phone) + ' · ' + esc(c.shop || '') + ' · ' + fmtDateTime(c.created_at) + '</div>' +
          (c.skin_type === '1' ? '<div class="tags"><span class="tag bad">Skin type 1</span></div>' : '') +
          '<div class="actions"><button class="btn btn-sm btn-ghost" data-action="open-customer" data-id="' + c.id + '">Open</button></div></div>';
      }).join('') : '<div class="empty">All checked</div>';
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ search
  function customerResult(c) {
    return '<button class="result" data-action="open-customer" data-id="' + c.id + '">' +
      '<span><span class="r-name">' + esc(c.name) + '</span><br><span class="r-meta">#' + esc(c.customer_number || c.id) + ' · ' + esc(c.phone) +
      (c.shop ? ' · ' + esc(c.shop) : '') + '</span></span>' +
      '<span class="r-mins">' + (c.remaining_minutes || 0) + ' min</span></button>';
  }

  function onSearchInput() {
    clearTimeout(state.searchTimer);
    var q = $('search').value.trim();
    var box = $('searchResults');
    if (!q) { box.classList.add('hidden'); return; }
    state.searchTimer = setTimeout(async function () {
      try {
        var list = await api('/api/customers?q=' + encodeURIComponent(q));
        if ($('search').value.trim() !== q) return;
        box.innerHTML = list.length ? list.map(customerResult).join('') :
          '<div class="search-empty">No customers found. <button class="link-btn" data-action="new-customer">Create a new customer</button></div>';
        box.classList.remove('hidden');
      } catch (e) { fail(e); }
    }, 200);
  }

  function closeSearch() { $('searchResults').classList.add('hidden'); }

  // ------------------------------------------------------------------ customers
  function customerForm(c) {
    c = c || {};
    var shop = c.shop || shopForNew() || '';
    return '<form data-form="' + (c.id ? 'edit-customer' : 'new-customer') + '"' + (c.id ? ' data-id="' + c.id + '"' : '') + '>' +
      '<div class="form-grid">' +
      '<label class="field full"><span>Full name *</span><input type="text" name="name" required maxlength="80" value="' + esc(c.name) + '"></label>' +
      '<label class="field"><span>Mobile *</span><input type="tel" name="phone" required value="' + esc(c.phone) + '"></label>' +
      '<label class="field"><span>Email</span><input type="email" name="email" value="' + esc(c.email) + '"></label>' +
      '<label class="field"><span>Postcode</span><input type="text" name="postcode" value="' + esc(c.postcode) + '"></label>' +
      '<label class="field"><span>Date of birth</span><input type="date" name="dob" value="' + esc(c.dob) + '"></label>' +
      '<label class="field"><span>Home salon</span><select name="shop"><option value="">—</option>' +
        ['Wyken', 'Earlsdon'].map(function (s) { return '<option' + (s === shop ? ' selected' : '') + '>' + s + '</option>'; }).join('') + '</select></label>' +
      '<label class="field"><span>Skin type</span><select name="skin_type"><option value="">Not recorded</option>' +
        Object.keys(SKIN).map(function (k) { return '<option value="' + k + '"' + (c.skin_type === k ? ' selected' : '') + '>' + esc(SKIN[k]) + '</option>'; }).join('') + '</select></label>' +
      '<div class="full"><div class="section-label">Marketing consent (only tick if the customer has said yes)</div>' +
      '<label class="check"><input type="checkbox" name="marketing_email"' + (c.marketing_email ? ' checked' : '') + '> Happy to get offers by email</label>' +
      '<label class="check"><input type="checkbox" name="marketing_sms"' + (c.marketing_sms ? ' checked' : '') + '> Happy to get offers by text</label></div>' +
      '</div><div class="error-text" data-error></div>' +
      '<div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button>' +
      '<button class="btn" type="submit">' + (c.id ? 'Save changes' : 'Create customer') + '</button></div></form>';
  }

  function readCustomerForm(f) {
    return {
      name: f.name.value.trim(), phone: f.phone.value.trim(), email: f.email.value.trim(),
      postcode: f.postcode.value.trim(), dob: f.dob.value || null, shop: f.shop.value || null,
      skin_type: f.skin_type.value || null,
      marketing_email: f.marketing_email.checked, marketing_sms: f.marketing_sms.checked
    };
  }

  async function openCustomer(id) {
    closeSearch();
    try {
      var results = await Promise.all([
        api('/api/customers/' + id),
        api('/api/customer/' + id + '/history'),
        api('/api/customer/' + id + '/bed-usage')
      ]);
      state.customer = results[0];
      closeAllModals();
      openModal('Customer', profileHtml(results[0], results[1], results[2]), { wide: true, noFocus: true });
    } catch (e) { fail(e); }
  }

  function daysLeft(d) { if (!d) return 0; return Math.max(0, Math.ceil((new Date(d) - new Date()) / 86400000)); }
  function ageFrom(dob) {
    if (!dob) return null;
    var b = new Date(dob), n = new Date();
    var a = n.getFullYear() - b.getFullYear();
    if (n.getMonth() < b.getMonth() || (n.getMonth() === b.getMonth() && n.getDate() < b.getDate())) a--;
    return a;
  }

  function profileHtml(p, history, usage) {
    var age = ageFrom(p.dob);
    var tags = [];
    tags.push(p.id_checked_at ? '<span class="tag ok">ID checked</span>' : '<span class="tag warn">ID not checked</span>');
    if (age !== null) tags.push('<span class="tag' + (age < 18 ? ' bad' : '') + '">Age ' + age + '</span>');
    if (p.skin_type) tags.push('<span class="tag' + (p.skin_type === '1' ? ' bad' : '') + '">Skin type ' + esc(p.skin_type) + '</span>');
    else tags.push('<span class="tag warn">Skin type not recorded</span>');
    tags.push(p.declaration_at ? '<span class="tag ok">Health declaration ' + fmtDate(p.declaration_at) + '</span>' : '<span class="tag">No online declaration</span>');
    tags.push('<span class="tag' + (p.marketing_email ? ' ok' : '') + '">Email offers: ' + (p.marketing_email ? 'yes' : 'no') + '</span>');
    tags.push('<span class="tag' + (p.marketing_sms ? ' ok' : '') + '">Text offers: ' + (p.marketing_sms ? 'yes' : 'no') + '</span>');
    tags.push(p.has_pin ? '<span class="tag ok">Has PIN</span>' : '<span class="tag">No PIN</span>');
    if (p.source === 'self') tags.push('<span class="tag">Signed up online</span>');

    var alerts = '';
    if (p.skin_type === '1') alerts += '<div class="alert bad"><b>Skin type 1:</b> sunbed use is not recommended for this skin type. Speak to the customer before any session.</div>';
    if (age !== null && age < 18) alerts += '<div class="alert bad"><b>Under 18:</b> by law this customer must not use a sunbed.</div>';
    if (!p.id_checked_at && p.source === 'self') alerts += '<div class="alert warn">Signed up online – check photo ID and confirm the date of birth before the first session.</div>';

    var pkg = p.package_type ? 'Unlimited ' + esc(p.package_type) + '<div class="muted small">' + daysLeft(p.package_expires) + ' days left</div>' : '<span class="muted">None</span>';

    var hist = history.length ? history.map(function (o) {
      return '<div class="h-row"><b>' + esc(o.items) + '</b><br><span class="muted small">' + fmtDate(o.created_at) + ' · ' + money(o.total) + ' · ' + esc(o.payment_method) + (o.shop ? ' · ' + esc(o.shop) : '') + '</span></div>';
    }).join('') : '<div class="empty">No purchases yet</div>';
    var use = usage.length ? usage.map(function (u) {
      return '<div class="h-row">' + esc(u.room_name) + ' · <b>' + (u.minutes || 0) + ' min</b><br><span class="muted small">' + fmtDateTime(u.created_at) + '</span></div>';
    }).join('') : '<div class="empty">No sessions yet</div>';

    return '<div class="profile-head"><span class="avatar">' + esc(initials(p.name)) + '</span><div>' +
      '<h2>' + esc(p.name) + '</h2><div class="muted">#' + esc(p.customer_number || p.id) + ' · ' + esc(p.phone) +
      (p.email ? ' · ' + esc(p.email) : '') + (p.postcode ? ' · ' + esc(p.postcode) : '') + (p.shop ? ' · ' + esc(p.shop) : '') + '</div></div></div>' +
      alerts + '<div class="tags">' + tags.join('') + '</div>' +
      '<div class="stats">' +
        '<div class="stat"><div class="k">Minutes left</div><div class="v gold">' + (p.remaining_minutes || 0) + '</div></div>' +
        '<div class="stat"><div class="k">Package</div><div class="v small">' + pkg + '</div></div>' +
        '<div class="stat"><div class="k">Visits</div><div class="v">' + (p.visits || 0) + '</div></div>' +
        '<div class="stat"><div class="k">Loyalty pts</div><div class="v">' + (p.loyalty || 0) + '</div></div>' +
      '</div>' +
      '<div class="profile-actions">' +
        '<button class="btn" data-action="use-minutes"' + ((p.remaining_minutes || 0) > 0 ? '' : ' disabled title="No minutes left"') + '>Start session (use minutes)</button>' +
        '<button class="btn" data-action="add-to-account">Add to account</button>' +
        '<button class="btn btn-ghost" data-action="edit-customer">Edit</button>' +
        (p.id_checked_at ? '' : '<button class="btn btn-ghost" data-action="id-check">Mark ID checked</button>') +
        '<button class="btn btn-ghost" data-action="set-pin">' + (p.has_pin ? 'Reset PIN' : 'Set PIN') + '</button>' +
        (isAdmin() ? '<button class="btn btn-danger" data-action="delete-customer">Delete</button>' : '') +
      '</div>' +
      '<div class="cols"><div><h4>Purchases</h4><div class="history">' + hist + '</div></div>' +
      '<div><h4>Bed sessions</h4><div class="history">' + use + '</div></div></div>' +
      (p.id_checked_at ? '<p class="muted small mt">ID checked by ' + esc(p.id_checked_by || 'staff') + ' on ' + fmtDate(p.id_checked_at) + '</p>' : '');
  }

  // ------------------------------------------------------------------ add to account
  function productsHtml() {
    var groups = [['session', 'Single sessions (added as minutes)'], ['package', 'Minute packs'], ['unlimited', 'Unlimited']];
    return groups.map(function (g) {
      var items = state.products.filter(function (p) { return p.category === g[0]; });
      if (!items.length) return '';
      return '<div class="section-label">' + esc(g[1]) + '</div><div class="products">' + items.map(function (p) {
        return '<button class="product" data-action="buy" data-id="' + p.id + '"><div class="p-name">' + esc(p.name) + '</div><div class="p-price">' + money(p.price) + '</div></button>';
      }).join('') + '</div>';
    }).join('');
  }

  async function buyProduct(id) {
    var p = state.products.find(function (x) { return x.id === id; });
    var c = state.customer;
    if (!p || !c) return;
    var ok = await confirmBox('Add to account', 'Add <b>' + esc(p.name) + '</b> (' + money(p.price) + ') to <b>' + esc(c.name) + '</b>\'s account? Take payment at the till.', 'Add ' + money(p.price));
    if (!ok) return;
    try {
      await api('/api/orders', { body: { customer_id: c.id, items: [{ id: p.id, name: p.name, price: p.price, qty: 1 }], discount: 0, payment_method: 'Add to Account', total: p.price, shop: shopForNew() || c.shop || 'Unknown' } });
      toast(p.name + ' added to ' + c.name + '\'s account');
      openCustomer(c.id);
      loadQueue();
    } catch (e) { fail(e); }
  }

  // ------------------------------------------------------------------ start a session
  function availableBeds() {
    return state.rooms.filter(function (r) { return r.status === 'available' && (state.site === 'all' || r.shop === state.site); });
  }

  function bedPicker(selected) {
    var beds = availableBeds();
    if (!beds.length) return '<div class="empty">No free beds' + (state.site === 'all' ? '' : ' at ' + esc(state.site)) + ' right now.</div>';
    return '<div class="bed-pick">' + beds.map(function (r) {
      return '<button type="button" data-action="pick-bed" data-room="' + r.id + '"' + (r.id === selected ? ' class="selected"' : '') + '>' +
        '<b>' + esc(shortBed(r.name)) + '</b><small>' + (state.site === 'all' ? esc(r.shop) + ' · ' : '') + esc(r.type) + '</small></button>';
    }).join('') + '</div>';
  }

  function openUseMinutes(c, roomId) {
    var max = c.remaining_minutes || 0;
    var quick = [3, 6, 9, 12, 15].filter(function (m) { return m <= max; });
    closeAllModals();
    var m = openModal('Start session – ' + c.name,
      '<form data-form="use-minutes" data-customer="' + c.id + '">' +
      '<p class="muted mb"></p>' +
      '<div class="section-label">Minutes (' + max + ' available)</div>' +
      '<div class="quick-mins">' + quick.map(function (q) { return '<button type="button" class="btn btn-sm btn-ghost" data-action="quick-min" data-min="' + q + '">' + q + ' min</button>'; }).join('') + '</div>' +
      '<label class="field"><input type="number" name="minutes" min="1" max="' + Math.min(max, 30) + '" required placeholder="Minutes"></label>' +
      '<div class="section-label">Bed</div>' + bedPicker(roomId) +
      '<input type="hidden" name="room" value="' + (roomId || '') + '">' +
      '<div class="error-text" data-error></div>' +
      '<div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button><button class="btn" type="submit">Start session</button></div></form>');
    m.querySelector('p.muted').textContent = c.name + ' has ' + max + ' minutes left.';
  }

  function openStartBed(roomId) {
    var r = state.rooms.find(function (x) { return x.id === roomId; });
    state.pendingBed = roomId;
    openModal('Start session – ' + (r ? r.name : 'bed'),
      '<label class="field"><span>Find the customer</span><input type="search" data-bed-search placeholder="Name, phone or customer number" autocomplete="off"></label>' +
      '<div data-bed-results class="scroll"></div>' +
      '<div class="form-actions"><button class="btn btn-ghost" data-action="new-customer">+ New customer</button></div>');
  }

  // ------------------------------------------------------------------ reports & admin
  async function loadReport(date) {
    var el = topModal().querySelector('[data-report]');
    el.innerHTML = '<div class="empty">Loading…</div>';
    try {
      var r = await api('/api/report/daily?date=' + encodeURIComponent(date));
      var html = '';
      Object.keys(r.shops).forEach(function (shop) {
        var s = r.shops[shop];
        var lines = Object.keys(s.breakdown).filter(function (k) { return s.breakdown[k] > 0; })
          .map(function (k) { return '<div class="kv"><span>' + esc(k) + '</span><span>' + money(s.breakdown[k]) + '</span></div>'; }).join('');
        html += '<div class="report-shop"><h3>' + esc(shop) + '</h3>' +
          '<div class="kv"><span>Sessions</span><b>' + s.sessions.length + '</b></div>' +
          '<div class="kv"><span>Minutes used</span><b>' + s.minutes + '</b></div>' +
          '<div class="kv"><span>Minutes left on customer accounts</span><b>' + s.minutes_left + '</b></div>' +
          '<div class="section-label">Takings</div>' + (lines || '<div class="empty">No sales</div>') +
          '<div class="kv"><b>Total</b><b>' + money(s.money) + '</b></div>' +
          (s.sessions.length ? '<div class="section-label">Sessions</div><div class="history">' + s.sessions.map(function (x) {
            return '<div class="h-row">' + esc(x.customer_name || 'Unknown') + ' · ' + x.minutes + ' min · ' + esc(shortBed(x.room_name)) + ' <span class="muted small">' + fmtDateTime(x.created_at) + '</span></div>';
          }).join('') + '</div>' : '') + '</div>';
      });
      html += '<div class="report-shop report-total"><h3>Both salons</h3>' +
        '<div class="kv"><span>Minutes used</span><b>' + r.total_minutes + '</b></div>' +
        '<div class="kv"><span>Orders</span><b>' + r.order_count + '</b></div>' +
        '<div class="kv"><b>Total takings</b><b>' + money(r.total_money) + '</b></div></div>';
      el.innerHTML = html;
    } catch (e) { el.innerHTML = '<div class="error-text">' + esc(e.message) + '</div>'; }
  }

  async function openStaffAccounts() {
    try {
      var list = await api('/api/staff');
      closeAllModals();
      openModal('Staff accounts',
        '<div class="scroll"><table class="data"><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Last sign-in</th><th></th></tr></thead><tbody>' +
        list.map(function (s) {
          var self = state.me && s.username === state.me.username;
          return '<tr><td>' + esc(s.display_name) + (s.active ? '' : ' <span class="tag bad">Off</span>') + '</td><td>' + esc(s.username) + '</td><td>' + esc(s.role === 'admin' ? 'Manager' : 'Staff') + '</td>' +
            '<td class="muted">' + (s.last_login_at ? fmtDateTime(s.last_login_at) : 'Never') + '</td><td class="nowrap">' +
            '<button class="btn btn-sm btn-ghost" data-action="staff-reset" data-id="' + s.id + '" data-name="' + esc(s.display_name) + '">Reset password</button> ' +
            (self ? '' : '<button class="btn btn-sm ' + (s.active ? 'btn-danger' : 'btn-ghost') + '" data-action="staff-toggle" data-id="' + s.id + '" data-active="' + (s.active ? '0' : '1') + '">' + (s.active ? 'Switch off' : 'Switch on') + '</button>') +
            '</td></tr>';
        }).join('') + '</tbody></table></div>' +
        '<div class="section-label">Add a staff member</div>' +
        '<form data-form="new-staff"><div class="form-grid">' +
        '<label class="field"><span>Name</span><input type="text" name="display_name" required></label>' +
        '<label class="field"><span>Username</span><input type="text" name="username" required autocapitalize="none" placeholder="e.g. sarah"></label>' +
        '<label class="field"><span>Temporary password (10+ characters)</span><input type="password" name="password" required minlength="10" autocomplete="new-password"></label>' +
        '<label class="field"><span>Role</span><select name="role"><option value="staff">Staff – bed board and customers</option><option value="admin">Manager – everything, incl. campaigns and settings</option></select></label>' +
        '</div><div class="error-text" data-error></div><div class="form-actions"><button class="btn" type="submit">Add staff member</button></div></form>',
        { wide: true, noFocus: true });
    } catch (e) { fail(e); }
  }

  async function openActivity() {
    try {
      var rows = await api('/api/audit?limit=300');
      openModal('Activity log', '<p class="muted small mb"></p><div class="scroll"><table class="data"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead><tbody>' +
        rows.map(function (a) {
          return '<tr><td class="nowrap">' + fmtDateTime(a.at) + '</td><td>' + esc(a.username || (a.action === 'customer_self_signup' ? 'Customer' : '—')) + '</td><td>' + esc(String(a.action).replace(/_/g, ' ')) + '</td><td class="muted">' + esc(a.detail) + ' <span class="small">' + esc(a.ip) + '</span></td></tr>';
        }).join('') + '</tbody></table></div>', { wide: true, noFocus: true });
      topModal().querySelector('p.muted').textContent = 'The last 300 actions: sign-ins, changes to customers, sales, campaigns and settings.';
    } catch (e) { fail(e); }
  }

  async function openSettings() {
    try {
      var r = await Promise.all([api('/api/email/settings'), api('/api/sms/settings')]);
      var em = r[0] || {}, sm = r[1] || {};
      openModal('Email & SMS settings',
        '<form data-form="email-settings"><div class="section-label">Email (SMTP)</div><div class="form-grid">' +
        '<label class="field"><span>SMTP server</span><input type="text" name="smtp_host" value="' + esc(em.smtp_host) + '" placeholder="smtp.gmail.com"></label>' +
        '<label class="field"><span>Port</span><input type="number" name="smtp_port" value="' + esc(em.smtp_port || 587) + '"></label>' +
        '<label class="field"><span>Username</span><input type="text" name="smtp_user" value="' + esc(em.smtp_user) + '"></label>' +
        '<label class="field"><span>Password / app password</span><input type="password" name="smtp_password" autocomplete="new-password" placeholder="' + (em.password_from_env ? 'Set on Render (SMTP_PASSWORD)' : em.has_password ? 'Saved – leave blank to keep' : '') + '"></label>' +
        '<label class="field"><span>From email</span><input type="email" name="from_email" value="' + esc(em.from_email) + '"></label>' +
        '<label class="field"><span>From name</span><input type="text" name="from_name" value="' + esc(em.from_name || 'The Tanning Co. Coventry') + '"></label>' +
        '</div><div class="error-text" data-error></div><div class="form-actions"><button class="btn" type="submit">Save email settings</button></div></form>' +
        '<form data-form="sms-settings"><div class="section-label">SMS (Twilio)</div><div class="form-grid">' +
        '<label class="field"><span>Account SID</span><input type="text" name="account_sid" value="' + esc(sm.account_sid) + '"></label>' +
        '<label class="field"><span>Auth token</span><input type="password" name="auth_token" autocomplete="new-password" placeholder="' + (sm.has_token ? 'Saved – leave blank to keep' : '') + '"></label>' +
        '<label class="field"><span>From number or sender name</span><input type="text" name="from_number" value="' + esc(sm.from_number) + '" placeholder="+447700900000"></label>' +
        '</div><div class="error-text" data-error></div><div class="form-actions"><button class="btn" type="submit">Save SMS settings</button></div></form>',
        { wide: true, noFocus: true });
    } catch (e) { fail(e); }
  }

  async function openCampaign(kind) {
    var counts = { email: '?', sms: '?' };
    try { counts = await api('/api/campaign/counts'); } catch (e) { /* ignore */ }
    if (kind === 'email') {
      openModal('Email campaign',
        '<form data-form="email-campaign"><p class="muted mb">Goes to the <b>' + esc(counts.email) + '</b> customers who have agreed to marketing emails. An unsubscribe link is added automatically. Use {name} for the customer\'s name.</p>' +
        '<label class="field"><span>Subject</span><input type="text" name="subject" required maxlength="150"></label>' +
        '<label class="field"><span>Message</span><textarea name="body" required placeholder="Hi {name}, …"></textarea></label>' +
        '<div class="error-text" data-error></div><div data-result></div>' +
        '<div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Close</button><button class="btn" type="submit">Send to ' + esc(counts.email) + ' customers</button></div></form>');
    } else {
      openModal('SMS campaign',
        '<form data-form="sms-campaign"><p class="muted mb">Goes to the <b>' + esc(counts.sms) + '</b> customers who have agreed to marketing texts. “Reply STOP to opt out.” is added if you don\'t include it. Use {name} for the customer\'s name.</p>' +
        '<label class="field"><span>Message</span><textarea name="message" required maxlength="459" data-sms-count></textarea></label>' +
        '<div class="muted small" data-sms-counter>0 characters · 1 text</div>' +
        '<div class="error-text" data-error></div><div data-result></div>' +
        '<div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Close</button><button class="btn" type="submit">Send to ' + esc(counts.sms) + ' customers</button></div></form>');
    }
  }

  // ------------------------------------------------------------------ click actions
  var actions = {
    'close-modal': function () { closeModal(); },
    'toggle-menu': function (el) {
      var menu = $(el.dataset.menu);
      var open = menu.classList.contains('hidden');
      document.querySelectorAll('.menu-list').forEach(function (m) { m.classList.add('hidden'); });
      if (open) menu.classList.remove('hidden');
    },
    site: function (el) { state.site = el.dataset.site; saveSite(state.site); renderBoard(); },
    'new-customer': function () { closeSearch(); closeAllModals(); openModal('New customer', customerForm()); },
    'open-customer': function (el) { openCustomer(Number(el.dataset.id)); },
    'edit-customer': function () { openModal('Edit customer', customerForm(state.customer)); },
    'add-to-account': function () { openModal('Add to ' + state.customer.name + '\'s account', productsHtml(), { wide: true, noFocus: true }); },
    buy: function (el) { buyProduct(Number(el.dataset.id)); },
    'use-minutes': function () { openUseMinutes(state.customer, null); },
    'quick-min': function (el) { el.closest('form').minutes.value = el.dataset.min; },
    'pick-bed': function (el) {
      var form = el.closest('form') || el.closest('.modal');
      form.querySelectorAll('[data-action=pick-bed]').forEach(function (b) { b.classList.remove('selected'); });
      el.classList.add('selected');
      var input = form.querySelector('input[name=room]');
      if (input) input.value = el.dataset.room;
    },
    'start-bed': function (el) { openStartBed(Number(el.dataset.room)); },
    'pick-customer-for-bed': async function (el) {
      try {
        var c = await api('/api/customers/' + el.dataset.id);
        if ((c.remaining_minutes || 0) < 1) {
          state.customer = c;
          toast(c.name + ' has no minutes left – add some to their account first', true);
          openCustomer(c.id);
          return;
        }
        state.customer = c;
        openUseMinutes(c, state.pendingBed);
      } catch (e) { fail(e); }
    },
    'id-check': async function () {
      try { await api('/api/customers/' + state.customer.id + '/id-check', { method: 'POST' }); toast('ID check recorded'); openCustomer(state.customer.id); loadSignups(); }
      catch (e) { fail(e); }
    },
    'set-pin': function () {
      openModal('Set PIN for ' + state.customer.name,
        '<form data-form="set-pin"><p class="muted mb">The customer uses this PIN with their mobile number to check their minutes online. Let them type it in themselves if possible.</p>' +
        '<label class="field"><span>New PIN (4–6 digits)</span><input type="password" name="pin" inputmode="numeric" pattern="[0-9]{4,6}" required autocomplete="off"></label>' +
        '<div class="error-text" data-error></div><div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button><button class="btn" type="submit">Save PIN</button></div></form>');
    },
    'delete-customer': async function () {
      var c = state.customer;
      if (!await confirmBox('Delete customer', 'Delete <b>' + esc(c.name) + '</b> and their details? This can\'t be undone. Their past sales stay in the reports.', 'Delete', true)) return;
      try { await api('/api/customers/' + c.id, { method: 'DELETE' }); closeAllModals(); toast(c.name + ' deleted'); loadSignups(); }
      catch (e) { fail(e); }
    },
    finish: async function (el) {
      var r = state.rooms.find(function (x) { return x.id === Number(el.dataset.room); });
      if (!await confirmBox('Finish session', 'Finish <b>' + esc(r ? r.customer_name : '') + '</b>\'s session on ' + esc(r ? r.name : 'this bed') + '? The bed will be marked for cleaning.', 'Finish')) return;
      try { await api('/api/rooms/' + el.dataset.room + '/finish', { method: 'POST' }); loadRooms(true); }
      catch (e) { fail(e); }
    },
    clean: async function (el) {
      try { await api('/api/rooms/' + el.dataset.room + '/clean', { method: 'POST' }); loadRooms(true); }
      catch (e) { fail(e); }
    },
    waitlist: function (el) { openWaitlist(Number(el.dataset.room)); },
    'waitlist-remove': async function (el) {
      try { await api('/api/waitlist/' + el.dataset.id, { method: 'DELETE' }); closeModal(); openWaitlist(Number(el.dataset.room)); }
      catch (e) { fail(e); }
    },
    'queue-assign': function (el) {
      openModal('Assign a bed', '<div class="modal-queue" data-queue="' + el.dataset.id + '">' + bedPicker(null) + '</div>', { noFocus: true });
      topModal().querySelector('.modal-queue').dataset.queue = el.dataset.id;
    },
    'queue-remove': async function (el) {
      if (!await confirmBox('Remove from queue', 'Remove this customer from the queue?', 'Remove', true)) return;
      try { await api('/api/queue/' + el.dataset.id, { method: 'DELETE' }); loadQueue(); }
      catch (e) { fail(e); }
    },
    report: function () {
      var today = new Date().toISOString().slice(0, 10);
      openModal('Daily report', '<label class="field"><span>Date</span><input type="date" data-report-date value="' + today + '" max="' + today + '"></label><div data-report></div>', { noFocus: true });
      loadReport(today);
    },
    'email-campaign': function () { openCampaign('email'); },
    'sms-campaign': function () { openCampaign('sms'); },
    import: function () {
      openModal('Import customers',
        '<form data-form="import"><p class="muted mb">Excel file with <b>Name</b> and <b>Phone</b> columns (Email optional). Numbers already on the system are skipped. Imported customers are <b>not</b> signed up for marketing until they agree.</p>' +
        '<label class="field"><input type="file" name="file" accept=".xlsx,.xls" required></label>' +
        '<div class="error-text" data-error></div><div data-result></div>' +
        '<div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Close</button><button class="btn" type="submit">Import</button></div></form>');
    },
    settings: openSettings,
    'staff-accounts': openStaffAccounts,
    activity: openActivity,
    'staff-reset': function (el) {
      openModal('Reset password – ' + el.dataset.name,
        '<form data-form="staff-reset" data-id="' + el.dataset.id + '"><label class="field"><span>New password (10+ characters)</span><input type="password" name="password" required minlength="10" autocomplete="new-password"></label>' +
        '<p class="muted small">They\'ll be signed out everywhere and need this password to sign back in.</p>' +
        '<div class="error-text" data-error></div><div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button><button class="btn" type="submit">Reset password</button></div></form>');
    },
    'staff-toggle': async function (el) {
      try { await api('/api/staff/' + el.dataset.id + '/active', { body: { active: el.dataset.active === '1' } }); openStaffAccounts(); }
      catch (e) { fail(e); }
    },
    'change-password': function () {
      openModal('Change my password',
        '<form data-form="change-password"><label class="field"><span>Current password</span><input type="password" name="current_password" required autocomplete="current-password"></label>' +
        '<label class="field"><span>New password (10+ characters)</span><input type="password" name="new_password" required minlength="10" autocomplete="new-password"></label>' +
        '<label class="field"><span>New password again</span><input type="password" name="confirm" required minlength="10" autocomplete="new-password"></label>' +
        '<div class="error-text" data-error></div><div class="form-actions"><button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button><button class="btn" type="submit">Change password</button></div></form>');
    },
    logout: async function () {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
      window.location.href = '/login';
    }
  };

  async function assignQueue(queueId, roomId) {
    try { await api('/api/queue/' + queueId + '/assign', { body: { queue_id: queueId, room_id: roomId } }); closeModal(); loadRooms(true); loadQueue(); toast('Bed assigned'); }
    catch (e) { fail(e); }
  }

  async function openWaitlist(roomId) {
    var r = state.rooms.find(function (x) { return x.id === roomId; });
    try {
      var list = await api('/api/waitlist/' + roomId);
      openModal('Waiting list – ' + (r ? r.name : 'bed'),
        (list.length ? list.map(function (w, i) {
          return '<div class="list-item"><div class="row"><span><b>' + (i + 1) + '.</b> ' + esc(w.customer_name) + ' <span class="muted small">since ' + fmtDateTime(w.created_at) + '</span></span>' +
            '<button class="btn btn-sm btn-danger" data-action="waitlist-remove" data-id="' + w.id + '" data-room="' + roomId + '">Remove</button></div></div>';
        }).join('') : '<div class="empty">No one waiting for this bed</div>') +
        '<form data-form="waitlist-add" data-room="' + roomId + '"><div class="section-label">Add someone</div>' +
        '<label class="field"><input type="text" name="name" required maxlength="80" placeholder="Customer name"></label>' +
        '<div class="form-actions"><button class="btn" type="submit">Add to waiting list</button></div></form>');
    } catch (e) { fail(e); }
  }

  // ------------------------------------------------------------------ form submits
  var forms = {
    'new-customer': async function (f) {
      var data = await api('/api/customers', { body: readCustomerForm(f) });
      toast('Customer created');
      openCustomer(data.id);
    },
    'edit-customer': async function (f) {
      await api('/api/customers/' + f.dataset.id, { method: 'PUT', body: readCustomerForm(f) });
      toast('Changes saved');
      openCustomer(Number(f.dataset.id));
    },
    'use-minutes': async function (f) {
      var minutes = Number(f.minutes.value), room = Number(f.room.value);
      if (!room) throw new Error('Choose a bed');
      if (!minutes) throw new Error('Enter the number of minutes');
      await api('/api/use-minutes', { body: { customer_id: Number(f.dataset.customer), minutes: minutes, room_id: room } });
      closeAllModals();
      state.pendingBed = null;
      toast(minutes + (minutes === 1 ? ' minute' : ' minutes') + ' started');
      loadRooms(true);
    },
    'set-pin': async function (f) {
      await api('/api/customers/' + state.customer.id + '/pin', { body: { pin: f.pin.value } });
      toast('PIN saved');
      openCustomer(state.customer.id);
    },
    'waitlist-add': async function (f) {
      await api('/api/waitlist', { body: { room_id: Number(f.dataset.room), customer_name: f.name.value.trim() } });
      closeModal();
      openWaitlist(Number(f.dataset.room));
    },
    'email-campaign': async function (f) {
      if (!await confirmBox('Send email campaign', 'Send this email now? It can\'t be recalled.', 'Send')) return;
      f.querySelector('[data-result]').innerHTML = '<div class="empty">Sending…</div>';
      var r = await api('/api/email/campaign', { body: { subject: f.subject.value, body: f.body.value } });
      f.querySelector('[data-result]').innerHTML = '<div class="alert warn">Sent: <b>' + r.sent + '</b> · Failed: <b>' + r.failed + '</b>' + (r.errors.length ? '<br>' + r.errors.map(esc).join('<br>') : '') + '</div>';
    },
    'sms-campaign': async function (f) {
      if (!await confirmBox('Send SMS campaign', 'Send this text now? It can\'t be recalled and each text costs credit.', 'Send')) return;
      f.querySelector('[data-result]').innerHTML = '<div class="empty">Sending…</div>';
      var r = await api('/api/sms/campaign', { body: { message: f.message.value } });
      f.querySelector('[data-result]').innerHTML = '<div class="alert warn">Sent: <b>' + r.sent + '</b> · Failed: <b>' + r.failed + '</b>' + (r.errors.length ? '<br>' + r.errors.map(esc).join('<br>') : '') + '</div>';
    },
    import: async function (f) {
      var fd = new FormData();
      fd.append('file', f.file.files[0]);
      f.querySelector('[data-result]').innerHTML = '<div class="empty">Importing…</div>';
      var r = await api('/api/import/excel', { form: fd });
      f.querySelector('[data-result]').innerHTML = '<div class="alert warn">Imported <b>' + r.imported + '</b> · skipped <b>' + r.skipped + '</b> of ' + r.total_rows + ' rows</div>';
    },
    'email-settings': async function (f) {
      await api('/api/email/settings', { body: { smtp_host: f.smtp_host.value, smtp_port: Number(f.smtp_port.value) || 587, smtp_user: f.smtp_user.value, smtp_password: f.smtp_password.value, from_email: f.from_email.value, from_name: f.from_name.value } });
      toast('Email settings saved'); f.smtp_password.value = '';
    },
    'sms-settings': async function (f) {
      await api('/api/sms/settings', { body: { account_sid: f.account_sid.value, auth_token: f.auth_token.value, from_number: f.from_number.value } });
      toast('SMS settings saved'); f.auth_token.value = '';
    },
    'new-staff': async function (f) {
      await api('/api/staff', { body: { display_name: f.display_name.value.trim(), username: f.username.value.trim(), password: f.password.value, role: f.role.value } });
      toast('Staff member added'); openStaffAccounts();
    },
    'staff-reset': async function (f) {
      await api('/api/staff/' + f.dataset.id + '/password', { body: { password: f.password.value } });
      toast('Password reset'); closeModal();
    },
    'change-password': async function (f) {
      if (f.new_password.value !== f.confirm.value) throw new Error('The new passwords don\'t match');
      await api('/api/auth/password', { body: { current_password: f.current_password.value, new_password: f.new_password.value } });
      toast('Password changed'); closeModal();
    }
  };

  // ------------------------------------------------------------------ wiring
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-action]');
    if (!e.target.closest('.menu')) document.querySelectorAll('.menu-list').forEach(function (m) { m.classList.add('hidden'); });
    if (!e.target.closest('.search-wrap')) closeSearch();
    if (!el || el.disabled) return;
    var fn = actions[el.dataset.action];
    if (!fn) return;
    if (el.tagName === 'A') return;
    e.preventDefault();
    if (el.closest('.menu-list')) el.closest('.menu-list').classList.add('hidden');
    // queue bed picker lives in a div, not a form
    if (el.dataset.action === 'pick-bed') {
      var q = el.closest('[data-queue]');
      if (q) { assignQueue(Number(q.dataset.queue), Number(el.dataset.room)); return; }
    }
    fn(el, e);
  });

  document.addEventListener('submit', async function (e) {
    var f = e.target.closest('form[data-form]');
    if (!f) return;
    e.preventDefault();
    var err = f.querySelector('[data-error]');
    if (err) err.textContent = '';
    var btn = f.querySelector('button[type=submit]');
    if (btn) btn.disabled = true;
    try { await forms[f.dataset.form](f); }
    catch (ex) { if (err) err.textContent = ex.message; else fail(ex); }
    if (btn) btn.disabled = false;
  });

  document.addEventListener('input', function (e) {
    if (e.target.id === 'search') return onSearchInput();
    if (e.target.matches('[data-sms-count]')) {
      var len = e.target.value.length;
      e.target.closest('form').querySelector('[data-sms-counter]').textContent = len + ' characters · ' + Math.max(1, Math.ceil(len / 153)) + ' text' + (len > 160 ? 's' : '') + ' each';
    }
    if (e.target.matches('[data-bed-search]')) {
      var q = e.target.value.trim();
      var box = e.target.closest('.modal').querySelector('[data-bed-results]');
      clearTimeout(state.searchTimer);
      if (!q) { box.innerHTML = ''; return; }
      state.searchTimer = setTimeout(async function () {
        try {
          var list = await api('/api/customers?q=' + encodeURIComponent(q));
          box.innerHTML = list.length ? list.map(function (c) {
            return customerResult(c).replace('data-action="open-customer"', 'data-action="pick-customer-for-bed"');
          }).join('') : '<div class="empty">No customers found</div>';
        } catch (ex) { fail(ex); }
      }, 200);
    }
  });

  document.addEventListener('change', function (e) {
    if (e.target.matches('[data-report-date]')) loadReport(e.target.value);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (topModal()) closeModal();
      else closeSearch();
    }
    if (e.key === 'Enter' && e.target.id === 'search') {
      var first = $('searchResults').querySelector('.result');
      if (first) { e.preventDefault(); first.click(); }
    }
    if (e.key === '/' && !e.target.closest('input, textarea, select') && !topModal()) {
      e.preventDefault(); $('search').focus();
    }
  });

  // ------------------------------------------------------------------ start
  async function init() {
    try {
      state.me = await api('/api/auth/me');
    } catch (e) { return; }
    $('userName').textContent = state.me.display_name;
    $('userInitial').textContent = initials(state.me.display_name);
    $('userRole').textContent = isAdmin() ? 'Manager' : 'Staff';
    document.querySelectorAll('.admin-only').forEach(function (el) { el.classList.toggle('hidden', !isAdmin()); });
    renderBoard();
    try { state.products = await api('/api/products'); } catch (e) { /* ignore */ }
    loadRooms(true); loadQueue(); loadSignups();
    setInterval(tick, 1000);
    setInterval(function () { loadRooms(); loadQueue(); }, 8000);
    setInterval(loadSignups, 30000);
  }
  init();
})();
