from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

import csv
import io
import json
import os
import shutil
import time
import traceback
import uuid
import zipfile

from typing import Any, Dict, List, Optional, Union

from wordcloud import WordCloud
from service_registration import register_service_registration_endpoints
import md_storage


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Disk mode when MD_PATH has a data/ directory, HTTP mode otherwise (see md_storage.py). Without
# MD_PATH the service used to refuse to start. MD_ROOT is None in HTTP mode.
MD_ROOT = str(md_storage.MD_ROOT) if md_storage.DISK_MODE else None


def is_disk_mode() -> bool:
    return MD_ROOT is not None


OUTPUT_FOLDER = os.path.abspath(os.getenv("OUTPUT_FOLDER", os.path.join(BASE_DIR, "output")))
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
# HTTP outputs nobody downloaded and unfinished many-to-one files are removed after this
OUTPUT_MAX_AGE_SECONDS = int(os.getenv("OUTPUT_MAX_AGE_SECONDS", str(24 * 3600)))
STOPWORDS_DIR = os.path.join(BASE_DIR, "stopwords")


app = FastAPI(
    title="text base API",
    description="API for text base tasks",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> Dict[str, str]:
    return {"message": "text base API for MessyDesk"}


register_service_registration_endpoints(
    app,
    BASE_DIR,
    service_id="md-text-base_fs",
    # elg_fs in disk mode, elg in HTTP mode, unless SERVICE_ADAPTER says otherwise
    adapter=os.getenv("SERVICE_ADAPTER") or md_storage.storage_adapter(),
)


def resolve_md_relative_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise HTTPException(status_code=400, detail="Invalid file.path")

    if not is_disk_mode():
        raise HTTPException(
            status_code=400,
            detail="Missing content file, and disk mode is off (MD_PATH not found).",
        )

    if os.path.isabs(relative_path):
        raise HTTPException(status_code=400, detail="file.path must be relative to MD_PATH")

    md_root = os.path.abspath(MD_ROOT)
    resolved = os.path.abspath(os.path.join(md_root, relative_path))
    if resolved != md_root and not resolved.startswith(md_root + os.sep):
        raise HTTPException(status_code=400, detail="file.path is outside MD_PATH")

    return resolved


def infer_file_kind(msg: Dict[str, Any], expect_uploaded_set_zip: bool = False) -> str:
    if expect_uploaded_set_zip and msg.get("input_set"):
        return "zip"

    file_type = str(msg.get("file", {}).get("type", "")).lower()
    extension = str(msg.get("file", {}).get("extension", "")).lower()

    if file_type in ("json", "ocr.json") or extension == "json":
        return "json"
    return "text"


async def parse_json_upload(upload: UploadFile, label: str) -> Dict[str, Any]:
    raw = await upload.read()
    return parse_request_payload(raw, label)


def parse_request_payload(raw: bytes, label: str = "request") -> Dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as err:
        raise HTTPException(status_code=400, detail=f"{label} encoding error: {err}")
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in {label}: {err}")

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as err:
            raise HTTPException(status_code=400, detail=f"Invalid nested JSON in {label}: {err}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=f"{label} must be a JSON object")

    return payload


async def extract_message(
    http_request: Request,
    request_file: Optional[UploadFile],
    message_file: Optional[UploadFile],
) -> Dict[str, Any]:
    if request_file:
        return await parse_json_upload(request_file, "request")

    if message_file:
        return await parse_json_upload(message_file, "message")

    content_type = (http_request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await http_request.body()
        return parse_request_payload(body, "request body")

    raise HTTPException(
        status_code=400,
        detail="Missing request payload. Provide multipart request/message JSON or application/json body.",
    )


async def parse_content_json_bytes(raw: bytes) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    try:
        content_text = raw.decode("utf-8")
        if is_jsonl(content_text):
            return parse_jsonl(content_text)
        return json.loads(content_text)
    except UnicodeDecodeError as err:
        raise HTTPException(status_code=400, detail=f"Content file encoding error: {err}")
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in content file: {err}")


async def get_files_zip_from_bytes(content_data: bytes, work_dir: str) -> List[str]:
    # inside the request's work dir, so it is removed with it (zip_<uuid> dirs stayed for good)
    extract_dir = os.path.join(work_dir, ".zip")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(content_data)) as zip_file:
            extracted_files = []
            for member in zip_file.infolist():
                if member.is_dir():
                    continue
                normalized = os.path.normpath(member.filename)
                if normalized.startswith("..") or os.path.isabs(normalized):
                    raise HTTPException(status_code=400, detail="Invalid zip entry path")

                target_path = os.path.join(extract_dir, normalized)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zip_file.open(member) as source, open(target_path, "wb") as dest:
                    shutil.copyfileobj(source, dest)
                extracted_files.append(target_path)

            return extracted_files
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")


async def load_input_content(
    msg: Dict[str, Any],
    content_file: Optional[UploadFile],
    work_dir: str,
) -> Union[str, Dict[str, Any], List[Any], List[str]]:
    if content_file is not None:
        file_kind = infer_file_kind(msg, expect_uploaded_set_zip=True)
        raw = await content_file.read()
        if file_kind == "zip":
            return await get_files_zip_from_bytes(raw, work_dir)
        if file_kind == "json":
            return await parse_content_json_bytes(raw)

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as err:
            raise HTTPException(status_code=400, detail=f"Content file encoding error: {err}")

    # In file-storage mode, callbacks are typically per-file and file.path points
    # directly to a real content file (not a zip archive), even for set processes.
    file_kind = infer_file_kind(msg, expect_uploaded_set_zip=False)

    source_rel_path = msg.get("file", {}).get("path")
    if not source_rel_path:
        raise HTTPException(
            status_code=400,
            detail="Missing file.path. File-storage mode requires file.path in request payload.",
        )

    source_abs_path = resolve_md_relative_path(source_rel_path)
    if not os.path.exists(source_abs_path):
        raise HTTPException(status_code=404, detail=f"Source file not found: {source_rel_path}")

    if file_kind == "json":
        with open(source_abs_path, "rb") as handle:
            return await parse_content_json_bytes(handle.read())

    with open(source_abs_path, "r", encoding="utf-8") as handle:
        return handle.read()


def infer_output_type(extension: str) -> str:
    ext = (extension or "").lower()
    if ext in {"png", "jpg", "jpeg", "gif", "bmp", "webp"}:
        return "image"
    if ext == "csv":
        return "csv"
    if ext == "json":
        return "json"
    if ext == "pdf":
        return "pdf"
    return "text"


def to_legacy_response(output_paths: List[str], work_dir: str) -> Dict[str, Any]:
    # a list of plain uris: the elg adapter keeps each file name as the label, as disk mode does
    output_id = os.path.basename(work_dir)
    uris = [f"/files/{output_id}/{os.path.basename(path)}" for path in output_paths]
    return {"response": {"type": "stored", "uri": uris}}


def get_db_tmp_dir(msg: Dict[str, Any]) -> str:
    source_path = str(msg.get("file", {}).get("path", ""))
    db_name = "messydesk"
    parts = source_path.replace("\\", "/").split("/")
    for idx, part in enumerate(parts[:-1]):
        if part == "data" and idx + 1 < len(parts) and parts[idx + 1]:
            db_name = parts[idx + 1]
            break

    tmp_dir = os.path.join(MD_ROOT, "data", db_name, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    return tmp_dir


def to_disk_response(output_paths: List[str], task_id: str, msg: Dict[str, Any]) -> Dict[str, Any]:
    tmp_dir = get_db_tmp_dir(msg)
    files: List[Dict[str, str]] = []
    for output_path in output_paths:
        safe_name = os.path.basename(output_path)
        # a unique tmp name, so parallel jobs can't overwrite each other; MessyDesk uses the label
        callback_name = f"{uuid.uuid4().hex}_{safe_name}"
        # moved, not copied: the copies stayed in output/ for good
        shutil.move(output_path, os.path.join(tmp_dir, callback_name))

        ext = os.path.splitext(safe_name)[1].lower().lstrip(".")
        files.append(
            {
                "path": callback_name,
                "label": safe_name,
                "type": infer_output_type(ext),
                "extension": ext or "txt",
            }
        )

    return {
        "task": task_id,
        "storage_mode": "disk",
        "response": {
            "type": "disk",
            "files": files,
        },
    }


def sweep_stale_outputs() -> None:
    """Remove HTTP outputs nobody downloaded and many-to-one files whose set never finished."""
    limit = time.time() - OUTPUT_MAX_AGE_SECONDS
    for folder in (OUTPUT_FOLDER, os.path.join(OUTPUT_FOLDER, "many-to-one")):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            entry = os.path.join(folder, name)
            if name == "many-to-one":
                continue
            try:
                if os.path.getmtime(entry) < limit:
                    if os.path.isdir(entry):
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        os.remove(entry)
            except OSError:
                pass


@app.post("/process")
async def process_files(
    http_request: Request,
    request: Optional[UploadFile] = File(None),
    message: Optional[UploadFile] = File(None),
    content: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    # With a content upload the request is answered in HTTP mode; without one the input is read
    # from MessyDesk's storage (disk mode). Before, STORAGE_MODE alone decided, so a disk-mode
    # service answered elg uploads with a disk response elg can't read.
    http_mode = content is not None
    work_dir = os.path.join(OUTPUT_FOLDER, uuid.uuid4().hex)
    os.makedirs(work_dir)
    served = False
    try:
        sweep_stale_outputs()
        msg = await extract_message(http_request, request, message)
        task_id = msg.get("task", {}).get("id")
        if not task_id:
            raise HTTPException(status_code=400, detail="Missing task.id in request payload")

        content_data = await load_input_content(msg, content, work_dir)
        # CPU-bound (wordcloud, large texts), so off the event loop
        output_paths = await run_in_threadpool(execute_task, task_id, content_data, msg, work_dir)
        shutil.rmtree(os.path.join(work_dir, ".zip"), ignore_errors=True)

        if not http_mode:
            return to_disk_response(output_paths, task_id, msg)

        response = to_legacy_response(output_paths, work_dir)
        response["response"]["storage_mode"] = "http"
        served = bool(output_paths)
        return response

    except HTTPException:
        raise
    except Exception as err:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Processing failed: {err}")
    finally:
        if not served:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.get("/files/{output_id}/{filename}")
def serve_file(output_id: str, filename: str, background_tasks: BackgroundTasks):
    output_dir = os.path.realpath(OUTPUT_FOLDER)
    file_path = os.path.realpath(os.path.join(output_dir, output_id, filename))
    # only files of one request dir; '../' paths read and then deleted any file
    if os.path.dirname(os.path.dirname(file_path)) != output_dir or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    def remove_file(path: str):
        try:
            os.remove(path)
            if not os.listdir(os.path.dirname(path)):
                os.rmdir(os.path.dirname(path))
        except Exception as err:
            print(f"Error deleting file {path}: {err}")

    background_tasks.add_task(remove_file, file_path)
    return FileResponse(file_path, background=background_tasks)


@app.get("/params_help/{task_id}/{param}")
async def get_params_help(task_id: str, param: str):
    if task_id == "remove_stop_words":
        if param == "language":
            return get_available_languages()
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {param}")

    raise HTTPException(status_code=404, detail=f"Not found: {task_id}")


def execute_task(
    task_id: str,
    content_data: Union[str, Dict[str, Any], List[Any], List[str]],
    msg: Dict[str, Any],
    out_dir: str,
) -> List[str]:
    if task_id == "json2csv":
        if not isinstance(content_data, (list, dict)):
            raise HTTPException(status_code=400, detail="json2csv expects JSON content")
        return json_to_csv(content_data, msg, out_dir)

    if task_id == "json2text" and msg.get("file", {}).get("type") == "ocr.json":
        return [ocr_json_to_text(content_data, out_dir)]

    if task_id == "wordcloud":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="wordcloud expects text content")
        return [create_wordcloud(content_data, out_dir)]

    if task_id == "remove_stop_words":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="remove_stop_words expects text content")
        return [remove_stop_words(content_data, msg, out_dir)]

    if task_id == "split_text":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="split_text expects text content")
        return split_text(content_data, msg, out_dir)

    if task_id == "split_by_character_sequence":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="split_by_character_sequence expects text content")
        return split_text_by_character_sequence_task(content_data, msg, out_dir)

    if task_id == "join_text":
        if not isinstance(content_data, (str, list)):
            raise HTTPException(status_code=400, detail="join_text expects text content")
        return join_texts(content_data, msg, out_dir)

    if task_id == "join_raw":
        if not isinstance(content_data, (str, list)):
            raise HTTPException(status_code=400, detail="join_raw expects text content")
        return join_raw_texts(content_data, msg, out_dir)

    if task_id == "search_replace":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="search_replace expects text content")
        return [search_replace_text(content_data, msg, out_dir)]

    raise HTTPException(status_code=400, detail=f"Unsupported task: {task_id}")


