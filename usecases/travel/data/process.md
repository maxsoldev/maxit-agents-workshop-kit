# Use case 2: Events, business travel and office requests

**Owner:** office management
**Status:** draft by Lu

## Context
The team travels to conferences, partner meetings and Max Planck institutes. Office management books hotels and transport, keeps an overview of the year's events and is the substitute (Vertreterin) for everyone's leave. Requests come in through different channels and are handled one by one, by urgency.

## How it works today
- **Annual event overview:** a list of the year's events by month (event, place, dates), without names of travellers.
- **Travel:** the traveller submits the travel request in the SAP portal. After approval they get a DRA email with the travel number, which they sometimes forward to office management. Separately they tell office management by email or Mattermost where they go and what they need. Office management then searches for and books hotel and transport.
- **Leave:** everyone enters office management as substitute in SAP, so it receives every leave approval.
- **Procurement requests (Beschaffungsantrag) and training requests (Fortbildungsantrag):** still on paper.

## Approach
The agent does not start from a predefined process. It starts from what office management already receives and gives one overview. The use-case owner then shapes the steps with her feedback on the day.

## Inputs in the data pack
| Source | File |
|---|---|
| Emails to office management: trip notes, forwarded DRA emails, SAP leave approvals | `office_inbox/` (8 .eml) |
| Mattermost direct messages, copied from the chat window | `mattermost_messages.txt` |
| Annual event overview (in reality a PowerPoint slide) | `events_2027.xlsx` |
| Example rules | `travel_rules.md` |

## Suggested agent steps (human in the loop)
1. **Intake and overview:** read all sources and build one task list: who, where, when, travel number (yes/no), hotel and transport status, open questions. Merge duplicates (the same trip in an email and on Mattermost) and apply updates (a cancelled trip).
2. **Prioritise:** sort by date and urgency (trip date, registration deadlines, missing approval). Note who is on leave, so questions reach travellers in time.
3. **Prepare:** propose train and hotel options within the rules and draft short messages (questions to travellers, reminders). No booking, no sending.
4. **Weekly view:** who is away when (trips and leave), and open reminders (e.g. paper requests mentioned in messages).

Office management decides and books. The agents only read and draft.

## Questions for the use-case owner on the day
- Which of these inputs arrive in practice, and which are missing?
- What comes first when several requests arrive at once?
- Which information usually has to be chased?
- Where would the overview be most useful (Excel, Outlook, a short daily email)?

## Possible success criteria
- One complete overview instead of searching through mails and chats
- No missed deadlines or bookings
- Less back-and-forth with travellers

## Input in production
Requests reach the office manager's personal mailbox and Mattermost. The inputs have to be something the owner can produce without technical help:
- **Emails:** an Outlook rule collects relevant mails (DRA, leave approvals, travel notes) in one folder; she drags them into a local folder. Classic Outlook on Windows saves them as .msg, so the agents should read both .msg and .eml.
- **Mattermost:** today only copy and paste. Our Mattermost (Minerva Messenger) is run by Digital Labs, so the developers could later give an agent read access via the API.
- **Paper requests:** no digital input. At most the agent keeps a reminder when a request is mentioned in a message. Leave approvals contain personal data and should only be processed in an approved environment (see README, MPG rules).
