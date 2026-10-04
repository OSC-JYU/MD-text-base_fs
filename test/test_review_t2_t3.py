"""Escaped colons in search_replace, and many-to-one jobs over HTTP with one file per job."""

import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault("MD_PATH", os.getcwd())

import asyncio

from fastapi import UploadFile
from starlette.requests import Request

import api


def empty_request() -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": "/process", "headers": []}, receive)


class TestSearchReplacePairs(unittest.TestCase):
    def pairs(self, text):
        return api.parse_search_replace_pairs({"task": {"params": {"search_replace": text}}})

    def test_escaped_colon_is_part_of_the_text(self):
        self.assertEqual(self.pairs("10\\:30:half past ten"), [{"search": "10:30", "replace": "half past ten"}])
        self.assertEqual(self.pairs("a:b\\:c"), [{"search": "a", "replace": "b:c"}])
        self.assertEqual(self.pairs("C\\\\:D"), [{"search": "C\\", "replace": "D"}])
        self.assertEqual(self.pairs("x:"), [{"search": "x", "replace": ""}])

    def test_a_line_without_an_unescaped_colon_is_an_error(self):
        with self.assertRaises(api.HTTPException) as ctx:
            self.pairs("only\\:escaped")
        self.assertIn("\\:", ctx.exception.detail)

    def test_list_pairs_unescape_too(self):
        msg = {"task": {"params": {"pairs": ["http\\://a:https\\://a"]}}}
        self.assertEqual(api.parse_search_replace_pairs(msg), [{"search": "http://a", "replace": "https://a"}])

    def test_replacement_in_text(self):
        with tempfile.TemporaryDirectory() as out:
            path = api.search_replace_text("Meet at 10:30.", {"task": {"params": {"search_replace": "10\\:30:half past ten"}}}, out)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "Meet at half past ten.")


class TestManyToOneOverHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(api, "OUTPUT_FOLDER", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, index, total, content, filename="page.txt"):
        msg = {
            "task": {"id": "join_raw", "params": {"separator": "\n"}},
            "file": {"@rid": f"#80:{index}", "label": filename, "type": "text", "extension": "txt"},
            "input_set": "#20:1", "set_process": "#30:1", "behaviour": "many-to-one",
            "current_file": index, "total_files": total,
        }
        response = asyncio.run(api.process_files(
            empty_request(),
            request=None,
            message=UploadFile(filename="message.json", file=io.BytesIO(json.dumps(msg).encode())),
            content=UploadFile(filename=filename, file=io.BytesIO(content)),
        ))
        return response["response"]["uri"]

    def served(self, uri):
        _, _, output_id, filename = uri.split("/")
        with open(os.path.join(self.tmp.name, output_id, filename), encoding="utf-8") as handle:
            return handle.read()

    def test_each_job_uploads_its_own_file_and_the_last_one_answers(self):
        self.assertEqual(self.job(1, 3, b"first page"), [])
        self.assertEqual(self.job(2, 3, b"second page"), [])
        uris = self.job(3, 3, b"third page")
        self.assertEqual(len(uris), 1)
        self.assertEqual(self.served(uris[0]), "first page\nsecond page\nthird page")

    def test_a_set_zip_from_an_older_consumer_still_works(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("a.txt", "alpha")
            z.writestr("b.txt", "beta")
        uris = self.job(1, 1, archive.getvalue(), filename="set.zip")
        self.assertEqual(self.served(uris[0]), "alpha\nbeta")


if __name__ == "__main__":
    unittest.main()
