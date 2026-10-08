"""Pemetaan bab ke minggu RPS — §12.

Aturan :func:`~domain.rules.align_source_weeks` ada karena minggu adalah **fakta
RPS**, bukan sesuatu yang boleh dikarang model. Yang diuji di sini bukan hanya
bahwa pemetaan yang salah diperbaiki, melainkan bahwa ia diperbaiki dengan
**cara yang benar untuk tiap jenis kesalahan**:

* minggu ujian yang dijadikan bahan bab disebut sebagai minggu ujian — bukan
  sebagai "minggu yang tidak ada", karena minggu 8 memang ada di RPS, dan orang
  yang membuka RPS untuk memeriksanya harus menemukannya;
* minggu yang tidak dipakai bab mana pun adalah masalah yang berbeda dari minggu
  yang dipakai dua kali, dan keduanya dilaporkan apa adanya;
* pemetaan yang **sah** tidak disentuh sama sekali. Perencana membaca topiknya
  dan tahu bahwa minggu 1 dan 2 sekeluarga; membuang keputusan itu berarti
  membuang satu-satunya bagian pekerjaan ini yang memang butuh pertimbangan.

Berkas ini juga menaruh tes :func:`~domain.rules.even_groups` dan
:func:`~domain.rules.parse_week_labels`, karena keduanya hanya dipakai di sini.
"""

from __future__ import annotations

from domain.book import BookSpec, ChapterSpec
from domain.rps import CoursePlan, WeekPlan
from domain.rules import (
    align_source_weeks,
    even_groups,
    parse_week_labels,
    reconcile_book_spec,
)

# ---------------------------------------------------------------------------
# Alat bantu
# ---------------------------------------------------------------------------


def make_course(*assessment: int, last: int = 6) -> CoursePlan:
    """RPS pendek berminggu ``1..last``, dengan minggu ujian yang disebutkan."""
    return CoursePlan(
        weeks=tuple(
            WeekPlan(number=n, topic=f"Topik {n}", is_assessment=n in assessment)
            for n in range(1, last + 1)
        )
    )


def make_spec(*week_groups: tuple[str, ...]) -> BookSpec:
    """Spesifikasi buku dengan satu bab per kelompok label minggu."""
    return BookSpec(
        title="Buku Uji",
        chapters=tuple(
            ChapterSpec(number=index, title=f"Bab {index}", source_weeks=group)
            for index, group in enumerate(week_groups, start=1)
        ),
    )


def labels(spec: BookSpec) -> tuple[tuple[str, ...], ...]:
    """``source_weeks`` setiap bab, untuk dibandingkan sebagai satu nilai."""
    return tuple(chapter.source_weeks for chapter in spec.chapters)


# ---------------------------------------------------------------------------
# even_groups — pembagian merata
# ---------------------------------------------------------------------------
def test_groups_divide_evenly_when_they_can() -> None:
    assert even_groups([1, 2, 3, 4], 2) == ((1, 2), (3, 4))


def test_the_remainder_goes_to_the_earlier_groups() -> None:
    """Bab awal memuat materi dasar yang lebih padat; kelompok terakhir yang
    hanya berisi satu minggu lebih mudah ditulis daripada bab pengantar yang
    hanya berisi satu minggu."""
    assert even_groups([1, 2, 3, 4, 5], 2) == ((1, 2, 3), (4, 5))


def test_more_chapters_than_weeks_leaves_empty_groups_at_the_end() -> None:
    """Dan itu memang keadaan sebenarnya: RPS-nya tidak punya cukup minggu."""
    assert even_groups([1, 2], 3) == ((1,), (2,), ())


def test_no_chapters_means_no_groups() -> None:
    assert even_groups([1, 2, 3], 0) == ()


def test_no_weeks_means_empty_groups() -> None:
    assert even_groups([], 3) == ((), (), ())


def test_every_item_lands_in_exactly_one_group() -> None:
    """Sifat yang paling penting: tidak ada minggu yang hilang atau kembar."""
    for count in range(1, 8):
        groups = even_groups(tuple(range(1, 8)), count)
        flat = [number for group in groups for number in group]
        assert flat == list(range(1, 8)), count


# ---------------------------------------------------------------------------
# parse_week_labels — bentuk label yang diterima
# ---------------------------------------------------------------------------
def test_a_plain_label_is_read() -> None:
    assert parse_week_labels(["Minggu 3"]) == (3,)


def test_a_range_is_expanded() -> None:
    assert parse_week_labels(["Minggu 1-3"]) == (1, 2, 3)


def test_several_labels_are_concatenated_in_order() -> None:
    """Bentuk yang diminta prompt kepada perencana: ``["Minggu 7", "Minggu 9"]``."""
    assert parse_week_labels(["Minggu 7", "Minggu 9"]) == (7, 9)


def test_a_comma_separated_label_is_read_like_a_list() -> None:
    assert parse_week_labels(["Minggu 7, 9"]) == (7, 9)


