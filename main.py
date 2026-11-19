"""Entry point — run with: uvicorn main:app --host 0.0.0.0 --port 8000"""
import sys
import os

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

from server import app
