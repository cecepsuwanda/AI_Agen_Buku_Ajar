"""LaTeX QA dan gate-nya (§26).

§26 menutup rantai LaTeX dengan satu pertanyaan: **apakah yang ditulis benar-benar
dapat dikompilasi?** Pertanyaan itu tidak dapat dijawab bacaan mata — galat LaTeX
yang paling mahal adalah satu ``&`` yang belum diloloskan, dan ia menghasilkan
pesan yang menunjuk ke tempat yang salah. Karena itu jawabannya diambil dari
perkakas LaTeX sendiri, dan gate ini adalah tempatnya.

**Mengapa gate ini boleh menolak bab, padahal gate §25 tidak.** Gate §25 adalah
tahap **penulisan**: kekurangan di sana adalah kekurangan bahan, dan menolak bab
karenanya mengembalikannya kepada penulis yang tidak menulis LaTeX. Gate ini
adalah tahap **pemeriksaan** dengan kesempatan memperbaiki lebih dulu: sebelum
menolak, ia mengirim log kepada model dan mencoba lagi. Yang tersisa setelah itu
adalah bab yang **tidak dapat dicetak**, dan menolaknya adalah satu-satunya
jawaban yang jujur — PDF yang tidak ada berarti bab yang tidak ada.

**Mengapa gate ini membaca berkas.** Potongan yang dikompilasinya adalah yang
sudah ditulis gate §25, bukan yang dihasilkan ulang. Menghasilkannya ulang berarti
membayar satu panggilan LLM untuk memperoleh kembali berkas yang sudah ada, dan
— yang lebih buruk — mengompilasi bab yang mungkin berbeda dari yang akan dicetak.

**Mengapa berkasnya hanya ditimpa oleh percobaan yang lolos.** Percobaan
perbaikan yang masih gagal tidak pernah ditulis. Menimpa potongan yang setidaknya
pernah dihasilkan dengan potongan yang lebih buruk adalah kerugian yang tidak
dapat dibatalkan, dan tidak ada satu pun cara mengetahui bahwa yang di disk sudah
bukan yang terbaik.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterRecord, ReviewResult
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.latex import (
    LatexBuildResult,
    LatexChapter,
    bibliography_sources,
    build_advisories,
    build_problems,
    chapter_citations,
    chapter_latex_filename,
    inspect_latex,
    lock_chapter_number,
    render_chapter_latex,
)
from domain.ports import ChatModel, LatexArtifacts, LatexCompiler, PromptLibrary, ReviewGate
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, PassThroughGate, register_gate

#: Skor yang dilaporkan ketika bab akhirnya ditolak. Bukan nol: nol berarti
#: "tidak ada yang dihasilkan", sedangkan di sini potongannya ada dan yang gagal
#: hanyalah kompilasinya — dan dua hal itu menuntut tindakan yang berbeda.
_FAILED_SCORE = 2


class LatexRepairAgent(StructuredAgent[LatexChapter]):
    """Memperbaiki potongan LaTeX yang gagal dikompilasi (§26)."""

    role = "latex"
    prompt_name = "latex.repair"

    def repair(
        self,
        fragment: str,
        *,
        spec: ChapterSpec,
        book: BookState,
        sources: tuple[tuple[str, str], ...],
        findings: tuple[str, ...],
        log_excerpt: str,
    ) -> LatexChapter:
        """Hasilkan potongan yang sudah diperbaiki untuk ``fragment`` (§26).

        :param sources: pasangan ``(kunci, sumber)`` yang boleh dikutip — daftar
            tertutup yang sama dengan yang diterima gate §25, supaya perbaikan
            tidak dapat menyelundupkan kunci sitasi baru.
        :param findings: temuan yang harus diperbaiki, dalam kalimat yang dapat
            dikerjakan. Diisi :func:`~domain.latex.build_problems` (dari log) dan
            :func:`~domain.latex.inspect_latex` (dari bentuk potongan).
        :param log_excerpt: potongan log LaTeX yang menyebut galatnya. Dikirim
            **apa adanya**: pesan perkakas menyebut baris dan perintah, dan
            menerjemahkannya lebih dulu hanya menambah satu tempat untuk salah.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "objectives": spec.objectives,
                "citations": sources,
                "fragment": fragment,
                "findings": findings,
                "log_excerpt": log_excerpt,
            }
        )


