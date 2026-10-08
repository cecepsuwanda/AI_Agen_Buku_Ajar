"""Registri gate pipeline (§27) — jembatan antara rantai 10 tahap dan MVP.

Blueprint §27 mendefinisikan rantai sepuluh status, sedangkan build ini baru
mengisi sebagiannya. Rantai itu tidak boleh diciutkan agar cocok dengan apa
yang sudah ada — status yang dihapus hari ini harus dimigrasikan di seluruh
``state/*.json`` besok. Karena itu rantai tetap utuh, dan tahap yang belum
berpenghuni diisi oleh :class:`PassThroughGate`: gate yang tidak memanggil LLM,
tidak memeriksa apa pun, dan **mencatat dirinya sebagai dilewati**.

Itulah yang membuat ``BookDirector`` tidak perlu tahu gate mana yang ada. Ia
mengulang daftar dari ``config.yaml`` dan memanggil ``evaluate`` pada setiap
gate. Konsekuensinya (OCP, dalam bentuk yang dapat diperiksa):

* Menambahkan pedagogy reviewer = berkas baru + satu baris di ``config.yaml``.
  Nol suntingan pada ``book_director.py``, ``base.py``, atau agent mana pun.
* Mengganti model peninjau = mengedit ``config.yaml``. Nol suntingan pada kode.

**Registri ini ditulis sekali saat import, lalu hanya dibaca.** Ia memang
``dict`` tingkat modul, tetapi bukan keadaan global yang termutasi di tengah
proses — inilah bedanya, dan bedanya ditegakkan: nama yang sudah terdaftar
tidak dapat didaftarkan ulang. Model yang patuh hari ini tidak boleh berubah
perilakunya karena modul lain kebetulan di-import belakangan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from domain.chapter import ChapterRecord, ReviewResult
from domain.enums import ChapterStatus
from domain.errors import ConfigError, InvalidGateError
from domain.ports import (
    LatexArtifacts,
    LatexCompiler,
    ModelProvider,
    PromptLibrary,
    Reporter,
    ReviewGate,
)
from domain.rules import validate_gates
from domain.state import BookState


@dataclass(frozen=True, slots=True)
class GateContext:
    """Segala yang mungkin dibutuhkan sebuah gate untuk dibangun.

    Sebuah **objek konteks**, bukan daftar argumen yang bertambah panjang. Gate
    pertama hanya butuh ``prompts``; gate LaTeX nanti akan butuh direktori
    template; gate yang memakai RAG akan butuh embedder. Dengan konteks,
    penambahan itu tidak mengubah signature factory mana pun yang sudah ada —
    dan yang paling penting, tidak ada gate yang dipaksa menerima dependensi
    yang tidak dipakainya (ISP).
    """

    router: ModelProvider
    prompts: PromptLibrary
    reporter: Reporter

    #: Target panjang bab, dari ``config.yaml``. Diteruskan ke writer dan reviewer
    #: supaya keduanya menilai terhadap angka yang sama.
    min_words: int = 1200

    #: Ambang skor kelulusan review. Ditegakkan :func:`~domain.rules.decide_review`.
    review_threshold: int = 7

    #: Batas percobaan perbaikan JSON, dari ``config.yaml``.
    repair_attempts: int = 2

    #: Penulis sumber LaTeX (§25), atau ``None`` bila LaTeX dimatikan.
    #:
    #: Satu-satunya dependensi gate yang menyentuh filesystem, dan ia sengaja
    #: masuk lewat sini alih-alih dirakit di dalam gate: gate tetap bebas dari
    #: ``open()``, dan yang memutuskan apakah berkas boleh ditulis adalah
    #: composition root — satu tempat, tempat keputusan yang sama sudah diambil
    #: untuk RAG (``retriever``) dan model.
    #:
    #: ``None`` berarti gate §25 mengembalikan pass-through, bukan bahwa gate-nya
    #: hilang: bab yang melewati tahap ini tanpa dikerjakan harus terlihat
    #: melewatinya, sama seperti tahap yang belum berpenghuni.
    latex: LatexArtifacts | None = None

    #: Perkakas kompilasi LaTeX (§26), atau ``None`` bila tidak ada.
    #:
    #: Dependensi gate kedua yang menyentuh mesin ini, dan ia dipisahkan dari
    #: ``latex`` meski keduanya berbicara tentang LaTeX: yang satu menulis
    #: berkas, yang lain menjalankan program. Composition root memutuskan
    #: keduanya secara terpisah, karena keduanya memang dapat gagal secara
    #: terpisah — direktori keluaran selalu dapat ditulis, sedangkan ``latexmk``
    #: belum tentu terpasang.
    #:
    #: ``None`` berarti gate §26 mengembalikan pass-through: sumber LaTeX-nya
    #: tetap ditulis, tetapi tidak ada yang mengompilasinya. Yang memutuskannya
    #: adalah composition root — ``latexmk`` yang diperiksa langsung oleh gate
    #: adalah gate yang tidak dapat diuji tanpa mesin ini.
    compiler: LatexCompiler | None = None


#: Pembuat gate dari konteksnya.
GateFactory = Callable[[GateContext], ReviewGate]


_GATE_REGISTRY: dict[str, GateFactory] = {}


def register_gate(name: str) -> Callable[[GateFactory], GateFactory]:
    """Dekorator: daftarkan sebuah gate dengan ``name``.

    :raises ConfigError: bila nama itu sudah terdaftar. Sengaja gagal keras —
        dua gate dengan nama sama berarti salah satunya tidak akan pernah
        dijalankan, dan itu jenis kesalahan yang tidak terlihat sampai
        seseorang bertanya mengapa babnya tidak pernah diperiksa.

    Dekoratornya mengembalikan factory apa adanya, sehingga fungsinya tetap
    dapat dipanggil langsung dalam tes tanpa melewati registri.
    """

    def decorate(factory: GateFactory) -> GateFactory:
        if name in _GATE_REGISTRY:
            raise ConfigError(
                f"Gate {name!r} sudah terdaftar. "
                "Nama gate harus unik — pakai nama lain atau hapus yang lama."
            )
        _GATE_REGISTRY[name] = factory
        return factory

    return decorate


def registered_gate_names() -> tuple[str, ...]:
    """Nama seluruh gate yang terdaftar, terurut (untuk pesan kesalahan dan ``status``)."""
    return tuple(sorted(_GATE_REGISTRY))


def build_gates(names: tuple[str, ...], context: GateContext) -> tuple[ReviewGate, ...]:
    """Bangun gate sesuai urutan ``names`` (§27).

    Dipanggil composition root saat start, bukan saat import. Konfigurasi gate
    yang salah karena itu gagal **sebelum satu token pun dibakar** — jauh lebih
    baik daripada gagal di bab ke-7 setelah membayar enam bab.

    Dua jenis kesalahan diperiksa di sini, dan keduanya adalah kesalahan
    konfigurasi, bukan kesalahan runtime:

    1. Nama yang tidak terdaftar.
    2. **``produces`` yang mundur atau keluar rantai** — diperiksa
       :func:`~domain.rules.validate_gates`. Tanpa pemeriksaan ini, gate seperti
       itu tidak gagal sama sekali: ``BookDirector._run_gates`` melewatinya
       karena ``can_advance`` bernilai salah, dan babnya diam-diam tidak pernah
       diperiksa oleh gate yang justru dimaksudkan untuk memeriksanya. Kegagalan
       sunyi seperti itu jauh lebih mahal daripada pesan galat di baris pertama.

    :raises ConfigError: bila ada nama yang tidak terdaftar, atau ``produces``
        sebuah gate tidak memajukan rantai §27.
    """
    built: list[ReviewGate] = []
    for name in names:
        factory = _GATE_REGISTRY.get(name)
        if factory is None:
            known = ", ".join(registered_gate_names()) or "(tidak ada)"
            raise ConfigError(
                f"Gate {name!r} tidak terdaftar di pipeline.gates. "
                f"Gate yang tersedia: {known}"
            )
        built.append(factory(context))

    gates = tuple(built)
    try:
        validate_gates(gates)
    except InvalidGateError as exc:
        raise ConfigError(
            f"Gate {exc.gate!r} punya `produces` yang tidak memajukan rantai §27: "
            f"{exc.source} → {exc.target}. Urutan di `pipeline.gates` harus maju, "
            "karena gate yang mundur akan dilewati tanpa suara."
        ) from exc
    return gates


# ---------------------------------------------------------------------------
# Gate bawaan
# ---------------------------------------------------------------------------
class PassThroughGate:
    """Memajukan rantai §27 tanpa memanggil LLM.

    Setiap vonisnya ``approved=True`` **dan** ``skipped=True``. Bendera itu
    penting dan bukan hiasan: tanpa ia, laporan akhir akan menyatakan bab ini
    "diperiksa" padahal tidak ada yang memeriksanya. Bab yang lolos tanpa
    pemeriksaan harus terlihat sebagai bab yang lolos tanpa pemeriksaan.

    Catatan yang dibawanya menyebutkan bahwa tahap ini belum diimplementasikan,
    sehingga pembaca ``state/chapterNN.json`` tahu persis apa yang terjadi.
    """

    def __init__(self, *, name: str, produces: ChapterStatus, note: str = "") -> None:
        self.name = name
        self.produces = produces
        self._note = note or f"Tahap {produces} belum diimplementasikan pada MVP ini."

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Loloskan bab tanpa memeriksa apa pun, dan katakan demikian."""
        del record, book
        return ReviewResult(
            gate=self.name,
            approved=True,
            score=10,
            feedback=(self._note,),
            skipped=True,
        )


