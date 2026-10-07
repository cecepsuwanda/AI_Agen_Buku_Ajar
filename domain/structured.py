"""Penanganan keluaran LLM terstruktur — MURNI (hanya stdlib + pydantic).

Tiga tugas, dan ketiganya murni sehingga bisa diuji habis-habisan tanpa LLM:

1. :func:`strict_schema` — membangkitkan skema JSON yang cukup ketat untuk
   dikompilasi Ollama menjadi grammar GBNF yang andal.
2. :func:`extract_json_object` — menyelamatkan JSON dari teks yang berantakan.
3. :func:`parse_output` — memvalidasi hasilnya menjadi tipe domain.

Catatan versi: rencana awal memakai sintaks generic PEP 695 (``def f[T]()``),
yang baru ada di Python 3.12. Mesin ini Python 3.11.9, jadi dipakai
``TypeVar`` — perilakunya sama, hanya sintaksnya berbeda.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel, ValidationError

from domain.base import FrozenModel

TModel = TypeVar("TModel", bound=BaseModel)

#: Batas kedalaman saat men-*inline* ``$ref``; mencegah skema rekursif tak berujung.
_MAX_REF_DEPTH = 16

#: Karakter tak terlihat yang kerap menyelinap dari output model.
_INVISIBLE = dict.fromkeys(map(ord, "﻿​‌‍⁠"), None)

_REF_PREFIX = "#/$defs/"


# ---------------------------------------------------------------------------
# 1. Skema ketat
# ---------------------------------------------------------------------------
def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Bangun skema JSON yang siap dikirim ke parameter ``format`` Ollama.

    Dua hal yang dilakukan, dan keduanya **bukan kosmetik**:

    * Setiap objek ditutup (``additionalProperties: false``) dan seluruh
      propertinya ditandai ``required``. Skema yang membiarkan properti opsional
      membuat grammar terlalu longgar: model dapat menghasilkan objek yang
      **sah secara grammar tetapi salah secara makna**.
    * ``$ref`` di-*inline*. Skema jadi berdiri sendiri, sehingga tidak bergantung
      pada kemampuan server menelusuri ``$defs``.

    Skema dikembalikan sebagai ``dict`` mentah — signature SDK 0.6.2 menyatakan
    ``format`` bertipe ``Literal['', 'json'] | dict | None``, jadi kita tidak
    bersandar pada dukungan kelas Pydantic yang tidak dijanjikan di sana.
    """
    raw = model.model_json_schema()
    defs: Mapping[str, Any] = raw.pop("$defs", {}) or {}
    resolved = _resolve_refs(raw, defs, depth=0)
    if not isinstance(resolved, dict):  # pragma: no cover - model domain selalu objek
        raise TypeError(f"Skema untuk {model.__name__} bukan objek JSON")
    strict: dict[str, Any] = _strictify(resolved)
    return strict


def _resolve_refs(node: Any, defs: Mapping[str, Any], *, depth: int) -> Any:
    """Ganti setiap ``{"$ref": "#/$defs/X"}`` dengan isi definisinya."""
    if depth > _MAX_REF_DEPTH:
        raise RecursionError("Skema terlalu dalam untuk di-inline")
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
            name = ref[len(_REF_PREFIX) :]
            if name not in defs:
                raise KeyError(f"$ref {ref!r} tidak ada di $defs")
            return _resolve_refs(defs[name], defs, depth=depth + 1)
        return {k: _resolve_refs(v, defs, depth=depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, defs, depth=depth) for item in node]
    return node


def _strictify(node: Any) -> Any:
    """Tutup setiap objek dan paksa seluruh propertinya wajib diisi."""
    if isinstance(node, dict):
        out: dict[str, Any] = {k: _strictify(v) for k, v in node.items()}

        properties = out.get("properties")
        if isinstance(properties, dict):
            out["type"] = "object"
            out["additionalProperties"] = False
            out["required"] = sorted(properties.keys())

        # `default` membuat sebagian server memperlakukan properti sebagai
        # opsional; kita sudah memaksa `required`, jadi hapus agar tidak rancu.
        out.pop("default", None)
        return out

    if isinstance(node, list):
        return [_strictify(item) for item in node]
    return node


