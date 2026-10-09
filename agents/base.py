"""Kerangka agent berkeluaran terstruktur.

Ini satu-satunya tempat di dalam ``agents/`` yang tahu cara memanggil LLM,
menegakkan skema, dan memperbaiki keluaran yang rusak. Agent konkret hanya
mendeklarasikan **peran**, **berkas prompt**, dan **cara menyiapkan konteks** —
tidak ada satu pun dari mereka yang menulis loop pemanggilan.

Dua keputusan yang perlu diketahui sebelum menyentuh berkas ini:

**``think`` bawaannya ``False``.** Model ``thinking`` dan keluaran JSON ketat
adalah pasangan yang buruk: token penalaran dibelanjakan lebih dulu, dan bila
anggarannya habis sebelum ``{`` pertama ditulis, server mengembalikan
``content`` **kosong** dengan ``done_reason='length'``. Perilaku ini
tereproduksi di mesin ini. Karena itu penalaran dimatikan sebagai bawaan, dan
parameter ``think`` tetap ada agar seseorang dapat bereksperimen secara sadar —
bukan agar ia menyala tanpa disadari.

**Perbaikan bukan retry.** Percobaan ulang di sini **mengubah prompt**: keluaran
yang rusak dan pesan validasinya disisipkan kembali, dan ``temperature``
diturunkan ke nol. Itu sebabnya ia berupa ``for`` loop berbatas, bukan
``tenacity`` — dan itu pula sebabnya ``tenacity`` tidak pernah diimpor di sini.
"""

from __future__ import annotations

from typing import Any, Generic, Mapping, Sequence, TypeVar, cast

from pydantic import BaseModel, ValidationError

from domain.errors import AgentOutputError
from domain.ports import ChatModel, ChatRequest, ChatResult, PromptLibrary, RenderedPrompt
from domain.structured import parse_output, strict_schema

TOut = TypeVar("TOut", bound=BaseModel)

#: Batas panjang teks rusak yang disisipkan kembali ke prompt perbaikan.
#: Keluaran yang terpotong bisa sangat panjang; menyisipkannya utuh akan
#: menghabiskan konteks dan membuat percobaan perbaikan justru lebih buruk
#: daripada percobaan pertama.
_MAX_ECHO_CHARS = 4000

_REPAIR_INSTRUCTION = """
---

# PERBAIKAN KELUARAN

Percobaan sebelumnya menghasilkan keluaran yang **tidak dapat diparsing** menjadi
JSON yang sesuai skema di atas.

**Keluaran yang rusak** (mungkin terpotong di bagian akhir):

```
{echo}
```

**Kesalahan:**

```
{error}
```

Perbaiki. Kembalikan **hanya satu objek JSON** yang sesuai skema — tanpa
penjelasan, tanpa prakata, tanpa pagar markdown. Seluruh field wajib ada.
"""


