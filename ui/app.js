/* ==========================================================================
   MOD STUDIO - front end
   Free-tier aware: selections are STAGED, then one Generate = one API call.
   Cache hits apply instantly and never cost a call.
   ========================================================================== */

const $  = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const S = {
  status:   {},
  vtype:    'bike',
  angles:   [],
  rules:    [],
  catalog:  null,
  vehicleId:null,
  label:    '',
  haveAngles:[],
  angle:    'side',
  applied:  {},        // mods currently rendered on screen
  staged:   {},        // mods clicked but not yet generated
  custom:   '',
  stagedCustom:'',
  activeCat:null,
  history:  [],        // {hash,url,mods,custom,angle,label,cached}
  histIdx:  -1,
  compare:  null,
  busy:     false,
};

/* ---------------- utils ---------------- */
const api = async (path, body) => {
  const r = await fetch(path, body
    ? { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) }
    : {});
  const j = await r.json().catch(() => ({ error:'Bad response from the tool backend.' }));
  if (!r.ok || j.error) throw Object.assign(new Error(j.error || ('HTTP ' + r.status)), { hint:j.hint });
  return j;
};

let toastT;
function toast(msg, ms = 2400) {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.add('hidden'), ms);
}

/* ===================== v14 INTERACTION SOUND =====================
   Files live in assets/sfx/. Drop in your own MP3s with the same
   names to replace them - no code changes needed. See SOUNDS.txt.
 * ================================================================= */
const SFX = (function () {
  const NAMES = ['click','hover','open','close','tab','done','error'];
  const VOICES = 3;
  let vol = parseFloat(localStorage.getItem('md_sfx_vol'));
  if (isNaN(vol)) vol = 0.55;
  let muted = localStorage.getItem('md_sfx_mute') === '1';
  const pool = {};
  NAMES.forEach(function (n) {
    const arr = [];
    for (let i = 0; i < VOICES; i++) {
      const a = new Audio('/assets/sfx/' + n + '.mp3');
      a.preload = 'auto'; a.volume = vol; arr.push(a);
    }
    pool[n] = { arr: arr, i: 0 };
  });
  const GAIN = { hover:0.35, click:1, open:0.9, close:0.8, tab:0.7, done:1, error:0.9 };
  function play(name) {
    if (muted || vol <= 0) return;
    const p = pool[name]; if (!p) return;
    const a = p.arr[p.i]; p.i = (p.i + 1) % p.arr.length;
    try {
      a.currentTime = 0;
      a.volume = Math.max(0, Math.min(1, vol * (GAIN[name] || 1)));
      const pr = a.play(); if (pr && pr.catch) pr.catch(function(){});
    } catch (e) {}
  }
  return {
    play: play,
    get volume() { return vol; },
    setVolume: function (v) { vol = Math.max(0, Math.min(1, v)); localStorage.setItem('md_sfx_vol', String(vol)); },
    get muted() { return muted; },
    setMuted: function (m) { muted = !!m; localStorage.setItem('md_sfx_mute', muted ? '1' : '0'); }
  };
})();

function show(name) {
  const _wasOn = (document.querySelector('.screen.on') || {}).id || '';
  if (_wasOn && _wasOn !== 'scr-' + name) SFX.play('tab');
  $$('.screen').forEach(s => s.classList.remove('on'));
  $('#scr-' + name).classList.add('on');
  $$('#stepNav .step').forEach(b => b.classList.toggle('on', b.dataset.goto === name));
  $('#stepNav').style.visibility = (name === 'setup') ? 'hidden' : 'visible';
}

function refreshStat() {
  const s = S.status;
  $('#statPill').textContent =
    `${s.cached_images || 0} cached · ${s.calls_today || 0} calls today`;
  $('#shopName').textContent = s.shop_name || '';
}

/* ---------------- splash ---------------- */
function runSplash() {
  return new Promise(resolve => {
    const el = $('#splash');
    if (!el) return resolve();
    // QA screenshots and reloads should not sit through the animation twice
    if (new URLSearchParams(location.search).has('screen') ||
        sessionStorage.getItem('moddex_splash') === 'seen') {
      el.remove(); return resolve();
    }
    /* ===================== v16 INTRO CUT =====================
       The intro is cut one frame BEFORE its final frame, so the last
       frame never paints. We freeze on that frame, bring the ModDex
       title up over it, then wipe to the app. Frame-accurate via
       requestVideoFrameCallback, with a timeupdate fallback.
     * ========================================================= */
    const vid = document.getElementById('spVideo');
    const brand = document.getElementById('spBrand');
    const FPS = 30;          // intro.mp4 is 30 fps
    const HOLD = 640;        // frozen frame + title held before the wipe
    const WIPE = 520;        // must match the #splash.out animation
    let done = false, revealed = false, cut = false;

    /* one frame before the final frame */
    const cutPoint = () => {
      const d = (vid && isFinite(vid.duration)) ? vid.duration : 0;
      return d ? Math.max(0, d - (2 / FPS)) : 0;
    };

    const reveal = () => {
      if (revealed) return; revealed = true;
      el.classList.add('reveal');
      if (brand) brand.classList.add('revealed');
    };

    const teardown = () => {
      sessionStorage.setItem('moddex_splash', 'seen');
      el.classList.add('out');
      setTimeout(() => { el.remove(); resolve(); }, WIPE);
    };

    /* reached the cut naturally: hold the frame, title in, then wipe */
    const cutNow = () => {
      if (cut || done) return;
      cut = true; done = true;
      if (vid) {
        try {
          vid.pause();
          const t = cutPoint();
          if (t && Math.abs(vid.currentTime - t) > 0.001) vid.currentTime = t;
        } catch (e) {}
      }
      el.classList.add('frozen');
      reveal();
      setTimeout(teardown, HOLD);
    };

    /* user skipped: get out of the way quickly */
    const skip = () => {
      if (done) return; done = true;
      if (vid) { try { vid.pause(); } catch (e) {} }
      reveal();
      setTimeout(teardown, 180);
    };

    if (vid) {
      if (typeof vid.requestVideoFrameCallback === 'function') {
        const step = (now, meta) => {
          if (done) return;
          const t = cutPoint();
          if (t && meta.mediaTime >= t - 0.0005) return cutNow();
          vid.requestVideoFrameCallback(step);
        };
        vid.requestVideoFrameCallback(step);
      }
      /* fallback for browsers without per-frame callbacks */
      vid.addEventListener('timeupdate', () => {
        const t = cutPoint();
        if (t && vid.currentTime >= t - 0.004) cutNow();
      });
      vid.addEventListener('ended', cutNow);
      vid.addEventListener('error', skip);
      const pr = vid.play();
      if (pr && pr.catch) pr.catch(() => { reveal(); setTimeout(teardown, 1400); });
    } else {
      reveal(); setTimeout(teardown, 1400);
    }

    el.addEventListener('click', skip);
    document.addEventListener('keydown', skip, { once:true });
    setTimeout(skip, 9000);   // safety net: never trap the user
  });
}

/* ---------------- cinematic generation loader ----------------
   Only ever shown for real API work. A cache hit stays at 0 ms and
   never sees this screen - that is the whole point of the cache. */
let glTimer = null, glStart = 0;
const GL_STEPS = [
  [0,   'Sending reference'],
  [1.2, 'Reading the vehicle'],
  [2.6, 'Applying modifications'],
  [4.4, 'Rendering surfaces'],
  [7.0, 'Finishing'],
  [11,  'Still working'],
];

function startLoad(title, sub) {
  const el = $('#genLoad');
  if (!el) return;
  $('#glTitle').textContent  = (title || 'Stock').slice(0, 64);
  $('#glVeh').textContent    = sub || '';
  $('#glStatus').textContent = GL_STEPS[0][1];
  $('#glTimer').textContent  = '0.0s';
  $('#glFill').style.width   = '0%';
  el.classList.remove('hidden');

  glStart = performance.now();
  clearInterval(glTimer);
  glTimer = setInterval(() => {
    const t = (performance.now() - glStart) / 1000;
    $('#glTimer').textContent = t.toFixed(1) + 's';
    let lbl = GL_STEPS[0][1];
    for (const [at, txt] of GL_STEPS) if (t >= at) lbl = txt;
    $('#glStatus').textContent = lbl;
    // asymptotic: never reaches 100 until the image actually lands
    $('#glFill').style.width = Math.min(93, 100 * (1 - Math.exp(-t / 4.2))).toFixed(1) + '%';
  }, 100);
}