# ---------------------------------------------------------------------------
# 2. Menyelamatkan JSON dari teks berantakan
# ---------------------------------------------------------------------------
def extract_json_object(raw: str) -> str | None:
    """Ambil objek JSON terluar yang seimbang dari keluaran model.

    Menangani kegagalan yang benar-benar sering terjadi: pagar markdown,
    prakata prosa ("Berikut JSON-nya:"), BOM, karakter zero-width, dan koma
    di ujung. Pencarian kurung **menghormati string literal** — regex akan
    rusak begitu ada ``}`` di dalam sebuah string.

    :returns: teks objek JSON, atau ``None`` bila tak ada objek yang seimbang
        (mis. keluaran terpotong).
    """
    text = _strip_wrappers(raw)
    start = text.find("{")
    if start < 0:
        return None
    balanced = _balanced_object(text, start)
    if balanced is None:
        return None
    return _drop_trailing_commas(balanced)


def _strip_wrappers(raw: str) -> str:
    """Buang BOM, karakter tak terlihat, dan pagar markdown **yang membungkus**.

    Pagar hanya dibuang bila ia benar-benar membungkus objek — yaitu bila ``{``
    pertama muncul **setelah** pagar pertama. Bila ``{`` sudah muncul lebih
    dulu, pagar itu berada **di dalam** JSON: contoh kode berpagar di dalam
    sebuah string ``body``, yang justru lazim untuk buku ajar pemrograman.

    Perbedaan itu bukan detail kosmetik, dan salah satu sisinya sunyi. Model
    yang menulis ``"body": "```python…```"`` menghasilkan JSON yang sah; kalau
    pagarnya dipotong di situ, yang tersisa adalah teks **di antara** dua pagar
    — dan bila potongan itu kebetulan juga berupa objek JSON yang sah, hasilnya
    lolos parse, tidak memicu satu galat pun, dan menjadi bab yang salah isi.
    Terjadi sungguhan (``state/parse_fail/chapter01.attempt3.txt``): keluaran
    10.449 karakter menyusut menjadi ``{target_value}``.
    """
    text = raw.translate(_INVISIBLE).strip()

    fence = text.find("```")
    brace = text.find("{")
    if 0 <= fence < brace:
        # Pagar mendahului objek → ia pembungkus. Ambil isi blok pertama;
        # label bahasa (mis. ```json) dibuang.
        parts = text.split("```")
        if len(parts) >= 3:
            body = parts[1]
            first_newline = body.find("\n")
            if first_newline >= 0:
                body = body[first_newline + 1 :]
            text = body.strip()

    # Prakata prosa sebelum objek dibuang oleh pemanggil lewat ``text.find("{")``:
    # di sini hanya pembungkus yang dilepas, sehingga teks aslinya tetap utuh
    # bila tidak ada objek sama sekali dan pesan galatnya jujur.
    return text


def _balanced_object(text: str, start: int) -> str | None:
    """Kembalikan substring dari ``{`` di ``start`` sampai penutup yang seimbang."""
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None  # tidak seimbang → keluaran kemungkinan terpotong


def _drop_trailing_commas(text: str) -> str:
    """Hapus koma sebelum ``}``/``]`` — string-aware."""
    out: list[str] = []
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            out.append(char)
            continue

        if char == ",":
            # Lihat karakter bermakna berikutnya; bila penutup, koma dibuang.
            rest = text[index + 1 :].lstrip()
            if rest[:1] in ("}", "]"):
                continue

        out.append(char)

    return "".join(out)


# ---------------------------------------------------------------------------
# 3. Validasi menjadi tipe domain
# ---------------------------------------------------------------------------
def parse_output(raw: str, model: type[TModel]) -> TModel:
    """Validasi keluaran LLM menjadi ``model``.

    :raises ValidationError: bila JSON valid tetapi tidak sesuai skema.
    :raises ValueError: bila tidak ada objek JSON yang bisa diselamatkan.
    """
    candidate = extract_json_object(raw)
    if candidate is None:
        raise ValueError(
            "Tidak menemukan objek JSON yang seimbang di keluaran model "
            "(kemungkinan terpotong karena anggaran token habis)"
        )
    return model.model_validate_json(candidate)


def is_json_parseable(raw: str) -> bool:
    """True bila ``raw`` memuat objek JSON yang bisa di-parse (untuk diagnostik)."""
    candidate = extract_json_object(raw)
    if candidate is None:
        return False
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return False
    return True


