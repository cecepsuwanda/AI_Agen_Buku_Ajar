"""Kebijakan atas hasil panggilan model — terpotong, dan kebocoran jejak penalaran.

`domain/completion.py` ada justru supaya berkas ini bisa ada. Sebelumnya ketiga
aturan di bawah hidup di dalam `models/ollama_client.py` — satu-satunya berkas
yang boleh meng-import `ollama`, dan karena itu satu-satunya berkas yang **tidak
boleh** diimpor tes mana pun: `test_architecture.py` menuntut `import ollama`
muncul di tepat satu berkas, sehingga tes yang mengimpornya menggagalkan gerbang
itu sendiri. Kebijakan yang tinggal di sana hanya dapat diuji dengan jaringan.
"""

from __future__ import annotations

import pytest

from domain.completion import interpret_completion
from domain.errors import TruncatedOutputError
from domain.ports import ChatResult


def _interpret(**overrides: object) -> ChatResult:
    """Panggil dengan nilai wajar, lalu timpa hanya yang sedang diuji."""
    args: dict[str, object] = {
        "model": "gemma3:4b",
        "content": "  {\"ok\": true}  ",
        "thinking": None,
        "done_reason": "stop",
        "max_tokens": 6144,
    }
    args.update(overrides)
    return interpret_completion(**args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Jalur sukses
# ---------------------------------------------------------------------------
def test_a_normal_completion_is_passed_through() -> None:
    result = _interpret()

    assert result.text == '{"ok": true}'


def test_surrounding_whitespace_is_trimmed() -> None:
    """Spasi di tepi bukan isi, dan menyisakannya membuat JSON gagal di-parse."""
    assert _interpret(content="\n\n  {\"a\": 1}\n\n").text == '{"a": 1}'


def test_token_counts_and_latency_survive() -> None:
    """Angka-angka ini yang membuat `run.jsonl` (§38) berguna."""
    result = _interpret(prompt_tokens=1200, output_tokens=340, latency_ms=8100)

    assert (result.prompt_tokens, result.output_tokens, result.latency_ms) == (1200, 340, 8100)


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------
def test_length_done_reason_is_truncation() -> None:
    """``done_reason == 'length'`` berarti anggaran token habis di tengah kalimat.

    Perbaikannya adalah menaikkan ``max_tokens``, **bukan** memperbaiki prompt —
    dan itu sebabnya ia error tersendiri, bukan "JSON rusak" biasa. Pesannya harus
    menyebut anggarannya, karena itulah satu-satunya angka yang perlu diubah.
    """
    with pytest.raises(TruncatedOutputError) as excinfo:
        _interpret(content='{"a": 1, "b": "belum seles', done_reason="length", max_tokens=2048)

    assert "2048" in str(excinfo.value)


def test_an_empty_content_with_a_reasoning_trace_is_truncation() -> None:
    """Perangkap model ``thinking``: seluruh anggaran habis di jejak penalaran.

    Tereproduksi di mesin ini (``gpt-oss:120b-cloud``, ``num_predict=5``):
    ``content`` keluar kosong sementara ``thinking`` terisi penuh. Memperlakukannya
    sebagai "model tidak menjawab" akan mengirimnya ke tangga perbaikan JSON, yang
    memperbaiki hal yang salah tiga kali berturut-turut.
    """
    with pytest.raises(TruncatedOutputError, match="penalaran"):
        _interpret(
            content="",
            thinking="Baik, saya perlu menulis bab tentang...",
            done_reason="stop",
        )


def test_whitespace_only_content_also_counts_as_empty() -> None:
    """Isi yang hanya berisi baris kosong sama saja dengan tidak ada isi."""
    with pytest.raises(TruncatedOutputError):
        _interpret(content="   \n\n  ", thinking="jejak penalaran")


def test_an_empty_answer_without_a_trace_is_not_truncation() -> None:
    """Kosong **tanpa** jejak penalaran bukan urusan modul ini.

    Model mengembalikan string kosong murni; itu JSON yang tidak sah, dan
    tangga perbaikan di ``StructuredAgent`` memang tempatnya. Membedakan kedua
    kondisi ini penting: menaikkan anggaran token tidak akan menolong di sini.
    """
    result = _interpret(content="", thinking=None, done_reason="stop")

    assert result.text == ""


def test_truncation_is_checked_before_the_reasoning_heuristic() -> None:
    """``length`` menang: sebabnya paling spesifik, dan tak perlu disimpulkan."""
    with pytest.raises(TruncatedOutputError) as excinfo:
        _interpret(content="", thinking="jejak", done_reason="length")

    assert "penalaran" not in str(excinfo.value), "sebab yang paling spesifik harus dilaporkan"


# ---------------------------------------------------------------------------
# Jejak penalaran tidak pernah bocor ke draf
# ---------------------------------------------------------------------------
def test_the_reasoning_trace_is_never_concatenated_into_the_text() -> None:
    """Inilah janji yang paling mahal bila dilanggar, dan paling sunyi.

    Jika jejak penalaran ikut tersambung, JSON-nya tetap di-parse, babnya tetap
    tersimpan, dan tidak ada satu galat pun — yang rusak hanya kualitasnya, dan
    itu baru terlihat setelah dibaca manusia.
    """
    result = _interpret(
        content='{"title": "Bab"}',
        thinking="Saya harus menulis bab tentang struktur data. Pertama, ...",
    )

    assert result.text == '{"title": "Bab"}'
    assert "Pertama" not in result.text
    assert result.thinking is not None and "Pertama" in result.thinking
