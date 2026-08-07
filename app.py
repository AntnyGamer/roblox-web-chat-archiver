#!/usr/bin/env python3
"""Roblox Web Chat Archiver - 2026 compatibility refresh.

This is a local-only archiver. Your .ROBLOSECURITY value is sent only to
Roblox API hosts used by this program and is never written to the archive.
"""

from __future__ import annotations

import datetime as _dt
import getpass
from http.cookiejar import Cookie, CookieJar
import json
import os
from pathlib import Path
import time
import traceback
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

APP_NAME = "Roblox Web Chat Archiver (2026 refresh)"
CHAT_BASE = "https://apis.roblox.com/platform-chat-api/v1"
USERS_BASE = "https://users.roblox.com/v1"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 6
MIN_REQUEST_INTERVAL = 0.52

_last_request_at = 0.0
_opener = None
_csrf_token: str | None = None


class ArchiverError(RuntimeError):
    pass


class AuthenticationError(ArchiverError):
    pass


def _normalize_cookie(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    if value.lower().startswith(".roblosecurity="):
        value = value.split("=", 1)[1].strip()
    if ";" in value:
        value = value.split(";", 1)[0].strip()
    if not value:
        raise AuthenticationError("No .ROBLOSECURITY value was entered.")
    return value


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for item in errors:
                if isinstance(item, dict):
                    code = item.get("code")
                    msg = item.get("message") or item.get("userFacingMessage") or "Unknown Roblox API error"
                    parts.append(f"{code}: {msg}" if code is not None else str(msg))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        for key in ("message", "Message", "error", "Error"):
            if payload.get(key):
                return str(payload[key])
    return str(payload)[:400]


def _sleep_for_rate_limit() -> None:
    global _last_request_at
    delay = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if delay > 0:
        time.sleep(delay)


def _init_session(cookie: str) -> None:
    global _opener
    jar = CookieJar()
    jar.set_cookie(Cookie(
        version=0,
        name=".ROBLOSECURITY",
        value=cookie,
        port=None,
        port_specified=False,
        domain=".roblox.com",
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
        rfc2109=False,
    ))
    _opener = build_opener(HTTPCookieProcessor(jar))


def _open(req: Request):
    global _last_request_at
    if _opener is None:
        raise ArchiverError("Roblox session was not initialized.")
    _sleep_for_rate_limit()
    _last_request_at = time.monotonic()
    return _opener.open(req, timeout=REQUEST_TIMEOUT)


def refresh_session() -> None:
    """Ask Roblox to rotate/update .ROBLOSECURITY and retain Set-Cookie in the jar."""
    global _csrf_token
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        ),
    }
    if _csrf_token:
        headers["X-CSRF-TOKEN"] = _csrf_token

    req = Request("https://auth.roblox.com/v1/session/refresh", headers=headers, data=b"", method="POST")
    try:
        with _open(req):
            return
    except HTTPError as exc:
        if exc.code == 403:
            token = exc.headers.get("x-csrf-token") or exc.headers.get("X-CSRF-TOKEN")
            if token:
                _csrf_token = token
                headers["X-CSRF-TOKEN"] = token
                req = Request("https://auth.roblox.com/v1/session/refresh", headers=headers, data=b"", method="POST")
                try:
                    with _open(req):
                        return
                except HTTPError as retry_exc:
                    if retry_exc.code == 401:
                        raise AuthenticationError(
                            "Roblox rejected this .ROBLOSECURITY session while refreshing it (HTTP 401). "
                            "Copy the CURRENT cookie from the Roblox tab again; Roblox may have rotated it."
                        ) from retry_exc
                    raise
        if exc.code == 401:
            raise AuthenticationError(
                "Roblox rejected this .ROBLOSECURITY session while refreshing it (HTTP 401). "
                "Copy the CURRENT cookie from the Roblox tab again; Roblox may have rotated it."
            ) from exc
        # A refresh failure alone should not block a normal authenticated GET.
        return