function stopLoad() {
  clearInterval(glTimer); glTimer = null;
  const el = $('#genLoad');
  if (!el) return;
  $('#glFill').style.width = '100%';
  setTimeout(() => el.classList.add('hidden'), 260);
}

/* ---------------- boot ---------------- */
(async function boot() {
  try {
    S.status = await api('/api/status');
  } catch (e) {
    document.body.innerHTML =
      '<div style="padding:60px;text-align:center;color:#ff9a9a">' +
      'Cannot reach the ModDex backend.<br><br>' +
      'Close this window and re-run ModDex.</div>';
    return;
  }
  const a = await api('/api/angles');
  S.angles = a.angles; S.rules = a.rules;
  refreshStat();
  renderGuide();
  renderSlots();
  // ?screen=guide is used for QA screenshots only
  applyEngineMode();
  const forced = new URLSearchParams(location.search).get('screen');
  const first = (forced === 'guide' ? 'upload' : forced) || (S.status.configured && S.status.shop_name ? 'upload' : 'setup');
  await runSplash();
  show(first);
  if (first !== 'setup' && !new URLSearchParams(location.search).has('noguide')) setTimeout(openGuide, 320);
  $('#setShop').value = S.status.shop_name || '';
  if (first === 'setup' && S.status.builtin_engine) $('#inpShop').focus();
})();

/* When credentials ship inside the app there is nothing to configure, so the
   setup screen collapses down to a single question: what is the shop called. */
function applyEngineMode() {
  if (!S.status.builtin_engine) return;
  const hide = el => el && el.classList.add('hidden');
  const key = $('#inpKey');
  hide(key && key.previousElementSibling);   // its "API key" label
  hide(key);
  hide(document.querySelector('#scr-setup .hintrow'));
  hide($('#vertexBox'));
  hide($('#btnTestKey'));
  $('#engineBox').classList.remove('hidden');
  $('#btnStartBuiltin').classList.remove('hidden');
}

$('#btnStartBuiltin').onclick = async () => {
  const shop = $('#inpShop').value.trim();
  if (!shop) { toast('Enter your shop name first'); $('#inpShop').focus(); return; }
  const b = $('#btnStartBuiltin'); b.disabled = true;
  try {
    await api('/api/settings', { shop_name: shop });
    S.status = await api('/api/status');
    refreshStat();
    show('upload');
  } catch (e) {
    const res = $('#keyResult');
    res.className = 'result show bad';
    res.innerHTML = '<b>' + e.message + '</b>' + (e.hint ? '<br>' + e.hint : '');
  } finally { b.disabled = false; }
};

/* ---------------- setup ---------------- */
$('#btnPeek').onclick = () => {
  const i = $('#inpKey');
  i.type = i.type === 'password' ? 'text' : 'password';
  $('#btnPeek').textContent = i.type === 'password' ? 'show' : 'hide';
};

$('#btnTestKey').onclick = async () => {
  const key = $('#inpKey').value.trim();
  const res = $('#keyResult');
  res.className = 'result show';

  if (!key) { res.classList.add('bad'); res.innerHTML = '<b>Paste your API key first.</b>'; return; }

  // Google AI Studio issues two valid key formats:
  //   AQ.…   current format
  //   AIza…  legacy format, still valid
  // ya29.… is a short-lived OAuth access token and is NOT an API key.
  if (key.startsWith('ya29.')) {
    res.classList.add('bad');
    res.innerHTML = '<b>That is an OAuth access token, not an API key.</b>' +
      'Tokens starting with <code>ya29.</code> expire within the hour.<br><br>' +
      'Go to <b>aistudio.google.com → Get API key → Create API key</b> and copy that value instead.';
    return;
  }
  if (!(key.startsWith('AQ.') || key.startsWith('AIza'))) {
    res.classList.add('bad');
    res.innerHTML = '<b>That does not look like a Gemini API key.</b>' +
      'Keys from AI Studio start with <code>AQ.</code> (current) or <code>AIza</code> (older).<br><br>' +
      'Testing it against Google anyway would just waste time — re-copy it from ' +
      '<b>aistudio.google.com → Get API key</b>.';
    return;
  }

  $('#btnTestKey').disabled = true;
  res.innerHTML = 'Contacting Google…';
  try {
    const r = await api('/api/key/test', { key, shop_name:$('#inpShop').value.trim() });
    res.classList.add('ok');
    res.innerHTML = `<b>Connected.</b>Image model: <code>${r.model}</code><br>` +
      `Available: ${r.image_models.join(', ')}`;
    S.status = await api('/api/status'); refreshStat();
    setTimeout(() => show('upload'), 1100);
  } catch (e) {
    res.classList.add('bad');
    res.innerHTML = `<b>${e.message}</b>${e.hint || ''}`;
  } finally {
    $('#btnTestKey').disabled = false;
  }
};

/* ---------------- capture guide ---------------- */
function renderGuide() {
  $('#angleGrid').innerHTML = S.angles.map((a, i) => {
    const ref   = (a.ref && a.ref[S.vtype]) || '';
    const chips = (a.chips || []).map(c => `<span class="chip">${c}</span>`).join('');
    return `
    <div class="acard">
      <div class="shot">
        ${ref ? `<img class="ref" src="${ref}" alt="${a.label} example">` : ''}
        <span class="anum">${String(i + 1).padStart(2, '0')}</span>
      </div>
      <div class="body">
        <h4>${a.label}</h4>
        <p>${a.why}</p>
        <div class="chips">${chips}</div>
      </div>
    </div>`;
  }).join('');

  const doList = S.rules.filter(r => r.ok), dont = S.rules.filter(r => !r.ok);
  const ul = arr => '<ul>' + arr.map(r => `<li>${r.text}</li>`).join('') + '</ul>';
  $('#rulesDo').className = 'rulecol do';
  $('#rulesDo').innerHTML = '<h3>Do</h3>' + ul(doList);
  $('#rulesDont').className = 'rulecol dont';
  $('#rulesDont').innerHTML = "<h3>Don't</h3>" + ul(dont);
}

$$('#vtypeToggle button, #vtypeToggle2 button').forEach(b => {
  b.onclick = () => {
    S.vtype = b.dataset.vtype;
    $$('#vtypeToggle button, #vtypeToggle2 button')
      .forEach(x => x.classList.toggle('on', x.dataset.vtype === S.vtype));
    renderGuide();          // reference photos differ per vehicle type
    renderSlots();          // ...and so do the ghosted upload targets
  };
});

/* ---------------- v11 capture guide modal ---------------- */
function gmEl() { return $('#guideModal'); }
function openGuide() {
  const M = gmEl(); if (!M) return;
  M.classList.remove('hidden');
  document.body.classList.add('gmopen');
  requestAnimationFrame(() => M.classList.add('in'));
  const cl = $('#gmClose'); if (cl) cl.focus();
}
function closeGuide() {
  const M = gmEl(); if (!M || M.classList.contains('hidden')) return;
  M.classList.remove('in');
  document.body.classList.remove('gmopen');
  setTimeout(() => M.classList.add('hidden'), 340);
}
$('#btnHelp').onclick = openGuide;
$('#gmClose').onclick = closeGuide;
$('#gmBack').onclick  = closeGuide;
$('#gmGotIt').onclick = closeGuide;
document.addEventListener('keydown', e => {
  const M = gmEl();
  if (e.key === 'Escape' && M && !M.classList.contains('hidden')) closeGuide();
});
$$('#stepNav .step').forEach(b => b.onclick = () => {
  if (b.dataset.goto === 'studio' && !S.vehicleId) return toast('Upload a vehicle photo first.');
  show(b.dataset.goto);
});

