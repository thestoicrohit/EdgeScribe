# EdgeScribe

A private, on-device study and meeting assistant that runs on an ordinary laptop.
A spiking neural network listens all the time and wakes the heavy models only when someone speaks.
Everything runs on the laptop: the server binds to `127.0.0.1`, the UI loads nothing from the internet,
and audio never leaves the machine. (Originally designed for Snapdragon PCs; the NPU is an optional speed-up.)

```
pip install numpy
python run.py --doctor                          # what works on this machine, and how to enable the rest
python run.py                                   # then open http://127.0.0.1:8765   (--port N if 8765 is busy)
python -m unittest discover -s tests -t .       # 66 tests
```

## Runs on any laptop

Only Python 3.10+ and `numpy` are required. Everything else is detected at start-up; missing pieces degrade
gracefully instead of breaking.

| | Windows | macOS | Linux |
|---|---|---|---|
| Spiking listener, learning, search, dashboard | ✓ | ✓ | ✓ |
| Offline voices (server) | built in (System.Speech) | built in (`say`) | `sudo apt install espeak-ng` |
| Voices in the page | on-device browser voices; cloud voices only in Online mode | same | same |
| Offline speech recognition | built in (Windows dictation) | Vosk (optional) | Vosk (optional) |
| Better recognition, any OS | `pip install vosk` + [vosk-model-small-en-us-0.15](https://alphacephei.com/vosk/models) (~40 MB) unzipped into `data/models/` | | |
| LLM summaries (optional) | Ollama + `llama3.2:1b` | same | same |
| NPU (optional) | Snapdragon + onnxruntime-qnn | – | – |

With no recognizer at all, the listener still detects speech and measures everything; demos fall back to a
scripted transcript (labelled as such). Only Windows was tested end to end; the macOS, Linux and Vosk
paths are covered by unit tests with fake engines, not on real machines.

## Pipeline

```
mic / WAV / demo audio
   │
   ▼
Spiking listener (CPU, numpy)   16 bands → adaptive spike encoder → LIF neuron → wake events
   │   wakes the heavy models only while someone is talking
   ▼
Speech segment ──► adaptive noise suppression (only if SNR < 25 dB)
               ──► speech recognition: Whisper (NPU/CPU) › Vosk › Windows offline dictation › none
               ──► sentences ──► action-item classifier (base + personal layer) ──► summary
                                                                 │
                              local BM25 search over transcripts + your notes
Text-to-speech: offline (Windows / macOS `say` / eSpeak NG, on-device browser voices) or online (browser cloud voices, opt-in)
```

## The app (7 sections)

| Section | What it does |
|---|---|
| **Live** | Start listening, run a demo (spoken or synthetic audio, quiet→noisy), or import a WAV. Live spike raster, membrane potential, learned neuron, transcript next to the known script, per-segment word error, summary with *Read aloud* |
| **Today** | Greeting + *Read my day* (spoken digest), overdue / today / this week tasks with due dates parsed from speech, add a task in plain English, topic cloud linking sessions, notes, tasks and cards, and a suggested next step |
| **Study** | Flashcards made automatically from notes and clean transcripts (definitions + fill-in-the-blank), SM-2 spaced repetition, keyboard grading (Space, 1–4), retention stats |
| **Teach** | Card game: label sentences, see the model's guess and confidence, streaks, XP, levels |
| **Insights** | KPIs and 14 charts (learning, speech recognition, synapses, calibration, study, tasks) plus a **System** panel: every event reactor's calls/errors/time, background jobs with *Cancel*, API latency; *Back up* downloads the database |
| **Ask** | Search sessions and notes (filterable); results link back to their session; 👍/👎 re-ranks results |
| **Library** | Every session with transcript, script, summary, topics, its tasks, related sessions and cards; *Export as Markdown* |

Settings (gear icon): voice mode, voice, speed, auto-read, noise suppression, theme (auto / light / dark),
live system health, and reset.

## How everything is connected

Features talk through an in-process **event bus** (`edgescribe/events.py`); each reaction is a named,
measured reactor, visible in Insights → System:

```
session finished ─► search index ─► topics ─► tasks (action items + parsed due dates) ─► flashcards ─► awards
notes added      ─► topics ─► flashcards
you flag a sentence "action" / "not"      ─► task created / dismissed
you dismiss an auto task / type one in    ─► the action-item classifier learns from it
you finish a task / review a card         ─► XP, streak, awards
```

Every user label (Teach cards, transcript flags, task decisions) goes through one path that scores the
model's guess first, then updates the personal layer. Background work (training, card generation, voice bank,
re-indexing old data at start-up) runs as cancellable **jobs**. Topics from real speech recognition must be
*corroborated* (said twice, or found in another session or note), so misheard words don't become topics.

