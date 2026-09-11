# Google OAuth verification — submission notes

Reference material for submitting Infinity Agent's OAuth consent screen for Google verification, so any
real Google account (not just accounts manually added as test users) can log in. This is a manual process
in Google Cloud Console — nothing here runs automatically.

## Before submitting

- [ ] Privacy Policy is live: `https://<your-domain>/privacy`
- [ ] Terms of Service is live: `https://<your-domain>/terms`
- [ ] App homepage set to `https://<your-domain>/`
- [ ] Support email set to `jefftian.oneinfinity@gmail.com`
- [ ] App name in the consent screen matches the product name shown to users ("Infinity Agent")
- [ ] Record a screen-capture demo video showing: signing in with "Continue with Google", the consent
      screen listing the requested scopes, and — once logged in — where each scope is actually used in
      the product (the inbox queue populated from Gmail, a scheduling draft referencing calendar
      availability, and approving a draft to show it actually sends via Gmail)
- [ ] Read Google's current requirements for restricted scopes (`gmail.readonly`, `gmail.send`) at
      submission time — the security-assessment requirement (CASA) and its cost/timeline change over
      time; don't rely on this doc for the current specifics

## Scope justifications (paste into the verification form)

**`openid`, `.../auth/userinfo.email`**
> Used to identify the merchant's account and create their session after they sign in with Google — this
> is the app's only login method, so no separate password system exists.

**`https://www.googleapis.com/auth/gmail.readonly`**
> Used to read the business owner's inbox so the assistant can identify messages that may need a reply
> and draft a suggested response for the owner to review. We only read message content the account
> owner has authorized by connecting their account; nothing is auto-forwarded or shared beyond generating
> the draft.

**`https://www.googleapis.com/auth/gmail.send`**
> Used to send a reply on the business owner's behalf — but only after the owner has reviewed the
> AI-drafted reply in-app and explicitly approved or edited it. No message is ever sent without that
> explicit human approval step.

**`https://www.googleapis.com/auth/calendar.readonly`**
> Used to read free/busy availability (not event details) so that a drafted reply to a scheduling
> request can propose real open times, rather than asking the customer to guess. We never create,
> modify, or delete calendar events.

## Known dependency (resolved)

Google's review for restricted scopes (the Gmail ones) checks that stated data-handling practices match
reality. `GmailAccountCredential.refresh_token`/`access_token` are now encrypted at rest
(`app/core/crypto.py`), matching what the Privacy Policy states — see README's "Known Design
Trade-offs" for the remaining caveat (the encryption key itself isn't yet in a real secrets
manager/KMS).
