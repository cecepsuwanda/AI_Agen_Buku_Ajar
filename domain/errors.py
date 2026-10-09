"""Taksonomi exception — domain MURNI (hanya stdlib, tanpa IO).

Mengapa di ``domain/`` dan bukan ``app/``: modul di dalam ``domain/`` yang
melempar exception ini (mis. ``domain.transitions``). Bila taksonominya tinggal
di ``app/``, maka ``domain/`` harus meng-import ``app/`` — arah dependensi
terbalik, dan DIP-nya bocor. Ini koreksi kecil atas rencana awal.

Pemisahan yang penting (lihat README, bagian "Jenis kegagalan"):

* Kondisi bisnis yang **diharapkan** — mis. review tidak lolos — BUKAN exception.
  Itu nilai balik eksplisit: ``domain.chapter.ReviewResult``.

  Rancangan awal sempat menyediakan ``ReviewRejectedError`` ("bab ditolak
  setelah batas revisi habis"). Kelas itu dihapus, dan penghapusannya disengaja:
  seluruh yang dibutuhkan pemanggil sudah ada di record tanpa perantara —
  ``record.status is FAILED_REVIEW`` menyatakan apa yang terjadi, dan
  ``record.last_review().feedback`` menyatakan mengapa. Membungkusnya dalam
  exception justru membuang record yang memuat buktinya, lalu memaksa pemanggil
  menyusunnya kembali dari potongan-potongan pesan.
* Exception di sini hanya untuk **kesalahan programmer** dan **kegagalan
  infrastruktur**.
"""

from __future__ import annotations

from typing import Any


class BukuAjarError(Exception):
    """Akar seluruh error aplikasi. CLI menangkap ini dan mencetaknya rapi."""


# ---------------------------------------------------------------------------
# Kesalahan programmer / pelanggaran kontrak domain
# ---------------------------------------------------------------------------
class DomainError(BukuAjarError):
    """Pelanggaran aturan domain."""


class IllegalTransitionError(DomainError):
    """Transisi status yang tidak ada di tabel §27.

    Sengaja di-``raise`` alih-alih mengembalikan ``Option``: transisi ilegal
    berarti daftar gate di konfigurasi dan kode tidak sinkron — itu kesalahan
    programmer, bukan kondisi runtime. Lebih baik gagal keras di awal.
    """

    def __init__(self, status: object, event: object) -> None:
        self.status = status
        self.event = event
        super().__init__(
            f"Transisi ilegal: status {status!r} tidak menerima event {event!r}"
        )


class InvalidGateError(DomainError):
    """Gate menghasilkan status yang tidak sah dari status saat ini."""

    def __init__(self, gate: str, source: object, target: object) -> None:
        self.gate = gate
        self.source = source
        self.target = target
        super().__init__(
            f"Gate {gate!r} menghasilkan status {target!r}, "
            f"yang tidak sah dari status {source!r}"
        )


class UnknownRoleError(DomainError):
    """Peran model tidak terdaftar di konfigurasi."""

    def __init__(self, role: str, known: tuple[str, ...]) -> None:
        self.role = role
        self.known = known
        super().__init__(
            f"Peran model {role!r} tidak dikenal. Peran tersedia: {', '.join(known)}"
        )


class AgentOutputError(DomainError):
    """LLM gagal menghasilkan output terstruktur yang valid setelah semua perbaikan."""

    def __init__(
        self,
        agent: str,
        raw: str,
        attempts: int,
        validation_error: BaseException | None = None,
    ) -> None:
        self.agent = agent
        self.raw = raw
        self.attempts = attempts
        self.validation_error = validation_error
        detail = f": {validation_error}" if validation_error else ""
        super().__init__(
            f"Agent {agent!r} gagal menghasilkan output valid "
            f"setelah {attempts} percobaan{detail}"
        )


class ChapterNotPlannedError(DomainError):
    """Bab yang diminta belum ada di BookSpec."""

    def __init__(self, number: int, available: tuple[int, ...]) -> None:
        self.number = number
        self.available = available
        super().__init__(
            f"Bab {number} tidak ada di BookSpec. "
            f"Bab tersedia: {list(available) or '(belum ada)'}"
        )