$('#btnUseExample').onclick = async () => {
  try {
    const ex = S.vtype === 'car' ? 'demo_car' : 'demo_bike';
    const r = await api('/api/vehicle/use_example',
      { example_id: ex, vtype:S.vtype, label:'Example ' + S.vtype });
    S.vehicleId = r.vehicle_id; S.haveAngles = r.angles;
    S.label = 'Example ' + S.vtype;
    await openStudio();
  } catch (e) { toast(e.message); }
};

/* ---------------- upload ---------------- */
function renderSlots() {
  $('#slotGrid').innerHTML = S.angles.map(a => {
    const ref = (a.ref && a.ref[S.vtype]) || '';
    return `
    <div class="slot" data-angle="${a.id}" id="slot-${a.id}">
      ${ref ? `<img class="ghostref" src="${ref}" alt="">` : ''}
      <div class="sglyph">⬆</div>
      <div class="slabel">${a.label}</div>
      <div class="shint">Click, or drag a photo here</div>
    </div>`;
  }).join('');

  $$('.slot').forEach(sl => {
    sl.onclick = () => pickFile(sl.dataset.angle);
    sl.ondragover = e => { e.preventDefault(); sl.classList.add('drag'); };
    sl.ondragleave = () => sl.classList.remove('drag');
    sl.ondrop = e => {
      e.preventDefault(); sl.classList.remove('drag');
      if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0], sl.dataset.angle);
    };
  });
}

function pickFile(angle) {
  const i = document.createElement('input');
  i.type = 'file'; i.accept = 'image/png,image/jpeg,image/webp';
  i.onchange = () => i.files[0] && handleFile(i.files[0], angle);
  i.click();
}

/* Downscale in-browser to 1536px long edge: faster uploads, faster generation,
   and it keeps us well inside free-tier request size limits. */
function downscale(file, max = 1536) {
  return new Promise((res, rej) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width:w, height:h } = img;
      if (Math.max(w, h) > max) { const k = max / Math.max(w, h); w = Math.round(w*k); h = Math.round(h*k); }
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').drawImage(img, 0, 0, w, h);
      res(c.toDataURL('image/jpeg', 0.93));
    };
    img.onerror = () => { URL.revokeObjectURL(url); rej(new Error('Could not read that image file.')); };
    img.src = url;
  });
}

async function handleFile(file, angle) {
  const sl = $('#slot-' + angle);
  sl.innerHTML = '<div class="slabel">Processing…</div>';
  try {
    const b64 = await downscale(file);
    const r = await api('/api/vehicle/upload', {
      image_b64:b64, angle, vehicle_id:S.vehicleId,
      vtype:S.vtype, label:$('#inpLabel').value.trim() || 'Customer vehicle'
    });
    S.vehicleId = r.vehicle_id;
    if (!S.haveAngles.includes(angle)) S.haveAngles.push(angle);
    const a = S.angles.find(x => x.id === angle);
    sl.classList.add('filled');
    sl.innerHTML = `<img src="${r.url}?t=${Date.now()}">` +
      `<div class="slabel"><span>${a.label}</span><button class="redo">Replace</button></div>`;
    sl.querySelector('.redo').onclick = e => { e.stopPropagation(); pickFile(angle); };
    $('#btnGoStudio').disabled = false;
  } catch (e) {
    toast(e.message);
    renderSlots();
  }
}

$('#btnGoStudio').onclick = () => {
  S.label = $('#inpLabel').value.trim() || 'Customer vehicle';
  openStudio();
};

/* ---------------- studio ---------------- */
async function openStudio() {
  S.catalog = await api('/api/catalog?type=' + S.vtype);
  try { S.products = (await api('/api/products?type=' + S.vtype)).products || []; }
  catch (e) { S.products = []; }
  S.applied = {}; S.staged = {}; S.custom = ''; S.stagedCustom = '';
  S.history = []; S.histIdx = -1; S.compare = null;
  S.activeCat = (S.catalog.categories[0] || {}).id || null;
  S.angle = S.haveAngles.includes('side') ? 'side' : (S.haveAngles[0] || 'side');
  $('#vehName').textContent = S.label + '  ·  ' + (S.catalog.label);
  renderCats(); renderAnglePills(); renderVariants(); renderBuild(); renderHistory();
  show('studio');
  await generate(true);   // show the stock photo, costs nothing
}

/* ===================== v15 CATEGORY ICONS =====================
   One icon per mod category. Outline inherits the label colour,
   elements marked .af / .as pick up the crimson accent.
 * ============================================================= */
const CAT_ICONS = {
  /* v17 PRICING + BACKGROUNDS */
  'background': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="8.6" cy="10" r="1.7" fill="var(--acc)" stroke="none"/><path d="M3 16.4l4.6-4.1 3.3 2.9 3.1-3.4L21 16.1"/></svg>',
  'paint': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="11" height="6" rx="1.6"/><path d="M14 8h3.4a1.6 1.6 0 0 1 1.6 1.6V12"/><rect x="16.6" y="12" width="4.8" height="7.4" rx="1.5" class="af"/><path d="M5.5 11v3.2"/></svg>',
  'wheels': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.4"/><circle cx="12" cy="12" r="3" class="af"/><path d="M12 3.6v5.4M12 15v5.4M3.6 12h5.4M15 12h5.4"/></svg>',
  'bodykit': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13.5l2-4.2A2 2 0 0 1 6.8 8h8.6l3.4 3.4 2.2.7v3.4H3z"/><circle cx="7.4" cy="16.8" r="1.9"/><circle cx="16.6" cy="16.8" r="1.9"/><path d="M3.4 18.9h17.2" class="as"/></svg>',
  'spoiler': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2.6" y="6.4" width="18.8" height="3.2" rx="1.2" class="af"/><path d="M6.6 9.6v4.2M17.4 9.6v4.2M4 17.6h16"/></svg>',
  'tint': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7.4l3.4-3h9.2l3.4 3v9.2a1.8 1.8 0 0 1-1.8 1.8H5.8A1.8 1.8 0 0 1 4 16.6z"/><path d="M4.4 11.4h15.2" class="as"/><path d="M7 15h4"/></svg>',
  'lights': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8.2a4 4 0 0 1 4-4h1.6v15.6H8a4 4 0 0 1-4-4z"/><circle cx="7.6" cy="12" r="2" class="af"/><path d="M12.6 7.6h7.8M12.6 12h6M12.6 16.4h7.8"/></svg>',
  'grille': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2.8" y="6.6" width="18.4" height="10.8" rx="2.4"/><path d="M7.2 9.4v5.2M12 9.4v5.2M16.8 9.4v5.2"/><path d="M2.8 12h18.4" class="as"/></svg>',
  'exhaust': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="9" width="11.6" height="3.2" rx="1.6"/><rect x="3" y="13.4" width="11.6" height="3.2" rx="1.6"/><circle cx="16.4" cy="10.6" r="1.5" class="af"/><path d="M18.6 15.4c1.8-.6 1.4-2.6 3-3"/></svg>',
  'decals': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.4 4.6h11.4l4 4v11a1.6 1.6 0 0 1-1.6 1.6H4.4A1.6 1.6 0 0 1 2.8 19.6V6.2a1.6 1.6 0 0 1 1.6-1.6z"/><path d="M15.8 4.6v4h4" class="as"/><path d="M7.4 16.2c1.6-4.4 4.4-6 8.2-6" class="af-s"/><circle cx="7" cy="11" r="1.4" class="af"/></svg>',
  'seat': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.4 13.4c2.6-3.4 6.4-4.6 11-4.6 3 0 5 .8 6.2 2.2-1 2.2-3 3.4-6 3.6"/><path d="M3.4 13.4c0 2 1.2 3 3.4 3h5" class="as"/><path d="M14.6 8.8V6.6" class="af-s"/><circle cx="14.6" cy="5.4" r="1.3" class="af"/></svg>',
  'handlebar': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 18.4v-6.2"/><path d="M4.4 8.4c2.6 0 4.4 1.4 7.6 1.4s5-1.4 7.6-1.4"/><rect x="2.2" y="6.6" width="3.4" height="3.6" rx="1.4" class="af"/><rect x="18.4" y="6.6" width="3.4" height="3.6" rx="1.4" class="af"/></svg>',
  'headlight': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.4" cy="12" r="6.4"/><circle cx="10.4" cy="12" r="2.6" class="af"/><path d="M18.6 8.2l3-1.4M19.4 12h3.2M18.6 15.8l3 1.4"/></svg>',
  'taillight': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.6 7.6h9.6a5 5 0 0 1 0 8.8H4.6z" class="af"/><path d="M7.4 10.4h4M7.4 13.6h4" class="as-w"/><path d="M19.6 9.4v5.2"/></svg>',
  'fender': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.6 15.6a8.4 8.4 0 0 1 16.8 0"/><circle cx="12" cy="15.6" r="4.4"/><circle cx="12" cy="15.6" r="1.5" class="af"/><path d="M3.6 15.6h2.6M17.8 15.6h2.6" class="as"/></svg>',
  'guards': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6.4 4.8v14.4M17.6 4.8v14.4"/><path d="M6.4 9.2h11.2M6.4 14.8h11.2" class="as"/><rect x="9.6" y="10.4" width="4.8" height="3.2" rx="1" class="af"/></svg>',
  '_': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14.6 6.2a3.6 3.6 0 1 0 4.2 4.2l-8.8 8.8a2.4 2.4 0 0 1-3.4-3.4z"/><circle cx="8.2" cy="17.4" r="1.2" class="af"/></svg>'
};
function catIcon(id) {
  return '<span class="cico" aria-hidden="true">' + (CAT_ICONS[id] || CAT_ICONS['_']) + '</span>';
}

