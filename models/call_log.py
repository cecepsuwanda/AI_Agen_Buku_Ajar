"""Catatan per panggilan model, dipisah per peran (§38).

Blueprint §38 meminta setiap panggilan dicatat — peran, model, versi prompt,
bahan yang diambil, keluaran, lama, dan pemakaian token — dan menyebutkan
alasannya sendiri: *"Hal ini penting untuk membandingkan model."* Perintah
``ai-book compare`` (§33) membaca berkas yang ditulis di sini, dan itulah
satu-satunya cara menjawab "model mana yang lebih baik untuk peran penulis"
dengan angka alih-alih dengan kesan tentang nama modelnya.

Tiga keputusan yang membentuk modul ini, dan alasannya masing-masing:

**Satu berkas per peran** — ``output/logs/writer.jsonl``, sesuai layout §38.
Yang membaca catatan ini hampir selalu ingin membandingkan **satu peran** lintas
model, dan berkas per peran membuat pembandingan itu selesai dengan satu
pembacaan berkas. Satu berkas besar akan menuntut setiap pembaca menyaring
terlebih dahulu, dan penyaring yang ditulis ulang di tiap pembaca adalah
penyaring yang cepat atau lambat berbeda satu sama lain.

**Pencatatnya membungkus, bukan mengubah.** :class:`CallLoggingModelSource`
menempati posisi :class:`~models.ollama_client.ChatModelFactory` di dalam
:class:`~models.model_router.ModelRouter` — titik tunggal tempat setiap agent
meminta modelnya. Karena itu tidak ada satu pun agent yang tahu panggilannya
dicatat, tidak ada satu pun yang dapat lupa mencatat, dan tidak ada tanda tangan
method yang berubah karenanya.

**Kegagalan menulis catatan tidak menggagalkan run.** Sejajar dengan
:mod:`app.logging_setup`, dan dengan pembenaran yang sama: catatan adalah artefak
turunan. Bab yang selesai tanpa catatan lebih berguna daripada bab yang gagal
karena direktori log kebetulan tidak dapat ditulis.

Berkas ini **tidak** memuat ``thinking``: alasan yang sama dengan
:class:`~domain.ports.ChatResult` — jejak penalaran bukan bagian dari keluaran,
dan menyalinnya ke catatan akan membuatnya terbaca sebagai keluaran oleh siapa
pun yang membuka berkas ini tanpa membaca kode yang menulisnya.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from domain.ports import ChatModel, ChatRequest, ChatResult, EmbeddingModel
from models.model_router import ChatModelSource

#: Versi skema satu baris catatan. Naik bila field-nya berubah bentuk, supaya
#: pembaca dapat menolak baris lama alih-alih menafsirkannya dengan salah.
RECORD_VERSION = 1

#: Nama berkas per peran (``writer.jsonl``), sesuai layout §38.
FILE_TEMPLATE = "{role}.jsonl"

#: Berapa karakter keluaran model yang disimpan apa adanya di setiap baris.
#:
#: Bukan ``0`` (tidak disimpan): yang dicari pembaca catatan justru *apa* yang
#: dijawab sebuah model, dan menyimpan angka saja akan membuat baris-baris itu
#: tidak dapat dibedakan satu sama lain kecuali oleh lama dan tokennya.
#: Bukan pula tanpa batas: satu draf bab utuh adalah puluhan ribu karakter, dan
#: delapan bab dengan tiga revisi akan menulis berkas yang lebih besar daripada
#: bukunya sendiri. Yang tetap utuh meski dipotong adalah ``output_chars`` dan
#: ``output_sha256``, sehingga keluaran yang sama tetap dapat dikenali.
DEFAULT_OUTPUT_CHARS = 4000

#: Penanda bahwa keluaran dipotong. Satu karakter, dan ia sendiri tidak akan
#: pernah muncul di akhir keluaran JSON mana pun secara kebetulan.
_ELLIPSIS = "…"

#: Kunci sementara untuk baris yang gagal di-parse. Sengaja bernama jelas dan
#: bukan nama field mana pun di skema, sehingga tidak mungkin tertukar dengan
#: catatan yang sah.
_UNREADABLE = "__unreadable__"


def _number(value: Any) -> int:
    """``value`` sebagai bilangan bulat, atau ``0`` bila bukan angka (MURNI).

    ``bool`` ditolak lebih dulu karena di Python ``True`` **adalah** ``int`` —
    tanpanya, satu field yang keliru berisi ``true`` akan menambah 1 ke token.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return int(value) if value > 0 else 0


