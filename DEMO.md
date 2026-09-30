# 3-minute demo script (Snapdragon AI Lab submission video)

**0:00 – Problem (15 s).** "Lecture and meeting recorders send your audio to the cloud and keep big models
running the whole time. EdgeScribe does it all on this Snapdragon laptop, and keeps the heavy models asleep
until someone speaks."

**0:15 – Live (50 s).** Live tab → *Start listening* (or *Run demo → Spoken → Quiet*).
Point at: spikes lighting up per frequency band, the membrane potential crossing the threshold, the green
"models awake" bands, and the *Heavy models awake* KPI (≈35–55 %). Say a sentence with a deadline in it; show
it appear in the transcript flagged ACTION. Press *Read aloud* on the summary (offline voice).

**1:05 – It learns (40 s).** Teach tab: answer 4–5 cards with Y/N; show the model's guess, the confidence bar
and the streak. Mark one wake event as *Noise* on Live and show the threshold change.

**1:45 – Proof (50 s).** Insights: the listener learning curve (0.59 → 0.93 on spoken audio), the classifier curve,
learned synapses (low bands excitatory), word error per session, and "does the recognizer know when it's wrong".
Then Settings → System: everything green is running on-device; show NPU status.

**2:35 – NPU (15 s).** "Whisper runs on the Hexagon NPU through Qualcomm AI Hub. The listener stays on the CPU
at ~200× real time, so the NPU only works when there's speech." (Record this part on the Snapdragon laptop.)

**2:50 – Close (10 s).** "Private by default. Online voices exist, but they're opt-in and clearly marked."

Before recording: *Settings → Data → Reset*, then *Insights → Train both* ×2 so the curves start from zero on camera.
