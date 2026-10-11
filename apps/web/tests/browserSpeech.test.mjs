import test from "node:test";
import assert from "node:assert/strict";
import { speechSnapshot, silenceDelay } from "../src/components/browserSpeech.ts";

const final = (transcript) => ({ isFinal: true, 0: { transcript }, length: 1 });
const interim = (transcript) => ({ isFinal: false, 0: { transcript }, length: 1 });

test("repeated final results never double the words", () => {
  const original = speechSnapshot([final("Buongiorno NODO")]);
  const repeated = speechSnapshot([final("Buongiorno NODO"), interim("come")]);
  const completed = speechSnapshot([final("Buongiorno NODO"), final("come stai?")]);
  assert.equal(original.final, "Buongiorno NODO");
  assert.equal(repeated.final, "Buongiorno NODO");
  assert.equal(completed.final, "Buongiorno NODO come stai?");
  assert.equal(completed.partial, "Buongiorno NODO come stai?");
});

test("interim revisions replace earlier guesses rather than appending", () => {
  const a = speechSnapshot([interim("Come va")]);
  const b = speechSnapshot([interim("Come vanno i siti?")]);
  assert.equal(a.partial, "Come va");
  assert.equal(b.partial, "Come vanno i siti?");
});

test("silence boundary waits longer while recognizer is still uncertain", () => {
  assert.equal(silenceDelay(speechSnapshot([])), null);
  assert.equal(silenceDelay(speechSnapshot([interim("Sto parlando")])), 1700);
  assert.equal(silenceDelay(speechSnapshot([final("Ho finito")])), 1100);
});

test("empty browser recognition input never starts a turn", () => {
  const s = speechSnapshot([interim("   ")]);
  assert.equal(s.partial, "");
  assert.equal(silenceDelay(s), null);
});
