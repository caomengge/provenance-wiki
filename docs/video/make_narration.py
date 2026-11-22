"""Synthesise the install-video narration, one WAV per scene (Kokoro TTS, Apache-2.0).

    python docs/video/make_narration.py docs/video/narration.json out_dir/
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
pipe = KPipeline(lang_code="a")
for scene in spec["scenes"]:
    parts = [audio for _, _, audio in pipe(scene["say"], voice=spec["voice"], speed=spec["speed"])]
    audio = np.concatenate([np.asarray(p) for p in parts])
    sf.write(out / f"{scene['id']}.wav", audio, 24000)
    print(f"{scene['id']}: {len(audio) / 24000:.2f}s")
