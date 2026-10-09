"""Perbandingan model (§33) — bagian yang murni, tanpa menjalankan satu model.

§33 meminta *"benchmark setiap model dengan tugas yang sama"*, dan berkas ini
menjaga tiga hal yang membuat hasilnya dapat dipercaya:

* **Skor yang dibaca adalah skor peninjau.** Pemeriksa sitasi, fakta, pedagogi,
  dan konsistensi menulis vonisnya ke daftar review yang sama, dan vonis mereka
  bernilai nol — yang mereka laporkan adalah temuan, bukan mutu. Bab yang
  sempurna akan terbaca berskor nol bila vonis terakhir yang dibaca kebetulan
  vonis seorang pemeriksa.
* **Nama direktori tidak pernah bertabrakan.** ``a:b`` dan ``a-b`` menghasilkan
  slug yang sama setelah karakter tidak aman diganti, dan dua model yang
  berbagi satu direktori akan saling menimpa catatannya tanpa gejala apa pun.
* **Daftar model yang diketik ganda tetap satu model.** Tabel yang menampilkan
  satu model dua kali terbaca seolah dua model diuji.
"""

from __future__ import annotations

from pathlib import Path

from app.commands import compare_slug, display_path, final_score, parse_model_list
from domain.chapter import ChapterRecord, ReviewResult
from domain.enums import ChapterStatus


def _record(*reviews: ReviewResult) -> ChapterRecord:
    """Record satu bab dengan vonis yang sudah ada."""
    return ChapterRecord(number=1, status=ChapterStatus.REVIEWED, reviews=reviews)


def _review(gate: str, score: int, *, approved: bool = True, skipped: bool = False) -> ReviewResult:
    """Satu vonis gate."""
    return ReviewResult(gate=gate, approved=approved, score=score, skipped=skipped)


# ---------------------------------------------------------------------------
# Skor yang dilaporkan
# ---------------------------------------------------------------------------
def test_the_reviewers_score_is_the_one_reported() -> None:
    """Vonis peninjau — bukan vonis gate lain yang kebetulan menyusul."""
    record = _record(_review("citation_checker", 0), _review("reviewer", 9))
    assert final_score(record) == 9


def test_a_checker_verdict_after_the_reviewer_does_not_replace_it() -> None:
    """Pemeriksa konsistensi berdiri **sesudah** peninjau di rantai §27.

    Inilah bentuk yang paling sering terjadi: vonis terakhir sebuah bab yang
    lulus adalah vonis pemeriksa berskor nol, dan membaca ``reviews[-1]`` akan
    melaporkan bab yang baik sebagai berskor nol.
    """
    record = _record(
        _review("reviewer", 8),
        _review("latex_writer", 5),
        _review("consistency_checker", 0),
    )
    assert final_score(record) == 8


def test_the_last_reviewer_verdict_wins() -> None:
    """Bab yang direvisi memiliki beberapa vonis peninjau; yang berlaku yang terakhir."""
    record = _record(_review("reviewer", 4), _review("reviewer", 9))
    assert final_score(record) == 9


def test_a_skipped_reviewer_verdict_is_not_a_score() -> None:
    """Gate yang dilewati belum menilai apa pun — nol, bukan "lulus dengan nol"."""
    record = _record(_review("reviewer", 7, skipped=True))
    assert final_score(record) == 0


def test_a_bab_without_a_reviewer_verdict_scores_zero() -> None:
    """Bab yang berhenti sebelum peninjau memang belum berskor."""
    assert final_score(_record(_review("fact_checker", 0))) == 0
    assert final_score(_record()) == 0


# ---------------------------------------------------------------------------
# Nama direktori
# ---------------------------------------------------------------------------
def test_unsafe_characters_become_dashes() -> None:
    """``:`` dan ``/`` tidak sah di nama direktori Windows."""
    slug = compare_slug("gemma4:31b-cloud")
    assert slug.startswith("gemma4-31b-cloud-")
    assert ":" not in slug and "/" not in slug


def test_two_names_that_sanitize_alike_do_not_share_a_directory() -> None:
    """Celah yang ditutup sidik jari: ``a:b`` dan ``a-b`` berbeda sebagai model."""
    assert compare_slug("a:b") != compare_slug("a-b")


def test_a_slug_is_deterministic() -> None:
    """Nama direktori yang berubah antar-jalankan akan menumpuk hasil yang basi."""
    assert compare_slug("gemma3:4b") == compare_slug("gemma3:4b")


def test_a_name_without_a_single_safe_character_still_yields_a_directory() -> None:
    """Nama yang seluruhnya karakter aneh tetap menghasilkan sesuatu yang dapat dibuat."""
    slug = compare_slug("///")
    assert slug.startswith("model-")


def test_a_path_under_the_working_directory_is_shown_relative() -> None:
    """Pesan yang menyebut jalur absolut tidak dapat disalin-tempel."""
    assert display_path(Path.cwd() / "state" / "compare") == "state/compare"


def test_a_path_outside_the_working_directory_is_shown_as_it_is() -> None:
    """Di luar direktori kerja tidak ada bentuk relatif yang jujur."""
    outside = Path.cwd().parent / "di-luar"
    assert display_path(outside) == outside.as_posix()


# ---------------------------------------------------------------------------
# Daftar model
# ---------------------------------------------------------------------------
def test_models_are_split_on_commas_and_trimmed() -> None:
    """Bentuk yang diketik di baris perintah: ``--models a, b ,c``."""
    assert parse_model_list(" a , b ,c") == ("a", "b", "c")


def test_empty_parts_are_dropped() -> None:
    """Koma di ujung adalah kebiasaan mengetik, bukan model tanpa nama."""
    assert parse_model_list("a,,b,") == ("a", "b")


def test_a_model_named_twice_is_one_model() -> None:
    """Dua baris untuk satu model akan terbaca seolah dua model diuji."""
    assert parse_model_list("a,b,a") == ("a", "b")


def test_an_empty_list_stays_empty() -> None:
    """Yang kosong harus tetap kosong — pemanggilnya yang memutuskan itu kesalahan."""
    assert parse_model_list("") == ()
    assert parse_model_list(" , ") == ()
