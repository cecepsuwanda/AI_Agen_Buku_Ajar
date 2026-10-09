"""Log perjalanan JSONL (§38) — separuh yang dibaca mesin.

:mod:`app.reporting` melayani dosen yang menonton terminal; berkas ini melayani
mesin yang membaca ``state/run.jsonl`` **setelah** kegagalan. Keduanya sengaja
dipisah: tabel rich berubah sesuka selera tampilan, sedangkan bentuk catatan ini
adalah antarmuka yang dibaca skrip, sehingga ia harus stabil dan mudah di-parse.

**Satu baris JSON per eksekusi**, bukan per tahap. Alasannya: yang dicari saat
membaca log setelah kegagalan adalah "run mana, kapan, berapa bab, dan apa yang
menghentikannya" — dan pertanyaan itu dijawab oleh satu baris. Log per tahap
akan membuat berkas ini tumbuh sebanding jumlah panggilan model, sementara
salinan lengkapnya sudah ada di ``state/chapterNN.json``, yang justru otoritatif.

**Kegagalan menulis log tidak pernah menggagalkan run.** Ia dilaporkan sebagai
peringatan lalu dilewati: pipeline yang sudah membayar delapan bab tidak boleh
kehilangan hasilnya karena berkas log terkunci antivirus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from domain.chapter import ChapterRecord
from domain.state import RunReport

#: Versi bentuk catatan. Pembaca skrip memeriksanya sebelum menafsirkan field.
RECORD_VERSION = 1


def chapter_entry(record: ChapterRecord) -> dict[str, Any]:
    """Ringkas satu bab menjadi bagian dari catatan (MURNI).

    Yang disertakan hanya hal yang **tidak dapat dibaca lagi nanti**: status
    akhir, jumlah revisi, dan vonis tiap gate. Isi drafnya tidak — ia sudah ada
    lengkap di ``state/chapterNN.json``, dan menyalinnya ke log berarti ada dua
    tempat yang harus dijaga sinkron.
    """
    return {
        "number": record.number,
        "status": str(record.status),
        "revision": record.revision,
        "gates": [
            {
                "gate": review.gate,
                "approved": review.approved,
                "score": review.score,
                "skipped": review.skipped,
                # Ikut dicatat karena tanpa ia, bab yang berhenti menunggu
                # keputusan manusia (§44) tidak dapat dibedakan di dalam log dari
                # bab yang ditolak gate — keduanya tampil sebagai
                # ``approved: false``, padahal yang diminta keduanya berbeda:
                # yang satu perbaikan, yang lain pembaca.
                "blocked": review.blocked,
            }
            for review in record.reviews
        ],
        "error": record.error,
    }


def run_record(
    report: RunReport,
    *,
    profile: str,
    models: Mapping[str, str],
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """Susun catatan satu kali ``run`` (MURNI).

    ``models`` adalah peta peran→nama model yang **benar-benar dipakai**. Ia ikut
    dicatat karena log yang tidak menyebut modelnya tidak dapat dipakai untuk
    membandingkan mutu antar model — dan itulah §33: pemilihan model adalah
    keputusan yang harus dapat ditelusuri kembali.
    """
    return {
        "version": RECORD_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "profile": profile,
        "models": dict(models),
        "total": report.total,
        "approved": report.approved,
        "failed": report.failed,
        "skipped": report.skipped,
        "pending": report.pending,
        "exit_code": report.exit_code(),
        "aborted": report.aborted,
        "abort_reason": report.abort_reason,
        "chapters": [chapter_entry(record) for record in report.records],
    }


def append_run_log(path: Path, record: Mapping[str, Any]) -> str | None:
    """Tambahkan ``record`` sebagai satu baris JSONL ke ``path``.

    :returns: jalur berkas bila berhasil, ``None`` bila gagal ditulis. Pemanggil
        **tidak** boleh menggagalkan run karenanya — lihat catatan modul.

    Memakai mode *append* dengan ``newline="\\n"``: JSONL ditentukan oleh
    pemisah baris, dan CRLF akan membuat pembaca berbasis ``readline`` menerima
    baris dengan ``\\r`` yang menempel di ujung nilai terakhir.
    """
    line = json.dumps(record, ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        return None
    return str(path)


__all__ = ["RECORD_VERSION", "append_run_log", "chapter_entry", "run_record"]