def call_record(
    request: ChatRequest,
    result: ChatResult,
    *,
    at: str,
    role: str | None = None,
    model: str | None = None,
    output_chars: int = DEFAULT_OUTPUT_CHARS,
) -> dict[str, Any]:
    """Susun satu baris catatan dari satu panggilan (MURNI).

    Fungsi ini yang membuat seluruh bentuk catatan dapat diuji tanpa menyentuh
    disk sama sekali — dan bentuk catatan itulah kontrak yang dibaca ``compare``.

    :param at: cap waktu ISO-8601. Diberikan pemanggil, bukan diambil di sini,
        supaya hasilnya dapat direproduksi di tes.
    :param role: nama peran menurut **pemanggilnya**. Dipakai alih-alih
        ``request.role`` bila ada, karena yang memberi nama berkas adalah router
        dan permintaan yang tidak menyetel ``role`` akan menulis ke
        ``unknown.jsonl`` — catatan yang tidak salah, tetapi juga tidak berguna.
    :param model: nama model menurut **konfigurasi peran itu**, diberikan
        pemanggil yang mengenal registry. Dipakai sebagai pilihan terakhir, dan
        ia memang diperlukan: :class:`~domain.ports.ChatRequest` sengaja
        mengirim ``model=""`` — adapter-lah yang memakai model peran dari
        ``ModelSpec`` — sehingga tanpa cadangan ini satu-satunya sumber nama
        model adalah jawabannya sendiri, dan adapter yang tidak mengisi
        ``ChatResult.model`` menghasilkan catatan tanpa model sama sekali.
    """
    text = result.text or ""
    return {
        "version": RECORD_VERSION,
        "at": at,
        "role": role or request.role,
        # Yang menjawab > yang diminta > yang dikonfigurasi untuk peran itu.
        "model": result.model or request.model or (model or ""),
        "prompt_version": request.prompt_version,
        "sources": list(request.sources),
        "input_chars": len(request.system) + len(request.user),
        # "input ID" §38: sidik jari atas apa yang dikirim. Prompt yang sama
        # menghasilkan sidik jari yang sama, sehingga dua panggilan yang
        # berbeda hanya pada modelnya tetap dapat dikenali sebagai tugas yang
        # sama — dan itulah yang membuat perbandingan §33 adil.
        "input_sha256": digest(request.system + "\x00" + request.user),
        "images": len(request.images),
        "output": _cut(text, output_chars),
        "output_chars": len(text),
        "output_sha256": digest(text),
        "done_reason": result.done_reason,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "output_tokens": result.output_tokens,
    }


def digest(text: str) -> str:
    """Sidik jari pendek atas ``text`` (MURNI).

    Dipotong menjadi 16 heksadesimal: panjang ini cukup untuk membedakan
    ribuan panggilan tanpa membuat mata pembaca catatan melompatinya. Ia
    **bukan** alat keamanan — yang di-hash di sini adalah prompt yang kita
    sendiri baru saja susun, bukan apa pun yang perlu dilindungi dari tabrakan.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def append_call_log(directory: Path, role: str, record: Mapping[str, Any]) -> Path | None:
    """Tambahkan ``record`` sebagai satu baris JSONL ke ``<directory>/<role>.jsonl``.

    :returns: jalur berkasnya bila berhasil, ``None`` bila gagal ditulis.
        Pemanggil **tidak** boleh menggagalkan panggilan model karenanya.

    Mode *append* dengan ``newline="\\n"``, sama seperti
    :func:`~app.logging_setup.append_run_log`: JSONL ditentukan oleh pemisah
    baris, dan CRLF akan menempelkan ``\\r`` di ujung nilai terakhir setiap baris
    bagi pembaca yang memakai ``readline``.
    """
    line = json.dumps(record, ensure_ascii=False)
    path = directory / FILE_TEMPLATE.format(role=role)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        return None
    return path


@dataclass(frozen=True, slots=True)
class CallTotals:
    """Angka yang disimpulkan dari satu berkas catatan (§33).

    Inilah bentuk yang dibaca ``ai-book compare``: berapa kali model itu
    dipanggil, berapa token yang masuk dan keluar, dan berapa lama ia berpikir.
    """

    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    #: Baris yang tidak dapat dibaca. Selalu ``0`` pada berkas yang sehat, dan
    #: **dilaporkan alih-alih disembunyikan**: baris rusak berarti angkanya
    #: kurang, dan pembandingan model yang diam-diam kehilangan satu panggilan
    #: adalah pembandingan yang salah tanpa gejala apa pun.
    unreadable: int = 0

    @property
    def tokens(self) -> int:
        """Seluruh token yang dibayar — masuk maupun keluar."""
        return self.prompt_tokens + self.output_tokens

    @property
    def seconds(self) -> float:
        """Lama menunggu model, dalam detik."""
        return self.latency_ms / 1000.0


def read_call_log(directory: Path, role: str) -> tuple[dict[str, Any], ...]:
    """Baca seluruh baris catatan satu peran dari ``<directory>/<role>.jsonl``.

    Berkas yang belum ada bukan kesalahan: peran yang belum pernah dipanggil
    memang belum punya catatan, dan perbedaan "belum dipanggil" dari "gagal
    dipanggil" hanya terlihat dari jumlah panggilannya — nol.

    Baris yang tidak dapat dibaca dilewati, bukan menggagalkan pembacaan:
    berkas ini ditulis *append* selama proses berjalan, sehingga satu baris
    terpotong di ujungnya adalah keadaan yang wajar setelah jalankan yang
    dihentikan. Yang dilewati **dihitung** — lihat :attr:`CallTotals.unreadable`.
    """
    path = directory / FILE_TEMPLATE.format(role=role)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()

    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            records.append({_UNREADABLE: True})
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
        else:
            records.append({_UNREADABLE: True})
    return tuple(records)


def call_totals(records: Iterable[Mapping[str, Any]]) -> CallTotals:
    """Jumlahkan ``records`` menjadi :class:`CallTotals` (MURNI).

    Nilai yang hilang atau bukan angka dihitung nol, bukan dilempar: catatan
    dari versi skema lain harus tetap dapat dibandingkan sebanyak yang dapat
    dibaca, bukan menolak seluruh berkasnya.
    """
    calls = prompt_tokens = output_tokens = latency_ms = unreadable = 0
    for record in records:
        if record.get(_UNREADABLE):
            unreadable += 1
            continue
        calls += 1
        prompt_tokens += _number(record.get("prompt_tokens"))
        output_tokens += _number(record.get("output_tokens"))
        latency_ms += _number(record.get("latency_ms"))
    return CallTotals(
        calls=calls,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        unreadable=unreadable,
    )


def call_models(records: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Nama model yang benar-benar muncul di ``records``, urut kemunculan (MURNI).

    Dipakai ``compare`` untuk memeriksa bahwa yang tercatat memang model yang
    diminta. Perbandingan yang menamai model A sementara yang menjawab model B
    adalah perbandingan yang salah, dan diamnya itulah yang berbahaya.
    """
    seen: dict[str, None] = {}
    for record in records:
        model = record.get("model")
        if isinstance(model, str) and model.strip():
            seen.setdefault(model.strip(), None)
    return tuple(seen)