function renderCats() {
  $('#catBar').innerHTML = S.catalog.categories.map(c => {
    const sel = S.staged[c.id] !== undefined ? S.staged[c.id] : S.applied[c.id];
    let pip = '';
    if (sel) {
      const v = (c.variants || []).find(x => x.id === sel);
      if (v && v.swatch) {
        pip = `<i class="cpip" style="background:${v.swatch}"></i>`;
      } else {
        const prod = (S.products || []).find(x => 'shop:' + x.product_id === sel);
        pip = (prod && prod.refs && prod.refs[0])
          ? `<i class="cpip" style="background-image:url('${prod.refs[0]}')"></i>`
          : '<i class="cpip solid"></i>';
      }
    }
    return `<button class="catbtn ${sel ? 'has' : ''}" data-cat="${c.id}">
      <span class="cwrap">${catIcon(c.id)}${pip}</span><span class="clab">${c.label}</span></button>`;
  }).join('');
  $$('.catbtn').forEach(b => b.onclick = () => {
    S.activeCat = b.dataset.cat;
    $$('.catbtn').forEach(x => x.classList.toggle('on', x.dataset.cat === S.activeCat));
    renderVariants();
  });
  if (S.activeCat) $$('.catbtn').forEach(x => x.classList.toggle('on', x.dataset.cat === S.activeCat));
}

function renderAnglePills() {
  $('#anglePills').innerHTML = S.angles.map(a => {
    const have = S.haveAngles.includes(a.id);
    return `<button class="apill ${a.id === S.angle ? 'on' : ''}" data-a="${a.id}" ${have ? '' : 'disabled'}>${a.short}</button>`;
  }).join('');
  $$('.apill').forEach(b => b.onclick = async () => {
    S.angle = b.dataset.a; renderAnglePills(); await generate();
  });
}

function renderVariants() {
  if (!S.activeCat) {
    $('#varHead').textContent = 'Select a category';
    $('#varList').innerHTML = '<div class="varempty">Pick a category on the left, then choose a look.<br><br>Selections stage up — press <b>Generate</b> once and all of them render in a single API call.</div>';
    return;
  }
  const c = S.catalog.categories.find(x => x.id === S.activeCat);
  if (!c) { S.activeCat = null; renderVariants(); return; }   // stale category after a bike/car switch
  $('#varHead').textContent = c.label;
  const cur = S.staged[c.id] !== undefined ? S.staged[c.id] : S.applied[c.id];
  const mine = (S.products || []).filter(p => p.category === c.id);
  $('#varList').innerHTML =
    mine.map(p => {
      const id = 'shop:' + p.product_id;
      return `<button class="varbtn ${cur === id ? 'on' : ''}" data-v="${id}" title="${esc(p.spec || '')}">
        ${p.refs && p.refs[0] ? `<span class="sw" style="background-image:url('${p.refs[0]}');background-size:cover"></span>` : ''}
        <span>${esc(p.label)}</span>
        <span class="vprice">${priceTag(p.price)}</span>
        <span class="badge-shop">IN STOCK</span>
      </button>`;
    }).join('') +
    c.variants.map(v => `
    <button class="varbtn ${cur === v.id ? 'on' : ''}" data-v="${v.id}">
      ${v.swatch ? `<span class="sw" style="background:${v.swatch}"></span>` : ''}
      <span>${v.label}</span>
      <span class="vprice">${priceTag(v.price)}</span>
    </button>`).join('') +
    (cur ? `<button class="varbtn" data-v="__clear__" style="color:var(--mut)">✕ Remove ${c.label}</button>` : '');

  $$('#varList .varbtn').forEach(b => b.onclick = () => {
    const v = b.dataset.v;
    if (v === '__clear__') delete S.staged[S.activeCat], delete S.applied[S.activeCat];
    else S.staged[S.activeCat] = (cur === v) ? undefined : v;
    if (S.staged[S.activeCat] === undefined) delete S.staged[S.activeCat];
    renderVariants(); renderCats(); renderBuild();
    tryInstant();          // if this exact combo is already cached, show it free
  });
}

function mergedMods() { return Object.assign({}, S.applied, S.staged); }
function mergedCustom() { return S.stagedCustom || S.custom; }

/* ===================== v17 PRICING =====================
   Prices live in catalog/mods_car.json and catalog/mods_bike.json ("price",
   whole rupees) and, for the shop's own inventory, in the products table.
   Everything here is an ESTIMATE the shop can talk over, never an invoice.
 * ======================================================= */
function inr(n) {
  const v = Math.round(Number(n) || 0);
  return '\u20b9' + v.toLocaleString('en-IN');
}

/* null  -> we genuinely do not know the price (quote on ask)
   0     -> deliberately free (backgrounds are a presentation choice) */
function priceOf(catId, vid) {
  if (!S.catalog || !vid) return null;
  if (String(vid).indexOf('shop:') === 0) {
    const p = (S.products || []).find(x => 'shop:' + x.product_id === vid);
    const n = p ? (Number(p.price) || 0) : 0;
    return n > 0 ? n : null;
  }
  const c = S.catalog.categories.find(x => x.id === catId);
  const v = c && c.variants.find(x => x.id === vid);
  if (!v || v.price === undefined || v.price === null) return null;
  return Number(v.price) || 0;
}

function priceTag(p) {
  if (p === undefined || p === null || p === '') return '<i class="pask">on ask</i>';
  const n = Number(p) || 0;
  return n > 0 ? inr(n) : 'Included';
}

/* Live total for whatever is on the vehicle right now, staged or applied. */
function renderBill() {
  const box = $('#billBox');
  if (!box) return;
  const m = mergedMods(), keys = Object.keys(m);
  let total = 0, chargeable = 0, onask = 0, freebies = 0;
  keys.forEach(k => {
    const p = priceOf(k, m[k]);
    if (p === null) { onask++; return; }
    total += p;
    if (p > 0) chargeable++; else freebies++;
  });
  const custom = mergedCustom();
  if (!keys.length && !custom) { box.innerHTML = ''; box.classList.add('hidden'); return; }
  box.classList.remove('hidden');

  const notes = [];
  if (onask)    notes.push(onask + ' on ask');
  if (freebies) notes.push(freebies + ' no charge');
  if (custom)   notes.push('custom not priced');

  box.innerHTML =
    '<div class="billtop"><span class="bl">Total estimate</span>' +
      '<span class="bt">' + inr(total) + '</span></div>' +
    '<div class="billsub">' + chargeable + ' chargeable item' + (chargeable === 1 ? '' : 's') +
      (notes.length ? ' \u00b7 ' + notes.join(' \u00b7 ') : '') + '</div>' +
    '<div class="billnote">Indicative estimate at current shop rates. Final quote depends on ' +
      'fitment time, vehicle condition and taxes.</div>';
}

