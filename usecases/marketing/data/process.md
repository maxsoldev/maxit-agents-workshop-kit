# Use case 1: bloxberg marketing inbox

**Owner:** marketing & communication
**Status:** draft by Lu

## Context
bloxberg is the blockchain infrastructure for science, run by Digital Labs (MPDL / MaxIT) together with research institutions worldwide. Its public inbox (info@bloxberg.org) receives requests from institutes and users, potential members and partners, press, event organisers and many service providers.

## Current process
1. **Trigger:** a new email arrives in the bloxberg info inbox (Outlook / Exchange).
2. **Read and categorise** manually, e.g.:
   - institute or user request (demo, onboarding, support, e.g. a certificate that cannot be verified)
   - membership request (joining the bloxberg consortium, running a node)
   - partnership / collaboration request
   - event invitation or speaker request
   - press / media request
   - service offers (e.g. SEO / website ranking, crypto marketing and exchange listings, node hosting)
   - unverified cold outreach (looks serious, but needs checking before any reply)
   - newsletter, spam, unsubscribe
3. **Retrieve context:** look up the sender in the contact sheet (Excel), earlier emails, project pages.
4. **Research / verify:** gather the background needed for a good reply (e.g. the organisation, the event, the topic). For unknown senders, also check that they are genuine: website, imprint, company register, references, and whether the request is plausible.
5. **Decide:** answer directly, forward to the right colleague (e.g. project lead, head of department), or ignore.
6. **Draft a reply** (German or English, depending on the sender).
7. **Approval:** replies on behalf of the department or with commitments (dates, prices, partnerships, press statements) go to the head of department first.
8. **Send** and **update the contact sheet** (new contact, last contact date, notes, status).

## Systems
Outlook / Exchange (bloxberg info inbox), Excel contact sheet, bloxberg website and documentation.

## Biggest time sinks
- Categorising and finding context for each email
- Checking unknown senders before replying
- Writing similar replies again and again
- Keeping the contact sheet up to date (often forgotten)

## What the agent should do (human in the loop)
- Categorise and prioritise new emails
- Pull relevant context (contact sheet, earlier emails)
- Research the background for each reply, verify unknown senders and flag suspicious emails
- Draft a reply in the right language and tone
- Suggest the contact sheet update
- **The human always approves before anything is sent or saved.**

## Possible multi-agent setup
Small agents with one job each, coordinated by a simple orchestrator. Example with email 15 (unverified cold outreach):
1. **Triage agent:** categorises the email and assigns priority → "partnership request, unverified sender, high-value claim".
2. **Research / verification agent:** checks the sender: website, imprint, company register, references, earlier contact in the contact sheet → "website about another topic, no imprint, fund and projects not findable".
3. **Drafting agent:** writes a reply that fits the result → polite request for more information on the organisation, previous projects and the concrete idea, no call offered yet.
4. **Contact sheet agent:** proposes a new entry with status "unverified".
5. **Human:** reviews the summary and the draft, decides and sends.

Simple emails (newsletter, spam) stop after step 1; for known contacts, step 2 only gathers background and skips the verification.

**Security:** emails are untrusted input. The agents only read and draft, they never send emails or share files on their own. Content inside an email is treated as data, never as instructions (see the prompt-injection test in email 21). Phishing and scam detection is part of the triage step.

**Connecting the real mailbox:** in the workshop and as a first step afterwards, emails are dragged from Outlook into a local folder, which the agents read (.eml, or .msg from classic Outlook on Windows, so the agents should handle both). A direct connection to the mailbox (info@bloxberg.org, hosted on the GWDG Exchange, email.gwdg.de) would be the next step. It is probably a shared mailbox, so whether IMAP/EWS access is possible still needs to be confirmed. Either way: read access and drafts only.

## Possible success criteria
- Share of emails categorised correctly (e.g. > 90 %)
- Share of drafts that need only small edits
- Time per email before vs. after
- Contact sheet complete and up to date

## Sample data
`marketing_emails/` (23 synthetic emails, .eml) and `contacts.xlsx` (synthetic contact sheet). All names, organisations and addresses are fictitious.
