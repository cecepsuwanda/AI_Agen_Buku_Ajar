"""Rendering Markdown — MURNI, jadi dapat diuji habis-habisan di sini.

Markdown adalah **turunan** dari ``ChapterRecord``. Yang membuat itu bukan sekadar
catatan arsitektur adalah kesempatan yang dibukanya: bab yang sudah ``APPROVED``
tetapi kehilangan berkas ``.md``-nya cukup dirender ulang, tanpa satu pun panggilan
model. Karena itu hasil render diuji sampai ke spasi barisnya.
"""

from __future__ import annotations

import pytest

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, Section
from domain.rendering import (
    chapter_filename,
    render_book_markdown,
    render_chapter_markdown,
)


def _draft(**overrides: object) -> ChapterDraft:
    """Draf wajar; setiap tes menimpa hanya field yang sedang diuji."""
    base: dict[str, object] = {
        "title": "Struktur Data",
        "learning_objectives": ("Memahami larik",),
        "sections": (Section(heading="Pengantar", body="Isi pengantar."),),
    }
    base.update(overrides)
    return ChapterDraft.model_validate(base)


# ---------------------------------------------------------------------------
# Judul dan tujuan
# ---------------------------------------------------------------------------
def test_the_title_carries_the_chapter_number() -> None:
    """Nomor bab datang dari pemanggil, bukan dari draf — draf tidak tahu ia bab keberapa."""
    markdown = render_chapter_markdown(_draft(), number=3)

    assert markdown.startswith("# Bab 3. Struktur Data\n")


def test_a_title_that_repeats_the_number_is_not_doubled() -> None:
    """Penulis menyalin header prompt ke dalam judul; nomor itu milik perender."""
    markdown = render_chapter_markdown(_draft(title="Bab 1: Struktur Data"), number=1)

    assert markdown.startswith("# Bab 1. Struktur Data\n")


@pytest.mark.parametrize(
    "title",
    [
        "Bab 1: Struktur Data",
        "Bab 1 - Struktur Data",
        "Bab 1. Struktur Data",
        "BAB 1 — Struktur Data",
        "bab 1 Struktur Data",
        "Bab 7: Struktur Data",  # nomor keliru pun dibersihkan
    ],
)
def test_every_style_of_chapter_prefix_is_stripped(title: str) -> None:
    """Gaya penulisan awalan berbeda-beda antar-model; yang dibersihkan adalah polanya."""
    markdown = render_chapter_markdown(_draft(title=title), number=1)

    assert markdown.startswith("# Bab 1. Struktur Data\n")


def test_a_title_without_a_prefix_is_left_alone() -> None:
    """Judul yang sudah benar tidak boleh tersentuh — tidak ada yang perlu dibuang."""
    markdown = render_chapter_markdown(_draft(title="Bab dan Buku"), number=2)

    assert markdown.startswith("# Bab 2. Bab dan Buku\n")


def test_a_title_that_is_only_a_prefix_is_kept() -> None:
    """Kehilangan judul lebih buruk daripada judul yang berlebih."""
    markdown = render_chapter_markdown(_draft(title="Bab 3"), number=3)

    assert markdown.startswith("# Bab 3. Bab 3\n")


def test_learning_objectives_are_rendered_as_a_list() -> None:
    draft = _draft(learning_objectives=("Memahami larik", "Membandingkan senarai"))

    markdown = render_chapter_markdown(draft, number=1)

    assert "## Tujuan Pembelajaran" in markdown
    assert "- Memahami larik" in markdown
    assert "- Membandingkan senarai" in markdown


def test_a_chapter_without_objectives_omits_the_heading() -> None:
    """Judul bagian kosong lebih buruk daripada tidak ada bagian sama sekali."""
    markdown = render_chapter_markdown(_draft(learning_objectives=()), number=1)

    assert "Tujuan Pembelajaran" not in markdown


