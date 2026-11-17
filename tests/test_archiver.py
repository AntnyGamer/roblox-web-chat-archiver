"""Network-free regression tests for the Roblox archive format and paging logic."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import gc
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import weakref
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pagination.json").read_text(encoding="utf-8"))


class ArchiverTests(unittest.TestCase):
    def test_release_version_is_consistent(self):
        version = "v2.0.0"
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
            with patch.object(app._archive_abort, "wait", return_value=False) as sleep:
                self.assertEqual(app.request_json("https://apis.roblox.com/test"), {"ok": True})
        sleep.assert_called_once_with(120.0)

    def test_cancel_wakes_authenticated_retry_waits_without_another_request(self):
        for error in (
            HTTPError("https://apis.roblox.com/test", 429, "Limited", {"Retry-After": "120"}, io.BytesIO(b"{}")),
            HTTPError("https://apis.roblox.com/test", 503, "Unavailable", {}, io.BytesIO(b"{}")),
            URLError("offline"),
        ):
            with self.subTest(error=error):
                app._archive_abort.clear()
                entered = threading.Event()
                wait = app._archive_abort.wait

                def retry_wait(seconds):
                    entered.set()
                    return wait(seconds)

                try:
                    with patch.object(app, "_open", side_effect=error) as request, \
                         patch.object(app._archive_abort, "wait", side_effect=retry_wait), \
                         contextlib.redirect_stdout(io.StringIO()), \
                         ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(app.fetch_all_messages, "chat")
                        waiting = entered.wait(2)
                        app._archive_abort.set()
                        self.assertTrue(waiting, "The request never reached its retry wait")
                        with self.assertRaises(app.AuthenticationError):
                            future.result(timeout=2)
                        self.assertEqual(request.call_count, 1)
                finally:
                    app._archive_abort.clear()

    def test_cancel_during_pacing_prevents_transport_request(self):
        app._archive_abort.clear()
        entered = threading.Event()
        wait = app._archive_abort.wait

        def pacing_wait(seconds):
            entered.set()
            return wait(seconds)

        try:
            with patch.object(app, "_opener") as opener, \
                 patch.object(app, "_last_request_at", time.monotonic()), \
                 patch.object(app, "MIN_REQUEST_INTERVAL", 30), \
                 patch.object(app._archive_abort, "wait", side_effect=pacing_wait), \
                 ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(app._open, None)
                waiting = entered.wait(2)
                app._archive_abort.set()
                self.assertTrue(waiting)
                with self.assertRaises(app.AuthenticationError):
                    future.result(timeout=2)
                opener.open.assert_not_called()
        finally:
            app._archive_abort.clear()

    def test_consumed_download_results_are_released_before_saving(self):
        class Messages(list):
            pass

        references = []

        def fetch(cid):
            messages = Messages([{"id": cid, "content": "preserved"}])
            references.append(weakref.ref(messages))
            return messages

        def save(output, user, people, conversations, failures, **kwargs):
            gc.collect()
            self.assertLessEqual(sum(ref() is not None for ref in references), 1,
                                 "Consumed futures retained raw message collections")
            self.assertEqual(len(conversations), 12)
            self.assertTrue(all(c["messages"][0]["content"] == "preserved" for c in conversations.values()))
            return Path("unused.json")

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value={
                str(i): {"id": str(i)} for i in range(12)}))
            stack.enter_context(patch.object(app, "fetch_all_messages", side_effect=fetch))
            stack.enter_context(patch.object(app, "resolve_users_batch", return_value={}))
            stack.enter_context(patch.object(app, "write_archive", side_effect=save))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)

    def test_concurrent_downloads_share_one_rate_limit(self):
        starts = []
        active = 0
        peak = 0
        guard = threading.Lock()

        class FakeOpener:
            def open(self, request, timeout=None):
                nonlocal active, peak
                with guard:
                    starts.append(time.monotonic())
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.09)
                with guard:
                    active -= 1
                return object()

        with patch.object(app, "_opener", FakeOpener()), \
             patch.object(app, "_last_request_at", 0.0), \
             patch.object(app, "MIN_REQUEST_INTERVAL", 0.025):
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: app._open(None), range(3)))
        self.assertEqual(len(results), 3)
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(peak, 2, "Independent requests should overlap their network wait")
        self.assertTrue(all(b - a >= 0.018 for a, b in zip(starts, starts[1:])),
                        "Parallel requests must not bypass the global rate limiter")

    def test_concurrent_conversation_fetch_retains_all_messages(self):
        gate = threading.Event()
        lock = threading.Lock()
        running = set()

        def fetch(cid):
            with lock:
                running.add(cid)
                if len(running) >= 2:
                    gate.set()
            if not gate.wait(4):
                raise AssertionError("Conversation fetches never overlapped")
            return [{"id": cid + "-1", "senderUserId": 21, "content": cid}]

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value={
                "one": {"id": "one"}, "two": {"id": "two"}}))
            stack.enter_context(patch.object(app, "fetch_all_messages", side_effect=fetch))
            stack.enter_context(patch.object(app, "resolve_users_batch", return_value={}))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        self.assertEqual(set(running), {"one", "two"})
        self.assertEqual(writer.call_args.args[3]["one"]["messages"][0]["content"], "one")
        self.assertEqual(writer.call_args.args[3]["two"]["messages"][0]["content"], "two")

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

    def test_verified_inventory_retains_friends_from_one_pass_and_all_channels(self):
        pages = [
            {"conversations": [
                {"id": None, "source": "friends", "type": "one_to_one",
                 "participant_user_ids": [21, 99], "name": "Friend",
                 "user_data": {"99": {"display_name": "Old friend"}}},
                {"id": "a", "source": "channels", "name": "First"}],
             "nextCursor": "next"},
            {"conversations": [{"id": "b", "source": "channels"}]},
            {"conversations": [{"id": "a", "source": "channels", "name": "First revised"}],
             "nextCursor": "next"},
            {"conversations": [
                {"id": None, "source": "friends", "type": "one_to_one",
                 "participant_user_ids": [99, 21], "name": "Friend"},
                {"id": "c", "source": "channels"}]},
        ]
        with patch.object(app, "request_json", side_effect=pages) as request:
            with contextlib.redirect_stdout(io.StringIO()):
                conversations = app.fetch_all_conversations(verify=True)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(len(conversations), 4)
        self.assertEqual(conversations["a"]["name"], "First revised")
        self.assertEqual(conversations["__missing-id-p1-i1"]["user_data"]["99"]["display_name"], "Old friend")

    def test_verified_inventory_recovers_after_one_pass_fails(self):
        pages = [
            {"conversations": [{"id": "first"}], "nextCursor": "next"},
            {"unexpected": True},
            {"conversations": [{"id": "second"}]},
        ]
        with patch.object(app, "request_json", side_effect=pages):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(app.PartialConversationsError) as caught:
                    app.fetch_all_conversations(verify=True)
        self.assertEqual(set(caught.exception.conversations), {"first", "second"})

    def test_different_embedded_messages_survive_inventory_overlap(self):
        old = {"chat": {"id": "chat", "messages": [
            {"id": "first", "content": "old content", "meta": {"sender": "Alice"}},
            {"id": "second", "content": "second"},
        ]}}
        new = {"chat": {"id": "chat", "messages": [
            {"id": "first", "content": None, "meta": {"sent": "2026"}},
            {"id": "third", "content": "third"},
        ]}}
        result = app._merge_conversation_snapshots([old, new])["chat"]["messages"]
        self.assertEqual([message["id"] for message in result],
                         ["first", "second", "third"])
        self.assertEqual(result[0]["content"], "old content")
        self.assertEqual(result[0]["meta"], {"sender": "Alice", "sent": "2026"})

    def test_embedded_idless_message_multiplicity_is_preserved(self):
        old = {"chat": {"id": "chat", "messages": [{"content": "same"}]}}
        new = {"chat": {"id": "chat", "messages": [{"content": "same"}, {"content": "same"}]}}
        merged = app._merge_conversation_snapshots([old, new])["chat"]["messages"]
        self.assertEqual(len(merged), 2, "The second identical ID-less message must not disappear")

    def test_idless_embedded_messages_require_exact_match_to_deduplicate(self):
        old = {"c": {"id": "c", "messages": [
            {"content": "same"}, {"content": "same"}
        ]}}
        new = {"c": {"id": "c", "messages": [
            {"content": "same"}, {"content": "different"}
        ]}}
        result = app._merge_conversation_snapshots([old, new])["c"]["messages"]
        self.assertEqual(result, [{"content": "same"}, {"content": "same"},
                                  {"content": "different"}])

    def test_participant_sets_keep_members_from_both_pages(self):
        pages = [
            {"conversations": [{"id": "chat", "participant_user_ids": [12, 21]}],
             "nextCursor": "next"},
            {"conversations": [{"id": "chat", "participant_user_ids": [21, 99]}]},
        ]
        with patch.object(app, "request_json", side_effect=pages):
            with contextlib.redirect_stdout(io.StringIO()):
                found = app.fetch_all_conversations()
        self.assertEqual(found["chat"]["participant_user_ids"], [12, 21, 99])

    def test_idless_groups_with_equal_members_are_not_combined(self):
        shared = {"source": "friends", "type": "group", "participant_user_ids": [12, 21]}
        snapshots = [
            {"__missing-id-p1-i1": {**shared, "created_at": "2025-01-01"}},
            {"__missing-id-p1-i1": {**shared, "created_at": "2026-01-01"}},
        ]
        result = app._merge_conversation_snapshots(snapshots)
        self.assertEqual(len(result), 2)

    def test_repeated_conversation_keeps_nested_user_metadata(self):
        pages = [
            {"conversations": [{"id": "chat", "user_data": {"99": {
                "display_name": "Best friend", "is_verified": True}},
                "preview_message": {"content": "Previous preview", "sent": "old"}}],
             "nextCursor": "page-2"},
            {"conversations": [{"id": "chat", "user_data": {"99": {
                "display_name": None, "combined_name": "Friend"}},
                "preview_message": {"content": None, "sent": "new"}}]},
        ]
        with patch.object(app, "request_json", side_effect=pages):
            with contextlib.redirect_stdout(io.StringIO()):
                result = app.fetch_all_conversations()
        self.assertEqual(len(result), 1)
        profile = result["chat"]["user_data"]["99"]
        self.assertEqual(profile["display_name"], "Best friend")
        self.assertTrue(profile["is_verified"])
        self.assertEqual(profile["combined_name"], "Friend")
        self.assertEqual(result["chat"]["preview_message"]["content"], "Previous preview")
        self.assertEqual(result["chat"]["preview_message"]["sent"], "old")

    def test_different_preview_ids_never_mix_text_and_time(self):
        old = {"chat": {"id": "chat", "preview_message": {
            "id": "old", "content": "old text", "sent": "2020"}}}
        new = {"chat": {"id": "chat", "preview_message": {
            "id": "new", "content": "new text", "sent": "2026"}}}
        result = app._merge_conversation_snapshots([old, new])["chat"]["preview_message"]
        self.assertEqual(result, {"id": "new", "content": "new text", "sent": "2026"})

        newest_blank = {"chat": {"id": "chat", "preview_message": {
            "id": "new", "content": None, "sent": "2026"}}}
        result = app._merge_conversation_snapshots([old, newest_blank])["chat"]["preview_message"]
        self.assertEqual(result, {"id": "old", "content": "old text", "sent": "2020"})

    def test_repeated_messages_keep_enriched_details(self):
        pages = [
            {"messages": [{"id": "message", "senderUserId": 12,
                           "content": "Original", "sent": "2026-10-01"}],
             "nextCursor": "next"},
            {"messages": [{"id": "message", "senderUserId": None,
                           "content": None, "extra": "new details"}]},
        ]
        with patch.object(app, "request_json", side_effect=pages):
            result = app.fetch_all_messages("chat")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Original")
        self.assertEqual(result[0]["extra"], "new details")
        self.assertEqual(result[0]["senderUserId"], 12)

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

    def test_overlapping_message_pages_do_not_duplicate_ids(self):
        pages = [
            {"messages": [{"id": "first", "content": "A"}], "nextCursor": "next"},
            {"messages": [{"id": "first", "content": "A"}, {"id": "second", "content": "B"}]},
        ]
        with patch.object(app, "request_json", side_effect=pages):
            self.assertEqual([m["id"] for m in app.fetch_all_messages("chat")], ["first", "second"])

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

    def test_embedded_profiles_skip_redundant_lookup_and_allow_incomplete_fallback(self):
        convo = {"id": "chat", "participantUserIds": [21, 99, 100],
                 "user_data": {
                     "99": {"id": 99, "name": "Known", "display_name": "Friend", "is_verified": True},
                     "100": {"id": 100, "name": "Incomplete", "display_name": ""},
                 }}
        embedded = app._embedded_people([convo])
        self.assertEqual(list(embedded), ["99"])
        self.assertEqual(embedded["99"]["displayName"], "Friend")
        self.assertTrue(embedded["99"]["hasVerifiedBadge"])

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value={"chat": convo}))
            stack.enter_context(patch.object(app, "fetch_all_messages", return_value=[
                {"id": "msg", "senderUserId": 99, "content": "Hello"}]))
            lookup = stack.enter_context(patch.object(app, "resolve_users_batch", return_value={
                "100": {"name": "Resolved", "displayName": "Resolved", "targetId": 100}}))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        self.assertEqual(set(lookup.call_args.args[0]), {100})
        self.assertEqual(writer.call_args.args[2]["99"]["name"], "Known")
        self.assertEqual(writer.call_args.args[2]["100"]["name"], "Resolved")

    def test_deleted_embedded_profiles_are_retried_but_valid_names_stay_cached(self):
        conversations = [
            {"id": "chat", "participantUserIds": [99, 100],
             "user_data": {
                 "99": {"name": "KnownUser", "display_name": "Known"},
                 "100": {"name": "Account Deleted", "display_name": "Account Deleted"},
                 "101": {"name": " account deleted ", "display_name": "Unknown"},
             }},
            {"id": "other", "user_data": {
                "99": {"name": "Account Deleted", "display_name": "Account Deleted"}}},
        ]
        cached = app._embedded_people(conversations)
        self.assertEqual(cached["99"]["name"], "KnownUser")
        self.assertTrue(app._placeholder_profile(cached["100"]))
        self.assertTrue(app._placeholder_profile(cached["101"]))

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value={
                "chat": conversations[0], "other": conversations[1]}))
            stack.enter_context(patch.object(app, "fetch_all_messages", return_value=[]))
            lookup = stack.enter_context(patch.object(app, "resolve_users_batch", return_value={
                "100": {"name": "RecoveredUser", "displayName": "Recovered", "targetId": 100},
                "101": {"name": "Unknown user 101", "displayName": "Unknown user 101",
                        "targetId": 101, "_lookupError": "404"},
            }))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        self.assertEqual(set(lookup.call_args.args[0]), {100, 101})
        profiles = writer.call_args.args[2]
        self.assertEqual(profiles["99"]["name"], "KnownUser")
        self.assertEqual(profiles["100"]["name"], "RecoveredUser")
        self.assertEqual(profiles["101"]["name"].strip().casefold(), "account deleted",
                         "Failed lookups should not discard the existing name placeholder")

    def test_bulk_placeholder_is_retried_at_individual_endpoint(self):
        calls = []

        def api(url, method="GET", body=None):
            calls.append((method, url))
            if method == "POST":
                return {"data": [
                    {"id": 99, "name": "ValidUser", "displayName": "Valid"},
                    {"id": 100, "name": "Account Deleted", "displayName": "Account Deleted"},
                    {"id": 101, "name": "Account Deleted", "displayName": "Account Deleted"},
                ]}
            if url.endswith("/100"):
                return {"id": 100, "name": "RestoredUser", "displayName": "Restored"}
            if url.endswith("/101"):
                raise app.ArchiverError("Unavailable")
            self.fail(f"Unexpected public user lookup: {url}")

        with patch.object(app, "request_public_json", side_effect=api):
            with contextlib.redirect_stdout(io.StringIO()):
                found = app.resolve_users_batch([99, 100, 101])
        self.assertEqual(found["99"]["name"], "ValidUser")
        self.assertEqual(found["100"]["name"], "RestoredUser")
        self.assertEqual(found["101"]["name"], "Account Deleted")
        self.assertEqual(sum(method == "POST" for method, _ in calls), 1)
        self.assertEqual({url.rsplit("/", 1)[-1] for method, url in calls if method == "GET"},
                         {"100", "101"})

    def test_parallel_public_profile_fallback_overlaps_requests(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        def public(url, method="GET", body=None):
            nonlocal active, peak
            if method == "POST":
                return {"data": []}
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.025)
            with lock:
                active -= 1
            uid = int(url.rsplit("/", 1)[-1])
            return {"id": uid, "name": f"User{uid}", "displayName": f"User{uid}"}
        with patch.object(app, "request_public_json", side_effect=public):
            with contextlib.redirect_stdout(io.StringIO()):
                found = app.resolve_users_batch(range(1, 7))
        self.assertEqual(len(found), 6)
        self.assertGreaterEqual(peak, 2, "Multiple independent missing profiles should be fetched concurrently")

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
        self.assertEqual(normalized["messages"][0]["createdAt"], "2026-01-01")
        self.assertEqual(normalized["_archiveStatus"], "failed")
        self.assertEqual(normalized["name"], "My chat")
        with tempfile.TemporaryDirectory() as directory:
            result = app.write_archive(Path(directory), {"id": 7, "name": "Example"},
                {}, {"first": normalized}, [{"conversationId": "first", "error": "transient error"}])
            archive = json.loads(result.read_text(encoding="utf-8"))
            self.assertFalse(result.with_suffix(result.suffix + ".tmp").exists())
            self.assertEqual(archive["archiveMeta"]["failedConversations"][0]["error"], "transient error")
            self.assertEqual(archive["conversations"]["first"]["messages"][0]["content"], "hello")
            self.assertEqual(archive["archiveMeta"]["previewOnlyConversations"], 0)

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

    def test_preview_ignores_empty_primary_payload_and_uses_available_alias(self):
        result = app._preview_message({"preview_message": {}, "previewMessage": {
            "id": "available", "content": "preserved"}})
        self.assertEqual(result["id"], "available")
        self.assertIsNone(app._preview_message({"preview_message": {}}))

    def test_main_preserves_preview_if_message_history_is_empty_or_unavailable(self):
        raw = {
            "empty": {"id": "empty", "preview_message": {"id": "preview-a", "content": "A"}},
            "failed": {"id": "failed", "previewMessage": {"id": "preview-b", "content": "B"}},
            "embedded": {"id": "embedded", "messages": [{"id": "saved", "content": "C"}]},
            "embedded-failed": {"id": "embedded-failed", "messages": [{"id": "saved-2", "content": "D"}]},
        }

        def fetch(cid):
            if cid in ("failed", "embedded-failed"):
                raise app.ArchiverError("Roblox returned an error")
            return []

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value=raw))
            stack.enter_context(patch.object(app, "fetch_all_messages", side_effect=fetch))
            stack.enter_context(patch.object(app, "resolve_users_batch", return_value={}))
            writer = stack.enter_context(patch.object(app, "write_archive", return_value=Path("archive.json")))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
        conversations = writer.call_args.args[3]
        self.assertEqual(conversations["empty"]["_archiveStatus"], "preview-only")
        self.assertEqual(conversations["empty"]["messages"][0]["content"], "A")
        self.assertEqual(conversations["failed"]["_archiveStatus"], "preview-only")
        self.assertEqual(conversations["failed"]["messages"][0]["content"], "B")
        self.assertEqual(conversations["failed"]["_archiveError"], "Roblox returned an error")
        self.assertEqual(conversations["embedded"]["_archiveStatus"], "preview-only")
        self.assertEqual(conversations["embedded"]["messages"][0]["content"], "C")
        self.assertEqual(conversations["embedded-failed"]["messages"][0]["content"], "D")
        self.assertEqual(conversations["embedded-failed"]["_archiveError"], "Roblox returned an error")
        with tempfile.TemporaryDirectory() as folder:
            archive_path = app.write_archive(Path(folder), {"id": 21, "name": "Me"},
                                             {}, conversations, [])
            record = json.loads(archive_path.read_text(encoding="utf-8"))
        self.assertEqual(record["archiveMeta"]["previewOnlyConversations"], 4)
        self.assertEqual(writer.call_args.args[4][0]["conversationId"], "failed")

    def test_interrupted_export_cancels_pending_requests(self):
        raw = {f"chat-{index}": {"id": f"chat-{index}"} for index in range(12)}

        def stop_early(cid):
            if cid == "chat-0":
                raise KeyboardInterrupt()
            app._archive_abort.wait(2)
            if app._archive_abort.is_set():
                raise app.AuthenticationError("Cancelled")
            return []

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(app.os.environ, {"ROBLOSECURITY": "fake"}))
            stack.enter_context(patch.object(app, "_init_session"))
            stack.enter_context(patch.object(app, "refresh_session"))
            stack.enter_context(patch.object(app, "request_json", return_value={"id": 21, "name": "Me"}))
            stack.enter_context(patch.object(app, "fetch_all_conversations", return_value=raw))
            stack.enter_context(patch.object(app, "fetch_all_messages", side_effect=stop_early))
            writer = stack.enter_context(patch.object(app, "write_archive"))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    app.main()
        self.assertTrue(app._archive_abort.is_set(), "Interrupts must stop queued downloads")
        writer.assert_not_called()
        app._archive_abort.clear()

    def test_authentication_failure_cancels_queued_message_fetches(self):
        app._archive_abort.clear()
        try:
            with patch.object(app, "fetch_all_messages", side_effect=app.AuthenticationError("expired")):
                with self.assertRaises(app.AuthenticationError):
                    app._fetch_archive_messages("expired")
            self.assertTrue(app._archive_abort.is_set())
            with patch.object(app, "request_json") as request:
                with self.assertRaisesRegex(app.AuthenticationError, "cancelling"):
                    app.fetch_all_messages("queued")
                request.assert_not_called()
        finally:
            app._archive_abort.clear()

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

    def test_concurrent_unauthorized_requests_refresh_session_only_once(self):
        barrier = threading.Barrier(2)
        attempts = {}
        lock = threading.Lock()

        def request(req):
            url = req.full_url
            with lock:
                count = attempts.get(url, 0) + 1
                attempts[url] = count
            if count == 1:
                barrier.wait(timeout=4)
                raise HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{}'))
            return io.BytesIO(b'{"ok":true}')

        with patch.object(app, "_open", side_effect=request), \
             patch.object(app, "_session_epoch", 0), \
             patch.object(app, "refresh_session") as refresh:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(app.request_json, [
                    "https://apis.roblox.com/test-a", "https://apis.roblox.com/test-b"]))
        self.assertEqual(results, [{"ok": True}, {"ok": True}])
        self.assertEqual(refresh.call_count, 1,
                         "Workers should reuse one successful session refresh")

    def test_authenticated_request_refreshes_once_on_401(self):
        denied = HTTPError("https://apis.roblox.com/test", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        with patch.object(app, "_open", side_effect=[denied, io.BytesIO(b'{"ok":true}')]):
            with patch.object(app, "refresh_session") as refresh:
                self.assertEqual(app.request_json("https://apis.roblox.com/test"), {"ok": True})
        refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

