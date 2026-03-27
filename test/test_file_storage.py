import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from starlette.requests import Request

os.environ.setdefault("MD_PATH", os.getcwd())

import api


class TestFileStorageMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.md_root = self.tmp.name
        self.output_dir = os.path.join(self.md_root, "output")
        os.makedirs(self.output_dir, exist_ok=True)

        self.patch_md_root = patch.object(api, "MD_ROOT", self.md_root)
        self.patch_output = patch.object(api, "OUTPUT_FOLDER", self.output_dir)
        self.patch_md_root.start()
        self.patch_output.start()

        self.addCleanup(self.patch_md_root.stop)
        self.addCleanup(self.patch_output.stop)

    def _write_source(self, rel_path: str, content: str) -> str:
        abs_path = os.path.join(self.md_root, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return abs_path

    @staticmethod
    def _empty_request() -> Request:
        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": "/process",
            "headers": [],
        }
        return Request(scope, receive)

    def test_resolve_md_relative_path_rejects_absolute_and_traversal(self):
        with self.assertRaises(HTTPException) as abs_err:
            api.resolve_md_relative_path("/etc/passwd")
        self.assertEqual(abs_err.exception.status_code, 400)

        with self.assertRaises(HTTPException) as traversal_err:
            api.resolve_md_relative_path("../outside.txt")
        self.assertEqual(traversal_err.exception.status_code, 400)

    def test_load_input_content_reads_text_from_disk(self):
        rel_path = "data/messydesk/source.txt"
        self._write_source(rel_path, "hello from disk")

        msg = {
            "task": {"id": "split_text", "params": {"chunk_size": 100}},
            "file": {"path": rel_path, "label": "source.txt", "extension": "txt"},
        }

        content = asyncio.run(api.load_input_content(msg, None))
        self.assertEqual(content, "hello from disk")

    def test_process_endpoint_request_file_storage_split_text(self):
        rel_path = "data/messydesk/sample.txt"
        self._write_source(rel_path, "abcdefghij")

        request_payload = {
            "task": {"id": "split_text", "params": {"chunk_size": 4}},
            "file": {"path": rel_path, "label": "sample.txt", "extension": "txt"},
        }

        request_upload = UploadFile(
            file=io.BytesIO(json.dumps(request_payload).encode("utf-8")),
            filename="request.json",
        )
        response_payload = asyncio.run(
            api.process_files(
                self._empty_request(),
                request=request_upload,
                message=None,
                content=None,
            )
        )

        self.assertIn("response", response_payload)
        self.assertEqual(response_payload["response"]["type"], "disk")
        self.assertIn("files", response_payload["response"])
        self.assertEqual(len(response_payload["response"]["files"]), 3)
        self.assertEqual(response_payload["response"]["files"][0]["label"], "sample_1.txt")

        expected_files = [
            os.path.join(self.output_dir, "sample_1.txt"),
            os.path.join(self.output_dir, "sample_2.txt"),
            os.path.join(self.output_dir, "sample_3.txt"),
        ]
        for expected in expected_files:
            self.assertTrue(os.path.exists(expected), expected)

    def test_process_endpoint_requires_file_path_in_request_mode(self):
        request_payload = {
            "task": {"id": "split_text", "params": {"chunk_size": 10}},
            "file": {"label": "missing.txt", "extension": "txt"},
        }

        request_upload = UploadFile(
            file=io.BytesIO(json.dumps(request_payload).encode("utf-8")),
            filename="request.json",
        )
        with self.assertRaises(HTTPException) as err:
            asyncio.run(
                api.process_files(
                    self._empty_request(),
                    request=request_upload,
                    message=None,
                    content=None,
                )
            )

        self.assertEqual(err.exception.status_code, 400)
        self.assertIn("Missing file.path", err.exception.detail)

    def test_join_text_many_to_one_appends_and_returns_only_on_final_file(self):
        rel_path_1 = "data/messydesk/part1.txt"
        rel_path_2 = "data/messydesk/part2.txt"
        self._write_source(rel_path_1, "alpha")
        self._write_source(rel_path_2, "beta")

        payload_1 = {
            "task": {"id": "join_text", "params": {"separator": "\n---\n"}},
            "file": {"path": rel_path_1, "label": "part1.txt", "extension": "txt"},
            "output": "many-to-one",
            "output_uuid": "joined_text_test",
            "current_file": 1,
            "total_files": 2,
        }

        req1 = UploadFile(
            file=io.BytesIO(json.dumps(payload_1).encode("utf-8")),
            filename="request.json",
        )
        res1 = asyncio.run(
            api.process_files(
                self._empty_request(),
                request=req1,
                message=None,
                content=None,
            )
        )

        self.assertIn("response", res1)
        self.assertEqual(res1["response"]["type"], "disk")
        self.assertEqual(res1["response"]["files"], [])

        payload_2 = {
            "task": {"id": "join_text", "params": {"separator": "\n---\n"}},
            "file": {"path": rel_path_2, "label": "part2.txt", "extension": "txt"},
            "output": "many-to-one",
            "output_uuid": "joined_text_test",
            "current_file": 2,
            "total_files": 2,
        }

        req2 = UploadFile(
            file=io.BytesIO(json.dumps(payload_2).encode("utf-8")),
            filename="request.json",
        )
        res2 = asyncio.run(
            api.process_files(
                self._empty_request(),
                request=req2,
                message=None,
                content=None,
            )
        )

        self.assertEqual(res2["response"]["type"], "disk")
        self.assertEqual(len(res2["response"]["files"]), 1)
        self.assertEqual(res2["response"]["files"][0]["label"], "joined_text_test.txt")

        joined_path = os.path.join(self.output_dir, "joined_text_test.txt")
        with open(joined_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "alpha\n---\nbeta")


if __name__ == "__main__":
    unittest.main()
