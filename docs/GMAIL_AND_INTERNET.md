# Peter’s internet and Gmail connection

Peter uses your existing LM Studio model. Current city clocks use the computer’s UTC clock and the IANA timezone database. Public web search uses Bing RSS, with Google News RSS first for news queries and as a fallback. Weather uses Open-Meteo. Sources and retrieval times are returned to Peter; webpage and email text are untrusted evidence. Search queries use words from your current message, and private tool contents cannot authorize web requests.

## Gmail setup

No Google desktop application is required. **Desktop app** is Google's name for the OAuth client type for applications like Peter. Authentication happens in your normal browser.

1. Open [Google Cloud Console](https://console.cloud.google.com/), sign in, and select or create a project.
2. Enable the **Gmail API** for that project.
3. Open **Google Auth Platform**. Configure the app name (Peter McGuire) and your support/contact email. Choose **External** for a personal Gmail account. In **Audience**, add your Gmail address as a test user while the app is in Testing.
   If sign-in returns `403: access_denied` and says only approved testers can access Peter, add the exact signed-in Gmail address under **Audience → Test users**. In Spanish this is **Público → Usuarios de prueba → Agregar usuarios**. Keep the app in Testing for personal setup; requesting send permission does not resolve this error.
4. In **Clients**, create an OAuth client. Choose **Desktop app** as its application type, then download its JSON configuration. This is configuration for Peter, not software to install.
5. In Peter, open **Settings → Internet & Gmail**. Browse to the downloaded JSON, then choose **Connect Gmail**.
6. Complete Google's browser sign-in and approve Peter’s **read-only Gmail** permission. Peter does not send, delete, mark read or modify email.

Google’s [official Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) documents the project, API, consent and Desktop OAuth setup. Peter includes its own sign-in implementation, so you do not need to copy Google's example code or install Python packages.

The Google account may require organization administrator approval. Google also limits refresh-token lifetime for external applications in Testing (normally seven days with Gmail access). When a sign-in expires, choose Connect Gmail again. Changing publishing status is a separate Google configuration decision; Peter does not change it automatically.

## Automatic startup inbox check

**Check new Gmail inbox messages at every launch** is enabled by default. It runs in the background independently of model and voice startup. The first successful connection records an inbox baseline and previews the last day’s inbox mail. Later launches retrieve new arrivals and messages newly moved into the inbox since the last successful check, including messages already read elsewhere.

Startup retrieves message headers and saves a private queue. After your positive reply to Peter's readiness greeting and the calendar briefing, he asks “You have a new email from X, would you like me to read it for you, sir?” He fetches a body only after your direct approval. Use **Check inbox now**, or ask “Check my Gmail inbox” or “Can you check my inbox and for any new emails?”, for another check. These requests use the durable inbox checkpoint directly and include the actual latest inbox header and its link. They do not ask the model to invent a today-only filter or claim to search Spam or Promotions. Email offers and approved body readings stay written in the conversation. Asking for the latest email retains its message reference, so a follow-up such as “Read through it and tell me what you think” reads that email without asking for an internal ID. The reference is bound to the connected Gmail account. “What’s my last email altogether?” retrieves the newest received message across folders, including archived mail, Spam and Trash. Sent mail and drafts are excluded from that received-mail query. Explicit Inbox requests and startup new-arrival checks keep their Inbox scope. Confirmed account totals come from Gmail’s profile; an empty filtered result does not mean an empty account. A new account or an ambiguous set of messages needs a fresh selection.

Gmail history is paginated and delivered in batches. A checkpoint advances after queued headers are successfully saved; failed network requests or local saves do not skip messages. Pending batches and queued headers survive a crash. Account changes keep separate checkpoints and sender offers only use the currently connected account. If Google expires its history cursor, Peter compares the current inbox against its saved IDs. A restarted session asks again before reading any queued body.

Messages already deleted or archived before the check are outside the current inbox. Peter does not download attachments or load remote email images. It reads bounded plain message text. Email is never treated as authorization for workspace changes or external lookup.

## Private local data

Tokens and sync state are stored with restrictive permissions under `.runtime/gmail`, excluded from Git, repository-reading tools and source packages. Only a configured Peter client file or `PETER_GMAIL_CLIENT_FILE` is used; credentials from other applications are not reused. The optional `PETER_GMAIL_TOKEN_FILE` must be Peter’s own account-bound read-only token, outside readable source folders.

## Verification

Live LM Studio checks returned the Shanghai clock immediately, Shanghai weather in 4.84 seconds, a web search answer with sources in 2.58 seconds, and the Example Domain page title in 2.25 seconds. Gmail sign-in and synchronization were tested with synthetic OAuth and Gmail responses, including a real local callback. Live Gmail sign-in, inbox headers and complete body retrieval have now been verified with the configured read-only account. The browser reports a successful connection only after the token exchange and Gmail profile check succeed; API-disabled errors are reported without overwriting a working token. The newest-email follow-up also succeeded on the local Qwen model and retained its Gmail link. Private email contents and credentials are excluded from source packages and validation reports.
