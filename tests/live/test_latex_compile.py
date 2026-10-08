"""Kompilasi dan log LaTeX sungguhan — opt-in, di-skip secara bawaan.

Suite bawaan proyek ini hijau **tanpa perkakas LaTeX** (``pytest.ini`` menyetel
``-m "not live"``), dan itu memang janji Tahap 7: hampir seluruh logika §26 —
pemetaan temuan, tangga perbaikan, keputusan menerima atau menolak — adalah fungsi
murni, dan karenanya dapat dibuktikan di mesin mana pun tanpa menjalankan apa pun.

Berkas ini menguji sisa satu hal yang tidak dapat dibuktikan oleh tes offline mana
pun, dan ia berbentuk dua bagian:

1. **Apakah perkakas LaTeX sungguhan setuju dengan tebakan kita tentang lognya.**
   Seluruh ``latex/validator.py`` dibangun dari bentuk log yang kita *harapkan* —
   lengkap dengan berkas contoh di ``tests/data/latex_logs/`` yang kita tulis
   sendiri. Bila MiKTeX menuliskannya dengan bentuk lain, tidak ada satu pun tes
   offline yang akan menangkapnya.
2. **Apakah kompilasi sungguhan berhasil** — potongan bab maupun buku utuh
   menjadi PDF. Ini bergantung pada mesin ini punya ``latexmk`` **yang dapat
   dijalankan**, dan itulah sebabnya bagian ini di-*skip* bila tidak.

Bila perkakas yang dibutuhkan tidak ada, tesnya **skip**, bukan gagal: mesin tanpa
LaTeX bukan mesin dengan kode yang rusak.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from domain.latex import (
    LatexBuildResult,
    bibliography_entries,
    build_problems,
    citation_key,
    render_main_tex,
)
from latex.artifacts import FileLatexArtifacts, templates_dir
from latex.compiler import LatexmkCompiler
from latex.validator import parse_log

pytestmark = pytest.mark.live

CORMEN = "Cormen, Introduction to Algorithms, 4th ed."

#: Nama perkakas. ``pdflatex`` dipakai langsung untuk membuktikan **parser log**;
#: ``latexmk`` dipakai untuk membuktikan **kompilasi utuh**.
PDFLATEX = "pdflatex"
LATEXMK = "latexmk"

#: Batas waktu kompilasi. Dinaikkan dari bawaan karena tes ini membayar cold
#: start ``pdflatex`` di mesin yang baru saja dinyalakan.
TIMEOUT_S = 300.0

#: Dokumen dengan satu galat fatal: ``&`` yang belum diloloskan. Inilah bentuk
#: yang paling sering ditangani tangga perbaikan §26.
ERROR_DOC = """\\documentclass{book}
\\begin{document}
Algoritma & Struktur Data.
\\end{document}
"""

#: Dokumen yang memicu tiga peringatan sekaligus, tanpa bibtex: label ganda,
#: rujukan menggantung, dan sitasi menggantung. Ketiganya ditulis LaTeX sendiri,
#: sehingga tidak satu pun bergantung pada perkakas lain.
WARNING_DOC = """\\documentclass{book}
\\begin{document}
\\label{bab:dup}
\\label{bab:dup}
Lihat \\ref{bab:9} dan \\cite{kunci-karangan}.
\\end{document}
"""

#: Dokumen bersih — tetapi rujukannya ke depan, jadi ia baru bersih pada
#: kompilasi **kedua**: ``\\ref`` baru menemukan ``\\label`` setelah ``.aux``
#: dibaca. Itu bukan keanehan tes ini melainkan alasan ``latexmk`` ada.
CLEAN_DOC = """\\documentclass{book}
\\begin{document}
\\chapter{Bab Satu}\\label{bab:1}
Lihat \\ref{bab:1}.
\\end{document}
"""

#: Potongan yang setara dengan keluaran gate §25: judul, label bab, blok tujuan
#: pembelajaran, dan satu sub-bab yang mengutip satu sumber.
CHAPTER = (
    "\\chapter{Algoritma Pencarian}\n"
    "\\label{chap:1}\n"
    "\n"
    "\\section*{Tujuan Pembelajaran}\n"
    "\\begin{itemize}\n"
    "  \\item Mahasiswa mampu menghitung kompleksitas waktu\n"
    "\\end{itemize}\n"
    "\n"
    "\\section{Pencarian linear}\n"
    "Pencarian linear memeriksa tiap elemen satu per satu \\cite{KUNCI}.\n"
    "Ia berjalan dalam waktu $O(n)$ \\& tidak membutuhkan larik terurut.\n"
)


# ---------------------------------------------------------------------------
# Bagian 1 — log sungguhan, tanpa ``latexmk``
# ---------------------------------------------------------------------------
def _compile_with(engine: str, tex: str, directory: Path, *, passes: int = 1) -> str:
    """Jalankan ``engine`` atas ``tex`` dan kembalikan isi berkas ``.log``-nya.

    :raises pytest.skip: bila perkakasnya tidak ada di ``PATH``.
    """
    if shutil.which(engine) is None:
        pytest.skip(f"{engine} tidak ada di PATH; uji log sungguhan dilewati")

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "probe.tex").write_text(tex, encoding="utf-8")
    for _ in range(passes):
        subprocess.run(
            [engine, "-interaction=nonstopmode", "probe.tex"],
            cwd=directory,
            capture_output=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    return (directory / "probe.log").read_text(encoding="utf-8", errors="replace")


def test_a_real_miktex_log_reports_a_real_error(tmp_path: Path) -> None:
    """Bentuk ``!`` yang kita andalkan benar-benar ditulis seperti itu."""
    log = _compile_with(PDFLATEX, ERROR_DOC, tmp_path)

    result = parse_log(log)

    assert result.ok is False
    assert any("Misplaced alignment tab character" in error for error in result.errors), (
        f"galat tidak terbaca dari log sungguhan:\n{log[-800:]}"
    )
    assert not any(error.startswith("!") for error in result.errors)
    assert "Misplaced alignment tab character" in result.log_excerpt


def test_a_real_miktex_log_reports_real_warnings_in_their_own_buckets(tmp_path: Path) -> None:
    """Ketiga jenis peringatan §26 ditulis persis dalam bentuk yang diparse.

    **Dua lintasan, dan itu bukan kelonggaran.** Label ganda baru ketahuan ketika
    LaTeX membaca ``.aux`` dari kompilasi **sebelumnya**: pada lintasan pertama
    berkas itu belum ada, dan yang muncul justru "Label(s) may have changed.
    Rerun". Karena itu ``parse_log`` tidak dapat diharapkan menemukannya dari satu
    lintasan — dan justru itulah alasan kompilasi dijalankan lewat ``latexmk``,
    yang mengulang sampai berkas bantunya tenang.
    """
    log = _compile_with(PDFLATEX, WARNING_DOC, tmp_path, passes=2)

    result = parse_log(log)

    assert result.duplicate_labels == ("bab:dup",)
    assert result.undefined_refs == ("bab:9",)
    assert result.undefined_citations == ("kunci-karangan",)
    assert not any("Citation" in warning for warning in result.warnings)
    assert not any("Reference" in warning for warning in result.warnings)


def test_a_real_clean_run_parses_clean_on_the_second_pass(tmp_path: Path) -> None:
    """Satu kompilasi belum cukup untuk ``\\ref`` — dan itu bukan temuan bab."""
    log = _compile_with(PDFLATEX, CLEAN_DOC, tmp_path, passes=2)

    result = parse_log(log)

    assert result.errors == ()
    assert result.undefined_refs == ()
    assert result.undefined_citations == ()
    assert result.duplicate_labels == ()


# ---------------------------------------------------------------------------
# Bagian 2 — kompilasi utuh, lewat adapter
# ---------------------------------------------------------------------------
@pytest.fixture
def compiler(tmp_path: Path) -> LatexmkCompiler:
    """Compiler sungguhan, atau lewati tesnya bila perkakasnya tidak dapat dijalankan."""
    made = LatexmkCompiler(tmp_path / "latex", timeout_s=TIMEOUT_S)
    if not made.available():
        pytest.skip(f"{LATEXMK} tidak ada atau tidak dapat dijalankan di mesin ini")
    return made


def _with_real_key(chapter: str) -> tuple[str, tuple[str, ...]]:
    """Ganti penanda ``KUNCI`` dengan kunci sungguhan hasil :func:`citation_key`."""
    return (chapter.replace("KUNCI", citation_key(CORMEN)), (CORMEN,))


def test_a_clean_fragment_really_compiles(compiler: LatexmkCompiler) -> None:
    """Yang dibuktikan: perkakasnya berhenti dengan kode 0 dan menghasilkan PDF."""
    chapter, sources = _with_real_key(CHAPTER)

    result = compiler.compile_fragment(chapter, sources=sources)

    assert isinstance(result, LatexBuildResult)
    assert result.ok is True, result.log_excerpt
    assert build_problems(result) == (), result.log_excerpt
    assert not result.errors


def test_an_unescaped_ampersand_really_fails(compiler: LatexmkCompiler) -> None:
    """Bentuk galat yang paling sering ditangani tangga perbaikan §26."""
    chapter, sources = _with_real_key(CHAPTER.replace("\\&", "&"))

    result = compiler.compile_fragment(chapter, sources=sources)

    assert build_problems(result), "kompilasi tanpa galat berarti parser lognya salah membaca"
    assert result.log_excerpt, "tanpa potongan log, tangga perbaikan tidak punya bahan"


def test_a_citation_without_a_bibliography_entry_is_reported_by_latex(
    compiler: LatexmkCompiler,
) -> None:
    """``\\cite`` yang menggantung adalah temuan yang **menahan** bab (§26)."""
    invented = citation_key("Buku yang Tidak Pernah Ada")

    result = compiler.compile_fragment(CHAPTER.replace("KUNCI", invented), sources=(CORMEN,))

    assert invented in result.undefined_citations, result.log_excerpt
    assert build_problems(result)


def test_the_whole_book_is_assembled_into_a_pdf(compiler: LatexmkCompiler, tmp_path: Path) -> None:
    """Perakitan ``main.tex`` + bab + daftar pustaka, sampai PDF-nya ada (§42)."""
    chapter, sources = _with_real_key(CHAPTER)
    artifacts = FileLatexArtifacts(compiler.latex_dir)
    artifacts.save_chapter(1, chapter)
    destination = tmp_path / "book.pdf"
    template = (templates_dir() / "main.tex").read_text(encoding="utf-8")

    document = render_main_tex(template, title="Algoritma dan Struktur Data", chapter_numbers=(1,))
    assert "\\include{chapters/chapter01}" in document, "blok daftar bab tidak terisi"

    result = compiler.compile_book(
        title="Algoritma dan Struktur Data",
        chapter_numbers=(1,),
        sources=sources,
        destination=destination,
    )

    assert result.ok is True, result.log_excerpt
    assert build_problems(result) == (), result.log_excerpt
    assert destination.is_file()
    assert destination.stat().st_size > 0
    # Daftar pustakanya benar-benar memuat entri yang dikutip bab itu: PDF yang
    # memuat ``[?]`` juga "berhasil" dikompilasi, dan itu bukan buku.
    ((key, _source),) = bibliography_entries(sources)
    assert key not in result.undefined_citations