def create_wordcloud(text: str, out_dir: str) -> str:
    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(out_dir, output_uuid + ".png")
    wordcloud = WordCloud(width=800, height=400, background_color="white").generate(text)
    wordcloud.to_file(output_path)
    return output_path


def remove_stop_words(content_data: str, msg: Dict[str, Any], out_dir: str) -> str:
    language = msg.get("task", {}).get("params", {}).get("language", "en")
    available_languages = get_available_languages()
    if language not in available_languages:
        language = "en"

    stopwords_file = f"stopwords_iso-{language}.json"
    stopwords_path = os.path.join(STOPWORDS_DIR, stopwords_file)

    try:
        with open(stopwords_path, "r", encoding="utf-8") as handle:
            stopwords_list = json.load(handle)
            stopwords_lower = {word.lower() for word in stopwords_list}
    except FileNotFoundError:
        with open(os.path.join(STOPWORDS_DIR, "stopwords_iso-en.json"), "r", encoding="utf-8") as handle:
            stopwords_list = json.load(handle)
            stopwords_lower = {word.lower() for word in stopwords_list}
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Error loading stopwords: {err}")

    lines = content_data.split("\n")
    filtered_lines = []
    for line in lines:
        words = line.split()
        filtered_words = []
        for word in words:
            cleaned_word = word.strip('.,!?;:"()[]{}')
            if cleaned_word and cleaned_word.lower() not in stopwords_lower:
                filtered_words.append(word)
        filtered_lines.append(" ".join(filtered_words))

    filtered_text = "\n".join(filtered_lines)
    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(out_dir, output_uuid + ".txt")
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(filtered_text)

    return output_path


