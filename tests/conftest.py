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
    """Paket riset kosong — kondisi seluruh MVP ini (RAG belum ada).

    ``degraded=True`` adalah bagian pentingnya: writer harus bekerja tanpanya,
    dan tes harus memastikan ia memang bekerja.
    """
    return ResearchPackage.empty()


@pytest.fixture
def full_research() -> ResearchPackage:
    """Paket riset terisi — kondisi setelah RAG tersedia (Tahap 3)."""
    return ResearchPackage(
        concepts=("kompleksitas waktu", "notasi Big-O"),
        definitions=("O(n) berarti waktu eksekusi tumbuh linear terhadap masukan.",),
        examples=("Pencarian linear pada larik.",),
        evidence=(
            Evidence(
                source="Cormen, Introduction to Algorithms, 4th ed.",
                page=45,
                section="3.1",
                text="Notasi asimtotik menggambarkan laju pertumbuhan.",
                score=0.91,
            ),
        ),
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
    """Konteks render untuk kelima prompt, memakai cabang riset kosong.

    Dikembalikan sebagai peta ``nama prompt -> konteks`` supaya tes dapat
    mengulanginya tanpa menyalin-tempel argumen.
    """
    common = _common_book_args()
    return {
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
