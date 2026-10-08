"""Sumber LaTeX — MURNI, jadi diuji habis-habisan di sini (§25).

Isi berkas ini adalah pasangan dari ``tests/unit/test_rendering.py``, dan
alasannya sama persis: ``.tex`` adalah **turunan** dari ``ChapterRecord``.
Bab yang sudah ``LATEX_GENERATED`` tetapi kehilangan berkas ``.tex``-nya cukup
dirender ulang, tanpa satu pun panggilan model. Karena itu hasil rendernya diuji
sampai ke spasi barisnya — dan ``output/latex/`` di-track git (§39), sehingga
setiap perbedaan byte adalah diff yang harus dibaca manusia.

Dua hal di luar rendering juga dikunci di sini, dan keduanya adalah kopling yang
tidak dapat ditegakkan pemeriksa tipe:

* **Nama berkas daftar pustaka** harus sama di ``latex/artifacts.py`` dan di
  ``\\bibliography{references}`` milik ``main.tex``. BibTeX tidak memberi tahu
  siapa pun ketika keduanya berbeda; ia hanya gagal menemukan entri.
* **Nama berkas bab** harus sama di ``domain/latex.py`` dan di baris
  ``\\include{chapters/chapterNN}``. Ketidakcocokan di sini menghasilkan PDF
  yang kehilangan seluruh isi buku tanpa satu pun galat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.book import ChapterSpec
from domain.latex import (
    LatexChapter,
    bibliography_entries,
    chapter_latex_filename,
    citation_key,
    escape_latex,
    extract_cite_keys,
    extract_labels,
    inspect_latex,
    lock_chapter_number,
    render_bibliography,
    render_chapter_latex,
    strip_tex_comments,
    unbalanced_environments,
)
from latex.artifacts import BIBLIOGRAPHY_FILENAME, templates_dir

CORMEN = "Cormen, Introduction to Algorithms, 4th ed."
SEDGEWICK = "Sedgewick, Algorithms, 4th ed."


def _spec(**overrides: object) -> ChapterSpec:
    """Spesifikasi bab wajar; setiap tes menimpa hanya field yang sedang diuji."""
    base: dict[str, object] = {
        "number": 2,
        "title": "Analisis Kompleksitas",
        "objectives": ("Mahasiswa mampu menghitung kompleksitas waktu",),
    }
    base.update(overrides)
    return ChapterSpec.model_validate(base)


def _chapter(**overrides: object) -> LatexChapter:
    """Potongan LaTeX wajar — bentuk yang diharapkan datang dari model."""
    base: dict[str, object] = {
        "number": 2,
        "title": "Analisis Kompleksitas",
        "body_tex": (
            "\\section{Notasi Big-O}\n"
            "Waktu eksekusi tumbuh linear \\cite{cormen-1a2b3c4d}.\n"
            "\\label{sec:bigo}\n"
        ),
    }
    base.update(overrides)
    return LatexChapter.model_validate(base)


# ---------------------------------------------------------------------------
# escape_latex — satu lintasan, bukan rangkaian replace
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("&", r"\&"),
        ("%", r"\%"),
        ("_", r"\_"),
        ("#", r"\#"),
        ("$", r"\$"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("^", r"\textasciicircum{}"),
        ("~", r"\textasciitilde{}"),
        ("\\", r"\textbackslash{}"),
    ],
)
def test_every_special_character_is_escaped(raw: str, expected: str) -> None:
    assert escape_latex(raw) == expected


def test_a_backslash_is_not_escaped_a_second_time() -> None:
    """Ini alasan pelolosan ditulis satu lintasan, bukan ``str.replace`` berurutan.

    Penggantian berurutan akan mengenali ``{`` dan ``}`` di dalam
    ``\\textbackslash{}`` sebagai karakter yang baru saja dibuatnya sendiri dan
    meloloskannya lagi — mencetak omong kosong di PDF.
    """
    assert escape_latex("\\") == r"\textbackslash{}"
    assert r"\textbackslash\{\}" not in escape_latex("\\")


def test_ordinary_text_passes_through_untouched() -> None:
    assert escape_latex("Algoritma dan Struktur Data") == "Algoritma dan Struktur Data"


def test_escaping_handles_several_specials_in_one_pass() -> None:
    assert escape_latex("100% & 50_50") == r"100\% \& 50\_50"


def test_the_empty_string_stays_empty() -> None:
    assert escape_latex("") == ""


# ---------------------------------------------------------------------------
# citation_key — stabil, unik, dan hanya ASCII
# ---------------------------------------------------------------------------
def test_the_same_source_always_yields_the_same_key() -> None:
    """Stabil lintas pemanggilan — ``output/latex/references.bib`` di-track git (§39)."""
    assert citation_key(CORMEN) == citation_key(CORMEN)


def test_different_sources_yield_different_keys() -> None:
    assert citation_key(CORMEN) != citation_key(SEDGEWICK)


def test_the_key_is_ascii_only() -> None:
    """``bibtex`` klasik gagal membaca kunci non-ASCII, dan pesannya menyesatkan."""
    key = citation_key("Buku Referensi: Algoritma & Struktur Data — edisi ke-3")

    assert key.isascii()
    assert set(key) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


def test_a_source_without_any_safe_character_falls_back_to_a_usable_slug() -> None:
    """Sumber berbahasa non-Latin tetap mendapat kunci yang sah bagi BibTeX."""
    key = citation_key("算法导论")

    assert key.startswith("sumber-")
    assert not key.startswith("-")


def test_the_key_ends_with_an_eight_hex_digit_fingerprint() -> None:
    """Sidik jari inilah yang membedakan dua sumber berslug sama."""
    key = citation_key(CORMEN)
    fingerprint = key.rsplit("-", 1)[1]

    assert len(fingerprint) == 8
    assert all(char in "0123456789abcdef" for char in fingerprint)


def test_two_sources_sharing_a_long_prefix_still_get_distinct_keys() -> None:
    """Dua edisi buku berjudul sama tidak boleh bertabrakan di slug 40 karakter."""
    first = citation_key("Introduction to Algorithms, Fourth Edition, MIT Press")
    second = citation_key("Introduction to Algorithms, Fourth Edition, Pearson")

    assert first != second


def test_the_slug_is_capped_so_keys_stay_readable() -> None:
    long_source = "Judul yang sangat panjang " * 8

    slug = citation_key(long_source).rsplit("-", 1)[0]

    assert len(slug) <= 40
    assert not slug.endswith("-")


# ---------------------------------------------------------------------------
# bibliography_entries — dedupe dan urutan deterministik
# ---------------------------------------------------------------------------
def test_entries_are_pairs_of_key_and_source() -> None:
    ((key, source),) = bibliography_entries([CORMEN])

    assert source == CORMEN
    assert key == citation_key(CORMEN)


def test_duplicate_sources_produce_exactly_one_entry() -> None:
    assert len(bibliography_entries([CORMEN, CORMEN])) == 1


def test_the_same_source_written_differently_is_a_single_entry() -> None:
    """Perbedaan spasi di tepi nama adalah perbedaan penulisan, bukan sumber lain."""
    assert len(bibliography_entries([CORMEN, f"  {CORMEN}  "])) == 1


def test_blank_sources_are_dropped() -> None:
    assert bibliography_entries(["", "   "]) == ()


def test_entries_are_sorted_so_the_file_does_not_churn() -> None:
    first = bibliography_entries([SEDGEWICK, CORMEN])
    second = bibliography_entries([CORMEN, SEDGEWICK])

    assert first == second
    assert [key for key, _ in first] == sorted(key for key, _ in first)


def test_an_empty_source_list_yields_no_entries() -> None:
    assert bibliography_entries([]) == ()


# ---------------------------------------------------------------------------
# strip_tex_comments
# ---------------------------------------------------------------------------
def test_a_percent_starts_a_comment_that_runs_to_the_end_of_the_line() -> None:
    assert strip_tex_comments("teks % ini komentar\nbaris berikutnya") == (
        "teks \nbaris berikutnya"
    )


def test_an_escaped_percent_is_kept() -> None:
    """``\\%`` adalah persen yang sesungguhnya, bukan awal komentar."""
    assert strip_tex_comments(r"naik 50\% dari basis") == r"naik 50\% dari basis"


def test_a_commented_command_is_not_visible_to_the_scanner() -> None:
    assert strip_tex_comments(r"% \cite{cormen}") == ""


def test_a_backslash_protects_the_character_after_it() -> None:
    """Karakter tepat sesudah ``\\`` diloloskan, apa pun isinya."""
    assert strip_tex_comments("a\\%b") == "a\\%b"


# ---------------------------------------------------------------------------
# extract_cite_keys / extract_labels
# ---------------------------------------------------------------------------
def test_a_comma_separated_cite_counts_as_several_keys() -> None:
    assert extract_cite_keys(r"\cite{a,b}") == ("a", "b")


def test_a_key_cited_twice_is_reported_once() -> None:
    """Yang diperiksa adalah himpunan rujukan, bukan berapa kali disebut."""
    assert extract_cite_keys(r"\cite{a} lalu \cite{a}") == ("a",)


def test_the_keys_keep_their_order_of_appearance() -> None:
    assert extract_cite_keys(r"\cite{b} \cite{a}") == ("b", "a")


def test_a_starred_or_prefixed_cite_command_is_recognised() -> None:
    """``\\citep``/``\\citet`` memakai kunci yang sama; ``\\cite*`` juga."""
    assert extract_cite_keys(r"\citep{a} \citet{b} \cite*{c}") == ("a", "b", "c")


def test_an_optional_argument_is_skipped() -> None:
    assert extract_cite_keys(r"\cite[p.~5]{a}") == ("a",)


def test_a_commented_cite_is_not_counted() -> None:
    """Sitasi yang dikomentari tidak pernah sampai ke BibTeX."""
    assert extract_cite_keys("% \\cite{a}") == ()


def test_a_cite_with_no_key_is_ignored() -> None:
    assert extract_cite_keys(r"\cite{}") == ()


def test_endinput_is_not_mistaken_for_an_environment_end() -> None:
    """``\\end`` di dalam ``\\endinput`` bukan akhir environment."""
    assert extract_labels(r"\endinput") == ()
    assert unbalanced_environments(r"\endinput") == ()


def test_labels_are_extracted_in_order() -> None:
    assert extract_labels(r"\label{sec:a} \label{sec:b}") == ("sec:a", "sec:b")


# ---------------------------------------------------------------------------
# unbalanced_environments
# ---------------------------------------------------------------------------
def test_balanced_environments_report_nothing() -> None:
    tex = "\\begin{example}\nisi\n\\end{example}"

    assert unbalanced_environments(tex) == ()


def test_a_missing_end_is_reported() -> None:
    assert unbalanced_environments("\\begin{example}\nisi") == ("example",)


def test_an_environment_appearing_twice_must_be_closed_twice() -> None:
    tex = "\\begin{itemize}\\item a\\end{itemize}\\begin{itemize}\\item b"

    assert unbalanced_environments(tex) == ("itemize",)


def test_the_report_is_sorted_so_it_is_deterministic() -> None:
    tex = "\\begin{zebra}\\begin{alpha}"

    assert unbalanced_environments(tex) == ("alpha", "zebra")


def test_a_commented_environment_is_not_counted() -> None:
    assert unbalanced_environments("% \\begin{example}") == ()


# ---------------------------------------------------------------------------
# inspect_latex — apa yang dapat dipastikan tanpa memanggil model
# ---------------------------------------------------------------------------
ALLOWED = (citation_key(CORMEN),)


def test_a_clean_chapter_is_reported_clean() -> None:
    chapter = _chapter(
        body_tex=(
            f"\\section{{Notasi Big-O}}\n"
            f"Waktu tumbuh linear \\cite{{{ALLOWED[0]}}}.\n"
            f"\\label{{sec:bigo}}\n"
        ),
        labels=("sec:bigo",),
        citations=(ALLOWED[0],),
    )

    clean, findings = inspect_latex(chapter, allowed_citations=ALLOWED)

    assert clean is True
    assert findings == ()


def test_an_empty_body_is_a_single_finding_and_stops_there() -> None:
    """Bab kosong membuat seluruh pemeriksaan lain hampa — jangan kubur temuan aslinya."""
    clean, findings = inspect_latex(
        _chapter(body_tex="", citations=(ALLOWED[0],)), allowed_citations=ALLOWED
    )

    assert clean is False
    assert findings == ("isi bab (body_tex) kosong",)


def test_a_body_of_comments_only_counts_as_empty() -> None:
    """Komentar tidak sampai ke PDF; bab yang isinya hanya komentar adalah bab kosong."""
    clean, findings = inspect_latex(
        _chapter(body_tex="% hanya komentar"), allowed_citations=ALLOWED
    )

    assert clean is False
    assert findings == ("isi bab (body_tex) kosong",)


def test_a_preamble_in_the_body_is_a_finding() -> None:
    clean, findings = inspect_latex(
        _chapter(body_tex="\\documentclass{book}\nisi"), allowed_citations=ALLOWED
    )

    assert clean is False
    assert any("preamble" in finding for finding in findings)


def test_a_chapter_command_in_the_body_is_a_finding() -> None:
    """Judul yang ditulis dua kali akan tercetak dua kali, dengan nomor yang berbeda."""
    clean, findings = inspect_latex(
        _chapter(body_tex="\\chapter{Judul}\nisi"), allowed_citations=ALLOWED
    )

    assert clean is False
    assert any("\\chapter" in finding for finding in findings)


def test_a_chapter_label_in_the_body_is_a_finding() -> None:
    clean, findings = inspect_latex(
        _chapter(body_tex="\\label{chap:2}\nisi"), allowed_citations=ALLOWED
    )

    assert clean is False
    assert any("label bab" in finding for finding in findings)


def test_an_invented_citation_key_is_a_finding() -> None:
    """§34 pada tingkat kunci: satu-satunya cara menyimpang adalah mengubah kunci."""
    clean, findings = inspect_latex(
        _chapter(body_tex="\\cite{kunci-karangan}"), allowed_citations=ALLOWED
    )

    assert clean is False
    assert any("kunci-karangan" in finding for finding in findings)


def test_a_declared_key_that_is_never_used_is_a_finding() -> None:
    clean, findings = inspect_latex(
        _chapter(body_tex="isi tanpa sitasi", citations=(ALLOWED[0],)),
        allowed_citations=ALLOWED,
    )

    assert clean is False
    assert any("tidak dipakai" in finding for finding in findings)


def test_a_duplicate_label_within_one_chapter_is_a_finding() -> None:
    """``\\ref`` akan menunjuk label yang terakhir, dan itu bukan yang dimaksud."""
    duplicated = "\\label{sec:a}\nteks\n\\label{sec:a}\nteks"

    clean, findings = inspect_latex(_chapter(body_tex=duplicated), allowed_citations=ALLOWED)

    assert clean is False
    assert any("label ganda" in finding for finding in findings)


def test_a_declared_label_missing_from_the_body_is_a_finding() -> None:
    clean, findings = inspect_latex(
        _chapter(body_tex="isi tanpa label", labels=("sec:hilang",)),
        allowed_citations=ALLOWED,
    )

    assert clean is False
    assert any("sec:hilang" in finding for finding in findings)


def test_an_unbalanced_environment_is_a_finding() -> None:
    clean, findings = inspect_latex(
        _chapter(body_tex="\\begin{example}\nisi"), allowed_citations=ALLOWED
    )

    assert clean is False
    assert any("tidak berpasangan" in finding for finding in findings)


def test_no_citation_is_required_when_no_key_is_allowed() -> None:
    """Bab yang memang tidak boleh mengutip apa pun tidak boleh dihukum karena itu."""
    clean, findings = inspect_latex(_chapter(body_tex="isi tanpa sitasi"), allowed_citations=())

    assert clean is True
    assert findings == ()


def test_an_allowed_key_that_is_not_used_is_not_a_finding() -> None:
    """Daftar kunci yang boleh dipakai bukan daftar kunci yang wajib dipakai."""
    clean, _ = inspect_latex(_chapter(body_tex="isi tanpa sitasi"), allowed_citations=ALLOWED)

    assert clean is True


# ---------------------------------------------------------------------------
# render_chapter_latex — deterministik byte-per-byte
# ---------------------------------------------------------------------------
def test_the_chapter_command_carries_the_title_only() -> None:
    rendered = render_chapter_latex(_chapter(title="Bab 2: Analisis"), number=2, spec=_spec())

    assert rendered.startswith("\\chapter{Analisis}\n")


def test_the_chapter_number_comes_from_the_caller_not_the_model() -> None:
    """Sama seperti Markdown: nomor yang benar adalah nomor yang diberikan sistem."""
    rendered = render_chapter_latex(_chapter(number=7), number=2, spec=_spec())

    assert rendered.startswith("\\chapter{Analisis Kompleksitas}\n")
    assert "\\label{chap:2}" in rendered


def test_a_title_with_latex_specials_is_escaped() -> None:
    """Judul datang dari planner, bukan dari model — ia tidak melewati pelolosan model."""
    rendered = render_chapter_latex(_chapter(title="A & B_2"), number=1, spec=_spec())

    assert "\\chapter{A \\& B\\_2}" in rendered


def test_objectives_come_from_the_specification_not_the_draft() -> None:
    """Spesifikasi sudah dikunci ``enforce_draft_contract``; draf bisa memparafrase.

    Perender tidak menerima draf justru supaya pilihan itu tidak dapat dibuat dua
    kali.
    """
    spec = _spec(objectives=("Tujuan dari spesifikasi",))

    rendered = render_chapter_latex(_chapter(), number=2, spec=spec)

    assert "\\section*{Tujuan Pembelajaran}" in rendered
    assert "\\item Tujuan dari spesifikasi" in rendered


def test_objectives_are_escaped_too() -> None:
    spec = _spec(objectives=("Menghitung O(n) & sejenisnya",))

    rendered = render_chapter_latex(_chapter(), number=2, spec=spec)

    assert "\\item Menghitung O(n) \\& sejenisnya" in rendered


def test_a_spec_without_objectives_omits_the_list() -> None:
    """Judul bagian kosong lebih buruk daripada tidak ada bagian sama sekali."""
    rendered = render_chapter_latex(_chapter(), number=2, spec=_spec(objectives=()))

    assert "Tujuan Pembelajaran" not in rendered


def test_the_body_is_included_verbatim() -> None:
    body = "\\section{Notasi}\nProsa dengan \\textbf{penekanan}."

    rendered = render_chapter_latex(_chapter(body_tex=body), number=2, spec=_spec())

    assert body in rendered


def test_the_assembled_chapter_carries_no_preamble() -> None:
    """Bab adalah potongan yang di-``\\include``; preamble ada di ``preamble.tex``."""
    rendered = render_chapter_latex(_chapter(), number=2, spec=_spec())

    assert "\\documentclass" not in rendered
    assert "\\begin{document}" not in rendered
    assert "\\usepackage" not in rendered


def test_the_output_ends_with_exactly_one_newline() -> None:
    rendered = render_chapter_latex(_chapter(body_tex="\n\nisi\n\n"), number=2, spec=_spec())

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_rendering_is_deterministic() -> None:
    """Murni: masukan yang sama menghasilkan berkas yang sama, byte per byte."""
    chapter = _chapter()

    first = render_chapter_latex(chapter, number=2, spec=_spec())
    second = render_chapter_latex(chapter, number=2, spec=_spec())

    assert first == second


# ---------------------------------------------------------------------------
# render_bibliography
# ---------------------------------------------------------------------------
def test_each_entry_is_a_misc_with_a_title() -> None:
    """``@misc`` dengan judul yang benar lebih baik daripada ``@book`` dengan penulis salah."""
    rendered = render_bibliography([CORMEN])

    assert rendered.startswith("@misc{" + citation_key(CORMEN) + ",")
    assert "title = {" + CORMEN + "}" in rendered


def test_special_characters_in_a_source_name_are_escaped() -> None:
    rendered = render_bibliography(["Smith & Jones_2001"])

    assert r"Smith \& Jones\_2001" in rendered


def test_the_bibliography_is_deterministic() -> None:
    first = render_bibliography([SEDGEWICK, CORMEN])
    second = render_bibliography([CORMEN, SEDGEWICK])

    assert first == second


def test_an_empty_source_list_yields_an_empty_file() -> None:
    """Berkas kosong adalah keluaran yang sah; entri yang dikarang tidak."""
    assert render_bibliography([]) == ""


# ---------------------------------------------------------------------------
# lock_chapter_number
# ---------------------------------------------------------------------------
def test_a_correct_number_is_left_untouched_and_unreported() -> None:
    chapter, notes = lock_chapter_number(_chapter(number=2), number=2)

    assert chapter.number == 2
    assert notes == ()


def test_a_wrong_number_is_locked_and_the_correction_is_reported() -> None:
    """Yang salah diperbaiki, dan perbaikannya **dilaporkan** — bukan ditelan."""
    chapter, notes = lock_chapter_number(_chapter(number=7), number=2)

    assert chapter.number == 2
    assert notes
    assert "7" in notes[0] and "2" in notes[0]


def test_locking_the_number_preserves_the_rest_of_the_output() -> None:
    chapter, _ = lock_chapter_number(_chapter(number=7), number=2)

    assert chapter.title == "Analisis Kompleksitas"
    assert chapter.body_tex == _chapter().body_tex


# ---------------------------------------------------------------------------
# Nama berkas dan kopling ke template
# ---------------------------------------------------------------------------
def test_latex_filenames_match_their_markdown_twins() -> None:
    """``chapter01.md`` dan ``chapter01.tex`` harus berbicara tentang bab yang sama."""
    assert chapter_latex_filename(1) == "chapter01.tex"
    assert chapter_latex_filename(9) == "chapter09.tex"
    assert chapter_latex_filename(10) == "chapter10.tex"


def test_numbering_does_not_truncate_beyond_ninety_nine() -> None:
    assert chapter_latex_filename(100) == "chapter100.tex"


def test_the_bibliography_filename_is_the_one_bibtex_is_told_to_look_for() -> None:
    """Kopling yang tidak dapat ditegakkan pemeriksa tipe: dua tempat, satu nama.

    BibTeX tidak memberi tahu siapa pun ketika keduanya berbeda; ia hanya gagal
    menemukan entri dan mencetak daftar pustaka kosong.
    """
    main_tex = (templates_dir() / "main.tex").read_text(encoding="utf-8")

    assert BIBLIOGRAPHY_FILENAME == "references.bib"
    assert "\\bibliography{references}" in main_tex


def test_the_template_directory_ships_the_two_files_the_book_needs() -> None:
    """``main.tex`` (dokumen) dan ``preamble.tex`` (kontrak environment)."""
    assert (templates_dir() / "main.tex").is_file()
    assert (templates_dir() / "preamble.tex").is_file()


def test_the_preamble_defines_every_named_environment_the_prompt_offers() -> None:
    """Environment yang ditawarkan prompt tapi tidak ada di preamble baru ketahuan
    saat kompilasi — dan pesan galatnya menunjuk ke tempat yang salah.
    """
    preamble = (templates_dir() / "preamble.tex").read_text(encoding="utf-8")

    for environment in ("definition", "example", "theorem"):
        assert f"\\newtheorem{{{environment}}}" in preamble
    assert "\\lstset{" in preamble, "lstlisting butuh konfigurasi, bukan hanya paketnya"
    assert "\\usepackage{listings}" in preamble


def test_main_tex_includes_chapters_from_the_directory_they_are_written_to() -> None:
    """``\\include{chapters/chapterNN}`` harus menunjuk tempat penulisnya menulis."""
    main_tex = (templates_dir() / "main.tex").read_text(encoding="utf-8")

    assert "chapters/" in main_tex
    assert "\\input{preamble}" in main_tex


def test_the_templates_directory_is_a_real_path() -> None:
    assert isinstance(templates_dir(), Path)
    assert templates_dir().is_dir()
