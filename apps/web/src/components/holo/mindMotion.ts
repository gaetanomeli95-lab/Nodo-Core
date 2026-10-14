/** Canvas ellipse radii must stay positive even when the orbit is tilted below the horizon. */
export function orbitRadii(size: number, tiltSine: number) {
  return {
    x: Math.max(1, size * 1.62),
    y: Math.max(1, size * (1.62 * Math.abs(tiltSine) * 0.9 + 0.08)),
  };
}