class StructuredAgent(Generic[TOut]):
    """Agent yang keluarannya selalu sebuah model Pydantic.

    Tidak menyimpan state bisnis: seluruh keadaan hidup di nilai balik dan di
    checkpoint. Ini yang membuat agent dapat dipanggil dalam urutan apa pun,
    diulang, dan diuji tanpa urutan setup tertentu.
    """

    #: Nama peran untuk logging (§38). Bukan nama model — model datang dari router.
    role: str = "agent"

    #: Nama prompt di :class:`~app.prompting.FilePromptLibrary`.
    prompt_name: str = ""

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        max_repair_attempts: int = 2,
        think: bool = False,
    ) -> None:
        self._model = model
        self._prompts = prompts
        self._max_repair_attempts = max(max_repair_attempts, 0)
        self._think = think

    # -- konfigurasi (dibaca, tidak diubah) --------------------------------
    def output_model_for(self, prompt_name: str) -> type[TOut]:
        """Model Pydantic yang dideklarasikan berkas prompt ``prompt_name``."""
        return cast("type[TOut]", self._prompts.output_model_for(prompt_name))

    @property
    def output_model(self) -> type[TOut]:
        """Model Pydantic yang dideklarasikan berkas prompt utama agent ini."""
        return self.output_model_for(self.prompt_name)

    # -- alur utama --------------------------------------------------------
    def generate(
        self,
        context: Mapping[str, Any],
        *,
        prompt_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        sources: Sequence[str] = (),
    ) -> TOut:
        """Render prompt, panggil model, dan kembalikan keluaran yang tervalidasi.

        :param prompt_name: berkas prompt yang dipakai, bila bukan
            :attr:`prompt_name` milik agent. Ada karena satu agent dapat memiliki
            lebih dari satu kontrak prompt — ``ChapterWriter`` menulis dan
            merevisi dengan kontrak yang berbeda. Yang **tidak** boleh berbeda
            adalah tangga perbaikannya, dan itulah sebabnya parameter ini ada
            di sini alih-alih menjadikan "revisi" subclass tersendiri: menyalin
            loop perbaikan ke subclass berarti dua tempat yang harus dijaga
            sinkron.

        :param sources: penanda bahan yang ikut menentukan permintaan ini — id
            potongan bukti dari basis pengetahuan (§13, §38). Diberikan hanya
            oleh agent yang benar-benar mengambil bukti; sisanya membiarkannya
            kosong, dan catatan §38 menulisnya sebagai daftar kosong alih-alih
            menebaknya dari isi prompt.

        :raises AgentOutputError: bila seluruh percobaan (termasuk perbaikan)
            gagal menghasilkan keluaran yang valid.
        :raises TruncatedOutputError: bila model kehabisan anggaran token.
            Sengaja **tidak** diperbaiki: memperbaiki prompt tidak akan menolong
            keluaran yang terpotong — yang perlu dinaikkan adalah ``max_tokens``.
        """
        name = prompt_name or self.prompt_name
        model = self.output_model_for(name)
        schema = strict_schema(model)
        rendered = self._prompts.render(name, context)

        raw = ""
        last_error: Exception | None = None
        attempts = 0

        for attempt in range(self._max_repair_attempts + 1):
            attempts = attempt + 1
            repair = attempt > 0
            user = (
                self._repair_prompt(rendered.user, raw, last_error) if repair else rendered.user
            )

            result = self._call(
                system=rendered.system,
                user=user,
                schema=schema,
                prompt_version=self._prompt_version(rendered),
                # Percobaan perbaikan berjalan deterministik: kita ingin model
                # memperbaiki kesalahan yang sudah ditunjuk, bukan berkreasi.
                temperature=0.0 if repair else temperature,
                max_tokens=max_tokens,
                sources=sources,
            )
            raw = result.text

            try:
                return parse_output(raw, model)
            except (ValidationError, ValueError) as exc:
                last_error = exc

        # Teks mentah ikut dibawa di dalam exception, bukan disimpan di sini:
        # agent tidak tahu bab mana yang sedang dikerjakannya, dan ia memang tidak
        # boleh tahu. Yang tahu nomor bab adalah pemanggil yang menangkapnya
        # (§37) — di sanalah bukti itu dituliskan ke ``state/parse_fail/``.
        raise AgentOutputError(self.role, raw, attempts, last_error)

    # -- langkah-langkah ---------------------------------------------------
    def _prompt_version(self, rendered: RenderedPrompt) -> str:
        """Tanda versi prompt untuk log (§38): ``versi+hash`` (MURNI).

        Dua-duanya, bukan salah satu: ``version`` ditulis manusia dan karena itu
        dapat dibaca; ``hash`` dihitung dari isi berkas dan karena itu tidak
        dapat lupa dinaikkan. Versi saja akan berbohong tepat pada kasus yang
        paling perlu ditangkap — prompt yang disunting tanpa menaikkan versinya.
        """
        return f"{rendered.version}+{rendered.hash}"

    def _call(
        self,
        *,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        prompt_version: str,
        temperature: float | None,
        max_tokens: int | None,
        sources: Sequence[str] = (),
    ) -> ChatResult:
        """Satu panggilan model. Skema selalu dikirim — pada setiap percobaan.

        Bukan hanya pada percobaan pertama: skema itulah yang menegakkan
        keluaran, dan mencabutnya saat perbaikan justru membuat percobaan
        terakhir paling longgar.
        """
        return self._model.complete(
            ChatRequest(
                model="",  # dibiarkan kosong: adapter memakai model peran dari ModelSpec
                system=system,
                user=user,
                format_schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
                think=self._think,
                role=self.role,
                prompt_version=prompt_version,
                sources=tuple(sources),
            )
        )

    def _repair_prompt(self, original_user: str, raw: str, error: Exception | None) -> str:
        """Prompt asli + keluaran rusak + pesan kesalahan (MURNI).

        Prompt asli dikirim ulang **utuh**, sehingga kontrak OUTPUT tetap ada di
        dalam konteks. Mengirim hanya instruksi perbaikan akan menghilangkan
        skema yang justru ingin ditegakkan.
        """
        echo = raw if len(raw) <= _MAX_ECHO_CHARS else "…" + raw[-_MAX_ECHO_CHARS:]
        return original_user + _REPAIR_INSTRUCTION.format(
            echo=echo.strip() or "(kosong)",
            error=str(error) if error else "(tidak ada)",
        )


__all__ = ["StructuredAgent"]
