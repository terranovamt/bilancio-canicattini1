/* ============ CONFIGURAZIONE LIVE ============
   Se "url" è vuoto la presentazione funziona come prima (solo locale). */
window.LIVE = {
  // URL del Realtime Database Firebase, es. https://assemblea-xxxx-default-rtdb.europe-west1.firebasedatabase.app
  url: 'https://assemblea-canicattini1-default-rtdb.europe-west1.firebasedatabase.app/',
  // 'corrente' = il pubblico non supera MAI la slide che mostri ora (anche se torni indietro)
  // 'massimo'  = il pubblico non supera la slide più avanzata che hai raggiunto
  limite: 'corrente',
  ruolo: 'locale'
};
/* Chi apre index.html?regia=LA-TUA-CHIAVE (una volta sola, poi la chiave resta salvata
   su quel browser) è il presentatore. Tutti gli altri sono "pubblico" e non possono superarti. */
(function () {
  const L = window.LIVE; if (!L.url) return;
  let c = new URLSearchParams(location.search).get('regia');
  try {
    if (c) { localStorage.setItem('regia_chiave', c); history.replaceState(null, '', location.pathname + location.hash) }
    else c = localStorage.getItem('regia_chiave');
  } catch (e) { }
  L.chiave = c || null; L.ruolo = c ? 'regia' : 'pubblico';
})();
