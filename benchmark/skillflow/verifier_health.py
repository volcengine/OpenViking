"""Detect verifier dependency/collection failures hidden by an outer pytest wrapper.

Do not label submission-dependent TypeError/IndexError/FileNotFoundError as an
outage: malformed workbooks and absent outputs legitimately cause those errors.
"""

import re
from pathlib import Path


def verifier_health(stdout: str) -> dict:
    patterns = {
        "missing_dependency": r"(?:ModuleNotFoundError: No module named|ImportError: Missing optional dependency)",
        "collection_error": r"(?:ERROR collecting |INTERNALERROR>|unrecognized arguments: --ctrf)",
        "expected_data_parser_failed": r"(?:VERIFIER_PDF_ERROR|No tables found matching regex|No tables found)[^\n]*",
    }
    problems = [name for name, pattern in patterns.items() if re.search(pattern, stdout)]
    return {"healthy": not problems, "problems": problems}


def check_verifier_stdout(path: Path) -> dict:
    if not path.is_file() or not path.stat().st_size:
        return {"healthy": False, "problems": ["missing_verifier_stdout"]}
    return verifier_health(path.read_text(errors="replace"))
