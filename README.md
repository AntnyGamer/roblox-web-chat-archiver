# Roblox web chat archiver — v2.0.0 (2026 compatibility refresh)

This is a compatibility refresh of pizzaboxer's small Roblox web-chat archiver. It keeps the same local JSON archive concept and includes a self-contained `viewer.html`.

This revised distribution keeps application version **v2.0.0** and uses release tag **`v2.0.0-r2`**. The original `v2.0.0` tag is unchanged.

Roblox changed `.ROBLOSECURITY` rotation behavior in 2026. This refresh uses a real cookie session, accepts Roblox `Set-Cookie` rotations, and calls Roblox's session refresh endpoint before archiving. If Roblox still returns HTTP 401, re-copy the current `.ROBLOSECURITY` value from the logged-in Roblox tab and run it again; an already-invalid session cannot be recovered by the archiver.

## Windows: easiest way

Double-click **`run_archiver.bat`**. On Windows 10/11 it uses the included PowerShell implementation, so there is **nothing extra to install**. It will ask for your `.ROBLOSECURITY` value and save the archive JSON into the same folder.

Then open **`viewer.html`**, click **Load archive**, and choose the generated JSON file.

### Faster downloads (optional)

If Python 3 is installed, open a Command Prompt in the archiver folder and run **`run_archiver.bat fast`** (or `py -3 app.py`). This uses the Python implementation, which downloads up to three independent conversations concurrently while **keeping the existing 520 ms minimum spacing between authenticated requests**. It can reduce elapsed time when Roblox replies slowly, without increasing the request rate. Actual speed depends on server latency and rate limits; it cannot bypass Roblox's 30-message pages.

The normal double-click path still uses Windows PowerShell without extra software. Both implementations reuse valid user profiles from conversation metadata and recheck `Account Deleted` placeholders against Roblox's public profile API. Python also overlaps up to three independent public-user fallback lookups if a batch lookup omits profiles; these do not increase the authenticated chat request rate.

## How to get your `.ROBLOSECURITY` value

Use your browser's built-in Developer Tools rather than a cookie extension.

For Chrome or Microsoft Edge:

1. Sign in to **Roblox.com** in the account you want to archive.
2. Press **F12** (or **Ctrl+Shift+I**) to open Developer Tools.
3. Open the **Application** tab. If it is hidden, use the `>>` menu to find it.
4. In the left sidebar, expand **Cookies** and select `https://www.roblox.com`.
5. Find the cookie named **`.ROBLOSECURITY`**.
6. Copy its **Value** and paste that value into the archiver when prompted.

`.ROBLOSECURITY` is effectively a login credential. **Never send it to another person, paste it into a chat, post it in an issue, or upload a screenshot containing it.** Anyone who obtains a valid value may be able to access your Roblox account. The archiver only needs the value locally for requests to Roblox.

## Changes from the archived version

- Fixed conversation and message pagination so the **final page is no longer discarded**;
- Validates API page structures instead of silently interpreting malformed responses as empty archives;
- Supports nested user-lookup results without unnecessary per-user fallback requests;
- Updated authentication for Roblox's current `.ROBLOSECURITY` rotation behavior by using a real cookie session and accepting rotated cookies;
- Refreshes the Roblox session when appropriate and gives clearer 401/403 authentication errors;
- Retries rate limits, temporary Roblox server errors, timeouts, and ordinary network failures instead of immediately crashing;
- Paces authenticated requests to reduce the chance of hitting Roblox's cookie-API rate limits;
- Requests 30 items per page on conversation and message endpoints to reduce API calls and includes user data in conversation-list requests;
- Cancels queued Python downloads and wakes retry/pacing waits if authentication expires or the archiver is interrupted;
- Releases consumed Python download results after normalization to reduce memory usage;
- Validates the logged-in Roblox account before beginning a full archive;
- Continues past deleted or unresolvable users and resolves user information in batches when possible;
- Preserves valid usernames when a later inventory reports `Account Deleted`, retries masked profiles with the public individual-user endpoint, and retains the available placeholder if recovery fails;
- **Preserves conversation metadata even when Roblox returns zero accessible messages or a conversation's message request fails**, rather than silently deleting that conversation from the archive;
- Keeps previously downloaded messages if a later page fails, and marks the conversation incomplete in both the archive and viewer;
- Preserves available embedded messages or a message preview when a known-ID conversation returns an empty page or its message-history request fails, and labels these as **preview-only** rather than implying that the full chat was retrieved;
- Marks preview-only conversations separately in archive statistics;
- Also preserves conversations discovered before a later conversation-list page fails, visibly marking the resulting archive incomplete;
- Keeps conversation metadata and any embedded message previews when Roblox returns a conversation without an ID, flags its history as incomplete, and continues with other conversations;
- Records metadata-only and failed conversation information in `archiveMeta` so archive completeness is easier to understand;
- Queries the conversation list twice per export, combining the **current API responses within the same run** to retain ID-less friend metadata and valid-ID chat channels if Roblox returns an incomplete list on either pass; this is not a merge of previous JSON exports and adds some API requests;
- Preserves the original Roblox conversation/message fields alongside normalized viewer fields, without discarding timestamp or sender aliases;
- Removes duplicate message IDs when pages overlap, keeping distinct messages and all available system notifications;
- Fixes PowerShell fallback when Roblox supplies null primary fields but valid alternate fields, preserving message text and sender IDs;
- Gives each export a unique filename to prevent one backup from overwriting another; Python also writes private archives with restricted Unix file permissions;
- Never writes the `.ROBLOSECURITY` value to the archive and redacts it from PowerShell diagnostics if it is still present during an early failure;
- Includes both a dependency-free **PowerShell** implementation (`archiver.ps1`) and a dependency-free **Python** implementation (`app.py`);
- Replaces the old CDN-based viewer with a fully local viewer;
- Renders archive-controlled text with `textContent` instead of injecting it as HTML;
- Adds conversation search with reusable DOM rows for faster searching in large archives, safer handling of missing users/dates, and responsive viewing for smaller screens.

## Security warning

`.ROBLOSECURITY` is an active Roblox login credential. Do **not** paste it into chats, websites, Discord, GitHub issues, or anywhere else. The included archivers use it only for HTTPS requests to Roblox API hosts and do not save it in the output JSON.

The generated archive itself can contain private chat history in plaintext. Treat archive JSON files as sensitive and do not upload or share them unless you intend to share their contents.

## Diagnostics

If the archiver fails, it creates `archiver-error.txt`. The diagnostic output is designed not to include the cookie value.

The archive also records metadata-only conversations and message-fetch failures under `archiveMeta`, so a conversation does not silently disappear just because Roblox returned no accessible messages.

## API limitation

This refresh targets the current `apis.roblox.com/platform-chat-api/v1` endpoints used by the latest version of the original project. These are Roblox-controlled web APIs and can change without notice. If Roblox removes historical platform-chat access server-side, a client-side archiver cannot recover data Roblox no longer exposes.

