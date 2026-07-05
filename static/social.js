// social.js — Fase social de Sibylla: votos, conteos agregados y comentarios.
//
// Isla progresiva: si Firebase/gstatic falla, no se añade body.social-on y el
// sitio permanece como estático. Los datos de build viajan en JSON inline del
// HTML para que este módulo sea cacheable.

const SDK = '10.12.0';
const G = `https://www.gstatic.com/firebasejs/${SDK}`;
const LEIDAS_KEY = 'sibylla_leidas';
const CONTEOS_KEY = 'sibylla_conteos_v2';
const CONTEOS_TTL = 30 * 60 * 1000;
const LEIDAS_MAX = 500;
const COMMENTS_PAGE = 20;

function readJson(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try { return JSON.parse(el.textContent || 'null'); } catch (e) { return null; }
}

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
  if (code === 'auth/popup-closed-by-user') return null;
  const key = ERR_MAP[code] || 'auth_err_generic';
  return TXT[key] || TXT.auth_err_generic;
}

(async () => {
  'use strict';

  const DATA = readJson('social-i18n');
  if (!DATA || !DATA.config || !DATA.texts) return;
  const TXT = DATA.texts;

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

    if (DATA.config.appCheckSiteKey) {
      try {
        const acMod = await import(`${G}/firebase-app-check.js`);
        acMod.initializeAppCheck(app, {
          provider: new acMod.ReCaptchaV3Provider(DATA.config.appCheckSiteKey),
          isTokenAutoRefreshEnabled: true,
        });
      } catch (e) {
        console.warn('[sibylla/social] App Check no disponible:', e);
      }
    }
  } catch (e) {
    console.warn('[sibylla/social] Firebase no disponible:', e);
    return;
  }
  document.body.classList.add('social-on');

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

  let uid = null;
  let currentUser = null;
  const miVoto = new Map();
  const conteos = new Map();
  const enVuelo = new Set();
  const commentState = new Map();
  let yaReordenado = false;
  let registroReciente = false;

  function cssId(id) { return (window.CSS && CSS.escape) ? CSS.escape(id) : id; }
  function cardIdDe(carta) { return carta && carta.id && carta.id.startsWith('n-') ? carta.id : null; }
  function norm(v) {
    v = v || {};
    return {
      l: Math.max(0, Number(v.l != null ? v.l : (v.likes || 0)) || 0),
      d: Math.max(0, Number(v.d != null ? v.d : (v.dislikes || 0)) || 0),
      c: Math.max(0, Number(v.c != null ? v.c : (v.comments || 0)) || 0),
    };
  }
  function score(v) { v = norm(v); return v.l - v.d + 2 * v.c; }
  function textoConteo(n) { return n === 0 ? '0' : String(n); }
  function format(tpl, vals) {
    return String(tpl || '').replace(/\{(\w+)\}/g, (_m, k) => vals[k] != null ? String(vals[k]) : '');
  }

  const numCache = (() => {
    let cache = null;
    try { cache = JSON.parse(sessionStorage.getItem(CONTEOS_KEY) || 'null'); } catch (e) { cache = null; }
    return {
      get() { return cache && (Date.now() - cache.ts) < CONTEOS_TTL ? (cache.val || {}) : null; },
      set(val) {
        cache = { val, ts: Date.now() };
        try { sessionStorage.setItem(CONTEOS_KEY, JSON.stringify(cache)); } catch (e) {}
      },
    };
  })();

  function todosCardIds() {
    return Array.prototype.map.call(document.querySelectorAll('.soc-grupo[data-card]'), (g) => g.getAttribute('data-card'));
  }
  function setConteosBulk(raw) {
    const vals = raw || {};
    todosCardIds().forEach((id) => conteos.set(id, norm(vals[id])));
  }
  function conteosObject() {
    const out = {};
    conteos.forEach((v, k) => { out[k] = norm(v); });
    return out;
  }
  function pintarConteo(cardId, val) {
    if (val) conteos.set(cardId, norm(val));
    const cur = conteos.get(cardId) || norm();
    const grupo = document.querySelector(`.soc-grupo[data-card="${cssId(cardId)}"]`);
    if (!grupo) return;
    const lk = grupo.querySelector('[data-num="like"]');
    const dk = grupo.querySelector('[data-num="dislike"]');
    const ck = grupo.querySelector('[data-num="comments"]');
    if (lk) lk.textContent = textoConteo(cur.l);
    if (dk) dk.textContent = textoConteo(cur.d);
    if (ck) ck.textContent = textoConteo(cur.c);
    const cb = grupo.querySelector('.soc-com');
    if (cb) cb.setAttribute('aria-label', format(TXT.social_comment_count_aria || TXT.social_comments, { n: cur.c }));
  }
  function pintarTodos() { todosCardIds().forEach((id) => pintarConteo(id)); }

  const horneados = readJson('social-conteos') || {};
  setConteosBulk(horneados);
  pintarTodos();

  async function cargarConteosFrescos() {
    const cacheado = numCache.get();
    if (cacheado) {
      setConteosBulk(cacheado);
      pintarTodos();
      return cacheado;
    }
    try {
      const snap = await fsApi.getDoc(fsApi.doc(db, 'agregados', 'conteos'));
      const data = snap.exists() ? (snap.data() || {}) : {};
      setConteosBulk(data);
      pintarTodos();
      const obj = conteosObject();
      numCache.set(obj);
      return obj;
    } catch (e) {
      console.warn('[sibylla/social] conteos agregados:', (e && e.code) || e);
      return null;
    }
  }

  function modoAleatorioActivo() {
    const feed = document.getElementById('feed');
    const cont = document.getElementById('secciones');
    return document.body.classList.contains('modo-aleatorio') || (feed && !feed.hidden) || (cont && cont.style.display === 'none');
  }
  function bloqueDatos() {
    return Array.prototype.map.call(document.querySelectorAll('#secciones .bloque'), (bloque) => {
      const rej = bloque.querySelector('.rejilla');
      const cards = rej ? Array.prototype.slice.call(rej.querySelectorAll('.carta')) : [];
      return { bloque, rej, cards, visibles: cards.filter((c) => c.style.display !== 'none').length };
    });
  }
  function needsReorder(datos) {
    return datos.some((d) => {
      const orden = d.cards.slice().sort((a, b) => score(conteos.get(b.id)) - score(conteos.get(a.id)));
      return orden.some((c, i) => c !== d.cards[i]);
    });
  }
  function crearVelo() {
    const cont = document.getElementById('secciones');
    if (!cont) return null;
    const velo = document.createElement('div');
    velo.className = 'social-velo';
    velo.setAttribute('role', 'status');
    const glifo = document.createElement('span');
    glifo.className = 'social-velo-glifo';
    glifo.textContent = '✶';
    const txt = document.createElement('span');
    txt.textContent = TXT.social_orden_cargando || '';
    velo.appendChild(glifo);
    velo.appendChild(txt);
    cont.appendChild(velo);
    return velo;
  }
  function aplicarReorden(datos) {
    datos.forEach((d) => {
      if (!d.rej) return;
      const orden = d.cards.slice().sort((a, b) => score(conteos.get(b.id)) - score(conteos.get(a.id)));
      orden.forEach((c, i) => { c.style.display = i < d.visibles ? '' : 'none'; d.rej.appendChild(c); });
      const val = d.bloque.querySelector('.card-ctrl-val');
      if (val) val.textContent = String(d.visibles);
    });
    if (window.SibyllaSocialRefreshHomes) window.SibyllaSocialRefreshHomes();
  }
  async function reordenarSiHaceFalta() {
    if (yaReordenado || modoAleatorioActivo()) return;
    yaReordenado = true;
    const started = Date.now();
    const datos = bloqueDatos();
    if (!needsReorder(datos)) return;
    const velo = crearVelo();
    aplicarReorden(datos);
    const wait = Math.max(0, 300 - (Date.now() - started));
    setTimeout(() => {
      if (!velo) return;
      velo.classList.add('is-out');
      setTimeout(() => velo.remove(), 220);
    }, wait);
  }

  // Tope de 2 s: si los conteos frescos llegan más tarde (red lenta), NO se
  // reordena — nunca mover tarjetas cuando el usuario ya está leyendo. Los
  // números sí se refrescan igual.
  const inicioConteos = Date.now();
  cargarConteosFrescos().then((fresh) => {
    if (fresh && Date.now() - inicioConteos <= 2000) reordenarSiHaceFalta();
  });

  function desbloquearGrupo(grupo) {
    grupo.querySelectorAll('.soc-btn.is-locked').forEach((b) => {
      b.classList.remove('is-locked');
      b.removeAttribute('title');
    });
  }
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
  document.querySelectorAll('.carta').forEach((carta) => {
    const cardId = cardIdDe(carta);
    if (cardId && Leidas.has(cardId)) {
      const grupo = carta.querySelector('.soc-grupo');
      if (grupo) desbloquearGrupo(grupo);
    }
  });

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
    if (btn.classList.contains('is-locked')) return;
    if (!uid) { abrirAuth('vote'); return; }
    if (enVuelo.has(cardId)) return;
    enVuelo.add(cardId);

    const previo = miVoto.get(cardId);
    const nuevo = previo === value ? 0 : value;
    const base = norm(conteos.get(cardId));
    const proy = norm(base);
    if (previo === 1) proy.l--; else if (previo === -1) proy.d--;
    if (nuevo === 1) proy.l++; else if (nuevo === -1) proy.d++;
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
      numCache.set(conteosObject());
    } catch (e) {
      pintarVotoPropio(grupo, previo);
      pintarConteo(cardId, base);
      alert(TXT.social_vote_error);
      console.warn('[sibylla/social] voto:', (e && e.code) || e);
    } finally {
      enVuelo.delete(cardId);
    }
  }

  function fechaRel(ts) {
    const d = ts && ts.toDate ? ts.toDate() : (ts instanceof Date ? ts : new Date());
    const diff = Math.max(0, Date.now() - d.getTime());
    const min = Math.floor(diff / 60000);
    if (min < 1) return 'ahora';
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    if (h < 24) return `${h} h`;
    return `${Math.floor(h / 24)} d`;
  }
  function toast(panel, msg) {
    let el = panel.querySelector('.comentarios-toast');
    if (!el) {
      el = document.createElement('p');
      el.className = 'comentarios-toast';
      el.setAttribute('role', 'status');
      panel.appendChild(el);
    }
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.hidden = true; }, 3500);
  }
  function renderComentario(panel, docId, data, prepend) {
    const list = panel.querySelector('.comentarios-lista');
    const empty = panel.querySelector('.comentarios-empty');
    if (empty) empty.hidden = true;
    const item = document.createElement('article');
    item.className = 'comentario';
    item.dataset.comment = docId;
    const meta = document.createElement('div');
    meta.className = 'comentario-meta';
    const autor = document.createElement('strong');
    autor.textContent = data.autor || 'Sibylla';
    const fecha = document.createElement('span');
    fecha.textContent = fechaRel(data.ts);
    meta.appendChild(autor);
    meta.appendChild(fecha);
    const texto = document.createElement('p');
    texto.className = 'comentario-texto';
    texto.textContent = data.texto || '';
    const acciones = document.createElement('div');
    acciones.className = 'comentario-acciones';
    if (currentUser && data.uid === currentUser.uid) {
      const del = document.createElement('button');
      del.type = 'button';
      del.dataset.action = 'delete';
      del.textContent = TXT.social_comment_delete;
      acciones.appendChild(del);
    } else if (currentUser) {
      const rep = document.createElement('button');
      rep.type = 'button';
      rep.dataset.action = 'report';
      rep.textContent = TXT.social_comment_report;
      acciones.appendChild(rep);
    }
    item.appendChild(meta);
    item.appendChild(texto);
    item.appendChild(acciones);
    if (prepend && list.firstChild) list.insertBefore(item, list.firstChild); else list.appendChild(item);
  }
  function crearPanel(carta, cardId) {
    const panel = document.createElement('div');
    panel.className = 'comentarios-panel';
    panel.hidden = true;
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-label', TXT.social_comments_title);
    const title = document.createElement('h5');
    title.textContent = TXT.social_comments_title;
    const list = document.createElement('div');
    list.className = 'comentarios-lista';
    const empty = document.createElement('p');
    empty.className = 'comentarios-empty';
    empty.textContent = TXT.social_comment_empty;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'comentarios-more';
    more.textContent = TXT.social_comment_more;
    more.hidden = true;
    const formWrap = document.createElement('div');
    formWrap.className = 'comentarios-form-wrap';
    panel.appendChild(title);
    panel.appendChild(list);
    panel.appendChild(empty);
    panel.appendChild(more);
    panel.appendChild(formWrap);
    carta.appendChild(panel);
    commentState.set(cardId, { loaded: false, last: null, done: false, loading: false });
    more.addEventListener('click', () => cargarComentarios(panel, cardId, true));
    panel.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      const item = btn.closest('.comentario');
      if (!item) return;
      if (btn.dataset.action === 'delete') borrarComentario(panel, cardId, item);
      if (btn.dataset.action === 'report') reportarComentario(panel, item);
    });
    return panel;
  }
  async function cargarComentarios(panel, cardId, more) {
    const st = commentState.get(cardId);
    if (!st || st.loading || st.done && more) return;
    st.loading = true;
    try {
      let q = fsApi.query(
        fsApi.collection(db, 'comments'),
        fsApi.where('card', '==', cardId),
        fsApi.where('oculto', '==', false),
        fsApi.orderBy('ts', 'desc'),
        fsApi.limit(COMMENTS_PAGE)
      );
      if (more && st.last) {
        q = fsApi.query(
          fsApi.collection(db, 'comments'),
          fsApi.where('card', '==', cardId),
          fsApi.where('oculto', '==', false),
          fsApi.orderBy('ts', 'desc'),
          fsApi.startAfter(st.last),
          fsApi.limit(COMMENTS_PAGE)
        );
      }
      const snap = await fsApi.getDocs(q);
      snap.forEach((d) => renderComentario(panel, d.id, d.data(), false));
      st.last = snap.docs[snap.docs.length - 1] || st.last;
      st.done = snap.size < COMMENTS_PAGE;
      st.loaded = true;
      panel.querySelector('.comentarios-more').hidden = st.done;
      panel.querySelector('.comentarios-empty').hidden = !!panel.querySelector('.comentario');
    } catch (e) {
      toast(panel, TXT.social_comment_error);
      console.warn('[sibylla/social] comentarios:', (e && e.code) || e);
    } finally {
      st.loading = false;
    }
  }
  function renderForm(panel, cardId) {
    const wrap = panel.querySelector('.comentarios-form-wrap');
    wrap.innerHTML = '';
    if (!currentUser) return;
    if (!currentUser.emailVerified) {
      const msg = document.createElement('p');
      msg.className = 'comentarios-aviso';
      msg.textContent = TXT.social_verify_needed;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn-mini';
      btn.textContent = TXT.social_verify_resend;
      btn.addEventListener('click', async () => {
        try { await authApi.sendEmailVerification(currentUser); toast(panel, TXT.social_verify_sent); }
        catch (e) { toast(panel, TXT.social_comment_error); }
      });
      wrap.appendChild(msg);
      wrap.appendChild(btn);
      return;
    }
    if (!currentUser.displayName) {
      const form = document.createElement('form');
      form.className = 'nick-form';
      const label = document.createElement('label');
      label.textContent = TXT.social_nick_label;
      const input = document.createElement('input');
      input.type = 'text';
      input.minLength = 2;
      input.maxLength = 40;
      input.placeholder = TXT.social_nick_hint;
      const btn = document.createElement('button');
      btn.type = 'submit';
      btn.textContent = TXT.social_nick_save;
      form.appendChild(label);
      form.appendChild(input);
      form.appendChild(btn);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = input.value.trim().replace(/\s+/g, ' ');
        if (name.length < 2 || name.length > 40 || /https?:|www\.|\.com\b/i.test(name)) {
          toast(panel, TXT.social_nick_error); return;
        }
        try {
          await authApi.updateProfile(currentUser, { displayName: name });
          await currentUser.getIdToken(true);
          currentUser = auth.currentUser;
          renderForm(panel, cardId);
          pintarSesion(currentUser);
        } catch (err) { toast(panel, TXT.social_nick_error); }
      });
      wrap.appendChild(form);
      return;
    }
    const form = document.createElement('form');
    form.className = 'comentario-form';
    const ta = document.createElement('textarea');
    ta.maxLength = 500;
    ta.minLength = 2;
    ta.placeholder = TXT.social_comment_placeholder;
    const count = document.createElement('span');
    count.className = 'comentario-count';
    count.textContent = '0/500';
    ta.addEventListener('input', () => { count.textContent = `${ta.value.length}/500`; });
    const btn = document.createElement('button');
    btn.type = 'submit';
    btn.textContent = TXT.social_comment_send;
    form.appendChild(ta);
    form.appendChild(count);
    form.appendChild(btn);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const texto = ta.value.trim();
      if (texto.length < 2) return;
      btn.disabled = true;
      try {
        const ref = fsApi.doc(fsApi.collection(db, 'comments'));
        const batch = fsApi.writeBatch(db);
        batch.set(ref, {
          card: cardId,
          uid: currentUser.uid,
          autor: currentUser.displayName,
          texto,
          ts: fsApi.serverTimestamp(),
          reportes: 0,
          oculto: false,
        });
        batch.set(fsApi.doc(db, 'users', currentUser.uid), { lastCommentAt: fsApi.serverTimestamp() }, { merge: true });
        await batch.commit();
        renderComentario(panel, ref.id, { uid: currentUser.uid, autor: currentUser.displayName, texto, ts: new Date() }, true);
        const cur = norm(conteos.get(cardId)); cur.c++;
        pintarConteo(cardId, cur); numCache.set(conteosObject());
        ta.value = ''; count.textContent = '0/500';
      } catch (err) {
        toast(panel, err && err.code === 'permission-denied' ? TXT.social_comment_rate : TXT.social_comment_error);
      } finally { btn.disabled = false; }
    });
    wrap.appendChild(form);
  }
  async function borrarComentario(panel, cardId, item) {
    if (!confirm(TXT.social_comment_delete_confirm)) return;
    try {
      await fsApi.deleteDoc(fsApi.doc(db, 'comments', item.dataset.comment));
      item.remove();
      const cur = norm(conteos.get(cardId)); cur.c = Math.max(0, cur.c - 1);
      pintarConteo(cardId, cur); numCache.set(conteosObject());
    } catch (e) { toast(panel, TXT.social_comment_error); }
  }
  async function reportarComentario(panel, item) {
    if (!currentUser || !currentUser.emailVerified) { toast(panel, TXT.social_verify_needed); return; }
    if (!confirm(TXT.social_comment_report_confirm)) return;
    const commentId = item.dataset.comment;
    try {
      await fsApi.runTransaction(db, async (tx) => {
        const cref = fsApi.doc(db, 'comments', commentId);
        const snap = await tx.get(cref);
        if (!snap.exists()) return;
        const data = snap.data();
        const next = (data.reportes || 0) + 1;
        tx.update(cref, { reportes: next, oculto: next >= 3 || !!data.oculto });
        tx.set(fsApi.doc(db, 'reports', `${commentId}_${currentUser.uid}`), {
          comment: commentId, uid: currentUser.uid, ts: fsApi.serverTimestamp(),
        });
        tx.set(fsApi.doc(db, 'users', currentUser.uid), { lastReportAt: fsApi.serverTimestamp() }, { merge: true });
      });
      item.remove();
      toast(panel, TXT.social_comment_reported);
    } catch (e) {
      if (e && e.code === 'permission-denied') toast(panel, TXT.social_comment_reported);
      else toast(panel, TXT.social_comment_error);
    }
  }
  function comentarios(btn) {
    if (!uid) { abrirAuth('comment'); return; }
    const carta = btn.closest('.carta');
    if (!carta) return;
    const cardId = cardIdDe(carta);
    if (!cardId) return;
    let panel = carta.querySelector('.comentarios-panel');
    if (!panel) panel = crearPanel(carta, cardId);
    const mostrar = panel.hidden;
    panel.hidden = !mostrar;
    btn.setAttribute('aria-expanded', String(mostrar));
    if (mostrar) {
      const st = commentState.get(cardId);
      if (st && !st.loaded) cargarComentarios(panel, cardId, false);
      renderForm(panel, cardId);
      const ta = panel.querySelector('textarea');
      if (ta) setTimeout(() => ta.focus(), 0);
    }
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.soc-btn');
    if (!btn) return;
    if (btn.classList.contains('soc-com')) comentarios(btn);
    else votar(btn);
  });

  const AUTH = document.getElementById('auth');
  const authSub = document.getElementById('auth-sub');
  const authMsg = document.getElementById('auth-msg');
  const authForm = document.getElementById('auth-form');
  const authSubmit = document.getElementById('auth-submit');
  const authAlternar = document.getElementById('auth-alternar');
  const authOlvide = document.getElementById('auth-olvide');
  const authEmail = document.getElementById('auth-email');
  const authPass = document.getElementById('auth-pass');
  let modoRegistro = false;

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
    authSub.textContent = motivo === 'comment' ? authSub.getAttribute('data-comment') : authSub.getAttribute('data-vote');
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
  AUTH.addEventListener('click', (e) => { if (e.target === AUTH) cerrarAuth(); });
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
      if (modoRegistro) {
        registroReciente = true;
        const cred = await authApi.createUserWithEmailAndPassword(auth, email, pass);
        await authApi.sendEmailVerification(cred.user);
        mostrarMsgAuth(TXT.social_verify_sent, true);
      } else {
        await authApi.signInWithEmailAndPassword(auth, email, pass);
      }
    } catch (err) {
      registroReciente = false;
      mostrarMsgAuth(mapearError(err && err.code, TXT));
    }
  });
  authOlvide.addEventListener('click', async () => {
    const email = authEmail.value.trim();
    if (!email) { mostrarMsgAuth(TXT.auth_err_invalid_email); authEmail.focus(); return; }
    try { await authApi.sendPasswordResetEmail(auth, email); mostrarMsgAuth(TXT.auth_reset_sent, true); }
    catch (err) { mostrarMsgAuth(mapearError(err && err.code, TXT)); }
  });
  setModo(false);

  const sesionEntrar = document.getElementById('sesion-entrar');
  const sesionChip = document.getElementById('sesion-chip');
  const sesionNombre = document.getElementById('sesion-nombre');
  const sesionCorreo = document.getElementById('sesion-correo');
  const sesionMenu = document.getElementById('sesion-menu');
  const sesionSalir = document.getElementById('sesion-salir');

  async function cargarMisVotos(user) {
    miVoto.clear();
    try {
      const snap = await fsApi.getDocs(fsApi.query(fsApi.collection(db, 'votes'), fsApi.where('uid', '==', user.uid)));
      snap.forEach((d) => {
        const data = d.data();
        if (data && data.card && (data.value === 1 || data.value === -1)) miVoto.set(data.card, data.value);
      });
    } catch (e) { console.warn('[sibylla/social] mis votos:', (e && e.code) || e); }
    document.querySelectorAll('.soc-grupo').forEach((g) => pintarVotoPropio(g, miVoto.get(g.getAttribute('data-card'))));
  }
  function inicial(user) { return (user.displayName || user.email || '?').trim().charAt(0).toUpperCase(); }
  function pintarAvatar(user) {
    sesionChip.textContent = '';
    sesionChip.classList.remove('has-img');
    if (user.photoURL) {
      const img = document.createElement('img');
      img.src = user.photoURL;
      img.alt = '';
      img.referrerPolicy = 'no-referrer';
      img.onerror = () => { sesionChip.classList.remove('has-img'); sesionChip.textContent = inicial(user); };
      sesionChip.classList.add('has-img');
      sesionChip.appendChild(img);
    } else {
      sesionChip.textContent = inicial(user);
    }
  }
  function pintarSesion(user) {
    currentUser = user || null;
    uid = user ? user.uid : null;
    if (user) {
      sesionEntrar.hidden = true;
      sesionChip.hidden = false;
      pintarAvatar(user);
      sesionChip.setAttribute('aria-label', user.email || TXT.auth_logout);
      if (sesionNombre) sesionNombre.textContent = user.displayName || 'Sibylla';
      sesionCorreo.textContent = user.email || '';
      cargarMisVotos(user);
      if (AUTH && !AUTH.hidden && !registroReciente) cerrarAuth();
      registroReciente = false;
      document.querySelectorAll('.comentarios-panel:not([hidden])').forEach((panel) => {
        const carta = panel.closest('.carta');
        if (carta) renderForm(panel, carta.id);
      });
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
