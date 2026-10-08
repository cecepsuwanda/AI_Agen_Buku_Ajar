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
from domain.errors import ConfigError
from domain.latex import (
    CHAPTER_BLOCK_BEGIN,
    CHAPTER_BLOCK_END,
    LatexBuildResult,
    LatexChapter,
    bibliography_entries,
    bibliography_sources,
    build_advisories,
    build_problems,
    chapter_citations,
    chapter_latex_filename,
    chapter_number_from_filename,
    citation_key,
    crossref_findings,
    escape_latex,
    extract_cite_keys,
    extract_labels,
    extract_ref_keys,
    inspect_latex,
    lock_chapter_number,
    log_excerpt,
    render_bibliography,
    render_chapter_latex,
    render_main_tex,
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


def test_the_chapter_number_can_be_read_back_from_the_filename() -> None:
    """Kebalikan :func:`chapter_latex_filename` — dipakai graf untuk menaruh konsep.

    Graf konsep membangun simpulnya dari **berkas**, jadi satu-satunya hal yang
    dapat diketahuinya tentang bab asal sebuah bahan adalah namanya. Tanpa
    kebalikan ini, setiap konsep dari ``input/source_latex/`` akan masuk graf
    tanpa nomor bab, dan prasyarat antar-bab tidak akan pernah dapat ditelusuri.
    """
    for number in (1, 7, 9, 10, 100):
        assert chapter_number_from_filename(chapter_latex_filename(number)) == number


@pytest.mark.parametrize(
    "filename",
    [
        "chapter.tex",  # tanpa nomor
        "chapter01.md",  # bukan berkas LaTeX
        "bab01.tex",  # awalan yang tidak dikenal
        "chapter01.tex.bak",
        "chapter-1.tex",
        "chapter0.tex",  # bab bernomor nol tidak ada
        "intro.tex",
    ],
)
def test_a_filename_that_does_not_follow_the_pattern_has_no_chapter(filename: str) -> None:
    """``None`` — bukan tebakan. Bahan rujukan tidak selalu milik satu bab."""
    assert chapter_number_from_filename(filename) is None


def test_a_filename_with_surrounding_whitespace_is_still_read() -> None:
    """Nama berkas yang datang dari daftar direktori dapat membawa spasi di ujungnya."""
    assert chapter_number_from_filename("  chapter03.tex  ") == 3


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


# ---------------------------------------------------------------------------
# bibliography_sources / chapter_citations — satu sumber untuk dua pemakai
# ---------------------------------------------------------------------------
def test_book_and_draft_sources_are_merged_in_order_without_duplicates() -> None:
    """Bab yang mengutip ulang sumber bab 1 tidak boleh menghasilkan entri kedua."""
    assert bibliography_sources([CORMEN], [SEDGEWICK, CORMEN]) == (CORMEN, SEDGEWICK)


def test_surrounding_whitespace_is_stripped_from_draft_sources() -> None:
    """Nama sumber yang berbeda spasi adalah sumber yang sama, bukan sumber lain."""
    assert bibliography_sources([], [f"  {CORMEN}  "]) == (CORMEN,)


def test_no_source_at_all_yields_an_empty_list() -> None:
    assert bibliography_sources([], []) == ()


def test_chapter_citations_pairs_only_the_sources_the_draft_actually_used() -> None:
    """Daftar kunci yang dikirim ke model adalah daftar tertutup untuk bab ini."""
    pairs = chapter_citations([CORMEN, SEDGEWICK], [CORMEN])

    assert pairs == ((citation_key(CORMEN), CORMEN),)


def test_a_draft_source_that_is_not_in_the_bibliography_is_dropped() -> None:
    """Sumber di luar daftar buku adalah kunci tanpa entri — dan entri tanpa kunci."""
    assert chapter_citations([CORMEN], [SEDGEWICK]) == ()


def test_chapter_citations_keeps_one_pair_per_source_however_often_it_is_cited() -> None:
    assert len(chapter_citations([CORMEN], [CORMEN, CORMEN])) == 1


# ---------------------------------------------------------------------------
# build_problems / build_advisories — apa yang menahan dan apa yang tidak
# ---------------------------------------------------------------------------
def test_a_clean_build_has_neither_problems_nor_advisories() -> None:
    result = LatexBuildResult(ok=True)

    assert build_problems(result) == ()
    assert build_advisories(result) == ()


def test_a_compilation_error_is_a_problem() -> None:
    problems = build_problems(LatexBuildResult(ok=False, errors=("Missing $ inserted.",)))

    assert any("Missing $ inserted." in problem for problem in problems)


def test_a_failed_build_without_readable_errors_is_still_a_problem() -> None:
    """``latexmk`` yang keluar non-nol tanpa pesan apa pun tetap berarti tidak ada PDF."""
    problems = build_problems(LatexBuildResult(ok=False))

    assert problems == ("kompilasi tidak menghasilkan PDF",)


def test_an_unreadable_error_list_wins_over_the_generic_message() -> None:
    """Pesan umum hanya dipakai bila tidak ada yang lebih spesifik untuk dikatakan."""
    problems = build_problems(LatexBuildResult(ok=False, errors=("Undefined control sequence.",)))

    assert "kompilasi tidak menghasilkan PDF" not in problems


def test_a_missing_figure_blocks_even_though_the_log_has_no_error_line() -> None:
    """Gambar yang hilang tidak selalu muncul sebagai baris ``!`` — dan tetap menahan."""
    problems = build_problems(LatexBuildResult(ok=True, missing_figures=("figures/graf.png",)))

    assert any("figures/graf.png" in problem for problem in problems)


def test_a_dangling_citation_is_a_problem() -> None:
    problems = build_problems(LatexBuildResult(ok=True, undefined_citations=("karangan-1a2b",)))

    assert any("karangan-1a2b" in problem for problem in problems)


def test_a_duplicate_label_is_a_problem() -> None:
    problems = build_problems(LatexBuildResult(ok=True, duplicate_labels=("sec:bigo",)))

    assert any("sec:bigo" in problem for problem in problems)


def test_a_dangling_reference_is_advisory_rather_than_blocking() -> None:
    """Bab adalah potongan: ``\\ref`` ke bab lain selalu tampak menggantung di sini.

    Menolak setiap bab yang merujuk bab sebelumnya berarti tidak ada satu pun
    buku yang dapat lolos. Yang tahu jawabannya adalah kompilasi tingkat buku.
    """
    result = LatexBuildResult(ok=True, undefined_refs=("chap:2",))

    assert build_problems(result) == ()
    assert any("chap:2" in advisory for advisory in build_advisories(result))


def test_an_overfull_box_is_advisory_with_its_count() -> None:
    result = LatexBuildResult(ok=True, overfull_boxes=("Overfull \\hbox (9.5pt too wide)",))

    assert build_problems(result) == ()
    assert any("1" in advisory for advisory in build_advisories(result))


def test_unclassified_warnings_are_passed_on_as_advisories() -> None:
    result = LatexBuildResult(ok=True, warnings=("Package hyperref Warning: x",))

    advisories = build_advisories(result)

    assert any("hyperref" in advisory for advisory in advisories)


# ---------------------------------------------------------------------------
# log_excerpt — jendela di sekitar isyarat pertama
# ---------------------------------------------------------------------------
def test_the_excerpt_skips_the_header_and_starts_near_the_signal() -> None:
    noise = [f"bising {index}" for index in range(30)]
    text = "\n".join([*noise, "! Missing $ inserted.", "a", "b"])

    excerpt = log_excerpt(text)

    assert "Missing $ inserted" in excerpt
    assert "bising 0" not in excerpt


def test_the_excerpt_keeps_the_two_lines_before_the_signal() -> None:
    """Konteks sebelum galat sering memuat lingkungan tempat galatnya terjadi."""
    text = "\n".join(["sebelum-1", "sebelum-2", "! Galat.", "sesudah"])

    assert log_excerpt(text).splitlines()[0] == "sebelum-1"


def test_an_overfull_box_counts_as_a_signal() -> None:
    noise = [f"bising {index}" for index in range(30)]
    text = "\n".join([*noise, "Overfull \\hbox (9.5pt too wide)"])

    assert "Overfull" in log_excerpt(text)


def test_a_generic_warning_is_not_a_signal() -> None:
    """Kalau setiap peringatan dianggap isyarat, jendelanya berhenti di baris pertama."""
    text = "\n".join(
        [*(f"Package foo Warning: {index}" for index in range(30)), "! Galat.", "ekor"]
    )

    assert "Galat" in log_excerpt(text)


def test_a_log_without_any_signal_falls_back_to_its_first_lines() -> None:
    text = "\n".join(f"baris {index}" for index in range(100))

    lines = log_excerpt(text).splitlines()

    assert lines[0] == "baris 0"


def test_the_window_is_capped() -> None:
    text = "\n".join(["! Galat.", *(f"ekor {index}" for index in range(200))])

    assert len(log_excerpt(text).splitlines()) <= 41


def test_an_empty_log_yields_an_empty_excerpt() -> None:
    assert log_excerpt("") == ""


# ---------------------------------------------------------------------------
# extract_ref_keys / crossref_findings
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["ref", "eqref", "pageref", "autoref", "cref", "Cref"])
def test_every_reference_command_is_recognised(command: str) -> None:
    """Perintah yang tidak dikenali berarti rujukan menggantung yang tidak terlihat."""
    assert extract_ref_keys(f"lihat \\{command}{{sec:a}}") == ("sec:a",)


