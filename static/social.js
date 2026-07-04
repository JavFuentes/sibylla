// social.js — Fase social de Sibylla: votos (like/dislike) + login Firebase.
//
// Módulo ES (ES2017+; el navegador lo filtra con type="module"). Hidratación
// progresiva: si Firebase/gstatic no carga o el navegador no soporta módulos,
// el sitio queda idéntico a la versión estática (los botones sociales y el chip
// de sesión permanecen display:none porque este módulo nunca añade
// body.social-on). No toca el script ES5 del pie.
//
// El cardId de cada tarjeta es c.id = "n-"+sha256(dedup_key)[:12], estable
// entre rebuilds. Los datos que dependen del build (textos ES + firebaseConfig)
// viajan en <script type="application/json" id="social-i18n"> del HTML, no aquí:
// así este archivo queda libre de Jinja y cacheable (cache-buster ?v=<build_v>).

const SDK = '10.12.0'; // SDK modular v10.x; getAggregateFromServer/sum() requieren >=10.5
const G = `https://www.gstatic.com/firebasejs/${SDK}`;
const LEIDAS_KEY = 'sibylla_leidas';      // gate de lectura (preferencia local)
const CONTEOS_KEY = 'sibylla_conteos';    // cache de contadores (sessionStorage, TTL 30 min)
const CONTEOS_TTL = 30 * 60 * 1000;       // 30 min
const LEIDAS_MAX = 500;                   // FIFO: tope de tarjetas "leídas" en localStorage

// ---- textos + config via #social-i18n (evita hornear datos del build aquí) ----
function readI18n() {
  const el = document.getElementById('social-i18n');
  if (!el) return null;
  try { return JSON.parse(el.textContent); } catch (e) { return null; }
}

// ---- mapeo de códigos de error de Firebase Auth → claves de locale ----
const ERR_MAP = {
  'auth/invalid-email': 'auth_err_invalid_email',
  'auth/invalid-credential': 'auth_err_wrong',
  'auth/wrong-password': 'auth_err_wrong',
  'auth/user-not-found': 'auth_err_wrong',
  'auth/missing-password': 'auth_err_wrong',
  'auth/invalid-login-credentials': 'auth_err_wrong',
  'auth/email-already-in-use': 'auth_err_email_in_use',
  'auth/weak-password': 'auth_err_weak_password',
  'auth/popup-blocked': 'auth_err_popup',
  'auth/network-request-failed': 'auth_err_network',
  'auth/too-many-requests': 'auth_err_too_many',
  'auth/unauthorized-domain': 'auth_err_popup',
};
function mapearError(code, TXT) {
  if (code === 'auth/popup-closed-by-user') return null; // silencioso
  const key = ERR_MAP[code] || 'auth_err_generic';
  return TXT[key] || TXT.auth_err_generic;
}

