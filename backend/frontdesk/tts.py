"""Text to speech with Kokoro-82M (free, Apache-2.0).
If Kokoro is not installed, audio is None and the browser reads the text with its own voice."""
import base64
import io
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _pipeline():
    if os.environ.get("TTS_ENGINE", "kokoro") == "browser":
        return None
    try:
        from kokoro import KPipeline

        return KPipeline(lang_code="a")  # American English
    except Exception as e:
        print("Kokoro not available, using the browser voice instead:", e)
        return None


def synthesize(text):
    """Returns WAV bytes, or None."""
    pipe = _pipeline()
    if pipe is None or not text:
        return None
    import numpy as np
    import soundfile as sf

    voice = os.environ.get("TTS_VOICE", "af_heart")
    parts = [np.asarray(audio) for _, _, audio in pipe(text, voice=voice) if audio is not None]
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts), 24000, format="WAV")
    return buf.getvalue()


def to_b64(text):
    wav = synthesize(text)
    return base64.b64encode(wav).decode() if wav else None
