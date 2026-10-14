/** NODO is meant to move. Windows «Effetti animazione» switched off (the default on many small PCs, to save speed)
 *  reaches the browser as prefers-reduced-motion, and it silently froze everything: no opening, a still mind, no
 *  spinning core. NODO now follows its OWN switch: add ?calmo to the address (or set localStorage nodo.calmo=1) for
 *  the calm version. */
export const calm: boolean = (() => {
  try { return /[?&]calmo\b/.test(location.search) || localStorage.getItem("nodo.calmo") === "1"; }
  catch { return false; }
})();
if (calm) document.documentElement.classList.add("calm");

/** A small PC (the shop laptop: 2 cores, integrated graphics) stuttered as soon as NODO opened: two full-screen
 *  canvases, an animated 60px blur behind everything and frosted glass recomputed on every frame. «lite» keeps
 *  all the movement but draws it cheaply: 1x resolution, fewer frames, no live blurs. ?pieno forces the full
 *  version, ?lite the light one. */
export const lite: boolean = (() => {
  try {
    if (/[?&]pieno\b/.test(location.search)) return false;
    if (/[?&]lite\b/.test(location.search) || localStorage.getItem("nodo.lite") === "1") return true;
    const cores = navigator.hardwareConcurrency || 8;
    const mem = (navigator as Navigator & { deviceMemory?: number }).deviceMemory || 8;
    return cores <= 2 || mem <= 4;
  } catch { return false; }
})();
if (lite) document.documentElement.classList.add("lite");
/** Device pixels for canvases: never more than needed. */
export const canvasDpr = (max = 1.5) => (lite ? 1 : Math.min(window.devicePixelRatio || 1, max));
