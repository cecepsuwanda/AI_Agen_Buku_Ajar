"""Tes pemotongan bahan menjadi potongan (§10, §13) — MURNI.

Berkas ini menguji satu fungsi yang tampak sepele dan justru menyimpan dua
kerusakan yang keduanya **tak terlihat** setelah terjadi:

1. **Potongan yang melebihi anggaran.** Model embedding memotong masukannya
   diam-diam. Melewati batasnya karena itu tidak menghasilkan galat — ia
   menghasilkan bahan yang separuhnya **tidak pernah tertanam**, sehingga tidak
   akan pernah ditemukan kembali oleh retrieval mana pun. Halaman yang tidak
   dapat ditemukan tidak akan pernah dikutip, dan tidak ada satu pun tanda di
   keluaran bahwa ia hilang.
2. **Potongan yang kembar.** Tumpang tindih yang membawa seluruh isi potongan
   sebelumnya menghasilkan potongan yang tidak menambahkan apa pun: satu
   pemanggilan embedding dibayar untuk menanam teks yang sudah tertanam.

Karena itu yang diuji di sini bukan "apakah hasilnya masuk akal", melainkan
invarian yang harus benar untuk **teks apa pun**: tidak ada potongan yang lebih
panjang daripada anggarannya, tidak ada paragraf yang hilang, tidak ada dua
potongan berturut-turut yang isinya sama, dan setiap paragraf dapat ditelusuri
kembali ke halaman asalnya.
"""

from __future__ import annotations

import pytest

from domain.document import Document, SourceType, chunk_id
from rag.chunker import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    chunk_document,
    chunk_documents,
    chunk_text,
    paragraphs,
)


def paragraph(tag: str, size: int) -> str:
    """Paragraf berpenanda ``tag`` di awal dan panjang **persis** ``size``."""
    assert size > len(tag) + 1
    return tag + " " + "a" * (size - len(tag) - 1)


def document(text: str, **overrides: object) -> Document:
    """Dokumen satu halaman yang sah, dengan bagian yang dapat diatur per tes."""
    payload: dict[str, object] = {
        "document_id": "compiler.pdf",
        "filename": "compiler.pdf",
        "source_type": SourceType.PDF,
        "text": text,
        "page": 42,
        "section": "Finite Automata",
    }
    payload.update(overrides)
    return Document.model_validate(payload)


# ---------------------------------------------------------------------------
# 1. Paragraf
# ---------------------------------------------------------------------------
def test_paragraphs_split_on_blank_lines() -> None:
    assert paragraphs("Satu.\n\nDua.\n\nTiga.") == ("Satu.", "Dua.", "Tiga.")


def test_empty_paragraphs_are_dropped_not_counted() -> None:
    """Paragraf kosong tidak membawa isi, tetapi ia menggeser indeks paragraf.

    Indeks itulah yang menjadi bagian identitas potongan, jadi paragraf kosong
    yang ikut dihitung akan membuat identitas potongan bergantung pada
    spasi kosong — dan ``ingest`` yang diulang pada berkas yang sama dengan
    spasi berbeda akan menghasilkan identitas yang berbeda pula.
    """
    assert paragraphs("Satu.\n\n\n\n   \n\nDua.") == ("Satu.", "Dua.")


def test_paragraphs_are_stripped_of_surrounding_whitespace() -> None:
    assert paragraphs("  Satu.  \n\n\tDua. ") == ("Satu.", "Dua.")


def test_carriage_returns_are_normalized() -> None:
    """``pypdf`` mengembalikan ``\\r\\n`` pada sebagian berkas.

    Tanpa normalisasi, paragraf masih membawa ``\\r`` yang tidak terlihat saat
    dikutip — dan kutipan yang tidak sama persis dengan halaman aslinya tidak
    dapat diperiksa manusia.
    """
    assert paragraphs("Satu.\r\n\r\nDua.") == ("Satu.", "Dua.")


def test_a_text_without_any_paragraph_separator_is_one_paragraph() -> None:
    assert paragraphs("Satu baris saja.") == ("Satu baris saja.",)


# ---------------------------------------------------------------------------
# 2. Teks kosong dan teks pendek
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["", "   ", "\n\n\n", "\r\n \r\n"])
def test_text_without_content_produces_no_chunks(text: str) -> None:
    """Potongan kosong akan menanam vektor dari ketiadaan — dan vektor semacam itu
    cocok dengan segalanya."""
    assert chunk_text(text) == ()


def test_a_short_text_becomes_one_chunk_starting_at_paragraph_zero() -> None:
    assert chunk_text("Hanya satu paragraf.") == ((0, "Hanya satu paragraf."),)


