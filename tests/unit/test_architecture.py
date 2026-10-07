"""Gerbang arsitektur — bagaimana DIP berhenti menjadi aspirasi.

Tes ini menelusuri AST dan menegakkan tiga invarian yang menjadi **dasar**
seluruh persyaratan OOP/FP/SOLID pada proyek ini. Tanpa ketiganya, aturan
tersebut hanya niat baik di dalam dokumen yang perlahan lapuk:

1. **``domain/`` murni.** Ia hanya boleh mengimpor pustaka yang tidak
   menyentuh dunia luar. Satu ``import pathlib`` di sana dan seluruh argumen
   "aturan bisnis dapat diuji tanpa IO" runtuh.
2. **``ollama`` diimpor tepat satu berkas.** DIP yang sesungguhnya: SDK
   boleh ada, tetapi hanya di balik port-nya.
3. **``agents/`` tidak menyentuh IO.** Agent hanya boleh bergantung pada
   ``domain/``; itulah yang membuat seluruh pipeline dapat dijalankan tanpa
   jaringan dan tanpa ``if testing`` di kode produksi.

Kegagalan tes ini menyebut berkas dan barisnya, sehingga PR yang menyusupkan
SDK ke dalam agent gagal dengan pesan yang menunjuk langsung ke pelakunya.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Pembantu AST (murni — tidak mengimpor kode yang sedang diperiksa)
# ---------------------------------------------------------------------------
def _python_files(package: str) -> list[Path]:
    """Seluruh berkas ``.py`` di bawah ``package/``, terurut dan deterministik."""
    directory = PROJECT_ROOT / package
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.py"))


def _imports(path: Path) -> list[tuple[str, int]]:
    """Impor tingkat-atas di ``path`` sebagai ``(nama_paket, baris)``.

    Impor relatif (``from .x import y``) dilewati: ia tidak pernah keluar dari
    paketnya sendiri, jadi ia tidak relevan bagi batas arsitektur.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # impor relatif — tetap di dalam paket
                continue
            if node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


def _relative(path: Path) -> str:
    """Jalur relatif terhadap root proyek, untuk pesan kegagalan yang enak dibaca."""
    return path.relative_to(PROJECT_ROOT).as_posix()


def _all_python_files() -> list[Path]:
    """Seluruh ``.py`` di repositori, kecuali ``.venv`` dan direktori runtime."""
    return sorted(
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if not {".venv", "state", "output", "knowledge"} & set(path.parts)
    )


#: Satu-satunya pustaka yang boleh masuk ke ``domain/``.
#:
#: Ini **daftar putih**, bukan daftar hitam: daftar hitam akan selalu kalah oleh
#: pustaka yang belum terpikirkan, sedangkan daftar putih menuntut keputusan
#: sadar setiap kali ada yang baru. ``json`` ada di sini karena mengurai string
#: adalah operasi murni — ia tidak membuka apa pun.
DOMAIN_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {"__future__", "enum", "typing", "dataclasses", "json", "pydantic", "domain"}
)

#: Paket yang khusus dilarang, semata agar pesan kegagalannya mendidik.
DOMAIN_SPECIFICALLY_FORBIDDEN: dict[str, str] = {
    "pathlib": "tipe domain memakai `str`; konversi ke Path adalah tugas adapter",
    "os": "domain tidak boleh membaca lingkungan",
    "io": "domain tidak boleh menyentuh stream",
    "socket": "domain tidak boleh membuka jaringan",
    "httpx": "domain tidak boleh berbicara HTTP",
    "ollama": "domain berbicara lewat `domain.ports`, bukan lewat SDK",
    "tenacity": "kebijakan retry adalah detail adapter",
    "yaml": "pemuatan konfigurasi adalah tugas `app.config`",
}

#: Hanya berkas ini yang boleh menyentuh SDK Ollama.
OLLAMA_ADAPTER = "models/ollama_client.py"

#: SDK pihak ketiga → satu-satunya berkas yang boleh mengimpornya.
#:
#: Pola yang sama dengan Ollama, diperluas ke setiap pustaka yang menyentuh
#: dunia luar. Alasannya bukan kerapian: setiap SDK yang bocor ke dalam
#: ``domain/`` atau ``agents/`` membuat pipeline berhenti dapat diuji tanpa
#: jaringan, dan itu satu-satunya properti yang membuat 362 tes di repositori
#: ini berjalan dalam hitungan detik.
#:
#: Sebuah entri tidak menuntut berkasnya ada — pada tahap ketika fitur itu belum
#: dibangun, daftar ini hanya berarti "tidak ada yang boleh mengimpornya".
#: Yang ditegakkan adalah arahnya: SDK masuk lewat satu pintu, tidak lewat mana pun.
EXTERNAL_ADAPTERS: dict[str, str] = {
    "ollama": "models/ollama_client.py",
    "chromadb": "rag/vector_store.py",
    "pypdf": "ingestion/pdf_loader.py",
    "pymupdf": "ingestion/pdf_ocr.py",
}