def test_a_reference_key_is_reported_once_however_often_it_appears() -> None:
    assert extract_ref_keys(r"\ref{a} dan \ref{a}") == ("a",)


def test_an_optional_argument_on_a_reference_is_skipped() -> None:
    assert extract_ref_keys(r"\cref[a]{b}") == ("b",)


def test_a_commented_reference_is_not_counted() -> None:
    assert extract_ref_keys("% \\ref{a}") == ()


def test_a_reference_pointing_at_a_missing_label_is_reported() -> None:
    findings = crossref_findings(r"\ref{sec:hilang}", bibliography_keys=())

    assert any("sec:hilang" in finding for finding in findings)


def test_a_reference_pointing_at_an_existing_label_is_silent() -> None:
    tex = "\\label{sec:ada}\nlihat \\ref{sec:ada}"

    assert crossref_findings(tex, bibliography_keys=()) == ()


def test_a_citation_without_a_bibliography_entry_is_reported() -> None:
    findings = crossref_findings(r"\cite{karangan}", bibliography_keys=("cormen-1a2b3c4d",))

    assert any("karangan" in finding for finding in findings)


def test_a_citation_with_a_bibliography_entry_is_silent() -> None:
    tex = r"\cite{cormen-1a2b3c4d}"

    assert crossref_findings(tex, bibliography_keys=("cormen-1a2b3c4d",)) == ()


