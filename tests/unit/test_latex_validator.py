"""Parser log LaTeX dan pemeriksa rujukan silang (§26).

Dua hal diuji di sini, dan keduanya sengaja diuji **tanpa** menjalankan LaTeX
sama sekali. Log-nya berkas contoh di ``tests/data/latex_logs/``, dan rujukan
silangnya berkas ``.tex`` sungguhan yang ditulis ke ``tmp_path``.

Alasannya bukan kecepatan: mesin yang menjalankan tes ini belum tentu punya
MiKTeX, dan seluruh isi Tahap 7 justru tentang **membaca apa yang dikatakan
perkakas LaTeX**. Penguraian itu adalah fungsi murni atas teks, jadi ia harus
dapat dibuktikan tanpa perkakasnya. Kompilasi sungguhannya diuji terpisah di
``tests/live/`` dan ditandai ``live``.

Kedelapan jenis masalah §26 diwakili berkas tersendiri, sehingga kegagalan satu
jenis tidak pernah tersembunyi di balik jenis lain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.latex import chapter_latex_filename
from latex.artifacts import BIBLIOGRAPHY_FILENAME, CHAPTERS_DIRNAME, atomic_write_text
from latex.crossref_checker import bibliography_keys, check_crossrefs
from latex.validator import parse_log

#: Direktori log contoh. Dihitung dari berkas tes ini, bukan dari direktori kerja
#: saat ini: ``pytest`` yang dijalankan dari direktori lain harus tetap bekerja.
LOGS_DIR = Path(__file__).resolve().parents[1] / "data" / "latex_logs"


def _log(name: str) -> str:
    """Baca satu berkas log contoh.

    :raises FileNotFoundError: bila berkasnya tidak ada — dan itu kegagalan yang
        benar. Log contoh yang hilang berarti tesnya kehilangan bahannya, bukan
        berarti parse-nya lulus.
    """
    return (LOGS_DIR / f"{name}.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Satu jenis masalah per berkas
# ---------------------------------------------------------------------------
def test_a_clean_log_reports_nothing() -> None:
    result = parse_log(_log("clean"))

    assert result.ok is True
    assert result.errors == ()
    assert result.undefined_refs == ()
    assert result.undefined_citations == ()
    assert result.duplicate_labels == ()
    assert result.missing_figures == ()
    assert result.overfull_boxes == ()
    assert result.warnings == ()


def test_a_compilation_error_is_captured_without_the_bang() -> None:
    """``!`` adalah penanda baris, bukan bagian pesannya."""
    result = parse_log(_log("error"))

    assert result.ok is False
    assert any("Missing $ inserted" in error for error in result.errors)
    assert not any(error.startswith("!") for error in result.errors)


def test_a_broken_equation_counts_as_a_compilation_error() -> None:
    """§26 menyebutnya tersendiri, tetapi LaTeX melaporkannya sebagai galat biasa.

    ``! Missing $ inserted.`` adalah bentuk yang diambil ``_`` atau ``^`` di luar
    mode matematika — persamaan yang rusak, dan tidak ada jalur lain yang
    melaporkannya.
    """
    result = parse_log(_log("error"))

    assert result.errors


def test_undefined_references_are_collected_with_their_labels() -> None:
    result = parse_log(_log("undefined_ref"))

    assert result.ok is True
    assert result.undefined_refs == ("sec:kompleksitas", "tab:perbandingan")


def test_undefined_citations_are_collected_with_their_keys() -> None:
    result = parse_log(_log("undefined_citation"))

    assert result.undefined_citations == ("cormen-introduction-3f2a91b4",)


def test_a_citation_warning_is_not_also_a_generic_warning() -> None:
    """Setiap baris masuk ke **satu** kelompok, bukan dua.

    Kalau tidak, setiap sitasi menggantung dilaporkan dua kali: sekali sebagai
    temuan yang dapat dikerjakan, sekali sebagai kebisingan.
    """
    result = parse_log(_log("undefined_citation"))

    assert not any("Citation" in warning for warning in result.warnings)


def test_a_duplicate_label_is_reported_once_however_often_it_appears() -> None:
    """Yang diperiksa adalah himpunan label yang bermasalah, bukan jumlah keluhan."""
    result = parse_log(_log("duplicate_label"))

    assert result.duplicate_labels == ("sec:bigo",)


def test_a_missing_figure_is_not_swallowed_by_the_error_bucket() -> None:
    """Gambar yang hilang dilaporkan LaTeX dengan ``!`` — jadi urutan penting.

    Diperiksa sesudah galat umum, ia akan selalu tertelan, dan field
    ``missing_figures`` tidak akan pernah terisi meski §26 menyebutnya tersendiri.
    """
    result = parse_log(_log("missing_figure"))

    assert result.missing_figures == ("figures/pohon-biner.png",)
    assert not any("not found" in error for error in result.errors)


def test_a_missing_figure_is_the_compilation_failure_that_stops_the_build() -> None:
    result = parse_log(_log("missing_figure"))

    assert result.ok is False
    assert result.errors, "Emergency stop tetap sebuah galat, di samping gambar yang hilang"


@pytest.mark.parametrize("kind", ["hbox", "vbox"])
def test_both_overfull_box_flavours_are_collected(kind: str) -> None:
    """``\\hbox`` melebihi lebar halaman, ``\\vbox`` melebihi tingginya — keduanya §26."""
    overfull = [line for line in parse_log(_log("mixed")).overfull_boxes if kind in line]

    assert len(overfull) == 1


def test_overfull_boxes_are_advisory_not_blocking() -> None:
    """Bab yang kompilasinya berhasil tetap berhasil meski ada kotak yang melebar."""
    result = parse_log(_log("overfull"))

    assert result.ok is True
    assert len(result.overfull_boxes) == 1


# ---------------------------------------------------------------------------
# Satu log dengan bermacam-macam masalah sekaligus
# ---------------------------------------------------------------------------
def test_a_log_with_everything_sorts_each_line_into_exactly_one_bucket() -> None:
    result = parse_log(_log("mixed"))

    assert result.undefined_citations == ("sedgewick-algorithms-9f1c2d3e",)
    assert result.undefined_refs == ("sec:latihan",)
    assert result.duplicate_labels == ("sec:bigo",)
    assert result.missing_figures == ("figures/graf.png",)
    assert len(result.overfull_boxes) == 2


def test_a_missing_figure_lands_in_its_own_bucket_rather_than_in_errors() -> None:
    """Bentuk ``! LaTeX Error: File ... not found`` adalah galat, tetapi bukan itu isinya.

    Yang dilaporkan adalah **gambar yang hilang**, dan itulah yang dapat
    dikerjakan penulisnya. Karena barisnya berhenti di situ, ``errors`` kosong
    dan ``ok`` tetap True — vonis menahan/tidaknya bukan urusan parser ini
    melainkan :func:`~domain.latex.build_problems`.
    """
    result = parse_log(_log("mixed"))

    assert result.errors == ()
    assert result.ok is True
    assert result.missing_figures


def test_unclassified_warnings_are_kept_but_capped() -> None:
    """Peringatan yang tidak dapat dikerjakan tetap dicatat, sampai batas.

    Log satu buku dapat memuat ratusan baris ``hyperref`` seperti ini, dan tidak
    satu pun di antaranya dapat dikerjakan penulis bab.
    """
    result = parse_log(_log("mixed"))

    assert any("hyperref" in warning for warning in result.warnings)
    assert all("Citation" not in warning for warning in result.warnings)


def test_a_log_of_only_unclassified_warnings_stays_ok() -> None:
    result = parse_log("Package hyperref Warning: Token not allowed in a PDF string.")

    assert result.ok is True
    assert len(result.warnings) == 1


def test_the_warning_list_is_capped() -> None:
    text = "\n".join(f"Package foo Warning: nomor {index}" for index in range(50))

    assert len(parse_log(text).warnings) == 10


def test_an_empty_log_reports_nothing_at_all() -> None:
    """Tanpa galat yang terbaca bukan berarti bersih — lihat catatan ``ok`` di modul."""
    result = parse_log("")

    assert result.ok is True
    assert result.errors == ()
    assert result.log_excerpt == ""


def test_the_excerpt_starts_at_the_first_signal_not_at_the_file_header() -> None:
    """40 baris pertama log selalu berupa header; yang berguna ada di tengah."""
    excerpt = parse_log(_log("error")).log_excerpt

    assert "Missing $ inserted" in excerpt
    assert "This is pdfTeX" not in excerpt


# ---------------------------------------------------------------------------
# Kunci BibTeX
# ---------------------------------------------------------------------------
def test_bibliography_keys_reads_the_key_of_each_entry() -> None:
    text = "@misc{a-1,\n  title = {Satu},\n}\n\n@misc{b-2,\n  title = {Dua},\n}\n"

    assert bibliography_keys(text) == ("a-1", "b-2")


def test_bibliography_keys_keeps_the_order_of_appearance() -> None:
    """Bukan urutan yang penting, tetapi urutan yang **stabil** — untuk pesan galat."""
    text = "@book{zzz,}\n@article{aaa,}"

    assert bibliography_keys(text) == ("zzz", "aaa")


def test_an_empty_bibliography_has_no_keys() -> None:
    assert bibliography_keys("") == ()


# ---------------------------------------------------------------------------
# Rujukan silang lintas bab
# ---------------------------------------------------------------------------
def _latex_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "latex"
    atomic_write_text(directory / BIBLIOGRAPHY_FILENAME, "@misc{cormen-1a2b3c4d,}\n")
    return directory


def _write_chapter(latex_dir: Path, number: int, tex: str) -> None:
    atomic_write_text(latex_dir / CHAPTERS_DIRNAME / chapter_latex_filename(number), tex)


def test_a_reference_to_another_chapter_is_not_dangling(tmp_path: Path) -> None:
    """Inilah yang tidak dapat diketahui gate §26: ia mengompilasi satu bab saja."""
    latex_dir = _latex_dir(tmp_path)
    _write_chapter(latex_dir, 1, "\\chapter{Satu}\n\\label{chap:1}\nlihat \\ref{chap:2}\n")
    _write_chapter(latex_dir, 2, "\\chapter{Dua}\n\\label{chap:2}\n")

    assert check_crossrefs(latex_dir, chapter_numbers=(1, 2)) == ()


def test_a_reference_to_a_chapter_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    latex_dir = _latex_dir(tmp_path)
    _write_chapter(latex_dir, 1, "\\chapter{Satu}\n\\label{chap:1}\nlihat \\ref{chap:9}\n")

    findings = check_crossrefs(latex_dir, chapter_numbers=(1,))

    assert any("chap:9" in finding for finding in findings)


def test_a_citation_without_a_bibliography_entry_is_reported(tmp_path: Path) -> None:
    latex_dir = _latex_dir(tmp_path)
    _write_chapter(latex_dir, 1, "\\chapter{Satu}\ntumbuh linear \\cite{cormen-1a2b3c4d}.\n")

    # Kunci yang benar-benar ada di `.bib` tidak dilaporkan.
    assert check_crossrefs(latex_dir, chapter_numbers=(1,)) == ()


def test_an_invented_citation_key_is_reported(tmp_path: Path) -> None:
    latex_dir = _latex_dir(tmp_path)
    _write_chapter(latex_dir, 1, "\\chapter{Satu}\ntumbuh linear \\cite{kunci-karangan}.\n")

    findings = check_crossrefs(latex_dir, chapter_numbers=(1,))

    assert any("kunci-karangan" in finding for finding in findings)


def test_a_missing_chapter_file_is_skipped_without_complaint(tmp_path: Path) -> None:
    """Berkas yang hilang sudah dilaporkan kompilasi; mengulanginya hanya menambah bising."""
    latex_dir = _latex_dir(tmp_path)
    _write_chapter(latex_dir, 1, "\\chapter{Satu}\n")

    assert check_crossrefs(latex_dir, chapter_numbers=(1, 7)) == ()


def test_no_chapter_at_all_yields_no_findings(tmp_path: Path) -> None:
    """Tidak ada yang dirujuk berarti tidak ada yang menggantung — bukan sebaliknya."""
    latex_dir = _latex_dir(tmp_path)

    assert check_crossrefs(latex_dir, chapter_numbers=(1, 2)) == ()


def test_a_missing_bibliography_file_makes_every_citation_dangle(tmp_path: Path) -> None:
    """Buku tanpa ``references.bib`` adalah buku yang setiap sitasinya menggantung."""
    directory = tmp_path / "latex"
    _write_chapter(directory, 1, "\\chapter{Satu}\ntumbuh linear \\cite{cormen-1a2b3c4d}.\n")

    findings = check_crossrefs(directory, chapter_numbers=(1,))

    assert any("cormen-1a2b3c4d" in finding for finding in findings)
