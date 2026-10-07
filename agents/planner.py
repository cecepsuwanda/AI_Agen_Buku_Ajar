"""Book Planner (§15) — RPS menjadi spesifikasi buku.

Agent ini **hanya** menghasilkan kerangka. Ia tidak menulis isi buku: bab yang
isinya sudah ditulis di sini akan ditulis ulang oleh penulis, dan usaha itu
terbuang. Batas itu ditegakkan oleh tipe keluaran (``BookSpec``), bukan oleh
disiplin penulis prompt.

Ini satu-satunya panggilan yang melihat **seluruh RPS, semua topik, dan panduan
gaya sekaligus** — karena itu ia dipetakan ke model berkonteks terbesar, dan
dibayar tepat sekali per buku.
"""

from __future__ import annotations

from domain.book import BookRequest, BookSpec
from domain.rules import reconcile_book_spec

from agents.base import StructuredAgent


class BookPlanner(StructuredAgent[BookSpec]):
    """Menyusun :class:`~domain.book.BookSpec` dari RPS."""

    role = "planner"
    prompt_name = "planner.book"

    def plan(
        self, request: BookRequest, *, style_guide: str = ""
    ) -> tuple[BookSpec, tuple[str, ...]]:
        """Hasilkan spesifikasi buku dari ``request`` (§15).

        :param style_guide: panduan gaya dari ``config.yaml``. Dikirim ke model
            supaya ia dapat menyesuaikan tingkat kedalaman dan gaya penamaan bab.

        :returns: ``(spesifikasi, catatan)``. Catatan berisi hal-hal yang perlu
            diketahui pengguna — terutama bila jumlah bab tidak sesuai
            permintaan. Mengembalikannya, bukan mencetaknya, menjaga agent tetap
            bebas dari UI dan tetap dapat diuji tanpa menangkap stdout.
        """
        produced = self.generate(
            {
                "title": request.title,
                "audience": request.audience,
                "language": request.language,
                "target_chapters": request.target_chapters,
                "style_guide": style_guide or "(tidak ada panduan gaya khusus)",
                "topics": request.topics,
                "rps_text": request.rps_text.strip() or "(RPS tidak diberikan)",
            }
        )

        return reconcile_book_spec(produced, target_chapters=request.target_chapters)


__all__ = ["BookPlanner"]
