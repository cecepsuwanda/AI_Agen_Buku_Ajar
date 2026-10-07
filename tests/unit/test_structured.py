"""Keluaran terstruktur — skema ketat, penyelamatan JSON, dan validasi.

Seluruh berkas ini lulus **tanpa satu pun panggilan model**, dan itu memang
tujuannya: `domain/structured.py` adalah tempat kegagalan LLM yang paling sering
terjadi disaring, dan setiap aturannya dapat dibuktikan di sini.

Dua di antaranya lahir dari keluaran model yang sungguh gagal di mesin ini dan
disimpan di `state/parse_fail/`; keduanya ditandai di bawah.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field, ValidationError

from domain.chapter import ChapterDraft, Section
from domain.structured import (
    example_instance,
    extract_json_object,
    is_json_parseable,
    parse_output,
    render_output_contract,
    strict_schema,
)


class Inner(BaseModel):
    label: str
    count: int = 3
    note: str | None = None


class Outer(BaseModel):
    name: str
    inner: Inner
    tags: list[str]
    ratio: float = 0.5


# ---------------------------------------------------------------------------
# 1. strict_schema — mempersempit ruang gerak grammar
# ---------------------------------------------------------------------------
def test_every_property_is_required_and_no_object_is_open() -> None:
    """Semua properti wajib, semua objek tertutup — termasuk yang bersarang.

    Inilah yang membuat grammar GBNF cukup ketat. Membiarkan satu properti
    opsional menghasilkan objek yang **sah secara grammar tetapi salah secara
    makna**, dan kegagalan seperti itu baru terlihat sebagai bab yang buruk.
    """
    schema = strict_schema(Outer)

    assert schema["additionalProperties"] is False
    assert schema["required"] == sorted(["name", "inner", "tags", "ratio"])

    nested = schema["properties"]["inner"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == sorted(["label", "count", "note"])


def test_refs_are_inlined_so_the_schema_stands_alone() -> None:
    """``$ref``/``$defs`` hilang: skema tidak bergantung pada penelusuran server."""
    schema = strict_schema(Outer)
    rendered = json.dumps(schema)

    assert "$defs" not in schema
    assert "$ref" not in rendered, "masih ada $ref yang belum di-inline"


def test_defaults_are_removed() -> None:
    """``default`` dibuang — sebagian server memperlakukannya sebagai opsional."""
    schema = strict_schema(Outer)

    assert "default" not in schema["properties"]["ratio"]
    assert "default" not in schema["properties"]["inner"]["properties"]["count"]


def test_the_schema_is_plain_json() -> None:
    """Hasilnya ``dict`` mentah yang bisa diserialisasi — bukan kelas Pydantic.

    SDK 0.6.2 menyatakan ``format`` bertipe ``Literal['', 'json'] | dict``, jadi
    mengirim kelas Pydantic berarti bersandar pada dukungan yang tidak dijanjikan.
    """
    schema = strict_schema(Outer)

    assert type(schema) is dict
    assert json.loads(json.dumps(schema)) == schema


# ---------------------------------------------------------------------------
# 2. extract_json_object — menyelamatkan JSON dari teks berantakan
# ---------------------------------------------------------------------------
def test_plain_object_passes_through() -> None:
    assert extract_json_object('{"a": 1}') == '{"a": 1}'


def test_markdown_fence_is_unwrapped() -> None:
    """Pagar yang **membungkus** objek dilepas, label bahasanya ikut dibuang."""
    raw = '```json\n{"a": 1}\n```'
    assert extract_json_object(raw) == '{"a": 1}'


def test_prose_preamble_is_discarded() -> None:
    raw = 'Tentu, berikut JSON-nya:\n\n{"a": 1}\n\nSemoga membantu.'
    assert extract_json_object(raw) == '{"a": 1}'


def test_bom_and_zero_width_characters_are_stripped() -> None:
    """Karakter tak terlihat dari model membuat ``json.loads`` gagal tanpa alasan jelas."""
    raw = '﻿​{"a": 1}‍'
    assert extract_json_object(raw) == '{"a": 1}'


def test_trailing_comma_is_dropped() -> None:
    assert extract_json_object('{"a": 1,}') == '{"a": 1}'
    assert extract_json_object('{"a": [1, 2,],}') == '{"a": [1, 2]}'


def test_a_closing_brace_inside_a_string_does_not_end_the_object() -> None:
    """Penghitungan kurung menghormati string literal — inilah alasan bukan regex."""
    raw = '{"a": "tutup } di dalam string", "b": 2}'

    assert extract_json_object(raw) == raw
    assert json.loads(raw)["b"] == 2


def test_an_escaped_quote_inside_a_string_does_not_close_it() -> None:
    raw = '{"a": "kata \\"lalu\\" } masih di dalam", "b": 2}'

    assert extract_json_object(raw) == raw


def test_a_fenced_block_inside_a_string_is_left_alone() -> None:
    """Pagar **di dalam** JSON bukan pembungkus, dan memotongnya di situ salah.

    Terjadi sungguhan pada ``state/parse_fail/chapter01.attempt3.txt`` (2026-10-07):
    penulis menyisipkan contoh kode berpagar ke dalam field ``body``. Pagar itu
    berada **setelah** ``{`` pertama, jadi ia bagian dari isi — bukan pembungkus.

    Akibatnya terukur pada berkas nyata itu: keluaran 10.449 karakter yang
    **valid** menyusut menjadi ``{target_value}``. Babnya sudah benar sejak awal;
    yang merusaknya adalah ekstraktornya.
    """
    payload_in = {"heading": "Contoh", "body": "teks"}
    raw = json.dumps(
        {
            "title": "Pencarian Linear",
            "body": f"Contoh kode:\n\n```python\nhasil = {json.dumps(payload_in)}\n```\n\nSelesai.",
        }
    )

    extracted = extract_json_object(raw)

    assert extracted is not None
    outer = json.loads(extracted)
    assert outer["title"] == "Pencarian Linear"
    assert "```" in outer["body"], "blok berpagar di dalam string ikut terpotong"


def test_the_inner_object_is_never_silently_preferred_over_the_outer_one() -> None:
    """Bentuk paling berbahayanya: potongan di antara pagar **sendiri** objek yang sah.

    Kalau keluaran model bukan JSON yang sah — kutip di dalam string tidak
    di-escape, dan itu kerap terjadi — isi blok berpagar justru menjadi objek JSON
    yang seimbang. Versi lama mengembalikannya tanpa satu pun galat, dan
    ``parse_output`` akan memvalidasinya menjadi ``ChapterDraft`` yang salah isi.

    Perilaku yang benar adalah **tidak menebak**: teksnya dikembalikan utuh, dan
    validasi yang memutuskan. Tes ini karena itu menuntut galat yang menyebut
    skemanya, bukan diam-diam menerima objek yang lebih dalam.
    """
    raw = '{"title": "Asli", "body": "```\n{"heading": "Potongan"}\n```"}'

    extracted = extract_json_object(raw)
    assert extracted is not None and "Potongan" in extracted and "Asli" in extracted, (
        "objek terluar ikut hilang — versi lama mengembalikan {'heading': 'Potongan'}"
    )
    with pytest.raises(ValidationError):
        parse_output(raw, ChapterDraft)


def test_the_wrapper_is_still_stripped_when_it_really_wraps() -> None:
    """Sisi lain dari aturan di atas: pagar **sebelum** objek tetap dilepas."""
    raw = 'Baik:\n\n```json\n{"a": {"b": 1}}\n```\n'

    assert extract_json_object(raw) == '{"a": {"b": 1}}'


def test_truncated_output_yields_none() -> None:
    """Keluaran terpotong tidak punya penutup yang seimbang — dan itu harus terlihat.

    Mengembalikan potongan yang belum seimbang akan menghasilkan pesan galat yang
    menyesatkan ("JSON rusak") padahal sebabnya anggaran token habis.
    """
    assert extract_json_object('{"a": 1, "b": "belum seles') is None


def test_text_without_any_object_yields_none() -> None:
    assert extract_json_object("Tidak ada JSON sama sekali di sini.") is None


def test_is_json_parseable_agrees_with_the_extractor() -> None:
    assert is_json_parseable('{"a": 1}') is True
    assert is_json_parseable("```json\n{\"a\": 1}\n```") is True
    assert is_json_parseable('{"a": ') is False
    assert is_json_parseable("tanpa json") is False


# ---------------------------------------------------------------------------
# 3. parse_output — gerbang yang memutuskan berhasil atau tidak
# ---------------------------------------------------------------------------
def test_parse_output_returns_the_domain_type() -> None:
    raw = '```json\n{"title": "Bab Uji", "sections": [{"heading": "A", "body": "isi"}]}\n```'

    draft = parse_output(raw, ChapterDraft)

    assert isinstance(draft, ChapterDraft)
    assert draft.title == "Bab Uji"
    assert draft.sections[0].heading == "A"


def test_parse_output_reports_truncation_as_value_error() -> None:
    """Tanpa objek yang seimbang, galatnya harus menyebut kemungkinan terpotong."""
    with pytest.raises(ValueError, match="terpotong"):
        parse_output('{"title": "Bab', ChapterDraft)


def test_parse_output_rejects_a_schema_mismatch() -> None:
    """JSON sah tetapi tidak sesuai skema tetap ditolak — di sini field wajib hilang."""
    with pytest.raises(ValidationError, match="title"):
        parse_output('{"sections": []}', ChapterDraft)


def test_extra_fields_are_rejected_not_silently_dropped() -> None:
    """``extra='forbid'`` pada model domain: field halusinasi gagal keras."""
    with pytest.raises(ValidationError):
        parse_output('{"title": "Bab", "panjang": 1200}', ChapterDraft)


# ---------------------------------------------------------------------------
# 4. example_instance & render_output_contract (§36)
# ---------------------------------------------------------------------------
def test_example_instance_satisfies_its_own_schema() -> None:
    """Fungsi kembar ``strict_schema`` harus menghasilkan nilai yang **lolos validasi**.

    Kalau tidak, contoh di prompt akan ditolak oleh skema yang dikirim bersama
    prompt itu sendiri.
    """
    schema = strict_schema(ChapterDraft)
    example = example_instance(schema)

    parsed = ChapterDraft.model_validate(example)

    assert parsed.title, "string contoh tidak boleh kosong (min_length=1 menolaknya)"
    assert len(parsed.sections) == 1, "array kosong sering ditolak min_length"
    assert parsed.sections[0].heading


def test_example_instance_honours_overrides_at_any_depth() -> None:
    """Override mengarahkan skenario — ``approved`` tak dapat ditebak dari skema mana pun."""
    schema = strict_schema(Outer)
    example = example_instance(schema, {"name": "ditimpa", "label": "dalam"})

    assert example["name"] == "ditimpa"
    assert example["inner"]["label"] == "dalam"
    assert example["inner"]["count"] == 1, "field yang tidak di-override tetap diisi"


def test_example_instance_prefers_a_non_null_variant() -> None:
    """``anyOf`` memilih varian non-null: nilai null ditolak field yang diwajibkan."""
    schema = strict_schema(Inner)
    example = example_instance(schema["properties"]["note"])

    assert example is not None


def test_the_contract_names_every_field_and_shows_a_parseable_frame() -> None:
    """Bagian OUTPUT prompt dibangkitkan dari tipe, jadi ia tak dapat menyimpang."""
    contract = render_output_contract(ChapterDraft)

    for field in ("title", "learning_objectives", "sections", "summary"):
        assert field in contract

    frame = contract.split("```json", 1)[1].split("```", 1)[0]
    assert ChapterDraft.model_validate(json.loads(frame))


def test_the_module_exports_do_not_leak_sdk_types() -> None:
    """``structured`` hanya menyentuh pydantic + stdlib — gerbang kemurnian kecil."""
    import domain.structured as module

    assert "ollama" not in dir(module)
    assert set(module.__all__) >= {"strict_schema", "extract_json_object", "parse_output"}


def test_section_level_is_bounded_by_the_model() -> None:
    """Tingkat sub-bab dibatasi model sebelum sampai ke perender."""
    with pytest.raises(ValidationError):
        Section(heading="A", level=7)


def test_optional_field_defaults_are_declared() -> None:
    """Nilai bawaan tetap milik model, meski ``default`` dibuang dari skema."""
    assert Outer.model_validate(
        {"name": "x", "inner": {"label": "y"}, "tags": []}
    ).ratio == Field(default=0.5).default
