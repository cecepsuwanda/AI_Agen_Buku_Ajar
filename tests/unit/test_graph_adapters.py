"""Tes adapter graf konsep (§11, §14).

Yang diuji di sini adalah **apa yang dapat dipastikan dari berkasnya**, dan
itulah batas yang ditarik paket ini. Hanya bagian, definisi, dan rujukan silang
yang diambil; relasi yang tidak menandai dirinya sendiri di dalam teks — *Lexer*
memakai *Regular Expression* — sengaja tidak ditebak dari kedekatan kata. Graf
yang salah lebih buruk daripada graf yang kosong, sebab ia terlihat dapat
dipercaya.

Dua hal yang paling perlu dibuktikan:

1. **Berkas yang tidak dapat dibaca tidak menggagalkan pembangunannya.** Graf
    yang kehilangan satu bahan tetap graf yang benar tentang bahan lainnya.
2. **Penulisannya atomik dan hasilnya dapat dibaca ulang.** Graf ini turunan —
    ia boleh dibuang kapan saja, tetapi tidak boleh pernah setengah tertulis.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.errors import ArtifactWriteError
from domain.graph import ConceptGraph, ConceptNode
from graph.entities import concepts_from_latex, plain_definition, sections_of
from graph.knowledge_graph import (
    GRAPH_FILENAME,
    JsonGraphStore,
    graph_from_directories,
    graph_from_directory,
    tex_files,
)
from graph.relations import references_from_latex

#: Berkas LaTeX kecil yang memuat keduanya: bagian, definisi, dan rujukan silang.
CHAPTER_TEX = r"""
\chapter{Mesin Keadaan}
Teks pembuka sebelum bagian pertama — bukan bagian, dan tidak menjadi konsep.

\section{Finite Automaton}
\label{sec:fa}
\begin{definition}
Mesin keadaan berhingga yang perpindahannya \emph{deterministik}.
\end{definition}
Lihat juga \ref{sec:regex} untuk bentuk yang lebih ringkas.

\section{Regular Expression}
\label{sec:regex}
\begin{definition}
Cara lain menuliskan himpunan string yang sama.
\end{definition}
Rujukan ke bagian yang sudah dibaca: \ref{sec:fa}.

