"""Tes ingestion: berkas bahan menjadi dokumen bermetadata (§§9–§11).

Seluruh berkas ini berjalan **tanpa satu pun berkas PDF dan tanpa satu pun
panggilan model**. Itu bukan penghematan, melainkan konsekuensi dari bentuk
paketnya: ``markdown_loader`` dan ``latex_loader`` menerima ``str`` dan
mengembalikan potongan, sedangkan keputusan OCR adalah fungsi murni atas daftar
teks halaman. Yang tersisa — yaitu benar-benar membuka PDF — diuji dengan
memalsukan dua pintunya (``read_page_texts`` dan ``ocr_documents``), sehingga
percabangan jalur §10 dapat diperiksa sampai ke pesan galatnya.

Yang paling penting diuji di sini bukan "apakah teksnya terbaca", melainkan
**apa yang tidak boleh hilang**: nomor halaman (§10), nama berkas, bagian, dan
setiap baris sumber. Bahan yang kehilangan salah satunya tetap menghasilkan bab
yang terbaca lancar — hanya saja kutipannya menunjuk halaman yang tidak memuat
kalimatnya, dan itu tidak dapat diperiksa manusia.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from domain.document import Document, SourceType
from domain.errors import InputError
from ingestion import loader as ingestion_loader
from ingestion.document_normalizer import (
    MAX_BLANK_RUN,
    SPACE_COLLAPSING_SOURCES,
    normalize_document,
    normalize_documents,
    normalize_text,
)
from ingestion.latex_loader import (
    SECTION_SEPARATOR,
    STRUCTURAL_ENVIRONMENTS,
    parse_latex,
)
from ingestion.loader import (
    IngestedFile,
    OcrRoute,
    documents_of,
    load_documents,
    load_reference_files,
)
from ingestion.markdown_loader import parse_markdown, parse_plain_text
from ingestion.pdf_loader import (
    DEFAULT_OCR_MIN_CHARS,
    _page_text,
    documents_from_pages,
    needs_ocr,
    read_page_texts,
)
from ingestion.sources import iter_reference_files, read_text
from tests.fakes.chat_models import ScriptedChatModel


def _document(text: str, **overrides: object) -> Document:
    """Satu potongan yang sah; metadata apa pun dapat ditimpa per tes."""
    payload: dict[str, object] = {
        "document_id": "compiler.pdf",
        "filename": "compiler.pdf",
        "source_type": SourceType.PDF,
        "text": text,
    }
    payload.update(overrides)
    return Document.model_validate(payload)


def _write(path: Path, text: str) -> Path:
    """Tulis berkas uji, lalu kembalikan jalurnya."""
    path.write_text(text, encoding="utf-8")
    return path


def _content_lines(text: str) -> list[str]:
    """Baris yang berisi sesuatu — pembanding untuk pemeriksaan keutuhan sumber."""
    return [line for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# parse_markdown — judul menjadi bagian (§9)
# ---------------------------------------------------------------------------
def test_a_markdown_heading_becomes_the_section_of_its_body() -> None:
    """Judul adalah metadata ``section`` yang diminta §9 — satu-satunya hal yang memberi tahu
    pembaca kutipan dari bagian mana sebuah paragraf berasal."""
    documents = parse_markdown("# Analisis Leksikal\n\nToken adalah satuan terkecil.\n",
                               filename="bahasa.md")

    assert len(documents) == 1
    assert documents[0].section == "Analisis Leksikal"
    assert documents[0].text == "Analisis Leksikal\n\nToken adalah satuan terkecil."


def test_the_heading_text_is_part_of_the_body() -> None:
    """Menaruhnya hanya di metadata membuat kata judul tidak dapat ditemukan retrieval.

    Padahal pertanyaan penulis bab sering berbunyi persis seperti judul bagian.
    """
    document = parse_markdown("## Notasi Big-O\n\nO(n) berarti linear.\n", filename="a.md")[0]

    assert "Notasi Big-O" in document.text


def test_several_headings_become_several_documents_in_order() -> None:
    documents = parse_markdown("# A\n\nisi a\n\n# B\n\nisi b\n", filename="a.md")

    assert [document.section for document in documents] == ["A", "B"]
    assert [document.text for document in documents] == ["A\n\nisi a", "B\n\nisi b"]


def test_a_heading_without_a_body_is_dropped() -> None:
    """Judul yang menggantung tidak dapat dikutip — tidak ada isinya.

    Menanamnya ke indeks hanya menghasilkan hasil pencarian yang menunjuk halaman kosong.
    """
    documents = parse_markdown("# Kosong\n\n# Terisi\n\nisi\n", filename="a.md")

    assert [document.section for document in documents] == ["Terisi"]


def test_markdown_without_a_heading_is_one_document_without_a_section() -> None:
    documents = parse_markdown("Kalimat tanpa judul.\n", filename="a.md")

    assert len(documents) == 1
    assert documents[0].section == ""
    assert documents[0].text == "Kalimat tanpa judul."
    assert documents[0].source_type is SourceType.MARKDOWN


def test_paragraph_breaks_inside_a_section_survive() -> None:
    """Chunker memotong pada baris kosong; merapikannya di sini memindahkan keputusan itu."""
    document = parse_markdown("# A\n\nsatu\n\ndua\n", filename="a.md")[0]

    assert document.text == "A\n\nsatu\n\ndua"


def test_the_document_identity_of_markdown_is_its_filename() -> None:
    document = parse_markdown("# A\n\nisi\n", filename="bahasa.md")[0]

    assert document.document_id == "bahasa.md"
    assert document.filename == "bahasa.md"


def test_blank_markdown_yields_nothing() -> None:
    assert parse_markdown("   \n\n", filename="a.md") == ()
    assert parse_markdown("", filename="a.md") == ()


# ---------------------------------------------------------------------------
# parse_plain_text — berkas tanpa struktur
# ---------------------------------------------------------------------------
def test_plain_text_is_one_document_without_a_section() -> None:
    documents = parse_plain_text("Catatan lepas.\n", filename="catatan.txt")

    assert len(documents) == 1
    assert documents[0].section == ""
    assert documents[0].text == "Catatan lepas."
    assert documents[0].source_type is SourceType.TEXT


def test_the_first_line_of_a_plain_file_is_not_treated_as_a_title() -> None:
    """Berbeda dari Markdown: tidak ada judul sama sekali, jadi tidak ada yang ditebak."""
    document = parse_plain_text("Baris pertama.\nBaris kedua.\n", filename="a.txt")[0]

    assert document.section == ""
    assert document.text == "Baris pertama.\nBaris kedua."


def test_empty_plain_text_yields_nothing() -> None:
    assert parse_plain_text("   \n\t\n", filename="a.txt") == ()


# ---------------------------------------------------------------------------
# parse_latex — struktur dikenali, bukan isi dikarang (§11)
# ---------------------------------------------------------------------------
#: Sumber contoh yang memuat bagian bersarang, satu lingkungan struktural, dan
#: pemisah paragraf — tiga hal yang berperilaku berbeda saat dipotong.
LATEX_SOURCE = (
    "\\chapter{Bab 2}\n"
    "\n"
    "Bab ini membahas token.\n"
    "\n"
    "\\section{Token}\n"
    "\\label{sec:token}\n"
    "Token adalah satuan terkecil.\n"
    "\n"
    "\\begin{definition}[Finite Automaton]\n"
    "Mesin yang mengenali bahasa reguler.\n"
    "\\end{definition}\n"
    "\n"
    "Sisa bab.\n"
)


def test_every_source_line_survives_the_split() -> None:
    """Invarian anti-karangan: parser memilih di mana **memotong**, tidak pernah menulis ulang.

    Setiap baris sumber yang berisi teks harus muncul verbatim, sekali, dan dalam
    urutan yang sama. Parser yang menyusun ulang teks akan mengubah kutipan — dan
    kutipan yang isinya berbeda dari berkas aslinya tidak dapat diperiksa manusia.
    """
    documents = parse_latex(LATEX_SOURCE, filename="bab.tex")

    assert [line for document in documents for line in _content_lines(document.text)] == (
        _content_lines(LATEX_SOURCE)
    )


def test_pieces_joined_back_reproduce_a_tightly_written_source() -> None:
    """Pemisah antar-potongan hanyalah baris baru — tanpa itu, ia tidak dihilangkan."""
    source = "\\chapter{Bab 2}\nTeks bab.\n\\section{Token}\nTeks token.\n"

    documents = parse_latex(source, filename="bab.tex")

    assert "\n".join(document.text for document in documents) == source.rstrip("\n")


def test_nested_sections_are_reported_as_a_path() -> None:
    source = (
        "\\chapter{Bab 2}\nTeks.\n"
        "\\section{Token}\nIsi token.\n"
        "\\subsection{Regex}\nIsi regex.\n"
    )

    documents = parse_latex(source, filename="bab.tex")

    assert [document.section for document in documents] == [
        "Bab 2",
        f"Bab 2{SECTION_SEPARATOR}Token",
        f"Bab 2{SECTION_SEPARATOR}Token{SECTION_SEPARATOR}Regex",
    ]


def test_a_sibling_chapter_does_not_nest_below_the_previous_one() -> None:
    """Tingkat dipakai memotong tumpukan judul — lihat ``SECTION_LEVELS``."""
    source = (
        "\\chapter{Bab 1}\nA.\n\\section{Satu}\nB.\n"
        "\\chapter{Bab 2}\nC.\n\\section{Dua}\nD.\n"
    )

    documents = parse_latex(source, filename="bab.tex")

    assert [document.section for document in documents] == [
        "Bab 1",
        f"Bab 1{SECTION_SEPARATOR}Satu",
        "Bab 2",
        f"Bab 2{SECTION_SEPARATOR}Dua",
    ]


def test_a_starred_section_command_is_recognized() -> None:
    """Lampiran dan RPS kerap memakai bentuk berbintang.

    Kalau tidak dikenali, seluruh isinya jatuh ke potongan sebelumnya — dan
    bagian itu lalu muncul di dalam kutipan sebagai bagian dari bab yang lain.
    """
    documents = parse_latex("\\chapter*{Lampiran}\nIsi lampiran.\n", filename="bab.tex")

    assert documents[0].section == "Lampiran"


def test_a_section_without_a_title_falls_back_to_the_command_name() -> None:
    """Lebih baik nama perintahnya daripada bagian yang tampak tanpa nama."""
    documents = parse_latex("\\section{}\nIsi.\n", filename="bab.tex")

    assert documents[0].section == "section"


def test_a_structural_environment_becomes_its_own_piece() -> None:
    """§11: ``definition``, ``example``, ``theorem``, dan kawan-kawan adalah satuan tersendiri."""
    source = (
        "\\section{Token}\n"
        "\\begin{definition}[FA]\n"
        "Sebuah FA.\n"
        "\\end{definition}\n"
        "Sisa.\n"
    )

    documents = parse_latex(source, filename="bab.tex")

    assert [document.section for document in documents] == [
        "Token",
        f"Token{SECTION_SEPARATOR}definition",
        "Token",
    ]


def test_the_closing_command_stays_inside_its_environment() -> None:
    """Memotong sebelum ``\\end`` akan meninggalkan potongan yang tampak belum selesai."""
    source = "\\section{Token}\n\\begin{definition}[FA]\nSebuah FA.\n\\end{definition}\n"

    document = parse_latex(source, filename="bab.tex")[1]

    assert document.text.endswith("\\end{definition}")


def test_a_non_structural_environment_does_not_split_the_piece() -> None:
    """``itemize`` bukan batas: memecahnya menghasilkan serpihan yang tidak dapat dibaca."""
    source = "\\section{Daftar}\n\\begin{itemize}\n\\item satu\n\\item dua\n\\end{itemize}\n"

    documents = parse_latex(source, filename="bab.tex")

    assert "itemize" not in STRUCTURAL_ENVIRONMENTS
    assert len(documents) == 1
    assert documents[0].text == source.strip()


def test_a_section_command_inside_a_line_does_not_split_the_piece() -> None:
    """Perintah bagian hanya dikenali di awal baris.

    Tanpa syarat itu, setiap ``\\ref{sec:...}`` di tengah kalimat akan memotong
    potongan — dan kalimat yang terbelah dua tidak dapat dikutip sebagai satu kesatuan.
    """
    source = "\\section{Notasi}\nLihat \\section{Bukan judul} dan \\ref{sec:x}.\n"

    documents = parse_latex(source, filename="bab.tex")

    assert len(documents) == 1
    assert documents[0].section == "Notasi"


def test_latex_documents_carry_the_latex_source_type() -> None:
    document = parse_latex("\\section{A}\nIsi.\n", filename="bab.tex")[0]

    assert document.source_type is SourceType.LATEX
    assert document.document_id == "bab.tex"


def test_blank_latex_yields_nothing() -> None:
    assert parse_latex("\n\n", filename="bab.tex") == ()


# ---------------------------------------------------------------------------
# normalize — yang disamakan hanya yang tidak pernah bermakna sebagai isi
# ---------------------------------------------------------------------------
def test_windows_line_endings_are_normalized() -> None:
    assert normalize_text("a\r\nb\rc\n") == "a\nb\nc"


def test_trailing_spaces_are_removed_without_touching_indentation() -> None:
    """Di Markdown dan LaTeX, lekukan adalah isi: ia yang membedakan blok kode dari prosa."""
    assert normalize_text("teks  \n    indent   \n") == "teks\n    indent"


def test_whitespace_at_the_edges_of_a_piece_is_not_content() -> None:
    """Spasi di tepi potongan hanyalah sisa pemotongan sebelumnya.

    Yang menentukan blok kode adalah lekukan baris-baris **di dalam** potongan,
    dan itulah yang dipertahankan. Tepinya dibuang supaya teks yang dikirim ke
    model tidak membayar spasi yang tidak pernah menjadi kalimat.
    """
    assert normalize_text("    satu\n") == "satu"


def test_at_most_one_blank_line_survives_a_run() -> None:
    """Tiga baris kosong adalah jarak yang lebih lebar, bukan makna yang lebih banyak."""
    assert normalize_text("satu\n\n\n\n\ndua") == "satu\n\ndua"
    assert MAX_BLANK_RUN == 1


def test_a_paragraph_separator_inside_a_section_survives() -> None:
    """Chunker bergantung padanya; menghapusnya menggabungkan dua paragraf menjadi satu."""
    assert normalize_text("satu\n\ndua") == "satu\n\ndua"


def test_collapsing_spaces_inside_a_line_is_opt_in() -> None:
    """PDF dua kolom menyisipkan spasi di tengah kalimat — itu sisa tata letak, bukan isi."""
    assert normalize_text("kata    lain", collapse_spaces=True) == "kata lain"


def test_spaces_inside_a_line_are_kept_by_default() -> None:
    assert normalize_text("kode    blok\n    lebih dalam") == "kode    blok\n    lebih dalam"


def test_pdf_text_loses_the_spaces_left_by_column_layout() -> None:
    document = _document("kata    lain", source_type=SourceType.PDF)

    assert normalize_document(document).text == "kata lain"


def test_ocr_text_is_cleaned_the_same_way_as_pdf_text() -> None:
    """Keduanya lahir dari tata letak halaman, jadi keduanya dirapikan sama."""
    document = _document("kata    lain", source_type=SourceType.PDF_OCR)

    assert normalize_document(document).text == "kata lain"


@pytest.mark.parametrize(
    "source_type", [SourceType.MARKDOWN, SourceType.LATEX, SourceType.TEXT]
)
def test_sources_that_use_indentation_keep_it(source_type: SourceType) -> None:
    document = _document("prosa\n    blok kode", source_type=source_type)

    assert normalize_document(document).text == "prosa\n    blok kode"


def test_a_code_block_inside_a_section_keeps_its_indentation() -> None:
    """Lekukan itu isi, dan ia harus bertahan melewati pemotongan sekaligus perapian."""
    document = parse_markdown("# Contoh\n\n    x = 1\n    y = 2\n", filename="a.md")[0]

    assert normalize_document(document).text == "Contoh\n\n    x = 1\n    y = 2"


def test_the_space_collapsing_set_is_the_documented_one() -> None:
    assert SPACE_COLLAPSING_SOURCES == frozenset({SourceType.PDF, SourceType.PDF_OCR})


def test_normalizing_keeps_every_metadata_field() -> None:
    """Metadata §9 melekat pada potongan; perapian teks tidak boleh menyentuhnya."""
    document = _document(
        "a    b", page=42, section="Token", paragraph=1, source_type=SourceType.PDF
    )

    cleaned = normalize_document(document)

    assert cleaned.model_dump(exclude={"text"}) == document.model_dump(exclude={"text"})


def test_normalize_documents_keeps_the_order_of_the_corpus() -> None:
    documents = (
        _document("b    b", page=2),
        _document("a    a", page=1),
    )

    cleaned = normalize_documents(documents)

    assert isinstance(cleaned, tuple)
    assert [document.page for document in cleaned] == [2, 1]
    assert [document.text for document in cleaned] == ["b b", "a a"]


# ---------------------------------------------------------------------------
# Keputusan OCR — murni, tanpa berkas dan tanpa model (§10)
# ---------------------------------------------------------------------------
def test_a_pdf_without_a_text_layer_needs_ocr() -> None:
    assert needs_ocr(("", "", "")) is True


def test_a_pdf_with_a_text_layer_does_not_need_ocr() -> None:
    """Memanggil jalur vision untuk PDF biasa berarti membayar satu panggilan model per halaman."""
    assert needs_ocr(("x" * 500, "y" * 400)) is False


def test_the_decision_uses_the_average_not_a_single_empty_page() -> None:
    """PDF nyata hampir selalu punya satu halaman kosong — sampul, halaman hak cipta.

    Memeriksa "ada satu halaman yang kosong" akan mengalihkan hampir setiap PDF
    nyata ke jalur vision, dan jalur itu memanggil model berkali-kali per halaman.
    """
    assert needs_ocr(("", "x" * 1000, "y" * 1000)) is False


def test_a_mixed_pdf_below_the_threshold_still_switches_to_vision() -> None:
    """Rata-ratanya yang menentukan; berkas setengah hasil scan tetap terdeteksi."""
    assert needs_ocr(("x" * 50, "y" * 50)) is True


def test_a_pdf_without_pages_never_needs_ocr() -> None:
    """Tidak ada yang dapat dibaca ulang model vision — memanggilnya hanya membuang token."""
    assert needs_ocr(()) is False


def test_a_non_positive_threshold_disables_the_ocr_route() -> None:
    """``0`` di ``config.yaml`` berarti "jangan pernah ke jalur vision"."""
    assert needs_ocr(("", ""), min_chars=0) is False


def test_the_threshold_is_average_characters_per_page() -> None:
    assert DEFAULT_OCR_MIN_CHARS == 80
    assert needs_ocr(("x" * 79,), min_chars=80) is True
    assert needs_ocr(("x" * 80,), min_chars=80) is False


# ---------------------------------------------------------------------------
# documents_from_pages — halaman tidak pernah dibuang (§10)
# ---------------------------------------------------------------------------
def test_pages_become_documents_numbered_from_one() -> None:
    """1-basis adalah cara manusia menyebut halaman, dan itu yang akan dikutip di buku."""
    documents = documents_from_pages(("satu", "dua"), filename="compiler.pdf")

    assert [document.page for document in documents] == [1, 2]


def test_a_blank_page_produces_no_document_and_does_not_shift_the_others() -> None:
    """Vektor dari ketiadaan cocok dengan setiap pertanyaan — jadi halaman kosong dibuang.

    Membuangnya **tidak** boleh menggeser nomor halaman sisanya: kutipan yang
    menunjuk halaman 3 padahal teksnya di halaman 4 adalah kerusakan yang tidak
    terlihat sampai ada orang membuka PDF-nya.
    """
    documents = documents_from_pages(("satu", "   ", "\n\n", "empat"), filename="compiler.pdf")

    assert [document.page for document in documents] == [1, 4]
    assert [document.text for document in documents] == ["satu", "empat"]


def test_page_text_is_stripped_and_the_source_type_is_pdf() -> None:
    document = documents_from_pages(("\n satu \n",), filename="compiler.pdf")[0]

    assert document.text == "satu"
    assert document.source_type is SourceType.PDF
    assert document.document_id == "compiler.pdf"


class _FakePage:
    """Halaman palsu: apa pun yang ``pypdf`` kembalikan, dalam dua bentuk terburuknya."""

    def __init__(self, text: str | None = None, error: BaseException | None = None) -> None:
        self._text = text
        self._error = error

    def extract_text(self) -> str | None:
        if self._error is not None:
            raise self._error
        return self._text


def test_page_text_reads_a_page() -> None:
    assert _page_text(_FakePage("isi halaman")) == "isi halaman"


def test_a_page_that_fails_to_extract_is_empty_not_fatal() -> None:
    """Satu halaman hasil scan di dalam PDF dua ratus halaman adalah keadaan yang wajar.

    Membuang dua ratus halaman karena satu halaman adalah pertukaran yang jelas
    merugikan — dan yang lebih buruk, ia gagal pada berkas yang sebenarnya baik.
    """
    assert _page_text(_FakePage(error=RuntimeError("halaman rusak"))) == ""


def test_a_page_without_extract_text_is_empty() -> None:
    assert _page_text(object()) == ""


def test_a_page_that_returns_none_is_empty() -> None:
    assert _page_text(_FakePage(None)) == ""


def test_reading_a_missing_pdf_names_the_file(tmp_path: Path) -> None:
    """Galat pustaka diterjemahkan menjadi pesan yang menyebut berkas mana yang harus diperbaiki."""
    with pytest.raises(InputError) as excinfo:
        read_page_texts(tmp_path / "hilang.pdf")

    assert "hilang.pdf" in str(excinfo.value)


# ---------------------------------------------------------------------------
# dispatch format — satu-satunya tempat yang tahu akhiran berarti apa
# ---------------------------------------------------------------------------
def test_a_markdown_file_is_parsed_as_markdown(tmp_path: Path) -> None:
    target = _write(tmp_path / "bahasa.md", "# Token\n\nToken adalah satuan terkecil.\n")

    result = load_documents(target)

    assert result.filename == "bahasa.md"
    assert result.used_ocr is False
    assert result.documents[0].section == "Token"
    assert result.documents[0].source_type is SourceType.MARKDOWN


def test_a_latex_file_is_parsed_as_latex(tmp_path: Path) -> None:
    target = _write(tmp_path / "bab.tex", "\\chapter{Bab 2}\nIsi.\n")

    result = load_documents(target)

    assert result.documents[0].source_type is SourceType.LATEX
    assert result.documents[0].section == "Bab 2"


def test_an_unrecognized_suffix_is_read_as_plain_text(tmp_path: Path) -> None:
    target = _write(tmp_path / "catatan.rst", "Catatan lepas.\n")

    result = load_documents(target)

    assert result.documents[0].source_type is SourceType.TEXT
    assert result.documents[0].section == ""


def test_the_suffix_is_matched_case_insensitively(tmp_path: Path) -> None:
    """Berkas yang datang dari Windows sering berakhiran huruf besar."""
    target = _write(tmp_path / "BAB.TEX", "\\section{A}\nIsi.\n")

    assert load_documents(target).documents[0].source_type is SourceType.LATEX


def test_an_ingested_file_reports_the_name_it_will_be_cited_by(tmp_path: Path) -> None:
    """Yang dikutip adalah nama berkas, bukan jalur lengkapnya.

    Jalur absolut di dalam kutipan akan membuat buku menyebut direktori mesin penulisnya.
    """
    item = IngestedFile(path=tmp_path / "dalam" / "compiler.pdf", documents=())

    assert item.filename == "compiler.pdf"
    assert item.used_ocr is False


# ---------------------------------------------------------------------------
# Jalur PDF — dua cabang §10, keduanya tanpa PDF dan tanpa model
# ---------------------------------------------------------------------------
def _fake_pages(monkeypatch: pytest.MonkeyPatch, page_texts: tuple[str, ...]) -> None:
    """Ganti pintu ``pypdf`` dengan teks halaman yang sudah ditentukan."""
    monkeypatch.setattr(ingestion_loader, "read_page_texts", lambda path: page_texts)


def test_a_text_pdf_uses_the_text_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _write(tmp_path / "compiler.pdf", "")
    _fake_pages(monkeypatch, ("x" * 200, "y" * 200))

    result = load_documents(target)

    assert result.used_ocr is False
    assert [document.page for document in result.documents] == [1, 2]
    assert all(document.source_type is SourceType.PDF for document in result.documents)


def test_a_scanned_pdf_without_the_vision_route_fails_and_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PDF hasil scan yang diam-diam menghasilkan nol dokumen adalah kegagalan paling mahal di sini.

    Seluruh bab sesudahnya akan menulis dari korpus yang tampak lengkap padahal
    halaman-halamannya hilang — karena itu keadaannya harus berisik, bukan sunyi.
    """
    target = _write(tmp_path / "scan.pdf", "")
    _fake_pages(monkeypatch, ("", ""))

    with pytest.raises(InputError) as excinfo:
        load_documents(target)

    assert "scan.pdf" in str(excinfo.value)
    assert "vision" in str(excinfo.value)


