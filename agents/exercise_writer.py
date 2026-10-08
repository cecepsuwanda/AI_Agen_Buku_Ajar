"""Exercise Agent dan gate-nya (§20).

§20 meminta latihan yang **menjangkau beberapa tingkat kesulitan** dan menguji
tujuan pembelajaran bab. Yang pertama diperiksa secara mekanis; yang kedua
**tidak**, dan perbedaan itu disengaja:

* **Tingkat kesulitan** diperiksa dan **dihitung**. Label yang berada di luar
  ``mudah``/``sedang``/``sulit`` adalah kekeliruan yang pasti — ia tercetak di
  buku, dan prompt sudah menyebut ketiga nilai yang sah secara harfiah. Sebaran
  tingkatnya sendiri **dihitung**, bukan ditanyakan: field ``difficulty_mix``
  yang diisi model adalah ringkasan yang dikarang, sedangkan yang dihitung dari
  butir yang benar-benar ada selalu lebih benar.
* **Kesesuaian latihan dengan tujuan** tidak diperiksa di sini. Itu pertanyaan
  penilaian: membandingkan teks ``objective`` dengan daftar tujuan secara
  mekanis akan menolak latihan yang baik hanya karena tujuannya diparafrase,
  dan setiap penolakan seperti itu berharga dua panggilan model plus satu skor
  rendah yang tidak berdasar. §23 menugaskan penilaian ini kepada Pedagogy
  Reviewer, dan di sanalah ia akan dilakukan — dengan pertimbangan, bukan
  dengan pencocokan teks.

Peran model untuk gate ini adalah ``writer``, bukan ``code`` seperti gate §19:
latihan adalah **prosa** — soal, petunjuk, kunci jawaban — dan memakai model
yang sama dengan penulis bab membuat gaya bahasanya konsisten dengan bab yang
sedang dilatihkan. Soal yang ditulis dengan istilah yang tidak dipakai babnya
adalah soal yang membingungkan.

Seperti gate §19, gate ini **selalu meloloskan** drafnya: ia tahap penulisan,
bukan tahap penilaian. Wewenang menolak bab ada pada peninjau, yang melihat
``draft.exercises`` lewat ``draft_json`` dan memang memegang wewenang itu.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, ReviewResult
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.examples import (
    ExerciseSet,
    describe_mix,
    difficulty_mix,
    inspect_exercises,
    render_exercises,
)
from domain.ports import ChatModel, PromptLibrary
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, register_gate

#: Skor ketika sebagian latihan tetap tidak memenuhi syarat (lihat komentar
#: yang sama di ``agents/example_writer.py``).
_SHORTFALL_SCORE = 5


class ExerciseWriterAgent(StructuredAgent[ExerciseSet]):
    """Menghasilkan latihan untuk satu bab (§20)."""

    role = "writer"
    prompt_name = "exercise.chapter"

    def write(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        research: ResearchPackage,
        feedback: tuple[str, ...] = (),
    ) -> ExerciseSet:
        """Hasilkan latihan untuk ``draft`` (§20)."""
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "objectives": spec.objectives,
                "required_exercises": spec.required_exercises,
                "draft_json": draft.model_dump_json(indent=2),
                "research": research,
                "feedback": feedback,
            }
        )


class ExerciseWriterGate:
    """Gate §20: perkaya draf dengan latihan, lalu periksa latihannya."""

    #: Nama gate di ``config.yaml`` — ``pipeline.gates: [..., exercise_writer, ...]``.
    name = "exercise_writer"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.EXERCISES_WRITTEN

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = ExerciseWriterAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._repair_attempts = max(repair_attempts, 0)

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Hasilkan latihan untuk bab pada ``record`` (§20).

        :raises GatePreconditionError: bila spesifikasi atau draf belum ada.
            Latihan harus menguji isi bab, dan isi bab itu hanya diketahui dari
            drafnya.
        :raises AgentOutputError: bila model gagal menghasilkan ``ExerciseSet``
            yang dapat di-parse. Ditahan per bab oleh ``BookDirector`` (§35).
        """
        spec = record.spec
        if spec is None:
            raise GatePreconditionError(self.name, record.number, "spesifikasi bab")
        draft = record.draft
        if draft is None:
            raise GatePreconditionError(self.name, record.number, "draf")

        required = max(spec.required_exercises, 0)
        if required == 0:
            return ReviewResult(
                gate=self.name,
                approved=True,
                score=10,
                feedback=("Spesifikasi bab tidak meminta satu pun latihan.",),
                skipped=True,
            )

        research = record.research or ResearchPackage.empty()
        produced = ExerciseSet()
        findings: tuple[str, ...] = ()
        attempts = 0

        for attempt in range(self._repair_attempts + 1):
            attempts = attempt + 1
            produced = self._agent.write(
                draft, spec=spec, book=book, research=research, feedback=findings
            )
            enough, findings = inspect_exercises(produced.exercises, required=required)
            if enough:
                break

        rendered = render_exercises(produced.exercises)
        mix = difficulty_mix(produced.exercises)
        feedback = list(produced.notes)

        if not rendered:
            feedback.append(
                f"Tidak satu pun latihan dapat dipakai setelah {attempts} percobaan; "
                f"{required} latihan diminta oleh spesifikasi bab."
            )
            score = 0
        elif findings:
            feedback.extend(findings)
            feedback.append(
                f"Latihan yang dihasilkan belum memenuhi syarat setelah {attempts} "
                "percobaan; peninjau diminta menilainya."
            )
            score = _SHORTFALL_SCORE
        else:
            feedback.append(f"{len(rendered)} latihan dihasilkan dari {required} yang diminta.")
            score = 10

        # Sebaran tingkat kesulitan dilaporkan apa adanya, termasuk ketika semua
        # latihan jatuh di satu tingkat: §20 meminta jangkauan, dan jangkauan
        # yang sempit adalah hal yang harus terlihat di laporan, bukan yang
        # dirapikan agar terlihat baik.
        feedback.append(f"Tingkat kesulitan: {describe_mix(mix)}.")

        if draft.exercises:
            feedback.append(
                f"{len(draft.exercises)} latihan dari penulis digantikan oleh hasil "
                "tahap latihan (§20)."
            )

        return ReviewResult(
            gate=self.name,
            approved=True,
            score=score,
            feedback=tuple(feedback),
            enriched_draft=draft.model_copy(update={"exercises": rendered}),
        )


@register_gate(ExerciseWriterGate.name)
def _build_exercise_writer(context: GateContext) -> ExerciseWriterGate:
    """Bangun gate §20 dari konteksnya."""
    return ExerciseWriterGate(
        model=context.router.chat("writer"),
        prompts=context.prompts,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["ExerciseWriterAgent", "ExerciseWriterGate"]
