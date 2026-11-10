"""Offline regression tests; synthetic vectors exist only inside a temporary directory."""
import contextlib
import importlib.util
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli


class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.inputs.mkdir()
        self.settings = patch.multiple(cli, INPUT_FOLDER=str(self.inputs), OUTPUT_FOLDER=str(self.outputs))
        self.settings.start()
        self.log = contextlib.redirect_stdout(io.StringIO())
        self.log.__enter__()

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.log.__exit__(None, None, None)
        self.settings.stop()
        self.tmp.cleanup()

    def write(self, name="one.md", text="---\nsource_id: GP-TEST\ntitle: Safety\n---\n# Rules\nStop."):
        p = self.inputs / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def chunk(self):
        cli.chunk("markdown-split")
        return cli.checked_manifest("markdown-split")

    def test_frontmatter_fallback_comments_and_breaks(self):
        p = self.write(text="\ufeff---\ntitle: Test\nquestion_ids: [A1, A2]\n---\nGo<br />slow<!-- page -->")
        meta, body = cli.read_md(p)
        self.assertEqual(meta["source_id"], "one")
        self.assertEqual(meta["question_ids"], "A1,A2")
        self.assertEqual(body, "Go\nslow")

    def test_malformed_header_fails(self):
        with self.assertRaises(ValueError):
            cli.read_md(self.write(text="---\ntitle: broken"))

    def test_short_content_and_title_metadata_survive(self):
        self.write()
        m = self.chunk()
        rows = cli.read_jsonl(self.outputs / next(iter(m["chunks"])))
        self.assertIn("Stop.", rows[0]["chunk"])
        self.assertEqual(rows[0]["section"], "Rules")
        self.assertEqual(len(rows[0]["input_sha256"]), 64)

    def test_recursive_input_and_duplicate_ids(self):
        self.write()
        self.write("nested/two.md")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.chunk()

    def test_long_sections_are_bounded(self):
        self.write(text="# Long\n" + "Ride slowly and check the route. " * 1000)
        m = self.chunk()
        self.assertLessEqual(m["length_max"], cli.CHUNK_SIZE)
        self.assertGreater(m["total_chunks"], 1)

    def test_changed_input_is_rejected_before_embedding(self):
        p = self.write()
        self.chunk()
        p.write_text("Changed")
        with self.assertRaisesRegex(ValueError, "Input files changed"):
            cli.embed("markdown-split")

    def test_single_input_task_and_normalization(self):
        calls = []
        def embed_content(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.] * 256)])
        fake = SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
        with patch.object(cli, "llm_client", return_value=fake):
            vectors = cli.generate_embeddings(["one", "two"], "RETRIEVAL_DOCUMENT")
        self.assertEqual([c["contents"] for c in calls], ["one", "two"])
        self.assertEqual(calls[0]["config"].task_type, "RETRIEVAL_DOCUMENT")
        self.assertFalse(calls[0]["config"].auto_truncate)
        self.assertAlmostEqual(sum(x * x for x in vectors[0]), 1.)

    def test_completed_embedding_manifest_and_tamper_detection(self):
        self.write()
        self.chunk()
        with patch.object(cli, "generate_embeddings", side_effect=lambda rows, task: [[1 / 16] * 256 for _ in rows]):
            cli.embed("markdown-split")
        m = cli.checked_manifest("markdown-split", embedded=True)
        target = self.outputs / next(iter(m["embeddings"]))
        target.write_text(target.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Embedding file changed"):
            cli.checked_manifest("markdown-split", embedded=True)

    def test_load_repeat_is_idempotent_and_replace_removes_stale(self):
        import chromadb
        client = chromadb.PersistentClient(path=str(self.root / "database"))
        self.clients.append(client)
        self.write()
        self.chunk()
        with patch.object(cli, "generate_embeddings", side_effect=lambda rows, task: [[1 / 16] * 256 for _ in rows]):
            cli.embed("markdown-split")
        with patch.object(cli, "chroma_client", return_value=client):
            cli.load("markdown-split")
            cli.load("markdown-split")
            collection = client.get_collection("greenpath-docs-markdown-split")
            self.assertEqual(collection.count(), 1)
            collection.add(ids=["stale"], documents=["stale"], embeddings=[[1 / 16] * 256])
            cli.load("markdown-split", replace=True)
            self.assertEqual(client.get_collection(collection.name).count(), 1)
            result = client.get_collection(collection.name).query(query_embeddings=[[1 / 16] * 256], n_results=1)
            self.assertEqual(result["metadatas"][0][0]["source_id"], "GP-TEST")


if __name__ == "__main__":
    unittest.main()