(async () => {
  'use strict';

  const DATA = readI18n();
  if (!DATA || !DATA.config || !DATA.texts) return;
  const TXT = DATA.texts;

  // ---- 0. Carga defensiva del SDK modular desde gstatic ----
  let auth, db, authApi, fsApi;
  try {
    const appMod = await import(`${G}/firebase-app.js`);
    const authMod = await import(`${G}/firebase-auth.js`);
    const fsMod = await import(`${G}/firebase-firestore.js`);
    authApi = authMod;
    fsApi = fsMod;
    const app = appMod.getApps().length ? appMod.getApp() : appMod.initializeApp(DATA.config);
    auth = authMod.getAuth(app);
    db = fsMod.getFirestore(app);
  } catch (e) {
    console.warn('[sibylla/social] Firebase no disponible:', e);
    return; // sin social-on → la UI queda como hoy
  }
  document.body.classList.add('social-on');

  // ============================================================
  // Reading gate: localStorage 'sibylla_leidas' = {cardId:1}, FIFO ~500.
  // like/dislike nacen atenuados (.is-locked) y se habilitan al leer la
  // noticia (desplegar Resumen o clicar Original/título/imagen). Es un
  // nudge de producto, no un control de seguridad (es client-side).
  // ============================================================
  const Leidas = (() => {
    let store = {};
    try { store = JSON.parse(localStorage.getItem(LEIDAS_KEY) || '{}') || {}; } catch (e) { store = {}; }
    return {
      has(id) { return !!store[id]; },
      mark(id) {
        if (store[id]) return;
        store[id] = 1;
        const keys = Object.keys(store);
        for (let i = 0; i < keys.length - LEIDAS_MAX; i++) delete store[keys[i]];
        try { localStorage.setItem(LEIDAS_KEY, JSON.stringify(store)); } catch (e) {}
      },
    };
  })();

  // ============================================================
  // Estado: usuario actual + voto propio por tarjeta + conteos vistos.
  // ============================================================
  let uid = null;                  // null = sin sesión
  const miVoto = new Map();        // cardId → 1|-1 (voto del usuario actual)
  const conteos = new Map();       // cardId → {likes, dislikes}
  const enVuelo = new Set();       // cardId con un write en curso (anti doble-clic)

  // ---- cache de contadores (sessionStorage, TTL 30 min) ----
  const numCache = (() => {
    let cache = {};
    try { cache = JSON.parse(sessionStorage.getItem(CONTEOS_KEY) || '{}') || {}; } catch (e) { cache = {}; }
    return {
      get(id) { const e = cache[id]; return e && (Date.now() - e.ts) < CONTEOS_TTL ? e.val : null; },
      set(id, val) {
        cache[id] = { val, ts: Date.now() };
        try { sessionStorage.setItem(CONTEOS_KEY, JSON.stringify(cache)); } catch (e) {}
      },
    };
  })();

  // ---- pintar contadores en los .soc-num ----
  function pintarConteo(cardId, val) {
    if (val) conteos.set(cardId, val);
    const cur = val || conteos.get(cardId);
    if (!cur) return;
    const grupo = document.querySelector(`.soc-grupo[data-card="${cssId(cardId)}"]`);
    if (!grupo) return;
    const lk = grupo.querySelector('[data-num="like"]');
    const dk = grupo.querySelector('[data-num="dislike"]');
    if (lk) lk.textContent = cur.likes > 0 ? String(cur.likes) : '';
    if (dk) dk.textContent = cur.dislikes > 0 ? String(cur.dislikes) : '';
  }
  function cssId(id) { return (window.CSS && CSS.escape) ? CSS.escape(id) : id; }

  // ---- aggregation: 1 read por lote de ≤1000 index entries que casan ----
  async function cargarConteo(cardId) {
    const cacheado = numCache.get(cardId);
    if (cacheado) { pintarConteo(cardId, cacheado); return; }
    try {
      const snap = await fsApi.getAggregateFromServer(
        fsApi.query(fsApi.collection(db, 'votes'), fsApi.where('card', '==', cardId)),
        { total: fsApi.count(), suma: fsApi.sum('value') }
      );
      const data = snap.data();
      const total = data.total || 0, suma = data.suma || 0;
      const val = { likes: Math.round((total + suma) / 2), dislikes: Math.round((total - suma) / 2) };
      numCache.set(cardId, val);
      pintarConteo(cardId, val);
    } catch (e) {
      // Índice compuesto ausente (failed-precondition) u offline → sin números, no rompe.
      console.warn('[sibylla/social] conteo ' + cardId + ':', (e && e.code) || e);
    }
  }

  // ---- IntersectionObserver: solo contar tarjetas que entran al viewport ----
  const io = new IntersectionObserver((entries) => {
    for (const ent of entries) {
      if (!ent.isIntersecting) continue;
      cargarConteo(ent.target.getAttribute('data-card'));
      io.unobserve(ent.target); // la 1ª vez basta; el cache cubre recargas
    }
  }, { rootMargin: '300px' });
  document.querySelectorAll('.soc-grupo').forEach((g) => io.observe(g));

  // ============================================================
  // Gate: marcar leída al pulsar Resumen / Original / título / imagen.
  // ============================================================
  function desbloquearGrupo(grupo) {
    grupo.querySelectorAll('.soc-btn.is-locked').forEach((b) => {
      b.classList.remove('is-locked');
      b.removeAttribute('title'); // quita el "Lee la noticia para votar"
    });
  }
  function cardIdDe(carta) { return carta && carta.id && carta.id.startsWith('n-') ? carta.id : null; }
  document.addEventListener('click', (e) => {
    const carta = e.target.closest('.carta');
    if (!carta) return;
    if (!e.target.closest('.btn-resumen, .btn-original, h4 a, .carta-img')) return;
    const cardId = cardIdDe(carta);
    if (!cardId) return;
    Leidas.mark(cardId);
    const grupo = carta.querySelector('.soc-grupo');
    if (grupo) desbloquearGrupo(grupo);
  });
  // Al cargar, desbloquear las ya leídas en sesiones previas.
  document.querySelectorAll('.carta').forEach((carta) => {
    const cardId = cardIdDe(carta);
    if (cardId && Leidas.has(cardId)) {
      const grupo = carta.querySelector('.soc-grupo');
      if (grupo) desbloquearGrupo(grupo);
    }
  });

  // ============================================================
  // Votar: optimista + setDoc/deleteDoc, revert + social_vote_error si falla.
  // ============================================================
  function pintarVotoPropio(grupo, v) {
    const lk = grupo.querySelector('.soc-like');
    const dk = grupo.querySelector('.soc-dislike');
    if (lk) lk.setAttribute('aria-pressed', String(v === 1));
    if (dk) dk.setAttribute('aria-pressed', String(v === -1));
  }
  async function votar(btn) {
    const grupo = btn.closest('.soc-grupo');
    if (!grupo) return;
    const cardId = grupo.getAttribute('data-card');
    const value = Number(btn.getAttribute('data-vote'));
    if (!cardId || (value !== 1 && value !== -1)) return;
    if (btn.classList.contains('is-locked')) return; // gate sin leer
    if (!uid) { abrirAuth('vote'); return; }
    if (enVuelo.has(cardId)) return;                 // write en curso
    enVuelo.add(cardId);

    const previo = miVoto.get(cardId);               // 1|-1|undefined
    const nuevo = previo === value ? 0 : value;      // re-clic → quitar

    // UI optimista
    const base = conteos.get(cardId) || { likes: 0, dislikes: 0 };
    const proy = { likes: base.likes, dislikes: base.dislikes };
    if (previo === 1) proy.likes--; else if (previo === -1) proy.dislikes--;
    if (nuevo === 1) proy.likes++; else if (nuevo === -1) proy.dislikes++;
    pintarVotoPropio(grupo, nuevo);
    pintarConteo(cardId, proy);

    try {
      const ref = fsApi.doc(db, 'votes', `${cardId}_${uid}`);
      if (nuevo === 0) {
        await fsApi.deleteDoc(ref);
        miVoto.delete(cardId);
      } else {
        await fsApi.setDoc(ref, { card: cardId, uid, value: nuevo, ts: fsApi.serverTimestamp() });
        miVoto.set(cardId, nuevo);
      }
      numCache.set(cardId, proy);
    } catch (e) {
      // revertir al estado anterior
      pintarVotoPropio(grupo, previo);
      pintarConteo(cardId, base);
      alert(TXT.social_vote_error);
      console.warn('[sibylla/social] voto:', (e && e.code) || e);
    } finally {
      enVuelo.delete(cardId);
    }
  }

  // ---- comentarios (teaser): abre panel "próximamente" ----
  function comentarios(btn) {
    if (!uid) { abrirAuth('comment'); return; }
    const carta = btn.closest('.carta');
    if (!carta) return;
    let panel = carta.querySelector('.comentarios-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'comentarios-panel';
      panel.textContent = TXT.social_comments_soon;
      panel.hidden = true;
      carta.appendChild(panel);
    }
    const mostrar = panel.hidden;
    panel.hidden = !mostrar;
    btn.setAttribute('aria-expanded', String(mostrar));
  }

  // ---- click delegado del contenedor social ----
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.soc-btn');
    if (!btn) return;
    if (btn.classList.contains('soc-com')) comentarios(btn);
    else votar(btn);
  });

  // ============================================================
  // Modal de auth (reutiliza .onb/.onb-panel). Patrón onboarding para
  // focus + trampa de Tab + Escape. Google por signInWithPopup directo
  // en el handler (gesto de usuario → evita el bloqueo de Safari/ITP).
  // ============================================================
  const AUTH = document.getElementById('auth');
  const authSub = document.getElementById('auth-sub');
  const authMsg = document.getElementById('auth-msg');
  const authForm = document.getElementById('auth-form');
  const authSubmit = document.getElementById('auth-submit');
  const authAlternar = document.getElementById('auth-alternar');
  const authOlvide = document.getElementById('auth-olvide');
  const authEmail = document.getElementById('auth-email');
  const authPass = document.getElementById('auth-pass');
  let modoRegistro = false; // false = entrar, true = registrar

  function setModo(registro) {
    modoRegistro = registro;
    authSubmit.textContent = registro ? TXT.auth_signup : TXT.auth_signin;
    authAlternar.textContent = registro ? TXT.auth_to_signin : TXT.auth_to_signup;
    authPass.setAttribute('autocomplete', registro ? 'new-password' : 'current-password');
    mostrarMsgAuth('');
  }
  function mostrarMsgAuth(texto, ok) {
    authMsg.textContent = texto || '';
    authMsg.hidden = !texto;
    if (ok) authMsg.setAttribute('data-ok', ''); else authMsg.removeAttribute('data-ok');
  }
  function abrirAuth(motivo) {
    if (!AUTH) return;
    authSub.textContent = motivo === 'comment'
      ? authSub.getAttribute('data-comment')
      : authSub.getAttribute('data-vote');
    mostrarMsgAuth('');
    AUTH.hidden = false;
    document.body.classList.add('auth-abierto');
    setTimeout(() => { (authEmail || AUTH).focus(); }, 0);
  }
  function cerrarAuth() {
    if (!AUTH) return;
    AUTH.hidden = true;
    document.body.classList.remove('auth-abierto');
  }
  document.getElementById('auth-cerrar').addEventListener('click', cerrarAuth);
  AUTH.addEventListener('click', (e) => { if (e.target === AUTH) cerrarAuth(); }); // fuera del panel
  AUTH.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { cerrarAuth(); return; }
    if (e.key !== 'Tab') return;
    const focos = AUTH.querySelectorAll('button:not([disabled]), input:not([disabled]), a[href]');
    if (!focos.length) return;
    const first = focos[0], last = focos[focos.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  authAlternar.addEventListener('click', () => setModo(!modoRegistro));

  document.getElementById('auth-google').addEventListener('click', () => {
    // Lanzar SÍNCRONO desde el gesto de usuario (sin await previo): Safari/ITP
    // bloquea los pop-ups abiertos fuera del stack del click.
    const provider = new authApi.GoogleAuthProvider();
    authApi.signInWithPopup(auth, provider).catch((e) => {
      const msg = mapearError(e && e.code, TXT);
      if (msg) mostrarMsgAuth(msg);
    });
  });

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    mostrarMsgAuth('');
    const email = authEmail.value.trim();
    const pass = authPass.value;
    try {
      if (modoRegistro) await authApi.createUserWithEmailAndPassword(auth, email, pass);
      else await authApi.signInWithEmailAndPassword(auth, email, pass);
      // onAuthStateChanged cierra el modal
    } catch (err) {
      mostrarMsgAuth(mapearError(err && err.code, TXT));
    }
  });

  authOlvide.addEventListener('click', async () => {
    const email = authEmail.value.trim();
    if (!email) { mostrarMsgAuth(TXT.auth_err_invalid_email); authEmail.focus(); return; }
    try {
      await authApi.sendPasswordResetEmail(auth, email);
      mostrarMsgAuth(TXT.auth_reset_sent, true);
    } catch (err) {
      mostrarMsgAuth(mapearError(err && err.code, TXT));
    }
  });

  setModo(false);

  // ============================================================
  // Sesión: onAuthStateChanged pinta chip/Entrar, carga miVoto y pinta
  // aria-pressed; #sesion-salir → signOut.
  // ============================================================
  const sesionEntrar = document.getElementById('sesion-entrar');
  const sesionChip = document.getElementById('sesion-chip');
  const sesionCorreo = document.getElementById('sesion-correo');
  const sesionMenu = document.getElementById('sesion-menu');
  const sesionSalir = document.getElementById('sesion-salir');

  async function cargarMisVotos(user) {
    miVoto.clear();
    try {
      const snap = await fsApi.getDocs(
        fsApi.query(fsApi.collection(db, 'votes'), fsApi.where('uid', '==', user.uid))
      );
      snap.forEach((d) => {
        const data = d.data();
        if (data && data.card && (data.value === 1 || data.value === -1)) miVoto.set(data.card, data.value);
      });
    } catch (e) {
      console.warn('[sibylla/social] mis votos:', (e && e.code) || e);
    }
    document.querySelectorAll('.soc-grupo').forEach((g) => {
      pintarVotoPropio(g, miVoto.get(g.getAttribute('data-card')));
    });
  }

  function pintarSesion(user) {
    uid = user ? user.uid : null;
    if (user) {
      sesionEntrar.hidden = true;
      sesionChip.hidden = false;
      sesionChip.textContent = (user.displayName || user.email || '?').trim().charAt(0).toUpperCase();
      sesionChip.setAttribute('aria-label', user.email || TXT.auth_logout);
      sesionCorreo.textContent = user.email || '';
      cargarMisVotos(user);
      if (AUTH && !AUTH.hidden) cerrarAuth();
    } else {
      sesionEntrar.hidden = false;
      sesionChip.hidden = true;
      cerrarSesionMenu();
      miVoto.clear();
      document.querySelectorAll('.soc-grupo').forEach((g) => pintarVotoPropio(g, undefined));
    }
  }
  function cerrarSesionMenu() {
    if (!sesionMenu) return;
    sesionMenu.removeAttribute('data-abierto');
    if (sesionChip) sesionChip.setAttribute('aria-expanded', 'false');
  }

  authApi.onAuthStateChanged(auth, pintarSesion);

  sesionEntrar.addEventListener('click', () => abrirAuth('vote'));
  sesionChip.addEventListener('click', (e) => {
    e.stopPropagation();
    const abierto = sesionMenu.hasAttribute('data-abierto');
    if (abierto) cerrarSesionMenu();
    else { sesionMenu.setAttribute('data-abierto', ''); sesionChip.setAttribute('aria-expanded', 'true'); }
  });
  document.addEventListener('click', (e) => {
    if (sesionMenu && sesionMenu.hasAttribute('data-abierto') && !e.target.closest('#sesion')) cerrarSesionMenu();
  });
  sesionSalir.addEventListener('click', () => { authApi.signOut(auth).catch(() => {}); cerrarSesionMenu(); });
})();