class LatexQAGate:
    """Gate §26: kompilasi potongan bab, perbaiki bila gagal, lalu putuskan.

    Alurnya persis §26 ("Errors? yes → LaTeX Agent → retry"):

    1. Potongan yang sudah ditulis gate §25 dikompilasi di dalam dokumen sekali
       pakai.
    2. Bila ada temuan, potongan dan log-nya dikirim ke role ``latex`` lewat
       ``prompts/latex.repair.md``; hasilnya diperiksa bentuknya lebih dulu, lalu
       dikompilasi lagi.
    3. Bila tidak ada percobaan yang lolos, bab **ditolak** dan kembali ke revisi.
    """

    #: Nama gate di ``config.yaml`` — ``pipeline.gates: [..., latex_writer, latex_qa]``.
    name = "latex_qa"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.LATEX_COMPILED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        artifacts: LatexArtifacts,
        compiler: LatexCompiler,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = LatexRepairAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._artifacts = artifacts
        self._compiler = compiler
        self._repair_attempts = max(repair_attempts, 0)

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Kompilasi potongan LaTeX bab pada ``record`` (§26).

        :raises GatePreconditionError: bila spesifikasi, draf, atau potongan
            LaTeX-nya belum ada. Potongan yang belum ada bukan bab yang lulus
            tanpa kompilasi: ia bab yang tidak dapat diperiksa, dan perbedaan itu
            harus terlihat.
        """
        spec = record.spec
        if spec is None:
            raise GatePreconditionError(self.name, record.number, "spesifikasi bab")
        draft = record.draft
        if draft is None:
            raise GatePreconditionError(self.name, record.number, "draf")

        fragment = self._artifacts.load_chapter(spec.number)
        if fragment is None:
            raise GatePreconditionError(
                self.name,
                record.number,
                "sumber LaTeX bab (output/latex/chapters/…; jalankan ulang gate latex_writer)",
            )

        sources = bibliography_sources(tuple(book.citations.values()), draft.citations)
        pairs = chapter_citations(sources, draft.citations)
        allowed = tuple(key for key, _ in pairs)

        result = self._compiler.compile_fragment(fragment, sources=sources)
        problems = build_problems(result)
        notes: tuple[str, ...] = ()
        accepted: LatexChapter | None = None
        attempts = 0

        for attempt in range(self._repair_attempts):
            if not problems:
                break
            attempts = attempt + 1
            repaired = self._agent.repair(
                fragment,
                spec=spec,
                book=book,
                sources=pairs,
                findings=problems,
                log_excerpt=result.log_excerpt,
            )
            repaired, notes = lock_chapter_number(repaired, number=spec.number)
            clean, structural = inspect_latex(repaired, allowed_citations=allowed)
            if not clean:
                # Tidak dikompilasi dan tidak ditulis: potongan yang bentuknya
                # sudah salah tidak akan menghasilkan log yang lebih berguna
                # daripada temuan bentuk itu sendiri.
                problems = structural
                continue

            candidate = render_chapter_latex(repaired, number=spec.number, spec=spec)
            result = self._compiler.compile_fragment(candidate, sources=sources)
            problems = build_problems(result)
            if not problems:
                fragment = candidate
                accepted = repaired
                break

        if problems:
            return ReviewResult(
                gate=self.name,
                approved=False,
                score=_FAILED_SCORE,
                feedback=self._rejection(spec, problems, result),
            )

        written: str | None = None
        if accepted is not None:
            written = self._artifacts.save_chapter(spec.number, fragment)
        return ReviewResult(
            gate=self.name,
            approved=True,
            score=10,
            feedback=self._acceptance(
                attempts,
                build_advisories(result),
                notes,
                written,
                filename=chapter_latex_filename(spec.number),
            ),
        )

    # -- pesan --------------------------------------------------------------
    def _acceptance(
        self,
        attempts: int,
        advisories: tuple[str, ...],
        notes: tuple[str, ...],
        written: str | None,
        *,
        filename: str,
    ) -> tuple[str, ...]:
        """Catatan untuk bab yang kompilasinya bersih (MURNI).

        ``written`` bernilai ``None`` bila berkas yang sudah ada **tidak** ditulis
        ulang, dan itu pun dilaporkan: "berkasnya ditulis" dan "berkasnya
        dibiarkan seperti semula" adalah dua keadaan yang berbeda, dan pembaca
        ``state/chapterNN.json`` berhak tahu yang mana yang terjadi.
        """
        head = (
            "Potongan LaTeX dikompilasi bersih."
            if attempts == 0
            else f"Potongan LaTeX dikompilasi bersih setelah {attempts} perbaikan."
        )
        message = list(notes)
        message.append(head)
        message.extend(advisories)
        message.append(
            f"Berkas potongan: {written}"
            if written is not None
            else f"Berkas potongan {filename} tidak diubah."
        )
        return tuple(message)

    def _rejection(
        self,
        spec: ChapterSpec,
        problems: tuple[str, ...],
        result: LatexBuildResult,
    ) -> tuple[str, ...]:
        """Catatan untuk bab yang tetap tidak dapat dikompilasi (MURNI).

        Log-nya ikut dikirim, dan itu disengaja: penerima catatan ini adalah
        penulis bab, yang harus tahu apa yang dikatakan perkakas LaTeX — bukan
        hanya bahwa "kompilasi gagal".
        """
        message = [f"Bab {spec.number} tidak dapat dikompilasi; draf dikembalikan untuk revisi."]
        message.extend(problems)
        if result.log_excerpt:
            message.append("Keluaran perkakas LaTeX:\n" + result.log_excerpt)
        return tuple(message)


@register_gate(LatexQAGate.name)
def _build_latex_qa(context: GateContext) -> ReviewGate:
    """Bangun gate §26 dari konteksnya.

    **Bila LaTeX dimatikan, atau kompilasi tidak mungkin di mesin ini, gate ini
    tetap ada — sebagai pass-through.** Bab yang melewati tahap ini harus terlihat
    sebagai bab yang melewatinya, bukan sebagai bab yang tidak pernah punya tahap
    itu. Keputusan "boleh mengompilasi atau tidak" diambil composition root,
    karena hanya ia yang tahu apakah perkakas LaTeX dapat dijalankan — gate yang
    memeriksa ``PATH`` sendiri adalah gate yang tidak dapat diuji tanpa mesin ini.

    Peran ``latex`` tetap dipakai di sini: yang memperbaiki adalah model yang sama
    dengan yang menulis, karena ia yang sudah memegang konteks babnya. Bila
    kompilasi tidak mungkin, peran itu tidak dipanggil sama sekali — sehingga
    konfigurasi tanpa peran ``latex`` tetap sah.
    """
    if context.latex is None:
        return PassThroughGate(
            name=LatexQAGate.name,
            produces=LatexQAGate.produces,
            note=(
                "LaTeX dimatikan di konfigurasi (latex.enabled: false); "
                "tidak ada sumber LaTeX yang dapat dikompilasi."
            ),
        )
    if context.compiler is None:
        return PassThroughGate(
            name=LatexQAGate.name,
            produces=LatexQAGate.produces,
            note=(
                "Perkakas LaTeX tidak tersedia di mesin ini; sumber LaTeX ditulis "
                "tetapi tidak dikompilasi."
            ),
        )
    return LatexQAGate(
        model=context.router.chat("latex"),
        prompts=context.prompts,
        artifacts=context.latex,
        compiler=context.compiler,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["LatexQAGate", "LatexRepairAgent"]
