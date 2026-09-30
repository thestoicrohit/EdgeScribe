"""WAV input/output (stdlib + numpy). Everything internal is mono float32 at 16 kHz."""
import io
import wave

import numpy as np

from .config import SR


def decode_wav(data: bytes) -> np.ndarray:
    """16-bit PCM WAV bytes (any rate, any channel count) -> mono float32 at 16 kHz."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError("only 16-bit PCM WAV is supported")
            ch, sr = w.getnchannels(), w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    except (wave.Error, EOFError):
        raise ValueError("not a valid 16-bit PCM WAV file") from None
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(1)
    return resample(x, sr)


def resample(x, sr):
    if sr == SR or len(x) == 0:
        return x.astype(np.float32)
    n = int(len(x) * SR / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def read_wav(path) -> np.ndarray:
    with open(path, "rb") as f:
        return decode_wav(f.read())


def encode_wav(x) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def write_wav(path, x):
    with open(path, "wb") as f:
        f.write(encode_wav(x))
