"""Голос Янки: распознавание речи на ПК (Vosk + микрофон через sounddevice), без интернета.
Голосом отвечает окно CozyCode (голоса Windows через speechSynthesis) - здесь только слух.

Модель распознавания ищем в %LOCALAPPDATA%\\CozyCode\\vosk\\<папка модели>.
Пакеты и модель ставятся отдельно с согласия Яны (tmp/install_voice.ps1)."""
import json
import os
import queue
import threading
import time
from pathlib import Path

RATE = 16000


def models_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "CozyCode" / "vosk"


def find_model():
    d = models_dir()
    if not d.exists():
        return None
    cands = [p for p in d.iterdir() if p.is_dir() and ((p / "am").exists() or (p / "conf").exists())]
    cands.sort(key=lambda p: ("small" not in p.name, p.name))  # маленькая модель - быстрее
    return cands[0] if cands else None


def status():
    out = {"vosk": False, "sounddevice": False, "model": None, "ready": False, "error": ""}
    try:
        import vosk  # noqa: F401
        out["vosk"] = True
    except Exception as e:
        out["error"] = f"vosk: {e}"
    try:
        import sounddevice as sd
        out["sounddevice"] = True
        try:
            dev = sd.query_devices(kind="input")
            out["mic"] = dev.get("name")
        except Exception as e:
            out["mic"] = None
            out["error"] = out["error"] or f"микрофон не найден: {e}"
    except Exception as e:
        out["error"] = out["error"] or f"sounddevice: {e}"
    m = find_model()
    out["model"] = m.name if m else None
    out["ready"] = out["vosk"] and out["sounddevice"] and bool(m) and bool(out.get("mic"))
    if out["vosk"] and out["sounddevice"] and not m:
        out["error"] = "модель распознавания речи не скачана"
    return out


class Listener:
    """Одна запись за раз: start() → слушаем, пока не будет паузы ~1.2 с после речи или stop().
    Результат - в result(); текущий кусок - в partial (для окна)."""

    def __init__(self, log=print):
        self.log = log
        self._model = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.partial, self.text, self.error, self.active = "", "", "", False

    def _load(self):
        if self._model is None:
            import vosk
            vosk.SetLogLevel(-1)
            m = find_model()
            if not m:
                raise RuntimeError("модель распознавания речи не скачана")
            self._model = vosk.Model(str(m))
        return self._model

    def start(self, max_seconds=30):
        with self._lock:
            if self.active:
                return False
            self.partial, self.text, self.error, self.active = "", "", "", True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(max_seconds,), daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()

    def wait(self, timeout=35):
        t = self._thread
        if t:
            t.join(timeout)
        return self.text

    def _run(self, max_seconds):
        try:
            import sounddevice as sd
            import vosk
            rec = vosk.KaldiRecognizer(self._load(), RATE)
            q = queue.Queue()

            def cb(data, frames, t, st):
                q.put(bytes(data))

            parts, heard, quiet_since = [], False, None
            t0 = time.time()
            with sd.RawInputStream(samplerate=RATE, blocksize=4000, dtype="int16", channels=1, callback=cb):
                while not self._stop.is_set() and time.time() - t0 < max_seconds:
                    try:
                        data = q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if rec.AcceptWaveform(data):
                        r = json.loads(rec.Result()).get("text", "")
                        if r:
                            parts.append(r)
                            heard, quiet_since = True, time.time()
                        self.partial = " ".join(parts)
                    else:
                        p = json.loads(rec.PartialResult()).get("partial", "")
                        self.partial = " ".join(parts + ([p] if p else []))
                        if p:
                            heard, quiet_since = True, None
                        elif heard and quiet_since is None:
                            quiet_since = time.time()
                    if heard and quiet_since and time.time() - quiet_since > 1.2:
                        break
            tail = json.loads(rec.FinalResult()).get("text", "")
            if tail:
                parts.append(tail)
            self.text = " ".join(parts).strip()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"Янка-голос: {self.error}")
        finally:
            self.active = False


def recognize_wav(path):
    """Распознать WAV-файл (16 кГц, моно, 16 бит) - для проверки без микрофона."""
    import wave
    import vosk
    vosk.SetLogLevel(-1)
    m = find_model()
    if not m:
        raise RuntimeError("модель распознавания речи не скачана")
    with wave.open(str(path), "rb") as w:
        rec = vosk.KaldiRecognizer(vosk.Model(str(m)), w.getframerate())
        out = []
        while True:
            data = w.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                out.append(json.loads(rec.Result()).get("text", ""))
        out.append(json.loads(rec.FinalResult()).get("text", ""))
    return " ".join(x for x in out if x).strip()
