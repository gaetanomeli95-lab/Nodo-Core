import { useEffect, useRef } from "react";
import type { PresenceMode } from "../Presence";

type Bins = { input: Uint8Array<ArrayBuffer> | null; output: Uint8Array<ArrayBuffer> | null };

/** NODO's body: ~12.000 particles of light (WebGL2). At start-up they fly in from deep space and assemble into a
 *  living sphere; the surface breathes with simplex noise and ripples with the real frequencies of the voice -
 *  yours while you speak, hers while she answers. Colour follows the state. Falls back to nothing (the 2D
 *  presence underneath keeps working) when WebGL2 is not available. */
const VERT = `#version 300 es
precision highp float;
in vec3 aSphere; in vec3 aStart; in float aSeed;
uniform float uTime, uBoot, uEnergy, uSpin, uAspect, uScale, uMode;
uniform float uBands[16];
uniform vec3 uC1, uC2;
out vec3 vColor; out float vAlpha;
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(((x*34.)+1.)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-.85373472095314*r;}
float snoise(vec3 v){const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);
 vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;
 vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
 i=mod289(i);vec4 p=permute(permute(permute(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
 float n_=.142857142857;vec3 ns=n_*D.wyz-D.xzx;vec4 j=p-49.*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
 vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
 vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
 vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
 vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
 vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);m=m*m;
 return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));}
void main(){
  float t = clamp((uBoot - aSeed * .55) / .45, 0., 1.);
  float ease = 1. - pow(1. - t, 3.);
  vec3 n = normalize(aSphere);
  float lat = acos(clamp(n.y, -1., 1.)) / 3.14159;
  int bi = int(clamp(abs(lat - .5) * 2. * 15.99, 0., 15.));
  float band = uBands[bi];
  float wobble = snoise(n * 1.7 + vec3(uTime * .22)) * (.06 + uEnergy * .18)
               + snoise(n * 4.5 - vec3(uTime * .5)) * .025 * (1. + uMode);
  vec3 surface = n * (1. + wobble + band * .42);
  vec3 swirl = vec3(sin(uTime * .3 + aSeed * 40.), cos(uTime * .27 + aSeed * 30.), sin(uTime * .2 + aSeed * 20.)) * .03;
  vec3 p = mix(aStart, surface + swirl, ease);
  float cs = cos(uSpin), sn = sin(uSpin);
  p = vec3(p.x * cs + p.z * sn, p.y, -p.x * sn + p.z * cs);
  float ct = cos(.38), st = sin(.38);
  p = vec3(p.x, p.y * ct - p.z * st, p.y * st + p.z * ct);
  float z = p.z + 3.4;
  gl_Position = vec4(p.x / z * uScale / uAspect, p.y / z * uScale, 0., 1.);
  float depth = clamp((3.4 - p.z) / 2.4 - .6, 0., 1.);
  gl_PointSize = (1.2 + 2.6 * depth + band * 3.) * (1. + uEnergy * .6) * uScale * .55;
  float k = .5 + .5 * snoise(n * 2.2 + vec3(uTime * .1));
  vColor = mix(uC1, uC2, k) * (.55 + .7 * depth) + band * .35;
  vAlpha = (.18 + .62 * depth) * mix(.25, 1., ease);
}`;
const FRAG = `#version 300 es
precision mediump float;
in vec3 vColor; in float vAlpha; out vec4 o;
void main(){ vec2 d = gl_PointCoord - .5; float r = dot(d, d); if (r > .25) discard;
  float a = smoothstep(.25, .0, r); o = vec4(vColor * a * vAlpha, a * vAlpha); }`;

const COLORS: Record<PresenceMode, [number[], number[]]> = {
  rest: [[0.45, 0.95, 0.88], [0.45, 0.62, 1.0]], listen: [[0.42, 0.9, 1.0], [0.3, 1.0, 0.85]],
  think: [[0.72, 0.6, 1.0], [0.38, 0.75, 1.0]], act: [[0.45, 1.0, 0.8], [0.5, 0.65, 1.0]],
  speak: [[1.0, 0.82, 0.52], [0.45, 0.92, 0.9]], approve: [[1.0, 0.76, 0.45], [1.0, 0.9, 0.7]],
  reconnect: [[0.7, 0.72, 0.8], [1.0, 0.65, 0.5]],
};

