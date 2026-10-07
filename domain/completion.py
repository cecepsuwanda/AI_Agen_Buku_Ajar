"""Kebijakan atas hasil satu panggilan model — MURNI.

Aturan di sini nyaris seluruhnya lahir dari pengujian nyata di mesin target,
dan ketiganya punya satu sifat yang sama: **melanggar salah satunya tidak
menghasilkan crash**, melainkan bab yang salah isi.

1. ``done_reason == "length"`` berarti keluaran terpotong. Perbaikannya adalah
   menaikkan anggaran token, **bukan** memperbaiki prompt — jadi ia punya error
   tersendiri, bukan diperlakukan sebagai "JSON rusak".
2. Pada model ``thinking``, jejak penalaran dapat menghabiskan seluruh anggaran
   sehingga ``content`` keluar kosong. Kosongnya bukan "model tidak menjawab".
3. Jejak penalaran **tidak pernah** disambungkan ke ``text``. Tercampurnya ia ke
   draf bab adalah kegagalan yang paling sunyi dari ketiganya: JSON tetap
   di-parse, bab tetap tersimpan, dan hanya pembacanya yang tahu ada yang aneh.

**Mengapa ia fungsi murni di ``domain/``, bukan beberapa baris di dalam
adapter.** Adapter adalah satu-satunya berkas yang boleh menyentuh SDK, dan
karena itu satu-satunya berkas yang **tidak dapat** diimpor oleh tes mana pun —
``tests/unit/test_architecture.py`` menegakkan ``import ollama`` muncul di tepat
satu berkas, sehingga tes yang mengimpornya akan menggagalkan gerbang itu
sendiri. Kebijakan yang tinggal di sana tidak dapat diuji tanpa jaringan. Di
sini, dengan argumen berupa nilai primitif, ia dapat diuji habis-habisan.
"""

from __future__ import annotations

from domain.errors import TruncatedOutputError
from domain.ports import ChatResult

#: Alasan kegagalan yang dilaporkan saat anggaran token habis di jejak penalaran.
_THINKING_REASON = "jejak-penalaran-menghabiskan-anggaran"


def interpret_completion(
    *,
    model: str,
    content: str,
    thinking: str | None,
    done_reason: str,
    max_tokens: int | None,
    latency_ms: int = 0,
    prompt_tokens: int = 0,
    output_tokens: int = 0,
) -> ChatResult:
    """Terjemahkan hasil mentah satu panggilan menjadi :class:`ChatResult`.

    Menerima nilai primitif, bukan objek SDK: itulah yang membuatnya murni dan
    dapat dipanggil tes tanpa ``ollama`` terpasang.

    :raises TruncatedOutputError: bila keluaran terpotong, baik karena
        ``done_reason == 'length'`` maupun karena jejak penalaran menghabiskan
        seluruh anggaran sehingga ``content`` kosong.
    """
    text = content.strip()

    if done_reason == "length":
        raise TruncatedOutputError(model, max_tokens, "length")

    if not text and thinking:
        raise TruncatedOutputError(model, max_tokens, _THINKING_REASON)

    return ChatResult(
        # HANYA ``content``. ``thinking`` ikut dilaporkan untuk logging (§38) dan
        # tidak pernah disambungkan ke sini.
        text=text,
        model=model,
        thinking=thinking,
        done_reason=done_reason,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )


__all__ = ["interpret_completion"]
