"""Chat and retrieval checks with mocked cloud responses; no Vertex calls."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli


class ChatTests(unittest.TestCase):
    def results(self, documents=None):
        return {
            "documents": [["Check brakes and tires before riding."] if documents is None else documents],
            "metadatas": [[{"title": "Preparation", "region": "US", "scope_note": "General advice"}]],
            "distances": [[0.1]],
        }

    def test_chat_returns_answer_and_passes_evidence_to_base_model(self):
        model = Mock()
        model.models.generate_content.return_value = SimpleNamespace(text="  Check brakes and tires.  ")
        with patch.object(cli, "retrieve", return_value=self.results()) as retrieve:
            with patch.object(cli, "llm_client", return_value=model):
                answer = cli.chat("markdown-split", "How should I prepare?", 3)
        self.assertEqual(answer, "Check brakes and tires.")
        retrieve.assert_called_once_with("markdown-split", "How should I prepare?", 3)
        call = model.models.generate_content.call_args.kwargs
        self.assertEqual(call["model"], cli.GENERATIVE_MODEL)
        self.assertIn("How should I prepare?", call["contents"])
        self.assertIn("Check brakes and tires", call["contents"])
        self.assertIn("region: US", call["contents"])
        self.assertEqual(call["config"].system_instruction, cli.CHAT_SYSTEM_INSTRUCTION)

    def test_no_passages_does_not_call_generation(self):
        with patch.object(cli, "retrieve", return_value=self.results([" "])):
            with patch.object(cli, "llm_client") as model:
                answer = cli.chat("markdown-split", "How should I prepare?")
        self.assertIn("not return enough information", answer)
        model.assert_not_called()

    def test_empty_model_answer_is_reported(self):
        model = Mock()
        model.models.generate_content.return_value = SimpleNamespace(text=None)
        with patch.object(cli, "retrieve", return_value=self.results()):
            with patch.object(cli, "llm_client", return_value=model):
                with self.assertRaisesRegex(RuntimeError, "no answer text"):
                    cli.chat("markdown-split", "How should I prepare?")

    def test_retrieve_caps_results_and_uses_query_embedding(self):
        collection = Mock()
        collection.count.return_value = 2
        collection.query.return_value = self.results()
        client = Mock()
        client.get_collection.return_value = collection
        vector = [1 / 16] * 256
        with patch.object(cli, "chroma_client", return_value=client):
            with patch.object(cli, "generate_embeddings", return_value=[vector]) as embed:
                result = cli.retrieve("markdown-split", "How should I prepare?", 5)
        embed.assert_called_once_with(["How should I prepare?"], "RETRIEVAL_QUERY")
        self.assertEqual(collection.query.call_args.kwargs["n_results"], 2)
        self.assertEqual(result, self.results())

    def test_invalid_question_and_result_count_fail_before_network(self):
        with patch.object(cli, "chroma_client") as client:
            for question, limit in ((" ", 5), ("How?", 0)):
                with self.assertRaises(ValueError):
                    cli.retrieve("markdown-split", question, limit)
        client.assert_not_called()

    def test_empty_collection_does_not_call_vertex(self):
        collection = Mock()
        collection.count.return_value = 0
        client = Mock()
        client.get_collection.return_value = collection
        with patch.object(cli, "chroma_client", return_value=client):
            with patch.object(cli, "generate_embeddings") as embed:
                with self.assertRaisesRegex(ValueError, "empty"):
                    cli.retrieve("markdown-split", "How should I prepare?")
        embed.assert_not_called()

    def test_main_chat_dispatch_prints_answer(self):
        args = SimpleNamespace(chunk=False, embed=False, load=False, query=False,
                               chat=True, replace=False, chunk_type="markdown-split",
                               question="How should I prepare?", n_results=3)
        output = io.StringIO()
        with patch.object(cli, "chat", return_value="Check your brakes.") as chat:
            with contextlib.redirect_stdout(output):
                cli.main(args)
        chat.assert_called_once_with("markdown-split", "How should I prepare?", 3)
        self.assertIn("Check your brakes.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