class BookNotPlannedError(DomainError):
    """Belum ada ``state/book.json`` — buku ini belum pernah direncanakan.

    Kesalahan **pemakaian**, bukan kesalahan programmer: urutan perintahnya
    yang salah. Karena itu pesannya menunjuk langsung ke perintah yang harus
    dijalankan lebih dulu, bukan sekadar menyatakan berkasnya tidak ada.
    """

    def __init__(self, path: object) -> None:
        self.path = path
        super().__init__(
            f"Belum ada rencana buku di {path}. "
            f"Jalankan 'plan' lebih dulu untuk menyusun BookSpec dari RPS."
        )


class GatePreconditionError(DomainError):
    """Sebuah gate dipanggil pada bab yang belum siap untuk dinilai.

    Kesalahan programmer, bukan kondisi runtime: ``BookDirector`` yang benar
    tidak pernah sampai ke gate review dengan bab yang belum punya draf. Bila itu
    terjadi, urutan tahap di konfigurasi dan kode sudah tidak sinkron — dan lebih
    baik gagal keras daripada menilai bab kosong dan meloloskannya.
    """

    def __init__(self, gate: str, number: int, missing: str) -> None:
        self.gate = gate
        self.number = number
        self.missing = missing
        super().__init__(
            f"Gate {gate!r} tidak dapat menilai bab {number}: {missing} belum ada. "
            f"Periksa urutan pipeline.gates di config.yaml."
        )


class ApprovalNotPossibleError(DomainError):
    """Bab diminta disetujui atau ditolak padahal belum sampai tahap itu (§44).

    Kesalahan **pemakaian**, bukan kesalahan programmer: yang salah adalah
    urutan perintahnya. Bab yang belum ditulis tidak dapat disetujui, dan
    menjawab "statusnya tidak sah" akan menyuruh pembacanya memeriksa konfigurasi
    yang sebenarnya tidak salah. Karena itu pesannya menunjuk apa yang harus
    dijalankan lebih dulu — sama seperti :class:`BookNotPlannedError`.
    """

    def __init__(self, number: int, status: object, *, action: str = "disetujui") -> None:
        self.number = number
        self.status = status
        self.action = action
        super().__init__(
            f"Bab {number} belum dapat {action}: statusnya {status}. "
            f"Kerjakan dulu dengan 'run' sampai babnya selesai ditulis, lalu ulangi."
        )


# ---------------------------------------------------------------------------
# Kegagalan infrastruktur
# ---------------------------------------------------------------------------
class InfraError(BukuAjarError):
    """Kegagalan IO, konfigurasi, atau layanan eksternal."""


class ConfigError(InfraError):
    """Konfigurasi tidak valid atau tidak lengkap."""

    def __init__(self, message: str, *, path: Any = None) -> None:
        self.path = path
        super().__init__(f"{message} ({path})" if path else message)


class InputError(InfraError):
    """Berkas masukan tidak ada atau tidak dapat dibaca.

    Dibedakan dari :class:`ConfigError` karena penyebab dan tindakannya berbeda:
    ``config.yaml`` yang salah adalah kesalahan pemasangan yang diperbaiki sekali,
    sedangkan RPS yang salah jalur adalah kesalahan pengetikan di baris perintah —
    dan pesannya harus menunjuk flag yang barusan diketik.
    """

    def __init__(self, what: str, path: object, detail: str = "") -> None:
        self.what = what
        self.path = path
        tambahan = f" — {detail}" if detail else ""
        super().__init__(f"{what} tidak dapat dibaca di {path}{tambahan}")


class ModelUnavailableError(InfraError):
    """Model tidak ada di server Ollama (HTTP 404) atau gagal dimuat."""

    def __init__(self, model: str, detail: str = "") -> None:
        self.model = model
        tambahan = f" — {detail}" if detail else ""
        super().__init__(
            f"Model {model!r} tidak tersedia{tambahan}. "
            f"Jalankan 'ollama pull {model}' atau periksa nama model di config.yaml."
        )


class ServiceUnavailableError(InfraError):
    """Server Ollama tidak dapat dihubungi sama sekali.

    Dibedakan dari :class:`ModelUnavailableError` karena penyebab dan
    tindakannya berbeda: yang ini soal *server*-nya tidak jalan, bukan soal
    modelnya tidak ada. Ini kegagalan pertama yang dialami pengguna baru, jadi
    pesannya harus langsung menunjuk ke penyebabnya.
    """

    def __init__(self, base_url: str, detail: str = "") -> None:
        self.base_url = base_url
        tambahan = f" ({detail})" if detail else ""
        super().__init__(
            f"Tidak dapat menghubungi Ollama di {base_url}{tambahan}. "
            f"Pastikan server Ollama berjalan — jalankan 'ollama serve' "
            f"atau buka aplikasi Ollama."
        )


