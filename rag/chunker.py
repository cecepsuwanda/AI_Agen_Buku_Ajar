"""Pemotongan bahan menjadi potongan siap-embed (§10, §13) — MURNI.

Modul ini **tidak** menyentuh ChromaDB, tidak membuka berkas, dan tidak
memanggil model embedding. Ia hanya mengubah teks menjadi potongan, dan itulah
sebabnya ia dapat diuji sampai ke batas-batasnya — potongan yang lebih panjang
daripada anggarannya, paragraf tunggal yang raksasa, tumpang tindih yang muat dan
yang tidak — tanpa satu pun berkas contoh.

Dua keputusan yang menentukan seluruh perilakunya:

**Potongan dipotong pada batas paragraf, bukan pada jumlah karakter.** Memotong
tepat di karakter ke-1200 secara teknis juga menghasilkan potongan sepanjang 1200
karakter, tetapi ia membelah kalimat definisi menjadi dua — dan dua separuh
kalimat definisi lebih buruk daripada tidak ada definisi sama sekali, karena
masing-masing separuhnya terdengar lengkap. Karena itu unitnya adalah paragraf,
dan anggaran karakter hanya menentukan **berapa paragraf** yang masuk ke satu
potongan.

**Tumpang tindih dibawa sebagai paragraf utuh, bukan sebagai potongan teks
mentah.** Tumpang tindih sepanjang 200 karakter yang diambil membabi buta bisa
dimulai di tengah kata; yang seperti itu tidak menolong siapa pun, baik bagi
model embedding maupun bagi pembaca kutipannya. Sebagai gantinya, potongan
berikutnya dimulai dari beberapa paragraf **terakhir** potongan sebelumnya —
yaitu kalimat-kalimat yang memang paling sering memuat definisi, dan persis
alasan mengapa tumpang tindih ada.

Satu invarian yang dijaga ketat: **tidak ada potongan yang lebih panjang daripada
``chunk_chars``**, dan **tidak ada potongan yang isinya kembar** dengan potongan
sebelumnya. Yang kedua itu bukan kemewahan — tumpang tindih yang kebetulan memakan
seluruh anggaran akan menghasilkan potongan yang tidak menambahkan apa pun, dan
potongan semacam itu membayar satu pemanggilan embedding untuk menanam teks yang
sudah tertanam.
"""

from __future__ import annotations

from typing import Iterable

from domain.document import Document

#: Pemisah antar-paragraf. Satu tempat, supaya penulis dan pembaca paragraf
#: tidak pernah berbeda pendapat soal apa yang memisahkan dua paragraf.
PARAGRAPH_SEPARATOR = "\n\n"

#: Anggaran bawaan, dipakai bila pemanggil tidak membawa angka dari ``config.yaml``.
DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 200


