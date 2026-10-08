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
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel

from domain.book import ChapterSpec
from domain.chapter import ChapterRecord, Evidence, ResearchPackage, ReviewResult
from domain.document import Document
from domain.enums import ChapterStatus
from domain.graph import ConceptGraph
from domain.latex import LatexBuildResult
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
    #: Gambar yang menyertai pesan pengguna — halaman PDF hasil scan untuk model
    #: ``vision`` (§10). ``bytes``, bukan jalur berkas: adapter PDF yang merender
    #: halamannya, dan port ini tidak boleh tahu bahwa gambar berasal dari PDF,
    #: apalagi dari pustaka mana. Yang menerjemahkan ``bytes`` menjadi base64
    #: adalah adapter HTTP, sesuai kontrak SDK-nya.
    images: tuple[bytes, ...] = ()
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


@runtime_checkable
class LatexArtifacts(Protocol):
    """Penulisan sumber LaTeX ke ``output/latex/`` (§25, §39).

    Terpisah dari :class:`ChapterArtifacts` meskipun keduanya menulis turunan
    dari record yang sama, karena keduanya **dapat dimatikan secara terpisah**:
    buku tanpa LaTeX tetap buku yang lengkap (Markdown-nya ada), sedangkan buku
    tanpa Markdown bukan apa-apa. Menyatukannya berarti setiap pembaca Markdown
    ikut menanggung direktori LaTeX yang mungkin tidak ada.

    Port ini **tidak** memuat langkah kompilasi, meski §26 memang mengompilasi.
    Bab yang ditulis di sini adalah potongan ``\\include`` — ia tidak berdiri
    sendiri, sehingga mengompilasi satu bab tidak mungkin dengan ``main.tex``
    buku; yang dilakukan gate §26 adalah mengompilasinya di dalam dokumen sekali
    pakai, dan itu pekerjaan :class:`LatexCompiler` yang terpisah. Pemisahan itu
    bukan kerapian: penulisan berkas dan pemanggilan ``subprocess`` adalah dua
    izin yang berbeda, dan gate yang hanya ingin memeriksa tidak perlu memegang
    yang pertama.

    Karena itu gate §25 adalah satu-satunya gate yang **menulis berkas**, dan itu
    disengaja: menulis ``.tex`` adalah pekerjaan batas sistem, dan alternatifnya
    — menyunting ``BookDirector`` agar mengenal artefak LaTeX — akan merusak sifat
    OCP yang justru menjadi alasan registri gate ada.
    """

    def save_chapter(self, number: int, text: str) -> str:
        """Tulis potongan LaTeX satu bab. Kembalikan jalurnya."""
        ...

    def load_chapter(self, number: int) -> str | None:
        """Baca kembali potongan LaTeX bab ``number``; ``None`` bila belum ada.

        Satu-satunya method yang membaca di port ini, dan ia ada karena satu
        alasan yang tidak berlaku bagi Markdown: potongan LaTeX **tidak dapat
        dirender ulang dengan murah**. Perender
        :func:`~domain.latex.render_chapter_latex` memang murni, tetapi masukannya
        adalah :class:`~domain.latex.LatexChapter` yang dihasilkan model — dan
        memintanya sekali lagi berarti membayar satu panggilan LLM untuk
        memperoleh kembali berkas yang sudah ada di disk.

        Yang membacanya adalah gate §26: ia mengompilasi **persis** yang ditulis
        :class:`~agents.latex_writer.LatexWriterGate`, bukan bab yang dihasilkan
        ulang dengan cara yang mungkin berbeda.
        """
        ...

    def save_bibliography(self, text: str) -> str:
        """Tulis daftar pustaka (``references.bib``). Kembalikan jalurnya.

        Ditulis oleh gate yang sama dengan babnya, bukan oleh ``export``: entri
        daftar pustaka hanya bertambah, dan bab pertama yang sudah menulisnya
        membuat setiap bab berikutnya tidak perlu menunggu perintah terakhir
        untuk dapat dikompilasi.
        """
        ...


