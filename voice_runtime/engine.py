import hashlib
import io
import json
import tempfile
import threading
from pathlib import Path

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest


class VoiceCapacityError(Exception):
    pass


class ChatterboxEngine:
    """One mutable model per process; no voice state survives an utterance."""

    def __init__(self, model_directory: str, manifest_sha256: str):
        directory = Path(model_directory).resolve(strict=True)
        manifest_bytes = (directory / "manifest.json").read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            raise ValueError("Model manifest revision mismatch")
        manifest = json.loads(manifest_bytes)
        required = {"ve.pt", "s3gen.pt", "t3_mtl23ls_v3.safetensors",
                    "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json"}
        if set(manifest) != required or (directory / "conds.pt").exists():
            raise ValueError("Unexpected model bundle")
        for name, expected in manifest.items():
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Invalid model file")
            with path.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("Model file checksum mismatch")
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        if not torch.cuda.is_available():
            raise RuntimeError("A CUDA GPU is required; CPU fallback is disabled")
        self.model = ChatterboxMultilingualTTS.from_local(directory, "cuda", t3_model="v3")
        self.revision = manifest_sha256
        self.lock = threading.Lock()

    def synthesize(self, request: SelfHostedVoiceRequest) -> bytes:
        if request.model_revision != self.revision:
            raise ValueError("Model revision mismatch")
        reference = request.reference_audio()
        if not self.lock.acquire(blocking=False):
            raise VoiceCapacityError()
        try:
            import numpy as np
            import soundfile as sf

            with tempfile.TemporaryDirectory(prefix="stay-voice-") as temporary:
                path = Path(temporary) / "reference.wav"
                path.write_bytes(reference)
                self.model.conds = None
                waveform = self.model.generate(request.text, language_id=request.language,
                    audio_prompt_path=str(path), exaggeration=.25 + .5 * request.energy)
                samples = waveform.detach().cpu().numpy().reshape(-1)
                if (not np.isfinite(samples).all() or samples.size == 0
                        or samples.size > self.model.sr * 90
                        or float(np.max(np.abs(samples))) < 0.0001):
                    raise RuntimeError("Invalid synthesized audio")
                result = io.BytesIO()
                sf.write(result, samples, self.model.sr, format="WAV", subtype="PCM_16")
                return result.getvalue()
        finally:
            self.model.conds = None
            self.lock.release()
