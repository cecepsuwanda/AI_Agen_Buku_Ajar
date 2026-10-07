"""Research Agent (§17) — pada MVP, sebuah *seam* yang jujur.

Retrieval belum ada di iterasi ini. Yang ada di sini adalah **titik sambungnya**:
:class:`~domain.ports.Researcher` sudah didefinisikan, ``ChapterWriter`` sudah
menerimanya lewat konstruktor, dan ``ResearchPackage`` sudah memuat
``source``/``page``/``section``/``evidence`` sejak sekarang (§13). Karena itu
menambahkan RAG di Tahap 3 berarti menambah satu berkas dan satu baris di
composition root — **tanpa menyunting penulis bab**.

Yang harus dijaga di sini adalah **kejujuran**. ``NullResearcher`` tidak
meneliti, jadi ia mengembalikan paket dengan ``degraded=True`` dan tidak
mengisi apa pun. Ia **tidak** mengisi ``sources`` dari ``spec.references``:
rujukan itu belum pernah diambil, dan menuliskannya akan membuat paket yang
mengaku punya sumber sekaligus mengaku terdegradasi — kontradiksi yang akan
membuat penulis mengutip bahan yang tidak pernah ada.

Bendera ``degraded`` itulah yang dipakai prompt penulis untuk memutuskan:
tanpa bahan, ia dilarang mencantumkan kutipan sama sekali dan wajib menandai
setiap klaim faktual di ``unresolved_claims``. Jadi degradasi ini **terlihat di
dalam buku**, bukan disembunyikan.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ResearchPackage
from domain.state import BookState


class NullResearcher:
    """Researcher yang tidak meneliti — implementasi MVP dari port §17.

    Kesesuaiannya dengan port ditegakkan oleh tipe, bukan oleh ``isinstance``:
    ``Researcher`` adalah ``Protocol`` tanpa ``runtime_checkable``, sehingga
    satu-satunya pemeriksaan yang bermakna adalah anotasi tipe di composition
    root dan ``mypy`` yang membacanya.
    """

    def collect(self, spec: ChapterSpec, book: BookState) -> ResearchPackage:
        """Kembalikan paket riset kosong yang menandai dirinya terdegradasi.

        Kedua argumen sengaja tidak dipakai. Signature-nya tetap lengkap supaya
        pengganti berbasis RAG dapat memakainya — ``spec`` memuat rujukan yang
        ditugaskan, ``book`` memuat istilah dan sitasi yang sudah terkumpul —
        tanpa mengubah satu pun pemanggil.
        """
        del spec, book
        return ResearchPackage.empty()


__all__ = ["NullResearcher"]
