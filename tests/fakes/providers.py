"""``ModelProvider`` palsu: satu model per peran, ditentukan dari tes.

Tanpa kelas ini, setiap tes integrasi harus menyusun sendiri peta peran→model,
dan setiap tes akan menyusunnya sedikit berbeda. Lebih buruk lagi: bila tiap
``chat(role)`` membangun model baru, model berskrip akan kehabisan balasan pada
panggilan kedua — karena antreannya ikut lahir ulang.

Dua janji yang dipegang kelas ini:

1. **Satu instance per peran, selamanya.** ``chat("writer")`` berkali-kali
   mengembalikan objek yang sama, sehingga penghitung panggilan dan antrean
   balasannya bermakna.
2. **``embedder`` melempar.** MVP ini tidak punya embedding sama sekali, dan
   permintaan embedding dari jalur kode mana pun adalah regresi yang harus
   terlihat keras — bukan ``NotImplementedError`` yang tertelan (ISP).
"""

from __future__ import annotations

from typing import Any

from tests.fakes.chat_models import RecordingChatModel


class StaticModelProvider:
    """Memetakan peran ke model yang sudah disiapkan tes."""

    def __init__(
        self,
        default: RecordingChatModel,
        **by_role: RecordingChatModel | None,
    ) -> None:
        self._default = default
        # Peran bernilai ``None`` diperlakukan sebagai "tidak diatur" supaya
        # pemanggilnya dapat menulis ``writer=writer_model, reviewer=None``
        # tanpa cabang if di tempat pemanggilan.
        self._by_role: dict[str, RecordingChatModel] = {
            role: model for role, model in by_role.items() if model is not None
        }
        self.requested: list[str] = []

    def chat(self, role: str) -> RecordingChatModel:
        """Model untuk ``role`` — instance yang sama pada setiap permintaan."""
        self.requested.append(role)
        return self._by_role.get(role, self._default)

    def embedder(self, role: str = "embedding") -> Any:
        """Selalu melempar: MVP ini tidak punya jalur embedding."""
        raise AssertionError(
            f"Pipeline MVP tidak boleh meminta model embedding (peran {role!r})."
        )

    # -- bantuan tes -------------------------------------------------------
    def total_calls(self) -> int:
        """Jumlah panggilan model dari **semua** model yang pernah diberikan.

        Inilah cara termurah membuktikan resume bekerja: ``run`` kedua atas buku
        yang sudah selesai harus menambah nol panggilan. Menghitungnya per model
        akan melewatkan panggilan dari peran yang tidak diperiksa tes.
        """
        return sum(model.call_count for model in self._all_models())

    def calls_for(self, role: str) -> int:
        """Jumlah panggilan model peran ``role``.

        **Peran yang tidak punya model sendiri akan menjawab dengan angka model
        bawaan** — dan model bawaan itu dipakai bersama oleh setiap peran yang
        tidak diatur, termasuk ``planner``. Jadi ``calls_for("chapter_planner")``
        pada provider yang tidak diberi ``chapter_planner`` mengembalikan jumlah
        panggilan **writer**, bukan nol. Peran yang hendak diukur harus diberi
        modelnya sendiri di :func:`build_director`.
        """
        return self.chat(role).call_count

    def _all_models(self) -> tuple[RecordingChatModel, ...]:
        """Setiap model unik — satu instance dapat melayani beberapa peran."""
        seen: dict[int, RecordingChatModel] = {id(self._default): self._default}
        for model in self._by_role.values():
            seen[id(model)] = model
        return tuple(seen.values())


__all__ = ["StaticModelProvider"]
