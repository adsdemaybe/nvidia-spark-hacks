"""Text-prompted 2D detection + masks: OWLv2 + SAM2 through HF transformers.

OWLv2 over GroundingDINO deliberately: both are pure PyTorch through
transformers, but GroundingDINO's repo path drags in the MultiScaleDeformableAttention
custom CUDA op, a known build failure on sm_121/aarch64. Slightly worse boxes,
zero compilation risk. Runs on CUDA, MPS, or CPU (slow).

Optional import -- only the Spark image ships torch/transformers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

DETECTOR_ID = "google/owlv2-base-patch16-ensemble"
SEGMENTER_ID = "facebook/sam2.1-hiera-small"


@dataclass
class Detection:
    frame: str
    term: str
    box_xyxy: tuple[float, float, float, float]
    score: float
    mask: np.ndarray  # (H,W) bool


def detect_and_segment(
    frames: list[Path],
    terms: list[str],
    *,
    box_threshold: float = 0.35,
    device: str | None = None,
) -> list[Detection]:
    import torch
    from PIL import Image
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    device = device or ("cuda" if torch.cuda.is_available()
                        else "mps" if torch.backends.mps.is_available() else "cpu")
    proc = Owlv2Processor.from_pretrained(DETECTOR_ID)
    model = Owlv2ForObjectDetection.from_pretrained(DETECTOR_ID).to(device).eval()

    sam = _load_sam2(device)

    out: list[Detection] = []
    with torch.no_grad():
        for fp in frames:
            img = Image.open(fp).convert("RGB")
            inputs = proc(text=[terms], images=img, return_tensors="pt").to(device)
            res = model(**inputs)
            target = torch.tensor([img.size[::-1]], device=device)
            post = proc.post_process_object_detection(res, threshold=box_threshold,
                                                      target_sizes=target)[0]
            for box, score, label in zip(post["boxes"], post["scores"], post["labels"], strict=True):
                b = tuple(float(x) for x in box.tolist())
                mask = _sam_mask(sam, img, b) if sam else _box_mask(img.size, b)
                out.append(Detection(frame=fp.name, term=terms[int(label)],
                                     box_xyxy=b, score=float(score), mask=mask))
    return out


def _load_sam2(device: str):
    try:
        from transformers import Sam2Model, Sam2Processor

        return (Sam2Processor.from_pretrained(SEGMENTER_ID),
                Sam2Model.from_pretrained(SEGMENTER_ID).to(device).eval(), device)
    except Exception:
        return None  # box-mask fallback below; coverage gate will flag quality


def _sam_mask(sam, img, box) -> np.ndarray:
    import torch

    proc, model, device = sam
    inputs = proc(images=img, input_boxes=[[list(box)]], return_tensors="pt").to(device)
    with torch.no_grad():
        out = model(**inputs, multimask_output=False)
    masks = proc.post_process_masks(out.pred_masks.cpu(),
                                    inputs["original_sizes"].cpu())[0]
    return masks[0, 0].numpy().astype(bool)


def _box_mask(size, box) -> np.ndarray:
    w, h = size
    m = np.zeros((h, w), dtype=bool)
    x0, y0, x1, y1 = (int(v) for v in box)
    m[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)] = True
    return m
