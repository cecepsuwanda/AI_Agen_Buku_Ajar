"""Basis bersama untuk seluruh model domain — MURNI (hanya stdlib + pydantic)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Model domain: imutabel, dan menolak field yang tidak dikenal.

    ``frozen=True`` — setiap perubahan menghasilkan objek **baru**
    (``model_copy(update=...)``). Tidak ada state yang termutasi diam-diam,
    yang membuat checkpoint dan pengujian jauh lebih mudah diprediksi.

    ``extra="forbid"`` — bila LLM berhalusinasi menambah field, kita ingin itu
    **gagal keras**. Menelan field asing diam-diam adalah bug yang baru terlihat
    jauh kemudian, saat bab sudah terlanjur ditulis tanpa data yang dimaksud.

    Catatan: ``frozen`` di Pydantic membekukan *atribut*, bukan isi
    ``dict``/``list`` bersarang. Karena itu seluruh koleksi di domain ini
    memakai ``tuple`` dan ``Mapping`` (bukan ``list``/``dict``) agar tidak ada
    yang bisa menyusup lewat referensi.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
