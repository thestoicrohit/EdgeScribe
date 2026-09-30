"""Spectral-gating noise suppression (numpy only), applied to speech segments before ASR.

Noise profile = per-frequency low percentile of the magnitude spectrum (speech is sparse in
time-frequency, so the quiet frames of each bin are mostly noise). A smoothed Wiener-style gain
with a floor removes stationary noise (room tone, fans, hum) without hard musical-noise artefacts."""
import numpy as np

N_FFT, HOP = 512, 128
_WIN = np.hanning(N_FFT).astype(np.float32)


def stft(x):
    pad = np.concatenate([np.zeros(N_FFT, np.float32), x.astype(np.float32), np.zeros(N_FFT, np.float32)])
    n = 1 + (len(pad) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.fft.rfft(pad[idx] * _WIN, axis=1)


def istft(S, length):
    frames = np.fft.irfft(S, n=N_FFT, axis=1) * _WIN
    out = np.zeros(HOP * (len(frames) - 1) + N_FFT, np.float32)
    norm = np.zeros_like(out)
    for i, f in enumerate(frames):
        out[i * HOP:i * HOP + N_FFT] += f
        norm[i * HOP:i * HOP + N_FFT] += _WIN ** 2
    out /= np.maximum(norm, 1e-6)
    return out[N_FFT:N_FFT + length]


def snr_db(x, noise_ref=None):
    """Rough segment SNR: loud-frame energy vs quiet-frame (or reference) energy, 20 ms frames."""
    n = len(x) // 320
    if n < 4:
        return 99.0
    e = (x[:n * 320].reshape(n, 320) ** 2).mean(1)
    sig = np.percentile(e, 90)
    if noise_ref is not None and len(noise_ref) >= 320 * 4:
        m = len(noise_ref) // 320
        noise = np.median((noise_ref[:m * 320].reshape(m, 320) ** 2).mean(1))
    else:
        noise = np.percentile(e, 10)
    return float(10 * np.log10((sig + 1e-12) / (noise + 1e-12)))


# Measured WER, Windows dictation on 12 voice-bank clips + white noise (raw -> suppressed):
#   SNR 29 dB 0.18 -> 0.23 (s=1.5)   23 dB 0.40 -> 0.34 (s=1.5)
#   SNR 17 dB 0.65 -> 0.58 (s=1.5)   11 dB 0.94 -> 0.78 (s=1.0)
# so suppression is applied only below ADAPTIVE_SNR_DB, gentler in very heavy noise.
ADAPTIVE_SNR_DB = 25.0


def params_for(snr):
    """(strength, floor) for a given segment SNR, or None to leave the audio untouched."""
    if snr >= ADAPTIVE_SNR_DB:
        return None
    return (1.0, 0.08) if snr < 14 else (1.5, 0.08)


def suppress(x, noise_ref=None, strength=1.5, floor=0.08, pct=20):
    """Return the denoised signal. noise_ref: optional audio known to be noise-only."""
    if len(x) < N_FFT:
        return x
    S = stft(x)
    mag = np.abs(S)
    # Noise power per bin. |X|^2 of Gaussian noise is exponential with mean s^2, so its p-quantile
    # is -ln(1-p) * s^2: dividing by that recovers the mean (a raw percentile underestimates ~4.5x).
    if noise_ref is not None and len(noise_ref) >= N_FFT * 4:
        noise_pow = np.percentile(np.abs(stft(noise_ref)) ** 2, 50, axis=0) / np.log(2)
    else:
        noise_pow = np.percentile(mag ** 2, pct, axis=0) / -np.log(1 - pct / 100)
    snr = np.maximum(mag ** 2 - strength * noise_pow[None, :], 0) / (mag ** 2 + 1e-12)
    gain = np.sqrt(snr)
    k = np.ones(5) / 5                                            # smooth gain over time
    gain = np.apply_along_axis(lambda g: np.convolve(g, k, mode="same"), 0, gain)
    gain = np.maximum(gain, floor)
    y = istft(S * gain, len(x))
    peak = float(np.abs(y).max()) or 1.0
    return (y / peak * 0.9).astype(np.float32)
