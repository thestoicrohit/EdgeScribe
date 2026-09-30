# EdgeScribe: private, event-driven, self-teaching study & meeting assistant

**Challenge:** Snapdragon AI Lab Build & Present (Qualcomm x HP): on-device AI for Snapdragon-powered PCs.

**Problem.** Recording and summarising lectures/meetings usually means sending audio to the cloud: a privacy risk, it needs internet, and always-on models drain the battery.

**Solution.** Everything runs on the laptop.
1. **Spiking-network listener (neuromorphic-style, CPU).** Always-on and event-driven; wakes the heavy models only when someone is speaking. It *learns*: per-band synaptic weights, thresholds and persistence adapt from feedback and ground truth.
2. **Whisper (NPU via Qualcomm AI Hub).** Local speech-to-text on just the segments the trigger flags.
3. **Small LLM / extractive summariser.** Summary and action items.
4. **Self-teaching loop.** Action-item classifier improves from the user's labels; search re-ranks from thumbs up/down. A dashboard shows every learning curve, weight and counter.
5. **Local search** across transcripts and notes.

**Measured, not claimed.** The dashboard reports the heavy-model duty cycle, trigger precision/recall/F1 on held-out simulated audio, classifier accuracy on unseen phrasing, and the SNN's real-time factor.

**Honest limits.** See README: simulated data, ASR needs a backend, duty-cycle is a proxy for power, the SNN runs on CPU.