export function HoloCore({ mode, sampleLevel, spectrum, boot }: {
  mode: PresenceMode; sampleLevel: () => { input: number; output: number }; spectrum?: () => Bins; boot: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const glow = useRef<HTMLDivElement>(null);
  const latest = useRef({ mode, sampleLevel, spectrum });
  latest.current = { mode, sampleLevel, spectrum };
  useEffect(() => {
    const el = ref.current!;
    const gl = el.getContext("webgl2", { premultipliedAlpha: true, antialias: false, alpha: true });
    if (!gl) { el.style.display = "none"; return; }
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const sh = (type: number, src: string) => {
      const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn(gl.getShaderInfoLog(s));
      return s;
    };
    const prog = gl.createProgram()!;
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { el.style.display = "none"; return; }
    gl.useProgram(prog);
    const N = innerWidth < 700 ? 6000 : 12000;
    const sphere = new Float32Array(N * 3), start = new Float32Array(N * 3), seed = new Float32Array(N);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = golden * i;
      const jitter = 1 + (Math.random() - 0.5) * 0.04;
      sphere.set([Math.cos(th) * r * jitter, y * jitter, Math.sin(th) * r * jitter], i * 3);
      const u = Math.random() * Math.PI * 2, v = Math.acos(2 * Math.random() - 1), R = 4 + Math.random() * 6;
      start.set([Math.sin(v) * Math.cos(u) * R, Math.cos(v) * R * 0.6, Math.sin(v) * Math.sin(u) * R - 2], i * 3);
      seed[i] = Math.random();
    }
    const attr = (name: string, data: Float32Array, size: number) => {
      const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, name); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    };
    attr("aSphere", sphere, 3); attr("aStart", start, 3); attr("aSeed", seed, 1);
    const U = (n: string) => gl.getUniformLocation(prog, n);
    const uTime = U("uTime"), uBoot = U("uBoot"), uEnergy = U("uEnergy"), uSpin = U("uSpin"), uAspect = U("uAspect"),
      uScale = U("uScale"), uMode = U("uMode"), uBands = U("uBands"), uC1 = U("uC1"), uC2 = U("uC2");
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
    let raf = 0, last = 0, energy = 0, spin = 0, bootT = boot && !reduced ? 0 : 1;
    const bands = new Float32Array(16);
    let c1 = [...COLORS.rest[0]], c2 = [...COLORS.rest[1]];
    const t0 = performance.now();
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (document.hidden) return;
      const dt = Math.min(0.05, (now - (last || now)) / 1000); last = now;
      const { width: w, height: h } = el.getBoundingClientRect();
      const dpr = Math.min(devicePixelRatio || 1, 2);
      if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) {
        el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      }
      gl.viewport(0, 0, el.width, el.height);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      const { mode: m, sampleLevel: read, spectrum: spec } = latest.current;
      const lv = read();
      const target = Math.min(1, (m === "speak" ? lv.output : m === "listen" ? lv.input : 0) * 9);
      energy += (target - energy) * (target > energy ? 0.5 : 0.1);
      const b = spec?.();
      const src = m === "speak" ? b?.output : m === "listen" ? b?.input : null;
      for (let i = 0; i < 16; i++) {
        let v = 0;
        if (src) { const idx = Math.min(src.length - 1, Math.floor((i / 16) ** 1.5 * src.length * 0.6) + 1); v = src[idx] / 255; }
        else if (m === "think" || m === "act") v = 0.12 + 0.1 * Math.sin(now * 0.004 + i * 0.7);
        bands[i] += (v * (src ? 0.9 : 1) - bands[i]) * (v > bands[i] ? 0.5 : 0.12);
      }
      const [t1, t2] = COLORS[m];
      c1 = c1.map((x, i) => x + (t1[i] - x) * 0.06); c2 = c2.map((x, i) => x + (t2[i] - x) * 0.06);
      if (bootT < 1) bootT = Math.min(1, bootT + dt / 3.2);
      spin += dt * (reduced ? 0 : m === "think" ? 0.55 : m === "act" ? 0.8 : 0.16);
      gl.uniform1f(uTime, reduced ? 0 : (now - t0) / 1000); gl.uniform1f(uBoot, bootT); gl.uniform1f(uEnergy, energy);
      gl.uniform1f(uSpin, spin); gl.uniform1f(uAspect, w / Math.max(1, h));
      gl.uniform1f(uScale, Math.min(1.25, (Math.min(w, h * 1.4) / Math.max(1, h)) * 0.95));
      gl.uniform1f(uMode, m === "think" || m === "act" ? 1 : 0);
      gl.uniform1fv(uBands, bands); gl.uniform3fv(uC1, c1); gl.uniform3fv(uC2, c2);
      gl.drawArrays(gl.POINTS, 0, N);
      if (glow.current) {
        glow.current.style.opacity = String(0.35 + energy * 0.5 + bands[0] * 0.3);
        glow.current.style.transform = `translate(-50%,-50%) scale(${0.9 + energy * 0.35})`;
        glow.current.style.background = `radial-gradient(circle, rgba(${c1.map((x) => Math.round(x * 255)).join(",")},.55), transparent 62%)`;
      }
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); gl.getExtension("WEBGL_lose_context")?.loseContext(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <div className="holo-core-wrap" aria-hidden="true"><div ref={glow} className="holo-core-glow" /><canvas ref={ref} className="holo-core" /></div>;
}
