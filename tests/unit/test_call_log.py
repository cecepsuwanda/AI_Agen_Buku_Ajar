"""Catatan per panggilan model (§38) — bentuknya, dan siapa yang menulisnya.

§38 meminta setiap panggilan dicatat, dan menyebutkan alasannya sendiri: *"Hal ini
penting untuk membandingkan model."* Berkas ini menjaga tiga hal yang membuat
catatan itu benar-benar dapat dipercaya untuk pembandingan:

* **Yang dicatat adalah fakta, bukan tebakan.** ``sources`` datang dari
  permintaan yang benar-benar dikirim, bukan disimpulkan dari teks prompt. Dua
  model yang diberi tugas sama menghasilkan ``input_sha256`` yang sama — dan
  itulah yang membuat perbandingan §33 adil.
* **Pencatatnya tidak mengubah apa pun.** Model yang dibungkus mengembalikan
  hasil yang identik dengan model yang tidak dibungkus; yang berbeda hanya satu
  baris di disk.
* **Kegagalan menulis catatan tidak menggagalkan panggilan.** Catatan adalah
  artefak turunan; bab yang selesai tanpa catatan lebih berguna daripada bab yang
  gagal karena direktori log tidak dapat ditulis.

Seluruh berkas ini bekerja di ``tmp_path``; tidak ada satu pun panggilan model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.ports import ChatRequest, ChatResult
from models.call_log import (
    DEFAULT_OUTPUT_CHARS,
    RECORD_VERSION,
    CallLoggingChatModel,
    CallLoggingModelSource,
    append_call_log,
    call_models,
    call_record,
    call_totals,
    digest,
    read_call_log,
)

#: Cap waktu tetap: keluarannya tidak bergantung pada jam dinding.
AT = "2026-01-01T00:00:00Z"


def _request(**overrides: object) -> ChatRequest:
    """Permintaan yang sudah terisi, dengan bagian yang relevan saja."""
    base: dict[str, object] = {
        "model": "gemma4:31b-cloud",
        "system": "Kamu penulis bab.",
        "user": "Tulis bab 1.",
        "role": "writer",
        "prompt_version": "writer.chapter.md+abc123",
        "sources": ("buku.pdf — Bab 2 — hlm. 17",),
    }
    base.update(overrides)
    return ChatRequest(**base)  # type: ignore[arg-type]


def _result(**overrides: object) -> ChatResult:
    """Hasil yang sudah terisi."""
    base: dict[str, object] = {
        "text": "Bab satu tentang sesuatu.",
        "model": "gemma4:31b-cloud",
        "done_reason": "stop",
        "prompt_tokens": 900,
        "output_tokens": 120,
        "latency_ms": 4200,
    }
    base.update(overrides)
    return ChatResult(**base)  # type: ignore[arg-type]


class _EchoModel:
    """``ChatModel`` paling sederhana: mengembalikan hasil yang sudah disiapkan."""

    def __init__(self, result: ChatResult) -> None:
        self._result = result
        #: Permintaan yang benar-benar sampai — dibaca tes untuk membuktikan
        #: bahwa pembungkusan tidak mengubah apa pun.
        self.seen: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResult:
        """Catat permintaannya, kembalikan hasilnya."""
        self.seen.append(request)
        return self._result


class _BlockedDirectory:
    """Direktori yang **tidak mungkin** ditulis: sebuah berkas berdiri di jalurnya.

    Lebih jujur daripada memalsukan ``Path.open``: yang diuji adalah tanggapan
    :func:`~models.call_log.append_call_log` atas ``OSError`` yang sungguhan
    datang dari sistem berkas.
    """

    def __init__(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("bukan direktori", encoding="utf-8")
        self.path = blocker / "logs"


# ---------------------------------------------------------------------------
# Bentuk satu baris catatan (MURNI)
# ---------------------------------------------------------------------------
def test_the_record_carries_every_field_section_38_asks_for() -> None:
    """Peran, model, versi prompt, bahan, keluaran, lama, dan token — semuanya ada."""
    record = call_record(_request(), _result(), at=AT)

    assert record["version"] == RECORD_VERSION
    assert record["at"] == AT
    assert record["role"] == "writer"
    assert record["model"] == "gemma4:31b-cloud"
    assert record["prompt_version"] == "writer.chapter.md+abc123"
    assert record["sources"] == ["buku.pdf — Bab 2 — hlm. 17"]
    assert record["output"] == "Bab satu tentang sesuatu."
    assert record["done_reason"] == "stop"
    assert record["latency_ms"] == 4200
    assert record["prompt_tokens"] == 900
    assert record["output_tokens"] == 120


def test_the_record_is_json_serializable_as_it_stands() -> None:
    """Tidak ada ``tuple`` atau objek yang terselip: ``json.dumps`` menerimanya apa adanya.

    Catatan yang baru dapat ditulis setelah dikonversi di tempat lain adalah
    catatan yang cepat atau lambat ditulis berbeda oleh dua penulis berbeda.
    """
    record = call_record(_request(), _result(), at=AT)
    assert json.loads(json.dumps(record))["role"] == "writer"


def test_the_model_is_taken_from_the_result_when_it_answers_with_one() -> None:
    """Model yang benar-benar menjawab menang atas yang diminta.

    Ollama dapat mengarahkan permintaan ke varian lain; yang dicatat harus yang
    menjawab, sebab itulah yang sedang dibandingkan.
    """
    record = call_record(_request(), _result(model="gemma3:4b"), at=AT)
    assert record["model"] == "gemma3:4b"


def test_the_requested_model_is_recorded_when_the_result_names_none() -> None:
    """Adapter yang tidak mengisi ``model`` tetap menghasilkan catatan yang berguna."""
    record = call_record(_request(), _result(model=""), at=AT)
    assert record["model"] == "gemma4:31b-cloud"


def test_the_configured_model_is_recorded_when_nothing_else_names_one() -> None:
    """``ChatRequest.model`` sengaja kosong dan adapter boleh tidak mengisi jawabannya.

    Tanpa cadangan dari konfigurasi, kedua keadaan itu bersama-sama menghasilkan
    catatan tanpa model — catatan yang tidak dapat dipakai membandingkan apa pun.
    """
    record = call_record(
        _request(model=""), _result(model=""), at=AT, model="gemma4:31b-cloud"
    )
    assert record["model"] == "gemma4:31b-cloud"


def test_the_configured_model_does_not_overrule_an_answer() -> None:
    """Model yang benar-benar menjawab tetap menang — konfigurasi hanya cadangan."""
    record = call_record(_request(), _result(model="gemma3:4b"), at=AT, model="gpt-oss:120b-cloud")
    assert record["model"] == "gemma3:4b"


def test_the_caller_names_the_role_and_overrides_the_request() -> None:
    """Router yang memberi nama peran — bukan permintaan yang mungkin tidak menyetelnya.

    Tanpa ini, permintaan tanpa ``role`` akan menulis ke ``unknown.jsonl``:
    catatan yang tidak salah, tetapi juga tidak berguna bagi siapa pun yang
    ingin membandingkan satu peran lintas model.
    """
    record = call_record(_request(role="unknown"), _result(), at=AT, role="writer")
    assert record["role"] == "writer"


def test_the_fingerprint_is_the_same_for_the_same_task() -> None:
    """Dua model, satu tugas → satu ``input_sha256``. Itulah yang membuat §33 adil."""
    a = call_record(_request(model="gemma4:31b-cloud"), _result(), at=AT)
    b = call_record(_request(model="gpt-oss:120b-cloud"), _result(), at=AT)
    assert a["input_sha256"] == b["input_sha256"]


def test_a_different_prompt_forks_the_fingerprint() -> None:
    """Prompt yang berbeda harus dapat dibedakan — kalau tidak, sidik jarinya sia-sia."""
    a = call_record(_request(), _result(), at=AT)
    b = call_record(_request(user="Tulis bab 2."), _result(), at=AT)
    assert a["input_sha256"] != b["input_sha256"]


def test_the_input_length_counts_system_and_user_together() -> None:
    """Satu angka yang menjawab "seberapa besar yang dikirim", tanpa membuka prompt."""
    request = _request(system="abc", user="de")
    record = call_record(request, _result(), at=AT)
    assert record["input_chars"] == 5


def test_the_images_are_counted_not_copied() -> None:
    """Halaman hasil scan dicatat sebagai jumlahnya — memuatnya akan membengkakkan berkas."""
    request = _request(images=(b"\x89PNG satu", b"\x89PNG dua"))
    record = call_record(request, _result(), at=AT)
    assert record["images"] == 2
    assert "images_bytes" not in record


def test_retrieval_that_did_not_happen_is_recorded_as_empty() -> None:
    """Tanpa retrieval, daftarnya kosong — bukan diisi nama berkas yang tidak diambil."""
    record = call_record(_request(sources=()), _result(), at=AT)
    assert record["sources"] == []


def test_an_empty_answer_is_still_a_recorded_answer() -> None:
    """Model ``thinking`` yang menghabiskan anggarannya mengembalikan teks kosong.

    Catatan harus dapat menyimpan keadaan itu **apa adanya**, sebab justru
    keadaan itulah yang perlu ditemukan saat membandingkan model.
    """
    record = call_record(_request(), _result(text=""), at=AT)
    assert record["output"] == ""
    assert record["output_chars"] == 0
    assert record["output_sha256"] == digest("")


def test_a_none_answer_does_not_break_the_record() -> None:
    """``text=None`` dari adapter yang kurang rapi tidak boleh menggagalkan pencatatan."""
    record = call_record(_request(), _result(text=None), at=AT)
    assert record["output"] == ""
    assert record["output_chars"] == 0


def test_long_output_is_cut_and_says_so() -> None:
    """Draf bab utuh dipotong, tetapi panjang dan sidik jarinya tetap utuh.

    Yang penting bukan seluruh teksnya, melainkan bahwa keluaran yang sama tetap
    dapat dikenali walau dipotong.
    """
    text = "x" * (DEFAULT_OUTPUT_CHARS + 500)
    record = call_record(_request(), _result(text=text), at=AT)

    assert record["output_chars"] == len(text)
    assert record["output_sha256"] == digest(text)
    assert len(record["output"]) == DEFAULT_OUTPUT_CHARS + 1
    assert record["output"].endswith("…")


def test_output_shorter_than_the_limit_is_untouched() -> None:
    """Tanpa pemotongan, tidak ada penanda yang muncul."""
    record = call_record(_request(), _result(text="pendek"), at=AT)
    assert record["output"] == "pendek"


def test_a_limit_of_zero_keeps_the_numbers_and_drops_the_text() -> None:
    """``output_chars: 0`` adalah pilihan yang sah, bukan kesalahan."""
    record = call_record(_request(), _result(text="apa pun"), at=AT, output_chars=0)
    assert record["output"] == ""
    assert record["output_chars"] == len("apa pun")


def test_a_negative_limit_behaves_like_zero() -> None:
    """Nilai negatif tidak boleh memotong dari belakang dan menghasilkan teks aneh."""
    record = call_record(_request(), _result(text="apa pun"), at=AT, output_chars=-1)
    assert record["output"] == ""


def test_the_fingerprint_is_short_and_hexadecimal() -> None:
    """16 heksadesimal: cukup untuk membedakan ribuan panggilan, cukup pendek untuk dibaca."""
    value = digest("apa saja")
    assert len(value) == 16
    assert all(char in "0123456789abcdef" for char in value)


def test_the_fingerprint_is_deterministic_and_sensitive() -> None:
    """Sama masukannya, sama keluarannya — dan satu huruf berbeda mengubahnya."""
    assert digest("abc") == digest("abc")
    assert digest("abc") != digest("abd")


# ---------------------------------------------------------------------------
# Menulis ke JSONL
# ---------------------------------------------------------------------------
def test_appending_creates_the_directory_and_the_file(tmp_path: Path) -> None:
    """Direktori log dibuat saat dibutuhkan — bukan diandaikan sudah ada."""
    record = call_record(_request(), _result(), at=AT)
    path = append_call_log(tmp_path / "logs", "writer", record)

    assert path == tmp_path / "logs" / "writer.jsonl"
    assert path is not None and path.is_file()


def test_appending_produces_one_line_per_call(tmp_path: Path) -> None:
    """JSONL ditentukan oleh satu baris per panggilan; menumpuknya adalah intinya."""
    directory = tmp_path / "logs"
    append_call_log(directory, "writer", call_record(_request(), _result(), at=AT))
    append_call_log(
        directory, "writer", call_record(_request(user="Tulis bab 2."), _result(), at=AT)
    )

    lines = (directory / "writer.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["role"] == second["role"] == "writer"
    assert first["input_sha256"] != second["input_sha256"]


def test_the_lines_are_separated_by_lf_only(tmp_path: Path) -> None:
    """CRLF akan menempelkan ``\\r`` di ujung nilai terakhir bagi pembaca ``readline``."""
    directory = tmp_path / "logs"
    append_call_log(directory, "writer", call_record(_request(), _result(), at=AT))

    raw = (directory / "writer.jsonl").read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw


def test_a_write_that_fails_returns_none_instead_of_raising(tmp_path: Path) -> None:
    """Pemanggilnya tidak boleh menggagalkan panggilan model karena catatan gagal ditulis."""
    blocked = _BlockedDirectory(tmp_path)
    result = append_call_log(blocked.path, "writer", call_record(_request(), _result(), at=AT))
    assert result is None


# ---------------------------------------------------------------------------
# Pembungkus: satu peran, satu berkas
# ---------------------------------------------------------------------------
def test_the_wrapped_model_returns_exactly_what_it_received(tmp_path: Path) -> None:
    """Pencatat tidak mengubah hasilnya sepeser pun."""
    inner = _EchoModel(_result())
    model = CallLoggingChatModel(
        inner, tmp_path / "logs", role="writer", now=lambda: AT
    )

    assert model.complete(_request()) == inner.complete(_request())


def test_the_wrapped_model_passes_the_request_through_unchanged(tmp_path: Path) -> None:
    """Permintaan yang sampai ke model asli adalah permintaan yang sama, utuh."""
    inner = _EchoModel(_result())
    model = CallLoggingChatModel(
        inner, tmp_path / "logs", role="writer", now=lambda: AT
    )
    request = _request()

    model.complete(request)

    assert inner.seen == [request]


def test_the_wrapped_model_writes_one_line_per_call(tmp_path: Path) -> None:
    """Dua panggilan, dua baris — dan perannya ikut tercatat."""
    directory = tmp_path / "logs"
    model = CallLoggingChatModel(
        _EchoModel(_result()), directory, role="writer", now=lambda: AT
    )

    model.complete(_request())
    model.complete(_request())

    lines = (directory / "writer.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["role"] for line in lines] == ["writer", "writer"]


def test_the_wrapped_model_reports_the_role_it_records_under(tmp_path: Path) -> None:
    """Peran itu nama berkasnya, jadi ia harus dapat dibaca dari luar."""
    model = CallLoggingChatModel(
        _EchoModel(_result()), tmp_path / "logs", role="latex", now=lambda: AT
    )
    assert model.role == "latex"


def test_the_wrapped_model_survives_a_log_directory_it_cannot_write(tmp_path: Path) -> None:
    """Bab yang selesai tanpa catatan lebih berguna daripada bab yang gagal karenanya."""
    blocked = _BlockedDirectory(tmp_path)
    result = _result()
    model = CallLoggingChatModel(
        _EchoModel(result), blocked.path, role="writer", now=lambda: AT
    )

    assert model.complete(_request()) == result


def test_the_source_wraps_each_role_under_its_own_file(tmp_path: Path) -> None:
    """Satu berkas per peran, sesuai layout §38 — pertanyaan "model mana untuk penulis"
    harus selesai dengan satu pembacaan berkas."""
    directory = tmp_path / "logs"
    source = _StaticSource({"writer": _result(), "reviewer": _result(text="setuju")})
    wrapped = CallLoggingModelSource(source, directory, now=lambda: AT)

    wrapped.chat_for("writer").complete(_request(role="writer"))
    wrapped.chat_for("reviewer").complete(_request(role="reviewer"))

    assert (directory / "writer.jsonl").is_file()
    assert (directory / "reviewer.jsonl").is_file()
    assert json.loads((directory / "reviewer.jsonl").read_text(encoding="utf-8"))["output"] == (
        "setuju"
    )


def test_embeddings_are_forwarded_without_being_wrapped(tmp_path: Path) -> None:
    """§38 berbicara tentang panggilan model bahasa; vektor embedding tidak dicatat."""
    source = _StaticSource({"writer": _result()})
    wrapped = CallLoggingModelSource(source, tmp_path / "logs", now=lambda: AT)

    embedder = wrapped.embedding_for("embedding")

    assert embedder is source.embedding
    assert not (tmp_path / "logs").exists()


def test_the_source_asks_the_registry_for_each_role_model(tmp_path: Path) -> None:
    """Nama model diambil per peran, dari konfigurasi — bukan satu nama untuk semua."""
    directory = tmp_path / "logs"
    asked: list[str] = []

    def model_for(role: str) -> str:
        asked.append(role)
        return f"model-{role}"

    source = _StaticSource({"writer": _result(text="")})
    wrapped = CallLoggingModelSource(
        source, directory, now=lambda: AT, model_for=model_for
    )

    wrapped.chat_for("writer")
    wrapped.chat_for("latex")

    assert asked == ["writer", "latex"]


def test_the_source_records_the_registry_model_when_the_answer_names_none(tmp_path: Path) -> None:
    """Ujung-ujungnya: peran ``writer`` tetap tercatat dengan model yang benar."""
    directory = tmp_path / "logs"
    wrapped = CallLoggingModelSource(
        _StaticSource({"writer": _result(model="")}),
        directory,
        now=lambda: AT,
        model_for=lambda role: "gemma4:31b-cloud",
    )

    wrapped.chat_for("writer").complete(_request(role="writer", model=""))

    record = json.loads((directory / "writer.jsonl").read_text(encoding="utf-8"))
    assert record["model"] == "gemma4:31b-cloud"


def test_the_source_forwards_the_role_to_the_inner_source(tmp_path: Path) -> None:
    """Pembungkus meminta model dari sumber aslinya — ia tidak memilih model sendiri."""
    source = _StaticSource({"writer": _result()})
    wrapped = CallLoggingModelSource(source, tmp_path / "logs", now=lambda: AT)

    wrapped.chat_for("writer")
    wrapped.chat_for("latex")

    assert source.asked == ["writer", "latex"]


# ---------------------------------------------------------------------------
# Membaca kembali: yang dipakai `compare` (§33)
# ---------------------------------------------------------------------------
def test_a_log_that_was_never_written_reads_as_nothing(tmp_path: Path) -> None:
    """Peran yang belum pernah dipanggil belum punya catatan — dan itu bukan galat."""
    assert read_call_log(tmp_path / "logs", "writer") == ()


def test_what_was_written_is_what_is_read(tmp_path: Path) -> None:
    """Menulis lalu membaca kembali menghasilkan baris yang sama."""
    directory = tmp_path / "logs"
    append_call_log(directory, "writer", call_record(_request(), _result(), at=AT))

    records = read_call_log(directory, "writer")

    assert len(records) == 1
    assert records[0]["role"] == "writer"


def test_the_totals_add_up_the_whole_file(tmp_path: Path) -> None:
    """Angka yang dibandingkan §33: panggilan, token masuk, token keluar, dan lama."""
    directory = tmp_path / "logs"
    for _ in range(3):
        append_call_log(directory, "writer", call_record(_request(), _result(), at=AT))

    totals = call_totals(read_call_log(directory, "writer"))

    assert totals.calls == 3
    assert totals.prompt_tokens == 2700
    assert totals.output_tokens == 360
    assert totals.latency_ms == 12600
    assert totals.tokens == 3060
    assert totals.seconds == pytest.approx(12.6)


def test_a_truncated_last_line_is_counted_not_swallowed(tmp_path: Path) -> None:
    """Jalankan yang dihentikan meninggalkan baris terpotong; angkanya harus mengaku.

    Yang berbahaya bukan barisnya, melainkan diamnya: pembandingan model yang
    kehilangan satu panggilan tanpa satu pun tanda adalah pembandingan yang
    salah tanpa gejala.
    """
    directory = tmp_path / "logs"
    append_call_log(directory, "writer", call_record(_request(), _result(), at=AT))
    with (directory / "writer.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"version": 1, "role": "wri')

    totals = call_totals(read_call_log(directory, "writer"))

    assert totals.calls == 1
    assert totals.unreadable == 1


def test_a_blank_line_is_not_a_broken_record(tmp_path: Path) -> None:
    """Baris kosong di ujung berkas adalah hal biasa, bukan catatan yang rusak."""
    directory = tmp_path / "logs"
    append_call_log(directory, "writer", call_record(_request(), _result(), at=AT))
    with (directory / "writer.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")

    totals = call_totals(read_call_log(directory, "writer"))

    assert totals.calls == 1
    assert totals.unreadable == 0


def test_a_record_that_is_not_an_object_counts_as_unreadable(tmp_path: Path) -> None:
    """Berkas yang tertimpa isi lain tidak boleh menambah token yang tidak ada."""
    directory = tmp_path / "logs"
    directory.mkdir(parents=True)
    (directory / "writer.jsonl").write_text("42\n", encoding="utf-8")

    totals = call_totals(read_call_log(directory, "writer"))

    assert totals.calls == 0
    assert totals.unreadable == 1


def test_missing_or_nonsense_numbers_count_as_zero() -> None:
    """Catatan dari versi skema lain dibandingkan sebanyak yang dapat dibaca."""
    totals = call_totals(
        [
            {"prompt_tokens": 10, "output_tokens": 2, "latency_ms": 500},
            {"prompt_tokens": True, "output_tokens": -5, "latency_ms": "lama"},
            {"prompt_tokens": None},
            {},
        ]
    )

    assert totals.calls == 4
    assert totals.prompt_tokens == 10
    assert totals.output_tokens == 2
    assert totals.latency_ms == 500


def test_the_models_that_appear_are_reported_in_order() -> None:
    """Yang menjawab harus dapat diperiksa terhadap yang diminta (§33)."""
    records = [
        {"model": "gemma3:4b"},
        {"model": "gemma4:31b-cloud"},
        {"model": "gemma3:4b"},
    ]
    assert call_models(records) == ("gemma3:4b", "gemma4:31b-cloud")


def test_a_record_without_a_model_contributes_no_name() -> None:
    """Nama kosong bukan nama model — ia tidak boleh muncul di daftar."""
    assert call_models([{"model": ""}, {"model": None}, {}]) == ()


class _StaticSource:
    """``ChatModelSource`` minimal: satu hasil per peran, satu embedder bersama."""

    def __init__(self, results: dict[str, ChatResult]) -> None:
        self._results = results
        self.embedding = _EchoEmbedder()
        #: Peran yang benar-benar diminta dari sumber ini.
        self.asked: list[str] = []

    def chat_for(self, role: str) -> _EchoModel:
        """Model peran itu — hasilnya sudah disiapkan."""
        self.asked.append(role)
        return _EchoModel(self._results.get(role, _result()))

    def embedding_for(self, role: str) -> "_EchoEmbedder":
        """Embedder yang tidak mencatat apa pun."""
        del role
        return self.embedding


class _EchoEmbedder:
    """``EmbeddingModel`` yang mengembalikan satu vektor tetap."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Satu vektor per teks, isinya tidak penting di sini."""
        return [[0.0] for _ in texts]
