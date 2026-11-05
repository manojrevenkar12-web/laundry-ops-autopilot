# Outreach e-mail — WeWash GmbH

**To:** Dr. Rafael Kirschner, Geschäftsführer, WeWash GmbH (co-founder; ran product development and customer service before taking over as CEO — confirm the current title and the best address on LinkedIn / the Impressum before sending; do not send to a generic inbox)
**From:** Manoj Kumar Prakash, Berlin
**Subject options:**
1. Ihre Hotline, ohne Warteschleife — Entsperren, Erstatten, Störung melden in 40 Sekunden
2. 89,6 % der Waschraum-Tickets ohne Mitarbeiter gelöst — zwei Wochen Shadow-Betrieb auf Ihren Tickets?
3. The €3.50 ticket problem: a working agent that re-releases the door and refunds from the ledger

---

## Version A — German (recommended for a Munich Geschäftsführer)

Sehr geehrter Herr Dr. Kirschner,

ein WeWash-Waschgang kostet 2 bis 4 Euro. Ein Support-Kontakt dazu — Telemetrie der Box prüfen, Mollie-Buchung suchen, erstatten, antworten — kostet mehr als der Waschgang selbst, und am Telefon noch einmal deutlich mehr. Genau diese drei Fälle wiederholen sich in den öffentlichen Bewertungen: Tür bleibt zu, Waschgang bricht ab, doppelt abgebucht — plus Hausverwaltungen, die nach ihrer Auszahlung fragen.

Ich habe dafür eine lauffähige Plattform gebaut, die diese Fälle ohne Mitarbeiter abschließt — im Chat und auf der Hotline:

- **Tür bleibt zu:** Der Agent prüft Buchung und Box-Telemetrie, schaltet das Relais aus der Ferne neu frei und fragt am Telefon nach: „Ist die Tür jetzt offen?“ Wenn nicht, erstattet er den exakten Ledger-Betrag und legt eine Störungsmeldung mit SLA-Uhr für die Hausverwaltung an — noch während des Anrufs.
- **Doppelt abgebucht / abgebrochen:** Erstattung nur gegen Beleg (doppelte Buchung, verwaiste Buchung, vom Gerät gemeldeter Abbruch), nie gegen eine Zahl aus der Nachricht; Deckel pro Nutzer und Monat; jede Entscheidung mit Begründungscodes im Audit-Log.
- **„Wo bleibt unsere Auszahlung?“:** Abrechnung, SEPA-Auszahlung (pain.001) und Bankreferenz in einem Satz, ohne Finance.

Gemessen auf 500 gelabelten Szenarien in fünf Sprachen, als Tickets und als Anrufe: 89,6 % ohne Mitarbeiter, 0 falsche Erstattungen, 0 Aktionen auf Fällen, die zu einem Menschen gehören. Die Daten sind synthetisch — deshalb mein Vorschlag:

**Zwei Wochen Shadow-Betrieb auf Ihrem Live-Ticketstrom.** Der Agent entscheidet und protokolliert, führt nichts aus und sendet nichts. Sie bekommen die Deflection-Quote auf Ihren eigenen Tickets, jede Abweichung mit Beleg, und eine Hotline auf einer Testnummer. Ihr Aufwand: Lesezugriff auf Box-Telemetrie und Mollie, ein anonymisierter Export von 500 Tickets, ein Ansprechpartner. Der Pilot kostet pauschal 9.900 € netto inklusive der Anbindungen; danach zahlen Sie nur für Kontakte, die ohne Mitarbeiter abgeschlossen wurden (0,95 € je Ticket, 1,45 € je Anruf, gedeckelt bei 6.000 € im Monat).

Der Code ist öffentlich und von Ihren Entwicklern prüfbar: https://github.com/manojrevenkar12-web/laundry-ops-autopilot
Mein Profil: https://www.linkedin.com/in/manoj-kumar-prakash-52b7a01b6/

Hätten Sie diese oder nächste Woche 30 Minuten für eine Demo? Ich bin in Berlin und komme gern nach München.

Mit freundlichen Grüßen
Manoj Kumar Prakash
+49 176 36795025

---

## Version B — English

Dear Dr. Kirschner,

A WeWash cycle costs €2–4. One support contact about it — checking the box telemetry, finding the Mollie payment, refunding, replying — costs more than the cycle, and on the phone considerably more. The same three cases recur in public reviews: the door stays locked, the cycle dies halfway, a double charge — plus building operators asking where their payout is.

I have built a working platform that closes those cases without a person, in chat and on the hotline:

- **Door stays locked:** the agent checks the booking and the box telemetry, re-triggers the relay remotely and, on the phone, asks "Is the door open now?" If not, it refunds the exact ledger amount and opens a fault ticket with an SLA clock for the building manager — while the caller is still on the line.
- **Double charge / aborted cycle:** refunds only against evidence (duplicate capture, orphan capture, box-reported abort), never against a number in the message; per-resident caps; every decision carries reason codes in an audit log.
- **"Where is our share?":** statement, SEPA payout (pain.001) and bank reference in one sentence, without finance.

Measured on 500 labelled scenarios in five languages, as tickets and as calls: 89.6 % resolved with no human, 0 wrong refunds, 0 actions on cases that belong to a person. The data is synthetic — hence the proposal:

**Two weeks of shadow mode on your live ticket stream.** The agent decides and logs, executes nothing, sends nothing. You get the deflection rate on your own tickets, every disagreement with its evidence, and a hotline on a test number. Your effort: read access to box telemetry and Mollie, an anonymised export of 500 tickets, one contact person. The pilot is a fixed €9,900 net including the integrations; afterwards you pay only for contacts closed with no human (€0.95 per ticket, €1.45 per call, capped at €6,000 a month).

The code is public and your engineers can inspect it: https://github.com/manojrevenkar12-web/laundry-ops-autopilot
My profile: https://www.linkedin.com/in/manoj-kumar-prakash-52b7a01b6/

Would you have 30 minutes this week or next for a demo? I am in Berlin and happy to come to Munich.

Kind regards
Manoj Kumar Prakash
+49 176 36795025

---

## Follow-up (5 working days later, if no reply)

Betreff: Re: Ihre Hotline, ohne Warteschleife

Sehr geehrter Herr Dr. Kirschner,

kurz nachgefasst: Falls der Pilot in Ihrem Haus eher beim Head of Operations oder beim CTO liegt, leiten Sie mir gern den Namen weiter — ich wende mich dann direkt dorthin. Die 3-Minuten-Demo (Anruf, Tür auf, Erstattung, Störungsmeldung) gibt es auch als Video, wenn ein Termin gerade nicht passt.

Mit freundlichen Grüßen
Manoj Kumar Prakash

## Notes before sending

- Verify the recipient and role on LinkedIn and the Impressum the day you send; CEO changes have been reported with different dates.
- Pricing is set (see `pricing.md`): €9,900 net pilot; €0.95 per auto-resolved ticket, €1.45 per contained call, €6,000 monthly cap, no minimum for three months, pilot fee credited if live within 60 days.
- Attach nothing on the first mail; link the repo and offer the deck and the 3-minute video on reply.
- If the reply comes from a BSH venture-client contact (BSH Startup Kitchen), the same pilot structure works as a purchase order: supplier number, PO, no equity.
