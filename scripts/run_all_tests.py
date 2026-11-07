"""
Master Automated Verification Runner for PowerPOS SUNMI Hardware Platform.
Executes all unit, hardware, integration, security, and end-to-end test suites.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for folder in ["unit", "hardware", "integration", "security", "end-to-end"]:
        target = ROOT_DIR / "tests" / folder
        if target.is_dir():
            discovered = loader.discover(start_dir=str(target), pattern="test_*.py")
            suite.addTests(discovered)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

