"""LaTeX Agent dan gate-nya (§25).

§25 meminta "LaTeX Agent" berperan ``latex``, dan gate ini adalah tempat ia
bekerja. Yang dihasilkannya **bukan** dokumen jadi, melainkan
:class:`~domain.latex.LatexChapter` — potongan terstruktur yang dirakit
:func:`~domain.latex.render_chapter_latex` menjadi ``.tex``. Pembagian itu
disengaja dan alasannya ada di ``domain/latex.py``: model yang menyalin ulang
seluruh isi bab ke LaTeX adalah cara paling andal kehilangan separuh isinya.

**Mengapa gate ini menulis berkas.** Ia satu-satunya gate yang menyentuh
filesystem, dan itu menyimpang dari sifat gate lain. Deviasi itu diakui terbuka
(lihat :class:`~domain.ports.LatexArtifacts`): menulis ``.tex`` adalah pekerjaan
batas sistem, dan alternatifnya — mengajari ``BookDirector`` tentang artefak
LaTeX — akan merusak sifat OCP yang justru menjadi alasan registri gate ada.
Yang dijaga di sini adalah batasnya: gate memanggil **port**, bukan ``open()``.

**Mengapa kunci sitasi dihitung di sini, bukan oleh model.** Model menerima
daftar ``(kunci, sumber)`` yang sudah dihitung :func:`~domain.latex.citation_key`
dan diperintahkan memakai hanya kunci itu. Dua akibatnya nyata: ``references.bib``
pasti memuat setiap kunci yang dikutip (tidak ada sitasi menggantung karena kunci
yang salah tulis), dan §34 ditegakkan pada tingkat kunci — satu-satunya cara
model menyimpang adalah mengubah kunci yang sudah diberikan, dan itu justru yang
ditangkap :func:`~domain.latex.inspect_latex` sebelum berkasnya ditulis.

**Mengapa gate ini selalu meloloskan drafnya.** Ia bukan tahap penilaian,
melainkan tahap **penulisan** — sama seperti gate contoh dan latihan (§19, §20).
Kekurangan pada potongan LaTeX adalah kekurangan bahan, dan menolak bab karena
itu mengembalikan seluruh bab kepada penulis, yang tidak dapat memperbaikinya:
penulis tidak menulis LaTeX. Karena itu temuan yang tersisa dilaporkan dengan
skor rendah dan catatan yang keras, lalu diteruskan ke peninjau sungguhan yang
memang memegang wewenang menolak. Total kegagalan menghasilkan potongan
(``AgentOutputError``) bukan vonis, melainkan kesalahan yang ditahan per bab
(§35) — bab itu ditandai ``FAILED``.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ReviewResult
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.latex import (
    LatexChapter,
    bibliography_entries,
    bibliography_sources,
    chapter_citations,
    inspect_latex,
    lock_chapter_number,
    render_bibliography,
    render_chapter_latex,
)
from domain.ports import ChatModel, LatexArtifacts, PromptLibrary, ReviewGate
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, PassThroughGate, register_gate

#: Skor yang dilaporkan ketika temuan LaTeX masih tersisa setelah seluruh
#: percobaan perbaikan habis. Bukan nol: nol berarti "tidak ada yang dihasilkan",
#: dan menyamakan keduanya menghapus satu-satunya isyarat yang membedakan
#: "potongannya cacat" dari "potongannya tidak ada".
_SHORTFALL_SCORE = 5


class LatexWriterAgent(StructuredAgent[LatexChapter]):
    """Menghasilkan potongan LaTeX satu bab (§25)."""

    role = "latex"
    prompt_name = "latex.chapter"

    def write(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        citations: tuple[tuple[str, str], ...],
        feedback: tuple[str, ...] = (),
    ) -> LatexChapter:
        """Hasilkan potongan LaTeX untuk ``draft`` (§25).

        :param citations: pasangan ``(kunci, sumber)`` yang **boleh** dikutip bab
            ini, dihitung :func:`~domain.latex.bibliography_entries`. Dikirim ke
            model sebagai daftar tertutup, bukan sebagai contoh: kunci yang tidak
            ada di daftar itu akan ditolak :func:`~domain.latex.inspect_latex`.
        :param feedback: temuan pemeriksaan atas percobaan sebelumnya. Dikirim
            sebagai bagian dari prompt, bukan sebagai percobaan ulang yang buta —
            pola yang sama dengan gate contoh dan latihan.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "objectives": spec.objectives,
                "citations": citations,
                "draft_json": draft.model_dump_json(indent=2),
                "feedback": feedback,
            }
        )


