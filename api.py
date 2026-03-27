from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

import csv
import io
import json
import os
import shutil
import uuid
import zipfile

from typing import Any, Dict, List, Optional, Union

from wordcloud import WordCloud


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

MD_PATH_ENV = os.getenv("MD_PATH", "")
CONTAINER_MODE = os.getenv("CONTAINER", "").strip().lower() in ("1", "true", "yes", "on")
STORAGE_MODE = (os.getenv("STORAGE_MODE") or os.getenv("FILE_STORAGE_MODE") or "disk").strip().lower()

def is_disk_mode() -> bool:
    return STORAGE_MODE == "disk"


def ensure_md_path_for_disk_mode(md_path_env: str) -> None:
    if is_disk_mode() and (not isinstance(md_path_env, str) or not md_path_env.strip()):
        raise RuntimeError("MD_PATH must be set when STORAGE_MODE=disk")

OUTPUT_FOLDER = "output"
for folder in (OUTPUT_FOLDER):
    os.makedirs(folder, exist_ok=True)


def resolve_md_root(md_path_env: str, container_mode: bool) -> str:
    """Resolve MessyDesk root containing data/ while allowing local dev fallback."""
    candidates = []
    if isinstance(md_path_env, str) and md_path_env.strip():
        raw = os.path.abspath(md_path_env.strip())
        if os.path.basename(raw) == "data":
            candidates.append(os.path.dirname(raw))
        candidates.append(raw)

    if container_mode:
        candidates.append("/app")

    candidates.append(os.path.abspath("."))

    seen = set()
    existing_dirs = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        if os.path.isdir(os.path.join(candidate, "data")):
            return candidate
        if os.path.isdir(candidate):
            existing_dirs.append(candidate)

    if existing_dirs:
        return existing_dirs[0]

    raise RuntimeError(
        "Could not resolve MessyDesk data root. Set MD_PATH to a directory containing data/."
    )


try:
    ensure_md_path_for_disk_mode(MD_PATH_ENV)
    MD_ROOT = resolve_md_root(MD_PATH_ENV, CONTAINER_MODE)
except RuntimeError:
    if is_disk_mode():
        raise
    MD_ROOT = os.path.abspath(".")


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


def resolve_md_relative_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise HTTPException(status_code=400, detail="Invalid file.path")

    if os.path.isabs(relative_path):
        raise HTTPException(status_code=400, detail="file.path must be relative to MD_PATH")

    md_root = os.path.abspath(MD_ROOT)
    resolved = os.path.abspath(os.path.join(md_root, relative_path))
    if resolved != md_root and not resolved.startswith(md_root + os.sep):
        raise HTTPException(status_code=400, detail="file.path is outside MD_PATH")

    return resolved


def infer_file_kind(msg: Dict[str, Any]) -> str:
    file_type = str(msg.get("file", {}).get("type", "")).lower()
    extension = str(msg.get("file", {}).get("extension", "")).lower()

    if file_type in ("json", "ocr.json") or extension == "json":
        return "json"
    if msg.get("input_set"):
        return "zip"
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


async def get_files_zip_from_bytes(content_data: bytes) -> List[str]:
    extract_dir = os.path.join(OUTPUT_FOLDER, f"zip_{uuid.uuid4().hex}")
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
) -> Union[str, Dict[str, Any], List[Any], List[str]]:
    file_kind = infer_file_kind(msg)

    if content_file is not None:
        raw = await content_file.read()
        if file_kind == "zip":
            return await get_files_zip_from_bytes(raw)
        if file_kind == "json":
            return await parse_content_json_bytes(raw)

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as err:
            raise HTTPException(status_code=400, detail=f"Content file encoding error: {err}")

    if not is_disk_mode():
        raise HTTPException(
            status_code=400,
            detail="Missing content file. HTTP storage mode requires multipart content upload.",
        )

    source_rel_path = msg.get("file", {}).get("path")
    if not source_rel_path:
        raise HTTPException(
            status_code=400,
            detail="Missing file.path. File-storage mode requires file.path in request payload.",
        )

    source_abs_path = resolve_md_relative_path(source_rel_path)
    if not os.path.exists(source_abs_path):
        raise HTTPException(status_code=404, detail=f"Source file not found: {source_rel_path}")

    if file_kind == "zip":
        with open(source_abs_path, "rb") as handle:
            return await get_files_zip_from_bytes(handle.read())

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


def to_legacy_response(output_paths: List[str]) -> Dict[str, Any]:
    uris = [f"/files/{os.path.basename(path)}" for path in output_paths]
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
        callback_name = safe_name
        source_exists = os.path.isfile(output_path)

        if source_exists:
            target_path = os.path.join(tmp_dir, callback_name)
            if os.path.abspath(output_path) != os.path.abspath(target_path):
                if os.path.exists(target_path):
                    callback_name = f"{uuid.uuid4().hex}_{safe_name}"
                    target_path = os.path.join(tmp_dir, callback_name)
                shutil.copy2(output_path, target_path)

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
        "storage_mode": STORAGE_MODE,
        "response": {
            "type": "disk",
            "files": files,
        },
    }


