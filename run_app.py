"""Start the local application:  .venv\Scripts\python.exe run_app.py  ->  http://127.0.0.1:8765"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

from pci_app.server import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