def test_a_scanned_pdf_goes_through_the_vision_route_when_it_is_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt_library: Any,
) -> None:
    target = _write(tmp_path / "scan.pdf", "")
    _fake_pages(monkeypatch, ("", ""))
    calls: list[tuple[Path, int]] = []

    def fake_ocr(path: object, *, model: object, prompts: object, dpi: int) -> tuple[Document]:
        calls.append((Path(str(path)), dpi))
        return (
            Document(
                document_id="scan.pdf",
                filename="scan.pdf",
                source_type=SourceType.PDF_OCR,
                text="terbaca dari gambar",
                page=1,
            ),
        )

    monkeypatch.setattr(ingestion_loader, "ocr_documents", fake_ocr)
    route = OcrRoute(model=ScriptedChatModel(()), prompts=prompt_library, dpi=200)

    result = load_documents(target, ocr=route)

    assert result.used_ocr is True
    assert result.documents[0].source_type is SourceType.PDF_OCR
    assert calls == [(target, 200)]


def test_the_configured_threshold_decides_which_pdf_route_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambangnya datang dari ``rag.ocr_min_chars_per_page``; ia harus benar-benar dipakai."""
    target = _write(tmp_path / "compiler.pdf", "")
    _fake_pages(monkeypatch, ("x" * 100,))

    assert load_documents(target, ocr_min_chars=50).used_ocr is False

    with pytest.raises(InputError):
        load_documents(target, ocr_min_chars=200)


# ---------------------------------------------------------------------------
# Menemukan berkas dan membacanya
# ---------------------------------------------------------------------------
def test_only_the_whitelisted_suffixes_are_picked_up(tmp_path: Path) -> None:
    """Daftar putih, bukan daftar hitam: ``.gitkeep`` dan berkas catatan tidak menjadi kutipan."""
    (tmp_path / "dalam").mkdir()
    for name in ("a.pdf", "b.md", "c.txt", "d.tex", "e.docx", "f", ".gitkeep"):
        _write(tmp_path / name, "x")
    _write(tmp_path / "dalam" / "g.md", "x")

    found = iter_reference_files(tmp_path)

    assert {path.name for path in found} == {"a.pdf", "b.md", "c.txt", "d.tex", "g.md"}


def test_reference_files_inside_one_directory_come_back_in_a_stable_order(tmp_path: Path) -> None:
    """Urutan pembacaan menentukan urutan potongan di dalam indeks.

    Indeks yang isinya bergantung pada urutan pembacaan direktori akan memberi
    hasil pencarian yang berbeda di dua mesin untuk bahan yang sama.
    """
    for name in ("c.md", "a.md", "b.md"):
        _write(tmp_path / name, "# X\n\nisi\n")

    assert [path.name for path in iter_reference_files(tmp_path)] == ["a.md", "b.md", "c.md"]
    assert iter_reference_files(tmp_path) == iter_reference_files(tmp_path)


def test_a_missing_reference_directory_is_not_an_error(tmp_path: Path) -> None:
    """Yang memutuskan "belum ada bahan" itu wajar atau kegagalan adalah pemanggilnya —
    ``ingest``."""
    assert iter_reference_files(tmp_path / "tidak-ada") == ()
    assert load_reference_files(tmp_path / "tidak-ada") == ()


def test_an_empty_directory_yields_nothing(tmp_path: Path) -> None:
    assert load_reference_files(tmp_path) == ()


def test_reference_files_are_read_with_their_own_parser(tmp_path: Path) -> None:
    (tmp_path / "dalam").mkdir()
    _write(tmp_path / "a.tex", "\\section{A}\nIsi LaTeX.\n")
    _write(tmp_path / "a.md", "# A\n\nIsi Markdown.\n")
    _write(tmp_path / "dalam" / "b.txt", "Isi teks.\n")

    by_name = {item.filename: item for item in load_reference_files(tmp_path)}

    assert set(by_name) == {"a.tex", "a.md", "b.txt"}
    assert by_name["a.tex"].documents[0].source_type is SourceType.LATEX
    assert by_name["a.md"].documents[0].source_type is SourceType.MARKDOWN
    assert by_name["b.txt"].documents[0].source_type is SourceType.TEXT


def test_documents_of_flattens_the_files_without_reordering(tmp_path: Path) -> None:
    files = (
        IngestedFile(
            path=tmp_path / "a.md",
            documents=(_document("a1", page=1), _document("a2", page=2)),
        ),
        IngestedFile(path=tmp_path / "b.md", documents=(_document("b1", page=3),)),
    )

    assert [document.text for document in documents_of(files)] == ["a1", "a2", "b1"]


# ---------------------------------------------------------------------------
# read_text — batas filesystem
# ---------------------------------------------------------------------------
def test_read_text_returns_the_file_contents(tmp_path: Path) -> None:
    target = _write(tmp_path / "a.md", "isi berkas\n")

    assert read_text(target) == "isi berkas\n"


def test_a_file_that_is_not_utf8_fails_and_names_the_file(tmp_path: Path) -> None:
    """``errors="replace"`` akan menghasilkan kutipan yang tampak wajar tetapi tidak sama
    dengan aslinya.

    Tanda tanya di tengah kutipan tidak dapat dicocokkan dengan halaman mana pun,
    sementara penyebabnya — berkasnya bukan UTF-8 — sudah tidak terlihat lagi.
    """
    target = tmp_path / "latin.txt"
    target.write_bytes("caf\xe9".encode("latin-1"))

    with pytest.raises(InputError) as excinfo:
        read_text(target)

    assert "latin.txt" in str(excinfo.value)
    assert "utf-8" in str(excinfo.value)
