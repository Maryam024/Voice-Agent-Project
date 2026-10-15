"""Compare speech-to-text models on your own test set.

Put audio files and matching reference texts in one folder:  case01.wav + case01.txt, ...
Run:  python tools/stt_bench.py test_audio tiny small large-v3
Prints word error rate (WER, lower is better) and real-time factor (RTF, below 1 = faster than real time).
"""
import sys
import time
from pathlib import Path

import jiwer
from faster_whisper import WhisperModel


def normalize(text):
    return jiwer.RemovePunctuation()(text.lower()).strip()


def main(folder, model_names):
    cases = [(p, p.with_suffix(".txt").read_text()) for p in sorted(Path(folder).glob("*.wav")) if p.with_suffix(".txt").exists()]
    print(f"{len(cases)} test files")
    for name in model_names:
        model = WhisperModel(name, device="auto", compute_type="int8")
        refs, hyps, audio_sec, work_sec = [], [], 0.0, 0.0
        for path, ref in cases:
            t = time.time()
            segments, info = model.transcribe(str(path), beam_size=1)
            hyps.append(normalize(" ".join(s.text for s in segments)))
            work_sec += time.time() - t
            audio_sec += info.duration
            refs.append(normalize(ref))
        print(f"{name:12} WER {jiwer.wer(refs, hyps):.3f}   RTF {work_sec / audio_sec:.2f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
