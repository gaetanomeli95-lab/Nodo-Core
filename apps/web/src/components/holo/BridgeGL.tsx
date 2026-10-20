import { useEffect, useRef } from "react";
import { calm, perf } from "../../motion";
import type { PresenceMode } from "../Presence";

/** The bridge of «NODO 2492» (approved by Gaetano on the test page, 19-20/10): one WebGL canvas behind the room.
 *  A sky with two nebulae and an amber dawn, a floor of light running to the horizon, and Maria: a sphere of
 *  thousands of points that breathes with the real voice level, three armillary rings, a scanner passing over her.
 *  Colours follow her state. The GPU draws it all in two calls per frame; on a small PC it lowers itself
 *  (fewer points, lower resolution) after measuring the real frame rate, and it stops when the tab is not shown. */

type Level = () => { input: number; output: number };
export const bridgeStats = { fps: 0, tier: 2 };

const PAL: Record<PresenceMode, { a: number[]; b: number[]; spin: number }> = {
  rest: { a: [0.31, 0.94, 1.0], b: [1.0, 0.31, 0.85], spin: 0.12 },
  listen: { a: [0.36, 1.0, 0.69], b: [0.31, 0.94, 1.0], spin: 0.18 },
  think: { a: [0.61, 0.48, 1.0], b: [1.0, 0.31, 0.85], spin: 0.85 },
  act: { a: [0.61, 0.48, 1.0], b: [1.0, 0.71, 0.28], spin: 0.7 },
  speak: { a: [1.0, 0.71, 0.28], b: [1.0, 0.42, 0.42], spin: 0.25 },
  approve: { a: [1.0, 0.71, 0.28], b: [1.0, 0.31, 0.85], spin: 0.06 },
  reconnect: { a: [1.0, 0.42, 0.42], b: [0.61, 0.48, 1.0], spin: 0.04 },
};
export const STATE_CSS: Record<PresenceMode, [string, string]> = {
  rest: ["#4ff0ff", "#ff4fd8"], listen: ["#5cffb0", "#4ff0ff"], think: ["#9b7bff", "#ff4fd8"], act: ["#9b7bff", "#ffb547"],
  speak: ["#ffb547", "#ff6b6b"], approve: ["#ffb547", "#ff4fd8"], reconnect: ["#ff6b6b", "#9b7bff"],
};

const BG_FS = `
precision mediump float;
uniform vec2 uRes; uniform float uT; uniform vec3 uC; uniform vec3 uC2; uniform vec2 uM; uniform float uSp; uniform float uSw;
uniform float uCy; uniform float uDim;
float h(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void main(){
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  vec3 c = vec3(0.016, 0.012, 0.04);
  vec2 q = p - vec2(0., uCy);
  c += uC * 0.08 * exp(-2.6 * length(q * vec2(0.8, 1.2)));
  c += vec3(0.55, 0.12, 0.5) * 0.16 * exp(-2.2 * length((p - vec2(-0.85, 0.42)) * vec2(0.7, 1.1)));
  c += vec3(0.05, 0.35, 0.45) * 0.16 * exp(-2.0 * length((p - vec2(0.9, 0.25)) * vec2(0.8, 1.2)));
  c += vec3(1.0, 0.55, 0.2) * 0.07 * exp(-6.0 * length((p - vec2(0.0, -0.2)) * vec2(0.35, 2.4)));
  vec2 g = floor(gl_FragCoord.xy / 2.5); float s = h(g);
  if (s > 0.9965) c += vec3(0.65, 0.85, 1.0) * (0.35 + 0.35 * sin(uT * 1.7 + s * 300.));
  float hz = -0.2 + uM.y * 0.025;
  float d = hz - p.y;
  if (d > 0.0) {
    float z = 0.42 / d; float x = (p.x + uM.x * 0.06) * z;
    vec2 gp = vec2(x * 1.6, z * 1.6 + uT * uSp);
    vec2 e = 0.5 - abs(fract(gp) - 0.5);
    vec2 w = vec2(z * 1.6, 0.42 * 1.6 / (d * d)) / uRes.y * 1.4;
    float ln = max(1. - smoothstep(0., w.x, e.x), 1. - smoothstep(0., min(w.y, .45), e.y));
    float fade = exp(-z * 0.11) * smoothstep(0.0, 0.05, d);
    vec3 gc = mix(uC, uC2, exp(-z * 0.18));
    c += gc * ln * fade * 0.62;
    c += uC2 * 0.06 * exp(-z * 0.25);
  }
  c += mix(uC2, vec3(1., .7, .35), .45) * (0.6 * exp(-abs(d) * 70.) + 0.12 * exp(-abs(d) * 9.));
  float sy = mix(0.7, -0.7, uSw);
  c += uC * (0.05 * exp(-pow((p.y - sy) * 9., 2.)) + 0.18 * exp(-pow((p.y - sy) * 260., 2.)));
  c *= 0.93 + 0.07 * sin(gl_FragCoord.y * 3.1416);
  c *= (1.0 - 0.5 * dot(p, p)) * uDim;
  gl_FragColor = vec4(c, 1.);
}`;

