"""Evolve → distill pipeline (B5 — v0.6).

Take the experiment's top-K prompts, synthesise
(instruction → response) pairs with a strong teacher model, and
fine-tune a LoRA on the base model so the evolved prompt's behaviour
is baked into the weights.

Optional deps
-------------
* ``transformers>=4.46``
* ``peft>=0.14``
* ``accelerate>=1.0``

When any of these is missing, the CLI command prints an install
hint and exits cleanly. Tests use ``hf-internal-testing/tiny-
random-llama`` so CI does not download a real model.

Scope
-----
v0.6 ships the plumbing (data generation, LoRA config, training
loop). GPU-intensive sweeps live in ``experiments/phase4_b5_distill/``
and are gated behind ``HERMES_DISTILL=1`` env var.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class DistillUnavailable(RuntimeError):
    """Raised when optional ML deps aren't installed."""


def _deps_available() -> bool:
    for pkg in ("transformers", "peft", "accelerate"):
        if importlib.util.find_spec(pkg) is None:
            return False
    return True


def ensure_available() -> None:
    if not _deps_available():
        raise DistillUnavailable(
            "evolve→distill needs `pip install transformers peft accelerate` "
            "(install the `darwinian-evolver-distill` extras to pull them in).",
        )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class DistillConfig:
    base_model:     str = "hf-internal-testing/tiny-random-llama"
    output_dir:     Path = field(default_factory=lambda: Path.cwd() / "distilled")
    lora_r:         int = 8
    lora_alpha:     int = 16
    lora_dropout:   float = 0.05
    learning_rate:  float = 1e-4
    epochs:         int = 1
    batch_size:     int = 2
    max_length:     int = 256


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class DistillExample:
    instruction: str
    response:    str


def build_dataset(
    prompts: list[str],
    *,
    teacher_call: "callable[[str], str]",     # type: ignore[valid-type]
    inputs_per_prompt: int = 3,
    seed_inputs: Optional[list[str]] = None,
) -> list[DistillExample]:
    """For each evolved prompt, generate teacher responses on diverse inputs.

    The caller owns the ``teacher_call`` closure so we don't couple
    the distiller to a specific provider. A sensible default is to
    wrap ``LLMClient.complete`` with the best Claude / GPT-4 the user
    can afford — quality of teacher directly bounds distilled
    quality.
    """
    inputs = seed_inputs or [
        "Explain the concept of entropy in simple terms.",
        "Summarise the plot of Hamlet.",
        "Convert 25 °C to Fahrenheit.",
    ]
    dataset: list[DistillExample] = []
    for prompt in prompts:
        for inp in inputs[: inputs_per_prompt]:
            user = f"{prompt}\n\nInput:\n{inp}"
            response = teacher_call(user)
            dataset.append(DistillExample(instruction=user, response=response))
    return dataset


# ---------------------------------------------------------------------------
# Training (thin wrapper over transformers + peft)
# ---------------------------------------------------------------------------


def train_lora(
    examples: list[DistillExample],
    cfg: DistillConfig,
) -> Path:
    """Fine-tune a LoRA; return the saved adapter path.

    Kept intentionally small — real training configs live in the
    corresponding ``experiments/`` folder. This path exists so the
    unit tests can exercise the "tiny-random-llama" loop end-to-end.
    """
    ensure_available()
    import torch
    from transformers import (AutoTokenizer, AutoModelForCausalLM,  # type: ignore
                              TrainingArguments, Trainer)
    from peft import LoraConfig, get_peft_model                      # type: ignore

    tok = AutoTokenizer.from_pretrained(cfg.base_model, use_fast=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token or "<|pad|>"
    model = AutoModelForCausalLM.from_pretrained(cfg.base_model)

    lora = LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        target_modules=["q_proj", "v_proj"],
    )
    model = get_peft_model(model, lora)

    def _encode(ex: DistillExample) -> dict:
        text = ex.instruction + "\n\n" + ex.response
        enc = tok(text, truncation=True, max_length=cfg.max_length,
                  return_tensors="pt", padding="max_length")
        input_ids = enc["input_ids"].squeeze(0)
        return {"input_ids": input_ids, "labels": input_ids.clone(),
                "attention_mask": enc["attention_mask"].squeeze(0)}

    ds = [_encode(e) for e in examples]

    args = TrainingArguments(
        output_dir=str(cfg.output_dir),
        per_device_train_batch_size=cfg.batch_size,
        num_train_epochs=cfg.epochs,
        learning_rate=cfg.learning_rate,
        logging_steps=1,
        save_strategy="no",
        report_to=[],
    )
    trainer = Trainer(model=model, args=args, train_dataset=ds)
    trainer.train()

    adapter_path = cfg.output_dir / "adapter"
    model.save_pretrained(str(adapter_path))
    tok.save_pretrained(str(adapter_path))
    return adapter_path
