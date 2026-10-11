/** Pure helpers for Web Speech snapshots; browsers resend old finalized results on every callback. */
export type RecognitionResultLike = ArrayLike<{ transcript: string }> & { isFinal?: boolean };

export function speechSnapshot(results: ArrayLike<RecognitionResultLike>) {
  const finals: string[] = [];
  const interim: string[] = [];
  for (const result of Array.from(results)) {
    const text = result[0]?.transcript?.trim();
    if (!text) continue;
    if (result.isFinal) finals.push(text);
    else interim.push(text);
  }
  const final = finals.join(" ");
  const tentative = interim.join(" ");
  return { final, tentative, partial: [final, tentative].filter(Boolean).join(" ") };
}

/** UI fallback: only close turns after silence following audible input. */
export function silenceDelay(snapshot: ReturnType<typeof speechSnapshot>): number | null {
  if (!snapshot.partial) return null;
  return snapshot.tentative ? 1700 : 1100;
}
