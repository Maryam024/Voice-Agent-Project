"""Speech to text. STT_ENGINE=groq uses Groq's hosted Whisper (about 10x faster, same key as the LLM).
Anything else uses faster-whisper on this PC (free, but slow on a CPU)."""
import os
import re
import tempfile
from functools import lru_cache

# Whisper invents these when it hears silence or noise
_JUNK = {"doctors", "doctor", "you", "bye", "thanks for watching", "thank you for watching"}
_SHORT_JUNK = {"thank you", "thanks"}  # real only when the clip is not tiny


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    # device="auto" uses the GPU if there is one, otherwise CPU
    return WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="auto", compute_type="int8")


def warm():
    """Load the local model at start-up, so the first call is not slow."""
    if os.environ.get("STT_ENGINE") != "groq":
        _model()


def _get(seg, name, default=0):
    return seg.get(name, default) if isinstance(seg, dict) else getattr(seg, name, default)


def _not_speech(seg):
    return _get(seg, "no_speech_prob") > 0.6 and _get(seg, "avg_logprob") < -1.0


def _clean(text, seconds=None):
    plain = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if plain in _JUNK:
        return ""
    if plain in _SHORT_JUNK and seconds is not None and seconds < 1.5:
        return ""
    return text.strip()


def transcribe(audio_bytes, hint=""):
    """hint = doctor names. Whisper uses it as a vocabulary hint."""
    ext = "wav" if audio_bytes[:4] == b"RIFF" else "webm"

    if os.environ.get("STT_ENGINE") == "groq":
        try:
            from .llm import _client_for, client

            # Use Groq's own client whenever a Groq key exists, so STT keeps working
            # even if LLM_BASE_URL points somewhere else.
            groq = _client_for("groq") if os.environ.get("GROQ_API_KEY") else client()
            extra = {"language": os.environ["STT_LANGUAGE"]} if os.environ.get("STT_LANGUAGE") else {}
            r = groq.audio.transcriptions.create(
                model=os.environ.get("STT_MODEL", "whisper-large-v3-turbo"),
                file=(f"speech.{ext}", audio_bytes),
                prompt=hint,
                **extra,
            )
            return r.text.strip()
        except Exception as e:  # rate limit or network: fall through to the local model below
            print("Groq speech-to-text failed, using local Whisper:", e)

    try:
        model = _model()
    except ImportError:  # free hosting has no local Whisper: say so clearly instead of crashing
        raise RuntimeError("Speech-to-text is busy right now. Please try again in a moment.")

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        segments, info = model.transcribe(
            path,
            language=os.environ.get("STT_LANGUAGE") or None,
            initial_prompt=hint or None,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments if not _not_speech(s)).strip()
        return _clean(text, info.duration)
    finally:
        os.remove(path)