def split_text(content_data: str, msg: Dict[str, Any], out_dir: str) -> List[str]:
    params = msg.get("task", {}).get("params", {})
    chunk_size = params.get("chunk_size", 2000)
    trim = as_bool(params.get("trim"), False)

    try:
        chunk_size = int(chunk_size)
        if chunk_size <= 0:
            chunk_size = 2000
    except (ValueError, TypeError):
        chunk_size = 2000

    file_label = msg.get("file", {}).get("label", "chunk")
    extension = msg.get("file", {}).get("extension", "txt")
    base_label = file_label.replace("." + extension, "")

    text_for_split = normalize_whitespace_for_chunk_split(content_data) if trim else content_data

    chunks = [
        text_for_split[start : start + chunk_size]
        for start in range(0, len(text_for_split), chunk_size)
    ]

    uris = []
    for chunk_id, chunk_content in enumerate(chunks, start=1):
        chunk_filename = f"{base_label}_{chunk_id}.txt"
        chunk_path = os.path.join(out_dir, chunk_filename)
        with open(chunk_path, "w", encoding="utf-8") as handle:
            handle.write(chunk_content)
        uris.append(chunk_path)

    return uris


def split_text_by_character_sequence_task(content_data: str, msg: Dict[str, Any], out_dir: str) -> List[str]:
    params = msg.get("task", {}).get("params", {})
    split_sequence = str(params.get("split_sequence") or "")
    if not split_sequence:
        raise HTTPException(status_code=400, detail="split_by_character_sequence requires task.params.split_sequence")

    remove_sequence = as_bool(params.get("remove_sequence"), False)
    sequence_at_line_start = as_bool(params.get("sequence_at_line_start"), False)

    file_label = msg.get("file", {}).get("label", "chunk")
    extension = msg.get("file", {}).get("extension", "txt")
    base_label = file_label.replace("." + extension, "")

    chunks = split_text_by_sequence(
        content_data,
        split_sequence,
        remove_sequence,
        sequence_at_line_start,
    )
    if not chunks:
        chunks = [content_data]

    uris = []
    for chunk_id, chunk_content in enumerate(chunks, start=1):
        chunk_filename = f"{base_label}_{chunk_id}.txt"
        chunk_path = os.path.join(out_dir, chunk_filename)
        with open(chunk_path, "w", encoding="utf-8") as handle:
            handle.write(chunk_content)
        uris.append(chunk_path)

    return uris


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def normalize_whitespace_for_chunk_split(content_data: str) -> str:
    # Collapse repeated whitespace so chunk_size works better for noisy text.
    return " ".join(str(content_data).split())


