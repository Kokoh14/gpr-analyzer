import io
import re

import pytest

from gpr_analyzer.report import compose, write_report
from gpr_analyzer.report.compose import Figure, Heading, Table
from gpr_analyzer.report.demo import mock_survey
from gpr_analyzer.report.rules import find_wording_violations


@pytest.fixture(scope="module")
def survey():
    return mock_survey()


@pytest.fixture(scope="module")
def survey_nofig():
    return mock_survey(with_radargram=False)


@pytest.mark.parametrize("lang", ["th", "en", "bi"])
def test_all_sections_present_and_wording_clean(survey_nofig, lang):
    r = compose(survey_nofig, lang=lang, figures=False)
    heads = [b.text for b in r.blocks if isinstance(b, Heading) and b.level == 1]
    assert len(heads) == 7  # summary, job, processing, lines, zones, limitations, recommendations
    assert find_wording_violations(r.all_text()) == []


def test_english_and_thai_text(survey_nofig):
    en = compose(survey_nofig, lang="en", figures=False).all_text()
    th = compose(survey_nofig, lang="th", figures=False).all_text()
    bi = compose(survey_nofig, lang="bi", figures=False).all_text()
    assert "Summary" in en and "สรุปผล" not in en
    assert "สรุปผล" in th and "Summary" not in th
    assert "สรุปผล / 1. Summary" in bi


def test_zone_table_has_two_axes_and_depth_ranges(survey_nofig):
    r = compose(survey_nofig, lang="en", figures=False)
    table = next(b for b in r.blocks if isinstance(b, Table) and b.header and b.header[0] == "Zone")
    assert "Confidence" in table.header and "Action" in table.header
    rows = {row[0]: row for row in table.rows}
    assert set(rows) == {"A", "B", "C", "D", "E"}
    assert re.fullmatch(r"\d+\.\d–\d+\.\d", rows["A"][4])  # depth range, not a single number
    # the most urgent zone is listed first
    assert table.rows[0][0] == "A" and table.row_tags[0] == "2"


def test_recommendations_cite_zone_ids_and_summary_counts(survey_nofig):
    r = compose(survey_nofig, lang="en", figures=False)
    text = r.all_text()
    assert "Survey cross lines through zone(s) A" in text
    assert "Re-survey zone(s) E" in text
    assert "4 anomaly zone(s) found" in text  # artifact zone D not counted as an anomaly
    assert r.draft  # machine-proposed zones -> draft
    assert not compose(mock_survey(with_radargram=False, reviewed=True), "en", figures=False).draft


def test_default_velocity_is_called_out(survey_nofig):
    text = compose(survey_nofig, lang="en", figures=False).all_text()
    assert "default velocity that was not measured" in text


def test_colour_figure_caption_warns_red_is_not_a_void(survey):
    r = compose(survey, lang="bi")
    figs = [b for b in r.blocks if isinstance(b, Figure)]
    assert len(figs) == 4 and all(f.png.startswith(b"\x89PNG") for f in figs)
    colour = [f for f in figs if "envelope" in " ".join(f.caption)]
    assert colour and all("not a void" in " ".join(f.caption) for f in colour)


def test_pdf_embeds_thai_font_and_text(survey, tmp_path):
    out = write_report(survey, tmp_path / "r.pdf", lang="bi")
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert b"Sarabun" in data
    pypdf = pytest.importorskip("pypdf")
    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(data)).pages)
    assert "รายงาน" in text and "Summary" in text


def test_docx_has_thai_font_and_zone_table(survey, tmp_path):
    docx = pytest.importorskip("docx")
    out = write_report(survey, tmp_path / "r.docx", lang="th")
    d = docx.Document(str(out))
    body = "\n".join(p.text for p in d.paragraphs)
    assert "สรุปผล" in body
    cells = {c.text for t in d.tables for row in t.rows for c in row.cells}
    assert "โซนคล้ายโพรง" in cells
    assert len(d.inline_shapes) == 4
    xml = d.styles["Normal"].element.xml
    assert 'w:cs="Leelawadee UI"' in xml


def test_html_csv_json_outputs(survey, tmp_path):
    html = write_report(survey, tmp_path / "r.html", lang="en").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "data:image/png;base64," in html
    csv_text = write_report(survey, tmp_path / "r.csv").read_text(encoding="utf-8-sig")
    assert csv_text.splitlines()[0].startswith("zone_id,line_id")
    assert len(csv_text.strip().splitlines()) == 6
    assert '"schema_version"' in write_report(survey, tmp_path / "r.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_report(survey, tmp_path / "r.txt")


def test_geojson_only_zones_with_coordinates(survey):
    from gpr_analyzer.report.exports import zones_geojson

    fc = zones_geojson(survey)
    assert [f["properties"]["zone_id"] for f in fc["features"]] == ["A"]
    assert fc["features"][0]["geometry"]["type"] == "LineString"
