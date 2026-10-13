"""Real audio round trip for the offline voice: Piper says a sentence, Whisper must hear it back.

    python -m nodo.voice.selftest            (run by installa-voce-locale.bat after the downloads)

It exercises BOTH Whisper input paths: the WAV file path (PyAV decoder: catches a broken PyAV version) and the
open-microphone PCM path. Exit code 0 = the voice works on this PC, 1 = it does not (the message says why).
"""
from __future__ import annotations

import asyncio
import io
import sys
import wave

PHRASE = "Ciao Gaetano, sono Maria. Prova audio riuscita."


def _pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


async def run(root: str, whisper_model: str, voice: str) -> tuple[bool, str]:
    from nodo.voice.local import LocalPiper, LocalWhisper
    tts, stt = LocalPiper(root, voice), LocalWhisper(root, whisper_model)
    for part in (tts, stt):
        st = part.status()
        if not st["ready"]:
            return False, st["reason"] or f"{part.name} non pronto"
    audio = (await tts.synthesize(PHRASE)).audio
    heard = []
    try:
        heard.append((await stt.transcribe(audio, "audio/wav", "it")).text)          # decoder path (PyAV)
    except Exception as e:  # noqa: BLE001 - this is exactly what the test is for
        return False, (f"Whisper non riesce a leggere un file audio ({type(e).__name__}: {e}). "
                       "Di solito è la versione di PyAV: reinstalla con installa-voce-locale.bat.")
    pcm, rate = _pcm(audio)
    heard.append((await stt.transcribe(pcm, f"audio/pcm;rate={rate}", "it")).text)    # open-mic path
    words = {"ciao", "gaetano", "maria", "prova"}
    for text in heard:
        if len(words & set(text.lower().replace(",", " ").replace(".", " ").split())) < 2:
            return False, f"Whisper ha capito «{text}» invece di «{PHRASE}»"
    return True, f"Piper parla e Whisper capisce: «{heard[0]}»"


def main() -> int:
    from nodo.config import get_settings
    s = get_settings()
    ok, msg = asyncio.run(run(s.local_voice_dir, s.whisper_model, s.piper_voice))
    print(("[OK] " if ok else "[ERRORE] ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
