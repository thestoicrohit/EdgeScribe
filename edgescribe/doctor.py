"""`python run.py --doctor`: what works on this machine, and how to enable the rest."""
import platform
import sys

from . import backends, config, tts_engines, voicebank, winspeech

OK, NO, OPT = "[ok]  ", "[--]  ", "[opt] "


def _fix(win, mac, linux):
    return {"win32": win, "darwin": mac}.get(sys.platform, linux)


def run():
    lines = []
    say = lines.append
    say(f"EdgeScribe doctor - {platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}")
    say("")
    try:
        import numpy
        say(f"{OK}numpy {numpy.__version__}: required core (spiking listener, learning, search, dashboard) works")
    except Exception:
        say(f"{NO}numpy missing: run  pip install numpy")
        return "\n".join(lines)

    ws = None
    if winspeech.supported():
        try:
            ws = winspeech.WinSpeech()
            ws.voices()
        except Exception as e:
            say(f"{NO}Windows speech worker failed: {e}")
            ws = None
    eng = tts_engines.detect(ws)
    if eng:
        try:
            vs = eng.voices()
            say(f"{OK}offline voices: {eng.name} ({len(vs)} English voices)")
        except Exception as e:
            say(f"{NO}offline voices: {eng.name} found but failed: {e}")
    else:
        say(f"{NO}offline voices (server): none. " + _fix(
            "Windows speech should be built in; check the PowerShell execution policy.",
            "the `say` command should be built in.",
            "install eSpeak NG:  sudo apt install espeak-ng"))
    say(f"{OK}browser voices: always available in the page (on-device voices offline; cloud voices only in Online mode)")

    asr = None
    if backends.whisper_works():
        asr = "Whisper (faster-whisper)"
    else:
        mp = backends.vosk_model_path()
        if mp and backends.vosk_works(mp):
            asr = f"Vosk ({mp})"
        elif ws is not None and ws.voices()["recognizers"]:
            asr = "Windows offline dictation"
    if asr:
        say(f"{OK}speech recognition: {asr}")
    else:
        say(f"{NO}speech recognition: none (speech is still detected, but there is no transcript)")
    if not asr or "Windows" in asr:
        say(f"{OPT}better recognition on any OS (offline):  pip install vosk   then unzip")
        say("       https://alphacephei.com/vosk/models  ->  vosk-model-small-en-us-0.15  (~40 MB)")
        say(f"       into {config.DATA / 'models'}")

    hw = backends.hardware()
    say(f"{OK if hw['npu'] else OPT}NPU: " + ("QNN execution provider available" if hw["npu"]
                                         else f"not available ({hw['npu_note']}); everything runs on the CPU"))
    llm = backends.Summarizer().llm_up()
    say(f"{OK if llm else OPT}summaries: " + ("Ollama is running" if llm else
                                          "extractive. For LLM summaries install Ollama, then  ollama pull llama3.2:1b"))
    n = len(voicebank.Bank(None).clips)
    say(f"{OK if n else OPT}voice bank: {n} spoken clips" + ("" if n else
                                                           " (built automatically on first start when offline voices exist)"))
    say("")
    say(f"data folder: {config.DATA}")
    say(f"start:       python run.py   ->  http://{config.HOST}:{config.PORT}")
    if ws:
        ws.close()
    return "\n".join(lines)
