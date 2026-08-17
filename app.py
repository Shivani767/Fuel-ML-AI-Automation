"""Compatibility shim — prefer `python -m backend.app` or `python backend/app.py`."""

from backend.app import app, main

if __name__ == "__main__":
    main()