def test_the_indices_returned_are_paragraph_indices_of_the_units() -> None:
    """Indeks yang dikembalikan harus menunjuk unit **pertama** potongan itu.

    Diuji dengan membangun paragraf yang tidak muat berdua, sehingga setiap
    potongan berisi tepat satu paragraf — dan indeksnya harus sama dengan
    nomor paragraf itu, bukan nomor potongan.
    """
    text = "\n\n".join(paragraph(f"P{n}", 60) for n in range(3))

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=20)

    assert [index for index, _ in chunks] == [0, 1, 2]
    for index, chunk in chunks:
        assert chunk.startswith(f"P{index}")


# ---------------------------------------------------------------------------
# 3. Invarian anggaran
# ---------------------------------------------------------------------------
def test_no_chunk_ever_exceeds_the_budget() -> None:
    """Ini invarian yang paling penting: model embedding memotong kelebihannya diam-diam."""
    text = "\n\n".join(paragraph(f"P{n}", 90) for n in range(8))

    chunks = chunk_text(text, chunk_chars=200, overlap_chars=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for _, chunk in chunks)


def test_no_paragraph_is_lost_along_the_way() -> None:
    """Setiap paragraf harus muncul **utuh** di suatu potongan.

    Ukuran paragrafnya sengaja seluruhnya di bawah anggaran: yang diuji di sini
    adalah pengemasannya, bukan pemotongan paksa. Paragraf yang lebih panjang
    daripada anggaran memang harus dibelah — dan bahwa belahannya tidak
    kehilangan satu karakter pun diuji tersendiri di bagian 5.
    """
    pieces = [paragraph(f"P{n}", 30 + n * 10) for n in range(10)]
    text = "\n\n".join(pieces)

    chunks = chunk_text(text, chunk_chars=150, overlap_chars=40)

    for piece in pieces:
        assert any(piece in chunk for _, chunk in chunks), f"hilang: {piece[:20]}"


def test_no_two_consecutive_chunks_are_identical() -> None:
    """Tumpang tindih diizinkan; potongan kembar tidak — ia token yang dibayar dua kali."""
    text = "\n\n".join(paragraph(f"P{n}", 40) for n in range(6))

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=95)

    texts = [chunk for _, chunk in chunks]
    assert len(set(texts)) == len(texts)


def test_a_generous_overlap_still_never_carries_the_whole_previous_chunk() -> None:
    """Tumpang tindih yang memakan seluruh anggaran berarti tidak ada kemajuan sama sekali."""
    text = "\n\n".join(paragraph(f"P{n}", 40) for n in range(4))

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=100)

    first = chunks[0][1]
    assert "P0" in first
    assert "P0" not in chunks[1][1], "potongan berikutnya harus melepas paragraf pertama"


# ---------------------------------------------------------------------------
# 4. Tumpang tindih
# ---------------------------------------------------------------------------
def test_the_last_paragraph_of_a_chunk_opens_the_next_one() -> None:
    """Kalimat definisi jarang jatuh tepat di tengah potongan — itulah alasan tumpang tindih."""
    text = "\n\n".join(paragraph(f"P{n}", 40) for n in range(4))

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=45)

    assert chunks[0][1].endswith(paragraph("P1", 40))
    assert chunks[1][1].startswith(paragraph("P1", 40))
    assert chunks[1][0] == 1, "indeks menunjuk paragraf yang dibawa, bukan yang baru"


def test_zero_overlap_means_every_chunk_is_fresh_material() -> None:
    text = "\n\n".join(paragraph(f"P{n}", 40) for n in range(4))

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=0)

    assert chunks[0][1] == "\n\n".join([paragraph("P0", 40), paragraph("P1", 40)])
    assert chunks[1][0] == 2, "tanpa tumpang tindih, potongan kedua mulai dari paragraf ketiga"
    assert "P0" not in chunks[1][1]
    assert "P1" not in chunks[1][1]


def test_an_overlap_too_large_for_the_next_unit_is_dropped_entirely() -> None:
    """Tumpang tindih yang tidak muat bersama unit berikutnya hanya menghasilkan ekor kembar.

    Keadaannya konkret: potongan sebelumnya ditutup karena unit berikutnya
    hampir sebesar seluruh anggaran. Membawa ekornya berarti potongan berikutnya
    harus ditutup sebelum satu kata baru pun masuk.
    """
    big = chunk_text(
        "\n\n".join([paragraph("P0", 40), paragraph("P1", 90), paragraph("P2", 90)]),
        chunk_chars=100,
        overlap_chars=40,
    )

    assert all(len(chunk) <= 100 for _, chunk in big)
    assert big[1][1] == paragraph("P1", 90), "tidak ada ekor yang dibawa"


# ---------------------------------------------------------------------------
# 5. Paragraf raksasa
# ---------------------------------------------------------------------------
def test_a_paragraph_longer_than_the_budget_is_split_by_force() -> None:
    """Sebagian PDF menghasilkan "paragraf" sepanjang halaman karena pemisahnya tidak terbaca.

    Membiarkannya utuh berarti model memotongnya diam-diam, dan bagian yang
    terpotong itu tidak akan pernah tertanam.
    """
    chunks = chunk_text("a" * 250, chunk_chars=100, overlap_chars=20)

    assert [chunk for _, chunk in chunks] == ["a" * 100, "a" * 100, "a" * 50]