function renderBuild() {
  const m = mergedMods(), keys = Object.keys(m);
  const dirty = Object.keys(S.staged).length > 0 || S.stagedCustom;
  if (!keys.length && !mergedCustom()) {
    $('#buildList').innerHTML = '<span class="muted">Stock — no mods</span>';
  } else {
    $('#buildList').innerHTML = keys.map(k => {
      const c = S.catalog.categories.find(x => x.id === k);
      const v = c && c.variants.find(x => x.id === m[k]);
      const pend = S.staged[k] !== undefined;
      return `<div class="bbitem"><span>${v ? v.label : m[k]}
        <span class="bbcat">${c ? c.label : ''}${pend ? ' · pending' : ''}</span></span>
        <span class="bbp">${priceTag(priceOf(k, m[k]))}</span>
        <span class="x" data-rm="${k}">✕</span></div>`;
    }).join('') + (mergedCustom()
      ? `<div class="bbitem"><span>“${mergedCustom()}”<span class="bbcat">custom</span></span>
         <span class="x" data-rm="__custom__">✕</span></div>` : '');
    $$('#buildList .x').forEach(x => x.onclick = () => {
      const k = x.dataset.rm;
      if (k === '__custom__') { S.custom = ''; S.stagedCustom = ''; $('#inpAsk').value = ''; }
      else { delete S.applied[k]; delete S.staged[k]; }
      renderBuild(); renderCats(); renderVariants(); tryInstant();
    });
  }
  const cnt = keys.length + (mergedCustom() ? 1 : 0);
  const badge = $('#bbCount');
  if (badge) badge.textContent = cnt ? String(cnt) : '';
  renderBill();
  const btn = $('#btnAsk');
  btn.textContent = dirty ? 'Generate →' : 'Generate';
  btn.style.filter = dirty ? 'brightness(1.25)' : '';
}

/* If the staged combo is already in the cache, apply it instantly for free. */
async function tryInstant() {
  if (!S.vehicleId) return;
  const state = { vehicle_id:S.vehicleId, vtype:S.vtype, angle:S.angle,
                  mods:mergedMods(), custom:mergedCustom() };
  try {
    const r = await api('/api/hash', state);
    if (r.cached) await generate();
  } catch (_) {}
}

$('#btnAsk').onclick = async () => {
  S.stagedCustom = $('#inpAsk').value.trim();
  await generate();
};
$('#inpAsk').addEventListener('keydown', e => { if (e.key === 'Enter') $('#btnAsk').click(); });

async function generate(silent, regen) {
  if (S.busy) return;
  S.busy = true;
  $('#errBox').classList.add('hidden');

  const mods = mergedMods(), custom = mergedCustom();
  const nMods = Object.keys(mods).length;
  let showingLoad = false;

  // Pre-check the cache so we only show the shimmer for real API work.
  let willCall = false;
  try {
    const pre = await api('/api/hash',
      { vehicle_id:S.vehicleId, vtype:S.vtype, angle:S.angle, mods, custom });
    willCall = !pre.cached && (nMods > 0 || custom);
  } catch (_) { willCall = true; }
  if (regen) willCall = (nMods > 0 || custom);

  if (willCall && !silent) {
    const angleLabel = (S.angles.find(a => a.id === S.angle) || {}).label || '';
    startLoad(describe({ mods, custom }), S.label + (angleLabel ? '  ·  ' + angleLabel : ''));
    showingLoad = true;
  }

  try {
    const prev = S.history[S.histIdx];
    const r = await api('/api/generate', {
      vehicle_id:S.vehicleId, vtype:S.vtype, angle:S.angle,
      mods, custom, prev_hash: prev ? prev.hash : null, regen: !!regen
    });

    $('#mainImg').src = r.url + '?h=' + r.hash;
    const b = $('#cacheBadge');
    b.className = 'badge ' + (r.original ? 'stock' : (r.cached ? 'cache' : 'fresh'));
    b.textContent = r.original ? 'Stock photo'
                  : r.cached  ? 'From cache · free'
                              : `New · ${(r.ms/1000).toFixed(1)}s`;
    b.classList.remove('hidden');

    // commit staged -> applied
    S.applied = mods; S.staged = {};
    if (custom) S.custom = custom;
    S.stagedCustom = '';

    pushHistory({ hash:r.hash, url:r.url, mods:Object.assign({}, mods),
                  custom, angle:S.angle, cached:r.cached, original:!!r.original });

    S.status = await api('/api/status'); refreshStat();
    renderCats(); renderVariants(); renderBuild();
    SFX.play(showingLoad ? 'done' : 'click');
  } catch (e) {
    const eb = $('#errBox');
    eb.innerHTML = `<b>${e.message}</b>${e.hint || ''}`;
    eb.classList.remove('hidden');
    SFX.play('error');
    S.staged = {};
    renderVariants(); renderBuild();
  } finally {
    if (showingLoad) stopLoad();
    S.busy = false;
  }
}

/* ---------------- history ---------------- */
function pushHistory(item) {
  const dup = S.history.findIndex(h => h.hash === item.hash);
  if (dup >= 0) { S.histIdx = dup; renderHistory(); return; }
  S.history.push(item); S.histIdx = S.history.length - 1;
  renderHistory();
}

function renderHistory() {
  $('#histStrip').innerHTML = S.history.map((h, i) => `
    <img class="hthumb ${i === S.histIdx ? 'on' : ''} ${S.compare === i ? 'pick' : ''}"
         src="${h.url}" data-i="${i}" title="${describe(h)}">`).join('')
    || '<span class="muted" style="font-size:12px">Every look you show the customer is saved here. Tap to jump back — instantly, and always the exact same image.</span>';

  $$('.hthumb').forEach(t => {
    t.onclick = () => {
      const i = +t.dataset.i;
      if (S.compare !== null && S.compare !== i) { doCompare(S.compare, i); return; }
      restore(i);
    };
  });
}

function describe(h) {
  const parts = Object.entries(h.mods).map(([k, v]) => {
    const c = S.catalog.categories.find(x => x.id === k);
    const vv = c && c.variants.find(x => x.id === v);
    return vv ? vv.label : v;
  });
  if (h.custom) parts.push('“' + h.custom + '”');
  return parts.length ? parts.join(' + ') : 'Stock';
}

/* Restoring is a pure cache read - no API call, pixel-identical to before. */
function restore(i) {
  const h = S.history[i];
  S.histIdx = i;
  S.applied = Object.assign({}, h.mods); S.staged = {};
  S.custom = h.custom || ''; S.stagedCustom = '';
  $('#inpAsk').value = S.custom;
  S.angle = h.angle;
  $('#mainImg').src = h.url;
  $('#cmpImg').classList.add('hidden'); $('#cmpDivider').classList.add('hidden');
  const b = $('#cacheBadge');
  b.className = 'badge cache'; b.textContent = 'From cache · free'; b.classList.remove('hidden');
  renderCats(); renderAnglePills(); renderVariants(); renderBuild(); renderHistory();
}

$('#btnCompare').onclick = () => {
  if (S.compare !== null) {
    S.compare = null;
    $('#cmpImg').classList.add('hidden'); $('#cmpDivider').classList.add('hidden');
    $('#btnCompare').textContent = 'Compare';
    renderHistory(); return;
  }
  if (S.history.length < 2) return toast('Generate at least two looks first.');
  S.compare = S.histIdx;
  $('#btnCompare').textContent = 'Pick 2nd…';
  toast('Now tap another thumbnail to compare against it.');
  renderHistory();
};

function doCompare(a, b) {
  $('#mainImg').src = S.history[a].url;
  $('#cmpImg').src  = S.history[b].url;
  $('#cmpImg').classList.remove('hidden');
  $('#cmpDivider').classList.remove('hidden');
  S.compare = null;
  $('#btnCompare').textContent = 'Exit compare';
  S.compare = null;
  renderHistory();
  toast(describe(S.history[a]) + '   |   ' + describe(S.history[b]), 4200);
}

$('#btnReset').onclick = async () => {
  S.applied = {}; S.staged = {}; S.custom = ''; S.stagedCustom = '';
  $('#inpAsk').value = '';
  renderCats(); renderVariants(); renderBuild();
  await generate(true);
};