def split_text_by_sequence(
    content_data: str,
    sequence: str,
    remove_sequence: bool,
    sequence_at_line_start: bool,
) -> List[str]:
    if not sequence:
        return [content_data]

    split_positions = find_split_positions(content_data, sequence, sequence_at_line_start)
    if not split_positions:
        return [content_data]

    starts = sorted({0, *split_positions})
    chunks: List[str] = []

    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(content_data)
        chunk = content_data[start:end]

        if remove_sequence and chunk.startswith(sequence):
            chunk = chunk[len(sequence) :]

        if chunk:
            chunks.append(chunk)

    return chunks


def find_split_positions(content_data: str, sequence: str, sequence_at_line_start: bool) -> List[int]:
    positions: List[int] = []

    if sequence_at_line_start:
        offset = 0
        for line in content_data.splitlines(keepends=True):
            trimmed = line.lstrip()
            if trimmed.startswith(sequence):
                line_leading_ws = len(line) - len(trimmed)
                positions.append(offset + line_leading_ws)
            offset += len(line)
        return positions

    start_idx = 0
    while True:
        found = content_data.find(sequence, start_idx)
        if found < 0:
            break
        positions.append(found)
        start_idx = found + len(sequence)

    return positions


def parse_search_replace_pairs(msg: Dict[str, Any]) -> List[Dict[str, str]]:
    params = msg.get("task", {}).get("params", {})
    raw_pairs: Any = (
        params.get("pairs")
        if params.get("pairs") is not None
        else params.get("replacements")
    )

    if raw_pairs is None:
        raw_pairs = params.get("search_replace")

    parsed_pairs: List[Dict[str, str]] = []

    if isinstance(raw_pairs, str):
        lines = raw_pairs.splitlines()
        for idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if ":" not in line:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid search_replace pair on line {idx}: expected search:replace",
                )

            search, replace = line.split(":", 1)
            if not search:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid search_replace pair on line {idx}: empty search text",
                )
            parsed_pairs.append({"search": search, "replace": replace})

        return parsed_pairs

    if not isinstance(raw_pairs, list):
        raise HTTPException(
            status_code=400,
            detail="search_replace requires task.params.pairs, replacements, or search_replace",
        )

    for idx, pair in enumerate(raw_pairs, start=1):
        if isinstance(pair, dict):
            search = pair.get("search")
            replace = pair.get("replace", "")
        elif isinstance(pair, str):
            if ":" not in pair:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid search_replace pair at index {idx}: expected search:replace",
                )
            search, replace = pair.split(":", 1)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search_replace pair at index {idx}: unsupported pair format",
            )

        if search is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search_replace pair at index {idx}: missing search",
            )

        search_text = str(search)
        replace_text = "" if replace is None else str(replace)

        if search_text == "":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search_replace pair at index {idx}: empty search text",
            )

        parsed_pairs.append({"search": search_text, "replace": replace_text})

    return parsed_pairs


