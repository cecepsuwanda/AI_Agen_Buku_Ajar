"""Tes kontrak prompt (§36).

Yang paling penting diuji di sini bukan "apakah prompt bisa dirender", melainkan
**apakah bagian OUTPUT-nya tidak mungkin menyimpang dari skema**. Itulah alasan
kontrak itu dibangkitkan dari tipe, bukan ditulis tangan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.prompting import (
    OUTPUT_MODELS,
    FilePromptLibrary,
    prompt_digest,
    split_front_matter,
    validate_metadata,
)
from domain.chapter import ReviewVerdict
from domain.errors import ConfigError
from domain.structured import strict_schema

#: Kesembilan prompt yang menjadi kontrak build ini. Daftarnya sengaja literal:
#: menambah prompt baru harus menuntut keputusan sadar di sini, bukan lolos
#: diam-diam.
EXPECTED_PROMPTS = (
    "example.chapter",
    "exercise.chapter",
    "ocr.page",
    "planner.book",
    "planner.chapter",
    "researcher.chapter",
    "reviewer.chapter",
    "writer.chapter",
    "writer.revise",
)


# ---------------------------------------------------------------------------
# Pemuatan
# ---------------------------------------------------------------------------
def test_library_loads_exactly_the_expected_prompts(prompt_library: FilePromptLibrary) -> None:
    """Himpunan prompt yang dimuat sama persis dengan yang diharapkan."""
    assert prompt_library.names() == EXPECTED_PROMPTS


@pytest.mark.parametrize("name", EXPECTED_PROMPTS)
def test_every_prompt_declares_a_known_output_model(
    prompt_library: FilePromptLibrary, name: str
) -> None:
    """Setiap prompt menunjuk ``output_model`` yang benar-benar ada."""
    assert prompt_library.output_model_for(name) in OUTPUT_MODELS.values()


def test_all_prompts_render_with_full_context(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]]
) -> None:
    """Kesembilan prompt render tanpa kesalahan dengan konteks lengkap."""
    for name in EXPECTED_PROMPTS:
        rendered = prompt_library.render(name, prompt_contexts[name])
        assert rendered.name == name
        assert rendered.user.strip(), f"prompt {name} menghasilkan badan kosong"
        assert rendered.hash and len(rendered.hash) == 12


def test_output_contract_is_injected_automatically(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]]
) -> None:
    """Pemanggil tidak perlu menyuplai ``output_contract`` — ia dibangkitkan."""
    rendered = prompt_library.render("reviewer.chapter", prompt_contexts["reviewer.chapter"])

    assert "Kerangka:" in rendered.user


# ---------------------------------------------------------------------------
# Kontrak OUTPUT tidak dapat menyimpang dari skema
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", EXPECTED_PROMPTS)
def test_rendered_output_contract_lists_every_schema_field(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]], name: str
) -> None:
    """Setiap field skema muncul di kontrak OUTPUT yang dirender.

    Inilah yang membuat bagian OUTPUT prompt tidak mungkin menyimpang dari
    ``format=``: keduanya dibangkitkan dari model Pydantic yang sama, dan tes ini
    mengunci kesamaan itu.
    """
    model = prompt_library.output_model_for(name)
    rendered = prompt_library.render(name, prompt_contexts[name])
    fields = set(strict_schema(model).get("properties", {}))

    assert fields, f"model {model.__name__} tidak punya field"
    missing = {field for field in fields if field not in rendered.user}
    assert not missing, f"prompt {name} tidak mendokumentasikan field: {sorted(missing)}"


@pytest.mark.parametrize("name", EXPECTED_PROMPTS)
def test_rendered_prompt_documents_required_properties_as_mandatory(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]], name: str
) -> None:
    """Kontrak menyatakan bahwa semua field wajib — sesuai ``strict_schema``."""
    rendered = prompt_library.render(name, prompt_contexts[name])
    assert "WAJIB" in rendered.user


# ---------------------------------------------------------------------------
# StrictUndefined
# ---------------------------------------------------------------------------
def test_missing_context_variable_names_the_variable(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]]
) -> None:
    """Variabel yang hilang melempar ConfigError yang menyebut namanya.

    Bukan diam-diam menjadi string kosong: prompt yang merender
    ``spec.objectives`` yang hilang sebagai "" menghasilkan bab yang buruk, dan
    penyebabnya baru terlihat dua puluh menit kemudian — jauh dari sumbernya.
    """
    context = dict(prompt_contexts["reviewer.chapter"])
    del context["draft_json"]

    with pytest.raises(ConfigError, match="draft_json"):
        prompt_library.render("reviewer.chapter", context)


def test_unknown_prompt_name_lists_alternatives(prompt_library: FilePromptLibrary) -> None:
    """Nama prompt yang tidak ada ditolak, dan alternatifnya disebutkan."""
    with pytest.raises(ConfigError, match="Tersedia"):
        prompt_library.render("tidak.ada", {})


# ---------------------------------------------------------------------------
# Validasi front-matter — gagal saat konstruksi, bukan saat dipakai
# ---------------------------------------------------------------------------
def test_unknown_output_model_key_fails_at_construction(tmp_path: Path) -> None:
    """``output_model`` yang tidak dikenal menggagalkan pemuatan, bukan pemakaian.

    Menemukan kesalahan ini di tengah bab ke-5 adalah kegagalan yang mahal dan
    sepenuhnya dapat dihindari.
    """
    (tmp_path / "rusak.md").write_text(
        "---\nversion: '1'\nrole: writer\noutput_model: writer.TidakAda\n---\nisi\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="writer.TidakAda"):
        FilePromptLibrary(tmp_path)


def test_missing_front_matter_key_fails_at_construction(tmp_path: Path) -> None:
    """Front-matter tanpa kunci wajib ditolak."""
    (tmp_path / "rusak.md").write_text("---\nrole: writer\n---\nisi\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="output_model"):
        FilePromptLibrary(tmp_path)


def test_empty_prompt_directory_is_rejected(tmp_path: Path) -> None:
    """Direktori prompt kosong ditolak, bukan menghasilkan pustaka kosong."""
    with pytest.raises(ConfigError, match="Tidak ada berkas prompt"):
        FilePromptLibrary(tmp_path)


def test_missing_prompt_directory_is_rejected(tmp_path: Path) -> None:
    """Direktori prompt yang tidak ada ditolak dengan menyebut jalurnya."""
    with pytest.raises(ConfigError) as excinfo:
        FilePromptLibrary(tmp_path / "tidak-ada")

    assert "tidak-ada" in str(excinfo.value)


# ---------------------------------------------------------------------------
# split_front_matter (murni)
# ---------------------------------------------------------------------------
def test_text_without_front_matter_is_returned_unchanged() -> None:
    """Berkas tanpa front-matter dikembalikan apa adanya."""
    meta, body = split_front_matter("# Judul\n\nIsi.\n")

    assert meta == {}
    assert body == "# Judul\n\nIsi.\n"


def test_horizontal_rule_inside_body_is_not_front_matter() -> None:
    """``---`` di tengah badan adalah garis Markdown biasa, bukan metadata.

    Ini penting: prompt sengaja memakai ``---`` sebagai pemisah visual, dan
    salah menafsirkannya akan memotong separuh isi prompt tanpa peringatan.
    """
    meta, body = split_front_matter("Isi awal.\n\n---\n\nIsi lanjutan.\n")

    assert meta == {}
    assert "Isi lanjutan." in body


def test_unclosed_front_matter_is_reported() -> None:
    """Front-matter yang tidak ditutup dilaporkan, bukan dipotong diam-diam."""
    with pytest.raises(ConfigError, match="tidak ditutup"):
        split_front_matter("---\nversion: '1'\n\nIsi tanpa penutup\n")


def test_front_matter_that_is_not_a_mapping_is_rejected() -> None:
    """Front-matter berupa daftar ditolak."""
    with pytest.raises(ConfigError, match="mapping"):
        split_front_matter("---\n- satu\n- dua\n---\nisi\n")


def test_bom_is_tolerated() -> None:
    """BOM dari editor Windows tidak boleh menggagalkan pengenalan front-matter."""
    meta, _ = split_front_matter("﻿---\nversion: '1'\n---\nisi\n")

    assert meta["version"] == "1"


def test_validate_metadata_returns_the_model_class() -> None:
    """``validate_metadata`` mengembalikan kelas model, bukan sekadar memvalidasi."""
    meta = {"version": "1", "role": "r", "output_model": "reviewer.ReviewVerdict"}
    model = validate_metadata("uji", meta)

    assert model is ReviewVerdict


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------
def test_digest_changes_when_content_changes() -> None:
    """Hash berbeda untuk isi berbeda — versi manual bisa lupa dinaikkan, hash tidak."""
    assert prompt_digest("satu") != prompt_digest("dua")


def test_digest_is_stable_for_identical_content() -> None:
    """Isi yang sama menghasilkan hash yang sama, lintas pemanggilan."""
    assert prompt_digest("sama") == prompt_digest("sama")


# ---------------------------------------------------------------------------
# Perilaku prompt yang menegakkan §34
# ---------------------------------------------------------------------------
def test_writer_prompt_forbids_inventing_citations_when_degraded(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]]
) -> None:
    """Tanpa bahan rujukan, writer dilarang mencantumkan rujukan apa pun.

    Ini konflik cakupan yang paling signifikan pada MVP (§34 vs RAG yang belum
    ada), dan mitigasinya harus hidup di dalam prompt — bukan hanya di dokumen
    rencana yang tidak dibaca model.
    """
    rendered = prompt_library.render("writer.chapter", prompt_contexts["writer.chapter"])
    lowered = rendered.user.lower()

    assert "unresolved_claims" in rendered.user
    assert "jangan mencantumkan" in lowered or "harus kosong" in lowered
    assert "mengarang" in lowered


def test_writer_prompt_with_research_requires_citations(
    prompt_library: FilePromptLibrary,
    prompt_contexts: dict[str, dict[str, Any]],
    full_research: Any,
) -> None:
    """Dengan bahan tersedia, prompt berubah: sumber harus dicantumkan."""
    context = dict(prompt_contexts["writer.chapter"])
    context["research"] = full_research

    rendered = prompt_library.render("writer.chapter", context)

    assert "Cormen" in rendered.user
    assert "citations" in rendered.user


def test_reviewer_prompt_does_not_penalise_empty_citations_when_degraded(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]]
) -> None:
    """Reviewer diberi tahu bahwa rujukan kosong itu benar — bukan alasan menolak.

    Tanpa ini, reviewer akan menolak setiap bab pada MVP karena tidak ada
    rujukan, dan penulis akan diminta memperbaiki sesuatu yang tidak dapat
    diperbaiki.
    """
    rendered = prompt_library.render("reviewer.chapter", prompt_contexts["reviewer.chapter"])

    assert "kosong adalah benar" in rendered.user


def test_reviewer_prompt_states_the_approval_threshold(
    prompt_library: FilePromptLibrary, prompt_contexts: dict[str, dict[str, Any]]
) -> None:
    """Ambang persetujuan disebut eksplisit di dalam prompt."""
    rendered = prompt_library.render("reviewer.chapter", prompt_contexts["reviewer.chapter"])

    assert "skor >= 7" in rendered.user
