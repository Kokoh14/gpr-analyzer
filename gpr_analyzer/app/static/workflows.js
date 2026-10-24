"use strict";
const workflowSlots = {A: null, B: null};
for (const side of ["A", "B"]) {
  $(`#workflowLoad${side}`).addEventListener("click", () => $(`#workflowFile${side}`).click());
  $(`#workflowFile${side}`).addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      if (file.size > 1000000) throw new Error("workflow เกิน 1 MB");
      const document = JSON.parse(await file.text());
      await post("/api/workflows/validate", document);
      workflowSlots[side] = document;
      $(`#workflowName${side}`).textContent = file.name;
      $("#workflowViews").hidden = true;
    } catch (error) { toast(error.message); }
    e.target.value = "";
  });
  $(`#workflowApply${side}`).addEventListener("click", async () => {
    try {
      const document = workflowSlots[side];
      if (!document) throw new Error(`โหลดไฟล์ workflow ${side} ก่อน`);
      const validated = await post("/api/workflows/validate", document);
      await reprocess(validated.options);
    } catch (error) { busy(false); toast(error.message); }
  });
}
$("#workflowCompare").addEventListener("click", async () => {
  const job = S.job, lid = S.line, button = $("#workflowCompare");
  if (!job || !lid) return;
  button.disabled = true;
  $("#workflowViews").hidden = true;
  $("#workflowStatus").textContent = "กำลังรัน A/B จากข้อมูลดิบเส้นเดียวกัน…";
  try {
    const a = workflowSlots.A || await api(`/api/jobs/${job.job_id}/lines/${lid}/workflow.json`);
    if (!workflowSlots.B) throw new Error("โหลด workflow B เพื่อเทียบกับ A (ค่าเริ่มต้น A = ค่าปัจจุบันของเส้น)");
    const result = await post(`/api/jobs/${job.job_id}/lines/${lid}/workflows/compare`, {left:a, right:workflowSlots.B});
    if (S.job !== job || S.line !== lid) return;
    for (const [side, value] of [["A", result.left], ["B", result.right]]) {
      $(`#workflowImage${side}`).src = value.png;
      $(`#workflowLog${side}`).textContent = JSON.stringify(value.parameters, null, 2);
    }
    $("#workflowViews").hidden = false;
    $("#workflowStatus").textContent = `A/B · ${lid} · amplitude scale ร่วม ±${result.shared_amplitude_clip.toPrecision(4)} · ผลเดิมและผลตรวจทานยังอยู่`;
  } catch (error) { $("#workflowStatus").textContent = error.message; }
  finally { button.disabled = false; }
});
window.addEventListener("gpr:job", () => {
  $("#workflowExport").href = `/api/jobs/${S.job.job_id}/lines/${S.line}/workflow.json`;
  $("#workflowViews").hidden = true;
  $("#workflowStatus").textContent = "A = ค่าปัจจุบันของเส้น หรือโหลดไฟล์ A; เลือกไฟล์ B แล้วกดเทียบ";
});