def search_replace_text(content_data: str, msg: Dict[str, Any], out_dir: str) -> str:
    pairs = parse_search_replace_pairs(msg)
    if not pairs:
        raise HTTPException(status_code=400, detail="search_replace requires at least one valid pair")

    result = str(content_data)
    for pair in pairs:
        result = result.replace(pair["search"], pair["replace"])

    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(out_dir, output_uuid + ".txt")
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(result)

    return output_path


def many_to_one_path(output_uuid: str, extension: str) -> str:
    folder = os.path.join(OUTPUT_FOLDER, "many-to-one")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{os.path.basename(str(output_uuid))}.{extension}")


def finish_many_to_one(output_path: str, out_dir: str) -> str:
    """Move the collected file into the request's dir, so it is served or staged like other outputs."""
    target = os.path.join(out_dir, os.path.basename(output_path))
    shutil.move(output_path, target)
    return target


def derive_many_to_one_output_uuid(msg: Dict[str, Any], task_id: str, include_root_source: bool = True) -> str:
    explicit = msg.get("output_uuid")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    set_process = str(msg.get("set_process") or msg.get("set_process_rid") or "")
    root_source = ""
    if include_root_source:
        root_source = str(
            msg.get("root_source_rid")
            or (msg.get("root_source") or {}).get("@rid")
            or ""
        )

    # Group-aware key: one stable output per process + root source group.
    key_parts = [str(task_id or "task"), set_process, root_source]
    stable_key = "|".join(key_parts)
    if stable_key.strip("|"):
        return uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex

    return str(uuid.uuid4())