/* ---------------- export ---------------- */
async function currentCanvas() {
  const img = $('#mainImg');
  await img.decode().catch(() => {});
  const c = document.createElement('canvas');
  c.width = img.naturalWidth || 1280; c.height = (img.naturalHeight || 860) + 92;
  const x = c.getContext('2d');
  x.fillStyle = '#0b0d10'; x.fillRect(0, 0, c.width, c.height);
  x.drawImage(img, 0, 0);
  x.fillStyle = '#f43f5e'; x.font = 'bold 26px Arial';
  x.fillText(S.status.shop_name || 'MODDEX', 26, c.height - 54);
  x.fillStyle = '#8b95a3'; x.font = '17px Arial';
  x.fillText(S.label + '  —  ' + describe({ mods:S.applied, custom:S.custom }).slice(0, 96),
             26, c.height - 24);
  return c;
}

$('#btnSavePng').onclick = async () => {
  const c = await currentCanvas();
  await api('/api/export',
    { image_b64:c.toDataURL('image/png'), filename:S.label + '_' + Date.now() });
  toast('Saved to the exports folder.');
};

$('#btnSheet').onclick = async () => {
  if (!S.history.length) return toast('Nothing to export yet.');
  const cols = Math.min(3, S.history.length);
  const rows = Math.ceil(S.history.length / cols);
  const cw = 640, ch = 430;
  const c = document.createElement('canvas');
  c.width = cols * cw; c.height = rows * ch + 96;
  const x = c.getContext('2d');
  x.fillStyle = '#0b0d10'; x.fillRect(0, 0, c.width, c.height);
  x.fillStyle = '#f43f5e'; x.font = 'bold 34px Arial';
  x.fillText(S.status.shop_name || 'MODDEX', 30, 52);
  x.fillStyle = '#8b95a3'; x.font = '20px Arial';
  x.fillText(S.label, 30, 80);

  await Promise.all(S.history.map((h, i) => new Promise(res => {
    const im = new Image();
    im.onload = () => {
      const px = (i % cols) * cw, py = Math.floor(i / cols) * ch + 96;
      const k = Math.min(cw / im.width, (ch - 46) / im.height);
      x.drawImage(im, px + (cw - im.width*k)/2, py + 8, im.width*k, im.height*k);
      x.fillStyle = '#e8edf3'; x.font = '17px Arial';
      x.fillText((i+1) + '. ' + describe(h).slice(0, 46), px + 16, py + ch - 12);
      res();
    };
    im.onerror = res;
    im.src = h.url;
  })));

  await api('/api/export',
    { image_b64:c.toDataURL('image/png'), filename:'sheet_' + S.label + '_' + Date.now() });
  toast('Contact sheet saved to the exports folder.');
};

$('#btnWhats').onclick = () => {
  const txt = `*${S.status.shop_name || 'ModDex'}*\n${S.label}\n\nBuild:\n` +
    (Object.keys(S.applied).length
      ? Object.entries(S.applied).map(([k, v]) => {
          const c = S.catalog.categories.find(x => x.id === k);
          const vv = c && c.variants.find(x => x.id === v);
          return `• ${c ? c.label : k}: ${vv ? vv.label : v}`;
        }).join('\n')
      : '• Stock') +
    (S.custom ? `\n• Custom: ${S.custom}` : '');
  window.open('https://wa.me/?text=' + encodeURIComponent(txt), '_blank');
};


/* ---------------- vertex (setup screen) ---------------- */
const vtx = document.querySelector('#btnTestVertex');
if (vtx) vtx.onclick = async () => {
  const res = $('#keyResult'); res.className = 'result show';
  res.innerHTML = 'Checking gcloud, credentials and Vertex access...';
  try {
    const r = await api('/api/vertex/test', {
      project: $('#inpProj').value.trim(),
      location: $('#inpLoc').value.trim() || 'us-central1',
      shop_name: $('#inpShop').value.trim()
    });
    res.classList.add('ok');
    res.innerHTML = '<b>Vertex AI connected.</b>project ' + r.project + '<br>region ' + r.location + '<br>model ' + r.model;
    S.status = await api('/api/status');
    setTimeout(() => show('upload'), 900);
  } catch (e) {
    res.classList.add('bad');
    res.innerHTML = '<b>' + e.message + '</b>' + (e.hint ? '<br>' + e.hint : '');
  }
};

/* ---------------- settings ---------------- */
$('#btnSettings').onclick = async () => {
  S.status = await api('/api/status');
  $('#setShop').value = S.status.shop_name || '';
  $('#setMode').value = S.status.edit_mode || 'cumulative';
  $('#setCap').value  = S.status.cache_cap_mb || 5000;
  $('#setAuth').value = S.status.auth_mode || 'aistudio';
  $('#setProj').value = S.status.gcp_project || '';
  $('#setLoc').value  = S.status.gcp_location || 'us-central1';
  $('#dStats').innerHTML =
    `engine        ${S.status.auth_mode === 'vertex' ? 'Vertex AI (' + S.status.gcp_project + ')' : 'AI Studio key'}<br>` +
    `model         ${S.status.model}<br>` +
    `cached images ${S.status.cached_images}<br>` +
    `cache size    ${S.status.cache_mb} MB<br>` +
    `calls today   ${S.status.calls_today}<br>` +
    `calls total   ${S.status.calls_total}`;
  $('#drawer').classList.remove('hidden');
};
$('#btnCloseDrawer').onclick = () => $('#drawer').classList.add('hidden');
$('#drawer').onclick = e => { if (e.target.id === 'drawer') $('#drawer').classList.add('hidden'); };

$('#btnSaveSettings').onclick = async () => {
  const res = $('#setResult'); res.className = 'result show';
  try {
    const k = $('#setKey').value.trim();
    if (k) {
      if (k.startsWith('ya29.')) throw new Error('That is an OAuth access token, not an API key. Use the value from AI Studio → Get API key.');
      if (!(k.startsWith('AQ.') || k.startsWith('AIza'))) throw new Error('Gemini API keys start with AQ. or AIza.');
      await api('/api/key/test', { key:k });
    }
    await api('/api/settings', {
      shop_name:$('#setShop').value.trim(),
      edit_mode:$('#setMode').value,
      cache_cap_mb:+$('#setCap').value || 5000,
      auth_mode:$('#setAuth').value,
      gcp_project:$('#setProj').value.trim(),
      gcp_location:$('#setLoc').value.trim() || 'us-central1'
    });
    S.status = await api('/api/status'); refreshStat();
    res.classList.add('ok'); res.innerHTML = '<b>Saved.</b>';
    $('#setKey').value = '';
  } catch (e) {
    res.classList.add('bad'); res.innerHTML = `<b>${e.message}</b>${e.hint || ''}`;
  }
};


$('#btnTestVertex2').onclick = async () => {
  const res = $('#setResult'); res.className = 'result show';
  res.innerHTML = 'Checking gcloud, credentials and Vertex access...';
  try {
    const r = await api('/api/vertex/test', {
      project: $('#setProj').value.trim(),
      location: $('#setLoc').value.trim() || 'us-central1'
    });
    $('#setAuth').value = 'vertex';
    $('#setProj').value = r.project;
    $('#setLoc').value  = r.location;
    S.status = await api('/api/status'); refreshStat();
    res.classList.add('ok');
    res.innerHTML = '<b>Vertex AI connected.</b>project ' + r.project + '<br>model ' + r.model;
  } catch (e) {
    res.classList.add('bad');
    res.innerHTML = '<b>' + e.message + '</b>' + (e.hint ? '<br>' + e.hint : '');
  }
};

$('#btnClearCache').onclick = async () => {
  if (!confirm('Delete every cached image? Looks you have already shown customers will need regenerating (and will cost API calls again).')) return;
  await api('/api/cache/clear', {});
  S.status = await api('/api/status'); refreshStat();
  toast('Cache cleared.');
};
$('#btnOpenExports').onclick = () => api('/api/open_folder', { which:'exports' }).catch(() => {});
$('#btnQuit').onclick = async () => {
  if (!confirm('Quit ModDex?')) return;
  await api('/api/shutdown', {}).catch(() => {});
  window.close();
};