const PT_VS = `
attribute vec4 a;
uniform float uT, uAsp, uL, uSpin, uScan, uSc, uDpr, uCy, uFl, uDim; uniform vec2 uM; uniform vec3 uC; uniform vec3 uC2;
varying float vA; varying float vK; varying vec3 vC;
mat3 rx(float t){ float c=cos(t), s=sin(t); return mat3(1.,0.,0., 0.,c,s, 0.,-s,c); }
mat3 ry(float t){ float c=cos(t), s=sin(t); return mat3(c,0.,-s, 0.,1.,0., s,0.,c); }
mat3 rz(float t){ float c=cos(t), s=sin(t); return mat3(c,s,0., -s,c,0., 0.,0.,1.); }
void main(){
  float kind = floor(a.w); float seed = fract(a.w);
  vec3 p; float size = 1.7; float alpha = 0.55;
  if (kind < 0.5) {
    p = a.xyz;
    float n = sin(p.x * 5. + uT * 1.6) * sin(p.y * 6. - uT * 1.3) * sin(p.z * 4. + uT * 1.1);
    p *= 1. + uL * 0.22 * n + 0.012 * sin(uT * 2. + seed * 40.);
    p = ry(uSpin) * p;
  } else if (kind < 3.5) {
    float ang = seed * 6.2832 + uT * (kind == 1. ? 0.5 : kind == 2. ? -0.35 : 0.22);
    p = vec3(cos(ang) * a.x, 0., sin(ang) * a.x);
    p = rx(a.y) * rz(a.z + uSpin * (kind == 2. ? -0.6 : 0.4)) * p;
    size = 2.1; alpha = 0.85 + uFl * 0.6;
  } else {
    p = vec3(0.); size = 110. + uL * 150. + uFl * 90.; alpha = 1.;
  }
  p = rx(0.32 + uM.y * 0.18) * ry(uM.x * 0.35) * p;
  float z = p.z + 3.4;
  vec2 s = p.xy / z * 2.6 * uSc;
  gl_Position = vec4(s.x / uAsp, s.y + uCy, 0., 1.);
  float depth = clamp(1. - (p.z + 1.3) / 2.6, 0., 1.);
  float scan = kind < 0.5 ? exp(-pow((p.y - uScan) * 12., 2.)) : 0.;
  vA = (kind > 3.5 ? 1. : alpha * (0.25 + 0.75 * depth) + scan * 1.4) * uDim;
  vK = kind;
  vC = kind < 0.5 ? mix(uC2, uC, smoothstep(-0.9, 0.7, a.y)) : kind < 1.5 ? vec3(1.0, 0.71, 0.28) : kind < 2.5 ? uC2 : kind < 3.5 ? uC : mix(uC, vec3(1.), 0.55);
  vC = mix(vC, vec3(1.), scan * 0.6);
  gl_PointSize = kind > 3.5 ? size * uDpr * uSc : size * uDpr * (3.4 / z) * (1. + scan * 0.8);
}`;
const PT_FS = `
precision mediump float;
varying float vA; varying float vK; varying vec3 vC;
void main(){
  vec2 d = gl_PointCoord - 0.5; float r = length(d);
  if (vK > 3.5) { float g = exp(-r * r * 26.) * 0.9 + exp(-r * r * 300.) * 1.2; gl_FragColor = vec4(vC * g * vA, 1.); return; }
  if (r > 0.5) discard;
  float a = vA * (1. - r * 2.);
  gl_FragColor = vec4(mix(vC, vec3(1.), 0.18) * a, 1.);
}`;