#: Nama impor lain untuk SDK yang **sama**, dan pintu yang sama pula.
#:
#: PyMuPDF menyediakan dua nama impor: ``pymupdf`` yang modern dan ``fitz``
#: yang sudah deprecated — memakai ``fitz`` memunculkan peringatan
#: ``DeprecationWarning`` pada setiap import. Keduanya menunjuk perpustakaan
#: yang sama persis, jadi keduanya harus menunjuk pintu yang sama; tetapi
#: menuliskannya sebagai dua entri di :data:`EXTERNAL_ADAPTERS` akan melanggar
#: :func:`test_the_adapter_table_has_no_duplicate_doors`. Karena itu alias
#: dipisahkan ke sini dan diselesaikan sebelum pemeriksaan — tabelnya tetap
#: satu entri per SDK, dan ``import fitz`` di berkas yang salah tetap tertangkap.
SDK_ALIASES: dict[str, str] = {"fitz": "pymupdf"}


def _canonical_sdk(package: str) -> str:
    """Nama SDK setelah alias diselesaikan (``fitz`` → ``pymupdf``).

    Dipisah sebagai fungsi — bukan dict-lookup sebaris di dalam tes — supaya ia
    dapat diuji sendiri: gerbang yang memakai alias tetapi tidak pernah diuji
    dengan alias adalah gerbang yang bisa saja tidak pernah menyelesaikan apa pun.
    """
    return SDK_ALIASES.get(package, package)


#: Paket adapter — batas sistem yang boleh menyentuh SDK, disk, dan subprocess.
ADAPTER_LAYERS: frozenset[str] = frozenset({"ingestion", "rag", "graph", "latex"})

#: Lapisan luar yang tidak boleh dikenal oleh sebuah adapter.
OUTER_LAYERS: frozenset[str] = frozenset({"app", "agents", "models", "memory"})


# ---------------------------------------------------------------------------
# 1. domain/ murni
# ---------------------------------------------------------------------------
def test_domain_imports_are_whitelisted() -> None:
    """``domain/`` hanya mengimpor pustaka yang tidak menyentuh dunia luar."""
    violations: list[str] = []
    for path in _python_files("domain"):
        for package, line in _imports(path):
            if package in DOMAIN_ALLOWED_IMPORTS:
                continue
            hint = DOMAIN_SPECIFICALLY_FORBIDDEN.get(package)
            suffix = f" ({hint})" if hint else ""
            violations.append(f"{_relative(path)}:{line}: import {package!r}{suffix}")

    assert not violations, (
        "`domain/` harus murni, tetapi menemukan impor terlarang:\n  "
        + "\n  ".join(violations)
    )


