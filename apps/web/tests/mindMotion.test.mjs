import test from "node:test";
import assert from "node:assert/strict";
import { orbitRadii } from "../src/components/holo/mindMotion.ts";

test("the orbit remains drawable at every allowed tilt, including negative tilt", () => {
  for (const size of [0, 1, 120, 400]) {
    for (let tilt = -0.2; tilt <= 1.1; tilt += 0.01) {
      const orbit = orbitRadii(size, Math.sin(tilt));
      assert.ok(Number.isFinite(orbit.x) && orbit.x > 0);
      assert.ok(Number.isFinite(orbit.y) && orbit.y > 0);
    }
  }
});
