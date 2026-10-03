from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

import json
import os

from typing import Any, Dict, List, Optional


def get_default_help_candidates(base_dir: str) -> List[str]:
    return [
        os.path.join(base_dir, "help", "index.md"),
        os.path.join(base_dir, "README.md"),
    ]


def load_service_descriptor(base_dir: str, descriptor_filename: str = "service.json") -> Dict[str, Any]:
    descriptor_path = os.path.join(base_dir, descriptor_filename)

    try:
        with open(descriptor_path, "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except FileNotFoundError as err:
        raise HTTPException(status_code=500, detail="service.json not found") from err
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=500, detail="service.json is invalid JSON") from err
    except OSError as err:
        raise HTTPException(status_code=500, detail=f"Failed to read service.json: {err}") from err

    if not isinstance(descriptor, dict):
        raise HTTPException(status_code=500, detail="service.json must contain a JSON object")

    return descriptor


def load_help_markdown(descriptor: Dict[str, Any], help_candidates: List[str]) -> str:
    for candidate in help_candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    return handle.read()
            except OSError as err:
                raise HTTPException(status_code=500, detail=f"Failed to read help file: {err}") from err

    service_id = descriptor.get("id", "md-base")
    service_name = descriptor.get("name", service_id)
    description = descriptor.get("description", "No description available.")
    return f"# {service_name}\n\nService id: `{service_id}`\n\n{description}\n"


def register_service_registration_endpoints(
    app: FastAPI,
    base_dir: str,
    descriptor_filename: str = "service.json",
    help_candidates: Optional[List[str]] = None,
    service_id: str = "md-base",
) -> None:
    candidates = help_candidates or get_default_help_candidates(base_dir)

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": service_id}

    @app.get("/config")
    async def get_config() -> JSONResponse:
        descriptor = load_service_descriptor(base_dir, descriptor_filename)
        return JSONResponse(status_code=200, content=descriptor)

    @app.get("/help")
    async def get_help() -> Response:
        descriptor = load_service_descriptor(base_dir, descriptor_filename)
        markdown = load_help_markdown(descriptor, candidates)
        return Response(content=markdown, media_type="text/markdown; charset=utf-8")
