"""Network-free regression tests for the Roblox archive format and paging logic."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pagination.json").read_text(encoding="utf-8"))


class ArchiverTests(unittest.TestCase):
    def test_release_version_is_consistent(self):
        version = "v1.0.1"
        root = Path(__file__).resolve().parents[1]
        self.assertIn(version, app.APP_NAME)
        self.assertIn(version, (root / "archiver.ps1").read_text(encoding="utf-8-sig"))
        self.assertIn(version, (root / "run_archiver.bat").read_text(encoding="utf-8"))
        self.assertIn(version, (root / "README.md").read_text(encoding="utf-8"))

    def test_cookie_normalization(self):
        self.assertEqual(app._normalize_cookie(" .ROBLOSECURITY=abc; Path=/ "), "abc")
        with self.assertRaises(app.AuthenticationError):
            app._normalize_cookie("  ")

    def test_participants_are_unique_and_keep_original_order(self):
        conversation = {"participants": [{"userId": 6}, "4", 6, "invalid", 0, {"targetId": 2}, 4]}
        self.assertEqual(app._participant_ids(conversation), [6, 4, 2])

    def test_final_conversation_page_is_retained(self):
        with patch.object(app, "request_json", side_effect=FIXTURE["conversations"]) as request:
            with contextlib.redirect_stdout(io.StringIO()):
                conversations = app.fetch_all_conversations()
        self.assertEqual(set(conversations), {"first", "last"})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[1]["cursor"], "page-2")

    def test_repeated_conversation_cursor_fails_closed(self):
        pages = [{"conversations": [], "nextCursor": "same"},
                 {"conversations": [], "nextCursor": "same"}]
        with patch.object(app, "request_json", side_effect=pages):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(app.ArchiverError, "cursor"):
                    app.fetch_all_conversations()

    def test_final_message_page_is_retained(self):
        with patch.object(app, "request_json", side_effect=FIXTURE["messages"]) as request:
            messages = app.fetch_all_messages("first")
        self.assertEqual([item["content"] for item in messages], ["hello", "last message"])
        self.assertEqual(request.call_args_list[1].args[1]["cursor"], "page-2")

    def test_repeated_message_cursor_fails_closed(self):
        pages = [{"messages": [], "nextCursor": "same"},
                 {"messages": [], "nextCursor": "same"}]
        with patch.object(app, "request_json", side_effect=pages):
            with self.assertRaisesRegex(app.ArchiverError, "cursor"):
                app.fetch_all_messages("first")

    def test_nested_paging_fields(self):
        self.assertEqual(app._next_cursor({"data": {"nextPageCursor": " x "}}), "x")
        self.assertIsNone(app._next_cursor({"nextCursor": " "}))

    def test_user_batches_and_missing_user_fallback(self):
        def users(url, method="GET", body=None):
            if method == "POST":
                return {"data": [{"id": uid, "name": f"user{uid}"} for uid in body["userIds"] if uid != 102]}
            raise app.ArchiverError("offline fixture has no user 102")
        with patch.object(app, "request_public_json", side_effect=users) as request:
            with contextlib.redirect_stdout(io.StringIO()):
                people = app.resolve_users_batch(range(1, 206))
        self.assertEqual(len(people), 205)
        self.assertEqual(people["205"]["name"], "user205")
        self.assertIn("_lookupError", people["102"])
        self.assertEqual(sum(call.kwargs.get("method") == "POST" for call in request.call_args_list), 3)

    def test_normalized_archive_preserves_failed_conversation(self):
        raw = {"conversationId": "first", "name": "My chat"}
        normalized = app.normalize_conversation(raw, "first",
            [{"senderUserId": 10, "text": "hello", "createdAt": "2026-01-01"}],
            archive_status="failed", archive_error="transient error")
        self.assertEqual(normalized["title"], "My chat")
        self.assertEqual(normalized["messages"][0]["senderTargetId"], 10)
        self.assertEqual(normalized["messages"][0]["sent"], "2026-01-01")
        self.assertNotIn("createdAt", normalized["messages"][0])
        self.assertEqual(normalized["_archiveStatus"], "failed")
        self.assertNotIn("name", normalized)
        with tempfile.TemporaryDirectory() as directory:
            result = app.write_archive(Path(directory), {"id": 7, "name": "Example"},
                {}, {"first": normalized}, [{"conversationId": "first", "error": "transient error"}])
            archive = json.loads(result.read_text(encoding="utf-8"))
            self.assertFalse(result.with_suffix(result.suffix + ".tmp").exists())
            self.assertEqual(archive["archiveMeta"]["failedConversations"][0]["error"], "transient error")
            self.assertEqual(archive["conversations"]["first"]["messages"][0]["content"], "hello")

    def test_authenticated_user_is_not_fetched_again(self):
        conversation = {"id": "chat", "participantUserIds": [21, 99]}
        messages = [{"senderUserId": 21, "text": "hello"}]
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value={"chat": conversation}))
            stack.enter_context(patch.object(app, "fetch_all_messages", return_value=messages))
            lookup = stack.enter_context(patch.object(app, "resolve_users_batch", return_value={"99": {"name": "Other"}}))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        self.assertEqual(set(lookup.call_args.args[0]), {99})
        self.assertEqual(writer.call_args.args[2]["21"]["name"], "Me")
        self.assertEqual(writer.call_args.args[2]["0"]["name"], "Roblox")

    def test_authenticated_request_refreshes_once_on_401(self):
        denied = HTTPError("https://apis.roblox.com/test", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        with patch.object(app, "_open", side_effect=[denied, io.BytesIO(b'{"ok":true}')]):
            with patch.object(app, "refresh_session") as refresh:
                self.assertEqual(app.request_json("https://apis.roblox.com/test"), {"ok": True})
        refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