def _passthrough_factory(name: str, produces: ChapterStatus) -> GateFactory:
    """Bangun factory pass-through untuk satu status.

    Ditulis sebagai fungsi penghasil, bukan satu kelas per tahap: kelas-kelas
    yang isinya identik hanya akan menyembunyikan bahwa semuanya memang
    placeholder — dan jumlahnya berkurang setiap tahap, sehingga angka yang
    ditulis di sini akan basi lebih cepat daripada isinya.
    """

    def factory(context: GateContext) -> ReviewGate:
        del context
        return PassThroughGate(name=name, produces=produces)

    return factory


#: Tahap §27 yang **belum** berpenghuni, dalam urutan rantai.
#:
#: Terdaftar sekarang meskipun belum ada di ``config.yaml``, supaya
#: mengaktifkannya nanti benar-benar hanya satu baris konfigurasi:
#:
#:     pipeline:
#:       gates: [pedagogy_reviewed, reviewer]
#:
#: Ketika agent sungguhnya tiba, ia mendaftar dengan namanya sendiri dan **baris
#: di bawah dihapus** — bukan ditimpa. Penghapusan itu bagian dari pekerjaan
#: tahapnya, bukan akibat sampingnya, dan itulah gunanya ``register_gate``
#: menolak nama yang sudah terpakai: ia memaksa keputusan sadar, bukan
#: pembiaran. Yang pertama menjalaninya adalah ``citation_checked``, yang
#: digantikan ``citation_checker`` pada Tahap 4 (§22); yang kedua
#: ``fact_checked``, digantikan ``fact_checker`` pada Tahap 5 (§21); yang ketiga
#: ``latex_generated``, digantikan ``latex_writer`` pada Tahap 6 (§25); yang
#: keempat ``latex_compiled``, digantikan ``latex_qa`` pada Tahap 7 (§26).
PLACEHOLDER_GATES: tuple[tuple[str, ChapterStatus], ...] = (
    ("pedagogy_reviewed", ChapterStatus.PEDAGOGY_REVIEWED),
    ("consistency_checked", ChapterStatus.CONSISTENCY_CHECKED),
)


def _register_placeholders() -> None:
    """Isi registri dengan seluruh tahap yang belum berpenghuni.

    Ditulis sebagai fungsi, bukan loop di tingkat modul, supaya tidak ada nama
    sementara (``_gate_name``) yang tertinggal di namespace paket ini — nama
    seperti itu terlihat seperti keadaan global bagi siapa pun yang membaca.
    """
    for name, produces in PLACEHOLDER_GATES:
        register_gate(name)(_passthrough_factory(name, produces))


_register_placeholders()


__all__ = [
    "PLACEHOLDER_GATES",
    "GateContext",
    "GateFactory",
    "PassThroughGate",
    "build_gates",
    "register_gate",
    "registered_gate_names",
]
