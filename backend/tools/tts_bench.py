"""Measure a TTS voice: time to first audio, and round-trip error (TTS audio fed back into STT).

Run (from backend/):  python tools/tts_bench.py
Edit SENTENCES to include names, drugs, dates and times from your test set.
Listening scores (1 to 5 from at least 10 people) still have to be collected by hand.
"""
import io
import time

import jiwer
import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel
from kokoro import KPipeline

SENTENCES = [
    "Your appointment with Doctor Ayesha Khan is on Tuesday at three p.m.",
    "Please take metformin five hundred milligrams twice a day.",
    "The doctor is in room two oh four on the second floor.",
]


def clean(text):
    return jiwer.RemovePunctuation()(text.lower()).strip()


pipe = KPipeline(lang_code="a")
stt = WhisperModel("small", device="auto", compute_type="int8")

for text in SENTENCES:
    start = time.time()
    first_audio = None
    chunks = []
    for _, _, audio in pipe(text, voice="af_heart"):
        if first_audio is None:
            first_audio = time.time() - start  # time to first audio chunk
        chunks.append(np.asarray(audio))
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(chunks), 24000, format="WAV")
    buf.seek(0)
    heard = " ".join(s.text for s in stt.transcribe(buf)[0])
    print(f"first audio {first_audio:.2f}s | round-trip WER {jiwer.wer(clean(text), clean(heard)):.2f} | {text}")
