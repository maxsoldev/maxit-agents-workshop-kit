# Routing: who gets what

## Actions

- `reply`: We answer the sender ourselves (marketing & communication).
- `forward`: Someone else in the department must handle it. Write who in "route to".
- `no_reply`: Newsletters, spam, phishing, and service offers we do not need. Log them and stop.

## Route to

- `tech_team`: Technical problems and support (e.g. errors with certificates, the verification page or nodes). Always high priority. If the sender writes again, mark it as urgent.
- `membership_owner`: Questions about joining the bloxberg consortium, membership conditions, running a member node. **Always** route these to the membership owner, also when we reply ourselves.
- `head`: Use only when the head of department must handle the email, not only approve our reply.

Leave "route to" empty when we reply ourselves, except for membership requests (see above).

## Head-of-department approval

Mark "needs head approval" when our reply would:
- commit the department to something: a talk, a date, a deadline, a deliverable, money, a partnership or collaboration,
- go to the press or media, or be published in our name (interviews, quotes, case studies, report texts, statements),
- speak for bloxberg or the MPG in public,
- give a statement, description or text on behalf of bloxberg or the department, or agree that others list us (e.g. as supporter, partner or member) in their documents.

No approval needed for: simple information, a demo date offer to an interested institute, a polite request for more information, a short "no, thank you".

## Selling to us or buying from us?

First ask: what does the sender want?

- The sender wants to **sell us** a service (marketing, SEO, crypto marketing, exchange listings, hosting, consulting): category `service_offer`. What we do: No reply (see below).
- The sender wants to **buy from us, fund us or partner with us**, with big claims (large budgets, investment, long-term deals), and we cannot verify them: category `unverified_outreach`. What we do: Flag `unverified`. Reply with a polite request for details (organisation, imprint, references, the concrete idea). No call, no meeting. This also applies when the offer mentions tokens or crypto.

## Service offers

- We do not buy marketing, SEO, listing or hosting services through this inbox.
- If the contact sheet says we declined this vendor before: no reply, or a very short decline that refers to our earlier decision. Never offer a meeting.
- Offers to sell us crypto marketing, token listings or exchange listings do not fit bloxberg (bloxberg has no speculative token). Flag `off_target`, no reply.