def join_texts(content_data: Union[str, List[str]], msg: Dict[str, Any], out_dir: str) -> List[str]:
    separator = msg.get("task", {}).get("params", {}).get("separator", "\n")
    if separator is None:
        separator = "\n"

    # Legacy mode: join a list of extracted file paths.
    if isinstance(content_data, list):
        readme_names = {"readme.txt", "readme.md"}
        file_paths = [
            path
            for path in content_data
            if os.path.basename(path).lower() not in readme_names
        ]

        joined_parts = []
        for path in sorted(file_paths):
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8", errors="ignore")
            if text:
                joined_parts.append(text)

        output_uuid = str(uuid.uuid4())
        output_path = os.path.join(out_dir, output_uuid + ".txt")
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(separator.join(joined_parts))

        return [output_path]

    if not isinstance(content_data, str):
        raise HTTPException(status_code=400, detail="join_text expects text content")

    # File-storage many-to-one mode: append each incoming file content and only return
    # output on the final file.
    output_uuid = derive_many_to_one_output_uuid(msg, "join_text")
    msg["output_uuid"] = output_uuid

    output_path = many_to_one_path(output_uuid, "txt")
    current_file = int(msg.get("current_file", 1) or 1)
    total_files = int(msg.get("total_files", current_file) or current_file)

    # First file rewrites target; subsequent files append with separator if output exists.
    if current_file <= 1:
        mode = "w"
    else:
        mode = "a"

    with open(output_path, mode, encoding="utf-8") as handle:
        needs_separator = current_file > 1 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if needs_separator:
            handle.write(separator)
        handle.write(content_data)

    if current_file == total_files:
        return [finish_many_to_one(output_path, out_dir)]

    return []


def join_raw_texts(content_data: Union[str, List[str]], msg: Dict[str, Any], out_dir: str) -> List[str]:
    separator = msg.get("task", {}).get("params", {}).get("separator", "\n")
    if separator is None:
        separator = "\n"

    # Legacy mode: join a list of extracted file paths.
    if isinstance(content_data, list):
        readme_names = {"readme.txt", "readme.md"}
        file_paths = [
            path
            for path in content_data
            if os.path.basename(path).lower() not in readme_names
        ]

        joined_parts = []
        for path in sorted(file_paths):
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8", errors="ignore")
            if text:
                joined_parts.append(text)

        output_uuid = str(uuid.uuid4())
        output_path = os.path.join(out_dir, output_uuid + ".txt")
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(separator.join(joined_parts))

        return [output_path]

    if not isinstance(content_data, str):
        raise HTTPException(status_code=400, detail="join_raw expects text content")

    # Group-agnostic many-to-one mode: append all callbacks from the same set process
    # to one output, while still accepting grouped message metadata.
    output_uuid = derive_many_to_one_output_uuid(msg, "join_raw", include_root_source=False)
    msg["output_uuid"] = output_uuid

    output_path = many_to_one_path(output_uuid, "txt")
    current_file = int(msg.get("batch_current_file", msg.get("current_file", 1)) or 1)
    total_files = int(msg.get("batch_total_files", msg.get("total_files", current_file)) or current_file)

    if current_file <= 1:
        mode = "w"
    else:
        mode = "a"

    with open(output_path, mode, encoding="utf-8") as handle:
        needs_separator = current_file > 1 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if needs_separator:
            handle.write(separator)
        handle.write(content_data)

    if current_file == total_files:
        return [finish_many_to_one(output_path, out_dir)]

    return []