class LatexCompiler(Protocol):
    """Kompilasi satu potongan bab dengan perkakas LaTeX (§26).

    Port ini **sempit dengan sengaja**: satu method, dan masukannya teks. Yang
    dipakainya adalah gate §26, yang perlu tahu "apakah potongan ini benar-benar
    dapat dikompilasi" — bukan "bagaimana buku ini dirakit". Merakit buku
    (``main.tex``, seluruh bab, daftar pustaka) adalah pekerjaan perintah
    ``export --latex``, dan perintah itu tinggal di ``app/``, yang bebas
    memanggil adapter konkretnya langsung.

    Karena itu tidak ada ``compile_book`` di sini. Port yang memuat dua method
    untuk dua pemanggil yang berbeda adalah port yang memaksa setiap pemakainya
    menanggung yang tidak dipakainya (ISP) — dan yang paling merugikan adalah
    tesnya: setiap fake compiler harus mengimplementasikan keduanya.

    Adapter-nya **tidak melempar** untuk kegagalan kompilasi. Kompilasi yang
    gagal adalah hasil yang diharapkan (§35), bukan kesalahan: ia dikembalikan
    sebagai :class:`~domain.latex.LatexBuildResult` yang memuat log-nya, dan
    :func:`~domain.latex.build_problems` yang memutuskan apa artinya.
    """

    def compile_fragment(
        self,
        fragment: str,
        *,
        sources: Sequence[str] = (),
    ) -> LatexBuildResult:
        """Kompilasi satu potongan bab dalam dokumen sekali pakai.

        :param fragment: potongan LaTeX seperti yang ditulis
            ``latex/artifacts.py`` — ``\\chapter``, label, isi. Adapter yang
            membungkusnya dengan preamble buku, karena preamble adalah
            pengetahuan yang hanya boleh ada di satu tempat
            (``latex/templates/preamble.tex``).
        :param sources: nama sumber yang ditulis menjadi ``references.bib`` di
            direktori kerja, lewat :func:`~domain.latex.render_bibliography`.
            Tanpa entri ini, setiap ``\\cite`` dilaporkan menggantung meski
            kuncinya benar.
        """
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

    MVP memakai :class:`agents.researcher.NullResearcher`; researcher berbasis
    RAG menyusul **tanpa mengubah** ``ChapterWriter``.
    """

    def collect(self, spec: ChapterSpec, book: BookState) -> ResearchPackage:
        """Kumpulkan paket riset untuk satu bab."""
        ...


# ---------------------------------------------------------------------------
# RAG (§§10, §11, §13)
# ---------------------------------------------------------------------------
class CorpusIndexer(Protocol):
    """Port pembangunan indeks bahan (§10, §13).

    Terpisah dari :class:`Retriever` dan bukan satu port dengan dua method:
    yang satu **menulis** indeks dari ``input/references/`` pada perintah
    ``ingest``, yang lain **membacanya** pada setiap bab yang ditulis. Menyatukan
    keduanya berarti setiap penulis bab memegang kemampuan untuk membuang indeks
    yang dibangun orang lain — dan itu bukan kemampuan yang perlu dimiliki siapa
    pun kecuali perintah ``ingest``.
    """

    def index(self, documents: Iterable[Document]) -> int:
        """Tanam ``documents`` ke indeks. Kembalikan jumlah potongan tertanam."""
        ...

    def reset(self) -> None:
        """Kosongkan indeks.

        Bukan kemewahan: ``ingest`` harus dapat diulang setelah bahan rujukan
        diganti, dan indeks yang menumpuk bahan lama akan mengutip halaman dari
        berkas yang sudah tidak ada di ``input/``.
        """
        ...


class Retriever(Protocol):
    """Port pencarian bukti (§13).

    Mengembalikan :class:`~domain.chapter.Evidence` — **bukan** ``str``. §13
    melarang menyerahkan hanya ``text`` kepada penulis karena informasi sumber
    akan hilang bersamanya; menegakkan larangan itu pada tingkat tipe berarti
    penulis tidak punya rute untuk menerima teks tanpa asalnya.
    """

    def retrieve(self, query: str, *, limit: int = 8) -> tuple[Evidence, ...]:
        """Cari bukti yang relevan dengan ``query``, terbaik lebih dulu."""
        ...


# ---------------------------------------------------------------------------
# Knowledge graph (§14)
# ---------------------------------------------------------------------------
class GraphStore(Protocol):
    """Port penyimpanan graf konsep (§14).

    Dua method, dan keduanya menyentuh **seluruh** graf. Itu disengaja: graf di
    sini berisi puluhan konsep, ia dibangun ulang dari bahan setiap kali
    ``ingest`` dijalankan, dan tidak ada satu pun operasi yang menyunting satu
    simpul. Port yang menawarkan ``add_node``/``add_edge`` akan menuntut setiap
    pemakainya menyusun grafnya sendiri dari bagian-bagian — pekerjaan yang justru
    sudah dilakukan :func:`~domain.graph.build_graph` secara murni, dan yang lebih
    baik diuji tanpa berkas.

    Berbeda dari :class:`Retriever` yang dibaca setiap bab, graf ini dibaca
    perintah yang membangunnya. Karena itu ``load`` mengembalikan graf **kosong**
    alih-alih melempar bila belum ada: graf yang belum dibangun bukan kerusakan,
    ia hanya belum dibangun — sama seperti indeks vektor yang belum di-``ingest``.
    """

    def load(self) -> ConceptGraph:
        """Baca graf tersimpan; graf kosong bila belum ada atau tidak terbaca."""
        ...

    def save(self, graph: ConceptGraph) -> None:
        """Tulis seluruh graf, menimpa yang lama."""
        ...