\section{Tanpa Definisi}
Bagian ini diajarkan, tetapi belum dijelaskan.
"""


@pytest.fixture
def tex_dir(tmp_path: Path) -> Path:
    """Direktori sumber LaTeX berisi satu berkas bernama sesuai pola (§25)."""
    directory = tmp_path / "source_latex"
    directory.mkdir()
    (directory / "chapter07.tex").write_text(CHAPTER_TEX, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# 1. Bagian dan definisi (§11)
# ---------------------------------------------------------------------------
def test_sections_are_the_headed_parts_and_nothing_else() -> None:
    """Teks sebelum bagian pertama tidak menjadi bagian: ia tidak punya judul."""
    titles = [title for title, _ in sections_of(CHAPTER_TEX)]

    assert titles == ["Finite Automaton", "Regular Expression", "Tanpa Definisi"]


def test_a_repeated_heading_stays_two_sections() -> None:
    """Bagian yang judulnya sama dua kali bukan kekeliruan yang boleh dirapikan di sini."""
    text = "\\section{Sama}\nIsi pertama.\n\\section{Sama}\nIsi kedua.\n"

    assert sections_of(text) == (("Sama", "\nIsi pertama.\n"), ("Sama", "\nIsi kedua.\n"))


def test_a_section_without_a_heading_is_not_a_section() -> None:
    """``\\section{}`` kosong tidak menamai apa pun, jadi tidak menjadi konsep."""
    assert sections_of("\\section{}\nIsi.\n") == ()


def test_subsection_and_starred_forms_are_sections_too() -> None:
    text = "\\subsection{Turunan}\nA.\n\\section*{Tanpa Nomor}\nB.\n"

    assert [title for title, _ in sections_of(text)] == ["Turunan", "Tanpa Nomor"]


def test_a_definition_is_readable_text_without_its_markup() -> None:
    """Penandanya dibuang, isinya dipertahankan: ``\\emph{DFA}`` menyatakan hal yang sama."""
    raw = r"Mesin \emph{keadaan} berhingga \label{def:fa} yang \textbf{deterministik}."
    plain = plain_definition(raw)

    assert plain == "Mesin keadaan berhingga yang deterministik."


def test_every_section_becomes_a_concept_even_without_a_definition() -> None:
    """Bagian tanpa definisi tetap diajarkan — dan justru itu yang perlu terlihat (§14)."""
    concepts = concepts_from_latex(CHAPTER_TEX, chapter=7)

    assert [item.name for item in concepts] == [
        "Finite Automaton",
        "Regular Expression",
        "Tanpa Definisi",
    ]
    assert concepts[0].definition.startswith("Mesin keadaan berhingga")
    assert concepts[0].chapter == 7
    assert concepts[2].definition == ""


def test_a_concept_without_a_chapter_carries_no_chapter() -> None:
    """Bahan rujukan tidak selalu milik satu bab, dan mengarangnya menunjuk ke tempat yang salah."""
    concepts = concepts_from_latex("\\section{Bahan Umum}\nTeks.\n")

    assert concepts[0].chapter is None


# ---------------------------------------------------------------------------
# 2. Rujukan silang (§14: cross-reference)
# ---------------------------------------------------------------------------
def test_a_reference_becomes_one_edge() -> None:
    """``\\label`` menamai bagian, ``\\ref`` menunjuknya — hanya itu yang pasti."""
    edges = references_from_latex(CHAPTER_TEX)

    assert [(item.source, item.target, item.relation) for item in edges] == [
        ("Finite Automaton", "Regular Expression", "references"),
        ("Regular Expression", "Finite Automaton", "references"),
    ]


def test_a_reference_is_one_edge_no_matter_how_often_it_is_repeated() -> None:
    text = "\\section{A}\n\\label{a}\n\\ref{b} dan \\ref{b} lagi.\n\\section{B}\n\\label{b}\n"

    assert len(references_from_latex(text)) == 1


def test_a_reference_to_its_own_section_is_dropped() -> None:
    """Bukan karena terlarang, melainkan karena ``build_graph`` akan membuangnya juga."""
    text = "\\section{A}\n\\label{a}\nLihat \\ref{a}.\n"

    assert references_from_latex(text) == ()


def test_a_reference_to_a_label_outside_the_file_is_dropped() -> None:
    """Berkas yang belum dibaca tidak boleh muncul di dalam graf sebagai janji."""
    text = "\\section{A}\nLihat \\ref{ada-di-berkas-lain}.\n"

    assert references_from_latex(text) == ()


def test_a_label_declared_after_the_reference_still_resolves() -> None:
    """Rujukan ke depan adalah hal biasa di LaTeX; urutan di berkas tidak menentukan."""
    text = "\\section{A}\n\\ref{b}\n\\section{B}\n\\label{b}\n"

    assert [(item.source, item.target) for item in references_from_latex(text)] == [("A", "B")]


# ---------------------------------------------------------------------------
# 3. Pembangunan dari direktori
# ---------------------------------------------------------------------------
def test_only_tex_files_are_read(tex_dir: Path) -> None:
    (tex_dir / "catatan.md").write_text("# Bukan sumber graf\n", encoding="utf-8")
    nested = tex_dir / "sub"
    nested.mkdir()
    (nested / "chapter08.tex").write_text("\\section{Bersarang}\nTeks.\n", encoding="utf-8")

    found = tex_files(tex_dir)

    assert [path.name for path in found] == ["chapter07.tex", "chapter08.tex"]


def test_a_directory_that_does_not_exist_yields_nothing(tmp_path: Path) -> None:
    """Bahan yang belum ada bukan keadaan rusak — ia keadaan yang jujur."""
    missing = tmp_path / "belum-ada"

    assert tex_files(missing) == ()
    assert graph_from_directory(missing) == ConceptGraph()


def test_the_build_is_the_same_every_time(tex_dir: Path) -> None:
    """Graf yang berubah setiap kali dibangun ulang tidak dapat dibandingkan dengan dirinya."""
    assert graph_from_directory(tex_dir) == graph_from_directory(tex_dir)


def test_an_unreadable_file_is_skipped_and_the_rest_is_still_read(tex_dir: Path) -> None:
    """Satu bahan yang rusak tidak boleh membatalkan seluruh graf."""
    broken = tex_dir / "chapter09.tex"
    broken.write_bytes(b"\xff\xfe bukan utf-8 \x00")

    graph = graph_from_directory(tex_dir)

    assert "Finite Automaton" in graph.names()
    assert len(graph.nodes) == 3


def test_the_chapter_number_comes_from_the_filename(tex_dir: Path) -> None:
    """``chapter07.tex`` menaruh konsepnya di bab 7 — bukan di bab yang ditebak."""
    graph = graph_from_directory(tex_dir)

    assert graph.node("Finite Automaton") is not None
    assert graph.node("Finite Automaton").chapter == 7  # type: ignore[union-attr]


def test_directories_are_joined_in_the_order_they_are_given(tmp_path: Path) -> None:
    """Bahan rujukan lebih dulu: definisinyalah yang bertahan saat keduanya ada."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "chapter01.tex").write_text(
        "\\section{DFA}\\begin{definition}Bahan rujukan.\n\\end{definition}\n", encoding="utf-8"
    )
    (second / "chapter01.tex").write_text(
        "\\section{DFA}\\begin{definition}Bab yang kita tulis sendiri.\n\\end{definition}\n",
        encoding="utf-8",
    )

    graph = graph_from_directories((first, second))

    assert len(graph.nodes) == 1
    assert graph.nodes[0].definition == "Bahan rujukan."


