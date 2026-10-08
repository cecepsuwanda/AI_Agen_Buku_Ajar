"""Adapter ``--dry-run``: pipeline sungguhan, nol panggilan jaringan.

``DryRunChatModel`` menempati posisi :class:`~models.ollama_client.OllamaChatModel`
di dalam :class:`~app.container.ChatModelFactory` — ia disuntikkan lewat
parameter ``chat_override`` yang sama yang dipakai suite offline. Karena itu
``--dry-run`` **tidak** punya jalur kode sendiri: perintah yang dijalankan
benar-benar ``BookDirector.run``, dengan planner, chapter planner, writer, gate,
checkpoint, dan penulisan Markdown yang sama persis.

Itulah yang membuatnya berguna. Cara lain — mode "hitung saja, jangan kirim" —
akan menempuh jalur yang berbeda dari produksi, dan jalur yang berbeda adalah
jalur yang tidak terbukti. Yang diuji jalur produksinya, bukan mode uji coba.

Dua hal yang dilakukannya sebagai ganti mengirim HTTP:

1. **Menulis setiap prompt ke ``state/dryrun/NN-peran.txt``** — system, user,
   dan skema ``format=`` mentah. Inilah keluaran yang sebenarnya dicari saat
   seseorang menjalankan ``--dry-run``: prompt seperti apa yang akan dikirim.
2. **Mengembalikan instance yang benar-benar lolos validasi**, disintesis dari
   ``request.format_schema``. Bukan teks palsu: pipeline menuntut JSON yang sah,
   dan mengembalikan yang tidak sah hanya akan menggagalkan run di bab pertama
   dengan pesan yang menyesatkan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from domain.errors import ConfigError
from domain.ports import ChatRequest, ChatResult
from domain.structured import example_instance
from models.model_registry import ModelSpec
from models.request_options import describe_options, resolve_options

#: Awalan nama berkas prompt yang ditulis, mis. ``01-planner.txt``.
FILE_TEMPLATE = "{index:02d}-{role}.txt"

#: Nilai yang ditimpa pada instance hasil sintesis, berdasarkan nama properti.
#:
#: Bawaannya sengaja membuat pipeline **lulus**: peninjau menjawab "setuju".
#: ``--dry-run`` yang berhenti di revisi hanya akan menulis prompt peninjau satu
#: kali, padahal yang ingin dilihat orang adalah seluruh prompt yang mungkin
#: dikirim. Untuk melihat jalur revisi, timpa ``approved`` menjadi ``False``.
#:
#: ``difficulty`` ada di sini karena alasan yang sama seperti ``approved``:
#: nilainya **tidak dapat ditebak dari skema**. Skema hanya menyatakan
#: ``string``, dan sintesis yang menuliskan nama field-nya sendiri ("difficulty")
#: menghasilkan label yang tidak sah menurut ``DIFFICULTY_LEVELS`` — sehingga
#: gate latihan (§20) akan meminta perbaikan berulang atas kekurangan yang
#: dibuat oleh penyintesis, bukan oleh pipeline. Nilai yang sah tidak dapat
#: disimpulkan dari tipe, jadi ia harus dinyatakan.
DEFAULT_OVERRIDES: Mapping[str, Any] = {
    "approved": True,
    "score": 9,
    "skipped": False,
    "error": None,
    "difficulty": "mudah",
}


class DryRunChatModel:
    """ :class:`domain.ports.ChatModel` yang menulis prompt, bukan mengirimnya.

    Mematuhi port secara struktural. Tidak menyimpan state bisnis: yang disimpan
    hanyalah urutan panggilan, supaya berkas prompt bernomor dan dapat dibaca
    dalam urutan yang sama dengan urutan pipeline menjalankannya.

    ``specs`` adalah peta peran → :class:`~models.model_registry.ModelSpec` dari
    registry. Ia **bukan** dependensi yang diperlukan agar pipeline berjalan —
    hanya agar berkas yang ditulis menyebut model dan angka yang benar-benar akan
    dipakai peran itu. Diberikan sebagai peta, bukan registry utuh, supaya adapter
    ini tidak dapat memakainya untuk hal lain.
    """

    def __init__(
        self,
        directory: Path,
        *,
        specs: Mapping[str, ModelSpec] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self._directory = Path(directory)
        self._specs: Mapping[str, ModelSpec] = dict(specs or {})
        self._overrides: Mapping[str, Any] = {**DEFAULT_OVERRIDES, **(overrides or {})}
        self._calls: list[ChatRequest] = []

    # -- domain.ports.ChatModel -------------------------------------------
    def complete(self, request: ChatRequest) -> ChatResult:
        """Tulis prompt ke disk, lalu kembalikan balasan yang sah.

        :raises ConfigError: bila permintaan tidak menyertakan ``format_schema``.
            Pipeline ini tidak punya panggilan model tanpa kontrak keluaran;
            bila itu terjadi, prompt yang bersangkutan kehilangan
            ``output_model`` di front-matter-nya — dan lebih baik gagal di sini
            dengan menunjuk berkasnya daripada menghasilkan bab yang kosong.
        """
        schema = request.format_schema
        if not schema:
            raise ConfigError(
                f"Peran {request.role!r} memanggil model tanpa format_schema, sehingga "
                f"--dry-run tidak dapat mensintesis balasan yang sah. Periksa kunci "
                f"'output_model' di front-matter prompt peran tersebut."
            )

        self._calls.append(request)
        index = len(self._calls)
        self._write_prompt(index, request, schema)
        payload = example_instance(schema, self._overrides)

        return ChatResult(
            text=json.dumps(payload, ensure_ascii=False),
            model=request.model,
            done_reason="stop",
            latency_ms=0,
        )

    # -- pemeriksaan untuk pemanggil --------------------------------------
    @property
    def calls(self) -> tuple[ChatRequest, ...]:
        """Seluruh permintaan yang dicegat, berurutan."""
        return tuple(self._calls)

    @property
    def directory(self) -> Path:
        """Direktori tempat prompt ditulis."""
        return self._directory

    def prompt_path(self, index: int, role: str) -> Path:
        """Jalur berkas prompt untuk panggilan ke-``index`` (1-based)."""
        return self._directory / FILE_TEMPLATE.format(index=index, role=role)

    # -- penulisan prompt --------------------------------------------------
    def _write_prompt(self, index: int, request: ChatRequest, schema: Mapping[str, Any]) -> Path:
        """Tulis satu prompt lengkap ke ``state/dryrun/``, kembalikan jalurnya.

        Ditulis dengan :meth:`Path.write_text` biasa, bukan
        :func:`~memory.checkpoints.atomic_write_text`: ini artefak sekali pakai
        untuk dibaca manusia, tidak ada proses lain yang membacanya, dan
        ``state/dryrun/`` tidak pernah menjadi masukan bagi apa pun. Atomisitas
        di sini hanya akan menambah lapisan tanpa melindungi apa pun.
        """
        path = self.prompt_path(index, request.role)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render(index, request, schema), encoding="utf-8", newline="\n")
        return path

    def _render(self, index: int, request: ChatRequest, schema: Mapping[str, Any]) -> str:
        """Susun isi berkas prompt (MURNI).

        Baris ``model`` dan ``opsi`` memakai :func:`~models.request_options.resolve_options`
        — fungsi yang sama yang menyusun payload sungguhan. Tanpa itu, berkas ini
        akan menulis ``model:`` yang kosong dan ``temperature=None``: jujur
        tentang isi permintaan, tetapi menyesatkan tentang satu-satunya hal yang
        tidak dapat dibaca dari prompt itu sendiri, yaitu **ke model mana dan
        dengan angka berapa** ia akan dikirim. ``request.model`` sendiri memang
        sengaja kosong: yang menentukan model adalah spesifikasi peran, bukan agent.

        ``format=`` disertakan meskipun kontrak OUTPUT sudah ada di dalam prompt
        user: yang satu adalah dokumentasi untuk model, yang lain adalah grammar
        yang benar-benar membatasi *decoding*. Keduanya bisa berbeda, dan
        perbedaan itulah yang perlu terlihat saat prompt sedang ditelusuri.
        """
        spec = self._specs.get(request.role)
        model = request.model or (spec.model if spec is not None else "(tidak dipetakan)")
        options = resolve_options(request, spec) if spec is not None else {}

        lines = [
            f"# dry-run: panggilan ke-{index}",
            f"# peran   : {request.role}",
            f"# model   : {model}",
            f"# opsi    : {describe_options(dict(options))}",
            f"# prompt  : versi {request.prompt_version}",
            f"# think   : {request.think}",
            "# Tidak ada permintaan jaringan yang dikirim.",
            "",
            "=" * 72,
            "SYSTEM",
            "=" * 72,
            request.system or "(kosong)",
            "",
            "=" * 72,
            "USER",
            "=" * 72,
            request.user,
            "",
            "=" * 72,
            "FORMAT (JSON Schema yang membatasi decoding)",
            "=" * 72,
            json.dumps(schema, indent=2, ensure_ascii=False),
            "",
        ]
        return "\n".join(lines)


__all__ = ["DEFAULT_OVERRIDES", "FILE_TEMPLATE", "DryRunChatModel"]
