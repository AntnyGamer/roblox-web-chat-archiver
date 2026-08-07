# Roblox web chat archiver — 2026 compatibility refresh

This is a compatibility refresh of pizzaboxer's small Roblox web-chat archiver. It keeps the same local JSON archive concept and includes a self-contained `viewer.html`.

2026 authentication note: Roblox changed `.ROBLOSECURITY` rotation behavior in May 2026. This refresh uses a real cookie session, accepts Roblox `Set-Cookie` rotations, and calls Roblox's session refresh endpoint before archiving. If Roblox still returns HTTP 401, re-copy the CURRENT `.ROBLOSECURITY` value from the logged-in Roblox tab and run it again; an already-invalid session cannot be recovered by the archiver.

## Windows: easiest way

Double-click **`run_archiver.bat`**. On Windows 10/11 it uses the included PowerShell implementation, so there is **nothing extra to install**. It will ask for your `.ROBLOSECURITY` value in a hidden prompt and save the archive JSON into the same folder.

Then open **`viewer.html`**, click **Load archive**, and choose the generated JSON file.

## Changes from the archived version

- Fixes the pagination bug that discarded the **final page** of conversations and messages;
- Validates the Roblox session before starting;
- Handles 401/403/429/5xx responses, timeouts, malformed responses and retries instead of simply crashing;
- Paces requests to reduce the chance of hitting Roblox's cookie-API rate limits;
- Continues past deleted/unresolvable users and most individual conversation errors;
- Preserves conversation metadata even when Roblox returns no accessible messages or one conversation's message request fails;
- Never writes the `.ROBLOSECURITY` cookie to the archive or diagnostic file;
- Includes both a dependency-free **PowerShell** implementation (`archiver.ps1`) and a dependency-free **Python** implementation (`app.py`);
- Replaces the old CDN-based viewer with a fully local viewer and renders archive text with `textContent`.

## Security warning

`.ROBLOSECURITY` is an active Roblox login credential. Do **not** paste it into chats, websites, Discord, GitHub issues, or anywhere else. The included archivers use it only for HTTPS requests to Roblox API hosts and do not save it in the output JSON.

The generated archive itself can contain private chat history in plaintext. Treat archive JSON files as sensitive and do not upload or share them unless you intend to share their contents.

## Diagnostics

If the archiver fails, it creates `archiver-error.txt`. The diagnostic output is designed not to include the cookie value.

The archive also records metadata-only conversations and message-fetch failures under `archiveMeta`, so a conversation does not silently disappear just because Roblox returned no accessible messages.

## API limitation

This refresh targets the current `apis.roblox.com/platform-chat-api/v1` endpoints used by the latest version of the original project. These are Roblox-controlled web APIs and can change without notice. If Roblox removes historical platform-chat access server-side, a client-side archiver cannot recover data Roblox no longer exposes.