# ---------------------------------------------------------------------------
# Paragraf
# ---------------------------------------------------------------------------
def _normalize_newlines(text: str) -> str:
    """Samakan akhir baris menjadi ``\\n``.

    Dilakukan di sini, bukan hanya di normalizer, karena :func:`chunk_text`
    harus benar untuk teks dari mana pun: ``pypdf`` mengembalikan ``\\r\\n`` pada
    sebagian berkas, dan paragraf yang masih membawa ``\\r`` akan lolos ke dalam
    prompt sebagai karakter yang tidak terlihat — jenis kerusakan yang baru
    ketahuan saat seseorang membandingkan kutipan dengan halaman aslinya.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def paragraphs(text: str) -> tuple[str, ...]:
    """Pecah ``text`` menjadi paragraf, membuang yang kosong (MURNI).

    Paragraf kosong dibuang alih-alih dipertahankan sebagai penanda: ia tidak
    membawa isi apa pun, tetapi ia ikut dihitung sebagai unit dan karenanya
    menggeser indeks paragraf — dan indeks itulah yang menjadi bagian dari
    identitas potongan (:func:`~domain.document.chunk_id`).
    """
    normalized = _normalize_newlines(text)
    return tuple(part.strip() for part in normalized.split(PARAGRAPH_SEPARATOR) if part.strip())


# ---------------------------------------------------------------------------
# Unit — paragraf yang dijamin muat dalam anggaran
# ---------------------------------------------------------------------------
def _hard_split(paragraph: str, limit: int) -> tuple[str, ...]:
    """Potong paksa paragraf yang lebih panjang daripada ``limit`` (MURNI).

    Ini jalur yang seharusnya jarang terpakai, dan ia ada karena satu alasan
    praktis: sebagian PDF menghasilkan "paragraf" sepanjang satu halaman penuh
    karena pemisah paragrafnya tidak terbaca. Membiarkannya utuh berarti
    mengirim satu permintaan embedding sepanjang puluhan ribu karakter — yang
    akan dipotong diam-diam oleh model, sehingga separuh bahan **tidak pernah
    tertanam** dan tidak akan pernah ditemukan kembali.

    Pemotongan diusahakan jatuh pada spasi terdekat sebelum batas, supaya yang
    terbelah adalah antar-kata, bukan di tengah kata.
    """
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit)
        # Spasi yang terlalu dekat dengan awal berarti potongan yang hampir
        # kosong; memotong tepat di batas lebih baik daripada menghasilkan
        # serpihan yang tidak berarti.
        if cut < limit // 2:
            cut = limit
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return tuple(piece for piece in pieces if piece)


def _units(text: str, limit: int) -> tuple[str, ...]:
    """Paragraf-paragraf ``text``, masing-masing dijamin tidak melebihi ``limit``."""
    units: list[str] = []
    for paragraph in paragraphs(text):
        if len(paragraph) <= limit:
            units.append(paragraph)
        else:
            units.extend(_hard_split(paragraph, limit))
    return tuple(units)


# ---------------------------------------------------------------------------
# Pengemasan
# ---------------------------------------------------------------------------
def _length(units: Iterable[str]) -> int:
    """Panjang teks yang akan dihasilkan ``PARAGRAPH_SEPARATOR.join(units)``."""
    materialized = list(units)
    if not materialized:
        return 0
    separators = len(PARAGRAPH_SEPARATOR) * (len(materialized) - 1)
    return sum(len(unit) for unit in materialized) + separators


def _overlap_tail(units: list[str], limit: int) -> tuple[str, ...]:
    """Paragraf-paragraf terakhir ``units`` yang muat dalam ``limit`` (MURNI).

    Selalu menyisakan minimal satu paragraf di belakang: tumpang tindih yang
    membawa **seluruh** potongan sebelumnya akan membuat potongan berikutnya
    dimulai persis seperti potongan sebelumnya, dan chunk yang kembar adalah
    token yang dibayar dua kali untuk isi yang sama.
    """
    kept: list[str] = []
    size = 0
    for unit in reversed(units):
        if len(kept) >= len(units) - 1:
            break
        addition = len(unit) if not kept else len(unit) + len(PARAGRAPH_SEPARATOR)
        if size + addition > limit:
            break
        kept.append(unit)
        size += addition
    kept.reverse()
    return tuple(kept)


def chunk_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> tuple[tuple[int, str], ...]:
    """Potong ``text`` menjadi ``(indeks paragraf awal, teks potongan)`` (MURNI).

    Indeks paragraf awal ikut dikembalikan karena ia menjadi bagian dari
    identitas potongan. Tanpa itu, seluruh potongan dari satu halaman PDF akan
    tampak identik bagi indeks vektor dan saling menimpa.
    """
    units = _units(text, chunk_chars)
    if not units:
        return ()

    chunks: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0

    for index, unit in enumerate(units):
        if current and _length(current) + len(PARAGRAPH_SEPARATOR) + len(unit) > chunk_chars:
            chunks.append((start, PARAGRAPH_SEPARATOR.join(current)))
            carry = _overlap_tail(current, overlap_chars)
            # Tumpang tindih yang tidak muat bersama unit berikutnya akan
            # memaksa potongan berikutnya ditutup sebelum satu kata pun
            # ditambahkan — yaitu potongan yang isinya hanya salinan ekor
            # potongan sebelumnya. Dalam keadaan itu, tidak ada tumpang tindih
            # lebih baik daripada tumpang tindih yang sia-sia.
            if _length(carry) + len(PARAGRAPH_SEPARATOR) + len(unit) > chunk_chars:
                carry = ()
            current = list(carry)
            start = max(0, index - len(carry))
        current.append(unit)

    if current:
        chunks.append((start, PARAGRAPH_SEPARATOR.join(current)))

    return tuple(chunks)


# ---------------------------------------------------------------------------
# Document → Document
# ---------------------------------------------------------------------------
def chunk_document(
    document: Document,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> tuple[Document, ...]:
    """Potong satu :class:`Document` menjadi potongan-potongan (MURNI).

    Seluruh metadata §9 — nama berkas, halaman, bagian, jenis sumber —
    **disalin apa adanya** ke setiap potongan. Itulah keseluruhan gunanya: §10
    melarang membuang nomor halaman, dan larangan itu hanya bermakna bila nomor
    halaman masih ada pada potongan yang benar-benar dikutip. Yang berbeda antar
    potongan hanyalah ``text`` dan ``paragraph``.

    Dokumen tanpa isi menghasilkan nol potongan, bukan satu potongan kosong.
    Memaksakan potongan kosong akan menanam vektor dari ketiadaan, dan vektor
    semacam itu akan cocok dengan setiap pertanyaan.
    """
    return tuple(
        document.model_copy(update={"text": text, "paragraph": index})
        for index, text in chunk_text(
            document.text, chunk_chars=chunk_chars, overlap_chars=overlap_chars
        )
    )


def chunk_documents(
    documents: Iterable[Document],
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> tuple[Document, ...]:
    """Potong sekumpulan dokumen, urutannya dipertahankan (MURNI).

    Urutan masukan dipertahankan alih-alih diurutkan ulang: yang menentukan
    urutan di indeks vektor adalah hasil retrieval, bukan urutan penanaman, dan
    urutan yang dipertahankan membuat keluaran fungsi ini dapat dibandingkan
    baris per baris saat menelusuri masalah ingest.
    """
    chunks: list[Document] = []
    for document in documents:
        chunks.extend(
            chunk_document(document, chunk_chars=chunk_chars, overlap_chars=overlap_chars)
        )
    return tuple(chunks)


__all__ = [
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "PARAGRAPH_SEPARATOR",
    "chunk_document",
    "chunk_documents",
    "chunk_text",
    "paragraphs",
]
