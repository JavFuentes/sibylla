// social.js — Fase social de Sibylla: votos, conteos agregados y comentarios.
//
// Isla progresiva: si Firebase/gstatic falla, no se añade body.social-on y el
// sitio permanece como estático. Los datos de build viajan en JSON inline del
// HTML para que este módulo sea cacheable.

const SDK = '10.12.0';
const G = `https://www.gstatic.com/firebasejs/${SDK}`;
const LEIDAS_KEY = 'sibylla_leidas';
const LEIDAS_MAX = 500;
const COMMENTS_PAGE = 20;
// Ventana durante la cual una tarjeta conserva su conteo optimista tras un voto
// o comentario propio, para no parpadear mientras la Cloud Function confirma.
const CONTEOS_HOLD_MS = 5000;

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
  const miVotoComentario = new Map();
  const comentariosVotosCargados = new Set();
  const conteos = new Map();
  const holdHasta = new Map();
  const enVuelo = new Set();
  const enVueloComentario = new Set();
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

  function todosCardIds() {
    return Array.prototype.map.call(document.querySelectorAll('.soc-grupo[data-card]'), (g) => g.getAttribute('data-card'));
  }
  function setConteosBulk(raw) {
    const vals = raw || {};
    todosCardIds().forEach((id) => conteos.set(id, norm(vals[id])));
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

  let unsubConteos = null;
  let primeraInstantanea = true;

  // Aplica una foto del documento agregado. No pisa las tarjetas con un voto o
  // comentario propio aún «en hold»: durante CONTEOS_HOLD_MS conservan su valor
  // optimista, así se evita el parpadeo mientras la Cloud Function recalcula y
  // confirma el conteo real. El resto se refresca al instante.
  function aplicarInstantanea(raw) {
    const vals = raw || {};
    const ahora = Date.now();
    todosCardIds().forEach((id) => {
      const hold = holdHasta.get(id);
      if (hold && hold > ahora) return;
      if (hold) holdHasta.delete(id);
      conteos.set(id, norm(vals[id]));
      pintarConteo(id);
    });
  }

  function suscribirConteos() {
    if (unsubConteos) return;
    // Tope de 2 s para el reorden: si la primera foto tarda (red lenta), los
    // números sí se refrescan pero NO se reordena — nunca mover tarjetas cuando
    // el usuario ya está leyendo. Las fotos siguientes solo actualizan conteos.
    const inicio = Date.now();
    try {
      unsubConteos = fsApi.onSnapshot(
        fsApi.doc(db, 'agregados', 'conteos'),
        (snap) => {
          aplicarInstantanea(snap.exists() ? (snap.data() || {}) : {});
          if (primeraInstantanea) {
            primeraInstantanea = false;
            if (Date.now() - inicio <= 2000) reordenarSiHaceFalta();
          }
        },
        (e) => { console.warn('[sibylla/social] conteos en vivo:', (e && e.code) || e); }
      );
    } catch (e) {
      console.warn('[sibylla/social] no se pudo suscribir a conteos:', e);
    }
  }

  function desuscribirConteos() {
    if (!unsubConteos) return;
    try { unsubConteos(); } catch (e) {}
    unsubConteos = null;
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

  // La suscripción en vivo solo existe con la pestaña visible: al ocultarla se
  // corta (no gastar lecturas de fondo) y al volver se reengancha con la foto
  // actual del documento agregado.
  function gestionVisibilidadConteos() {
    if (document.visibilityState === 'visible') suscribirConteos();
    else desuscribirConteos();
  }
  document.addEventListener('visibilitychange', gestionVisibilidadConteos);
  gestionVisibilidadConteos();

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
    holdHasta.set(cardId, Date.now() + CONTEOS_HOLD_MS);

    try {
      const ref = fsApi.doc(db, 'votes', `${cardId}_${uid}`);
      if (nuevo === 0) {
        await fsApi.deleteDoc(ref);
        miVoto.delete(cardId);
      } else {
        await fsApi.setDoc(ref, { card: cardId, uid, value: nuevo, ts: fsApi.serverTimestamp() });
        miVoto.set(cardId, nuevo);
      }
    } catch (e) {
      holdHasta.delete(cardId);
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

  function pintarVotoComentario(item, v) {
    const like = item.querySelector('[data-action="comment-like"]');
    const dislike = item.querySelector('[data-action="comment-dislike"]');
    if (like) like.setAttribute('aria-pressed', String(v === 1));
    if (dislike) dislike.setAttribute('aria-pressed', String(v === -1));
  }

  function pintarTodosVotosComentarios(panel) {
    panel.querySelectorAll('.comentario').forEach((item) => {
      pintarVotoComentario(item, miVotoComentario.get(item.dataset.comment));
    });
  }

  async function cargarMisVotosComentarios(cardId) {
    if (!uid || !cardId) return;
    const key = `${uid}|${cardId}`;
    if (comentariosVotosCargados.has(key)) return;
    comentariosVotosCargados.add(key);
    try {
      const snap = await fsApi.getDocs(fsApi.query(
        fsApi.collection(db, 'commentVotes'),
        fsApi.where('uid', '==', uid),
        fsApi.where('card', '==', cardId),
      ));
      snap.forEach((d) => {
        const data = d.data();
        if (data && data.comment && (data.value === 1 || data.value === -1)) {
          miVotoComentario.set(data.comment, data.value);
        }
      });
    } catch (e) {
      comentariosVotosCargados.delete(key);
      console.warn('[sibylla/social] mis votos de comentarios:', (e && e.code) || e);
    }
  }

  function crearBotonVotoComentario(docId, value, n) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'comentario-voto';
    btn.dataset.action = value === 1 ? 'comment-like' : 'comment-dislike';
    btn.dataset.vote = String(value);
    btn.setAttribute('aria-pressed', 'false');
    btn.setAttribute('aria-label', format(
      value === 1 ? TXT.social_comment_like_aria : TXT.social_comment_dislike_aria,
      { n },
    ));
    const marca = document.createElement('span');
    marca.setAttribute('aria-hidden', 'true');
    marca.textContent = value === 1 ? '+' : '-';
    const num = document.createElement('span');
    num.className = 'comentario-voto-num';
    num.dataset.num = value === 1 ? 'like' : 'dislike';
    num.textContent = textoConteo(n);
    btn.appendChild(marca);
    btn.appendChild(num);
    return btn;
  }

  function syncBotonRespuestas(item) {
    const n = Math.max(0, Number(item.dataset.respuestas || 0) || 0);
    let btn = item.querySelector(':scope > .comentario-rama > .comentario-respuestas-toggle');
    const rama = item.querySelector(':scope > .comentario-rama');
    if (!rama) return;
    if (n <= 0) {
      if (btn) btn.remove();
      return;
    }
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'comentarios-more comentario-respuestas-toggle';
      btn.dataset.action = 'toggle-replies';
      btn.setAttribute('aria-expanded', 'false');
      rama.insertBefore(btn, rama.firstChild);
    }
    const open = btn.getAttribute('aria-expanded') === 'true';
    btn.textContent = open ? TXT.social_replies_hide : format(TXT.social_replies_show, { n });
  }

  function setRootReplyCount(panel, rootId, delta) {
    const root = panel.querySelector(`.comentario[data-comment="${cssId(rootId)}"]`);
    if (!root) return;
    root.dataset.respuestas = String(Math.max(0, (Number(root.dataset.respuestas || 0) || 0) + delta));
    syncBotonRespuestas(root);
  }

  function construirComentario(panel, docId, data) {
    data = data || {};
    const eliminado = data.eliminado === true;
    const parent = typeof data.parent === 'string' ? data.parent : null;
    const isRoot = !parent;
    const item = document.createElement('article');
    item.className = `comentario${parent ? ' comentario-respuesta' : ''}${eliminado ? ' comentario-eliminado' : ''}`;
    item.dataset.comment = docId;
    item.dataset.parent = parent || '';
    item.dataset.respuestas = String(Math.max(0, Number(data.respuestas || 0) || 0));
    item.dataset.l = String(Math.max(0, Number(data.l || 0) || 0));
    item.dataset.d = String(Math.max(0, Number(data.d || 0) || 0));
    const meta = document.createElement('div');
    meta.className = 'comentario-meta';
    const fecha = document.createElement('span');
    fecha.textContent = fechaRel(data.ts);
    if (!eliminado) {
      const autor = document.createElement('strong');
      autor.textContent = data.autor || 'Sibylla';
      meta.appendChild(autor);
    }
    meta.appendChild(fecha);
    const texto = document.createElement('p');
    texto.className = 'comentario-texto';
    texto.textContent = eliminado ? TXT.social_comment_deleted : (data.texto || '');
    if (eliminado) texto.setAttribute('aria-label', TXT.social_comment_deleted);
    const acciones = document.createElement('div');
    acciones.className = 'comentario-acciones';
    if (!eliminado) {
      acciones.appendChild(crearBotonVotoComentario(docId, 1, Number(item.dataset.l)));
      acciones.appendChild(crearBotonVotoComentario(docId, -1, Number(item.dataset.d)));
      const reply = document.createElement('button');
      reply.type = 'button';
      reply.dataset.action = 'reply';
      reply.textContent = TXT.social_reply;
      reply.setAttribute('aria-expanded', 'false');
      acciones.appendChild(reply);
    }
    if (!eliminado && currentUser && data.uid === currentUser.uid) {
      const del = document.createElement('button');
      del.type = 'button';
      del.dataset.action = 'delete';
      del.textContent = TXT.social_comment_delete;
      acciones.appendChild(del);
    } else if (!eliminado && currentUser) {
      const rep = document.createElement('button');
      rep.type = 'button';
      rep.dataset.action = 'report';
      rep.textContent = TXT.social_comment_report;
      acciones.appendChild(rep);
    }
    item.appendChild(meta);
    item.appendChild(texto);
    item.appendChild(acciones);
    pintarVotoComentario(item, miVotoComentario.get(docId));
    if (isRoot) {
      const rama = document.createElement('div');
      rama.className = 'comentario-rama';
      const respuestas = document.createElement('div');
      respuestas.className = 'comentario-respuestas';
      respuestas.hidden = true;
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'comentarios-more comentario-respuestas-more';
      more.dataset.action = 'more-replies';
      more.textContent = TXT.social_comment_more;
      more.hidden = true;
      const formWrap = document.createElement('div');
      formWrap.className = 'comentario-respuesta-form-wrap';
      rama.appendChild(respuestas);
      rama.appendChild(more);
      rama.appendChild(formWrap);
      item.appendChild(rama);
      syncBotonRespuestas(item);
    }
    return item;
  }

  function renderComentario(panel, docId, data, prepend) {
    const list = panel.querySelector('.comentarios-lista');
    const empty = panel.querySelector('.comentarios-empty');
    if (empty) empty.hidden = true;
    const item = construirComentario(panel, docId, data);
    if (prepend && list.firstChild) list.insertBefore(item, list.firstChild); else list.appendChild(item);
    return item;
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
    commentState.set(cardId, { loaded: false, last: null, done: false, loading: false, replies: new Map() });
    more.addEventListener('click', () => cargarComentarios(panel, cardId, true));
    panel.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      const item = btn.closest('.comentario');
      if (!item) return;
      if (btn.dataset.action === 'delete') borrarComentario(panel, cardId, item);
      if (btn.dataset.action === 'report') reportarComentario(panel, item);
      if (btn.dataset.action === 'reply') abrirRespuesta(panel, cardId, item, btn);
      if (btn.dataset.action === 'toggle-replies') toggleRespuestas(panel, cardId, item, btn);
      if (btn.dataset.action === 'more-replies') cargarRespuestas(panel, cardId, item.closest('.comentario').dataset.comment, true);
      if (btn.dataset.action === 'comment-like' || btn.dataset.action === 'comment-dislike') votarComentario(panel, cardId, item, btn);
    });
    return panel;
  }

  async function cargarComentarios(panel, cardId, more) {
    const st = commentState.get(cardId);
    if (!st || st.loading || st.done && more) return;
    st.loading = true;
    try {
      await cargarMisVotosComentarios(cardId);
      let q = fsApi.query(
        fsApi.collection(db, 'comments'),
        fsApi.where('card', '==', cardId),
        fsApi.where('oculto', '==', false),
        fsApi.where('parent', '==', null),
        fsApi.orderBy('ts', 'desc'),
        fsApi.limit(COMMENTS_PAGE)
      );
      if (more && st.last) {
        q = fsApi.query(
          fsApi.collection(db, 'comments'),
          fsApi.where('card', '==', cardId),
          fsApi.where('oculto', '==', false),
          fsApi.where('parent', '==', null),
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

  async function cargarRespuestas(panel, cardId, rootId, more) {
    const st = commentState.get(cardId);
    if (!st) return;
    if (!st.replies.has(rootId)) st.replies.set(rootId, { loaded: false, last: null, done: false, loading: false });
    const rs = st.replies.get(rootId);
    if (rs.loading || rs.done && more) return;
    const root = panel.querySelector(`.comentario[data-comment="${cssId(rootId)}"]`);
    if (!root) return;
    const wrap = root.querySelector(':scope > .comentario-rama > .comentario-respuestas');
    const moreBtn = root.querySelector(':scope > .comentario-rama > .comentario-respuestas-more');
    rs.loading = true;
    try {
      await cargarMisVotosComentarios(cardId);
      let q = fsApi.query(
        fsApi.collection(db, 'comments'),
        fsApi.where('parent', '==', rootId),
        fsApi.where('oculto', '==', false),
        fsApi.orderBy('ts', 'asc'),
        fsApi.limit(COMMENTS_PAGE),
      );
      if (more && rs.last) {
        q = fsApi.query(
          fsApi.collection(db, 'comments'),
          fsApi.where('parent', '==', rootId),
          fsApi.where('oculto', '==', false),
          fsApi.orderBy('ts', 'asc'),
          fsApi.startAfter(rs.last),
          fsApi.limit(COMMENTS_PAGE),
        );
      }
      const snap = await fsApi.getDocs(q);
      snap.forEach((d) => wrap.appendChild(construirComentario(panel, d.id, d.data())));
      rs.last = snap.docs[snap.docs.length - 1] || rs.last;
      rs.done = snap.size < COMMENTS_PAGE;
      rs.loaded = true;
      if (moreBtn) moreBtn.hidden = rs.done;
      pintarTodosVotosComentarios(panel);
    } catch (e) {
      toast(panel, TXT.social_comment_error);
      console.warn('[sibylla/social] respuestas:', (e && e.code) || e);
    } finally {
      rs.loading = false;
    }
  }

  function toggleRespuestas(panel, cardId, item, btn) {
    const wrap = item.querySelector(':scope > .comentario-rama > .comentario-respuestas');
    if (!wrap) return;
    const open = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!open));
    wrap.hidden = open;
    btn.textContent = open ? format(TXT.social_replies_show, { n: Number(item.dataset.respuestas || 0) || 0 }) : TXT.social_replies_hide;
    if (!open) cargarRespuestas(panel, cardId, item.dataset.comment, false);
  }

  function abrirRespuesta(panel, cardId, item, btn) {
    if (!currentUser) { abrirAuth('comment'); return; }
    if (!currentUser.emailVerified) { toast(panel, TXT.social_verify_needed); return; }
    if (!currentUser.displayName) { renderForm(panel, cardId); return; }
    const rootId = item.dataset.parent || item.dataset.comment;
    const root = panel.querySelector(`.comentario[data-comment="${cssId(rootId)}"]`);
    if (!root) return;
    const wrap = root.querySelector(':scope > .comentario-rama > .comentario-respuesta-form-wrap');
    const respuestas = root.querySelector(':scope > .comentario-rama > .comentario-respuestas');
    const toggle = root.querySelector(':scope > .comentario-rama > .comentario-respuestas-toggle');
    if (respuestas) respuestas.hidden = false;
    if (toggle) { toggle.setAttribute('aria-expanded', 'true'); toggle.textContent = TXT.social_replies_hide; }
    renderReplyForm(panel, cardId, rootId, wrap, btn);
  }

  function renderForm(panel, cardId) {
    const wrap = panel.querySelector('.comentarios-form-wrap');
    wrap.replaceChildren();
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
          parent: null,
        });
        batch.set(fsApi.doc(db, 'users', currentUser.uid), { lastCommentAt: fsApi.serverTimestamp() }, { merge: true });
        await batch.commit();
        renderComentario(panel, ref.id, { uid: currentUser.uid, autor: currentUser.displayName, texto, ts: new Date(), parent: null }, true);
        const cur = norm(conteos.get(cardId)); cur.c++;
        pintarConteo(cardId, cur); holdHasta.set(cardId, Date.now() + CONTEOS_HOLD_MS);
        ta.value = ''; count.textContent = '0/500';
      } catch (err) {
        toast(panel, err && err.code === 'permission-denied' ? TXT.social_comment_rate : TXT.social_comment_error);
      } finally { btn.disabled = false; }
    });
    wrap.appendChild(form);
  }

  function renderReplyForm(panel, cardId, rootId, wrap, opener) {
    if (!wrap) return;
    const existing = wrap.querySelector('.comentario-form');
    if (existing) { wrap.replaceChildren(); if (opener) opener.setAttribute('aria-expanded', 'false'); return; }
    wrap.replaceChildren();
    const form = document.createElement('form');
    form.className = 'comentario-form comentario-reply-form';
    const ta = document.createElement('textarea');
    ta.maxLength = 500;
    ta.minLength = 2;
    ta.placeholder = TXT.social_reply_placeholder;
    const count = document.createElement('span');
    count.className = 'comentario-count';
    count.textContent = '0/500';
    ta.addEventListener('input', () => { count.textContent = `${ta.value.length}/500`; });
    const btn = document.createElement('button');
    btn.type = 'submit';
    btn.textContent = TXT.social_reply_send;
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
          parent: rootId,
        });
        batch.set(fsApi.doc(db, 'users', currentUser.uid), { lastCommentAt: fsApi.serverTimestamp() }, { merge: true });
        await batch.commit();
        const root = panel.querySelector(`.comentario[data-comment="${cssId(rootId)}"]`);
        const respuestas = root && root.querySelector(':scope > .comentario-rama > .comentario-respuestas');
        if (respuestas) {
          respuestas.hidden = false;
          respuestas.appendChild(construirComentario(panel, ref.id, {
            uid: currentUser.uid, autor: currentUser.displayName, texto, ts: new Date(), parent: rootId,
          }));
        }
        setRootReplyCount(panel, rootId, 1);
        const cur = norm(conteos.get(cardId)); cur.c++;
        pintarConteo(cardId, cur); holdHasta.set(cardId, Date.now() + CONTEOS_HOLD_MS);
        ta.value = ''; count.textContent = '0/500';
        wrap.replaceChildren();
        if (opener) opener.setAttribute('aria-expanded', 'false');
      } catch (err) {
        toast(panel, err && err.code === 'permission-denied' ? TXT.social_comment_rate : TXT.social_comment_error);
      } finally { btn.disabled = false; }
    });
    wrap.appendChild(form);
    if (opener) opener.setAttribute('aria-expanded', 'true');
    setTimeout(() => ta.focus(), 0);
  }

  async function votarComentario(panel, cardId, item, btn) {
    if (!uid) { abrirAuth('vote'); return; }
    const commentId = item.dataset.comment;
    const value = Number(btn.dataset.vote);
    if (!commentId || (value !== 1 && value !== -1) || enVueloComentario.has(commentId)) return;
    enVueloComentario.add(commentId);
    const previo = miVotoComentario.get(commentId);
    const nuevo = previo === value ? 0 : value;
    const base = { l: Number(item.dataset.l || 0) || 0, d: Number(item.dataset.d || 0) || 0 };
    const proy = { l: base.l, d: base.d };
    if (previo === 1) proy.l--; else if (previo === -1) proy.d--;
    if (nuevo === 1) proy.l++; else if (nuevo === -1) proy.d++;
    actualizarConteosComentario(item, proy);
    pintarVotoComentario(item, nuevo);
    try {
      const ref = fsApi.doc(db, 'commentVotes', `${commentId}_${uid}`);
      if (nuevo === 0) {
        await fsApi.deleteDoc(ref);
        miVotoComentario.delete(commentId);
      } else {
        await fsApi.setDoc(ref, { comment: commentId, card: cardId, uid, value: nuevo, ts: fsApi.serverTimestamp() });
        miVotoComentario.set(commentId, nuevo);
      }
    } catch (e) {
      actualizarConteosComentario(item, base);
      pintarVotoComentario(item, previo);
      toast(panel, TXT.social_comment_vote_error);
      console.warn('[sibylla/social] voto comentario:', (e && e.code) || e);
    } finally {
      enVueloComentario.delete(commentId);
    }
  }

  function actualizarConteosComentario(item, vals) {
    item.dataset.l = String(Math.max(0, vals.l || 0));
    item.dataset.d = String(Math.max(0, vals.d || 0));
    const like = item.querySelector('[data-num="like"]');
    const dislike = item.querySelector('[data-num="dislike"]');
    if (like) like.textContent = textoConteo(Number(item.dataset.l));
    if (dislike) dislike.textContent = textoConteo(Number(item.dataset.d));
    const likeBtn = item.querySelector('[data-action="comment-like"]');
    const dislikeBtn = item.querySelector('[data-action="comment-dislike"]');
    if (likeBtn) likeBtn.setAttribute('aria-label', format(TXT.social_comment_like_aria, { n: Number(item.dataset.l) }));
    if (dislikeBtn) dislikeBtn.setAttribute('aria-label', format(TXT.social_comment_dislike_aria, { n: Number(item.dataset.d) }));
  }

  async function borrarComentario(panel, cardId, item) {
    if (!confirm(TXT.social_comment_delete_confirm)) return;
    try {
      const respuestas = Number(item.dataset.respuestas || 0) || 0;
      const parent = item.dataset.parent || '';
      if (!parent && respuestas > 0) {
        await fsApi.updateDoc(fsApi.doc(db, 'comments', item.dataset.comment), { eliminado: true, texto: '', autor: '' });
        const nuevo = construirComentario(panel, item.dataset.comment, {
          uid: currentUser.uid, texto: '', autor: '', ts: new Date(), parent: null,
          eliminado: true, respuestas,
        });
        const ramaVieja = item.querySelector(':scope > .comentario-rama');
        const ramaNueva = nuevo.querySelector(':scope > .comentario-rama');
        if (ramaVieja && ramaNueva) ramaNueva.replaceChildren(...Array.from(ramaVieja.childNodes));
        item.replaceWith(nuevo);
      } else {
        await fsApi.deleteDoc(fsApi.doc(db, 'comments', item.dataset.comment));
        item.remove();
        if (parent) setRootReplyCount(panel, parent, -1);
      }
      const cur = norm(conteos.get(cardId)); cur.c = Math.max(0, cur.c - 1);
      pintarConteo(cardId, cur); holdHasta.set(cardId, Date.now() + CONTEOS_HOLD_MS);
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
      const parent = item.dataset.parent || '';
      if (parent) setRootReplyCount(panel, parent, -1);
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
      else cargarMisVotosComentarios(cardId).then(() => pintarTodosVotosComentarios(panel));
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
  const sesionApodo = document.getElementById('sesion-apodo');
  const sesionApodoForm = document.getElementById('sesion-apodo-form');
  const sesionApodoInput = document.getElementById('sesion-apodo-input');
  const sesionApodoMsg = document.getElementById('sesion-apodo-msg');
  const NICK_COOLDOWN_MS = 30 * 24 * 60 * 60 * 1000; // 1 cambio de apodo al mes

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
      miVotoComentario.clear();
      comentariosVotosCargados.clear();
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
        if (carta) {
          cargarMisVotosComentarios(carta.id).then(() => pintarTodosVotosComentarios(panel));
          renderForm(panel, carta.id);
        }
      });
    } else {
      sesionEntrar.hidden = false;
      sesionChip.hidden = true;
      cerrarSesionMenu();
      miVoto.clear();
      miVotoComentario.clear();
      comentariosVotosCargados.clear();
      document.querySelectorAll('.soc-grupo').forEach((g) => pintarVotoPropio(g, undefined));
      document.querySelectorAll('.comentarios-panel').forEach((panel) => pintarTodosVotosComentarios(panel));
    }
  }
  function cerrarSesionMenu() {
    if (!sesionMenu) return;
    sesionMenu.removeAttribute('data-abierto');
    if (sesionChip) sesionChip.setAttribute('aria-expanded', 'false');
    if (sesionApodoForm) sesionApodoForm.hidden = true;
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

  // ---- Cambiar apodo (una vez al mes) ----
  // El límite lo refuerza la regla de Firestore sobre users/{uid}.lastNickChangeAt
  // (gate de 30 días en servidor); aquí solo espejamos el estado en la UI.
  function apodoValido(name) {
    return name.length >= 2 && name.length <= 40 && !/https?:|www\.|\.com\b/i.test(name);
  }
  async function ultimoCambioApodo(user) {
    try {
      const snap = await fsApi.getDoc(fsApi.doc(db, 'users', user.uid));
      const ts = snap.exists() ? snap.data().lastNickChangeAt : null;
      return ts && ts.toMillis ? ts.toMillis() : null;
    } catch (e) { console.warn('[sibylla/social] apodo:', (e && e.code) || e); return null; }
  }
  function bloqueoApodo(ultimo) {
    if (!ultimo || Date.now() - ultimo >= NICK_COOLDOWN_MS) return null;
    const fecha = new Date(ultimo + NICK_COOLDOWN_MS).toLocaleDateString();
    return format(TXT.auth_nick_locked, { date: fecha });
  }
  function msgApodo(texto) {
    if (!sesionApodoMsg) return;
    if (texto) { sesionApodoMsg.textContent = texto; sesionApodoMsg.hidden = false; }
    else { sesionApodoMsg.hidden = true; }
  }
  if (sesionApodo && sesionApodoForm && sesionApodoInput) {
    const btnApodo = sesionApodoForm.querySelector('button[type="submit"]');
    sesionApodo.addEventListener('click', async () => {
      if (!currentUser) return;
      if (!sesionApodoForm.hidden) { sesionApodoForm.hidden = true; return; }
      msgApodo(null);
      sesionApodoInput.value = currentUser.displayName || '';
      sesionApodoInput.disabled = false;
      if (btnApodo) btnApodo.disabled = false;
      sesionApodoForm.hidden = false;
      sesionApodoInput.focus();
      const bloqueo = bloqueoApodo(await ultimoCambioApodo(currentUser));
      if (bloqueo) {
        msgApodo(bloqueo);
        sesionApodoInput.disabled = true;
        if (btnApodo) btnApodo.disabled = true;
      }
    });
    sesionApodoForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!currentUser) return;
      const name = sesionApodoInput.value.trim().replace(/\s+/g, ' ');
      if (!apodoValido(name)) { msgApodo(TXT.social_nick_error); return; }
      const bloqueo = bloqueoApodo(await ultimoCambioApodo(currentUser));
      if (bloqueo) { msgApodo(bloqueo); return; }
      if (btnApodo) btnApodo.disabled = true;
      try {
        // Primero el doc de Firestore: la regla users/{uid}.lastNickChangeAt
        // aplica el gate de 30 días en servidor. Si lo rechaza (muy pronto),
        // NO llegamos a updateProfile, así que el apodo no cambia sin registro.
        await fsApi.setDoc(fsApi.doc(db, 'users', currentUser.uid),
          { lastNickChangeAt: fsApi.serverTimestamp() }, { merge: true });
        await authApi.updateProfile(currentUser, { displayName: name });
        await currentUser.getIdToken(true);
        currentUser = auth.currentUser;
        pintarSesion(currentUser);
        sesionApodoForm.hidden = true;
      } catch (err) {
        const code = err && err.code;
        console.warn('[sibylla/social] cambiar apodo:', code || err);
        msgApodo(code === 'permission-denied'
          ? bloqueoApodo(await ultimoCambioApodo(currentUser)) || TXT.social_nick_error
          : TXT.social_nick_error);
      } finally { if (btnApodo) btnApodo.disabled = false; }
    });
  }
})();
