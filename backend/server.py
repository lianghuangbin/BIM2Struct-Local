from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import copy
import json
import re
import tempfile
import threading
import traceback
import uuid

from process_once import process_ifc_counts, process_ifc_seismic


HOST = "127.0.0.1"
PORT = 8000
SEISMIC_JOBS = {}
SEISMIC_JOBS_LOCK = threading.Lock()


ENTITY_IDS = {
    "IFCBUILDINGSTOREY": "storeys",
    "IFCGRID": "grids",
    "IFCCOLUMN": "columns",
    "IFCBEAM": "beams",
    "IFCSLAB": "slabs",
    "IFCPROPERTYSET": "property_sets",
    "IFCMATERIAL": "materials",
}


def count_entity(text, entity):
    return len(re.findall(r"=\s*" + re.escape(entity) + r"\s*\(", text, flags=re.IGNORECASE))


def count_analysis_levels(text):
    matches = re.findall(
        r"IFCBUILDINGSTOREY\s*\([^;]*?\.ELEMENT\.\s*,\s*([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    levels = []
    for value in matches:
        try:
            z = float(value)
        except ValueError:
            continue
        if z >= -1e-6:
            levels.append(z)
    return len(levels)


def parse_ifc_raw(text):
    raw = {label: count_entity(text, entity) for entity, label in ENTITY_IDS.items()}
    raw["walls"] = count_entity(text, "IFCWALL") + count_entity(text, "IFCWALLSTANDARDCASE")
    raw["analysis_levels"] = count_analysis_levels(text)

    schema_match = re.search(r"FILE_SCHEMA\s*\(\('([^']+)'", text, flags=re.IGNORECASE)
    raw["schema"] = schema_match.group(1) if schema_match else "IFC"
    return raw


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        if self.path == "/api/status":
            self._send_json(200, {"status": "running", "backend": "BIM2Struct", "python": "BIMFEMenv"})
            return
        job_match = re.fullmatch(r"/api/seismic/jobs/([0-9a-fA-F-]+)", self.path)
        if job_match:
            self._send_seismic_job(job_match.group(1))
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/ifc/seismic/jobs":
            self._create_seismic_job()
            return

        if self.path == "/api/ifc/seismic":
            self._handle_seismic()
            return

        if self.path != "/api/ifc/process":
            self._send_json(404, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body)
            filename = payload.get("filename", "uploaded.ifc")
            text = payload["text"]
            gravity_loads = payload.get("gravity", {})
            model_options = payload.get("model_options", {})
        except Exception as exc:
            self._send_json(400, {"error": f"Invalid JSON payload: {exc}"})
            return

        raw = parse_ifc_raw(text)

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                safe_name = Path(filename).name or "uploaded.ifc"
                ifc_path = Path(tmp_dir) / safe_name
                ifc_path.write_text(text, encoding="utf-8", errors="replace")
                processed = process_ifc_counts(
                    ifc_path,
                    run_modal=True,
                    num_modes=6,
                    run_gravity=True,
                    gravity_loads=gravity_loads,
                    model_options=model_options,
                )

            self._send_json(200, {
                "filename": filename,
                "raw": raw,
                "processed": processed,
                "message": "BIM2Struct processing completed",
            })
        except Exception as exc:
            self._send_json(500, {
                "filename": filename,
                "raw": raw,
                "processed": None,
                "message": f"BIM2Struct processing failed: {exc}",
                "traceback": traceback.format_exc(),
            })

    def _send_seismic_job(self, job_id):
        with SEISMIC_JOBS_LOCK:
            job = copy.deepcopy(SEISMIC_JOBS.get(job_id))
        if job is None:
            self._send_json(404, {"error": "Job not found"})
            return
        self._send_json(200, job)

    def _create_seismic_job(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body)
            filename = payload.get("filename", "uploaded.ifc")
            payload["text"]
            payload["ground_motion"]["text"]
        except Exception as exc:
            self._send_json(400, {"error": f"Invalid JSON payload: {exc}"})
            return

        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued seismic response analysis",
            "filename": filename,
            "seismic": None,
            "error": None,
        }
        with SEISMIC_JOBS_LOCK:
            SEISMIC_JOBS[job_id] = job

        worker = threading.Thread(
            target=run_seismic_job,
            args=(job_id, payload),
            daemon=True,
        )
        worker.start()

        self._send_json(202, job)

    def _handle_seismic(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body)
            filename = payload.get("filename", "uploaded.ifc")
            text = payload["text"]
            ground_motion = payload["ground_motion"]
            gm_filename = ground_motion.get("filename", "ground_motion.txt")
            gm_text = ground_motion["text"]
            seismic = payload.get("seismic", {})
            gravity_loads = payload.get("gravity", {})
            model_options = payload.get("model_options", {})
        except Exception as exc:
            self._send_json(400, {"error": f"Invalid JSON payload: {exc}"})
            return

        raw = parse_ifc_raw(text)

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                safe_ifc_name = Path(filename).name or "uploaded.ifc"
                safe_gm_name = Path(gm_filename).name or "ground_motion.txt"
                ifc_path = tmp_path / safe_ifc_name
                gm_path = tmp_path / safe_gm_name
                ifc_path.write_text(text, encoding="utf-8", errors="replace")
                gm_path.write_text(gm_text, encoding="utf-8", errors="replace")
                result = process_ifc_seismic(
                    ifc_path=ifc_path,
                    gm_path=gm_path,
                    seismic=seismic,
                    gravity_loads=gravity_loads,
                    model_options=model_options,
                    output_prefix=tmp_path / "seismic_response",
                )

            self._send_json(200, {
                "filename": filename,
                "raw": raw,
                "seismic": result,
                "message": "Seismic response analysis completed",
            })
        except Exception as exc:
            self._send_json(500, {
                "filename": filename,
                "raw": raw,
                "seismic": None,
                "message": f"Seismic response analysis failed: {exc}",
                "traceback": traceback.format_exc(),
            })


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"BIM2Struct backend running at http://{HOST}:{PORT}")
    print("Endpoints: GET /api/status, POST /api/ifc/process, POST /api/ifc/seismic, POST /api/ifc/seismic/jobs")
    server.serve_forever()


