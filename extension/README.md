# GGAB Local Companion

This optional Manifest V3 extension connects the local app to an existing GGAB account in the same Edge or Chrome browser. It runs in the background and has no popup. Local video upload, playback, and analysis of already imported shot data can be used without this integration.

## Configure and load

1. Start the local app and open **http://127.0.0.1:8102/login**. Create the first administrator account if necessary.
2. Keep the **8000 compatibility port** enabled. The extension sends its local requests to `http://127.0.0.1:8000`; port 8102 is the main website entry point. The default launcher exposes both ports in one process.
3. For GGAB video migration and source verification, edit `extension/config.js` and set `globalThis.GGAB_PUBLIC_ANON_KEY` to the current **public anon/publishable key** supplied by the upstream provider for the configured Supabase project. The repository deliberately ships an empty value. Use an authorized public client configuration; never enter a service-role key, private API secret, user access token, refresh token, password, or Google Drive credential. If a required key is missing, the task stops with a configuration error before its Supabase request.
4. Open `edge://extensions` or `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select this `extension` directory. Reload the extension after changing `config.js`.
5. Sign in to your existing GGAB account at `https://app.getgoodatbadminton.com` in the same browser. Keep an authenticated GGAB tab available. In the local administrator account page, select **Connect background extension** (连接后台扩展), then refresh the local page.

The configured public key identifies the upstream client; it does not sign in a user or grant a local account access to another user's data. The extension still obtains the authenticated GGAB session from its existing tab. The backend fixes the shared GGAB identity on first pairing and rejects tasks after an unexpected account change. Local website users retain their existing ownership and token quota checks.

## What the integration does

- Uploads an approved project's original video and player snapshots, transfers the project settings, and synchronizes verified remote status and shot records.
- Checks an exact remote video record and its original storage object without recreating the project or charging for that check.
- Pauses before paid tracking. A local user must explicitly confirm the actual upstream estimate; a changed estimate requires confirmation again. Local token allocations do not top up the real upstream balance.
- Retains uncertain remote write outcomes for review instead of automatically replaying a potentially chargeable operation.

The extension uses authenticated GGAB web endpoints and the provider's configured Supabase host. These are external services, require your own authorized account and applicable service permissions, and may change independently of this repository. The local app does not include a badminton recognition model, an upstream subscription, or free analysis credits. This project is an independent companion and is not an official GGAB product.

## Browser permissions and boundaries

`manifest.json` limits remote host permissions to the GGAB web host and its configured Supabase host, plus local `127.0.0.1`. The `storage`, `alarms`, `tabs`, and `scripting` permissions support pairing, background checks, and retrieving the session from the existing GGAB tab. An integration account grants access to the real external service: use it only for projects you are authorized to process.

Keep one app process for both local ports, the browser, the extension, and the authenticated GGAB tab running while tasks are active. Remote website users do not need to install this administrator-side extension. If pairing, authentication, identity, project association, or source verification fails, resolve the displayed error before confirming another task. Do not widen network binding or change access protection merely to connect the extension.

## Tests

From the repository root with Node.js installed:

```powershell
node --test extension/*.test.cjs
```

The tests use mocked browser/network interfaces and fake public keys. They do not contact, upload to, or charge an actual GGAB account.
