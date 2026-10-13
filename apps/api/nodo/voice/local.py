"""Opt-in offline speech providers for NODO on Windows and Linux.

No API keys and no implicit model downloads during a conversation.
The one-time setup script installs the dependencies and models explicitly.
Model inference runs in worker threads so HTTP/WebSocket handlers remain responsive.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import re
import threading
import time
import wave
from pathlib import Path

from nodo.voice.base import AudioResult, Transcript

MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_SPEECH_CHARS = 1600


def voice_files(root: str, model: str, voice: str) -> tuple[Path, Path]:
    """Resolve local assets without retrieving anything over the network."""
    location = Path(root).expanduser().resolve()
    whisper = Path(model).expanduser()
    if not whisper.is_absolute() and ("/" in model or "\\" in model):
        whisper = location / whisper
    elif not whisper.is_absolute():
        whisper = location / "whisper" / model
    piper = location / "piper" / f"{voice}.onnx"
    return whisper, piper


def pcm_samples(audio: bytes, rate: int = 16000):
    """16-bit mono PCM -> float32 at 16 kHz for Whisper, without PyAV (immune to decoder version breaks)."""
    import numpy as np
    x = np.frombuffer(audio[: len(audio) - len(audio) % 2], dtype="<i2").astype(np.float32) / 32768.0
    if rate != 16000 and len(x):
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


class LocalWhisper:
    name, location = "faster-whisper", "local"
    accepts_pcm = True   # microphone worklet frames (open-mic conversation) are wrapped into WAV here

    def __init__(self, root: str, model_name: str = "base", device: str = "cpu",
                 compute_type: str = "int8"):
        self.root, self.model_name = root, model_name
        self.device, self.compute_type = device, compute_type
        self._model = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def status(self) -> dict:
        model_dir, _ = voice_files(self.root, self.model_name, "unused")
        package = importlib.util.find_spec("faster_whisper") is not None
        ready = package and model_dir.is_dir() and (model_dir / "model.bin").is_file()
        return {"ready": ready, "model": self.model_name,
                "reason": None if ready else
                "Installa Whisper e scarica il modello con installa-voce-locale.bat"}

    def _load(self):
        with self._load_lock:
            if self._model is None:
                if not self.status()["ready"]:
                    raise RuntimeError("Modello Whisper locale non installato: esegui installa-voce-locale.bat")
                from faster_whisper import WhisperModel
                model_dir, _ = voice_files(self.root, self.model_name, "unused")
                self._model = WhisperModel(str(model_dir), device=self.device, compute_type=self.compute_type,
                                           local_files_only=True)
            return self._model

    def _transcribe(self, audio: bytes, language: str, pcm_rate: int | None = None) -> str:
        with self._infer_lock:
            model = self._load()
            if pcm_rate:
                samples = pcm_samples(audio, pcm_rate)    # open-mic frames: no decoder involved at all
            else:
                from faster_whisper import decode_audio
                # PyAV (bundled in faster-whisper) handles WebM/Opus/WAV without a system FFmpeg install.
                samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)
            if not len(samples):
                return ""
            segments, _ = model.transcribe(samples, language=language.split("-")[0], beam_size=2,
                                           temperature=0, vad_filter=False, condition_on_previous_text=False)
            return " ".join(segment.text.strip() for segment in segments).strip()

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        if not audio:
            return Transcript("", language, None, self.name)
        if len(audio) > MAX_AUDIO_BYTES:
            raise ValueError("Registrazione troppo lunga (limite 12 MB): dividi la richiesta in turni più brevi")
        rate = None
        if (mime or "").startswith("audio/pcm"):
            m = re.search(r"rate=(\d+)", mime)
            rate = int(m.group(1)) if m else 16000
        started = time.perf_counter()
        text = await asyncio.to_thread(self._transcribe, audio, language, rate)
        return Transcript(text, language, None, self.name, int((time.perf_counter() - started) * 1000))


class LocalPiper:
    name, location = "piper", "local"

    def __init__(self, root: str, voice: str = "it_IT-paola-medium"):
        self.root, self.voice = root, voice
        self._voice = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def status(self) -> dict:
        _, path = voice_files(self.root, "unused", self.voice)
        ready = (importlib.util.find_spec("piper") is not None and path.is_file()
                 and Path(str(path) + ".json").is_file())
        return {"ready": ready, "voice": self.voice,
                "reason": None if ready else "Installa Piper e la voce italiana con installa-voce-locale.bat"}

    def _load(self):
        with self._load_lock:
            if self._voice is None:
                if not self.status()["ready"]:
                    raise RuntimeError("Modello Piper locale non installato: esegui installa-voce-locale.bat")
                from piper import PiperVoice
                _, path = voice_files(self.root, "unused", self.voice)
                self._voice = PiperVoice.load(str(path))
            return self._voice

    def _synthesize(self, text: str) -> bytes:
        with self._infer_lock:
            voice = self._load()
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                voice.synthesize_wav(text, wav)
            return output.getvalue()

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        if len(text) > MAX_SPEECH_CHARS:
            raise ValueError("Testo troppo lungo per un singolo segmento vocale")
        if not text.strip():
            raise ValueError("Testo vuoto")
        # The installed voice is explicit: never download or change it based on untrusted model output.
        audio = await asyncio.to_thread(self._synthesize, text)
        return AudioResult(audio, "audio/wav", self.name)
