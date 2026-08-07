# Roblox web chat archiver — 2026 compatibility refresh

This is a compatibility refresh of pizzaboxer's small Roblox web-chat archiver. It keeps the same local JSON archive concept and includes a self-contained `viewer.html`.

Roblox changed `.ROBLOSECURITY` rotation behavior in 2026. This refresh uses a real cookie session, accepts Roblox `Set-Cookie` rotations, and calls Roblox's session refresh endpoint before archiving. If Roblox still returns HTTP 401, re-copy the current `.ROBLOSECURITY` value from the logged-in Roblox tab and run it again; an already-invalid session cannot be recovered by the archiver.

## Windows: easiest way

Double-click **`run_archiver.bat`**. On Windows 10/11 it uses the included PowerShell implementation, so there is **nothing extra to install**. It will ask for your `.ROBLOSECURITY` value and save the archive JSON into the same folder.

Then open **`viewer.html`**, click **Load archive**, and choose the generated JSON file.

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
- Updated authentication for Roblox's current `.ROBLOSECURITY` rotation behavior by using a real cookie session and accepting rotated cookies;
- Refreshes the Roblox session when appropriate and gives clearer 401/403 authentication errors;
- Retries rate limits, temporary Roblox server errors, timeouts, and ordinary network failures instead of immediately crashing;
- Paces authenticated requests to reduce the chance of hitting Roblox's cookie-API rate limits;
- Validates the logged-in Roblox account before beginning a full archive;
- Continues past deleted or unresolvable users and resolves user information in batches when possible;
- **Preserves conversation metadata even when Roblox returns zero accessible messages or a conversation's message request fails**, rather than silently deleting that conversation from the archive;
- Records metadata-only and failed conversation information in `archiveMeta` so archive completeness is easier to understand;
- Never writes the `.ROBLOSECURITY` value to the archive and redacts it from PowerShell diagnostics if it is still present during an early failure;
- Includes both a dependency-free **PowerShell** implementation (`archiver.ps1`) and a dependency-free **Python** implementation (`app.py`);
- Replaces the old CDN-based viewer with a fully local viewer;
- Renders archive-controlled text with `textContent` instead of injecting it as HTML;
- Adds conversation search, safer handling of missing users/dates, and responsive viewing for smaller screens.

## Security warning

`.ROBLOSECURITY` is an active Roblox login credential. Do **not** paste it into chats, websites, Discord, GitHub issues, or anywhere else. The included archivers use it only for HTTPS requests to Roblox API hosts and do not save it in the output JSON.

The generated archive itself can contain private chat history in plaintext. Treat archive JSON files as sensitive and do not upload or share them unless you intend to share their contents.

## Diagnostics

If the archiver fails, it creates `archiver-error.txt`. The diagnostic output is designed not to include the cookie value.

The archive also records metadata-only conversations and message-fetch failures under `archiveMeta`, so a conversation does not silently disappear just because Roblox returned no accessible messages.

## API limitation

This refresh targets the current `apis.roblox.com/platform-chat-api/v1` endpoints used by the latest version of the original project. These are Roblox-controlled web APIs and can change without notice. If Roblox removes historical platform-chat access server-side, a client-side archiver cannot recover data Roblox no longer exposes.
