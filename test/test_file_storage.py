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

    def _output_file(self, label: str) -> str:
        """The output with this label in MessyDesk's tmp/ (outputs are moved there as <random>_<label>)."""
        tmp_dir = os.path.join(self.md_root, "data", "messydesk", "tmp")
        matches = [name for name in os.listdir(tmp_dir) if name.endswith("_" + label)] if os.path.isdir(tmp_dir) else []
        self.assertEqual(len(matches), 1, (label, matches))
        return os.path.join(tmp_dir, matches[0])

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

        content = asyncio.run(api.load_input_content(msg, None, self.output_dir))
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
            self._output_file("sample_1.txt"),
            self._output_file("sample_2.txt"),
            self._output_file("sample_3.txt"),
        ]
        for expected in expected_files:
            self.assertTrue(os.path.exists(expected), expected)

    def test_process_endpoint_split_text_trim_normalizes_whitespace(self):
        rel_path = "data/messydesk/sample_trim.txt"
        self._write_source(rel_path, "A   B\n\n   C\t\tD")

        request_payload = {
            "task": {"id": "split_text", "params": {"chunk_size": 3, "trim": True}},
            "file": {"path": rel_path, "label": "sample_trim.txt", "extension": "txt"},
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

        self.assertEqual(response_payload["response"]["type"], "disk")

        chunk_1 = self._output_file("sample_trim_1.txt")
        chunk_2 = self._output_file("sample_trim_2.txt")
        chunk_3 = self._output_file("sample_trim_3.txt")

        with open(chunk_1, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "A B")
        with open(chunk_2, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), " C ")
        with open(chunk_3, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "D")

    def test_process_endpoint_split_by_character_sequence_remove_sequence(self):
        rel_path = "data/messydesk/sequence_sample.txt"
        self._write_source(rel_path, "Title\n## A\none\n## B\ntwo\n")

        request_payload = {
            "task": {
                "id": "split_by_character_sequence",
                "params": {
                    "split_sequence": "##",
                    "remove_sequence": True,
                    "sequence_at_line_start": True,
                },
            },
            "file": {"path": rel_path, "label": "sequence_sample.txt", "extension": "txt"},
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

        self.assertEqual(response_payload["response"]["type"], "disk")
        self.assertEqual(len(response_payload["response"]["files"]), 3)

        chunk_1 = self._output_file("sequence_sample_1.txt")
        chunk_2 = self._output_file("sequence_sample_2.txt")
        chunk_3 = self._output_file("sequence_sample_3.txt")

        with open(chunk_1, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Title\n")
        with open(chunk_2, "r", encoding="utf-8") as handle:
            self.assertTrue(handle.read().startswith(" A"))
        with open(chunk_3, "r", encoding="utf-8") as handle:
            self.assertTrue(handle.read().startswith(" B"))

    def test_split_text_sequence_at_line_start_does_not_split_mid_line(self):
        text = "alpha ## marker\n  ## section\nbeta\n"
        chunks = api.split_text_by_sequence(
            text,
            "##",
            remove_sequence=False,
            sequence_at_line_start=True,
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "alpha ## marker\n  ")
        self.assertTrue(chunks[1].startswith("## section"))

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

        joined_path = self._output_file("joined_text_test.txt")
        with open(joined_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "alpha\n---\nbeta")

    def test_join_text_many_to_one_with_input_set_uses_disk_file_content(self):
        rel_path_1 = "data/messydesk/part_set_1.txt"
        rel_path_2 = "data/messydesk/part_set_2.txt"
        self._write_source(rel_path_1, "first")
        self._write_source(rel_path_2, "second")

        payload_1 = {
            "task": {"id": "join_text", "params": {"separator": "\n"}},
            "file": {"path": rel_path_1, "label": "part_set_1.txt", "extension": "txt", "type": "text"},
            "input_set": "#1:1",
            "output": "many-to-one",
            "output_uuid": "joined_input_set_test",
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

        self.assertEqual(res1["response"]["type"], "disk")
        self.assertEqual(res1["response"]["files"], [])

        payload_2 = {
            "task": {"id": "join_text", "params": {"separator": "\n"}},
            "file": {"path": rel_path_2, "label": "part_set_2.txt", "extension": "txt", "type": "text"},
            "input_set": "#1:1",
            "output": "many-to-one",
            "output_uuid": "joined_input_set_test",
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
        self.assertEqual(res2["response"]["files"][0]["label"], "joined_input_set_test.txt")

        joined_path = self._output_file("joined_input_set_test.txt")
        with open(joined_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "first\nsecond")

    def test_join_text_grouped_many_to_one_without_output_uuid_appends_full_group(self):
        rel_path_1 = "data/messydesk/group_part_1.txt"
        rel_path_2 = "data/messydesk/group_part_2.txt"
        rel_path_3 = "data/messydesk/group_part_3.txt"
        self._write_source(rel_path_1, "alpha")
        self._write_source(rel_path_2, "beta")
        self._write_source(rel_path_3, "gamma")

        root_source = {
            "@rid": "#73:125007",
            "label": "example.pdf",
            "type": "pdf",
        }

        payloads = [
            {
                "task": {"id": "join_text", "params": {"separator": "\n"}},
                "file": {"path": rel_path_1, "label": "group_part_1.txt", "extension": "txt", "type": "text"},
                "input_set": "#1:1",
                "output": "many-to-one",
                "set_process": "#200:1",
                "current_file": 1,
                "total_files": 3,
                "root_source": root_source,
                "root_source_rid": root_source["@rid"],
                "root_source_label": root_source["label"],
                "group_size": 3,
            },
            {
                "task": {"id": "join_text", "params": {"separator": "\n"}},
                "file": {"path": rel_path_2, "label": "group_part_2.txt", "extension": "txt", "type": "text"},
                "input_set": "#1:1",
                "output": "many-to-one",
                "set_process": "#200:1",
                "current_file": 2,
                "total_files": 3,
                "root_source": root_source,
                "root_source_rid": root_source["@rid"],
                "root_source_label": root_source["label"],
                "group_size": 3,
            },
            {
                "task": {"id": "join_text", "params": {"separator": "\n"}},
                "file": {"path": rel_path_3, "label": "group_part_3.txt", "extension": "txt", "type": "text"},
                "input_set": "#1:1",
                "output": "many-to-one",
                "set_process": "#200:1",
                "current_file": 3,
                "total_files": 3,
                "root_source": root_source,
                "root_source_rid": root_source["@rid"],
                "root_source_label": root_source["label"],
                "group_size": 3,
            },
        ]

        responses = []
        for payload in payloads:
            req = UploadFile(
                file=io.BytesIO(json.dumps(payload).encode("utf-8")),
                filename="request.json",
            )
            responses.append(
                asyncio.run(
                    api.process_files(
                        self._empty_request(),
                        request=req,
                        message=None,
                        content=None,
                    )
                )
            )

        self.assertEqual(responses[0]["response"]["files"], [])
        self.assertEqual(responses[1]["response"]["files"], [])
        self.assertEqual(len(responses[2]["response"]["files"]), 1)

        output_label = responses[2]["response"]["files"][0]["label"]
        output_path = self._output_file(output_label)
        with open(output_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "alpha\nbeta\ngamma")

    def test_join_raw_grouped_many_to_one_creates_single_batch_output(self):
        rel_path_1 = "data/messydesk/raw_group_1.txt"
        rel_path_2 = "data/messydesk/raw_group_2.txt"
        rel_path_3 = "data/messydesk/raw_group_3.txt"
        self._write_source(rel_path_1, "doc-a")
        self._write_source(rel_path_2, "doc-b")
        self._write_source(rel_path_3, "doc-c")

        payloads = [
            {
                "task": {"id": "join_raw", "params": {"separator": "\n"}},
                "file": {"path": rel_path_1, "label": "raw_group_1.txt", "extension": "txt", "type": "text"},
                "input_set": "#1:1",
                "output": "many-to-one",
                "set_process": "#200:2",
                "current_file": 1,
                "total_files": 2,
                "batch_current_file": 1,
                "batch_total_files": 3,
                "root_source_rid": "#73:1",
                "group_size": 2,
            },
            {
                "task": {"id": "join_raw", "params": {"separator": "\n"}},
                "file": {"path": rel_path_2, "label": "raw_group_2.txt", "extension": "txt", "type": "text"},
                "input_set": "#1:1",
                "output": "many-to-one",
                "set_process": "#200:2",
                "current_file": 2,
                "total_files": 2,
                "batch_current_file": 2,
                "batch_total_files": 3,
                "root_source_rid": "#73:1",
                "group_size": 2,
            },
            {
                "task": {"id": "join_raw", "params": {"separator": "\n"}},
                "file": {"path": rel_path_3, "label": "raw_group_3.txt", "extension": "txt", "type": "text"},
                "input_set": "#1:1",
                "output": "many-to-one",
                "set_process": "#200:2",
                "current_file": 1,
                "total_files": 1,
                "batch_current_file": 3,
                "batch_total_files": 3,
                "root_source_rid": "#73:2",
                "group_size": 1,
            },
        ]

        responses = []
        for payload in payloads:
            req = UploadFile(
                file=io.BytesIO(json.dumps(payload).encode("utf-8")),
                filename="request.json",
            )
            responses.append(
                asyncio.run(
                    api.process_files(
                        self._empty_request(),
                        request=req,
                        message=None,
                        content=None,
                    )
                )
            )

        self.assertEqual(responses[0]["response"]["files"], [])
        self.assertEqual(responses[1]["response"]["files"], [])
        self.assertEqual(len(responses[2]["response"]["files"]), 1)

        output_label = responses[2]["response"]["files"][0]["label"]
        output_path = self._output_file(output_label)
        with open(output_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "doc-a\ndoc-b\ndoc-c")

    def test_search_replace_applies_pairs_in_order(self):
        rel_path = "data/messydesk/search_replace_source.txt"
        self._write_source(rel_path, "cat dog bird")

        request_payload = {
            "task": {
                "id": "search_replace",
                "params": {
                    "search_replace": "cat:dog\ndog:wolf",
                },
            },
            "file": {"path": rel_path, "label": "search_replace_source.txt", "extension": "txt", "type": "text"},
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

        self.assertEqual(response_payload["response"]["type"], "disk")
        self.assertEqual(len(response_payload["response"]["files"]), 1)

        output_label = response_payload["response"]["files"][0]["label"]
        output_path = self._output_file(output_label)
        with open(output_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "wolf wolf bird")

    def test_search_replace_rejects_invalid_pair_format(self):
        with self.assertRaises(HTTPException) as err:
            api.search_replace_text(
                "abc",
                {
                    "task": {
                        "id": "search_replace",
                        "params": {
                            "search_replace": "invalid-line-without-colon",
                        },
                    }
                },
                self.output_dir,
            )

        self.assertEqual(err.exception.status_code, 400)
        self.assertIn("expected search:replace", err.exception.detail)

    def test_http_mode_serves_outputs_from_a_request_dir(self):
        payload = {"task": {"id": "split_text", "params": {"chunk_size": 4}}, "file": {"label": "up.txt", "extension": "txt"}}
        message = UploadFile(file=io.BytesIO(json.dumps(payload).encode("utf-8")), filename="message.json")
        content = UploadFile(file=io.BytesIO(b"abcdefgh"), filename="up.txt")
        with patch.object(api, "MD_ROOT", None):
            result = asyncio.run(api.process_files(self._empty_request(), request=None, message=message, content=content))
        self.assertEqual(result["response"]["type"], "stored")
        uris = result["response"]["uri"]
        self.assertEqual([u.rsplit("/", 1)[1] for u in uris], ["up_1.txt", "up_2.txt"])
        output_id = uris[0].split("/")[2]
        self.assertEqual(sorted(os.listdir(os.path.join(self.output_dir, output_id))), ["up_1.txt", "up_2.txt"])

    def test_disk_request_without_md_path_is_refused(self):
        payload = {"task": {"id": "split_text"}, "file": {"path": "data/messydesk/x.txt"}}
        message = UploadFile(file=io.BytesIO(json.dumps(payload).encode("utf-8")), filename="message.json")
        with patch.object(api, "MD_ROOT", None):
            with self.assertRaises(HTTPException) as err:
                asyncio.run(api.process_files(self._empty_request(), request=None, message=message, content=None))
        self.assertEqual(err.exception.status_code, 400)
        self.assertIn("disk mode is off", err.exception.detail)

    def test_disk_mode_leaves_nothing_in_output(self):
        rel_path = "data/messydesk/leftover.txt"
        self._write_source(rel_path, "one two three")
        payload = {"task": {"id": "wordcloud"}, "file": {"path": rel_path, "label": "leftover.txt", "extension": "txt"}}
        message = UploadFile(file=io.BytesIO(json.dumps(payload).encode("utf-8")), filename="message.json")
        result = asyncio.run(api.process_files(self._empty_request(), request=None, message=message, content=None))
        self.assertEqual(len(result["response"]["files"]), 1)
        self.assertEqual(os.listdir(self.output_dir), [])


if __name__ == "__main__":
    unittest.main()
