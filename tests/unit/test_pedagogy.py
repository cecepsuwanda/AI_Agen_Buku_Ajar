"""Tes lapis murni §23 — tangga penyajian, dan anak tangga yang terbukti absen.

Yang diuji di sini adalah pertanyaan yang **tidak boleh** sampai ke model:
apakah anak tangganya ada. Empat dari enam kelemahan yang §23 minta dideteksi
hanya dapat dijawab pada bab yang tangganya lengkap, jadi jawaban atas
pertanyaan itu menentukan apakah penilaian model berikutnya bermakna atau
sekadar terdengar seperti penilaian.

Berkas ini sengaja tidak memuat satu pun ``ChatModel``: seluruh fungsinya murni,
dan itulah yang membuatnya dapat diuji habis-habisan tanpa satu token.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, Section
from domain.pedagogy import LADDER, missing_rungs, repeated_headings

EXPLANATION = "Pencarian linear memeriksa tiap elemen satu per satu."

SPEC = ChapterSpec(
    number=1,
    title="Algoritma Pencarian",
    objectives=("Mahasiswa mampu menjelaskan pencarian linear.",),
    sections=("Pencarian linear",),
    required_examples=3,
    required_exercises=5,
)

#: Draf yang tangganya utuh. Setiap tes di bawah merusak **satu** hal darinya,
#: sehingga temuan yang muncul dapat dipastikan berasal dari kerusakan itu.
COMPLETE = ChapterDraft(
    title="Algoritma Pencarian",
    learning_objectives=("Mahasiswa mampu menjelaskan pencarian linear.",),
    sections=(Section(heading="Pencarian linear", body=EXPLANATION),),
    examples=("**Contoh 1.1**\n\n```python\nfor x in data: pass\n```",),
    exercises=("Cari 7 pada larik [3, 7, 9].",),
)


def damaged(**fields: object) -> ChapterDraft:
    """Draf lengkap dengan satu bagian dirusak."""
    return COMPLETE.model_copy(update=fields)


def subjects(*, spec: ChapterSpec = SPEC, draft: ChapterDraft = COMPLETE) -> tuple[str, ...]:
    """Subjek anak tangga yang terbukti absen."""
    return tuple(finding.subject for finding in missing_rungs(draft, spec=spec))


# ---------------------------------------------------------------------------
# 1. Tangga §23 itu sendiri
# ---------------------------------------------------------------------------
def test_the_ladder_is_the_five_rungs_of_section_23_in_order() -> None:
    """Tangga ini adalah pengait yang dipakai prompt dan ``unexamined``.

    ``subject`` temuan yang diminta kepada model disalin dari sini, dan
    ``unexamined`` mencocokkannya kembali dengan daftar yang sama. Mengubah
    urutannya atau menerjemahkan satu nama akan memutus kait itu tanpa satu pun
    tanda di tempat lain.
    """
    assert LADDER == (
        "Learning Objective",
        "Explanation",
        "Example",
        "Practice",
        "Exercise",
    )


# ---------------------------------------------------------------------------
# 2. Draf yang tangganya utuh
# ---------------------------------------------------------------------------
def test_a_complete_draft_has_no_missing_rung() -> None:
    """Tidak ada temuan berarti penilaian boleh dilanjutkan ke model."""
    assert missing_rungs(COMPLETE, spec=SPEC) == ()


def test_a_thin_explanation_is_not_a_missing_one() -> None:
    """Yang ditanyakan di sini "ada atau tidak", bukan "bagus atau tidak".

    Penjelasan satu kalimat yang melompat terlalu cepat adalah temuan §23 yang
    sesungguhnya — dan tempatnya bukan di sini. Menilainya secara mekanis akan
    menggantikan penilaian model dengan panjang teks.
    """
    thin = damaged(sections=(Section(heading="Pencarian linear", body="Lihat buku."),))

    assert missing_rungs(thin, spec=SPEC) == ()


# ---------------------------------------------------------------------------
# 3. Anak tangga yang hilang
# ---------------------------------------------------------------------------
def test_a_chapter_without_objectives_cannot_be_assessed_at_all() -> None:
    """Tanpa tujuan, "latihan tidak sesuai tujuan" tidak punya jawaban."""
    assert subjects(draft=damaged(learning_objectives=())) == ("Learning Objective",)


def test_whitespace_is_not_an_objective() -> None:
    """Tujuan berisi spasi tetap tujuan yang tidak ada."""
    assert subjects(draft=damaged(learning_objectives=("   ",))) == ("Learning Objective",)


def test_sections_without_a_body_are_no_explanation() -> None:
    """Judul tanpa isi bukan penjelasan; tangganya berhenti di anak tangga kedua."""
    bare = damaged(sections=(Section(heading="Pencarian linear", body="  "),))

    assert subjects(draft=bare) == ("Explanation",)


def test_an_empty_section_list_is_reported_once_not_twice() -> None:
    """Dua anak tangga rusak tidak boleh muncul sebagai dua temuan yang sama.

    Daftar sub-bab yang kosong adalah bentuk paling parah dari "tidak ada
    penjelasan" — bukan kelemahan kedua di sampingnya.
    """
    assert subjects(draft=damaged(sections=())) == ("Explanation",)


def test_examples_asked_for_and_absent_are_a_gap() -> None:
    assert subjects(draft=damaged(examples=())) == ("Example",)


def test_exercises_asked_for_and_absent_are_a_gap() -> None:
    assert subjects(draft=damaged(exercises=())) == ("Exercise",)


def test_a_chapter_that_asks_for_no_example_is_not_missing_them() -> None:
    """``required_examples=0`` berarti babnya memang tidak meminta contoh.

    Bab teori atau matematika dapat tidak punya contoh dan tetap menjadi bab yang
    benar; menghukumnya di sini berarti menolak bab yang spesifikasi babnya
    sendiri tidak menuntut apa pun.
    """
    no_examples = SPEC.model_copy(update={"required_examples": 0})
    without_examples = damaged(examples=())

    assert missing_rungs(without_examples, spec=no_examples) == ()


def test_a_chapter_that_asks_for_no_exercise_is_not_missing_them() -> None:
    """Sama untuk latihan: batas ``ge=0`` di spesifikasi bukan hiasan."""
    no_exercises = SPEC.model_copy(update={"required_exercises": 0})
    without_exercises = damaged(exercises=())

    assert missing_rungs(without_exercises, spec=no_exercises) == ()


def test_the_missing_rungs_come_back_in_ladder_order() -> None:
    """Urutan temuan mengikuti urutan tangga, bukan urutan pemeriksaan."""
    empty = ChapterDraft(title="Kosong")

    assert subjects(draft=empty) == (
        "Learning Objective",
        "Explanation",
        "Example",
        "Exercise",
    )


def test_the_gap_says_what_the_specification_asked_for() -> None:
    """Temuan menyebut angka yang diminta, bukan sekadar "contohnya kurang"."""
    finding, = missing_rungs(damaged(examples=()), spec=SPEC)

    assert "3 contoh" in finding.detail
    assert finding.ok is False


# ---------------------------------------------------------------------------
# 4. Pengulangan yang tidak diperlukan — kelemahan keenam §23
# ---------------------------------------------------------------------------
def test_the_same_heading_twice_is_reported_by_its_heading() -> None:
    """Subjeknya judul, bukan kalimat: itulah yang dapat dicari penulis."""
    twice = damaged(
        sections=(
            Section(heading="Pencarian linear", body=EXPLANATION),
            Section(heading="Pencarian linear", body="Materi lain."),
        )
    )

    assert repeated_headings(twice) == ("Pencarian linear",)
    assert subjects(draft=twice) == ("Pencarian linear",)


def test_heading_matching_ignores_case_and_trailing_space() -> None:
    """``"Notasi Big-O"`` dan ``"notasi big-o "`` adalah sub-bab yang sama bagi pembaca."""
    twice = damaged(
        sections=(
            Section(heading="Notasi Big-O", body="A."),
            Section(heading="notasi big-o ", body="B."),
        )
    )

    assert repeated_headings(twice) == ("notasi big-o",)


def test_two_sections_with_identical_bodies_are_reported() -> None:
    twin = damaged(
        sections=(
            Section(heading="Pencarian linear", body=EXPLANATION),
            Section(heading="Pencarian biner", body=EXPLANATION),
        )
    )

    findings = missing_rungs(twin, spec=SPEC)

    assert len(findings) == 1
    assert findings[0].subject == "Pencarian biner"
    assert "identik" in findings[0].detail


def test_every_repeating_section_is_reported_separately() -> None:
    """Yang dilaporkan adalah tiap sub-bab yang mengulang — bukan setiap pasangannya.

    Tiga sub-bab kembar menghasilkan dua temuan, bukan tiga dan bukan satu:
    sub-bab pertama adalah aslinya, dan dua sisanya masing-masing punya judul
    yang harus dicari penulis di dalam drafnya.
    """
    triple = damaged(
        sections=(
            Section(heading="Satu", body=EXPLANATION),
            Section(heading="Dua", body=EXPLANATION),
            Section(heading="Tiga", body=EXPLANATION),
        )
    )

    assert len(missing_rungs(triple, spec=SPEC)) == 2


def test_a_repeated_paragraph_inside_one_section_is_not_reported() -> None:
    """Paragraf yang diulang di dalam satu sub-bab seringkali disengaja penulisnya.

    Penegasan dan rangkuman adalah bentuk pengulangan yang sah; yang tidak punya
    pembacaan lain adalah sub-bab yang **seluruh** isinya kembar.
    """
    echoing = damaged(
        sections=(Section(heading="Notasi", body=f"{EXPLANATION}\n\n{EXPLANATION}"),)
    )

    assert missing_rungs(echoing, spec=SPEC) == ()


def test_empty_bodies_are_not_repetition() -> None:
    """Dua sub-bab kosong adalah anak tangga yang hilang, bukan pengulangan.

    Melaporkannya sebagai keduanya akan menagih penulis dua kali untuk satu
    pekerjaan — dan temuan pengulangan yang menunjuk sub-bab kosong tidak dapat
    dikerjakan siapa pun.
    """
    empty = damaged(
        sections=(
            Section(heading="Satu", body=""),
            Section(heading="Dua", body="   "),
        )
    )

    assert subjects(draft=empty) == ("Explanation",)