def ocr_json_to_text(
    json_data: Union[str, List[Dict[str, Any]], Dict[str, Any], List[Any]],
    out_dir: str,
) -> str:
    if not isinstance(json_data, list):
        try:
            json_data = json.loads(json_data) if isinstance(json_data, str) else [json_data]
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Invalid OCR JSON payload: {err}")

    text_parts = []
    for item in json_data:
        if isinstance(item, dict) and "text" in item:
            text = item["text"]
            if text and text.strip():
                text_parts.append(text.strip())

    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(out_dir, output_uuid + ".txt")
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(text_parts))

    return output_path


def json_to_csv(content_json: Union[Dict[str, Any], List[Any]], msg: Dict[str, Any], out_dir: str) -> List[str]:
    if not isinstance(msg, dict):
        raise HTTPException(status_code=400, detail=f"Expected JSON object, got {type(msg).__name__}")

    set_process = msg.get("output", "default")
    if set_process == "many-to-one":
        output_uuid = msg.get("output_uuid")
        if not output_uuid:
            output_uuid = str(uuid.uuid4())
            msg["output_uuid"] = output_uuid

        output_path = many_to_one_path(output_uuid, "csv")
        current_file = int(msg.get("current_file", 1) or 1)
        total_files = int(msg.get("total_files", current_file) or current_file)

        if current_file == 1:
            json_to_csv_custom(content_json, msg, append=False, output_path=output_path)
        else:
            json_to_csv_custom(content_json, msg, append=True, output_path=output_path)

        if current_file == total_files:
            return [finish_many_to_one(output_path, out_dir)]
        return []

    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(out_dir, output_uuid + ".csv")
    json_to_csv_custom(content_json, msg, output_path=output_path)
    if not os.path.exists(output_path):
        # nothing to convert: this returned a path that didn't exist
        raise HTTPException(status_code=400, detail="json2csv found no JSON objects to convert")
    return [output_path]


def is_jsonl(text: str) -> bool:
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return False

    valid_json_lines = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
            valid_json_lines += 1
        except json.JSONDecodeError:
            return False

    return valid_json_lines >= 2


def parse_jsonl(text: str) -> List[Dict[str, Any]]:
    json_objects = []
    lines = text.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                json_objects.append(obj)
        except json.JSONDecodeError:
            continue

    return json_objects


def inject_message_data(flattened_item: Dict[str, Any], message: Dict[str, Any]) -> Dict[str, Any]:
    if "file" in message:
        flattened_item["filename"] = message["file"].get("label", "")
    if "file" in message and "original_filename" in message["file"]:
        flattened_item["original_filename"] = message["file"]["original_filename"]
    return flattened_item


def flatten_json_custom(
    data: Dict[str, Any],
    message: Dict[str, Any],
    parent_key: str = "",
    sep: str = ".",
) -> Dict[str, Any]:
    items: List[Any] = []

    for key, value in data.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key

        if isinstance(value, dict):
            items.extend(flatten_json_custom(value, message, new_key, sep=sep).items())
        elif isinstance(value, list):
            items.append((new_key, "; ".join(str(item) for item in value)))
        else:
            items.append((new_key, value))

    return dict(items)


def json_to_csv_custom(
    json_data: Union[Dict[str, Any], List[Any]],
    message: Dict[str, Any],
    append: bool = False,
    output_path: Optional[str] = None,
) -> str:
    flattened_data = []

    if isinstance(json_data, list):
        for item in json_data:
            if not isinstance(item, dict):
                continue
            flattened_item = flatten_json_custom(item, message)
            flattened_item = inject_message_data(flattened_item, message)
            flattened_data.append(flattened_item)
    elif isinstance(json_data, dict):
        keys = list(json_data.keys())
        if len(keys) == 1 and isinstance(json_data[keys[0]], list):
            parent_key = keys[0]
            for item in json_data[keys[0]]:
                if not isinstance(item, dict):
                    continue
                flattened_item = flatten_json_custom(item, message, parent_key=parent_key)
                flattened_item = inject_message_data(flattened_item, message)
                flattened_data.append(flattened_item)
        else:
            flattened_item = flatten_json_custom(json_data, message)
            flattened_item = inject_message_data(flattened_item, message)
            flattened_data.append(flattened_item)
    else:
        return ""

    if not flattened_data:
        return ""

    all_keys = set()
    for item in flattened_data:
        all_keys.update(item.keys())

    existing_fieldnames = None
    if append and output_path and os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as handle:
                reader = csv.reader(handle, delimiter=";")
                existing_fieldnames = next(reader, None)
        except Exception:
            existing_fieldnames = None

    fieldnames = existing_fieldnames if existing_fieldnames else sorted(all_keys)

    if output_path:
        if append:
            with open(output_path, "a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_ALL)
                for item in flattened_data:
                    row = {key: item.get(key, "") for key in fieldnames}
                    writer.writerow(row)
            return ""

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writeheader()

        for item in flattened_data:
            row = {key: item.get(key, "") for key in fieldnames}
            writer.writerow(row)

        csv_content = output.getvalue()
        output.close()

        with open(output_path, "w", newline="", encoding="utf-8") as handle:
            handle.write(csv_content)

        return csv_content

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_ALL)
    if not append:
        writer.writeheader()

    for item in flattened_data:
        row = {key: item.get(key, "") for key in fieldnames}
        writer.writerow(row)

    csv_content = output.getvalue()
    output.close()
    return csv_content


