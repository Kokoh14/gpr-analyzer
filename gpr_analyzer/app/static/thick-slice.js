"use strict";
// Uses the review page's local API and job context.
$("#thickRender").addEventListener("click", async () => {
  const button = $("#thickRender"), context = S.job;
  if (!context) return;
  button.disabled = true;
  $("#thickImage").hidden = true;
  $("#thickStatus").textContent = "กำลังสร้างภาพ…";
  try {
    const top = +$("#thickTop").value, bottom = +$("#thickBottom").value;
    const result = await post(`/api/jobs/${context.job_id}/thick-slice`, {
      top_m: top, bottom_m: bottom, contrast_db: +$("#thickContrast").value, frame: $("#thickFrame").value,
    });
    if (S.job !== context) return;
    $("#thickImage").src = result.png;
    $("#thickImage").hidden = false;
    $("#thickLegend").innerHTML = `<span class="legend">${top.toFixed(2)} m (ตื้น)
      <span class="bar" style="background:linear-gradient(90deg,#ffff00,#00ff00,#0000ff)"></span>${bottom.toFixed(2)} m (ลึก)</span>`;
    $("#thickStatus").textContent = `${result.cells} cells · ${result.cell_m} m/cell · E/N bounds ${result.bounds_m.map(x => x.toFixed(1)).join(", ")} m · เว้น ${result.excluded.length} แนว`;
  } catch (e) { $("#thickStatus").textContent = e.message; }
  finally { button.disabled = false; }
});
let thickRevision = "";
window.addEventListener("gpr:job", () => {
  const revision = JSON.stringify([S.job?.job_id, S.job?.lines.map(l => [l.line_id, l.revision, l.sections, l.options])]);
  if (revision !== thickRevision) {
    $("#thickImage").hidden = true;
    $("#thickLegend").textContent = "";
    $("#thickStatus").textContent = "เลือกช่วงความลึก แล้วกดสร้างภาพ";
    thickRevision = revision;
  }
});
