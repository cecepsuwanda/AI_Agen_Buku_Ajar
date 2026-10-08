"""Example Agent dan gate-nya (§19).

§19 menaruh Example Agent **sesudah** Chapter Writer, lalu menambahkan Code
Reviewer di belakangnya::

    Chapter Writer -> Contoh -> Code Reviewer

**Mengapa kedua peran itu hidup di dalam satu gate.** "Code Reviewer" pada §19
bukan *reviewer* yang menilai prosa — ia memeriksa hal-hal yang dapat
dipastikan tentang sebuah contoh: apakah ada contohnya, apakah setiap contoh
kode punya penjelasan, apakah ada contoh yang berulang persis. Pertanyaan-
pertanyaan itu tidak membutuhkan model kedua; ia membutuhkan **kriteria**.
Karena itu pemeriksaannya deterministik (``domain.examples.inspect_examples``),
dan yang memakai model hanyalah **tangga perbaikan**: contoh yang tidak lolos
dikembalikan ke model yang sama beserta temuannya, maksimum
``repair_attempts`` kali. Itu pola yang sama dengan tangga perbaikan JSON di
``agents/base.py`` — dan alasannya sama: keluaran yang salah bentuk diperbaiki
dengan memberi tahu apa yang salah, bukan dengan mengulang pertanyaan yang sama.

**Mengapa gate ini selalu meloloskan drafnya.** Ia bukan tahap penilaian,
melainkan tahap **penulisan**. Kekurangan contoh adalah kekurangan bahan, dan
menolak bab karena itu berarti mengembalikan seluruh bab — termasuk bagian yang
sudah benar — kepada penulis, yang tidak dapat memperbaikinya: penulis tidak
membuat contoh. Karena itu kekurangan yang tersisa dilaporkan dengan skor
rendah dan catatan yang keras, lalu diteruskan ke peninjau sungguhan, yang
**dapat** menolak bab dan memang memegang wewenang itu. Total kegagalan
menghasilkan contoh (``AgentOutputError``) bukan vonis, melainkan kesalahan
yang dapat ditahan per bab (§35) — bab itu ditandai ``FAILED``.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, ReviewResult
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.examples import (
    ExampleSet,
    inspect_examples,
    render_examples,
)
from domain.ports import ChatModel, PromptLibrary
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, register_gate

#: Skor yang dilaporkan gate ketika sebagian contoh tetap tidak memenuhi syarat
#: setelah seluruh percobaan perbaikan habis. Bukan nol: nol berarti "tidak ada
#: apa-apa yang dihasilkan", dan menyamakan keduanya akan menghapus satu-satunya
#: isyarat yang membedakan "contohnya kurang" dari "contohnya tidak ada".
_SHORTFALL_SCORE = 5


class ExampleWriterAgent(StructuredAgent[ExampleSet]):
    """Menghasilkan contoh beserta penjelasannya untuk satu bab (§19)."""

    role = "code"
    prompt_name = "example.chapter"

    def write(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        research: ResearchPackage,
        feedback: tuple[str, ...] = (),
    ) -> ExampleSet:
        """Hasilkan contoh untuk ``draft`` (§19).

        :param feedback: temuan pemeriksaan atas percobaan sebelumnya. Dikirim
            sebagai bagian dari prompt, bukan sebagai percobaan ulang yang buta.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "objectives": spec.objectives,
                "required_examples": spec.required_examples,
                "draft_json": draft.model_dump_json(indent=2),
                "research": research,
                "feedback": feedback,
            }
        )


