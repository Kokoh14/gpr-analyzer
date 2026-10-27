"""Report generation (PDF / Word / HTML, plus CSV / JSON zone exports).

    from gpr_analyzer.report import write_report
    write_report(survey, "out.pdf", lang="bi")   # "th", "en" or "bi" (bilingual)

`survey` is a report.schema.SurveyResult; the detect package emits report.schema.Zone records.
"""

from __future__ import annotations

import os
from pathlib import Path

from gpr_analyzer.report.compose import Report, compose
from gpr_analyzer.report.schema import (ArtifactRegion, Evidence, LineResult, ProcessingStep, SurveyInfo,
                                        SurveyResult, Zone)

__all__ = ["write_report", "compose", "Report", "SurveyResult", "SurveyInfo", "LineResult", "Zone",
           "Evidence", "ProcessingStep", "ArtifactRegion", "FORMATS"]

FORMATS = (".pdf", ".docx", ".html", ".json", ".csv", ".cnf", ".pobj", ".lobj")


def write_report(survey: SurveyResult, path: str | os.PathLike, lang: str = "bi", signoff=None) -> Path:
    """Write one report file; the format comes from the extension (.pdf .docx .html .json .csv).

    signoff (compose.Signoff, from the review app) adds the review record and draft reasons to PDF/Word/HTML.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in FORMATS:
        raise ValueError(f"unsupported report format {ext!r}; use one of {FORMATS}")
    if ext in (".cnf", ".pobj", ".lobj"):
        from gpr_analyzer.report.exports import zones_condor
        path.write_text(zones_condor(survey)[ext[1:]], encoding="utf-8", newline="")
    elif ext == ".json":
        from gpr_analyzer.report.exports import survey_json
        survey_json(survey, path)
    elif ext == ".csv":
        from gpr_analyzer.report.exports import zones_csv
        zones_csv(survey, path)
    else:
        report = compose(survey, lang=lang, signoff=signoff)
        if ext == ".pdf":
            from gpr_analyzer.report.pdf import render_pdf
            render_pdf(report, path)
        elif ext == ".docx":
            from gpr_analyzer.report.docx_writer import render_docx
            render_docx(report, path)
        else:
            from gpr_analyzer.report.html import render_html
            render_html(report, path)
    return path