@app.post("/process")
async def process_files(
    http_request: Request,
    request: Optional[UploadFile] = File(None),
    message: Optional[UploadFile] = File(None),
    content: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    try:
        msg = await extract_message(http_request, request, message)
        task_id = msg.get("task", {}).get("id")
        if not task_id:
            raise HTTPException(status_code=400, detail="Missing task.id in request payload")

        content_data = await load_input_content(msg, content)
        output_paths = execute_task(task_id, content_data, msg)

        if is_disk_mode():
            return to_disk_response(output_paths, task_id, msg)

        response = to_legacy_response(output_paths)
        response["response"]["storage_mode"] = STORAGE_MODE
        return response

    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Processing failed: {err}")


@app.get("/files/{filename:path}")
def serve_file(filename: str, background_tasks: BackgroundTasks):
    file_path = os.path.join(OUTPUT_FOLDER, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    def remove_file(path: str):
        try:
            os.remove(path)
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
) -> List[str]:
    if task_id == "json2csv":
        if not isinstance(content_data, (list, dict)):
            raise HTTPException(status_code=400, detail="json2csv expects JSON content")
        return json_to_csv(content_data, msg)

    if task_id == "json2text" and msg.get("file", {}).get("type") == "ocr.json":
        return [ocr_json_to_text(content_data)]

    if task_id == "wordcloud":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="wordcloud expects text content")
        return [create_wordcloud(content_data)]

    if task_id == "remove_stop_words":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="remove_stop_words expects text content")
        return [remove_stop_words(content_data, msg)]

    if task_id == "split_text":
        if not isinstance(content_data, str):
            raise HTTPException(status_code=400, detail="split_text expects text content")
        return split_text(content_data, msg)

    if task_id == "join_text":
        if not isinstance(content_data, (str, list)):
            raise HTTPException(status_code=400, detail="join_text expects text content")
        return join_texts(content_data, msg)

    raise HTTPException(status_code=400, detail=f"Unsupported task: {task_id}")


def create_wordcloud(text: str) -> str:
    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".png")
    wordcloud = WordCloud(width=800, height=400, background_color="white").generate(text)
    wordcloud.to_file(output_path)
    return output_path


def remove_stop_words(content_data: str, msg: Dict[str, Any]) -> str:
    language = msg.get("task", {}).get("params", {}).get("language", "en")
    available_languages = get_available_languages()
    if language not in available_languages:
        language = "en"

    stopwords_file = f"stopwords_iso-{language}.json"
    stopwords_path = os.path.join("stopwords", stopwords_file)

    try:
        with open(stopwords_path, "r", encoding="utf-8") as handle:
            stopwords_list = json.load(handle)
            stopwords_lower = {word.lower() for word in stopwords_list}
    except FileNotFoundError:
        with open(os.path.join("stopwords", "stopwords_iso-en.json"), "r", encoding="utf-8") as handle:
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
    output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".txt")
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(filtered_text)

    return output_path


def split_text(content_data: str, msg: Dict[str, Any]) -> List[str]:
    params = msg.get("task", {}).get("params", {})
    chunk_size = params.get("chunk_size", 2000)

    try:
        chunk_size = int(chunk_size)
        if chunk_size <= 0:
            chunk_size = 2000
    except (ValueError, TypeError):
        chunk_size = 2000

    file_label = msg.get("file", {}).get("label", "chunk")
    extension = msg.get("file", {}).get("extension", "txt")
    base_label = file_label.replace("." + extension, "")

    uris = []
    for chunk_id, start in enumerate(range(0, len(content_data), chunk_size), start=1):
        chunk_content = content_data[start : start + chunk_size]
        chunk_filename = f"{base_label}_{chunk_id}.txt"
        chunk_path = os.path.join(OUTPUT_FOLDER, chunk_filename)
        with open(chunk_path, "w", encoding="utf-8") as handle:
            handle.write(chunk_content)
        uris.append(chunk_path)

    return uris


def join_texts(content_data: Union[str, List[str]], msg: Dict[str, Any]) -> List[str]:
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
        output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".txt")
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(separator.join(joined_parts))

        return [output_path]

    if not isinstance(content_data, str):
        raise HTTPException(status_code=400, detail="join_text expects text content")

    # File-storage many-to-one mode: append each incoming file content and only return
    # output on the final file.
    output_uuid = msg.get("output_uuid")
    if not output_uuid:
        output_uuid = str(uuid.uuid4())
        msg["output_uuid"] = output_uuid

    output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".txt")
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
        return [output_path]

    return []


def ocr_json_to_text(
    json_data: Union[str, List[Dict[str, Any]], Dict[str, Any], List[Any]]
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
    output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".txt")
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(text_parts))

    return output_path


def json_to_csv(content_json: Union[Dict[str, Any], List[Any]], msg: Dict[str, Any]) -> List[str]:
    if not isinstance(msg, dict):
        raise HTTPException(status_code=400, detail=f"Expected JSON object, got {type(msg).__name__}")

    set_process = msg.get("output", "default")
    if set_process == "many-to-one":
        output_uuid = msg.get("output_uuid")
        if not output_uuid:
            output_uuid = str(uuid.uuid4())
            msg["output_uuid"] = output_uuid

        output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".csv")
        current_file = int(msg.get("current_file", 1) or 1)
        total_files = int(msg.get("total_files", current_file) or current_file)

        if current_file == 1:
            json_to_csv_custom(content_json, msg, append=False, output_path=output_path)
        else:
            json_to_csv_custom(content_json, msg, append=True, output_path=output_path)

        if current_file == total_files:
            return [output_path]
        return []

    output_uuid = str(uuid.uuid4())
    output_path = os.path.join(OUTPUT_FOLDER, output_uuid + ".csv")
    json_to_csv_custom(content_json, msg, output_path=output_path)
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
    stopwords_dir = "stopwords"

    if os.path.exists(stopwords_dir):
        for filename in os.listdir(stopwords_dir):
            if filename.startswith("stopwords_iso-") and filename.endswith(".json"):
                lang_code = filename.replace("stopwords_iso-", "").replace(".json", "")
                if lang_code in language_names:
                    available_languages[lang_code] = language_names[lang_code]

    return available_languages


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9008)
