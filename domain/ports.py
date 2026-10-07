"""Port — abstraksi tempat seluruh dependensi dibalik (DIP).

Modul ini hanya berisi ``Protocol`` dan DTO transport. **Tidak ada
implementasi**, dan tidak ada import ``ollama``. Inilah yang membuat
``agents/`` dan ``domain/`` dapat diuji sepenuhnya tanpa jaringan.

Port sengaja **sempit** (ISP): ``ChatModel`` hanya tahu satu method, dan
``EmbeddingModel`` terpisah darinya. ``ChapterWriter`` hanya menerima
``ChatModel``, jadi ia **tidak punya rute** untuk memanggil embedding —
ditegakkan oleh tipe, bukan oleh konvensi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel

from domain.book import ChapterSpec
from domain.chapter import ChapterRecord, ResearchPackage, ReviewResult
from domain.enums import ChapterStatus
from domain.state import BookState


# ---------------------------------------------------------------------------
# DTO transport LLM
#
# Dataclass beku, bukan Pydantic: nilai-nilai ini tidak pernah diserialisasi,
# jadi validasi Pydantic hanya menambah biaya tanpa manfaat.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Satu panggilan chat yang sepenuhnya sudah ditentukan.

    Adapter tidak boleh mengambil keputusan apa pun dari sini — ia hanya
    menerjemahkan ke HTTP. ``None`` pada ``temperature``/``max_tokens``/
    ``num_ctx`` berarti "pakai default dari ModelSpec peran ini".
    """

    model: str
    system: str
    user: str
    format_schema: Mapping[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    num_ctx: int | None = None
    think: bool = False
    stop: tuple[str, ...] = ()
    role: str = "unknown"  # hanya untuk logging (§38)
    prompt_version: str = "0"


@dataclass(frozen=True, slots=True)
class ChatResult:
    """Hasil satu panggilan chat.

    ``thinking`` disimpan **terpisah** dan tidak pernah disambungkan ke ``text``.
    Tercampurnya jejak penalaran ke dalam draf bab adalah bug kualitas yang
    sunyi dan jelek — lihat catatan di ``models/ollama_client.py``.
    """

    text: str
    model: str
    thinking: str | None = None
    done_reason: str = ""
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
@runtime_checkable
class ChatModel(Protocol):
    """Port LLM. Satu method, tanpa pengetahuan soal peran atau retry."""

    def complete(self, request: ChatRequest) -> ChatResult:
        """Jalankan satu panggilan chat dan kembalikan hasilnya."""
        ...


@runtime_checkable
class EmbeddingModel(Protocol):
    """Port embedding — terpisah dari :class:`ChatModel` (ISP)."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Kembalikan satu vektor embedding untuk setiap teks masukan."""
        ...


@runtime_checkable
class ModelProvider(Protocol):
    """Port pemilihan model per peran (§6, §7).

    ``ModelRouter`` yang konkret ada di ``models/``, tetapi ``agents/`` tidak
    boleh mengimpornya — jadi yang dipinjam di sini hanyalah **bentuknya**.
    Nama port sengaja berbeda dari kelas konkretnya supaya tidak ada yang
    menyangka keduanya hal yang sama: yang satu adalah kontrak, yang lain adalah
    salah satu pemenuhannya.

    Dua method saja, tanpa percabangan. Bila port ini suatu saat menerima
    parameter ``temperature`` atau ``retry``, arahnya sudah salah — keduanya
    milik ``ModelSpec`` dan factory.
    """

    def chat(self, role: str) -> ChatModel:
        """Model chat untuk ``role``."""
        ...

    def embedder(self, role: str = "embedding") -> EmbeddingModel:
        """Model embedding. Terpisah dari :meth:`chat` (ISP)."""
        ...


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """Prompt yang sudah dirender, siap dikirim.

    ``hash`` disimpan agar perubahan prompt yang belum di-*bump* versinya tetap
    dapat dilacak saat membandingkan kualitas model (§38).
    """

    name: str
    system: str
    user: str
    version: str
    hash: str
    output_model_key: str


@runtime_checkable
class PromptLibrary(Protocol):
    """Memuat dan merender kontrak prompt (§36)."""

    def render(self, name: str, context: Mapping[str, object]) -> RenderedPrompt:
        """Render prompt ``name`` dengan ``context``."""
        ...

    def names(self) -> tuple[str, ...]:
        """Nama seluruh prompt yang tersedia."""
        ...

    def output_model_for(self, name: str) -> type[BaseModel]:
        """Model Pydantic yang dideklarasikan prompt ``name``.

        Ada di port ini karena agent membutuhkannya untuk mengambil skema
        ``format=``. Alternatifnya — agent mengimpor peta ``OUTPUT_MODELS`` dari
        ``app.prompting`` — akan membuat ``agents/`` bergantung pada lapisan
        aplikasi dan membalik arah DIP. Skema keluaran adalah bagian dari
        kontrak prompt, jadi ia wajar berada di sini.
        """
        ...


# ---------------------------------------------------------------------------
# State / checkpoint
# ---------------------------------------------------------------------------
@runtime_checkable
class StateStore(Protocol):
    """Persistensi state (§28). Satu-satunya port yang menyentuh filesystem."""

    def load_book(self) -> BookState | None: ...
    def save_book(self, state: BookState) -> None: ...
    def load_chapter(self, number: int) -> ChapterRecord | None: ...
    def save_chapter(self, record: ChapterRecord) -> None: ...
    def is_completed(self, number: int) -> bool: ...

    def save_raw_failure(self, number: int, attempt: int, raw: str) -> str:
        """Simpan teks mentah LLM yang gagal parse. Kembalikan path-nya."""
        ...


@runtime_checkable
class ChapterArtifacts(Protocol):
    """Penulisan deliverable Markdown ke ``output/`` (§39).

    Sengaja **bukan** bagian dari :class:`StateStore`, meskipun keduanya menulis
    berkas. Yang satu menyimpan kebenaran (``state/chapterNN.json`` — otoritatif),
    yang lain menyimpan turunannya (``output/chapters/chapterNN.md``), dan
    keduanya berbeda dalam hal yang paling penting: state yang hilang tidak
    dapat direkonstruksi, Markdown yang hilang dapat dirender ulang kapan saja
    dari record. Menyatukan keduanya ke dalam satu kelas akan menyembunyikan
    perbedaan itu sampai seseorang menghapus direktori yang salah.

    ``BookDirector`` hanya butuh :meth:`save_chapter`; :meth:`save_book` dipakai
    perintah ``export``.
    """

    def save_chapter(self, number: int, text: str) -> str:
        """Tulis Markdown satu bab. Kembalikan jalurnya."""
        ...

    def exists(self, number: int) -> bool:
        """True bila Markdown bab ``number`` sudah ada di ``output/``.

        Ada di port ini karena ``BookDirector`` membutuhkannya untuk menepati
        janji §28: bab yang sudah ``APPROVED`` tetapi berkasnya hilang
        **dirender ulang** dari record. Tanpa cara memeriksa, satu-satunya
        pilihan adalah menulis ulang setiap kali — dan menulis ulang berkas yang
        di-track git pada setiap ``run`` menghasilkan diff palsu yang membuat
        riwayat buku tidak terbaca.
        """
        ...

    def save_book(self, text: str) -> str:
        """Tulis Markdown buku gabungan. Kembalikan jalurnya."""
        ...


# ---------------------------------------------------------------------------
# Keluaran / pelaporan
# ---------------------------------------------------------------------------
@runtime_checkable
class Reporter(Protocol):
    """Pelaporan progres. Dipisah agar test tidak pernah bergantung pada UI."""

    def info(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def chapter_started(self, number: int, title: str) -> None: ...
    def stage(self, number: int, status: ChapterStatus) -> None: ...
    def gate_result(self, number: int, result: ReviewResult) -> None: ...
    def chapter_finished(self, record: ChapterRecord) -> None: ...


@runtime_checkable
class Clock(Protocol):
    """Sumber waktu. Di-inject supaya test deterministik."""

    def now_iso(self) -> str: ...


# ---------------------------------------------------------------------------
# Gate pipeline (§§21–§26, §27)
# ---------------------------------------------------------------------------
class ReviewGate(Protocol):
    """Satu tahap pemeriksaan dalam rantai §27.

    ``produces`` adalah status yang dicapai bila gate **lulus**. Inilah kunci
    OCP: ``BookDirector`` hanya mengulang daftar gate dari konfigurasi dan tidak
    pernah tahu gate mana yang ada. Menambahkan pedagogy reviewer nanti =
    berkas baru + satu baris di ``config.yaml`` — nol suntingan pada orchestrator.

    Tidak memakai ``runtime_checkable``: kita tidak pernah butuh ``isinstance``
    pada gate, dan Protocol dengan anggota data (``name``/``produces``)
    membatasi penggunaan ``issubclass``.
    """

    name: str
    produces: ChapterStatus

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Periksa satu bab dan kembalikan vonis."""
        ...


class Researcher(Protocol):
    """Port Research Agent (§17).

    MVP memakai :class:`agents.researcher.NullResearcher`; Tahap 3 menggantinya
    dengan researcher berbasis RAG **tanpa mengubah** ``ChapterWriter``.
    """

    def collect(self, spec: ChapterSpec, book: BookState) -> ResearchPackage:
        """Kumpulkan paket riset untuk satu bab."""
        ...
