"""Run the same unittest suite locally and in CI, with a durable report."""
import io
import os
from pathlib import Path
import sys
import unittest


def main() -> int:
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    if suite.countTestCases() == 0:
        print("ERROR: no se descubrieron pruebas del backend", file=sys.stderr)
        return 1
    report = io.StringIO()
    result = unittest.TextTestRunner(stream=report, verbosity=2).run(suite)
    output = report.getvalue()
    print(output, end="")
    (root / "test-results.txt").write_text(output, encoding="utf-8")
    if summary_path := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(
                "## Pruebas del backend\n\n"
                "| Resultado | Cantidad |\n| --- | ---: |\n"
                f"| Ejecutadas | {result.testsRun} |\n"
                f"| Fallos | {len(result.failures)} |\n"
                f"| Errores | {len(result.errors)} |\n"
                f"| Omitidas | {len(result.skipped)} |\n\n"
                "El detalle está en el artefacto `backend-check-results`.\n"
            )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
