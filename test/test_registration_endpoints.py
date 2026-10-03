import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MD_PATH", os.getcwd())

import api
import service_registration


class TestRegistrationEndpoints(unittest.TestCase):
    def test_load_service_descriptor_reads_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            descriptor_path = os.path.join(tmp, "service.json")
            expected = {"id": "md-base", "api": "/process", "adapter": "elg_fs"}
            with open(descriptor_path, "w", encoding="utf-8") as handle:
                json.dump(expected, handle)

            loaded = service_registration.load_service_descriptor(tmp)

            self.assertEqual(loaded["id"], "md-base")
            self.assertEqual(loaded["api"], "/process")
            self.assertEqual(loaded["adapter"], "elg_fs")

    def test_load_help_markdown_prefers_existing_help_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            help_md = os.path.join(tmp, "index.md")
            with open(help_md, "w", encoding="utf-8") as handle:
                handle.write("# Text Base Help\n\nhello")

            markdown = service_registration.load_help_markdown({"id": "md-base", "name": "Base Service"}, [help_md])

            self.assertIn("Text Base Help", markdown)

    def test_help_fallback_uses_descriptor(self):
        markdown = service_registration.load_help_markdown(
            {
                "id": "md-base",
                "name": "Base Service",
                "description": "Base Python scripts for texts and images.",
            },
            [],
        )

        self.assertIn("Base Service", markdown)
        self.assertIn("md-base", markdown)
        self.assertIn("Base Python scripts for texts and images.", markdown)

    def _endpoint(self, path):
        return next(route.endpoint for route in api.app.routes if getattr(route, "path", None) == path)

    def test_health_endpoint_returns_ok(self):
        response = asyncio.run(self._endpoint("/health")())
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["service"], "md-text-base_fs")

    def test_config_reports_the_adapter_of_the_storage_mode(self):
        response = asyncio.run(self._endpoint("/config")())
        descriptor = json.loads(response.body)
        self.assertEqual(descriptor["id"], "md-text-base_fs")
        self.assertEqual(descriptor["adapter"], "elg_fs" if api.is_disk_mode() else "elg")


if __name__ == "__main__":
    unittest.main()
