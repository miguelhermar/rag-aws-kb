# Acme Notes Product FAQ

Answers to the ten most common questions about the Acme Notes product. For additional help, contact **support@acmenotes.example**.

## 1. What are the differences between Free, Pro, Team, and Enterprise?

- **Free**: 1 GB storage, up to **3 collaborators** per workspace, **30 days** of version history.
- **Pro**: 50 GB storage, up to 10 collaborators, **90 days** of version history, offline mode.
- **Team**: 1 TB shared storage, **unlimited collaborators**, **1 year** of version history, admin controls, SSO.
- **Enterprise**: Custom storage, unlimited collaborators, **unlimited** version history retention, SAML SSO, audit logs, data residency options, and a dedicated customer success manager.

## 2. Which file formats can I import?

Acme Notes supports import from **Markdown** (`.md`), **Notion workspace exports** (`.zip`), **Evernote ENEX** (`.enex`), and **Microsoft Word** (`.docx`). Imports preserve headings, lists, links, and inline images. Complex tables and embedded databases may require manual cleanup.

## 3. Does Acme Notes work offline?

Offline mode is available on **Pro, Team, and Enterprise** tiers across desktop and mobile apps. Edits made offline are queued locally and **sync automatically when the device reconnects**. Conflict resolution uses a last-writer-wins strategy with a visible conflict marker for the affected paragraphs.

## 4. How many people can collaborate in real time?

- **Free**: up to **3 collaborators** per workspace, with up to 2 active editors per document at a time.
- **Pro**: up to 10 collaborators, 5 active editors per document.
- **Team and Enterprise**: **unlimited collaborators**, with up to 50 active editors per document before performance throttling.

## 5. Where is my data stored?

Free, Pro, and Team customers are hosted in the **United States (us-east-1)** by default. **Enterprise** customers may choose data residency in **United States, European Union (eu-west-1), or Australia (ap-southeast-2)** at contract signing. Residency cannot be changed after provisioning without a migration project.

## 6. What are the API rate limits?

- **Pro**: **60 requests per minute** per API key.
- **Team**: **600 requests per minute** per API key.
- **Enterprise**: **custom limits** negotiated per contract, typically in the thousands per minute.

Rate-limited responses return HTTP 429 with a `Retry-After` header. The Free tier does not include API access.

## 7. Which mobile devices are supported?

The Acme Notes mobile app supports **iOS 15 and later** and **Android 10 and later**. Older OS versions can still view content via the mobile web app at `app.acmenotes.example` but cannot use offline mode or push notifications.

## 8. How do I export my data?

Data export is available on **every tier**, including Free. From **Settings → Export**, request either a **JSON bundle** (preserves all structure, metadata, and revision history) or a **Markdown bundle** (one `.md` file per note, packaged as a `.zip`). Exports are generated asynchronously and delivered via a one-time download link valid for 24 hours.

## 9. Is there a keyboard shortcuts reference?

Press `?` from anywhere inside the app to open the keyboard shortcuts overlay. The full reference is also published at `docs.acmenotes.example/shortcuts` and stays in sync with each release.

## 10. How do I set up two-factor authentication?

Open **Settings → Security → Two-Factor Authentication** and follow the on-screen prompts to register a TOTP authenticator app (e.g., 1Password, Authy, Google Authenticator). Acme Notes will generate **10 single-use recovery codes** at setup — store these in a safe place. SMS-based 2FA is not offered.

*Last updated: 2026-05-01* — *Owner: Product*
