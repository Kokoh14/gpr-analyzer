"""HTTP API and static page for the local review app (FastAPI).

Everything runs on the user's computer; uploads never leave it. The page in static/ talks to
these routes only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from gpr_analyzer.app import folders, geo, scene3d
from gpr_analyzer.app.files import safe_name
from gpr_analyzer.app.compare import TEMPLATE_CSV
from gpr_analyzer.app.jobs import IMAGE_TYPES, VIEWS, Job, JobStore

STATIC = Path(__file__).parent / "static"
MEDIA = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8",
         ".cnf": "text/plain; charset=utf-8", ".pobj": "text/plain; charset=utf-8",
         ".lobj": "text/plain; charset=utf-8", ".zip": "application/zip",
         ".json": "application/json", ".sgy": "application/octet-stream",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def create_app(workdir: Path, background: bool = True) -> FastAPI:
    """background=False processes uploads inside the request (tests)."""
    store = JobStore(workdir)
    app = FastAPI(title="GPR Analyzer", docs_url=None, redoc_url=None)
    app.state.store = store
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def job(job_id: str) -> Job:
        try:
            return store.get(job_id)
        except KeyError:
            raise HTTPException(404, "job not found (the app keeps jobs only while it is running)") from None

    def guarded(fn, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"not found: {e}") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        return store.summaries()

    @app.post("/api/jobs")
    def create_job(files: list[UploadFile] = File(...)) -> dict:
        j = store.new()
        paths = []
        for f in files:
            p = j.upload_dir / safe_name(f.filename or "upload.bin")
            with p.open("wb") as out:
                while chunk := f.file.read(1 << 20):
                    out.write(chunk)
            paths.append(p)
        return begin(j, paths)

    def begin(j: Job, paths: list[Path], ranges: dict | None = None, include: set[str] | None = None) -> dict:
        with j.lock:
            j.plan_files(paths, ranges, include)
            j.save()
        if background:
            j.start()
        else:
            j.run_plan()
        with j.lock:
            return j.to_json()

    # ---------------------------------------------------------- data folder on this computer
    @app.post("/api/pick-folder")
    def pick_folder(body: dict = Body(default={})) -> dict:
        try:
            return {"path": folders.pick_folder(start=str(body.get("start") or ""))}
        except RuntimeError as e:
            raise HTTPException(501, str(e)) from None

    @app.get("/api/fs")
    def list_dir(path: str = "") -> dict:
        return guarded(folders.list_dir, path)

    @app.post("/api/scan")
    def scan(body: dict = Body(...)) -> dict:
        return guarded(folders.scan_folder, str(body.get("path", "")), bool(body.get("recursive")))

    @app.post("/api/jobs/from-folder")
    def from_folder(body: dict = Body(...)) -> dict:
        path = str(body.get("path", ""))
        files = guarded(folders.folder_files, path, bool(body.get("recursive")))
        include = set(body["include"]) if body.get("include") is not None else None
        j = store.new()
        j.source_folder = str(Path(path).expanduser())
        if body.get("info"):
            with j.lock:
                guarded(j.set_info, body["info"])
        return begin(j, files, body.get("ranges") or {}, include)

    @app.get("/api/jobs/{job_id}/progress")
    def progress(job_id: str) -> dict:
        j = job(job_id)
        with j.lock:
            return {**j.progress_json(), "n_lines": len(j.lines)}

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str) -> dict:
        j = job(job_id)
        with j.lock:
            if background:
                j.resume()
            else:
                j.resume_now()
            return j.to_json()

    @app.get("/api/jobs/{job_id}/log.txt")
    def log_txt(job_id: str) -> Response:
        p = job(job_id).folder / "log.txt"
        return Response(p.read_text(encoding="utf-8") if p.exists() else "", media_type="text/plain; charset=utf-8")

    @app.post("/api/demo")
    def demo() -> dict:
        from gpr_analyzer.app.demo import write_demo_files

        j = store.new()
        with j.lock:
            j.set_info({"project": "Demo (synthetic data)", "site": "-", "surface_use": "road"})
        return begin(j, write_demo_files(j.upload_dir))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return job(job_id).to_json()

    @app.post("/api/demo3d")
    def demo3d() -> dict:
        from gpr_analyzer.app.demo import write_fence_demo_files

        j = store.new()
        with j.lock:
            j.set_info({"project": "3D fence demo (SYNTHETIC)", "site": "Simulated grid; not a field survey"})
        return begin(j, write_fence_demo_files(j.upload_dir))

    @app.get("/api/jobs/{job_id}/scene3d")
    def get_scene3d(job_id: str, frame: str = "gps", track: str = "grey") -> dict:
        j = job(job_id)
        with j.lock:
            frozen = scene3d.snapshot(j)
        return guarded(scene3d.survey_scene, frozen, frame, track)

    @app.put("/api/jobs/{job_id}/scene3d/layout")
    def set_scene3d_layout(job_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            return guarded(scene3d.save_layout, j, body)

    @app.post("/api/jobs/{job_id}/thick-slice")
    def thick_slice(job_id: str, body: dict = Body(...)) -> dict:
        from gpr_analyzer.app.thick_slice import render

        j = job(job_id)
        with j.lock:
            frozen = scene3d.snapshot(j)
        return guarded(render, frozen, body.get("top_m"), body.get("bottom_m"),
                       body.get("contrast_db", 40), body.get("frame", "gps"))

    @app.post("/api/jobs/{job_id}/info")
    def set_info(job_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.set_info, body)
        return j.to_json()

    @app.get("/api/jobs/{job_id}/lines/{line_id}/{view}.png")
    def view_png(job_id: str, line_id: str, view: str) -> Response:
        if view not in VIEWS:
            raise HTTPException(404, "unknown view")
        png = guarded(lambda: job(job_id).line(line_id).png(view))
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs/{job_id}/lines/{line_id}/map.json")
    def line_map(job_id: str, line_id: str) -> dict:
        j = job(job_id)
        with j.lock:
            return guarded(lambda: geo.line_map(j.line(line_id)))

    @app.get("/api/jobs/{job_id}/map.json")
    def job_map(job_id: str) -> list[dict]:
        j = job(job_id)
        with j.lock:
            return geo.job_overview(j.lines)

    @app.get("/api/jobs/{job_id}/dielectric.json")
    def job_dielectric(job_id: str) -> list[dict]:
        j = job(job_id)
        with j.lock:
            return geo.dielectric_points(j.lines)

    @app.post("/api/jobs/{job_id}/lines/{line_id}/zones/{zone_id}/review")
    def review(job_id: str, line_id: str, zone_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.review, line_id, zone_id, body.get("action", ""), body.get("changes"), body.get("note"))
        return j.to_json()

    @app.post("/api/jobs/{job_id}/lines/{line_id}/zones")
    def add_zone(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.add_zone, line_id, body)
        return j.to_json()

    @app.delete("/api/jobs/{job_id}/lines/{line_id}/zones/{zone_id}")
    def delete_zone(job_id: str, line_id: str, zone_id: str) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.delete_zone, line_id, zone_id)
        return j.to_json()

    @app.post("/api/jobs/{job_id}/lines/{line_id}/sections")
    def add_section(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.add_section, line_id, body)
        return j.to_json()

    @app.delete("/api/jobs/{job_id}/lines/{line_id}/sections/{section_id}")
    def delete_section(job_id: str, line_id: str, section_id: str) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.delete_section, line_id, section_id)
        return j.to_json()

    @app.post("/api/jobs/{job_id}/lines/{line_id}/status")
    def line_status(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.set_line_status, line_id, str(body.get("status", "")), body.get("note"))
        return j.to_json()

    @app.get("/api/jobs/{job_id}/summary")
    def summary(job_id: str) -> dict:
        j = job(job_id)
        with j.lock:
            return j.summary()

    @app.post("/api/jobs/{job_id}/lines/{line_id}/reprocess")
    def reprocess(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        j = job(job_id)
        with j.lock:
            guarded(j.reprocess, line_id, body.get("options", {}))
        return j.to_json()

    # ---------------------------------------------------------- comparison with reference software
    @app.get("/api/jobs/{job_id}/lines/{line_id}/workflow.json")
    def workflow_export(job_id: str, line_id: str) -> Response:
        import json
        from gpr_analyzer.processing.workflows import export_workflow

        j = job(job_id)
        with j.lock:
            ln = guarded(j.line, line_id)
            document = guarded(export_workflow, ln.result, ln.options)
        return Response(json.dumps(document, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{line_id}-workflow.json"'})

    @app.post("/api/workflows/validate")
    def workflow_validate(body: dict = Body(...)) -> dict:
        from gpr_analyzer.processing.workflows import load_workflow
        from dataclasses import asdict

        opt, tracks = guarded(load_workflow, body)
        return {"options": asdict(opt), "tracks": sorted(tracks)}

    @app.post("/api/jobs/{job_id}/lines/{line_id}/workflows/compare")
    def workflow_compare(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        from gpr_analyzer.app.workflows import compare_workflows

        j = job(job_id)
        with j.lock:
            raw = guarded(j.line, line_id).raw
        return guarded(compare_workflows, raw, body.get("left"), body.get("right"))

    def ref_route(job_id: str, fn, *a: Any) -> dict:
        j = job(job_id)
        with j.lock:
            extra = guarded(fn, j, *a)
        out = j.to_json()
        if isinstance(extra, dict) and "added" in extra:
            out["import_result"] = extra
        return out

    @app.post("/api/jobs/{job_id}/lines/{line_id}/ref/picks")
    def add_pick(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        return ref_route(job_id, Job.add_ref_pick, line_id, body)

    @app.put("/api/jobs/{job_id}/lines/{line_id}/ref/picks/{ref_id}")
    def edit_pick(job_id: str, line_id: str, ref_id: str, body: dict = Body(...)) -> dict:
        return ref_route(job_id, Job.update_ref_pick, line_id, ref_id, body)

    @app.delete("/api/jobs/{job_id}/lines/{line_id}/ref/picks/{ref_id}")
    def delete_pick(job_id: str, line_id: str, ref_id: str) -> dict:
        return ref_route(job_id, Job.delete_ref_pick, line_id, ref_id)

    @app.post("/api/jobs/{job_id}/lines/{line_id}/ref/import")
    def import_picks(job_id: str, line_id: str, file: UploadFile = File(...), source: str = Form("other"),
                     replace: bool = Form(False)) -> dict:
        return ref_route(job_id, Job.import_ref_csv, line_id, file.file.read(), source, replace)

    @app.post("/api/jobs/{job_id}/lines/{line_id}/ref/meta/{source}")
    def ref_meta(job_id: str, line_id: str, source: str, body: dict = Body(...)) -> dict:
        return ref_route(job_id, Job.set_ref_meta, line_id, source, body)

    @app.post("/api/jobs/{job_id}/lines/{line_id}/ref/verdict")
    def verdict(job_id: str, line_id: str, body: dict = Body(...)) -> dict:
        return ref_route(job_id, Job.set_verdict, line_id, str(body.get("key", "")), body.get("verdict"),
                         body.get("note"))

    @app.post("/api/jobs/{job_id}/lines/{line_id}/ref/images")
    def add_image(job_id: str, line_id: str, files: list[UploadFile] = File(...), source: str = Form("other")) -> dict:
        j = job(job_id)
        with j.lock:
            for f in files:
                guarded(j.add_ref_image, line_id, source, f.filename or "image.png", f.file.read())
        return j.to_json()

    @app.get("/api/jobs/{job_id}/lines/{line_id}/ref/images/{fname}")
    def get_image(job_id: str, line_id: str, fname: str) -> FileResponse:
        p = guarded(job(job_id).ref_image_path, line_id, fname)
        return FileResponse(p, media_type=IMAGE_TYPES.get(p.suffix.lower(), "application/octet-stream"))

    @app.delete("/api/jobs/{job_id}/lines/{line_id}/ref/images/{fname}")
    def delete_image(job_id: str, line_id: str, fname: str) -> dict:
        return ref_route(job_id, Job.delete_ref_image, line_id, fname)

    @app.post("/api/jobs/{job_id}/compare/settings")
    def compare_settings(job_id: str, body: dict = Body(...)) -> dict:
        return ref_route(job_id, Job.set_compare_settings, body)

    @app.get("/api/compare/template.csv")
    def template() -> Response:
        return Response("\ufeff" + TEMPLATE_CSV, media_type=MEDIA[".csv"],
                        headers={"Content-Disposition": 'attachment; filename="reference_picks_template.csv"'})

    @app.get("/api/compare/export/{name}")
    def export_all(name: str) -> Response:
        text = guarded(store.export_all, name)
        return Response(text, media_type=MEDIA[".csv"], headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/jobs/{job_id}/export/{name}")
    def export(job_id: str, name: str) -> FileResponse:
        j = job(job_id)
        with j.lock:
            path = guarded(j.export_comparison if name.startswith(("comparison", "features")) else j.export, name)
        return FileResponse(path, media_type=MEDIA[path.suffix], filename=f"{j.job_id}_{path.name}")

    @app.get("/api/jobs/{job_id}/lines/{line_id}/export/{fmt}")
    def export_line(job_id: str, line_id: str, fmt: str) -> FileResponse:
        j = job(job_id)
        with j.lock:
            path = guarded(j.export_line, line_id, fmt)
        return FileResponse(path, media_type=MEDIA[path.suffix], filename=path.name)

    @app.get("/api/jobs/{job_id}/labels.jsonl")
    def labels(job_id: str) -> Response:
        p = job(job_id).labels_path
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        return Response(text, media_type="application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="{job_id}_labels.jsonl"'})

    return app