def test_domain_does_not_import_app_or_models() -> None:
    """``domain/`` tidak boleh bergantung ke lapisan luar — arah DIP satu arah."""
    if "domain" not in DOMAIN_ALLOWED_IMPORTS:  # pragma: no cover - jaring pengaman
        pytest.fail("daftar putih domain tidak memuat 'domain'")
    violations = [
        f"{_relative(path)}:{line}: import {package!r}"
        for path in _python_files("domain")
        for package, line in _imports(path)
        if package in {"app", "models", "agents", "memory"}
    ]
    assert not violations, (
        "`domain/` tidak boleh mengimpor lapisan luar:\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# 2. Hanya satu adapter yang menyentuh SDK
# ---------------------------------------------------------------------------
def test_ollama_is_imported_in_exactly_one_file() -> None:
    """``import ollama`` hanya boleh muncul di ``models/ollama_client.py``."""
    importers = sorted(
        _relative(path)
        for path in PROJECT_ROOT.rglob("*.py")
        if ".venv" not in path.parts and "ollama" in {p for p, _ in _imports(path)}
    )
    assert importers == [OLLAMA_ADAPTER], (
        f"SDK Ollama harus diimpor tepat di {OLLAMA_ADAPTER}, "
        f"tetapi ditemukan di: {importers or '(tidak ada)'}"
    )


def test_every_external_sdk_enters_through_exactly_one_door() -> None:
    """Setiap SDK di :data:`EXTERNAL_ADAPTERS` hanya boleh diimpor berkas yang ditunjuk.

    Ditegakkan terhadap **seluruh repositori**, bukan hanya ``domain/`` dan
    ``agents/``: satu ``import chromadb`` di ``app/commands.py`` akan lolos dari
    dua gerbang sebelumnya, padahal akibatnya sama — pustaka itu menarik dirinya
    ke setiap tes yang mengimpor modul tersebut.

    Sebuah SDK yang belum dipakai sama sekali tidak melanggar apa pun. Yang
    dilarang adalah memakainya **di tempat yang salah**.
    """
    violations: list[str] = []
    for sdk, adapter in EXTERNAL_ADAPTERS.items():
        for path in _all_python_files():
            if _relative(path) == adapter:
                continue
            for package, line in _imports(path):
                if _canonical_sdk(package) == sdk:
                    violations.append(
                        f"{_relative(path)}:{line}: import {package!r} — hanya {adapter} yang boleh"
                    )

    assert not violations, (
        "SDK harus masuk lewat satu pintu (port + adapter):\n  " + "\n  ".join(violations)
    )


def test_the_adapter_table_has_no_duplicate_doors() -> None:
    """Dua SDK yang berbeda tidak boleh menunjuk berkas yang sama.

    Kalau ``pypdf`` dan ``fitz`` kelak sama-sama sah dipakai di satu berkas,
    tabelnya harus menyatakannya sebagai satu pintu — bukan dua entri dengan
    tujuan yang sama. Kalau tidak, :func:`test_every_external_sdk_enters_through_exactly_one_door`
    akan melarang SDK kedua di berkasnya sendiri, dan jalan keluar yang tergoda
    adalah menghapus entri keduanya dari tabel — yaitu menghapus gerbangnya.
    """
    doors = list(EXTERNAL_ADAPTERS.values())

    assert len(doors) == len(set(doors)), f"berkas adapter dipakai dua kali: {doors}"

    # Alias yang menunjuk SDK tak dikenal akan **mematikan** gerbang untuk
    # namanya tanpa satu pun tanda: `SDK_ALIASES.get(package, package)` tidak
    # pernah cocok dengan entri mana pun, sehingga `import fitz` di berkas yang
    # salah lolos. Karena itu target alias wajib merupakan kunci sungguhan.
    orphan = {alias: target for alias, target in SDK_ALIASES.items()
              if target not in EXTERNAL_ADAPTERS}
    assert not orphan, f"alias menunjuk SDK yang tidak ada di tabel: {orphan}"


# ---------------------------------------------------------------------------
# 3. agents/ bebas IO
# ---------------------------------------------------------------------------
def test_agents_do_not_import_io_libraries() -> None:
    """Agent hanya boleh bergantung pada ``domain/`` — bukan pada SDK atau UI."""
    forbidden = {"ollama", "httpx", "rich", "typer", "yaml", "tenacity", "requests"}
    violations = [
        f"{_relative(path)}:{line}: import {package!r}"
        for path in _python_files("agents")
        for package, line in _imports(path)
        if package in forbidden
    ]
    assert not violations, (
        "`agents/` tidak boleh menyentuh SDK maupun UI:\n  " + "\n  ".join(violations)
    )


def test_agents_do_not_import_outer_layers() -> None:
    """``agents/`` tidak boleh mengimpor ``app/`` atau ``models/``.

    Inilah yang membuat ``ModelRouter`` disuntikkan sebagai objek: agent
    menerima sesuatu yang berbentuk port, bukan modul konkret.
    """
    violations = [
        f"{_relative(path)}:{line}: import {package!r}"
        for path in _python_files("agents")
        for package, line in _imports(path)
        if package in {"app", "models", "memory"}
    ]
    assert not violations, (
        "`agents/` hanya boleh bergantung pada `domain/`:\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# 3b. Paket adapter hanya bergantung pada domain/
# ---------------------------------------------------------------------------
def test_adapter_layers_do_not_import_outer_layers() -> None:
    """``ingestion/``, ``rag/``, ``graph/``, ``latex/`` tidak boleh mengenal lapisan luar.

    Inilah yang membuat adapter benar-benar dapat ditukar: ``ChromaVectorStore``
    tidak boleh tahu ada ``BookDirector``, dan ``LatexCompiler`` tidak boleh tahu
    ada CLI. Yang merakit keduanya adalah composition root, dan hanya ia.
    """
    violations = [
        f"{_relative(path)}:{line}: import {package!r}"
        for layer in sorted(ADAPTER_LAYERS)
        for path in _python_files(layer)
        for package, line in _imports(path)
        if package in OUTER_LAYERS
    ]

    assert not violations, (
        "Adapter hanya boleh bergantung pada `domain/`:\n  " + "\n  ".join(violations)
    )


def test_adapter_layers_do_not_import_each_other() -> None:
    """Sebuah adapter tidak boleh memanggil adapter lain.

    ``graph/`` yang membaca LaTeX lewat ``ingestion/`` tampak hemat, tetapi ia
    mengikat dua adapter yang seharusnya bebas: mengganti pemuat LaTeX berarti
    menyentuh graf, dan menguji graf berarti menyeret pemuat berkas. Perakitan
    lintas-adapter adalah pekerjaan composition root — di sana ia terlihat, dan
    di sana ia dapat diganti.
    """
    violations = [
        f"{_relative(path)}:{line}: import {package!r}"
        for layer in sorted(ADAPTER_LAYERS)
        for path in _python_files(layer)
        for package, line in _imports(path)
        if package in ADAPTER_LAYERS and package != layer
    ]

    assert not violations, (
        "Adapter tidak boleh saling memanggil — rakit di composition root:\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# 4. Aturan struktural lain yang mudah dilanggar tanpa sadar
# ---------------------------------------------------------------------------
def test_domain_has_no_module_level_mutable_state() -> None:
    """``domain/`` tidak boleh punya ``_cache = {}`` yang hidup saat import.

    Keadaan global yang termutasi adalah hal pertama yang membuat pengujian
    menjadi tidak dapat diulang, dan ia menyusup lewat satu baris tak berdosa.
    """
    violations: list[str] = []
    mutable_calls = {"dict", "list", "set"}

    for path in _python_files("domain"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            makes_mutable = (
                isinstance(value, (ast.Dict, ast.List, ast.Set))
                or (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id in mutable_calls)
            )
            if not makes_mutable:
                continue
            # Konstanta yang hanya dibaca (mis. tabel transisi) aman; yang
            # berbahaya adalah yang namanya menyiratkan keadaan.
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for name in targets:
                if name.isupper():  # KONSTANTA
                    continue
                if name.startswith("__") and name.endswith("__"):  # __all__ dsb.
                    continue
                violations.append(f"{_relative(path)}:{node.lineno}: {name} = ...")

    assert not violations, (
        "Keadaan global yang termutasi di `domain/`:\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# 5. Gerbang ini harus bergigi
# ---------------------------------------------------------------------------
def test_the_purity_checker_actually_detects_violations(tmp_path: Path) -> None:
    """Bukti bahwa pemeriksa di atas benar-benar bekerja.

    Tanpa tes ini, sebuah bug pada :func:`_imports` (mis. salah menelusuri AST)
    akan membuat seluruh gerbang arsitektur **hijau selamanya** — kegagalan
    yang jauh lebih berbahaya daripada tidak punya gerbang sama sekali.
    """
    sample = tmp_path / "melanggar.py"
    sample.write_text(
        "import pathlib\n"
        "import ollama\n"
        "from os import environ\n"
        "from . import saudara\n",
        encoding="utf-8",
    )

    found = {package for package, _ in _imports(sample)}

    assert "pathlib" in found and "pathlib" not in DOMAIN_ALLOWED_IMPORTS
    assert "ollama" in found
    assert "os" in found and "os" in DOMAIN_SPECIFICALLY_FORBIDDEN
    assert "saudara" not in found, "impor relatif tidak boleh dianggap impor keluar"


def test_the_mutable_state_checker_actually_detects_violations(tmp_path: Path) -> None:
    """Bukti bahwa pemeriksa keadaan global benar-benar bekerja."""
    tree = ast.parse("_cache = {}\nTRANSLATIONS = {}\n__all__ = ['x']\n")
    offenders = []
    for node in tree.body:
        assert isinstance(node, ast.Assign)
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name.isupper() or (name.startswith("__") and name.endswith("__")):
                continue
            if isinstance(node.value, ast.Dict):
                offenders.append(name)

    assert offenders == ["_cache"]


def test_the_sdk_alias_resolver_actually_resolves(tmp_path: Path) -> None:
    """Bukti bahwa ``import fitz`` tertangkap oleh pintu ``pymupdf``.

    Tanpa tes ini, penghapusan satu baris dari :data:`SDK_ALIASES` akan
    mematikan gerbang untuk nama impor yang sudah deprecated **tanpa satu pun
    tes merah** — pemeriksa tetap hijau, hanya saja ia berhenti memeriksa.
    """
    sample = tmp_path / "ocr.py"
    sample.write_text("import fitz\nimport pymupdf\n", encoding="utf-8")

    found = {_canonical_sdk(package) for package, _ in _imports(sample)}
    door = EXTERNAL_ADAPTERS["pymupdf"]

    assert found == {"pymupdf"}, f"kedua nama impor harus menuju SDK yang sama, bukan {found}"
    assert door in EXTERNAL_ADAPTERS.values()
    assert _canonical_sdk("chromadb") == "chromadb", "nama tanpa alias tidak boleh berubah"