class ExampleWriterGate:
    """Gate §19: perkaya draf dengan contoh, lalu periksa contohnya.

    Ia mengembalikan draf yang sudah diperkaya lewat
    :attr:`~domain.chapter.ReviewResult.enriched_draft`. Itulah yang membuat
    ``BookDirector`` tidak perlu tahu apa pun tentang gate ini: baginya ia
    hanyalah gate yang lulus, dan drafnya sudah berubah.
    """

    #: Nama gate di ``config.yaml`` — ``pipeline.gates: [example_writer, ...]``.
    name = "example_writer"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.EXAMPLES_WRITTEN

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = ExampleWriterAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._repair_attempts = max(repair_attempts, 0)

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Hasilkan contoh untuk bab pada ``record`` (§19).

        :raises GatePreconditionError: bila spesifikasi atau draf belum ada.
            Menghasilkan contoh untuk bab yang belum ditulis berarti menebak
            isi babnya — dan contoh yang tidak nyambung dengan penjelasannya
            lebih buruk daripada tidak ada contoh sama sekali.
        :raises AgentOutputError: bila model gagal menghasilkan ``ExampleSet``
            yang dapat di-parse. Ditahan per bab oleh ``BookDirector`` (§35).
        """
        spec = record.spec
        if spec is None:
            raise GatePreconditionError(self.name, record.number, "spesifikasi bab")
        draft = record.draft
        if draft is None:
            raise GatePreconditionError(self.name, record.number, "draf")

        required = max(spec.required_examples, 0)
        if required == 0:
            # Bab yang tidak meminta contoh bukan bab yang gagal memilikinya.
            # Gate ini tidak memeriksa apa pun, dan ia mengatakannya.
            return ReviewResult(
                gate=self.name,
                approved=True,
                score=10,
                feedback=("Spesifikasi bab tidak meminta satu pun contoh.",),
                skipped=True,
            )

        research = record.research or ResearchPackage.empty()
        produced = ExampleSet()
        findings: tuple[str, ...] = ()
        attempts = 0

        for attempt in range(self._repair_attempts + 1):
            attempts = attempt + 1
            produced = self._agent.write(
                draft, spec=spec, book=book, research=research, feedback=findings
            )
            enough, findings = inspect_examples(produced.examples, required=required)
            if enough:
                break

        rendered = render_examples(produced.examples)
        feedback = list(produced.notes)

        if not rendered:
            feedback.append(
                f"Tidak satu pun contoh dapat dipakai setelah {attempts} percobaan; "
                f"{required} contoh diminta oleh spesifikasi bab."
            )
            score = 0
        elif findings:
            feedback.extend(findings)
            feedback.append(
                f"Contoh yang dihasilkan belum memenuhi syarat setelah {attempts} "
                "percobaan; peninjau diminta menilainya."
            )
            score = _SHORTFALL_SCORE
        else:
            feedback.append(f"{len(rendered)} contoh dihasilkan dari {required} yang diminta.")
            score = 10

        if draft.examples:
            # Draf dari Chapter Writer mungkin sudah membawa contoh. Yang dipakai
            # adalah hasil tahap ini (§19 memang menaruh Example Agent sesudah
            # penulis), tetapi menggantinya diam-diam akan menyembunyikan bahwa
            # penulis membayar token untuk bahan yang tidak terpakai.
            feedback.append(
                f"{len(draft.examples)} contoh dari penulis digantikan oleh hasil "
                "tahap contoh (§19)."
            )

        return ReviewResult(
            gate=self.name,
            approved=True,
            score=score,
            feedback=tuple(feedback),
            enriched_draft=draft.model_copy(update={"examples": rendered}),
        )


@register_gate(ExampleWriterGate.name)
def _build_example_writer(context: GateContext) -> ExampleWriterGate:
    """Bangun gate §19 dari konteksnya.

    Satu-satunya tempat ``router.chat("code")`` disebut untuk gate ini. Model
    untuk peran itu dipilih karena contoh butuh ketepatan sintaks dan kelakuan
    program — bukan karena ia "model kode" pada namanya.
    """
    return ExampleWriterGate(
        model=context.router.chat("code"),
        prompts=context.prompts,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["ExampleWriterAgent", "ExampleWriterGate"]