/* ---------------- shop products ---------------- */
function esc(t) {
  return String(t == null ? '' : t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function loadProdCats() {
  const vt = $('#pVtype').value;
  let cats = [];
  try { cats = (await api('/api/catalog?type=' + vt)).categories || []; } catch (e) {}
  $('#pCat').innerHTML = cats.map(c => `<option value="${c.id}">${esc(c.label)}</option>`).join('');
}

async function renderProdList() {
  let list = [];
  try { list = (await api('/api/products')).products || []; } catch (e) {}
  if (!list.length) {
    $('#prodList').innerHTML = '<div class="varempty">No products yet. Add your first one above.</div>';
    return;
  }
  $('#prodList').innerHTML = list.map(p => `
    <div class="prodrow">
      <div class="prodthumbs">${(p.refs || []).map(r =>
        `<img src="${r}" alt="">`).join('') || '<span class="noimg">no photo</span>'}</div>
      <div class="prodmeta">
        <b>${esc(p.label)}</b>
        <span class="prodprice">${priceTag(p.price)}</span>
        <span>${esc(p.vtype)} \u00b7 ${esc(p.category)}${p.spec ? ' \u00b7 ' + esc(p.spec) : ''}</span>
      </div>
      <button class="btn ghost del" data-del="${p.product_id}">Delete</button>
    </div>`).join('');
  $$('#prodList [data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this product? Any cached images that used it will be cleared.')) return;
    try {
      await api('/api/product/delete', { product_id: b.dataset.del });
      await renderProdList();
      if (S.catalog) { S.products = (await api('/api/products?type=' + S.vtype)).products || []; renderVariants(); }
      toast('Product deleted');
    } catch (e) { toast(e.message || 'Could not delete'); }
  });
}

async function openProducts() {
  $('#prodDrawer').classList.remove('hidden');
  $('#pVtype').value = S.vtype || 'car';
  await loadProdCats();
  await renderProdList();
}

$('#btnProducts').onclick = openProducts;
$('#btnCloseProd').onclick = () => $('#prodDrawer').classList.add('hidden');
$('#pVtype').onchange = loadProdCats;

$('#btnSaveProd').onclick = async () => {
  const btn = $('#btnSaveProd');
  const res = $('#prodResult');
  const label = $('#pName').value.trim();
  if (!label) { res.className = 'result bad'; res.textContent = 'Give the product a name.'; return; }

  const images = [];
  for (const sel of ['#pImg1', '#pImg2']) {
    const f = $(sel).files && $(sel).files[0];
    if (f) images.push(await downscale(f, 1536));
  }
  if (!images.length) {
    res.className = 'result bad';
    res.textContent = 'Add at least Photo 1 \u2014 a clear shot of the product itself.';
    return;
  }

  btn.disabled = true; btn.textContent = 'Saving...';
  res.className = 'result'; res.textContent = '';
  try {
    await api('/api/product/save', {
      label,
      vtype: $('#pVtype').value,
      category: $('#pCat').value,
      spec: $('#pSpec').value.trim(),
      price: Number($('#pPrice').value) || 0,
      images,
    });
    res.className = 'result good';
    res.textContent = label + ' saved. It now appears in the sidebar for every customer.';
    $('#pName').value = ''; $('#pSpec').value = ''; $('#pPrice').value = '';
    $('#pImg1').value = ''; $('#pImg2').value = '';
    await renderProdList();
    if (S.catalog) {
      S.products = (await api('/api/products?type=' + S.vtype)).products || [];
      renderVariants();
    }
  } catch (e) {
    res.className = 'result bad';
    res.textContent = e.message || 'Could not save the product.';
  } finally {
    btn.disabled = false; btn.textContent = 'Add product';
  }
};

/* ===== v5 zoom lightbox ===== */
(function(){
  var z=document.getElementById('zoom'), zi=document.getElementById('zoomImg'),
      zc=document.getElementById('zoomClose'), mi=document.getElementById('mainImg');
  if(!z||!zi||!mi) return;
  function open_(){ if(!mi.src||mi.classList.contains('hidden')) return;
    zi.src=mi.src; z.classList.remove('hidden'); }
  function close_(){ z.classList.add('hidden'); zi.removeAttribute('src'); }
  mi.addEventListener('click', open_);
  if(zc) zc.addEventListener('click', function(e){ e.stopPropagation(); close_(); });
  z.addEventListener('click', function(e){ if(e.target!==zi) close_(); });
  document.addEventListener('keydown', function(e){
    if(e.key==='Escape' && !z.classList.contains('hidden')) close_(); });
  var rt=document.getElementById('btnRetry');
  if(rt) rt.onclick=function(){ generate(false, true); };
})();

/* ===== v6 premium motion ===== */
(function(){
  /* sliding tab indicator */
  var nav=document.getElementById('stepNav'), ink=document.getElementById('stepInk');
  function moveInk(){
    if(!nav||!ink) return;
    var on=nav.querySelector('.step.on');
    if(!on){ ink.style.opacity='0'; return; }
    ink.style.opacity='1';
    ink.style.width=on.offsetWidth+'px';
    ink.style.transform='translateX('+on.offsetLeft+'px)';
  }
  if(nav&&ink){
    new MutationObserver(moveInk).observe(nav,{subtree:true,attributes:true,attributeFilter:['class']});
    window.addEventListener('resize',moveInk);
    setTimeout(moveInk,60); setTimeout(moveInk,400);
  }
  /* blur-up cross-fade for the render */
  var mi=document.getElementById('mainImg');
  if(mi){
    var ready=function(){ mi.classList.add('ready'); };
    mi.addEventListener('load',ready);
    mi.addEventListener('error',ready);
    new MutationObserver(function(){
      if(!mi.complete) mi.classList.remove('ready');
    }).observe(mi,{attributes:true,attributeFilter:['src']});
    if(mi.complete&&mi.getAttribute('src')) ready();
  }
})();


/* ===================== v14 SOUND WIRING ===================== */
(function () {
  const OPENERS = ['btnHelp','btnSettings','btnProducts','btnGoStudio','btnUseExample'];
  const CLOSERS = ['gmClose','gmBack','gmGotIt','btnCloseDrawer','zoomClose','btnCloseProducts'];
  const HITS = 'button, .chip, .angle, .hthumb, .modcard, .vcard, [data-goto], #mainImg';
  document.addEventListener('pointerdown', function (e) {
    const t = e.target.closest(HITS);
    if (!t || t.disabled || t.dataset.nosfx) return;
    if (CLOSERS.indexOf(t.id) >= 0) return SFX.play('close');
    if (OPENERS.indexOf(t.id) >= 0 || t.id === 'mainImg') return SFX.play('open');
    if (t.classList.contains('step')) return;
    SFX.play('click');
  }, true);
  let lastHover = null;
  document.addEventListener('pointerover', function (e) {
    /* v18: touch has no hover, so a tap must not fire the hover voice */
    if (e.pointerType && e.pointerType !== 'mouse') { lastHover = null; return; }
    const t = e.target.closest('button, .chip, .angle, [data-goto]');
    if (!t || t.disabled || t.dataset.nosfx) { lastHover = null; return; }
    if (t === lastHover) return;
    lastHover = t; SFX.play('hover');
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && document.body.classList.contains('gmopen')) SFX.play('close');
  }, true);
  function paintSfx() {
    const b = document.getElementById('btnSfxMute');
    const r = document.getElementById('setSfxVol');
    const v = document.getElementById('sfxVal');
    if (!b || !r || !v) return;
    b.textContent = SFX.muted ? 'Off' : 'On';
    b.classList.toggle('sfxoff', SFX.muted);
    r.value = Math.round(SFX.volume * 100);
    v.textContent = SFX.muted ? 'muted' : Math.round(SFX.volume * 100) + '%';
  }
  const rng = document.getElementById('setSfxVol');
  if (rng) {
    rng.addEventListener('input', function () {
      SFX.setVolume((+rng.value) / 100);
      if (SFX.muted && +rng.value > 0) SFX.setMuted(false);
      paintSfx();
    });
    rng.addEventListener('change', function () { SFX.play('click'); });
  }
  const mb = document.getElementById('btnSfxMute');
  if (mb) mb.addEventListener('click', function () {
    SFX.setMuted(!SFX.muted); paintSfx(); if (!SFX.muted) SFX.play('click');
  });
  paintSfx();
})();


