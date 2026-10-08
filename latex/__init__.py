"""Adapter LaTeX — batas sistem tempat sumber LaTeX bertemu filesystem (§25, §26).

Paket ini mengikuti aturan yang sama dengan ``ingestion/``, ``rag/``, dan
``graph/``: ia boleh menyentuh dunia luar (berkas, dan ``latexmk`` lewat
``subprocess``), ia hanya boleh bergantung pada ``domain/``, dan ia **tidak
boleh** mengenal ``app/``, ``agents/``, ``models/``, maupun ``memory/``. Batas
itu ditegakkan mesin oleh ``tests/unit/test_architecture.py``, bukan oleh
kesepakatan.

Empat bagian, dan pemisahannya mengikuti izin yang berbeda-beda:

* :mod:`latex.artifacts` — menulis ``.tex`` dan ``.bib`` ke ``output/latex/``.
* :mod:`latex.validator` — mengurai log ``latexmk`` menjadi
  :class:`~domain.latex.LatexBuildResult`. **Fungsi murni atas teks**, dan itu
  disengaja: parser log dapat diuji habis-habisan terhadap log contoh yang
  disimpan sebagai berkas uji, tanpa menjalankan LaTeX sama sekali.
* :mod:`latex.crossref_checker` — ``\\label``/``\\ref``/``\\cite`` yang
  menggantung di seluruh direktori LaTeX.
* :mod:`latex.compiler` — satu-satunya tempat di aplikasi ini yang menjalankan
  program lain.

:mod:`latex.dry_run` sengaja **tidak** di-import di sini. Ia hanya dipakai
composition root pada ``--dry-run``, dan meng-import-nya berarti setiap pemakai
paket ini — termasuk tes murni — ikut memuatnya.
"""

from latex.artifacts import (
    BIBLIOGRAPHY_FILENAME,
    FileLatexArtifacts,
    templates_dir,
)
from latex.compiler import MAIN_JOBNAME, PROBE_JOBNAME, LatexmkCompiler
from latex.crossref_checker import bibliography_keys, check_crossrefs
from latex.validator import parse_log

__all__ = [
    "BIBLIOGRAPHY_FILENAME",
    "MAIN_JOBNAME",
    "PROBE_JOBNAME",
    "FileLatexArtifacts",
    "LatexmkCompiler",
    "bibliography_keys",
    "check_crossrefs",
    "parse_log",
    "templates_dir",
]
