"""Hands-free voice loop: wake word -> record -> transcribe -> answer -> speak.

Audio is analysed on this PC. Nothing is recorded or sent anywhere until the wake word fires,
and then only the transcribed question goes to the selected AI.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path

import numpy as np

from .. import assistant, config, events

RATE = 16000
FRAME = 1280            # 80 ms, what openWakeWord expects
VAD_WINDOW = 512        # what Silero expects at 16 kHz
END_SILENCE_S = 0.8
MAX_QUESTION_S = 20
NO_SPEECH_S = 6
FOLLOW_UP_S = 4
WAKE_COOLDOWN_S = 2
WAKE_NAME = "hey_jarvis"

_MOONSHINE = (Path.home() / "AppData/Local/moonshine_voice/moonshine_voice/Cache"
              / "download.moonshine.ai/model/base-en/quantized/base-en")
_PIPER = config.MODELS / "piper" / "en_US-lessac-medium.onnx"

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_sentences(buffer: str, min_chars: int = 8) -> tuple[list[str], str]:
    """Pull finished sentences out of streaming text. Returns (sentences, remainder)."""
    out, start = [], 0
    for m in _SENTENCE_END.finditer(buffer):
        piece = buffer[start:m.start()].strip()
        if len(piece) >= min_chars:
            out.append(piece)
            start = m.end()
    return out, buffer[start:]


def speakable(text: str) -> str:
    """Remove what shouldn't be read aloud even if the model includes it."""
    text = re.sub(r"\[[^\]]*p\.\s*\d+[^\]]*\]", "", text)
    text = re.sub(r"[*_#`$\\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class VoiceEngine:
    def __init__(self, ask=assistant.ask):
        self._ask = ask
        self.state = "off"
        self.detail: str | None = None
        self.muted = False
        self._audio: queue.Queue = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._turn = 0                 # bumping this abandons the answer in progress
        self._manual = threading.Event()
        self._wake = None
        self._vad = None
        self._stt = None
        self._tts = None
        self._last_wake = 0.0
        self._reset_recording()

    # ---- public --------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._run, name="margin-voice", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def trigger(self) -> None:
        """Start listening without the wake word (the Talk button)."""
        self._manual.set()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:  # stops the speech; the written answer still finishes
            self._stop_audio()
        self._set_state("muted" if muted else "idle")

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "muted": self.muted}

    # ---- setup ---------------------------------------------------------

    def load(self) -> None:
        self._set_state("loading", "Getting the voice models ready")
        import openwakeword
        import torch
        from openwakeword.model import Model
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)
        try:
            self._wake = Model(wakeword_models=[WAKE_NAME], inference_framework="onnx")
        except Exception:
            openwakeword.utils.download_models(model_names=["hey_jarvis_v0.1"])
            self._wake = Model(wakeword_models=[WAKE_NAME], inference_framework="onnx")
        self._vad = load_silero_vad()

        from moonshine_voice.moonshine_api import ModelArch
        from moonshine_voice.transcriber import Transcriber
        if _MOONSHINE.exists():
            stt_path, arch = str(_MOONSHINE), ModelArch.BASE
        else:  # first run on this PC: download the English model
            from moonshine_voice import get_model_for_language
            stt_path, arch = get_model_for_language("en", ModelArch.BASE)
        self._stt = Transcriber(model_path=str(stt_path), model_arch=arch)

        if not _PIPER.exists():  # first run on this PC: download the voice
            import subprocess
            import sys
            _PIPER.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "piper.download_voices", _PIPER.stem,
                            "--download-dir", str(_PIPER.parent)], check=True, capture_output=True)
        from piper import PiperVoice
        self._tts = PiperVoice.load(str(_PIPER))

    def _run(self) -> None:
        try:
            import sounddevice as sd
            self.load()
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                    callback=self._on_audio)
        except Exception as e:
            self._set_state("error", _friendly(e))
            return
        self._set_state("muted" if self.muted else "idle")
        with stream:
            while not self._stop.is_set():
                try:
                    frame = self._audio.get(timeout=0.5)
                except queue.Empty:
                    continue
                self.feed(frame)

    def _on_audio(self, indata, _frames, _time, _status) -> None:
        try:
            self._audio.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    # ---- audio state machine -------------------------------------------

    def feed(self, frame: np.ndarray) -> None:
        """Process one 80 ms int16 frame."""
        if self.muted or not config.load_settings()["wake_enabled"] and not self._manual.is_set() and self.state == "idle":
            return
        if self._manual.is_set():
            self._manual.clear()
            self._interrupt()
            self._begin_listening()
            return
        if self.state == "listening":
            self._record(frame)
        else:
            self._check_wake(frame)

    def _check_wake(self, frame: np.ndarray) -> None:
        threshold = config.load_settings()["wake_threshold"]
        score = self._wake.predict(frame).get(WAKE_NAME, 0.0)
        if score >= threshold and time.monotonic() - self._last_wake > WAKE_COOLDOWN_S:
            self._last_wake = time.monotonic()
            self._wake.reset()
            self._interrupt()
            self._chime()
            self._begin_listening()

    def _reset_recording(self) -> None:
        self._rec: list[np.ndarray] = []
        self._pending = np.zeros(0, dtype=np.int16)
        self._heard_speech = False
        self._silence_s = 0.0
        self._elapsed_s = 0.0
        self._no_speech_limit = NO_SPEECH_S

    def _begin_listening(self, follow_up: bool = False) -> None:
        self._reset_recording()
        self._no_speech_limit = FOLLOW_UP_S if follow_up else NO_SPEECH_S
        self._vad.reset_states()
        self._set_state("listening", "follow-up" if follow_up else None)

    def _record(self, frame: np.ndarray) -> None:
        import torch
        self._rec.append(frame)
        self._elapsed_s += len(frame) / RATE
        self._pending = np.concatenate([self._pending, frame])
        while len(self._pending) >= VAD_WINDOW:
            window, self._pending = self._pending[:VAD_WINDOW], self._pending[VAD_WINDOW:]
            prob = self._vad(torch.from_numpy(window.astype(np.float32) / 32768), RATE).item()
            if prob >= 0.45:
                self._heard_speech = True
                self._silence_s = 0.0
            else:
                self._silence_s += VAD_WINDOW / RATE

        finished = (self._heard_speech and self._silence_s >= END_SILENCE_S) or self._elapsed_s >= MAX_QUESTION_S
        if finished and self._heard_speech:
            audio = np.concatenate(self._rec).astype(np.float32) / 32768
            self._reset_recording()
            self._set_state("thinking")
            self._turn += 1
            threading.Thread(target=self._answer, args=(audio, self._turn), daemon=True).start()
        elif not self._heard_speech and self._elapsed_s >= self._no_speech_limit:
            self._reset_recording()
            self._wake.reset()
            self._set_state("idle")

    # ---- answering -----------------------------------------------------

    def transcribe(self, audio: np.ndarray) -> str:
        result = self._stt.transcribe_without_streaming(audio.tolist(), sample_rate=RATE)
        return " ".join(ln.text for ln in result.lines if ln.text).strip()

    def synthesize(self, text: str) -> tuple[np.ndarray | None, int]:
        chunks = [c.audio_int16_array for c in self._tts.synthesize(text)]
        rate = self._tts.config.sample_rate
        return (np.concatenate(chunks) if chunks else None), rate

    def _answer(self, audio: np.ndarray, turn: int) -> None:
        try:
            question = self.transcribe(audio)
            if turn != self._turn:
                return
            if not question:
                self._set_state("idle")
                return
            speak = config.load_settings()["speak_answers"]
            sentences: queue.Queue = queue.Queue()
            player = threading.Thread(target=self._speak_loop, args=(sentences, turn), daemon=True)
            if speak:
                player.start()

            buffer = ""

            def on_delta(piece: str) -> None:
                nonlocal buffer
                if turn != self._turn:
                    raise assistant.Cancelled()
                buffer += piece
                ready, buffer = split_sentences(buffer)
                for s in ready:
                    sentences.put(s)

            result = self._ask(question, "voice", on_delta)
            if result.get("error"):
                buffer = result["error"]
            if buffer.strip():
                sentences.put(buffer.strip())
            sentences.put(None)
            if speak:
                player.join()
            if turn == self._turn and not self.muted:
                self._begin_listening(follow_up=True)
        except Exception as e:
            if turn == self._turn:
                events.toast(_friendly(e), "error")
                self._set_state("idle")

    def _speak_loop(self, sentences: queue.Queue, turn: int) -> None:
        """Synthesise the next sentence while the current one plays, so speech has no gaps."""
        import sounddevice as sd
        clips: queue.Queue = queue.Queue(maxsize=2)

        def synth() -> None:
            while True:
                s = sentences.get()
                if s is None or turn != self._turn:
                    clips.put(None)
                    return
                text = speakable(s)
                if text:
                    clips.put(self.synthesize(text))

        threading.Thread(target=synth, daemon=True).start()
        while True:
            clip = clips.get()
            if clip is None or turn != self._turn:
                return
            audio, rate = clip
            if audio is None or self.muted:
                continue
            self._set_state("speaking")
            sd.play(audio, samplerate=rate)
            sd.wait()

    def _interrupt(self) -> None:
        self._turn += 1
        self._stop_audio()

    def _stop_audio(self) -> None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def _chime(self) -> None:
        try:
            import sounddevice as sd
            t = np.linspace(0, 0.12, int(RATE * 0.12), endpoint=False)
            tone = np.sin(2 * np.pi * 880 * t) * np.hanning(len(t)) * 0.25
            sd.play(tone.astype(np.float32), samplerate=RATE)
        except Exception:
            pass

    def _set_state(self, state: str, detail: str | None = None) -> None:
        self.state, self.detail = state, detail
        events.publish({"type": "voice.state", **self.status()})


def _friendly(e: Exception) -> str:
    text = str(e)
    if "PortAudio" in e.__class__.__name__ or "device" in text.lower():
        return "No microphone was found. Connect one, then restart Margin."
    return f"Voice couldn't start: {text}"


engine = VoiceEngine()