/* ===================== v18 MOBILE =====================
   Phone and tablet behaviour. Nothing here changes the desktop build: every
   branch is gated on a media query or on touch input.
   ====================================================== */
(function () {
  var MQ_MOB   = window.matchMedia('(max-width:900px)');
  var MQ_TOUCH = window.matchMedia('(hover:none),(pointer:coarse)');

  function flags() {
    document.body.classList.toggle('mob', MQ_MOB.matches);
    document.body.classList.toggle('touch', MQ_TOUCH.matches);
    if (!MQ_MOB.matches) document.body.classList.remove('bfopen');
  }
  flags();
  if (MQ_MOB.addEventListener) {
    MQ_MOB.addEventListener('change', flags);
    MQ_TOUCH.addEventListener('change', flags);
  }
  window.addEventListener('resize', flags);
  window.addEventListener('orientationchange', function () { setTimeout(flags, 140); });

  /* iPhones and iPads keep every sound silent until one plays inside a real
     finger gesture, so run the pool once, inaudibly, on the first touch. */
  var unlocked = false;
  function unlockAudio() {
    if (unlocked) return;
    unlocked = true;
    try {
      var vol = SFX.volume, wasMuted = SFX.muted;
      if (wasMuted) SFX.setMuted(false);
      SFX.setVolume(0.0001);
      var names = ['click', 'hover', 'open', 'close', 'tab', 'done', 'error'];
      for (var round = 0; round < 3; round++) {
        for (var i = 0; i < names.length; i++) SFX.play(names[i]);
      }
      setTimeout(function () {
        SFX.setVolume(vol);
        if (wasMuted) SFX.setMuted(true);
      }, 80);
    } catch (e) {}
  }
  document.addEventListener('pointerdown', unlockAudio, true);
  document.addEventListener('touchend', unlockAudio, true);
  document.addEventListener('keydown', unlockAudio, true);

  /* the build panel is a pull-up sheet on a phone: its header is the handle,
     and the estimate total stays visible while it is collapsed */
  document.addEventListener('click', function (e) {
    if (!document.body.classList.contains('mob')) return;
    var head = e.target.closest && e.target.closest('#buildFloat .bbhead');
    if (!head) return;
    var opened = document.body.classList.toggle('bfopen');
    try { SFX.play(opened ? 'open' : 'close'); } catch (err) {}
  });

  /* camera straight from an empty slot, so the bike gets photographed on the
     spot instead of hunting through a gallery */
  function camPick(angle) {
    var i = document.createElement('input');
    i.type = 'file';
    i.accept = 'image/png,image/jpeg,image/webp';
    i.setAttribute('capture', 'environment');
    i.onchange = function () { if (i.files[0]) handleFile(i.files[0], angle); };
    i.click();
  }

  function paintCams() {
    if (!MQ_TOUCH.matches) return;
    var grid = document.getElementById('slotGrid');
    if (!grid) return;
    var slots = grid.querySelectorAll('.slot');
    for (var n = 0; n < slots.length; n++) {
      var sl = slots[n];
      var existing = sl.querySelector('.mdcam');
      if (sl.classList.contains('filled')) {
        if (existing) existing.parentNode.removeChild(existing);
        continue;
      }
      if (existing) continue;
      var b = document.createElement('button');
      b.className = 'mdcam';
      b.type = 'button';
      b.title = 'Take the photo now';
      b.setAttribute('aria-label', 'Take the photo now');
      b.textContent = '\uD83D\uDCF7';
      b.dataset.angle = sl.dataset.angle || '';
      b.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        camPick(this.dataset.angle);
      });
      sl.appendChild(b);
    }
  }

  var slotGridEl = document.getElementById('slotGrid');
  if (slotGridEl && window.MutationObserver) {
    new MutationObserver(function () { paintCams(); })
      .observe(slotGridEl, { childList: true, subtree: true });
  }
  paintCams();
  if (MQ_TOUCH.addEventListener) MQ_TOUCH.addEventListener('change', paintCams);
})();


/* ===================== v21 SHEET =====================
   Phone only. Keeps the price panel from covering the mod choices, and keeps
   "close" and "remove" as two clearly different actions.
   ===================================================== */
(function () {
  var sheet = document.getElementById('buildFloat');
  var head  = sheet && sheet.querySelector('.bbhead');
  var bill  = document.getElementById('billBox');
  if (!sheet || !head) return;

  function isMob() { return document.body.classList.contains('mob'); }

  /* the running total lives in the collapsed bar, so the price is visible
     without opening anything */
  var total = document.createElement('span');
  total.className = 'bbtotal';
  total.id = 'bbTotal';
  head.appendChild(total);

  /* a Done button that only ever closes the sheet */
  var done = document.createElement('button');
  done.className = 'bbclose';
  done.id = 'btnBuildClose';
  done.type = 'button';
  done.textContent = 'Done';
  done.setAttribute('aria-label', 'Close the build panel');
  head.appendChild(done);

  var scrim = document.createElement('div');
  scrim.id = 'bfScrim';
  /* v21 SHEET LAYER: .screen is a stacking context at z-index 1, so the dim
     layer has to live inside it or it covers the sheet too. */
  (document.getElementById('scr-studio') || document.body).appendChild(scrim);

  /* v22 SHEET STACK: the sheet lives inside .viewport, which is its own
     stacking layer, so on a phone it can never rise above the dim layer that
     belongs to the screen. Move it up to the screen while on a phone, and put
     it back for desktop. Handlers are delegated or rebound, so moving is safe. */
  var stage = document.getElementById('scr-studio');
  var homeParent = sheet.parentElement;
  var homeNext = sheet.nextElementSibling;

  function place() {
    if (!stage || !homeParent) return;
    if (isMob()) {
      if (sheet.parentElement !== stage) stage.appendChild(sheet);
    } else if (sheet.parentElement !== homeParent) {
      if (homeNext && homeNext.parentElement === homeParent) homeParent.insertBefore(sheet, homeNext);
      else homeParent.appendChild(sheet);
    }
  }

  place();
  try {
    var mq = matchMedia('(max-width:900px)');
    var onMq = function () { setTimeout(place, 0); };
    if (mq.addEventListener) mq.addEventListener('change', onMq);
    else if (mq.addListener) mq.addListener(onMq);
  } catch (e) {}
  window.addEventListener('orientationchange', function () { setTimeout(place, 60); });
  window.addEventListener('resize', function () { setTimeout(place, 120); });


  function close(quiet) {
    if (!document.body.classList.contains('bfopen')) return;
    document.body.classList.remove('bfopen');
    if (!quiet) { try { SFX.play('close'); } catch (e) {} }
  }

  /* capture, so the header's own open/close toggle never also fires */
  done.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    close();
  }, true);

  scrim.addEventListener('click', function () { close(); });

  /* mirror the total out of the price box whenever it is rewritten */
  function syncTotal() {
    if (!bill) return;
    var t = bill.querySelector('.bt');
    total.textContent = (t && !bill.classList.contains('hidden')) ? t.textContent : '';
  }
  if (bill && window.MutationObserver) {
    new MutationObserver(syncTotal).observe(bill, {
      childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ['class'],
    });
  }
  syncTotal();

  /* choosing a mod must never leave the sheet sitting over the categories */
  var list = document.getElementById('varList');
  if (list) {
    list.addEventListener('click', function () {
      if (isMob()) close(true);
    }, true);
  }
  var cats = document.getElementById('catBar');
  if (cats) {
    cats.addEventListener('click', function () {
      if (isMob()) close(true);
    }, true);
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') close();
  });
})();


/* ===================== HOSTED PWA =====================
   Installable on Android/iPhone home screens. The worker never caches APIs,
   customer photos or generated renders.
   ====================================================== */
if ('serviceWorker' in navigator && location.protocol === 'https:') {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}
