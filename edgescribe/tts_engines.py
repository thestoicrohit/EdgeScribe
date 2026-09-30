"""Offline text-to-speech on any desktop OS, behind one interface.

  Windows  System.Speech via the persistent PowerShell worker (winspeech.py)
  macOS    the built-in `say` command
  Linux    `espeak-ng` (or `espeak`) - `sudo apt install espeak-ng`

Every engine: .name, .voices() -> [{name, culture, gender}], .tts_to_file(text, path, voice, rate),
.tts(text, voice, rate) -> WAV bytes. rate is -5..5 (0 = normal). Text is passed through a temp file,
never as a command-line argument, so text starting with '-' can't be parsed as an option."""
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from . import config

log = logging.getLogger("edgescribe.tts")


def wpm(rate):
    return int(round(175 * 1.1 ** max(-5, min(5, int(rate)))))


class _Engine:
    name = "?"

    def tts(self, text, voice=None, rate=0):
        tmp = config.DATA / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        out = tmp / f"tts-{uuid.uuid4().hex}.wav"
        try:
            self.tts_to_file(text, out, voice, rate)
            return out.read_bytes()
        finally:
            out.unlink(missing_ok=True)


class WindowsTTS(_Engine):
    name = "Windows speech"

    def __init__(self, ws):
        self.ws = ws

    def voices(self):
        return self.ws.voices()["voices"]

    def tts_to_file(self, text, path, voice=None, rate=0):
        self.ws.tts_to_file(text, path, voice or None, rate)


class _CmdEngine(_Engine):
    exe = None

    def _run(self, args, text, timeout=90):
        fd, tf = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(" ".join(str(text).split())[:5000])
            r = subprocess.run([self.exe, *args, "-f", tf], capture_output=True, timeout=timeout)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[:200] or f"{self.exe} failed")
        finally:
            pathlib.Path(tf).unlink(missing_ok=True)


class MacTTS(_CmdEngine):
    name = "macOS say"
    _LINE = re.compile(r"^(.+?)\s{2,}([a-z]{2,3}[_-][A-Za-z0-9]+)\s+#")

    def __init__(self, exe="say"):
        self.exe = exe

    def voices(self):
        out = subprocess.run([self.exe, "-v", "?"], capture_output=True, text=True, timeout=30).stdout
        return parse_say_voices(out)

    def tts_to_file(self, text, path, voice=None, rate=0):
        args = ["-o", str(path), "--data-format=LEI16@16000", "-r", str(wpm(rate))]
        if voice:
            args = ["-v", voice] + args
        self._run(args, text)


class EspeakTTS(_CmdEngine):
    name = "eSpeak NG"

    def __init__(self, exe):
        self.exe = exe

    def voices(self):
        out = subprocess.run([self.exe, "--voices=en"], capture_output=True, text=True, timeout=30).stdout
        return parse_espeak_voices(out)

    def tts_to_file(self, text, path, voice=None, rate=0):
        self._run(["-v", voice or "en-us", "-s", str(wpm(rate)), "-w", str(path)], text)


def parse_say_voices(out):
    """`say -v ?` lines look like: 'Samantha            en_US    # Hello! My name is Samantha.'"""
    voices = []
    for line in out.splitlines():
        m = MacTTS._LINE.match(line)
        if m and m.group(2).lower().startswith("en"):
            voices.append(dict(name=m.group(1).strip(), culture=m.group(2).replace("_", "-"), gender=""))
    return voices


def parse_espeak_voices(out):
    """`espeak-ng --voices=en` rows: 'Pty Language Age/Gender VoiceName File Other'."""
    voices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            lang, gender, vname = parts[1], parts[2].split("/")[-1], parts[3]
            voices.append(dict(name=lang, culture=lang, gender={"M": "Male", "F": "Female"}.get(gender, ""),
                               label=vname.replace("_", " ")))
    return voices


def detect(winspeech=None):
    """The best offline TTS engine on this machine, or None."""
    if winspeech is not None:
        return WindowsTTS(winspeech)
    if sys.platform == "darwin" and shutil.which("say"):
        return MacTTS(shutil.which("say"))
    for exe in ("espeak-ng", "espeak"):
        path = shutil.which(exe)
        if path:
            return EspeakTTS(path)
    return None
