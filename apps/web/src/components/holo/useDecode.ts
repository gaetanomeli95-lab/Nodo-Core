import { useEffect, useRef, useState } from "react";
import { calm } from "../../motion";

const GLYPHS = "▓▒░<>/\\|=+*#%01ΔΣΩ※";

/** A heading that «decodes» itself when it changes, the way a ship console prints a new readout: random glyphs
 *  settle into the real letters from left to right in about half a second. Only the visual changes: the real text
 *  is there for screen readers from the first moment (aria-label on the caller). */
export function useDecode(text: string, ms = 520, onMount = false): string {
  const [shown, setShown] = useState(onMount && !calm ? "" : text);
  const first = useRef(true);
  useEffect(() => {
    const skip = first.current && !onMount;
    first.current = false;
    if (skip || calm) { setShown(text); return; }
    const t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      const fixed = Math.floor(k * text.length);
      let out = text.slice(0, fixed);
      for (let i = fixed; i < text.length; i++)
        out += text[i] === " " ? " " : GLYPHS[(i * 7 + Math.floor(t / 45)) % GLYPHS.length];
      setShown(out);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [text, ms]);  // eslint-disable-line react-hooks/exhaustive-deps
  return shown;
}
