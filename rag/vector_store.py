"""Indeks vektor ChromaDB lokal (§13) — **satu-satunya berkas yang mengimpor ``chromadb``**.

Ditegakkan oleh ``tests/unit/test_architecture.py``: tidak ada berkas lain di
repositori ini — termasuk berkas tes — yang boleh mengimpor ``chromadb``. Ganti
pustaka vektornya, dan hanya berkas ini yang berubah; ``Retriever`` di
``rag/retriever.py`` berbicara lewat tipe :class:`VectorHit` yang ditetapkan di
sini, bukan lewat tipe ChromaDB.

Tiga keputusan yang perlu diketahui sebelum menyunting berkas ini:

**Klien dibuka malas.** Membangun :class:`ChromaVectorStore` tidak menyentuh
disk sama sekali; klien ``PersistentClient`` baru dibuat pada operasi pertama.
ChromaDB membuka berkas SQLite + indeks, dan ia mengunci berkasnya di Windows.
Melakukannya saat container dirakit berarti setiap perintah — termasuk
``status`` dan ``plan`` — memegang kunci itu, sedangkan yang benar-benar
membutuhkan indeks hanya ``ingest`` dan penulisan bab.

**Metadata ditulis apa adanya, tanpa nilai kosong.** Nilai ``None`` bukan
metadata ChromaDB yang sah, dan kunci bersisi string kosong hanya menambah
kebisingan pada pencarian manual. Karena itu ``page`` dan ``paragraph`` hanya
ditulis bila memang ada. Yang **tidak** boleh dihilangkan adalah ``source`` dan
``document_id``: keduanya yang membuat sebuah potongan dapat disebut kembali
oleh pemeriksa sitasi (§22).

**Identitas potongan diturunkan dari metadata §9**, lewat
:func:`~domain.document.chunk_id` — bukan dari nomor urut kemunculan. Karena
itu menanam ulang korpus yang sama bersifat idempoten: potongan yang sama
menimpa dirinya sendiri alih-alih menghasilkan salinan kedua di sebelahnya.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from domain.document import Document, chunk_id
from domain.errors import InputError
from domain.ports import EmbeddingModel

#: Nama koleksi ChromaDB. ChromaDB menuntut nama 3–512 karakter; nama ini juga
#: yang muncul di ``knowledge/vector_db`` saat diperiksa manual.
COLLECTION_NAME = "references"

#: Ruang jarak. Cosinus, bukan L2: yang dicari adalah kemiripan **arah** antara
#: kalimat pencarian dan potongan, dan panjang vektor ``nomic-embed-text``
#: dipengaruhi panjang teks — sehingga L2 akan menghukum potongan panjang
#: semata karena ia panjang.
DISTANCE_SPACE = "cosine"


@dataclass(frozen=True, slots=True)
class VectorHit:
    """Satu hasil pencarian indeks, sebelum menjadi :class:`~domain.chapter.Evidence`.

    Sengaja bukan ``Evidence``: yang ini membawa ``distance`` — satuan milik
    indeks, yang tidak bermakna apa-apa bagi penulis bab. Penerjemahannya menjadi
    skor kemiripan dilakukan ``rag/retriever.py``, satu tempat, sehingga ambang
    dan urutannya dapat diuji tanpa ChromaDB.
    """

    chunk_id: str
    text: str
    source: str
    page: int | None
    section: str
    distance: float


def _metadata(document: Document) -> dict[str, Any]:
    """Metadata §9 untuk satu potongan, tanpa nilai kosong (MURNI).

    ``section`` yang kosong tidak ditulis: potongan dari berkas tanpa bagian
    akan menghasilkan kunci bersisi ``""`` di setiap entri, dan pada pemeriksaan
    manual ia tampak seperti bagian yang benar-benar bernama kosong.
    """
    meta: dict[str, Any] = {
        "document_id": document.document_id,
        "source": document.filename,
        "source_type": str(document.source_type),
    }
    if document.section.strip():
        meta["section"] = document.section.strip()
    if document.page is not None:
        meta["page"] = document.page
    if document.paragraph is not None:
        meta["paragraph"] = document.paragraph
    return meta


def _deduplicate(documents: Sequence[Document]) -> tuple[Document, ...]:
    """Buang potongan yang identitasnya sama, yang terakhir menang (MURNI).

    ChromaDB **menolak** ``upsert`` dengan id ganda dalam satu panggilan. Potongan
    beridentitas sama hampir selalu berarti korpus memuat berkas yang sama dua
    kali; yang benar adalah menyimpannya sekali, bukan menggagalkan seluruh
    ``ingest`` pada bahan yang isinya baik-baik saja.
    """
    unique: dict[str, Document] = {}
    for document in documents:
        unique[chunk_id(document)] = document
    return tuple(unique.values())


class ChromaVectorStore:
    """Indeks vektor lokal: menanam potongan dan mencarinya kembali.

    Memenuhi :class:`~domain.ports.CorpusIndexer` secara struktural
    (``index``/``reset``), dan menambahkan :meth:`query` yang dipakai
    ``rag/retriever.py``. Keduanya sengaja berada di kelas yang sama: yang
    memisahkan penulis indeks dari pembacanya adalah perakitan di composition
    root, bukan kelas ini.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        embedder: EmbeddingModel,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        self._directory = Path(directory)
        self._embedder = embedder
        self._collection_name = collection_name
        self._client: Any = None
        self._collection: Any = None

    # -- domain.ports.CorpusIndexer ---------------------------------------
    def index(self, documents: Iterable[Document]) -> int:
        """Tanam ``documents`` ke indeks; kembalikan jumlah potongan tertanam.

        Potongan berteks kosong **dibuang tanpa suara**, dan itu disengaja:
        teks kosong tidak dapat di-embed secara bermakna dan tidak akan pernah
        berguna sebagai bukti. Yang tidak boleh diabaikan adalah potongan
        *berisi* — semuanya ikut, sekalipun pendek.
        """
        usable = _deduplicate([d for d in documents if d.text.strip()])
        if not usable:
            return 0

        texts = [document.text for document in usable]
        vectors = self._embedder.embed(texts)
        metadatas = [_metadata(document) for document in usable]

        try:
            self._collection_at().upsert(
                ids=[chunk_id(document) for document in usable],
                embeddings=[list(vector) for vector in vectors],
                documents=texts,
                metadatas=metadatas,
            )
        except Exception as exc:  # noqa: BLE001 - pustaka pihak ketiga punya taksonomi sendiri
            raise self._failure("gagal menanam potongan", exc) from exc

        return len(usable)

    def reset(self) -> None:
        """Kosongkan indeks.

        Dibutuhkan ``ingest``: tanpa ini, bahan rujukan yang diganti akan
        meninggalkan potongan lamanya di indeks, dan buku akan mengutip halaman
        dari berkas yang sudah tidak ada di ``input/``.
        """
        if self._collection is not None:
            self._collection = None
        try:
            client = self._client_at()
            names = {getattr(item, "name", item) for item in client.list_collections()}
            if self._collection_name in names:
                client.delete_collection(self._collection_name)
        except Exception as exc:  # noqa: BLE001
            raise self._failure("gagal mengosongkan indeks", exc) from exc

    # -- Tambahan di luar port --------------------------------------------
    def count(self) -> int:
        """Jumlah potongan yang tersimpan. Nol pada indeks yang belum dibangun."""
        try:
            return int(self._collection_at().count())
        except Exception as exc:  # noqa: BLE001
            raise self._failure("gagal membaca jumlah potongan", exc) from exc

    def query(self, text: str, *, limit: int = 8) -> tuple[VectorHit, ...]:
        """Cari ``limit`` potongan terdekat dengan ``text``, terdekat lebih dulu.

        Pencarian meng-embed ``text`` dengan **model yang sama** yang menanam
        korpus — perbedaan model antara penanam dan pencari menghasilkan jarak
        yang tidak bermakna, dan tidak ada satu pun galat yang menunjukkannya.
        Kesamaan itu dijamin di sini karena keduanya memakai ``self._embedder``.
        """
        if limit < 1:
            return ()
        if self.count() == 0:
            return ()

        vector = self._embedder.embed([text])[0]
        try:
            result = self._collection_at().query(
                query_embeddings=[list(vector)],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise self._failure("gagal mencari di indeks", exc) from exc

        return self._hits(result)

    @property
    def directory(self) -> Path:
        """Tempat indeks disimpan (``knowledge/vector_db``, §13)."""
        return self._directory

    # -- internal ----------------------------------------------------------
    @staticmethod
    def _hits(result: Mapping[str, Any]) -> tuple[VectorHit, ...]:
        """Terjemahkan balasan ChromaDB menjadi :class:`VectorHit` (MURNI).

        ChromaDB mengembalikan setiap field sebagai daftar **per-query**: satu
        daftar luar untuk permintaan, satu daftar dalam untuk hasilnya. Karena
        kita selalu mengirim satu permintaan, yang diambil adalah elemen
        pertamanya — dan bila kosong, hasilnya kosong, bukan galat.
        """
        ids = result.get("ids") or [[]]
        documents = result.get("documents") or [[]]
        metadatas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]

        hits: list[VectorHit] = []
        for index, identifier in enumerate(ids[0]):
            meta: Mapping[str, Any] = metadatas[0][index] or {}
            page = meta.get("page")
            hits.append(
                VectorHit(
                    chunk_id=str(identifier),
                    text=str(documents[0][index] or ""),
                    source=str(meta.get("source", "")),
                    page=int(page) if page is not None else None,
                    section=str(meta.get("section", "")),
                    distance=float(distances[0][index]),
                )
            )
        return tuple(hits)

    def _client_at(self) -> Any:
        """Buka ``PersistentClient`` pada pemakaian pertama, lalu simpan."""
        if self._client is None:
            import chromadb  # impor lokal: pustaka ini mengunci berkas saat dibuka

            self._client = chromadb.PersistentClient(path=str(self._directory))
        return self._client

    def _collection_at(self) -> Any:
        """Koleksi, dibuat sekaligus dengan ruang jaraknya bila belum ada."""
        if self._collection is None:
            self._collection = self._client_at().get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": DISTANCE_SPACE},
            )
        return self._collection

    def _failure(self, what: str, exc: Exception) -> InputError:
        """Ubah kegagalan ChromaDB menjadi error domain.

        Jenis error SDK tidak boleh naik ke atas — aturan yang sama yang berlaku
        untuk ``ollama`` di ``models/ollama_client.py``. Pesannya menyebut
        indeks yang dibangun ulang, karena itulah satu-satunya tindakan yang
        berguna: ``knowledge/`` adalah turunan, dan menghapusnya tidak
        menghilangkan pekerjaan siapa pun.
        """
        return InputError(
            "Indeks vektor",
            self._directory,
            f"{what} ({exc}). Hapus direktori indeks lalu jalankan 'ingest' lagi.",
        )


__all__ = ["COLLECTION_NAME", "DISTANCE_SPACE", "ChromaVectorStore", "VectorHit"]
