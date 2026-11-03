"""Entry point for the compiled executable (PyInstaller)."""

import sys

from alfred_desktop.cli import main

if __name__ == "__main__":
    sys.exit(main())