def test_relations_are_not_forged_between_two_directories(tmp_path: Path) -> None:
    """Dua bagian berlabel sama dari dua buku berbeda tidak boleh saling merujuk."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "chapter01.tex").write_text("\\section{A}\n\\label{a}\nTeks.\n", encoding="utf-8")
    (second / "chapter01.tex").write_text(
        "\\section{B}\nLihat \\ref{a}.\n", encoding="utf-8"
    )

    assert graph_from_directories((first, second)).edges == ()


# ---------------------------------------------------------------------------
# 4. Penyimpanan
# ---------------------------------------------------------------------------
def test_a_graph_that_was_never_saved_loads_as_empty(tmp_path: Path) -> None:
    """Graf yang belum dibangun bukan kerusakan."""
    assert JsonGraphStore(tmp_path / "graph").load() == ConceptGraph()


def test_a_corrupt_graph_file_loads_as_empty_rather_than_raising(tmp_path: Path) -> None:
    """Justru perintah yang memperbaikinya tidak boleh terhalang oleh berkas rusaknya."""
    store = JsonGraphStore(tmp_path / "graph")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ ini bukan json", encoding="utf-8")

    assert store.load() == ConceptGraph()


def test_a_saved_graph_comes_back_unchanged(tmp_path: Path) -> None:
    store = JsonGraphStore(tmp_path / "graph")
    graph = ConceptGraph(
        nodes=(
            ConceptNode(name="DFA", definition="Mesin keadaan berhingga.", chapter=7),
            ConceptNode(name="Regex"),
        ),
        edges=(),
    )

    store.save(graph)

    assert store.load() == graph


def test_the_graph_lands_in_a_predictably_named_file(tmp_path: Path) -> None:
    """Satu berkas, supaya diff-nya terbaca sebagai "grafnya berubah" (§14)."""
    store = JsonGraphStore(tmp_path / "graph")
    store.save(ConceptGraph())

    assert store.path == tmp_path / "graph" / GRAPH_FILENAME
    assert store.path.is_file()


def test_saving_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """Penulisannya atomik: tidak ada ``.tmp`` yang tertinggal setelah berhasil."""
    store = JsonGraphStore(tmp_path / "graph")
    store.save(ConceptGraph(nodes=(ConceptNode(name="DFA"),)))

    assert [path.name for path in store.path.parent.iterdir()] == [GRAPH_FILENAME]


def test_saving_over_an_existing_graph_replaces_it(tmp_path: Path) -> None:
    """Graf ini turunan: yang lama ditimpa, bukan digabungkan."""
    store = JsonGraphStore(tmp_path / "graph")
    store.save(ConceptGraph(nodes=(ConceptNode(name="Lama"),)))
    store.save(ConceptGraph(nodes=(ConceptNode(name="Baru"),)))

    assert store.load().names() == ("Baru",)


def test_a_directory_that_cannot_be_created_is_reported_as_a_write_failure(
    tmp_path: Path,
) -> None:
    """Direktori yang tertutup bagi penulis adalah kegagalan yang harus terlihat.

    ``ArtifactWriteError`` adalah jenis kesalahan yang sudah dikenal
    :func:`~app.commands.guarded`, jadi ia menjadi pesan + kode keluar — bukan
    traceback. Yang tidak boleh terjadi adalah grafnya diam-diam tidak ditulis.
    """
    blocker = tmp_path / "berkas-biasa"
    blocker.write_text("bukan direktori", encoding="utf-8")

    with pytest.raises(ArtifactWriteError):
        JsonGraphStore(blocker / "graph").save(ConceptGraph())