def get_available_languages() -> Dict[str, str]:
    language_names = {
        "en": "English",
        "fi": "Finnish",
        "sv": "Swedish",
        "de": "German",
        "fr": "French",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
        "ru": "Russian",
        "ja": "Japanese",
        "ko": "Korean",
        "zh": "Chinese",
        "ar": "Arabic",
        "hi": "Hindi",
        "th": "Thai",
        "vi": "Vietnamese",
        "tr": "Turkish",
        "pl": "Polish",
        "nl": "Dutch",
        "da": "Danish",
        "no": "Norwegian",
        "is": "Icelandic",
        "cs": "Czech",
        "sk": "Slovak",
        "hu": "Hungarian",
        "ro": "Romanian",
        "bg": "Bulgarian",
        "hr": "Croatian",
        "sr": "Serbian",
        "sl": "Slovenian",
        "et": "Estonian",
        "lv": "Latvian",
        "lt": "Lithuanian",
        "uk": "Ukrainian",
        "be": "Belarusian",
        "mk": "Macedonian",
        "sq": "Albanian",
        "mt": "Maltese",
        "ga": "Irish",
        "cy": "Welsh",
        "eu": "Basque",
        "ca": "Catalan",
        "gl": "Galician",
        "el": "Greek",
        "he": "Hebrew",
        "fa": "Persian",
        "ur": "Urdu",
        "bn": "Bengali",
        "ta": "Tamil",
        "te": "Telugu",
        "ml": "Malayalam",
        "kn": "Kannada",
        "gu": "Gujarati",
        "pa": "Punjabi",
        "or": "Odia",
        "as": "Assamese",
        "ne": "Nepali",
        "si": "Sinhala",
        "my": "Burmese",
        "km": "Khmer",
        "lo": "Lao",
        "ka": "Georgian",
        "hy": "Armenian",
        "az": "Azerbaijani",
        "kk": "Kazakh",
        "ky": "Kyrgyz",
        "uz": "Uzbek",
        "tg": "Tajik",
        "mn": "Mongolian",
        "bo": "Tibetan",
        "id": "Indonesian",
        "ms": "Malay",
        "tl": "Filipino",
        "sw": "Swahili",
        "am": "Amharic",
        "yo": "Yoruba",
        "ig": "Igbo",
        "ha": "Hausa",
        "zu": "Zulu",
        "af": "Afrikaans",
        "xh": "Xhosa",
        "st": "Sesotho",
        "tn": "Tswana",
        "ss": "Swati",
        "ve": "Venda",
        "ts": "Tsonga",
        "nr": "Ndebele",
        "nso": "Northern Sotho",
    }

    available_languages: Dict[str, str] = {}
    stopwords_dir = STOPWORDS_DIR

    if os.path.exists(stopwords_dir):
        for filename in os.listdir(stopwords_dir):
            if filename.startswith("stopwords_iso-") and filename.endswith(".json"):
                lang_code = filename.replace("stopwords_iso-", "").replace(".json", "")
                if lang_code in language_names:
                    available_languages[lang_code] = language_names[lang_code]

    return available_languages


if __name__ == "__main__":
    import uvicorn

    print(f"storage mode: {md_storage.describe_mode()}")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "9008")))