def test_a_forced_split_prefers_a_word_boundary() -> None:
    """Yang terbelah harus antar-kata; potongan yang dimulai di tengah kata tidak
    menolong siapa pun."""
    text = "kata " * 40

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=20)

    pieces = [chunk for _, chunk in chunks]
    assert len(pieces) > 1
    assert " ".join(pieces) == " ".join(text.split())


def test_a_forced_split_loses_no_character_of_the_paragraph() -> None:
    """Belahannya harus menyusun ulang paragrafnya persis, hanya spasinya yang dirapikan."""
    text = "kata " * 40

    chunks = chunk_text(text, chunk_chars=100, overlap_chars=0)

    assert " ".join(chunk for _, chunk in chunks) == " ".join(text.split())


# ---------------------------------------------------------------------------
# 6. Document → Document
# ---------------------------------------------------------------------------
def test_chunking_a_document_keeps_every_piece_of_metadata() -> None:
    """§10: nomor halaman tidak boleh dibuang — larangan itu hanya bermakna pada potongan.

    Yang dikutip kelak adalah potongan, bukan berkasnya. Metadata yang tinggal
    di dokumen induk tidak akan pernah sampai ke citation checker.
    """
    source = document("Satu.\n\nDua.\n\nTiga.")

    chunks = chunk_document(source, chunk_chars=10, overlap_chars=0)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.document_id == "compiler.pdf"
        assert chunk.filename == "compiler.pdf"
        assert chunk.source_type is SourceType.PDF
        assert chunk.page == 42
        assert chunk.section == "Finite Automata"


def test_each_chunk_gets_its_own_paragraph_index() -> None:
    chunks = chunk_document(document("Satu.\n\nDua."), chunk_chars=10, overlap_chars=0)

    assert [chunk.paragraph for chunk in chunks] == [0, 1]


def test_each_chunk_is_identifiable_on_its_own() -> None:
    """Dua potongan dari halaman yang sama hanya dapat dibedakan lewat indeks paragrafnya."""
    chunks = chunk_document(document("Satu.\n\nDua."), chunk_chars=10, overlap_chars=0)

    assert len({chunk_id(chunk) for chunk in chunks}) == len(chunks)


def test_an_empty_document_produces_no_chunks() -> None:
    assert chunk_document(document("   ")) == ()


def test_chunking_a_batch_preserves_the_order_of_its_input() -> None:
    documents = [
        document("Satu.", document_id="a.pdf", filename="a.pdf", page=1),
        document("Dua.", document_id="b.pdf", filename="b.pdf", page=2),
    ]

    chunks = chunk_documents(documents)

    assert [chunk.document_id for chunk in chunks] == ["a.pdf", "b.pdf"]


def test_the_batch_accepts_any_iterable_not_only_a_list() -> None:
    """Sumbernya adalah pembacaan direktori; memaksanya menjadi list berarti
    menahan semuanya di memori."""
    chunks = chunk_documents(document(f"Halaman {n}.") for n in range(3))

    assert len(chunks) == 3


# ---------------------------------------------------------------------------
# 7. Identitas potongan
# ---------------------------------------------------------------------------
def test_the_chunk_id_names_its_place_not_its_position_in_a_sequence() -> None:
    """Identitas yang diturunkan dari posisi akan berubah setiap kali bahan disusun ulang."""
    assert chunk_id(document("x", page=3, paragraph=1)) == "compiler.pdf#p3#c1"


def test_a_document_without_a_page_still_gets_a_distinct_id() -> None:
    """Markdown tidak punya halaman; ia tetap harus dapat dibedakan dari PDF."""
    assert chunk_id(document("x", page=None, paragraph=0)) == "compiler.pdf#p-#c0"


def test_two_chunks_differing_only_in_page_do_not_collide() -> None:
    """Indeks paragraf dimulai ulang di setiap halaman, jadi halaman harus ikut ke dalam kunci."""
    first = document("x", page=1, paragraph=0)
    second = document("x", page=2, paragraph=0)

    assert chunk_id(first) != chunk_id(second)


def test_the_id_is_stable_across_identical_documents() -> None:
    """Diperlukan agar ``ingest`` yang diulang menimpa potongannya, bukan menggandakannya."""
    assert chunk_id(document("x")) == chunk_id(document("x"))


# ---------------------------------------------------------------------------
# 8. Angka bawaan
# ---------------------------------------------------------------------------
def test_the_defaults_match_the_configuration_file() -> None:
    """Angka di ``config.yaml`` dan bawaan fungsi ini harus sama, atau keduanya berdebat."""
    assert DEFAULT_CHUNK_CHARS == 1200
    assert DEFAULT_OVERLAP_CHARS == 200