function points(nShell: number, nRing: number) {
  const arr: number[] = [];
  for (let i = 0; i < nShell; i++) {                       // Fibonacci sphere
    const y = 1 - (i + 0.5) / nShell * 2, r = Math.sqrt(1 - y * y), th = i * 2.39996323;
    arr.push(Math.cos(th) * r, y, Math.sin(th) * r, Math.random() * 0.999);
  }
  for (const [k, rad, tx, tz] of [[1, 1.32, 0, 0.35], [2, 1.5, 1.15, -0.25], [3, 1.68, 0.5, 0.9]]) {
    for (let i = 0; i < nRing; i++) {
      const u = i / nRing;
      if (Math.floor(u * 24) % 4 === 3) continue;            // dashed rings
      arr.push(rad, tx, tz, k + u * 0.999);
    }
  }
  arr.push(0, 0, 0, 4);
  return new Float32Array(arr);
}
const TIERS = [{ dpr: 0.6, shell: 1500, ring: 220 }, { dpr: 1, shell: 2600, ring: 300 }, { dpr: 1.5, shell: 5200, ring: 520 }];

export function BridgeGL({ mode, sampleLevel, pulse, dim }: { mode: PresenceMode; sampleLevel: Level; pulse: number; dim: boolean }) {
  const cv = useRef<HTMLCanvasElement>(null);
  const live = useRef({ mode, sampleLevel, dim, pulseAt: -1e9 });
  live.current.mode = mode; live.current.sampleLevel = sampleLevel; live.current.dim = dim;
  useEffect(() => { if (pulse) live.current.pulseAt = performance.now(); }, [pulse]);

  useEffect(() => {
    const c = cv.current!;
    const gl = c.getContext("webgl", { antialias: false, alpha: false, powerPreference: "high-performance" });
    if (!gl) { c.style.display = "none"; return; }           // no 3D here: the starfield behind stays
    const sh = (type: number, src: string) => { const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = (vs: string, fs: string) => { const p = gl.createProgram()!; gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
      gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); return p; };
    const bgP = prog("attribute vec2 a; void main(){ gl_Position = vec4(a, 0., 1.); }", BG_FS);
    const ptP = prog(PT_VS, PT_FS);
    if (!gl.getProgramParameter(bgP, gl.LINK_STATUS) || !gl.getProgramParameter(ptP, gl.LINK_STATUS)) { c.style.display = "none"; return; }
    const quad = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const pts = gl.createBuffer(); let nPts = 0;
    let tier = perf.low ? 1 : 2;
    const setTier = (t: number) => {
      tier = t; bridgeStats.tier = t;
      const d = points(TIERS[t].shell, TIERS[t].ring);
      gl.bindBuffer(gl.ARRAY_BUFFER, pts); gl.bufferData(gl.ARRAY_BUFFER, d, gl.STATIC_DRAW); nPts = d.length / 4;
      resize();
    };
    const U = (p: WebGLProgram, names: string[]) => Object.fromEntries(names.map((n) => [n, gl.getUniformLocation(p, n)]));
    const bu = U(bgP, ["uRes", "uT", "uC", "uC2", "uM", "uSp", "uSw", "uCy", "uDim"]);
    const pu = U(ptP, ["uT", "uAsp", "uL", "uSpin", "uScan", "uSc", "uDpr", "uC", "uC2", "uM", "uCy", "uFl", "uDim"]);
    const ba = gl.getAttribLocation(bgP, "a"), pa = gl.getAttribLocation(ptP, "a");
    const geo = { cy: 0.12, sc: 0.44, dpr: 1 };
    const root = document.documentElement;
    function resize() {
      const W = innerWidth, H = innerHeight;
      geo.dpr = Math.min(window.devicePixelRatio || 1, TIERS[tier].dpr);
      c.width = Math.max(1, Math.round(W * geo.dpr)); c.height = Math.max(1, Math.round(H * geo.dpr));
      gl!.viewport(0, 0, c.width, c.height);
      const narrow = W < 980;
      geo.cy = narrow ? 0.2 : 0.17;
      geo.sc = Math.min(1, (W / H) / 1.3) * (narrow ? 0.5 : 0.41) * Math.min(1, H / 760 + 0.12);
      const R = (2.6 * geo.sc / 3.4) * H / 2, cyPx = (1 - (geo.cy + 1) / 2) * H;   // the sphere in pixels, for the HTML rings
      root.style.setProperty("--bridge-r", `${R.toFixed(1)}px`);
      root.style.setProperty("--bridge-cy", `${cyPx.toFixed(1)}px`);
    }
    setTier(tier);
    addEventListener("resize", resize);

    let mx = 0, my = 0, smx = 0, smy = 0;
    const move = (e: PointerEvent) => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; };
    addEventListener("pointermove", move, { passive: true });

    const col = [...PAL.rest.a], col2 = [...PAL.rest.b];
    let raf = 0, last = performance.now(), spin = 0.12, spinA = 0, level = 0, frames = 0, fpsT = last, shownFor = 0, dimK = 1;
    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      const visible = !document.hidden && c.getClientRects().length > 0;
      if (!visible) { last = t; fpsT = t; frames = 0; return; }
      if (tier === 0 && bridgeStats.fps && bridgeStats.fps < 24 && t - last < 50) return;   // last resort: 20 frames, steady
      const dt = Math.min(0.1, (t - last) / 1000); last = t;
      frames++; shownFor += dt;
      if (t - fpsT > 2000) {                                  // measured, then lowered if this PC struggles
        bridgeStats.fps = Math.round(frames * 1000 / (t - fpsT)); frames = 0; fpsT = t;
        if (shownFor > 6 && bridgeStats.fps < 38 && tier > 0 && !document.querySelector(".intro")) { setTier(tier - 1); shownFor = 0; }
      }
      const L = live.current, P = PAL[L.mode] || PAL.rest, k = Math.min(1, dt * 2.5);
      for (let i = 0; i < 3; i++) { col[i] += (P.a[i] - col[i]) * k; col2[i] += (P.b[i] - col2[i]) * k; }
      spin += (P.spin - spin) * Math.min(1, dt * 1.5);
      spinA += dt * spin * (calm ? 0.2 : 1);
      const lv = L.sampleLevel();
      level += (Math.min(1, Math.max(lv.input, lv.output) * 1.6) - level) * Math.min(1, dt * 12);
      dimK += ((L.dim ? 0.38 : 1) - dimK) * Math.min(1, dt * 3);
      const flare = Math.max(0, 1 - (t - L.pulseAt) / 1500);
      smx += (mx - smx) * Math.min(1, dt * 3); smy += (my - smy) * Math.min(1, dt * 3);
      const ts = t / 1000, W = c.width, H = c.height, think = L.mode === "think" || L.mode === "act";

      gl.disable(gl.BLEND);
      gl.useProgram(bgP);
      gl.bindBuffer(gl.ARRAY_BUFFER, quad); gl.enableVertexAttribArray(ba); gl.vertexAttribPointer(ba, 2, gl.FLOAT, false, 0, 0);
      gl.uniform2f(bu.uRes, W, H); gl.uniform1f(bu.uT, ts); gl.uniform3fv(bu.uC, col); gl.uniform3fv(bu.uC2, col2);
      gl.uniform2f(bu.uM, smx, smy); gl.uniform1f(bu.uSp, calm ? 0.05 : think ? 1.4 : 0.45);
      gl.uniform1f(bu.uSw, Math.min(1, (ts * (think ? 0.33 : 0.09)) % 1.25)); gl.uniform1f(bu.uCy, geo.cy * 0.5);
      gl.uniform1f(bu.uDim, 0.55 + 0.45 * dimK);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
      gl.useProgram(ptP);
      gl.bindBuffer(gl.ARRAY_BUFFER, pts); gl.enableVertexAttribArray(pa); gl.vertexAttribPointer(pa, 4, gl.FLOAT, false, 0, 0);
      gl.uniform1f(pu.uT, ts); gl.uniform1f(pu.uAsp, W / H); gl.uniform1f(pu.uL, level); gl.uniform1f(pu.uSpin, spinA);
      gl.uniform1f(pu.uScan, Math.sin(ts * (think ? 2.4 : 0.7)) * 1.05); gl.uniform1f(pu.uSc, geo.sc); gl.uniform1f(pu.uDpr, geo.dpr);
      gl.uniform3fv(pu.uC, col); gl.uniform3fv(pu.uC2, col2); gl.uniform2f(pu.uM, smx, smy); gl.uniform1f(pu.uCy, geo.cy);
      gl.uniform1f(pu.uFl, flare); gl.uniform1f(pu.uDim, dimK);
      gl.drawArrays(gl.POINTS, 0, nPts);
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); removeEventListener("resize", resize); removeEventListener("pointermove", move); };
  }, []);

  return <canvas ref={cv} className="bridge-gl" aria-hidden="true" />;
}