class CallLoggingChatModel:
    """:class:`~domain.ports.ChatModel` yang mencatat setiap panggilan (§38).

    Meneruskan permintaannya apa adanya dan mengembalikan hasilnya tanpa
    mengubahnya sepeser pun — yang ditambahkan hanyalah satu baris di disk.
    """

    def __init__(
        self,
        inner: ChatModel,
        directory: Path,
        *,
        role: str,
        now: Callable[[], str],
        model: str = "",
        output_chars: int = DEFAULT_OUTPUT_CHARS,
    ) -> None:
        self._inner = inner
        self._directory = directory
        self._role = role
        self._now = now
        self._model = model
        self._output_chars = output_chars

    def complete(self, request: ChatRequest) -> ChatResult:
        """Panggil modelnya, catat hasilnya, kembalikan hasilnya."""
        result = self._inner.complete(request)
        record = call_record(
            request,
            result,
            at=self._now(),
            role=self._role,
            model=self._model,
            output_chars=self._output_chars,
        )
        append_call_log(self._directory, self._role, record)
        return result

    @property
    def role(self) -> str:
        """Peran yang dicatat model ini — nama berkasnya."""
        return self._role


class CallLoggingModelSource:
    """:class:`~models.model_router.ChatModelSource` yang membungkus setiap model.

    Hanya ``chat_for`` yang dibungkus. Panggilan embedding tidak dicatat: §38
    berbicara tentang panggilan model bahasa — prompt, keluaran, dan penilaian —
    dan baris embedding akan mengisi berkas yang sama dengan jutaan angka yang
    tidak dibaca siapa pun. Yang lebih penting, ia akan membuat pembaca catatan
    harus menyaring terlebih dahulu untuk menemukan panggilan yang dimaksud.
    """

    def __init__(
        self,
        inner: ChatModelSource,
        directory: Path,
        *,
        now: Callable[[], str],
        model_for: Callable[[str], str] | None = None,
        output_chars: int = DEFAULT_OUTPUT_CHARS,
    ) -> None:
        self._inner = inner
        self._directory = directory
        self._now = now
        self._model_for = model_for
        self._output_chars = output_chars

    def chat_for(self, role: str) -> ChatModel:
        """Model peran itu, dibungkus pencatat."""
        return CallLoggingChatModel(
            self._inner.chat_for(role),
            self._directory,
            role=role,
            now=self._now,
            model="" if self._model_for is None else self._model_for(role),
            output_chars=self._output_chars,
        )

    def embedding_for(self, role: str) -> EmbeddingModel:
        """Diteruskan tanpa dibungkus — lihat catatan kelasnya."""
        return self._inner.embedding_for(role)


def _cut(text: str, limit: int) -> str:
    """Potong ``text`` menjadi paling banyak ``limit`` karakter (MURNI)."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + _ELLIPSIS


__all__ = [
    "DEFAULT_OUTPUT_CHARS",
    "FILE_TEMPLATE",
    "RECORD_VERSION",
    "CallLoggingChatModel",
    "CallLoggingModelSource",
    "CallTotals",
    "append_call_log",
    "call_models",
    "call_record",
    "call_totals",
    "digest",
    "read_call_log",
]
