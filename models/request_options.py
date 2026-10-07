"""Kosakata opsi permintaan Ollama — satu fungsi murni, dua pemakainya.

Berkas ini ada karena **dua adapter perlu jawaban yang sama** atas pertanyaan
"dengan angka berapa permintaan ini sebenarnya dikirim?":

* :class:`~models.ollama_client.OllamaChatModel` menyusun payload dari jawaban
  itu;
* :class:`~models.dry_run.DryRunChatModel` menuliskannya ke berkas prompt.

Menyalin aturannya ke keduanya akan menghasilkan artefak yang paling merugikan:
``--dry-run`` yang menampilkan angka **berbeda** dari yang benar-benar dikirim,
tepat pada satu-satunya keadaan orang menjalankannya — saat ia ingin tahu angka
apa yang akan dikirim.

Diletakkan di modul tersendiri, bukan di dalam ``ollama_client``, karena
``models/dry_run.py`` tidak boleh bergantung pada adapter SDK: modul yang
seluruh gunanya tidak menyentuh Ollama tidak pantas meng-import ``ollama``.
"""

from __future__ import annotations

from typing import Any

from domain.ports import ChatRequest
from models.model_registry import ModelSpec


def resolve_options(request: ChatRequest, spec: ModelSpec) -> dict[str, Any]:
    """Gabungkan opsi per-panggilan dengan default peran (MURNI).

    ``None`` berarti **"tidak diset"**, bukan nol — dan bagi panggilan LLM
    perbedaan itu nyata: ``temperature=0`` adalah permintaan determinisme yang
    sah, sedangkan ``temperature=None`` berarti "pakai bawaan peran". Karena itu
    nilainya dipilih dengan ``is not None``, bukan dengan ``or``.

    Nama kuncinya adalah kosakata Ollama (``num_predict``, ``num_ctx``), bukan
    kosakata domain — memang itu tujuannya: yang dikembalikan fungsi ini adalah
    potongan ``options`` yang siap masuk ke payload.
    """
    options: dict[str, Any] = {}

    temperature = request.temperature if request.temperature is not None else spec.temperature
    if temperature is not None:
        options["temperature"] = temperature

    max_tokens = request.max_tokens if request.max_tokens is not None else spec.max_tokens
    if max_tokens is not None:
        options["num_predict"] = max_tokens

    num_ctx = request.num_ctx if request.num_ctx is not None else spec.num_ctx
    if num_ctx is not None:
        options["num_ctx"] = num_ctx

    if request.stop:
        options["stop"] = list(request.stop)

    return options


def describe_options(options: dict[str, Any]) -> str:
    """Rangkum opsi menjadi satu baris yang terbaca mata (MURNI).

    Diurutkan berdasarkan nama kunci, bukan berdasarkan urutan penyisipan:
    keluaran ``--dry-run`` harus dapat dibandingkan antar-jalankan dan antar-diff
    git, dan urutan yang berasal dari urutan percabangan ``if`` tidak menjamin itu.
    """
    if not options:
        return "(bawaan server)"
    return " ".join(f"{key}={options[key]}" for key in sorted(options))


__all__ = ["describe_options", "resolve_options"]
