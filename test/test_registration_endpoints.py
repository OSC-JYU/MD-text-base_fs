import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MD_PATH", os.getcwd())

import api


class TestRegistrationEndpoints(unittest.TestCase):
    def test_load_service_descriptor_reads_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            descriptor_path = os.path.join(tmp, "service.json")
            expected = {"id": "md-base", "api": "/process", "adapter": "elg_fs"}
            with open(descriptor_path, "w", encoding="utf-8") as handle:
                json.dump(expected, handle)

            with patch.object(api, "SERVICE_DESCRIPTOR_PATH", descriptor_path):
                loaded = api.load_service_descriptor()

            self.assertEqual(loaded["id"], "md-base")
            self.assertEqual(loaded["api"], "/process")
            self.assertEqual(loaded["adapter"], "elg_fs")

    def test_load_help_markdown_prefers_existing_help_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            help_md = os.path.join(tmp, "index.md")
            with open(help_md, "w", encoding="utf-8") as handle:
                handle.write("# Text Base Help\n\nhello")

            with patch.object(api, "HELP_SOURCE_CANDIDATES", [help_md]):
                markdown = api.load_help_markdown({"id": "md-base", "name": "Base Service"})

            self.assertIn("Text Base Help", markdown)

    def test_help_fallback_uses_descriptor(self):
        with patch.object(api, "HELP_SOURCE_CANDIDATES", []):
            markdown = api.load_help_markdown(
                {
                    "id": "md-base",
                    "name": "Base Service",
                    "description": "Base Python scripts for texts and images.",
                }
            )

        self.assertIn("Base Service", markdown)
        self.assertIn("md-base", markdown)
        self.assertIn("Base Python scripts for texts and images.", markdown)

    def test_health_endpoint_returns_ok(self):
        response = asyncio.run(api.health())
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["service"], "md-base")


if __name__ == "__main__":
    unittest.main()
