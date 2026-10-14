/** NODO is meant to move. Windows «Effetti animazione» switched off (the default on many small PCs, to save speed)
 *  reaches the browser as prefers-reduced-motion, and it silently froze everything: no opening, a still mind, no
 *  spinning core. NODO now follows its OWN switch: add ?calmo to the address (or set localStorage nodo.calmo=1) for
 *  the calm version. */
export const calm: boolean = (() => {
  try { return /[?&]calmo\b/.test(location.search) || localStorage.getItem("nodo.calmo") === "1"; }
  catch { return false; }
})();
if (calm) document.documentElement.classList.add("calm");
