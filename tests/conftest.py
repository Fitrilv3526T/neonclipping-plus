"""Shared pytest setup for the NeonClipping smoke tests.

Runs BEFORE any repo module is imported: no yt-dlp auto-update, no GPU assumptions, and a
throwaway data folder so tests never touch real clips. Every smoke test works with NO GPU
and NO internet (the speech model is faked where the pipeline would call it).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault('NEONCLIPPING_NO_AUTOUPDATE', '1')
os.environ.setdefault('NEONCLIPPING_DATA_DIR', os.path.join(ROOT, 'tests', 'out', 'data'))
os.environ.setdefault('NEONCLIPPING_CPU', '1')          # never touch a GPU from the tests
