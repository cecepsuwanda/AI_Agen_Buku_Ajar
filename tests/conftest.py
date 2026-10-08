"""Fixture bersama.

Dua keputusan yang disengaja di sini:

1. **Prompt dibaca dari direktori ``prompts/`` yang asli**, bukan dari salinan
   palsu. Membaca berkas itu murni, dan berkas prompt itu sendiri sedang diuji —
   memakai salinan berarti tes tetap hijau meskipun prompt aslinya rusak.
2. **Prompt bukan satu-satunya yang di-*fake*.** Hanya ``ChatModel`` (dan nanti
   ``Reporter``) yang dipalsukan. Justru itu inti pembayaran DIP: seluruh
   pipeline berjalan sungguhan tanpa jaringan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.prompting import FilePromptLibrary
from agents.context import evidence_lines
from domain.chapter import Evidence, ResearchPackage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"


@pytest.fixture(scope="session")
def prompts_dir() -> Path:
    """Direktori prompt yang asli."""
    return PROMPTS_DIR


@pytest.fixture(scope="session")
def prompt_library(prompts_dir: Path) -> FilePromptLibrary:
    """Pustaka prompt asli, dimuat sekali untuk seluruh sesi tes."""
    return FilePromptLibrary(prompts_dir)


@pytest.fixture
def empty_research() -> ResearchPackage:
    """Paket riset kosong — kondisi ketika RAG mati atau indeksnya belum dibangun.

    ``degraded=True`` adalah bagian pentingnya: writer harus bekerja tanpanya,
    dan tes harus memastikan ia memang bekerja.
    """
    return ResearchPackage.empty()


def _fixture_evidence() -> tuple[Evidence, ...]:
    """Bukti contoh, bentuknya persis seperti yang dikembalikan retriever (§13).

    Satu halaman untuk dua potongan yang berurutan: itulah yang membuat tes
    halaman dapat membuktikan bahwa ``page`` benar-benar diteruskan ke dalam
    prompt, bukan sekadar ada di dalam tipe.
    """
    return (
        Evidence(
            source="Cormen, Introduction to Algorithms, 4th ed.",
            page=45,
            section="3.1",
            text="Notasi asimtotik menggambarkan laju pertumbuhan.",
            score=0.91,
        ),
        Evidence(
            source="Cormen, Introduction to Algorithms, 4th ed.",
            page=46,
            section="3.2",
            text="O(n) berarti waktu eksekusi tumbuh linear terhadap masukan.",
            score=0.84,
        ),
    )


@pytest.fixture
def full_research() -> ResearchPackage:
    """Paket riset terisi — kondisi ketika bahan bersumber halaman tersedia (§17)."""
    return ResearchPackage(
        concepts=("kompleksitas waktu", "notasi Big-O"),
        definitions=("O(n) berarti waktu eksekusi tumbuh linear terhadap masukan.",),
        examples=("Pencarian linear pada larik.",),
        evidence=_fixture_evidence(),
        sources=("Cormen, Introduction to Algorithms, 4th ed.",),
        degraded=False,
    )


def _common_book_args() -> dict[str, Any]:
    """Argumen yang dipakai hampir semua prompt bab."""
    return {
        "book_title": "Algoritma dan Struktur Data",
        "language": "id",
        "number": 2,
        "chapter_title": "Analisis Kompleksitas",
        "min_words": 1200,
        "objectives": (
            "Mahasiswa mampu menghitung kompleksitas waktu algoritma sederhana",
            "Mahasiswa mampu membandingkan dua algoritma berdasarkan notasi Big-O",
        ),
        "style_guide": "Bahasa Indonesia akademik formal.",
        "previous_summaries": ("Bab 1 membahas pengertian algoritma.",),
        "terminology": ("algoritma", "kompleksitas waktu"),
    }


@pytest.fixture
def prompt_contexts(
    empty_research: ResearchPackage,
) -> dict[str, dict[str, Any]]:
    """Konteks render untuk setiap prompt, memakai cabang riset kosong.

    Dikembalikan sebagai peta ``nama prompt -> konteks`` supaya tes dapat
    mengulanginya tanpa menyalin-tempel argumen.
    """
    common = _common_book_args()
    return {
        "citation.chapter": {
            **common,
            "citations": ("Cormen, Introduction to Algorithms, 4th ed.",),
            "evidence": _fixture_evidence(),
            "research": empty_research,
            "draft_json": '{"title": "Analisis Kompleksitas"}',
        },
        "example.chapter": {
            **common,
            "required_examples": 3,
            "draft_json": '{"title": "Analisis Kompleksitas"}',
            "research": empty_research,
            "feedback": (),
        },
        "exercise.chapter": {
            **common,
            "required_exercises": 5,
            "draft_json": '{"title": "Analisis Kompleksitas"}',
            "research": empty_research,
            "feedback": ("2 latihan tidak menyebut satu pun tujuan bab.",),
        },
        "factcheck.chapter": {
            **common,
            "claims": ("Pencarian biner memeriksa paling banyak 20 elemen.",),
            "evidence": _fixture_evidence(),
            "research": empty_research,
            "draft_json": '{"title": "Analisis Kompleksitas"}',
        },
        "planner.book": {
            "title": "Algoritma dan Struktur Data",
            "audience": "mahasiswa S1",
            "language": "id",
            "target_chapters": 8,
            "style_guide": "Bahasa Indonesia akademik formal.",
            "topics": ("analisis kompleksitas",),
            "rps_text": "Minggu 1: Pengantar algoritma.\nMinggu 2: Notasi Big-O.",
        },
        "planner.chapter": {**common, "planned_sections": ("Pengantar", "Notasi Big-O")},
        "ocr.page": {"page": 7},
        "researcher.chapter": {
            **common,
            "section_plan": ("Pengantar", "Notasi Big-O"),
            "evidence": evidence_lines(_fixture_evidence()),
        },
        "writer.chapter": {
            **common,
            "section_plan": ("Pengantar", "Notasi Big-O"),
            "required_examples": 3,
            "required_exercises": 5,
            "research": empty_research,
        },
        "writer.revise": {
            **common,
            "revision": 1,
            "feedback": ("Sub-bab 2.1 belum menjelaskan notasi Big-O.",),
            "current_draft_json": '{"title": "Analisis Kompleksitas"}',
        },
        "reviewer.chapter": {
            **common,
            "draft_json": '{"title": "Analisis Kompleksitas"}',
            "research": empty_research,
        },
    }


__all__ = ["PROJECT_ROOT", "PROMPTS_DIR"]