def test_bare_numbers_are_accepted() -> None:
    """Model kadang menulis ``"3"``; artinya sama dan tidak perlu ditolak."""
    assert parse_week_labels(["3", "4-5"]) == (3, 4, 5)


def test_an_en_dash_is_read_like_a_hyphen() -> None:
    """Tanda hubung panjang muncul ketika model menyalin dari dokumen."""
    assert parse_week_labels(["Minggu 9–15"]) == tuple(range(9, 16))


def test_a_descending_range_is_not_read() -> None:
    """``"Minggu 5-3"`` tidak bermakna, dan menebaknya berarti menebak."""
    assert parse_week_labels(["Minggu 5-3"]) is None


def test_a_range_without_an_end_is_not_read() -> None:
    assert parse_week_labels(["Minggu 5-"]) is None


def test_a_wordy_label_is_not_read() -> None:
    """Dibiarkan tidak terbaca, karena yang dipertaruhkan adalah pemetaan materi."""
    assert parse_week_labels(["minggu pertama"]) is None


def test_an_empty_label_is_not_read() -> None:
    assert parse_week_labels([""]) is None


def test_one_unreadable_label_among_good_ones_returns_none() -> None:
    """Seluruh bab dipetakan ulang bila salah satunya tidak terbaca — bukan hanya
    yang rusak. Membiarkan sebagian tetap berarti buku yang setengah terpetakan."""
    assert parse_week_labels(["Minggu 1-2", "awal semester"]) is None


def test_no_labels_is_an_empty_tuple_not_none() -> None:
    """"Tidak ada label" dan "label tidak terbaca" adalah dua hal berbeda."""
    assert parse_week_labels([]) == ()


# ---------------------------------------------------------------------------
# align_source_weeks — pemetaan yang sah tidak disentuh
# ---------------------------------------------------------------------------
def test_a_valid_mapping_is_kept_exactly_as_written() -> None:
    course = make_course()
    spec = make_spec(("Minggu 1-3",), ("Minggu 4-6",))

    fixed, notes = align_source_weeks(spec, course)

    assert notes == ()
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_grouping_by_a_split_list_is_just_as_valid() -> None:
    """Perencana boleh menulis satu bab sebagai daftar minggu yang terputus."""
    course = make_course(4, last=6)  # minggu 4 adalah ujian
    spec = make_spec(("Minggu 1-3",), ("Minggu 5", "Minggu 6"))

    fixed, notes = align_source_weeks(spec, course)

    assert notes == ()
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 5", "Minggu 6"))


def test_a_single_chapter_taking_every_week_is_valid() -> None:
    """RPS dua minggu untuk satu bab: tidak ada yang hilang, tidak ada yang kembar."""
    course = make_course(last=2)
    spec = make_spec(("Minggu 1-2",))

    fixed, notes = align_source_weeks(spec, course)

    assert notes == ()
    assert labels(fixed) == (("Minggu 1-2",),)


# ---------------------------------------------------------------------------
# align_source_weeks — pemetaan yang cacat diperbaiki, dengan alasannya
# ---------------------------------------------------------------------------
def test_a_teaching_week_left_out_is_reported_and_the_weeks_are_redivided() -> None:
    course = make_course()
    spec = make_spec(("Minggu 1-2",), ("Minggu 3-4",))

    fixed, notes = align_source_weeks(spec, course)

    assert any("tidak dipakai bab mana pun: 5, 6" in note for note in notes)
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_a_week_used_by_two_chapters_is_reported() -> None:
    """Dua bab yang saling menimpa minggu berarti materi yang ditulis dua kali."""
    course = make_course()
    spec = make_spec(("Minggu 1-3",), ("Minggu 3-6",))

    fixed, notes = align_source_weeks(spec, course)

    assert any("dipakai lebih dari satu bab: 3" in note for note in notes)
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_an_exam_week_is_reported_as_an_exam_week_not_as_a_missing_one() -> None:
    """Minggu 4 ada di RPS — ia minggu ujian, dan itulah masalahnya.

    Melaporkannya sebagai "minggu yang tidak ada" akan menyesatkan orang yang
    membuka RPS untuk memeriksanya: ia akan mencari minggu yang memang ada di sana.
    """
    course = make_course(4)
    spec = make_spec(("Minggu 1-4",), ("Minggu 5-6",))

    fixed, notes = align_source_weeks(spec, course)

    assert any("minggu penilaian dijadikan bahan bab: 4" in note for note in notes)
    assert not any("tidak ada di RPS" in note for note in notes)
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 5-6",))


def test_a_week_that_the_rps_does_not_have_at_all_is_reported() -> None:
    course = make_course()
    spec = make_spec(("Minggu 1-2",), ("Minggu 9",))

    _, notes = align_source_weeks(spec, course)

    assert any("tidak ada di RPS: 9" in note for note in notes)


