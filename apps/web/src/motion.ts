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

/** Measured, not guessed: the browser's real frame rate. If the PC cannot keep up (under ~24 frames a second for
 *  several seconds, intro excluded) NODO switches itself to «lite» on the spot instead of stuttering; ?pieno keeps
 *  the full version whatever happens. The cockpit shows this number (HoloHud). */
export const perf = { low: lite, fps: 0 };
let watching = false;
export function watchFrames() {
  if (watching) return;
  watching = true;
  const forcedFull = (() => { try { return /[?&]pieno\b/.test(location.search); } catch { return false; } })();
  let frames = 0, start = performance.now(), bad = 0;
  const born = performance.now();
  const tick = (t: number) => {
    requestAnimationFrame(tick);
    if (document.hidden) { frames = 0; start = t; return; }
    frames++;
    if (t - start < 2000) return;
    perf.fps = Math.round((frames * 1000) / (t - start));
    frames = 0; start = t;
    const busyStart = t - born < 9000 || document.querySelector(".intro");   // the opening is heavy on purpose
    if (!perf.low && !forcedFull && !busyStart) {
      bad = perf.fps < 24 ? bad + 1 : 0;
      if (bad >= 3) {
        perf.low = true;
        document.documentElement.classList.add("lite");
        window.dispatchEvent(new Event("nodo:perf"));
      }
    }
  };
  requestAnimationFrame(tick);
}
