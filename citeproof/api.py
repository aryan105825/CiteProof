# @exports: app: fastapi.FastAPI  (run: uvicorn citeproof.api:app --port 8000)
# @imports: __init__.py:__version__
# @imports: config.py:load_config, load_thresholds
# @imports: extract.py:read_input, extract_references
# @imports: httpclient.py:HttpError, get_json
# @imports: pipeline.py:MODES, report_dict, verify_compare, verify_many
# @env: CITEPROOF_MAILTO
# @env: CITEPROOF_RATE
# @env: CITEPROOF_TIMEOUT
# @env: CITEPROOF_CACHE
# @env: CITEPROOF_OPENALEX
# @schema: GET / -> web/index.html
# @schema: GET /api/health -> {"ok": true, "version": str, "crossref_reachable": bool}
# @schema: POST /api/check JSON {"text": str | "refs": [str], "mode": "weak"|"strong"|"evidence", "compare": bool} -> report_dict, or with compare=true {"weak","strong","evidence"} report_dicts
# @schema: POST /api/check-file multipart file (.pdf|.txt|.md), mode, compare -> same payload plus "extracted": int
# @schema: errors {"detail": str}: 400 bad input, 413 more than 60 refs or file over 10 MB, 502 upstream failure; server always runs with the HTTP cache off
import os
import sys
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from citeproof import __version__
from citeproof.config import load_config, load_thresholds
from citeproof.extract import extract_references, read_input
from citeproof.httpclient import HttpError, get_json
from citeproof.pipeline import MODES, report_dict, verify_compare, verify_many

MAX_REFS = 60
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
WORKERS = 3
_INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "index.html")

_CFG = load_config(use_cache=False)
_TH, _CALIBRATED = load_thresholds()
if not _CALIBRATED:
    print("WARN: results/calibration.json not found; serving with default thresholds (uncalibrated).", file=sys.stderr)

app = FastAPI(title="CiteProof", version=__version__)


def _run_check(refs: list[str], mode: str, compare: bool) -> dict:
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(MODES)}, got {mode!r}.")
    if not refs:
        raise HTTPException(status_code=400, detail="No references found. Paste at least one reference of 25+ characters.")
    if len(refs) > MAX_REFS:
        raise HTTPException(status_code=413, detail=f"Found {len(refs)} references; the limit is {MAX_REFS}. Split the input.")
    try:
        if compare:
            return verify_compare(refs, _CFG, _TH, WORKERS)
        return report_dict(verify_many(refs, _CFG, _TH, mode, WORKERS), mode)
    except HttpError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _check_upload(data: bytes, ext: str, mode: str, compare: bool) -> dict:
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            text = read_input(path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        os.remove(path)
    refs = extract_references(text)
    result = _run_check(refs, mode, compare)
    result["extracted"] = len(refs)
    return result


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_INDEX)


@app.get("/api/health")
def health() -> dict:
    try:
        get_json(f"{_CFG.crossref_base}/works", {"rows": "0", "mailto": _CFG.mailto}, _CFG)
        reachable = True
    except HttpError:
        reachable = False
    return {"ok": True, "version": __version__, "crossref_reachable": reachable}


@app.post("/api/check")
async def check(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from e
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail='Request body must be a JSON object with "text" or "refs".')
    mode = body.get("mode", "evidence")
    compare = body.get("compare", False)
    if not isinstance(compare, bool):
        raise HTTPException(status_code=400, detail='"compare" must be true or false.')
    text, refs = body.get("text"), body.get("refs")
    if (text is None) == (refs is None):
        raise HTTPException(status_code=400, detail='Provide exactly one of "text" or "refs".')
    if text is not None:
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail='"text" must be a string.')
        refs = extract_references(text)
    else:
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise HTTPException(status_code=400, detail='"refs" must be a list of strings.')
        refs = [r.strip() for r in refs if r.strip()]
    return await run_in_threadpool(_run_check, refs, mode, compare)


@app.post("/api/check-file")
async def check_file(
    file: UploadFile = File(...),
    mode: str = Form("evidence"),
    compare: bool = Form(False),
) -> dict:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".pdf", ".txt", ".md"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type {ext or '(none)'!r}. Upload a .pdf, .txt or .md file.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 10 MB. Upload a smaller file.")
    return await run_in_threadpool(_check_upload, data, ext, mode, compare)
