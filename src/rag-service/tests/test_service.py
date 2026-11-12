"""API contract and generator checks; never call cloud services."""
import asyncio
import os
import time
import unittest
import io
import json
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import cli
import service
from generators import BaseGeminiGenerator, get_generator


def evidence():
    return {"ids": [["chunk-1"]], "documents": [["Check your brakes."]],
            "metadatas": [[{"source_id": "GP-TEST", "title": "Preparation", "region": "US"}]],
            "distances": [[0.1]]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"RAG_GENERATOR": "base", "GCP_PROJECT": ""})
        self.env.start()
        self.client = TestClient(service.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()

    def test_validation_happens_before_network(self):
        with patch.object(service, "retrieve") as retrieve:
            for data in ({"question": " "}, {"question": "x" * 2001},
                         {"question": "How?", "n_results": 0}, {"question": "How?", "n_results": 11},
                         {"question": "How?", "n_results": True}, {"question": "How?", "extra": 1}):
                self.assertEqual(self.client.post("/chat", json=data).status_code, 422)
        retrieve.assert_not_called()

    def test_health_is_independent_of_database_and_cloud(self):
        with patch.object(cli, "chroma_client") as db, patch.object(cli, "llm_client") as cloud:
            assert self.client.get("/health").json() == {"status": "ok"}
        db.assert_not_called()
        cloud.assert_not_called()

    def test_error_logs_link_request_and_trace_without_question_text(self):
        output = io.StringIO()
        trace = "0123456789abcdef0123456789abcdef"
        with patch.dict(os.environ, {"GCP_PROJECT": "test-project"}), redirect_stdout(output):
            response = self.client.post("/chat", json={"question": "PRIVATE QUESTION", "n_results": 0}, headers={"X-Cloud-Trace-Context": trace + "/1;o=1"})
        assert response.status_code == 422
        event = json.loads(output.getvalue().strip())
        assert event["status"] == 422
        assert event["request_id"] == response.headers["X-Request-ID"]
        assert event["logging.googleapis.com/trace"] == "projects/test-project/traces/" + trace
        assert "PRIVATE QUESTION" not in output.getvalue()

    def test_chat_retrieves_once_and_returns_metadata(self):
        generator = Mock()
        generator.generate.return_value = "Check brakes."
        service.app.state.generator = generator
        with patch.object(service, "retrieve", return_value=evidence()) as retrieve:
            response = self.client.post("/chat", json={"question": " How? "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(retrieve.call_count, 1)
        data = response.json()
        self.assertEqual(data["question"], "How?")
        self.assertEqual(data["answer"], "Check brakes.")
        self.assertEqual(data["sources"][0]["metadata"]["source_id"], "GP-TEST")
        generator.generate.assert_called_once_with("How?", evidence())

    def test_query_does_not_generate_answer(self):
        generator = Mock()
        service.app.state.generator = generator
        with patch.object(service, "retrieve", return_value=evidence()):
            response = self.client.post("/query", json={"question": "How?"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["answer"])
        generator.generate.assert_not_called()

    def test_ready_is_free_and_reports_missing_collection(self):
        with patch.object(service, "collection_status", return_value=42), patch.object(cli, "llm_client") as cloud:
            self.assertEqual(self.client.get("/ready").json()["count"], 42)
        cloud.assert_not_called()
        with patch.object(service, "collection_status", side_effect=ValueError("missing")):
            self.assertEqual(self.client.get("/ready").status_code, 503)

    def test_failure_is_sanitized(self):
        with patch.object(service, "retrieve", side_effect=RuntimeError("private credential path")):
            response = self.client.post("/chat", json={"question": "How?"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    def test_slow_dependency_returns_timeout(self):
        original_timeout = asyncio.timeout

        def slow_retrieval(request):
            time.sleep(0.05)
            return evidence()

        with patch.object(service, "retrieve", side_effect=slow_retrieval), patch.object(service.asyncio, "timeout", side_effect=lambda seconds: original_timeout(0.01)):
            response = self.client.post("/query", json={"question": "How?"})
        self.assertEqual(response.status_code, 504)

    def test_base_generator_reuses_existing_generation(self):
        with patch.object(cli, "answer_from_results", return_value="Answer") as generate:
            self.assertEqual(BaseGeminiGenerator().generate("How?", evidence()), "Answer")
        generate.assert_called_once_with("How?", evidence())

    def test_finetuned_and_unknown_generators_fail_clearly(self):
        for name, error in (("finetuned", NotImplementedError), ("unknown", ValueError)):
            with patch.dict(os.environ, {"RAG_GENERATOR": name}):
                with self.assertRaises(error):
                    get_generator()

    def test_existing_collection_is_never_loaded_or_replaced(self):
        with patch.dict(os.environ, {"RAG_LOAD_EXISTING": "true"}), patch.object(cli, "chroma_client"), patch.object(cli, "load") as load:
            with TestClient(service.app):
                pass
        load.assert_not_called()

    def test_startup_never_opens_local_data_or_contacts_chroma(self):
        # Even a stale legacy flag must not restore/rebuild data from the API.
        with patch.dict(os.environ, {"RAG_LOAD_EXISTING": "true"}), patch.object(cli, "chroma_client") as client, patch.object(cli, "load") as load, patch.object(cli, "generate_embeddings") as embed:
            with TestClient(service.app):
                pass
        client.assert_not_called()
        load.assert_not_called()
        embed.assert_not_called()

    def test_remote_chroma_configuration_uses_http_client(self):
        # Exercise the CLI's original client factory, wrapped by the web service.
        with patch.object(cli, "CHROMADB_HOST", "vectors.example.test"), patch.object(cli, "CHROMADB_PORT", 443), patch.object(cli, "CHROMADB_SSL", True), patch.object(cli.chromadb, "HttpClient") as factory:
            cli.chroma_client()
        factory.assert_called_once_with(host="vectors.example.test", port=443, ssl=True)


if __name__ == "__main__":
    unittest.main()
