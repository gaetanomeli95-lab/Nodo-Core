/** The intro and Maria's first words, glued together («la fine dell'intro e il saluto devono essere consecutivi»).
 *  The hello is asked as the intro STARTS; her voice is held back while the intro plays (so the 5-15 seconds the
 *  live voice needs to wake up are hidden behind it); the intro waits on its last held frame if she is not ready
 *  yet, then folds into the core and her voice starts exactly as the curtain opens. */
export const VOICE_READY = "nodo:voice-ready";   // the first piece of the greeting has arrived (held, not played)
export const VOICE_GO = "nodo:voice-go";         // the intro says: speak now
export const sync = {
  pending: false,   // the panel will try to greet (decided once the voice settings are known)
  waiting: false,   // the hello was asked: its audio is being held
  ready: false,     // the first audio of the hello is here
};
export const greetingExpected = () => (sync.pending || sync.waiting) && !sync.ready;
export function markReady() {
  if (sync.ready) return;
  sync.ready = true;
  window.dispatchEvent(new Event(VOICE_READY));
}
