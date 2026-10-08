"""Adapter LaTeX — batas sistem tempat sumber LaTeX bertemu filesystem (§25).

Paket ini mengikuti aturan yang sama dengan ``ingestion/``, ``rag/``, dan
``graph/``: ia boleh menyentuh dunia luar (berkas, dan kelak ``latexmk`` lewat
``subprocess``), ia hanya boleh bergantung pada ``domain/``, dan ia **tidak
boleh** mengenal ``app/``, ``agents/``, ``models/``, maupun ``memory/``. Batas
itu ditegakkan mesin oleh ``tests/unit/test_architecture.py``, bukan oleh
kesepakatan.

Isi paket ini pada tahap ini kecil dan itu disengaja: menulis ``.tex`` dan
``.bib`` (lihat :mod:`latex.artifacts`) serta template yang mendefinisikan
environment-nya. Mengompilasi adalah pekerjaan Tahap 7 (§26), dan ia akan tiba
sebagai ``latex/compiler.py`` di sini juga — di paket yang sama, karena
``latexmk`` adalah batas sistem yang sama.
"""

from latex.artifacts import (
    BIBLIOGRAPHY_FILENAME,
    FileLatexArtifacts,
    templates_dir,
)

__all__ = [
    "BIBLIOGRAPHY_FILENAME",
    "FileLatexArtifacts",
    "templates_dir",
]
