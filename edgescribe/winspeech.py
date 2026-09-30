"""Python side of the offline Windows speech worker (TTS + dictation ASR).

One long-lived PowerShell process per role (so recognition never blocks speaking), a JSON-lines
protocol, per-request timeouts, and one automatic restart if the worker dies or hangs."""
import itertools
import json
import logging
import os
import pathlib
import queue
import subprocess
import threading
import uuid

from . import config

log = logging.getLogger("edgescribe.winspeech")
SCRIPT = pathlib.Path(__file__).with_name("speech_worker.ps1")
TMP = config.DATA / "tmp"


class OpError(RuntimeError):
    """The worker ran the request and reported an error (not retried)."""


class TransportError(RuntimeError):
    """The worker died, hung or spoke garbage (restart + retry once)."""


def supported():
    return os.name == "nt" and SCRIPT.exists()


class Worker:
    def __init__(self, role):
        self.role = role
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.proc = None
        self.lines = None

    def _start(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags, bufsize=0)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self.lines), daemon=True).start()
        try:
            first = self.lines.get(timeout=40)
        except queue.Empty:
            first = None
        if not first or not first.startswith("{") or not json.loads(first).get("ready"):
            self._kill()
            raise TransportError("speech worker failed to start")
        log.info("speech worker (%s) started pid=%s", self.role, self.proc.pid)

    @staticmethod
    def _pump(proc, q):
        for raw in iter(proc.stdout.readline, b""):
            q.put(raw.decode("utf-8", "replace").strip().lstrip("﻿"))
        q.put(None)

    def _kill(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def _once(self, op, wait_s, fields):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        rid = next(self.ids)
        try:
            self.proc.stdin.write((json.dumps(dict(id=rid, op=op, **fields)) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as e:
            raise TransportError(str(e)) from None
        while True:
            try:
                line = self.lines.get(timeout=wait_s)
            except queue.Empty:
                raise TransportError(f"no reply within {wait_s}s") from None
            if line is None:
                raise TransportError("speech worker exited")
            if not line.startswith("{"):
                continue                      # stray host output
            msg = json.loads(line)
            if msg.get("id") != rid:
                continue                      # late reply to an abandoned request
            if not msg.get("ok"):
                raise OpError(msg.get("error") or "speech worker error")
            return msg

    def call(self, op, wait_s=60, **fields):
        with self.lock:
            try:
                return self._once(op, wait_s, fields)
            except TransportError as e:
                log.warning("speech worker (%s) %s: %s; restarting", self.role, op, e)
                self._kill()
                try:
                    return self._once(op, wait_s, fields)
                except TransportError:
                    self._kill()
                    raise

    def close(self):
        with self.lock:
            self._kill()


class WinSpeech:
    """Facade: .voices(), .tts(text, voice, rate) -> wav bytes, .asr_file(path, seconds) -> (text, conf)."""

    def __init__(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.tts_w, self.asr_w = Worker("tts"), Worker("asr")
        self._voices = None

    def voices(self):
        if self._voices is None:
            m = self.tts_w.call("voices", wait_s=60)
            self._voices = dict(voices=m.get("voices") or [], recognizers=m.get("recognizers") or [])
        return self._voices

    def tts_to_file(self, text, path, voice=None, rate=0):
        self.tts_w.call("tts", wait_s=90, text=str(text)[:5000], voice=voice or "",
                        rate=int(rate), out=str(path))

    def tts(self, text, voice=None, rate=0):
        out = TMP / f"tts-{uuid.uuid4().hex}.wav"
        try:
            self.tts_to_file(text, out, voice, rate)
            return out.read_bytes()
        finally:
            out.unlink(missing_ok=True)

    def asr_file(self, path, seconds=10.0):
        wait = int(10 + seconds * 2)
        m = self.asr_w.call("asr", wait_s=wait + 20, path=str(path), wait=wait)
        from .backends import as_sentences          # lazy: backends imports this module's users
        parts = [p for p in (m.get("parts") or []) if p.get("text", "").strip()]
        text = as_sentences(p["text"] for p in parts)
        conf = sum(p["conf"] for p in parts) / len(parts) if parts else 0.0
        return text, conf

    def close(self):
        self.tts_w.close()
        self.asr_w.close()
