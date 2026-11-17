"""Network-free regression tests for the Roblox archive format and paging logic."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pagination.json").read_text(encoding="utf-8"))


class ArchiverTests(unittest.TestCase):
    def test_release_version_is_consistent(self):
        version = "v1.1.0"
        root = Path(__file__).resolve().parents[1]
        self.assertIn(version, app.APP_NAME)
        self.assertIn(version, (root / "archiver.ps1").read_text(encoding="utf-8-sig"))
        self.assertIn(version, (root / "run_archiver.bat").read_text(encoding="utf-8"))
        self.assertIn(version, (root / "README.md").read_text(encoding="utf-8"))

    def test_cookie_normalization(self):
        self.assertEqual(app._normalize_cookie(" .ROBLOSECURITY=abc; Path=/ "), "abc")
        self.assertEqual(app._normalize_cookie(" .ROBLOSECURITY = abc; Path=/ "), "abc")
        self.assertEqual(app._normalize_cookie(" .roblosecurity = abc "), "abc")
        with self.assertRaises(app.AuthenticationError):
            app._normalize_cookie("  ")

    def test_empty_prompt_reports_missing_cookie_not_prompt_failure(self):
        with patch.dict(app.os.environ, {"ROBLOSECURITY": ""}):
            with patch.object(app.getpass, "getpass", return_value="   "):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(app.AuthenticationError, "No \\.ROBLOSECURITY value was entered"):
                        app.main()
            with patch.object(app.getpass, "getpass", side_effect=OSError("terminal unavailable")):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(app.ArchiverError, "hidden credential prompt"):
                        app.main()

    def test_retry_after_is_bounded_and_handles_invalid_numbers(self):
        self.assertEqual(app._retry_wait("999999999", 4), 120.0)
        self.assertEqual(app._retry_wait("-8", 4, 2), 2)
        self.assertEqual(app._retry_wait("nan", 4), 4)
        self.assertEqual(app._retry_wait("Infinity", 4), 4)
        self.assertEqual(app._retry_wait("not a number", 4), 4)
        self.assertEqual(app._retry_wait(None, 4), 4)
        denied = HTTPError("https://apis.roblox.com/test", 429, "Rate limited",
                           {"Retry-After": "100000"}, io.BytesIO(b'{}'))
        with patch.object(app, "_open", side_effect=[denied, io.BytesIO(b'{"ok":true}')]):
            with patch.object(app.time, "sleep") as sleep:
                self.assertEqual(app.request_json("https://apis.roblox.com/test"), {"ok": True})
        sleep.assert_called_once_with(120.0)

    def test_public_requests_reuse_an_uncredentialed_opener(self):
        with patch.object(app, "_public_opener") as opener:
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            for _ in range(2):
                self.assertEqual(app.request_public_json("https://users.roblox.com/v1/users/1"), {"ok": True})
            self.assertEqual(opener.open.call_count, 2)

    def test_bad_public_user_response_does_not_abort_archive(self):
        with patch.object(app, "_public_opener") as opener:
            opener.open.return_value.__enter__.return_value.read.return_value = b"<html>temporary error</html>"
            with self.assertRaisesRegex(app.ArchiverError, "public API returned non-JSON"):
                app.request_public_json("https://users.roblox.com/v1/users/3")
            with contextlib.redirect_stdout(io.StringIO()):
                people = app.resolve_users_batch([3])
        self.assertEqual(people["3"]["name"], "Unknown user 3")
        self.assertIn("_lookupError", people["3"])
        self.assertEqual(opener.open.call_count, 3, "one direct request plus batch and per-user fallbacks")

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
        self.assertTrue(all(call.args[1]["pageSize"] == 30 for call in request.call_args_list))

    def test_partial_conversation_list_is_retained_after_later_page_failure(self):
        pages = [{"conversations": [{"id": "saved"}], "nextCursor": "next"},
                 {"error": "broken page"}]
        with patch.object(app, "request_json", side_effect=pages):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(app.PartialConversationsError) as caught:
                    app.fetch_all_conversations()
        self.assertEqual(list(caught.exception.conversations), ["saved"])
        self.assertIn("Malformed Roblox page", str(caught.exception))

    def test_optional_conversation_ids_are_retained_and_pagination_continues(self):
        pages = [
            {"conversations": [
                {"id": None, "name": "Missing ID", "messages": [{"content": "preview"}]},
                {"id": "known", "name": "Known"}], "nextCursor": "next"},
            {"conversations": [{"name": "Another missing ID"}]},
        ]
        with patch.object(app, "request_json", side_effect=pages) as request:
            with contextlib.redirect_stdout(io.StringIO()):
                conversations = app.fetch_all_conversations()
        self.assertEqual(set(conversations), {"__missing-id-p1-i1", "known", "__missing-id-p2-i1"})
        self.assertEqual(conversations["__missing-id-p1-i1"]["messages"][0]["content"], "preview")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args[1]["include_user_data"] == "true" for call in request.call_args_list))

    def test_repeated_conversation_cursor_fails_closed(self):
        pages = [{"conversations": [], "nextCursor": "same"},
                 {"conversations": [], "nextCursor": "same"}]
        with patch.object(app, "request_json", side_effect=pages):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(app.ArchiverError, "cursor"):
                    app.fetch_all_conversations()

    def test_conversation_page_requires_a_real_array(self):
        for page in ({"error": "temporary failure"}, {"conversations": None},
                     {"conversations": {"items": []}}):
            with self.subTest(page=page), patch.object(app, "request_json", return_value=page):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(app.ArchiverError, "Malformed Roblox page"):
                        app.fetch_all_conversations()

    def test_invalid_collection_members_are_not_silently_skipped(self):
        with patch.object(app, "request_json", return_value={"conversations": [{"id": "one"}, None]}):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(app.ArchiverError, "items must be objects"):
                    app.fetch_all_conversations()
        pages = [{"messages": [{"content": "retained"}], "nextCursor": "next"},
                 {"messages": [{"content": "valid"}, "invalid"]}]
        with patch.object(app, "request_json", side_effect=pages):
            with self.assertRaisesRegex(app.PartialMessagesError, "items must be objects") as caught:
                app.fetch_all_messages("one")
        self.assertEqual(caught.exception.messages, [{"content": "retained"}])

    def test_nested_conversation_items_are_accepted(self):
        with patch.object(app, "request_json", return_value={
            "data": {"conversations": [{"id": "chat"}]}}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(set(app.fetch_all_conversations()), {"chat"})

    def test_invalid_later_page_retains_earlier_messages(self):
        pages = [{"messages": [{"content": "saved"}], "nextCursor": "page-2"},
                 {"message": "temporary failure"}]
        with patch.object(app, "request_json", side_effect=pages):
            with self.assertRaisesRegex(app.PartialMessagesError, "Malformed Roblox page") as caught:
                app.fetch_all_messages("chat")
        self.assertEqual(caught.exception.messages, [{"content": "saved"}])
        with patch.object(app, "request_json", return_value={"messages": None}):
            with self.assertRaises(app.ArchiverError) as first:
                app.fetch_all_messages("chat")
        self.assertNotIsInstance(first.exception, app.PartialMessagesError)

    def test_nested_batch_users_avoid_redundant_individual_calls(self):
        with patch.object(app, "request_public_json", return_value={
            "data": {"users": [{"id": 89, "name": "Found"}]}}) as request:
            with contextlib.redirect_stdout(io.StringIO()):
                people = app.resolve_users_batch([89])
        self.assertEqual(people["89"]["name"], "Found")
        self.assertEqual(request.call_count, 1)

    def test_final_message_page_is_retained(self):
        with patch.object(app, "request_json", side_effect=FIXTURE["messages"]) as request:
            messages = app.fetch_all_messages("first")
        self.assertEqual([item["content"] for item in messages], ["hello", "last message"])
        self.assertEqual(request.call_args_list[1].args[1]["cursor"], "page-2")
        self.assertTrue(all(call.args[1]["pageSize"] == 30 for call in request.call_args_list))

    def test_failed_later_message_page_preserves_earlier_messages(self):
        pages = [{"messages": [{"senderUserId": 31, "content": "already downloaded"}], "nextCursor": "next"},
                 app.ArchiverError("second page unavailable")]
        with patch.object(app, "request_json", side_effect=pages):
            with self.assertRaises(app.PartialMessagesError) as caught:
                app.fetch_all_messages("first")
        self.assertEqual(caught.exception.messages[0]["content"], "already downloaded")
        self.assertIn("second page unavailable", str(caught.exception))

    def test_authentication_error_after_first_page_still_aborts(self):
        pages = [{"messages": [{"content": "downloaded"}], "nextCursor": "next"},
                 app.AuthenticationError("session expired")]
        with patch.object(app, "request_json", side_effect=pages):
            with self.assertRaises(app.AuthenticationError):
                app.fetch_all_messages("first")

    def test_repeated_message_cursor_fails_closed(self):
        pages = [{"messages": [], "nextCursor": "same"},
                 {"messages": [], "nextCursor": "same"}]
        with patch.object(app, "request_json", side_effect=pages):
            with self.assertRaisesRegex(app.ArchiverError, "cursor"):
                app.fetch_all_messages("first")

    def test_nested_paging_fields(self):
        self.assertEqual(app._next_cursor({"data": {"nextPageCursor": " x "}}), "x")
        self.assertIsNone(app._next_cursor({"nextCursor": " "}))
        self.assertEqual(app._next_cursor({"next_cursor": None, "nextCursor": "next"}), "next")
        self.assertEqual(app._next_cursor({"next_cursor": " ", "data": {"nextPageCursor": " deep "}}), "deep")

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

    def test_consecutive_archive_exports_preserve_both_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user = {"id": 7, "name": "Example"}
            first = app.write_archive(root, user, {}, {"original": {"messages": [{"content": "old"}]}}, [])
            second = app.write_archive(root, user, {}, {"new": {"messages": [{"content": "new"}]}}, [])
            self.assertNotEqual(first, second, "exports must never share predictable second-resolution paths")
            self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["conversations"]["original"]["messages"][0]["content"], "old")
            self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["conversations"]["new"]["messages"][0]["content"], "new")
            self.assertEqual(len(list(root.glob("*.tmp"))), 0, "temporary files must be cleaned up")
            if os.name == "posix":
                self.assertEqual(first.stat().st_mode & 0o777, 0o600, "chat archives must be private on Unix")
                self.assertEqual(second.stat().st_mode & 0o777, 0o600)

    def test_main_preserves_idless_conversation_and_fetches_known_ids(self):
        raw = {"__missing-id-p1-i1": {"id": None, "name": "No ID",
                    "messages": [{"sender_user_id": 11, "content": "preview"}]},
               "__missing-id-p1-i2": {"id": None, "name": "Preview only",
                    "preview_message": {"sender_user_id": 12, "content": "preview fallback"}},
               "known": {"id": "known", "name": "Known conversation"}}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value=raw))
            fetch = stack.enter_context(patch.object(app, "fetch_all_messages", return_value=[{"content": "full"}]))
            stack.enter_context(patch.object(app, "resolve_users_batch", return_value={}))
            with tempfile.TemporaryDirectory() as folder:
                stack.enter_context(patch.object(app.Path, "cwd", return_value=Path(folder)))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(app.main(), 0)
                files = list(Path(folder).glob("roblox-chat-archive-*.json"))
                self.assertEqual(len(files), 1)
                archive = json.loads(files[0].read_text(encoding="utf-8"))
        fetch.assert_called_once_with("known")
        unknown = archive["conversations"]["__missing-id-p1-i1"]
        self.assertEqual(unknown["_archiveStatus"], "missing-id")
        self.assertEqual(unknown["messages"][0]["content"], "preview")
        self.assertEqual(archive["conversations"]["__missing-id-p1-i2"]["messages"][0]["content"], "preview fallback")
        self.assertEqual(archive["conversations"]["known"]["messages"][0]["content"], "full")
        self.assertEqual(archive["archiveMeta"]["missingIdConversations"], 2)
        self.assertEqual(len(archive["archiveMeta"]["failedConversations"]), 2)

    def test_main_saves_partial_conversation_list_and_marks_archive(self):
        conversation = {"id": "partial", "name": "Only known"}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            for name in ("_init_session", "refresh_session"):
                stack.enter_context(patch.object(app, name))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", side_effect=app.PartialConversationsError(
                "pagination failed", {"partial": conversation})))
            stack.enter_context(patch.object(app, "fetch_all_messages", return_value=[{"content": "retained"}]))
            stack.enter_context(patch.object(app, "resolve_users_batch", return_value={}))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        self.assertEqual(writer.call_args.args[3]["partial"]["messages"][0]["content"], "retained")
        self.assertEqual(writer.call_args.kwargs["conversation_list_error"], "pagination failed")

        with tempfile.TemporaryDirectory() as directory:
            result = app.write_archive(Path(directory), {"id": 7, "name": "Example"}, {},
                {"partial": {"messages": [{"content": "retained"}]}}, [], conversation_list_error="pagination failed")
            payload = json.loads(result.read_text(encoding="utf-8"))
        self.assertTrue(payload["archiveMeta"]["conversationListIncomplete"])
        self.assertEqual(payload["archiveMeta"]["conversationListError"], "pagination failed")

    def test_main_saves_partial_messages_and_marks_failure(self):
        conversation = {"id": "chat", "name": "Somebody"}
        saved = {}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            for name in ("_init_session", "refresh_session"):
                stack.enter_context(patch.object(app, name))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value={"chat": conversation}))
            stack.enter_context(patch.object(app, "fetch_all_messages", side_effect=app.PartialMessagesError(
                "later page failed", [{"senderUserId": 33, "content": "saved message"}])))
            stack.enter_context(patch.object(app, "resolve_users_batch", return_value={}))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        saved = writer.call_args.args[3]["chat"]
        self.assertEqual(saved["_archiveStatus"], "partial-error")
        self.assertEqual(saved["messages"][0]["content"], "saved message")
        self.assertEqual(writer.call_args.args[4][0]["conversationId"], "chat")

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

    def test_optional_refresh_failures_do_not_abort_authenticated_requests(self):
        with patch.object(app, "_open", side_effect=URLError("temporary outage")):
            self.assertIsNone(app.refresh_session())
        forbidden = HTTPError("https://auth.roblox.com/v1/session/refresh", 403,
                              "Forbidden", {"x-csrf-token": "sample-token"}, io.BytesIO(b""))
        still_forbidden = HTTPError("https://auth.roblox.com/v1/session/refresh", 403,
                                    "Forbidden", {}, io.BytesIO(b""))
        old_token = app._csrf_token
        try:
            with patch.object(app, "_open", side_effect=[forbidden, still_forbidden]) as request:
                self.assertIsNone(app.refresh_session())
                self.assertEqual(request.call_count, 2)
            denied = HTTPError("https://auth.roblox.com/v1/session/refresh", 401,
                               "Unauthorized", {}, io.BytesIO(b""))
            with patch.object(app, "_open", side_effect=denied):
                with self.assertRaises(app.AuthenticationError):
                    app.refresh_session()
        finally:
            app._csrf_token = old_token

    def test_authenticated_request_refreshes_once_on_401(self):
        denied = HTTPError("https://apis.roblox.com/test", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        with patch.object(app, "_open", side_effect=[denied, io.BytesIO(b'{"ok":true}')]):
            with patch.object(app, "refresh_session") as refresh:
                self.assertEqual(app.request_json("https://apis.roblox.com/test"), {"ok": True})
        refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