# ---------------------------------------------------------------------------
# Sub-bab
# ---------------------------------------------------------------------------
def test_section_level_becomes_the_markdown_heading_depth() -> None:
    draft = _draft(
        sections=(
            Section(heading="Dua", body="isi", level=2),
            Section(heading="Tiga", body="isi", level=3),
        )
    )

    markdown = render_chapter_markdown(draft, number=1)

    assert "\n## Dua\n" in markdown
    assert "\n### Tiga\n" in markdown


def test_levels_outside_the_renderable_range_are_clamped() -> None:
    """Level 1 akan bertabrakan dengan judul bab; level 6 terlalu dalam untuk dibaca.

    Model dibiarkan meminta 1..6 (``Section.level``), tetapi perender yang
    memutuskan apa yang masuk akal di dalam sebuah bab.
    """
    draft = _draft(
        sections=(
            Section(heading="Terlalu tinggi", body="isi", level=1),
            Section(heading="Terlalu dalam", body="isi", level=6),
        )
    )

    markdown = render_chapter_markdown(draft, number=1)

    assert "\n## Terlalu tinggi\n" in markdown
    assert "\n##### Terlalu dalam\n" in markdown
    assert "\n# Terlalu tinggi\n" not in markdown


def test_a_section_without_a_body_is_rendered_as_a_bare_heading() -> None:
    """Sub-bab yang hanya berisi judul tetap berarti; jangan diisi spasi kosong.

    Yang diuji adalah **ketiadaan** isi: tidak ada baris kosong tambahan, tidak ada
    placeholder, dan heading berikutnya langsung menyusul.
    """
    draft = _draft(
        sections=(
            Section(heading="Kosong", body="   "),
            Section(heading="Berisi", body="Isi."),
        )
    )

    markdown = render_chapter_markdown(draft, number=1)

    assert "\n## Kosong\n\n## Berisi\n" in markdown
    assert "\n## Kosong\n   \n" not in markdown, "spasi kosong ikut tersalin"


def test_section_bodies_are_stripped() -> None:
    draft = _draft(sections=(Section(heading="A", body="\n\n  isi  \n\n"),))

    assert "\nisi\n" in render_chapter_markdown(draft, number=1)


# ---------------------------------------------------------------------------
# Bagian opsional
# ---------------------------------------------------------------------------
def test_examples_are_numbered_with_the_chapter_number() -> None:
    """``Contoh 2.1`` — nomor bab ikut, karena berkas bab dibaca berdampingan."""
    draft = _draft(examples=("Contoh pertama.", "Contoh kedua."))

    markdown = render_chapter_markdown(draft, number=2)

    assert "**Contoh 2.1**" in markdown
    assert "**Contoh 2.2**" in markdown


def test_exercises_are_numbered_from_one() -> None:
    draft = _draft(exercises=("Latihan pertama", "Latihan kedua"))

    markdown = render_chapter_markdown(draft, number=5)

    assert "\n1. Latihan pertama\n" in markdown
    assert "\n2. Latihan kedua\n" in markdown


def test_citations_become_a_reference_list() -> None:
    draft = _draft(citations=("Cormen, Introduction to Algorithms.",))

    markdown = render_chapter_markdown(draft, number=1)

    assert "## Rujukan" in markdown
    assert "- Cormen, Introduction to Algorithms." in markdown


def test_unresolved_claims_are_rendered_as_a_visible_warning() -> None:
    """§34: klaim tanpa bukti **ditandai**, bukan disembunyikan.

    Pada MVP ini (tanpa RAG) blok ini akan hampir selalu terisi. Itu memang benar:
    lebih baik terlihat daripada menyamar sebagai fakta yang terdokumentasi.
    """
    draft = _draft(unresolved_claims=("Kompleksitas quicksort rata-rata O(n log n).",))

    markdown = render_chapter_markdown(draft, number=1)

    assert "> **Catatan verifikasi**" in markdown
    assert "> - Kompleksitas quicksort rata-rata O(n log n)." in markdown


