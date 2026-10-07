"""Titik masuk ``python -m app`` (§42).

Bentuk kanonik pemanggilan adalah ``python -m app <perintah>``. Modul ini
sengaja hanya berisi beberapa baris: seluruh pengetahuan tentang argumen, dan
tentang penyiapan terminal, tinggal di :mod:`app.cli`.
"""

from __future__ import annotations

from app.cli import main

if __name__ == "__main__":
    main()
