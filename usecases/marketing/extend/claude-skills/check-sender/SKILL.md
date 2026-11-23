---
name: check-sender
description: Check an unknown sender before we reply. Use when an email makes commercial or financial claims (money, investment, a large partnership) and the sender is not in the contact sheet.
---

# Check the sender

1. Call `contacts_lookup` with the sender's email address, then with the organisation name.
2. Call `inbox_history` with the sender's email address and `before` = today. Note earlier emails and what we answered.
3. Read "Unverified senders" in the owner's reply guidelines (CLAUDE.md, part "The owner's rules").
4. Decide:
   - Known contact, or a research institution or university: a normal request. No `unverified` flag.
   - Unknown, with big commercial or financial claims: category `unverified_outreach`, flag `unverified`.
     The reply asks politely for the full name of the organisation, a website with imprint, references and
     the concrete idea. No call, no meeting.
5. Write what you found in `reasons` (one sentence).

To make your own skill: copy this folder, change `name`, `description` and the steps.
