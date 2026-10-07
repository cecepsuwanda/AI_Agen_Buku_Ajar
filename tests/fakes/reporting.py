"""``Reporter`` palsu yang merekam, bukan mencetak.

Reporter asli menggambar tabel rich ke terminal. Tes yang memeriksa keluarannya
berarti menguji tampilan, bukan perilaku — dan tampilan berubah jauh lebih
sering daripada aturan yang dilaporkannya.

Yang perlu diperiksa adalah **apa yang dilaporkan**: bahwa bab yang gagal
memang diberitahukan, bahwa gate yang dilewati menyebut dirinya dilewati, bahwa
catatan rekonsiliasi tidak ditelan. Semuanya terekam di sini, dalam bentuk yang
dapat ditanyakan tanpa mencocokkan string tabel.

Kelas ini juga sengaja **tidak pernah melempar**. Reporter yang melempar di
tengah pipeline akan tampak seperti kegagalan agent, dan itu menyesatkan.
"""

from __future__ import annotations

from typing import Literal

from domain.chapter import ChapterRecord, ReviewResult
from domain.enums import ChapterStatus

#: Jenis peristiwa yang direkam.
EventKind = Literal["info", "warn", "started", "stage", "gate", "finished"]


class RecordingReporter:
    """Merekam setiap panggilan pelaporan sebagai ``(jenis, pesan)``."""

    def __init__(self) -> None:
        self.events: list[tuple[EventKind, str]] = []

    # -- port Reporter -----------------------------------------------------
    def info(self, message: str) -> None:
        self.events.append(("info", message))

    def warn(self, message: str) -> None:
        self.events.append(("warn", message))

    def chapter_started(self, number: int, title: str) -> None:
        self.events.append(("started", f"{number}:{title}"))

    def stage(self, number: int, status: ChapterStatus) -> None:
        self.events.append(("stage", f"{number}:{status}"))

    def gate_result(self, number: int, result: ReviewResult) -> None:
        verdict = "lulus" if result.approved else "tolak"
        skipped = " (dilewati)" if result.skipped else ""
        self.events.append(("gate", f"{number}:{result.gate}:{verdict}{skipped}"))

    def chapter_finished(self, record: ChapterRecord) -> None:
        self.events.append(("finished", f"{record.number}:{record.status}"))

    # -- bantuan tes -------------------------------------------------------
    def of_kind(self, kind: EventKind) -> tuple[str, ...]:
        """Seluruh pesan dengan jenis tertentu, berurutan."""
        return tuple(message for event_kind, message in self.events if event_kind == kind)

    def messages(self) -> tuple[str, ...]:
        """Seluruh pesan, tanpa memandang jenisnya."""
        return tuple(message for _, message in self.events)

    def warnings(self) -> tuple[str, ...]:
        """Seluruh peringatan. Inilah yang biasanya diperiksa tes."""
        return self.of_kind("warn")

    def said(self, needle: str) -> bool:
        """True bila ada pesan apa pun yang memuat ``needle``.

        Sengaja pencocokan **substring**, bukan kesamaan: yang diperiksa adalah
        "apakah hal ini diberitahukan", dan tes yang mengunci kalimat lengkapnya
        akan gagal setiap kali pesannya diperbaiki — padahal justru memperbaiki
        pesan itu yang diinginkan.
        """
        return any(needle in message for message in self.messages())

    def clear(self) -> None:
        """Buang seluruh rekaman, untuk memeriksa satu fase saja."""
        self.events.clear()


__all__ = ["EventKind", "RecordingReporter"]