class LatexWriterGate:
    """Gate §25: ubah draf menjadi potongan LaTeX, lalu periksa potongannya.

    Ia **tidak** mengembalikan draf yang diperkaya. Draf adalah Markdown —
    bentuk yang dibaca penulis dan peninjau — sedangkan potongan LaTeX adalah
    turunannya di ``output/latex/``, sama seperti ``output/chapters/chapterNN.md``
    adalah turunan dari record yang sama. Menaruh ``body_tex`` ke dalam
    ``record.draft`` akan membuat dua representasi saling menimpa.
    """

    #: Nama gate di ``config.yaml`` — ``pipeline.gates: [..., latex_writer]``.
    name = "latex_writer"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.LATEX_GENERATED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        artifacts: LatexArtifacts,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = LatexWriterAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._artifacts = artifacts
        self._repair_attempts = max(repair_attempts, 0)

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Tulis ``.tex`` bab pada ``record`` beserta daftar pustakanya (§25).

        :raises GatePreconditionError: bila spesifikasi atau draf belum ada.
            Menulis LaTeX untuk bab yang belum ditulis berarti mengarang isi
            bab — dan isi yang dikarang di sini akan sampai ke PDF, yang jauh
            lebih dipercaya pembaca daripada draf di ``state/``.
        :raises AgentOutputError: bila model gagal menghasilkan ``LatexChapter``
            yang dapat di-parse. Ditahan per bab oleh ``BookDirector`` (§35).
        """
        spec = record.spec
        if spec is None:
            raise GatePreconditionError(self.name, record.number, "spesifikasi bab")
        draft = record.draft
        if draft is None:
            raise GatePreconditionError(self.name, record.number, "draf")

        # Sumber daftar pustaka dan daftar kunci tertutup bab ini dihitung
        # :mod:`domain.latex`: gate §26 memakai aturan yang sama persis, dan dua
        # salinan aturan itu akan menyimpang menjadi sitasi menggantung.
        sources = bibliography_sources(tuple(book.citations.values()), draft.citations)
        entries = bibliography_entries(sources)
        pairs = chapter_citations(sources, draft.citations)
        allowed = tuple(key for key, _ in pairs)

        produced = LatexChapter(number=spec.number, title=spec.title)
        notes: tuple[str, ...] = ()
        findings: tuple[str, ...] = ()
        attempts = 0

        for attempt in range(self._repair_attempts + 1):
            attempts = attempt + 1
            produced = self._agent.write(
                draft, spec=spec, book=book, citations=pairs, feedback=findings
            )
            produced, notes = lock_chapter_number(produced, number=spec.number)
            _, findings = inspect_latex(produced, allowed_citations=allowed)
            if not findings:
                break

        chapter_path = self._artifacts.save_chapter(
            spec.number, render_chapter_latex(produced, number=spec.number, spec=spec)
        )
        bibliography_path = self._artifacts.save_bibliography(render_bibliography(sources))

        feedback = list(notes)
        feedback.append(
            f"Daftar pustaka ({len(entries)} entri) ditulis ke {bibliography_path}."
        )
        if not pairs:
            feedback.append(
                "Bab ini tidak mengutip satu pun sumber, sehingga tidak ada \\cite "
                "yang ditulis."
            )

        if findings:
            feedback.extend(findings)
            feedback.append(
                f"Potongan LaTeX dihasilkan setelah {attempts} percobaan dan masih "
                "memuat temuan; peninjau diminta menilainya."
            )
            score = _SHORTFALL_SCORE
        else:
            feedback.append(
                f"Potongan LaTeX ditulis ke {chapter_path}: "
                f"{len(produced.citations)} sitasi, {len(produced.labels)} label."
            )
            score = 10

        return ReviewResult(
            gate=self.name,
            approved=True,
            score=score,
            feedback=tuple(feedback),
        )


@register_gate(LatexWriterGate.name)
def _build_latex_writer(context: GateContext) -> ReviewGate:
    """Bangun gate §25 dari konteksnya.

    Satu-satunya tempat ``router.chat("latex")`` disebut untuk gate ini. Perannya
    tersendiri di §6, dan model untuk peran itu dipilih dari konfigurasi seperti
    peran lainnya — bukan dari nama model yang ditulis di sini.

    **Bila LaTeX dimatikan, gate ini tetap ada — sebagai pass-through.** Bukan
    ``None``, dan bukan absen dari registri: bab yang melewati tahap ini harus
    terlihat sebagai bab yang **melewatinya**, bukan sebagai bab yang tidak
    pernah punya tahap itu. Karena itu yang dikembalikan adalah
    :class:`~agents.gates.PassThroughGate` dengan ``skipped=True`` — jejak yang
    sama dengan tahap yang dimatikan karena perkakasnya tidak ada.

    Konsekuensi kedua, dan itu yang membuat percabangan ini ada di sini alih-alih
    di ``book_director.py``: peran ``latex`` **tidak dipanggil** ketika LaTeX
    mati, sehingga konfigurasi tanpa peran itu tetap sah. Composition root hanya
    perlu meneruskan ``latex=None``.
    """
    if context.latex is None:
        return PassThroughGate(
            name=LatexWriterGate.name,
            produces=LatexWriterGate.produces,
            note=(
                "LaTeX dimatikan di konfigurasi (latex.enabled: false); "
                "sumber LaTeX tidak dihasilkan."
            ),
        )
    return LatexWriterGate(
        model=context.router.chat("latex"),
        prompts=context.prompts,
        artifacts=context.latex,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["LatexWriterAgent", "LatexWriterGate"]