def test_a_mapping_out_of_order_is_reported() -> None:
    """Bab pertama yang mencakup minggu terakhir membalik urutan buku."""
    course = make_course()
    spec = make_spec(("Minggu 4-6",), ("Minggu 1-3",))

    fixed, notes = align_source_weeks(spec, course)

    assert any("tidak berurutan" in note for note in notes)
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_an_unreadable_label_is_reported_and_the_whole_mapping_replaced() -> None:
    course = make_course()
    spec = make_spec(("Minggu pertama",), ("Minggu 2-6",))

    fixed, notes = align_source_weeks(spec, course)

    assert any("label minggu tidak dapat dibaca" in note for note in notes)
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_the_redivision_is_named_as_a_repair() -> None:
    """Perbaikan disebut sebagai perbaikan, bukan disamarkan sebagai rencana."""
    course = make_course()
    spec = make_spec(("Minggu 1-2",), ("Minggu 3-4",))

    _, notes = align_source_weeks(spec, course)

    assert any(
        "dibagi ulang secara merata" in note and "Minggu 1-3, Minggu 4-6" in note
        for note in notes
    )


def test_a_chapter_without_weeks_when_weeks_run_out_gets_an_empty_mapping() -> None:
    """Lebih bab daripada minggu: bab yang tidak kebagian tampil kosong, bukan
    diberi minggu karangan atau minggu yang sudah dipakai bab lain."""
    course = make_course(last=2)
    spec = make_spec(("Minggu 1-2",), ("Minggu 3",), ("Minggu 4",))

    fixed, notes = align_source_weeks(spec, course)

    assert any("tidak ada di RPS: 3, 4" in note for note in notes)
    assert labels(fixed) == (("Minggu 1",), ("Minggu 2",), ())
    assert any("(kosong)" in note for note in notes)


# ---------------------------------------------------------------------------
# align_source_weeks — keadaan yang tidak dapat diperiksa
# ---------------------------------------------------------------------------
def test_without_chapters_there_is_nothing_to_align() -> None:
    course = make_course()
    spec = BookSpec(title="Buku Uji", chapters=())

    fixed, notes = align_source_weeks(spec, course)

    assert fixed is spec
    assert notes == ()


def test_without_teaching_weeks_there_is_nothing_to_align_to() -> None:
    """RPS yang seluruh minggunya ujian tidak memberi satu pun minggu untuk bab."""
    course = make_course(1, 2, 3, 4, 5, 6)
    spec = make_spec(("Minggu 1",), ("Minggu 2",))

    fixed, notes = align_source_weeks(spec, course)

    assert fixed is spec
    assert notes == ()


# ---------------------------------------------------------------------------
# align_source_weeks — kemurnian
# ---------------------------------------------------------------------------
def test_aligning_leaves_the_input_spec_and_course_untouched() -> None:
    course = make_course()
    spec = make_spec(("Minggu 1-2",), ("Minggu 3-4",))
    spec_snapshot = spec.model_dump()
    course_snapshot = course.model_dump()

    align_source_weeks(spec, course)

    assert spec.model_dump() == spec_snapshot
    assert course.model_dump() == course_snapshot


# ---------------------------------------------------------------------------
# Lewat reconcile_book_spec — jalur yang benar-benar dipakai BookPlanner
# ---------------------------------------------------------------------------
def test_reconcile_passes_a_valid_mapping_through() -> None:
    """Perencana yang benar tidak menghasilkan satu pun catatan tentang minggu."""
    course = make_course()
    spec = make_spec(("Minggu 1-3",), ("Minggu 4-6",))

    fixed, notes = reconcile_book_spec(spec, target_chapters=2, course=course)

    assert notes == ()
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_reconcile_reports_the_week_repair_alongside_its_own_notes() -> None:
    """Catatan dari rekonsiliasi dan dari pemetaan minggu muncul bersama."""
    course = make_course()
    spec = BookSpec(
        title="Buku Uji",
        chapters=(
            ChapterSpec(number=5, title="Bab A", source_weeks=("Minggu 1-2",)),
            ChapterSpec(number=9, title="Bab B", source_weeks=("Minggu 3-4",)),
        ),
    )

    fixed, notes = reconcile_book_spec(spec, target_chapters=2, course=course)

    assert any("berurutan" in note for note in notes), "penomoran ulang tetap dilaporkan"
    assert any("tidak dipakai bab mana pun" in note for note in notes)
    assert labels(fixed) == (("Minggu 1-3",), ("Minggu 4-6",))


def test_reconcile_without_a_course_does_not_touch_the_weeks() -> None:
    """RPS yang tidak dapat diuraikan berarti tidak ada yang dapat dipakai untuk
    memeriksa — dan pemetaannya dibiarkan apa adanya, bukan ditebak."""
    spec = make_spec(("Minggu 1-2",), ("Minggu 3-4",))

    fixed, notes = reconcile_book_spec(spec, target_chapters=2)

    assert labels(fixed) == (("Minggu 1-2",), ("Minggu 3-4",))
    assert not any("minggu" in note for note in notes)
