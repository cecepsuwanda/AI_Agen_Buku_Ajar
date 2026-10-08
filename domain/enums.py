"""Enum domain — MURNI (hanya stdlib).

``ChapterStatus`` mendefinisikan **rantai §27 lengkap sekarang juga**, jauh
sebelum gate-nya ada. Alasannya: menambah status belakangan berarti harus
migrasi seluruh berkas ``state/*.json`` yang sudah tertulis. Enum lengkap sejak
awal = tidak akan pernah perlu migrasi.
"""

from __future__ import annotations

from enum import StrEnum


class ChapterStatus(StrEnum):
    """Status sebuah bab sepanjang pipeline.

    Alur bahagia §27::

        PLANNED → RESEARCHED → DRAFTED → EXAMPLES_WRITTEN → EXERCISES_WRITTEN
                → FACT_CHECKED → CITATION_CHECKED → PEDAGOGY_REVIEWED
                → CONSISTENCY_CHECKED → REVIEWED
                → LATEX_GENERATED → LATEX_COMPILED → APPROVED

    Setiap tahap yang menghasilkan statusnya punya gate-nya sendiri; tahap yang
    fiturnya dimatikan — mis. LaTeX pada mesin tanpa perkakasnya — dilewati oleh
    ``agents.gates.PassThroughGate`` dengan ``skipped=True``, sehingga bentuk
    rantainya tetap setia pada §27 tanpa menjalankan apa pun.
    """

    # --- Tahap perencanaan & penulisan ------------------------------------
    PLANNED = "PLANNED"
    RESEARCHED = "RESEARCHED"
    DRAFTED = "DRAFTED"
    # §19/§20 menaruh Example Agent dan Exercise Agent SESUDAH Chapter Writer,
    # jadi keduanya adalah tahap penulisan — bukan bagian pemeriksaan di bawah.
    # Urutan keduanya mengikuti blueprint: contoh lebih dulu, latihan kemudian,
    # karena latihan yang baik sering merujuk contoh yang baru saja diberikan.
    EXAMPLES_WRITTEN = "EXAMPLES_WRITTEN"
    EXERCISES_WRITTEN = "EXERCISES_WRITTEN"

    # --- Tahap pemeriksaan kualitas (§21–§24, §26) -------------------------
    FACT_CHECKED = "FACT_CHECKED"
    CITATION_CHECKED = "CITATION_CHECKED"
    PEDAGOGY_REVIEWED = "PEDAGOGY_REVIEWED"
    CONSISTENCY_CHECKED = "CONSISTENCY_CHECKED"

    # --- Tahap LaTeX (§25, §26) -------------------------------------------
    REVIEWED = "REVIEWED"
    LATEX_GENERATED = "LATEX_GENERATED"
    LATEX_COMPILED = "LATEX_COMPILED"

    # --- Keadaan akhir & penyimpangan -------------------------------------
    APPROVED = "APPROVED"
    REVISION = "REVISION"
    FAILED_REVIEW = "FAILED_REVIEW"
    FAILED = "FAILED"


class ChapterEvent(StrEnum):
    """Peristiwa yang memicu transisi status."""

    RESEARCH = "RESEARCH"
    DRAFT = "DRAFT"
    REVISE = "REVISE"
    REVIEW_FAIL = "REVIEW_FAIL"
    APPROVE = "APPROVE"
    FAIL = "FAIL"


#: Status yang berarti bab sudah selesai dan tidak akan dikerjakan ulang.
TERMINAL_STATUSES: frozenset[ChapterStatus] = frozenset(
    {ChapterStatus.APPROVED, ChapterStatus.FAILED}
)

#: Status yang menandakan bab butuh perhatian manusia.
PROBLEM_STATUSES: frozenset[ChapterStatus] = frozenset(
    {ChapterStatus.FAILED_REVIEW, ChapterStatus.FAILED}
)


def is_terminal(status: ChapterStatus) -> bool:
    """True bila bab sudah tidak akan diproses lagi (MURNI)."""
    return status in TERMINAL_STATUSES


def is_approved(status: ChapterStatus) -> bool:
    """True bila bab sudah **selesai**, yaitu tidak perlu dikerjakan ulang (§28) (MURNI).

    Sengaja dibedakan dari :func:`is_terminal`. ``FAILED`` juga terminal — proses
    berhenti di sana — tetapi ia berarti "berhenti karena rusak", dan bab yang
    berhenti karena rusak justru yang paling perlu dikerjakan ulang saat resume.
    Menyamakan keduanya membuat resume melewati tepat bab-bab yang gagal.
    """
    return status is ChapterStatus.APPROVED


def is_problem(status: ChapterStatus) -> bool:
    """True bila bab gagal dan mungkin butuh tinjauan manusia (MURNI)."""
    return status in PROBLEM_STATUSES
