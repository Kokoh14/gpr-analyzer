"""One-click report package: PDF + Word report, zone CSV, KMZ map, survey JSON and a manifest, in one ZIP.

Everything comes from one SurveyResult and one composed report, so the PDF, the Word file, the CSV and
the map show the same zones with the same numbers. manifest.json lists the SHA-256 of every file in the
package and of the input data files, and says whether the package is a confirmed report or a draft (and why).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

from gpr_analyzer.report.compose import Signoff, compose
from gpr_analyzer.report.exports import survey_json, zones_csv
from gpr_analyzer.report.kml import write_kmz
from gpr_analyzer.report.schema import SCHEMA_VERSION, SurveyResult


def build_package(survey: SurveyResult, lang: str = "bi", signoff: Signoff | None = None,
                  tracks: dict[str, list[tuple[float, float]]] | None = None,
                  extra: dict[str, bytes] | None = None) -> tuple[dict[str, bytes], dict]:
    """(files, manifest). Report files are named DRAFT_report.* while the report is a draft."""
    from gpr_analyzer.report.docx_writer import render_docx
    from gpr_analyzer.report.pdf import render_pdf

    report = compose(survey, lang=lang, signoff=signoff)
    stem = "DRAFT_report" if report.draft else "report"
    files = {f"{stem}.pdf": render_pdf(report), f"{stem}.docx": render_docx(report),
             # BOM so Excel reads Thai (same bytes as the single CSV export)
             "zones.csv": zones_csv(survey).encode("utf-8-sig"),
             "zones.kmz": write_kmz(survey, tracks=tracks),
             "survey.json": survey_json(survey).encode("utf-8")}
    files.update(extra or {})
    so = signoff or Signoff()
    manifest = {
        "status": "draft" if report.draft else "final",
        "draft_reasons": [{"code": c, "params": p} for c, p in so.draft_reasons]
        + ([{"code": "unreviewed", "params": {}}] if survey.unreviewed and not so.draft_reasons else []),
        "generated_at": so.generated_at, "reviewer": so.reviewer, "software": f"gpr-analyzer {so.app_version}".strip(),
        "lang": lang, "schema_version": SCHEMA_VERSION,
        "zones": {"reported": sum(1 for z in survey.zones if z.review != "rejected"),
                  "rejected": sum(1 for z in survey.zones if z.review == "rejected"),
                  "unreviewed": sum(1 for z in survey.zones if z.review == "auto")},
        "inputs": so.inputs,
        "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()},
    }
    return files, manifest


def write_package(survey: SurveyResult, path: str | os.PathLike, **kw) -> tuple[Path, dict]:
    """Write the ZIP (see build_package for the arguments) and return (path, manifest)."""
    files, manifest = build_package(survey, **kw)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    path = Path(path)
    path.write_bytes(buf.getvalue())
    return path, manifest