def test_both_kinds_of_findings_can_be_reported_at_once() -> None:
    tex = "lihat \\ref{hilang} dan \\cite{karangan}"

    findings = crossref_findings(tex, bibliography_keys=("ada-1a2b3c4d",))

    assert len(findings) == 2


# ---------------------------------------------------------------------------
# render_main_tex — skeleton buku dari template
# ---------------------------------------------------------------------------
def _template() -> str:
    return (
        "\\documentclass{book}\n"
        "\\title{Buku Ajar}\n"
        f"{CHAPTER_BLOCK_BEGIN}\n"
        "% \\include{chapters/chapter01}\n"
        f"{CHAPTER_BLOCK_END}\n"
        "\\begin{document}\n\\maketitle\n\\end{document}\n"
    )


def test_each_chapter_becomes_one_include_line() -> None:
    rendered = render_main_tex(_template(), title="Algoritma", chapter_numbers=(1, 2))

    assert "\\include{chapters/chapter01}" in rendered
    assert "\\include{chapters/chapter02}" in rendered


def test_the_placeholder_lines_inside_the_block_are_replaced() -> None:
    """Baris contoh di dalam penanda adalah komentar, tetapi tetap harus hilang."""
    rendered = render_main_tex(_template(), title="Algoritma", chapter_numbers=(1,))

    body = rendered.split(CHAPTER_BLOCK_BEGIN)[1].split(CHAPTER_BLOCK_END)[0]

    assert body.strip() == "\\include{chapters/chapter01}"


def test_the_title_line_is_replaced_with_escaped_title() -> None:
    rendered = render_main_tex(_template(), title="Algoritma & Struktur", chapter_numbers=())

    assert "\\title{Algoritma \\& Struktur}" in rendered
    assert "\\title{Buku Ajar}" not in rendered


def test_the_rest_of_the_template_is_left_untouched() -> None:
    """Prodi yang punya preamble sendiri tidak boleh kehilangan preamblenya."""
    rendered = render_main_tex(_template(), title="X", chapter_numbers=(1,))

    assert rendered.startswith("\\documentclass{book}\n")
    assert "\\maketitle" in rendered


def test_no_chapter_yields_an_empty_block_rather_than_stale_includes() -> None:
    rendered = render_main_tex(_template(), title="X", chapter_numbers=())

    body = rendered.split(CHAPTER_BLOCK_BEGIN)[1].split(CHAPTER_BLOCK_END)[0]

    assert body.strip() == ""


def test_a_template_without_the_markers_is_refused_loudly() -> None:
    """Skeleton yang diisi di tempat yang salah lebih buruk daripada skeleton yang gagal."""
    with pytest.raises(ConfigError):
        render_main_tex("\\documentclass{book}\n", title="X", chapter_numbers=(1,))


def test_inverted_markers_are_refused() -> None:
    template = f"{CHAPTER_BLOCK_END}\n{CHAPTER_BLOCK_BEGIN}\n"

    with pytest.raises(ConfigError):
        render_main_tex(template, title="X", chapter_numbers=(1,))


def test_the_shipped_template_has_the_markers_in_order() -> None:
    """Kopling yang tidak dapat ditegakkan pemeriksa tipe: konstanta dan berkasnya."""
    template = (templates_dir() / "main.tex").read_text(encoding="utf-8")

    assert render_main_tex(template, title="X", chapter_numbers=(1, 2)).count(
        "\\include{chapters/chapter"
    ) == 2