class ModelAuthError(InfraError):
    """Model cloud menolak karena autentikasi (butuh akun Ollama)."""

    def __init__(self, model: str, detail: str = "") -> None:
        self.model = model
        tambahan = f" — {detail}" if detail else ""
        super().__init__(
            f"Model {model!r} memerlukan akun Ollama{tambahan}. "
            f"Jalankan 'ollama signin', atau pakai --profile local."
        )


class ModelTimeoutError(InfraError):
    """Panggilan model melewati batas waktu (transien — kandidat retry)."""

    def __init__(self, model: str, timeout_s: float) -> None:
        self.model = model
        self.timeout_s = timeout_s
        super().__init__(f"Model {model!r} melewati batas waktu {timeout_s:.0f} detik")


class TruncatedOutputError(InfraError):
    """Output terpotong: anggaran token habis.

    Ini error tersendiri karena terbukti terjadi di mesin ini — pada model
    ``thinking``, jejak penalaran dapat menghabiskan seluruh anggaran sehingga
    ``content`` keluar kosong dengan ``done_reason == 'length'``.
    """

    def __init__(self, model: str, budget: int | None, reason: str = "length") -> None:
        self.model = model
        self.budget = budget
        self.reason = reason
        anggaran = f"{budget} token" if budget else "yang tidak diketahui"
        super().__init__(
            f"Output model {model!r} terpotong ({reason}) pada {anggaran}. "
            f"Naikkan max_tokens untuk peran ini di config.yaml — "
            f"model 'thinking' butuh anggaran jauh lebih longgar."
        )


class EmbeddingShapeError(InfraError):
    """Model embedding mengembalikan jumlah vektor yang salah.

    Bukan kesalahan yang dapat dibiarkan: vektor dikaitkan ke potongan
    berdasarkan urutan, sehingga satu vektor yang hilang menggeser **seluruh**
    kaitan setelahnya. Tidak ada galat yang muncul setelahnya — yang muncul
    adalah indeks yang menunjuk halaman yang salah, dan itu jauh lebih mahal
    daripada berhenti di sini.
    """

    def __init__(self, *, expected: int, produced: int, offset: int = 0) -> None:
        self.expected = expected
        self.produced = produced
        self.offset = offset
        where = f"batch mulai teks ke-{offset}" if offset else "batch pertama"
        super().__init__(
            f"Model embedding mengembalikan {produced} vektor untuk {expected} teks "
            f"({where}). Urutan vektor menentukan potongan mana yang diwakilinya — "
            f"indeks tidak dibangun dari hasil yang tidak sepanjang masukannya."
        )


class StateCorruptError(InfraError):
    """Berkas state tidak dapat dibaca. State TIDAK pernah dihapus otomatis."""

    def __init__(self, path: Any, detail: str = "") -> None:
        self.path = path
        tambahan = f": {detail}" if detail else ""
        super().__init__(
            f"State rusak di {path}{tambahan}. "
            f"Perbaiki berkasnya, atau jalankan 'run --force --chapter N' untuk mengulang bab itu."
        )


class StateWriteError(InfraError):
    """Penulisan state gagal (mis. berkas terkunci proses lain)."""

    def __init__(self, path: Any) -> None:
        self.path = path
        super().__init__(
            f"Gagal menulis state ke {path}. "
            f"Pastikan tidak ada proses lain yang memegang berkas tersebut."
        )


class ArtifactWriteError(InfraError):
    """Penulisan deliverable (``output/``) gagal (mis. berkas terkunci proses lain).

    Terpisah dari :class:`StateWriteError` meskipun keduanya berarti "gagal
    menulis berkas", karena akibatnya berbeda jauh dan pesannya harus
    mengatakannya: state yang gagal ditulis berarti pekerjaan yang hilang,
    sedangkan deliverable yang gagal ditulis dapat dirender ulang dari state
    kapan saja. Menyamakan keduanya akan membuat pengguna mengira babnya lenyap
    padahal hanya berkas turunannya yang belum sempat ditulis.
    """

    def __init__(self, path: Any, detail: str = "") -> None:
        self.path = path
        self.detail = detail
        tambahan = f": {detail}" if detail else ""
        super().__init__(
            f"Gagal menulis berkas keluaran ke {path}{tambahan}. "
            f"Pastikan tidak ada proses lain yang memegang berkas tersebut."
        )
