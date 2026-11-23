# Message templates (questions and reminders)

Write in the language the traveller uses with us (German or English). Short, friendly, one topic per message. The agent only drafts. Office management sends.

**Recipient:** `to` is always the traveller's email address from the team directory (`travel/team`), also for Mattermost drafts. Never a name and never a Mattermost username. **Channel:** `mattermost` when the traveller wrote to us on Mattermost, else `email`. When a Mattermost draft is sent, the platform finds the Mattermost username for that email in `travel/team`.

## Missing travel request

DE: "Hallo {Vorname}, für deine Reise nach {Ort} am {Datum} fehlt noch der genehmigte Reiseantrag (DRA mit Reisenummer). Sobald er da ist, buche ich {Zug/Hotel}. Kannst du ihn bitte bis {Datum} im SAP stellen? Danke, Martina"

EN: "Hi {first name}, for your trip to {place} on {date} I still need the approved travel request (DRA with travel number). As soon as I have it, I will book {train/hotel}. Could you submit it in SAP by {date}? Thanks, Martina"

## Options for approval

"Hi {first name}, here are options for {place} ({dates}): {option 1}, {option 2}. Which one do you prefer? I will book once you confirm."

## Question before leave

"Hi {first name}, before your leave starts on {date}, a quick question about {trip}: {question}"

## Cost estimate

"Hi {first name}, a rough estimate for {event} ({dates}): flight about {x} EUR, hotel about {y} EUR per night × {n} nights, total about {z} EUR. Prices change; I will check them again before booking. {open question if any}"

## Paper request reminder

"Reminder: the {training/procurement} request from {name} is on paper on my desk. Next step: {step}."