## What learns, and from what

| Component | Learns | From |
|---|---|---|
| Spiking listener | per-band synaptic weights (can become inhibitory), band threshold, fire threshold, persistence | ✓/✗ on wake events, "it missed something", and ground-truth training episodes (**spoken** voice-bank audio when available) |
| Action-item classifier | base layer: hashed logistic regression (SGD) on a curriculum · personal layer: your labels, L2-shrunk | Train button · your labels |
| Search ranking | a small boost per passage | 👍/👎 |

## Measured results (this development PC: AMD Ryzen 5 5600H, Windows 11, no NPU)

| What | Result |
|---|---|
| Listener, spoken audio (held-out) | F1 **0.59 → 0.93** after 20 training episodes |
| Listener speed | ~200× real time on one CPU core |
| Action-item classifier (held-out, unseen phrasing) | F1 **0.61 → 0.97** |
| Robustness to bad labels | 25 labels on garbled transcripts: F1 0.973 → 0.973 (it used to fall to ~0.7; see `learner.py`) |
| Windows dictation, single clean clips | ~18% word error |
| End-to-end word error (listener + recognizer), quiet → noisy room | **31–37%** → 83–97% |
| Noise suppression effect on word error | 23 dB: 40% → 34% · 17 dB: 65% → 58% · 29 dB: 18% → 23% (so it's applied only below 25 dB) |
| Offline TTS | ~0.2–0.5 s per sentence; cached repeats are instant |

## What is real and what is simulated

* **Demo audio.** "Spoken" demos mix real synthesized speech (Windows voices, 60 clips) with room noise,
  typing, coughs and beeps, and have a known script: that's how precision, recall and word error are measured.
  They are **not human recordings**. "Synthetic" demos use a speech-like signal and a scripted transcript
  (labelled as such). Microphone and imported WAVs are fully real, but have no script to score against.
* **The recognizer is the weak link.** Windows dictation is fine in a quiet room and poor in noise. Whisper
  on the Snapdragon NPU is the intended replacement, and the dashboard's word-error chart is how to prove the gain.
* **"Compute saved" is a duty-cycle proxy** (seconds the heavy models were awake ÷ seconds of audio), not measured power.
* **The spiking listener runs on the CPU.** Spiking nets don't map onto the NPU; the NPU's job is the
  recognizer and LLM that the listener wakes.
* **Online voices** use the browser's cloud voices (for example Google, or Microsoft Natural in Edge) and send the text
  being read to that provider. They are off by default and clearly marked. The in-app browser used during
  development offers none, so that path is only verified to fall back correctly.
* **Not exercised on this PC:** the microphone path (no mic in the test browser), Whisper, the NPU, Ollama.

## Porting to the Snapdragon laptop
1. `pip install onnxruntime-qnn`, export Whisper from Qualcomm AI Hub, and add a `QNNWhisperASR` next to
   `WhisperASR` in `edgescribe/backends.py` (first working backend wins). The NPU badge turns on automatically.
2. Optional: install Ollama and `ollama pull llama3.2:1b` for LLM summaries (extractive otherwise).
3. Run a few spoken demos: the Insights word-error chart shows the difference against Windows dictation.

## Security
Local-only bind; bodies are read exactly once per request (keep-alive safe); ids validated by route patterns;
session start is atomic (no double start); POSTs require an `X-EdgeScribe` header (blocks cross-site requests); Host/Origin must be local
(blocks DNS rebinding); static files confined to `web/`; JSON bodies ≤ 4 MB, uploads ≤ 300 MB; setting values are
validated. No authentication: it's single-user and local, so don't expose the port.

## Layout
```
edgescribe/  core.py features.py events.py jobs.py server.py (route table + metrics)
             snn.py learner.py trainer.py sim.py voicebank.py corpus.py denoise.py textmetrics.py audioio.py
             tasks.py topics.py study.py rag.py gamify.py db.py config.py
             pipeline.py backends.py tts_engines.py winspeech.py speech_worker.ps1 doctor.py
web/         index.html style.css app.js connected.js charts.js
tests/       66 tests: listener, learners, drift regression, retrieval, pipeline, HTTP guards, speech, denoise,
             migrations, portability, due dates, SM-2, topics, event bus, jobs, end-to-end connected flows, router
data/        edgescribe.db (SQLite, WAL, versioned migrations), voicebank/, edgescribe.log
```
