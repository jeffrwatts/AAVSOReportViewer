"""Entry point for the PyInstaller builds (PyInstaller needs a script, not a module)."""

import sys

from photometry_workbench.app import main

sys.exit(main())