def update_seismic_job(job_id, **updates):
    with SEISMIC_JOBS_LOCK:
        if job_id in SEISMIC_JOBS:
            SEISMIC_JOBS[job_id].update(updates)


def run_seismic_job(job_id, payload):
    try:
        update_seismic_job(job_id, status="running", progress=5, message="Preparing uploaded files")
        filename = payload.get("filename", "uploaded.ifc")
        text = payload["text"]
        ground_motion = payload["ground_motion"]
        gm_filename = ground_motion.get("filename", "ground_motion.txt")
        gm_text = ground_motion["text"]
        seismic = payload.get("seismic", {})
        gravity_loads = payload.get("gravity", {})
        model_options = payload.get("model_options", {})

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            safe_ifc_name = Path(filename).name or "uploaded.ifc"
            safe_gm_name = Path(gm_filename).name or "ground_motion.txt"
            ifc_path = tmp_path / safe_ifc_name
            gm_path = tmp_path / safe_gm_name
            ifc_path.write_text(text, encoding="utf-8", errors="replace")
            gm_path.write_text(gm_text, encoding="utf-8", errors="replace")

            update_seismic_job(job_id, progress=20, message="Building OpenSees model and modal damping")
            result = process_ifc_seismic(
                ifc_path=ifc_path,
                gm_path=gm_path,
                seismic=seismic,
                gravity_loads=gravity_loads,
                model_options=model_options,
                output_prefix=tmp_path / "seismic_response",
            )

        update_seismic_job(
            job_id,
            status="completed",
            progress=100,
            message="Seismic response analysis completed",
            seismic=result,
        )
    except Exception as exc:
        update_seismic_job(
            job_id,
            status="failed",
            progress=100,
            message=f"Seismic response analysis failed: {exc}",
            error={"message": str(exc), "traceback": traceback.format_exc()},
        )


if __name__ == "__main__":
    main()
