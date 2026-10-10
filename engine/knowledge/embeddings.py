"""Text embeddings, 384 dimensions.

Backends (``EMBEDDINGS_BACKEND``):

* ``fastembed``: ``BAAI/bge-small-en-v1.5`` through fastembed (a real semantic model). The model
  is downloaded from Hugging Face on first use.
* ``hash``: a deterministic fallback. Signed, hashed word, word-pair and character-trigram
  features, L2-normalised. It is lexical, not semantic: it finds texts that share words or word
  stems, it does not know that "invoice" and "bill" mean the same thing. It exists so tests and
  offline demos run, and it is labelled as such wherever it is reported.
* unset (auto): try ``fastembed`` once, fall back to ``hash`` when the model cannot be loaded.

``backend_name()`` reports which one is active; the store records it so vectors from two
backends are never mixed in one database.

Text is passed through ``mask_sensitive`` before it is embedded (SSNs and sensitive field
values never become vectors). The stored text is not altered.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from itertools import pairwise
from typing import Any

DIM = 384
FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
HASH_BACKEND_NAME = f"hash-{DIM}"
FASTEMBED_BACKEND_NAME = f"fastembed:{FASTEMBED_MODEL}"

# Minimum cosine similarity for the vector half of a search. 0.66 for fastembed is the specified
# default and is UNVALIDATED: the model could not be downloaded where this was built, so it has
# not been measured against real bge-small similarities. Recalibrate when the model runs.
# The hash backend has a different, lexical similarity scale; its floor was measured, see
# ``engine/tests/test_knowledge.py::test_hash_floor_calibration``. Hash recall for paraphrases
# relies on the keyword half of the search, not on the vectors.
FASTEMBED_VECTOR_FLOOR = 0.66
HASH_VECTOR_FLOOR = 0.25

_lock = threading.Lock()
_state: dict[str, Any] = {"backend": None, "model": None, "note": ""}

STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "of", "on", "or", "s", "so", "such", "that", "the", "their", "then", "there", "these", "this", "to", "was", "were", "what", "when", "where", "which", "who", "why", "will", "with", "you", "your", "do", "does", "did", "can", "could", "should", "would", "about"]
)


# ------------------------------------------------------------------ backend selection
def _load_fastembed() -> Any:
    from fastembed import TextEmbedding

    return TextEmbedding(FASTEMBED_MODEL)


def _load_with_timeout() -> Any:
    """Load the model, giving up after ``EMBEDDINGS_LOAD_TIMEOUT`` seconds (default 30). A
    blocked network otherwise costs fastembed's retry sleeps (about 40 seconds) on first use."""
    timeout = float(os.environ.get("EMBEDDINGS_LOAD_TIMEOUT", "30"))
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["model"] = _load_fastembed()
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            box["error"] = exc

    worker = threading.Thread(target=run, name="fastembed-load", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"loading {FASTEMBED_MODEL} took longer than {timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["model"]


def _select() -> None:
    with _lock:
        if _state["backend"] is not None:
            return
        wanted = os.environ.get("EMBEDDINGS_BACKEND", "").strip().lower()
        if wanted == "hash":
            _state.update(backend="hash", model=None, note="EMBEDDINGS_BACKEND=hash")
            return
        try:
            model = _load_with_timeout()
        except Exception as exc:  # network refusal, missing cache, onnx errors, timeout
            if wanted == "fastembed":
                raise RuntimeError(f"fastembed model {FASTEMBED_MODEL} unavailable: {exc}") from exc
            _state.update(
                backend="hash", model=None,
                note=f"fastembed unavailable ({type(exc).__name__}); using the hash fallback",
            )
            return
        _state.update(backend="fastembed", model=model, note="")


def reset_backend() -> None:
    """Forget the backend decision (tests; re-reads ``EMBEDDINGS_BACKEND``)."""
    with _lock:
        _state.update(backend=None, model=None, note="")


def backend_name() -> str:
    _select()
    return FASTEMBED_BACKEND_NAME if _state["backend"] == "fastembed" else HASH_BACKEND_NAME


def backend_note() -> str:
    """Why the hash backend is active, or an empty string."""
    _select()
    return _state["note"]


def default_vector_floor() -> float:
    _select()
    return FASTEMBED_VECTOR_FLOOR if _state["backend"] == "fastembed" else HASH_VECTOR_FLOOR


# ------------------------------------------------------------------ hash backend
_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _stem(w: str) -> str:
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


def tokens(text: str) -> list[str]:
    """Lowercase word tokens; camelCase and snake_case identifiers also yield their parts."""
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9_]+", text):
        parts = [p for p in re.split(r"_+", _CAMEL.sub("_", raw)) if p]
        whole = raw.replace("_", "").lower()
        if len(parts) > 1 and whole not in STOPWORDS:
            out.append(whole)
        out.extend(p.lower() for p in parts)
    return [t for t in out if t not in STOPWORDS]


def _slot(feature: str) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(h, "big")
    return n % DIM, 1.0 if (n >> 40) & 1 else -1.0


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    toks = tokens(text)

    def add(feature: str, weight: float) -> None:
        i, sign = _slot(feature)
        vec[i] += sign * weight

    for t in toks:
        add("w:" + t, 1.0)
        s = _stem(t)
        if s != t:
            add("w:" + s, 0.7)
        padded = f"#{s}#"
        for j in range(len(padded) - 2):
            add("c:" + padded[j:j + 3], 0.25)
    for a, b in pairwise(toks):
        add(f"b:{_stem(a)}_{_stem(b)}", 0.5)
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


# ------------------------------------------------------------------ public API
def embed(texts: list[str]) -> list[list[float]]:
    """One 384-dimension, L2-normalised vector per text, in order."""
    if not texts:
        return []
    from engine.agents.provider import mask_sensitive  # lazy: provider imports knowledge.memory

    safe = [mask_sensitive(t) for t in texts]
    _select()
    if _state["backend"] == "fastembed":
        vectors = [list(map(float, v)) for v in _state["model"].embed(safe)]
        out: list[list[float]] = []
        for v in vectors:
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out
    return [_hash_embed(t) for t in safe]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