def request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET JSON through the authenticated cookie session."""
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean_params:
            url = f"{url}?{urlencode(clean_params)}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        ),
    }
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with _open(Request(url, headers=headers, method="GET")) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ArchiverError(
                        f"Roblox returned a non-JSON response from {url}. "
                        f"Response started with: {raw[:160]!r}"
                    ) from exc
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = body
            detail = _error_message(payload)

            if exc.code == 401:
                if attempt == 1:
                    try:
                        refresh_session()
                        continue
                    except AuthenticationError:
                        pass
                raise AuthenticationError(
                    "Roblox rejected the login session (HTTP 401). The browser cookie may have "
                    "rotated since it was copied. Copy the CURRENT .ROBLOSECURITY value and retry."
                ) from exc
            if exc.code == 403:
                raise AuthenticationError(
                    f"Roblox refused the authenticated request (HTTP 403). Roblox said: {detail}"
                ) from exc
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After")
                try:
                    wait = max(2.0, float(retry_after)) if retry_after else min(30.0, 2 ** attempt)
                except ValueError:
                    wait = min(30.0, 2 ** attempt)
                print(f"\nRate limited by Roblox; retrying after {wait:g}s...", flush=True)
                time.sleep(wait)
                last_error = exc
                continue
            if 500 <= exc.code <= 599 and attempt < MAX_RETRIES:
                wait = min(20.0, 2 ** (attempt - 1))
                print(f"\nRoblox API returned HTTP {exc.code}; retrying in {wait:g}s...", flush=True)
                time.sleep(wait)
                last_error = exc
                continue
            raise ArchiverError(f"Roblox API error HTTP {exc.code}: {detail} ({url})") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break
            wait = min(20.0, 2 ** (attempt - 1))
            print(f"\nNetwork error; retrying in {wait:g}s...", flush=True)
            time.sleep(wait)

    raise ArchiverError(f"Could not reach Roblox after {MAX_RETRIES} attempts: {last_error}")


def request_public_json(url: str, method: str = "GET", body: Any = None) -> Any:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "RobloxWebChatArchiver/2026",
    }
    data: bytes | None = None
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")

    opener = build_opener()
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with opener.open(Request(url, headers=headers, data=data, method=method), timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After")
                try:
                    wait = max(1.0, float(retry_after)) if retry_after else min(20.0, 2 ** attempt)
                except ValueError:
                    wait = min(20.0, 2 ** attempt)
                time.sleep(wait)
                continue
            if 500 <= exc.code <= 599 and attempt < MAX_RETRIES:
                time.sleep(min(10.0, 2 ** (attempt - 1)))
                continue
            raise ArchiverError(f"Roblox public API error HTTP {exc.code}: {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break
            time.sleep(min(10.0, 2 ** (attempt - 1)))
    raise ArchiverError(f"Could not reach Roblox public API after {MAX_RETRIES} attempts: {last_error}")


def _nested_dicts(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, dict):
        yield data
        nested = data.get("data")
        if isinstance(nested, dict):
            yield nested


def _first_value(data: Any, *keys: str, default: Any = None) -> Any:
    for obj in _nested_dicts(data):
        for key in keys:
            if key in obj:
                return obj[key]
    return default


def _first_list(data: Any, *keys: str) -> list[Any]:
    value = _first_value(data, *keys, default=[])
    return value if isinstance(value, list) else []


def _next_cursor(data: Any) -> str | None:
    value = _first_value(data, "next_cursor", "nextCursor", "nextPageCursor", default=None)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _conversation_id(convo: dict[str, Any]) -> str | None:
    value = convo.get("id") or convo.get("conversation_id") or convo.get("conversationId")
    return str(value) if value not in (None, "") else None


def _message_sender_id(message: dict[str, Any]) -> int | None:
    value = message.get("sender_user_id") or message.get("senderUserId") or message.get("senderTargetId")
    if value is None and isinstance(message.get("sender"), dict):
        value = message["sender"].get("id") or message["sender"].get("userId")
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _message_content(message: dict[str, Any]) -> str:
    value = message.get("content")
    if value is None:
        value = message.get("text")
    if value is None:
        value = message.get("message")
    return "" if value is None else str(value)


def _message_time(message: dict[str, Any]) -> str:
    value = message.get("created_at") or message.get("createdAt") or message.get("sent") or message.get("timestamp")
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            pass
    return str(value)


def _participant_ids(convo: dict[str, Any]) -> list[int]:
    raw = convo.get("participant_user_ids") or convo.get("participantUserIds") or convo.get("participants") or []
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("id") or item.get("userId") or item.get("targetId")
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value and value not in result:
            result.append(value)
    return result


def _created_by(convo: dict[str, Any]) -> int | None:
    value = convo.get("created_by") or convo.get("createdBy")
    if isinstance(value, dict):
        value = value.get("id") or value.get("userId") or value.get("targetId")
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_all_conversations() -> dict[str, dict[str, Any]]:
    conversations: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    page = 0

    while True:
        page += 1
        data = request_json(f"{CHAT_BASE}/get-user-conversations", {"cursor": cursor})
        items = _first_list(data, "conversations", "Conversations", "items")
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = _conversation_id(item)
            if cid:
                conversations[cid] = item
        print(f"  conversations page {page}: +{len(items)} (total {len(conversations)})", flush=True)

        next_cursor = _next_cursor(data)
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise ArchiverError("Roblox returned the same conversation cursor twice; stopping to avoid an infinite loop.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return conversations


def fetch_all_messages(conversation_id: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while True:
        data = request_json(
            f"{CHAT_BASE}/get-conversation-messages",
            {"conversation_id": conversation_id, "cursor": cursor},
        )
        items = _first_list(data, "messages", "Messages", "items")
        messages.extend(item for item in items if isinstance(item, dict))

        next_cursor = _next_cursor(data)
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise ArchiverError(
                f"Roblox returned the same message cursor twice for conversation {conversation_id}; "
                "stopping that conversation to avoid an infinite loop."
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return messages


def resolve_users_batch(user_ids: Iterable[int]) -> dict[str, dict[str, Any]]:
    unique = sorted({int(uid) for uid in user_ids if int(uid) != 0})
    people: dict[str, dict[str, Any]] = {}

    for start in range(0, len(unique), 100):
        batch = unique[start:start + 100]
        print(f"  resolving users {start + 1}-{start + len(batch)} of {len(unique)}", flush=True)
        try:
            payload = request_public_json(
                f"{USERS_BASE}/users",
                method="POST",
                body={"userIds": batch, "excludeBannedUsers": False},
            )
            for user in _first_list(payload, "data", "users", "items"):
                if not isinstance(user, dict):
                    continue
                try:
                    uid = int(user.get("id") or user.get("userId") or user.get("targetId"))
                except (TypeError, ValueError):
                    continue
                if not uid:
                    continue
                name = str(user.get("name") or f"Unknown user {uid}")
                people[str(uid)] = {
                    "hasVerifiedBadge": bool(user.get("hasVerifiedBadge", False)),
                    "name": name,
                    "displayName": str(user.get("displayName") or name),
                    "targetId": uid,
                }
        except ArchiverError as exc:
            print(f"  Batch user lookup failed; falling back for this batch. ({exc})", flush=True)

        for uid in batch:
            if str(uid) in people:
                continue
            try:
                user = request_public_json(f"{USERS_BASE}/users/{uid}")
                name = str(user.get("name") or f"Unknown user {uid}") if isinstance(user, dict) else f"Unknown user {uid}"
                people[str(uid)] = {
                    "hasVerifiedBadge": bool(user.get("hasVerifiedBadge", False)) if isinstance(user, dict) else False,
                    "name": name,
                    "displayName": str(user.get("displayName") or name) if isinstance(user, dict) else name,
                    "targetId": uid,
                }
            except ArchiverError as exc:
                people[str(uid)] = {
                    "hasVerifiedBadge": False,
                    "name": f"Unknown user {uid}",
                    "displayName": f"Unknown user {uid}",
                    "targetId": uid,
                    "_lookupError": str(exc),
                }
    return people


def normalize_conversation(
    raw: dict[str, Any],
    cid: str,
    messages: list[dict[str, Any]],
    archive_status: str | None = None,
    archive_error: str | None = None,
) -> dict[str, Any]:
    title = raw.get("name") or raw.get("title") or raw.get("displayName") or f"Conversation {cid}"
    normalized_messages = []
    for raw_message in messages:
        sender_id = _message_sender_id(raw_message) or 0
        normalized = dict(raw_message)
        normalized["senderTargetId"] = sender_id
        normalized["sent"] = _message_time(raw_message)
        normalized["content"] = _message_content(raw_message)
        for key in ("sender_user_id", "senderUserId", "created_at", "createdAt"):
            normalized.pop(key, None)
        normalized_messages.append(normalized)

    normalized_convo = dict(raw)
    normalized_convo["id"] = raw.get("id", cid)
    normalized_convo["title"] = str(title)
    normalized_convo["messages"] = normalized_messages
    normalized_convo.pop("name", None)
    if archive_status:
        normalized_convo["_archiveStatus"] = archive_status
    if archive_error:
        normalized_convo["_archiveError"] = archive_error
    return normalized_convo


def write_archive(
    output_dir: Path,
    current_user: dict[str, Any],
    people: dict[str, dict[str, Any]],
    conversations: dict[str, dict[str, Any]],
    failures: list[dict[str, str]],
) -> Path:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_name = "".join(c for c in str(current_user.get("name", "user")) if c.isalnum() or c in "-_ ").strip() or "user"
    path = output_dir / f"roblox-chat-archive-{current_user['id']}-{safe_name}-{stamp}.json"

    metadata_only = sum(1 for convo in conversations.values() if not convo.get("messages"))
    payload = {
        "userId": current_user["id"],
        "people": people,
        "conversations": conversations,
        "archiveMeta": {
            "createdAt": _dt.datetime.now(tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "archiver": APP_NAME,
            "conversationCount": len(conversations),
            "metadataOnlyConversations": metadata_only,
            "failedConversations": failures,
        },
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(path)
    return path


def main() -> int:
    print("=" * 62)
    print(APP_NAME)
    print("=" * 62)
    print("This program reads your existing Roblox website chat history and")
    print("writes a local JSON archive. Your cookie is NOT saved to the file.")
    print("Never paste your .ROBLOSECURITY value into chats or websites.\n")

    env_cookie = os.environ.get("ROBLOSECURITY", "").strip()
    if env_cookie:
        cookie = _normalize_cookie(env_cookie)
        os.environ.pop("ROBLOSECURITY", None)
        print("Using .ROBLOSECURITY from the ROBLOSECURITY environment variable.")
    else:
        try:
            cookie = _normalize_cookie(getpass.getpass("Paste your .ROBLOSECURITY value (hidden): "))
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as exc:
            raise ArchiverError("Could not open a hidden credential prompt in this terminal.") from exc

    _init_session(cookie)
    cookie = ""

    print("\nPreparing 2026 Roblox session...", flush=True)
    refresh_session()
    print("Checking Roblox login...", flush=True)
    current_user = request_json(f"{USERS_BASE}/users/authenticated")
    if not isinstance(current_user, dict) or not current_user.get("id"):
        raise AuthenticationError(f"Roblox did not return a valid authenticated user: {_error_message(current_user)}")

    print(f"Logged in as {current_user.get('name', 'Unknown')} (ID {current_user['id']}).")
    print("\nFetching conversations...", flush=True)
    raw_conversations = fetch_all_conversations()
    print(f"Found {len(raw_conversations)} conversations.\n")

    normalized_conversations: dict[str, dict[str, Any]] = {}
    unknown_ids: set[int] = {int(current_user["id"])}
    failures: list[dict[str, str]] = []

    total = len(raw_conversations)
    for index, (cid, raw_convo) in enumerate(raw_conversations.items(), start=1):
        title = raw_convo.get("name") or raw_convo.get("title") or cid
        print(f"[{index}/{total}] Fetching {title!s}...", end=" ", flush=True)

        creator = _created_by(raw_convo)
        if creator:
            unknown_ids.add(creator)
        unknown_ids.update(_participant_ids(raw_convo))

        try:
            messages = fetch_all_messages(cid)
        except AuthenticationError:
            raise
        except ArchiverError as exc:
            error = str(exc)
            failures.append({"conversationId": cid, "title": str(title), "error": error})
            normalized_conversations[cid] = normalize_conversation(
                raw_convo, cid, [], archive_status="metadata-only-error", archive_error=error
            )
            print(f"FAILED; metadata saved ({error})")
            continue

        status = "metadata-only-empty-response" if not messages else None
        normalized = normalize_conversation(raw_convo, cid, messages, archive_status=status)
        normalized_conversations[cid] = normalized

        for message in normalized["messages"]:
            try:
                sender_id = int(message.get("senderTargetId", 0))
            except (TypeError, ValueError):
                sender_id = 0
            if sender_id:
                unknown_ids.add(sender_id)

        if messages:
            print(f"{len(messages)} messages")
        else:
            print("0 messages; metadata saved")

    print(f"\nResolving {len(unknown_ids)} users in batches...", flush=True)
    people = resolve_users_batch(unknown_ids)

    current_id = int(current_user["id"])
    people[str(current_id)] = {
        "hasVerifiedBadge": bool(current_user.get("hasVerifiedBadge", False)),
        "name": str(current_user.get("name") or current_id),
        "displayName": str(current_user.get("displayName") or current_user.get("name") or current_id),
        "targetId": current_id,
    }
    people["0"] = {
        "hasVerifiedBadge": False,
        "name": "Roblox",
        "displayName": "Roblox",
        "targetId": 0,
    }

    archive_path = write_archive(Path.cwd(), current_user, people, normalized_conversations, failures)
    total_messages = sum(len(c.get("messages", [])) for c in normalized_conversations.values())
    metadata_only = sum(1 for c in normalized_conversations.values() if not c.get("messages"))

    print("\nDone.")
    print(f"Saved: {archive_path}")
    print(f"Archived conversations: {len(normalized_conversations)}")
    print(f"Archived messages: {total_messages}")
    if metadata_only:
        print(f"Metadata-only conversations: {metadata_only}")
    if failures:
        print(f"WARNING: {len(failures)} conversation(s) had message-fetch errors; their metadata was still saved.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
    except Exception as exc:
        error_path = Path.cwd() / "archiver-error.txt"
        try:
            with error_path.open("w", encoding="utf-8") as f:
                f.write(f"{APP_NAME}\n")
                f.write(f"Time: {_dt.datetime.now(tz=_dt.timezone.utc).isoformat()}\n\n")
                traceback.print_exc(file=f)
        except Exception:
            pass

        print("\nERROR:")
        print(str(exc))
        print(f"\nA diagnostic traceback was written to: {error_path}")
        print("The diagnostic file does NOT contain the cookie value.")
        raise SystemExit(1)