# ---------------------------------------------------------------------------
# Kontrak OUTPUT untuk prompt (§36)
# ---------------------------------------------------------------------------
def render_output_contract(model: type[BaseModel], *, indent: int = 2) -> str:
    """Render daftar field + kerangka JSON dari tipe, untuk bagian OUTPUT prompt.

    Dua lapis penegakan §36 dimulai di sini. Bagian OUTPUT yang ditulis tangan
    menyimpang dari skema ``format=`` dalam hitungan minggu; yang **dibangkitkan
    dari tipe yang sama** tidak mungkin menyimpang, karena keduanya berasal dari
    model Pydantic yang sama.
    """
    schema = strict_schema(model)
    properties: Mapping[str, Any] = schema.get("properties", {}) or {}

    lines = ["Objek JSON dengan field berikut (semuanya WAJIB ada):", ""]
    for name, spec in properties.items():
        lines.append(f"  - {name}: {_describe_type(spec)}")
    lines.append("")
    lines.append("Kerangka:")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(example_instance(schema), indent=indent, ensure_ascii=False))
    lines.append("```")
    return "\n".join(lines)


def _describe_type(spec: Mapping[str, Any]) -> str:
    """Deskripsi tipe yang enak dibaca manusia untuk satu properti."""
    if "anyOf" in spec:
        return " | ".join(_describe_type(s) for s in spec["anyOf"])
    kind = spec.get("type", "any")
    if kind == "array":
        items = spec.get("items", {})
        return f"array<{_describe_type(items)}>"
    if kind == "object":
        nested = spec.get("properties", {}) or {}
        return "object{" + ", ".join(nested.keys()) + "}"
    if "enum" in spec:
        return " | ".join(repr(v) for v in spec["enum"])
    return str(kind)


def example_instance(
    schema: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
    *,
    field_name: str | None = None,
) -> Any:
    """Bangun satu instance **yang benar-benar lolos validasi** dari ``schema`` (MURNI).

    Ini fungsi **kembar** dari :func:`strict_schema`: yang satu mempersempit
    skema agar model tidak dapat menyimpang, yang ini menyusun nilai yang
    memenuhinya. Keduanya membaca bentuk skema yang sama, dan itu sebabnya ia
    tinggal di sini — bukan di tiga tempat.

    Ia melayani tiga pemakai dengan kebutuhan yang sama persis:

    * :func:`render_output_contract` — contoh di prompt harus konkret.
    * ``models.dry_run`` — ``--dry-run`` harus menempuh pipeline sungguhan
      tanpa jaringan, dan pipeline tidak dapat dilewati dengan nilai palsu.
    * ``tests.fakes.chat_models`` — tes integrasi tidak menyimpan JSON tulisan
      tangan, sehingga ia tidak membusuk saat model domain berubah.

    **String sengaja tidak kosong** (``min_length=1`` menolaknya, dan instance
    yang gagal validasi bukan contoh yang berguna); **array berisi satu elemen**
    (array kosong sering ditolak ``min_length``); dan seluruh properti terisi
    karena ``strict_schema`` menandai semuanya wajib.

    ``overrides`` menimpa nilai berdasarkan **nama properti**, di kedalaman mana
    pun. Itulah satu-satunya jalan mengarahkan skenario: ``approved=True`` pada
    peninjau tidak dapat ditebak dari skema mana pun.
    """
    overrides = overrides or {}
    if field_name is not None and field_name in overrides:
        return overrides[field_name]

    if "anyOf" in schema:
        # Utamakan varian non-null: nilai null ditolak oleh field yang memang
        # diwajibkan, dan pemanggil akan gagal karena alasan yang salah.
        variants = [v for v in schema["anyOf"] if isinstance(v, Mapping)]
        preferred = next((v for v in variants if v.get("type") != "null"), variants[0])
        return example_instance(preferred, overrides, field_name=field_name)

    if "enum" in schema:
        return schema["enum"][0]

    kind = schema.get("type")
    if kind == "object":
        properties: Mapping[str, Any] = schema.get("properties", {}) or {}
        return {
            name: example_instance(spec, overrides, field_name=name)
            for name, spec in properties.items()
        }
    if kind == "array":
        return [example_instance(schema.get("items", {}), overrides, field_name=field_name)]
    if kind == "string":
        return field_name or "teks"
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    return field_name or "teks"


__all__ = [
    "FrozenModel",
    "ValidationError",
    "example_instance",
    "extract_json_object",
    "is_json_parseable",
    "parse_output",
    "render_output_contract",
    "strict_schema",
]