def test_chapter_references_come_from_the_spec_not_the_draft() -> None:
    """Referensi bab milik perencana; draf hanya mengutip apa yang benar-benar dipakai."""
    spec = ChapterSpec(
        number=1,
        title="Struktur Data",
        references=("Sedgewick, Algorithms, 4th ed.",),
    )

    markdown = render_chapter_markdown(_draft(), number=1, spec=spec)

    assert "## Referensi Bab" in markdown
    assert "- Sedgewick, Algorithms, 4th ed." in markdown


def test_a_spec_without_references_adds_nothing() -> None:
    spec = ChapterSpec(number=1, title="Struktur Data")

    assert "Referensi Bab" not in render_chapter_markdown(_draft(), number=1, spec=spec)


def test_the_draft_summary_is_deliberately_not_rendered() -> None:
    """``summary`` adalah metadata pipeline, bukan isi yang dibaca mahasiswa.

    Ia dipakai ``BookDirector`` untuk memberi konteks bab-bab berikutnya. Menampakannya
    di berkas bab akan menduplikasi isi bab dalam bentuk yang lebih buruk. Perilaku
    ini dikunci di sini supaya menjadi **keputusan**, bukan kebetulan.
    """
    draft = _draft(summary="Bab ini membahas larik dan senarai.")

    assert "Bab ini membahas larik dan senarai." not in render_chapter_markdown(draft, number=1)


# ---------------------------------------------------------------------------
# Bentuk keluaran
# ---------------------------------------------------------------------------
def test_the_output_ends_with_exactly_one_newline() -> None:
    """Satu baris baru di akhir: bukan nol (POSIX), dan bukan dua (diff berisik)."""
    markdown = render_chapter_markdown(_draft(), number=1)

    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


def test_rendering_is_deterministic() -> None:
    """Murni: masukan yang sama menghasilkan keluaran yang sama, byte per byte."""
    draft = _draft()

    assert render_chapter_markdown(draft, number=1) == render_chapter_markdown(draft, number=1)


# ---------------------------------------------------------------------------
# Buku gabungan
# ---------------------------------------------------------------------------
def test_the_book_is_ordered_by_chapter_number() -> None:
    """Urutan ditentukan pemanggil lewat nomor, bukan oleh urutan kedatangan."""
    markdown = render_book_markdown(
        [(3, "# Bab 3\n"), (1, "# Bab 1\n"), (2, "# Bab 2\n")],
        title="Buku Uji",
    )

    assert markdown.index("# Bab 1") < markdown.index("# Bab 2") < markdown.index("# Bab 3")


def test_the_book_starts_with_its_title_and_separates_chapters() -> None:
    markdown = render_book_markdown([(1, "# Bab 1\n"), (2, "# Bab 2\n")], title="Buku Uji")

    assert markdown.startswith("# Buku Uji\n")
    assert markdown.count("\n---\n") == 2


def test_an_empty_book_is_still_a_valid_document() -> None:
    """Nol bab bukan galat di sini; ``export`` yang memutuskan itu layak ditulis."""
    assert render_book_markdown([], title="Buku Kosong") == "# Buku Kosong\n"


# ---------------------------------------------------------------------------
# Nama berkas
# ---------------------------------------------------------------------------
def test_filenames_are_zero_padded_so_they_sort_correctly() -> None:
    """``chapter01.md`` mengurutkan sama sebagai teks maupun sebagai bab."""
    assert chapter_filename(1) == "chapter01.md"
    assert chapter_filename(9) == "chapter09.md"
    assert chapter_filename(10) == "chapter10.md"


def test_numbering_does_not_truncate_beyond_ninety_nine() -> None:
    """Padding tidak boleh memotong nomor; §15 mengizinkan sampai 60 bab."""
    assert chapter_filename(100) == "chapter100.md"
