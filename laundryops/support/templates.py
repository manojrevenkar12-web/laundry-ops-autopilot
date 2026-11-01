"""Deterministic reply templates in the five languages the eval set uses.

The live model writes nicer prose from the same ``facts``; these are the floor and the
fallback. Everything variable comes from ``facts`` — never from the ticket text.
"""
from __future__ import annotations

from typing import Any

from laundryops.domain.models import ActionType, Language

_T: dict[Language, dict[str, str]] = {
    Language.DE: {
        "hi": "Hallo {name},",
        "fault_first": "wir haben eine Störungsmeldung für {machine} an die Hausverwaltung geschickt, damit das Gerät geprüft wird.",
        "refund_nomachine": "wir haben {amount} € erstattet; das Geld ist in etwa {eta} Werktagen auf Ihrem Zahlungsmittel.",
        "conflict": "wir sehen in den Gerätedaten, dass auf {machine} gerade eine andere Buchung läuft – Ihre Reservierung konnte deshalb nicht starten.",
        "handover_why_review": "Diese Erstattung prüft eine Kollegin / ein Kollege kurz manuell.",
        "retrigger_ok": "wir haben {machine} gerade aus der Ferne neu freigeschaltet – die Tür sollte jetzt offen sein und der Waschgang läuft.",
        "refund": "wir haben {amount} € für {machine} erstattet; das Geld ist in etwa {eta} Werktagen auf Ihrem Zahlungsmittel.",
        "refund_dup": "wir haben die doppelte Abbuchung von {amount} € storniert; die Gutschrift erscheint in etwa {eta} Werktagen.",
        "credit": "als Ausgleich haben wir Ihrem Konto {amount} € Guthaben gutgeschrieben.",
        "fault": "Außerdem haben wir eine Störungsmeldung für {machine} an die Hausverwaltung geschickt, damit das Gerät geprüft wird.",
        "running": "Laut Gerätedaten läuft Ihr Waschgang auf {machine} seit {started}. Falls das nicht stimmt, antworten Sie bitte kurz auf diese Nachricht.",
        "refund_found": "Ihre Erstattung über {amount} € wurde am {date} ausgelöst und hat den Status „{status}“. Je nach Bank dauert die Gutschrift bis zu {eta} Werktage.",
        "no_refund_found": "wir finden in Ihrem Konto keine offene Erstattung und in den letzten 24 Stunden nur eine Abbuchung von {amount} €. Falls Ihr Kontoauszug etwas anderes zeigt, antworten Sie bitte mit einem Screenshot.",
        "single_charge": "in unserem Zahlungssystem sehen wir für diesen Waschgang nur eine Abbuchung von {amount} €. Eine zweite Zeile auf Ihrem Kontoauszug ist meist eine Vorautorisierung, die Ihre Bank in wenigen Tagen löscht. Falls sie bleibt, antworten Sie bitte mit einem Screenshot.",
        "report_thanks": "vielen Dank für den Hinweis. Wir haben eine Störungsmeldung für {machine} angelegt und die Hausverwaltung informiert.",
        "report_which": "vielen Dank für den Hinweis. Welche Maschine ist betroffen (Nummer auf dem Aufkleber)? Dann legen wir sofort eine Störungsmeldung an.",
        "faq_app": "bei Anmelde- oder Zahlungsproblemen hilft meist: App aktualisieren, abmelden und neu anmelden, Zahlungsmethode unter „Konto“ prüfen. Klappt es dann noch nicht, antworten Sie bitte mit dem Fehlertext.",
        "faq_general": "Waschen kostet an Ihrem Standort {price} € pro Gang; die Maschine wird in der App gebucht und per Karte oder PayPal bezahlt. Weitere Antworten finden Sie in der App unter „Hilfe“.",
        "partner": "Ihre Abrechnung für {period}: Nettoumsatz {net} €, Ihr Anteil {share} €. Auszahlung {payout_status} (Referenz {e2e}).",
        "handover": "Ihr Anliegen wurde an eine Kollegin / einen Kollegen übergeben, die sich innerhalb eines Werktags melden.",
        "handover_why_damage": "Bei Sachschäden prüfen wir jeden Fall persönlich.",
        "handover_why_identity": "Dafür müssen wir Ihr Konto sehen – bitte schreiben Sie uns aus der App oder nennen Sie die E-Mail-Adresse Ihres Kontos.",
        "handover_why_evidence": "Wir konnten den Vorgang in den Gerätedaten noch nicht zuordnen.",
        "bye": "Viele Grüße\nIhr Waschraum-Service",
    },
    Language.EN: {
        "hi": "Hi {name},",
        "fault_first": "we've opened a fault report for {machine} with the building manager so the machine gets checked.",
        "refund_nomachine": "we've refunded {amount} €; it should reach your payment method within about {eta} business days.",
        "conflict": "the machine data shows another booking is currently running on {machine}, which is why your reservation couldn't start.",
        "handover_why_review": "This refund needs a quick manual check by a colleague.",
        "retrigger_ok": "we've just remotely re-released {machine} – the door should be open now and your cycle is running.",
        "refund": "we've refunded {amount} € for {machine}; it should reach your payment method within about {eta} business days.",
        "refund_dup": "we've reversed the duplicate charge of {amount} €; it will show up within about {eta} business days.",
        "credit": "to make up for it we've added {amount} € of credit to your account.",
        "fault": "We've also opened a fault report for {machine} with the building manager so the machine gets checked.",
        "running": "According to the machine data your cycle on {machine} has been running since {started}. If that's not what you see, just reply to this message.",
        "refund_found": "your refund of {amount} € was issued on {date} and is currently \"{status}\". Depending on your bank it can take up to {eta} business days to appear.",
        "no_refund_found": "we can't find an open refund on your account, and only one charge of {amount} € in the last 24 hours. If your bank statement shows otherwise, please reply with a screenshot.",
        "single_charge": "our payment system shows a single charge of {amount} € for this cycle. A second line on your statement is usually a pre-authorisation your bank drops within a few days. If it stays, reply with a screenshot.",
        "report_thanks": "thanks for letting us know. We've logged a fault report for {machine} and informed the building manager.",
        "report_which": "thanks for letting us know. Which machine is affected (the number on the sticker)? We'll log a fault report right away.",
        "faq_app": "for login or payment problems this usually helps: update the app, sign out and back in, and check your payment method under \"Account\". If it still fails, reply with the error text.",
        "faq_general": "a cycle at your site costs {price} €; you book the machine in the app and pay by card or PayPal. More answers are under \"Help\" in the app.",
        "partner": "Your statement for {period}: net revenue {net} €, your share {share} €. Payout {payout_status} (reference {e2e}).",
        "handover": "Your request has been handed to a colleague who will get back to you within one business day.",
        "handover_why_damage": "We review every damage claim personally.",
        "handover_why_identity": "We need to see your account for that – please write to us from the app or tell us the email address on your account.",
        "handover_why_evidence": "We couldn't yet match this to the machine data.",
        "bye": "Best regards\nYour laundry-room team",
    },
    Language.TR: {
        "hi": "Merhaba {name},",
        "fault_first": "{machine} için bina yönetimine bir arıza bildirimi gönderdik.",
        "refund_nomachine": "{amount} € iade ettik; tutar yaklaşık {eta} iş günü içinde ödeme yönteminize ulaşır.",
        "conflict": "cihaz verilerine göre {machine} üzerinde şu anda başka bir rezervasyon çalışıyor; bu yüzden rezervasyonunuz başlayamadı.",
        "handover_why_review": "Bu iadeyi bir meslektaşımız kısaca manuel olarak kontrol edecek.",
        "retrigger_ok": "{machine} makinesini az önce uzaktan yeniden açtık – kapı artık açık olmalı ve yıkama devam ediyor.",
        "refund": "{machine} için {amount} € iade ettik; tutar yaklaşık {eta} iş günü içinde ödeme yönteminize ulaşır.",
        "refund_dup": "mükerrer {amount} € çekimini iptal ettik; yaklaşık {eta} iş günü içinde hesabınızda görünür.",
        "credit": "telafi olarak hesabınıza {amount} € bakiye tanımladık.",
        "fault": "Ayrıca {machine} için bina yönetimine bir arıza bildirimi gönderdik.",
        "running": "Cihaz verilerine göre {machine} üzerindeki yıkamanız {started} itibarıyla çalışıyor. Durum farklıysa bu mesaja yanıt verin.",
        "refund_found": "{amount} € tutarındaki iadeniz {date} tarihinde başlatıldı ve durumu \"{status}\". Bankanıza bağlı olarak {eta} iş günü sürebilir.",
        "no_refund_found": "hesabınızda açık bir iade bulamadık; son 24 saatte yalnızca {amount} € çekim var. Ekstreniz farklı gösteriyorsa ekran görüntüsüyle yanıt verin.",
        "single_charge": "ödeme sistemimizde bu yıkama için tek bir {amount} € çekim görünüyor. İkinci satır genellikle bankanızın birkaç gün içinde sildiği bir ön provizyondur. Kalırsa ekran görüntüsüyle yanıt verin.",
        "report_thanks": "bildiriminiz için teşekkürler. {machine} için arıza kaydı açtık ve bina yönetimini bilgilendirdik.",
        "report_which": "bildiriminiz için teşekkürler. Hangi makine etkilendi (etiketteki numara)? Hemen arıza kaydı açalım.",
        "faq_app": "giriş veya ödeme sorunlarında genellikle şu yardımcı olur: uygulamayı güncelleyin, çıkış yapıp tekrar girin, \"Hesap\" altından ödeme yöntemini kontrol edin. Olmazsa hata metniyle yanıt verin.",
        "faq_general": "lokasyonunuzda bir yıkama {price} €; makineyi uygulamadan ayırtıp kart veya PayPal ile ödersiniz. Diğer yanıtlar uygulamada \"Yardım\" altında.",
        "partner": "{period} dönemi hesap özetiniz: net ciro {net} €, payınız {share} €. Ödeme {payout_status} (referans {e2e}).",
        "handover": "Talebiniz bir iş günü içinde size dönecek bir meslektaşımıza iletildi.",
        "handover_why_damage": "Hasar taleplerini her zaman kişisel olarak inceliyoruz.",
        "handover_why_identity": "Bunun için hesabınızı görmemiz gerekiyor – lütfen uygulamadan yazın veya hesabınızın e-posta adresini belirtin.",
        "handover_why_evidence": "İşlemi cihaz verileriyle henüz eşleştiremedik.",
        "bye": "Saygılarımızla\nÇamaşırhane ekibiniz",
    },
    Language.FR: {
        "hi": "Bonjour {name},",
        "fault_first": "nous avons signalé une panne sur {machine} au gestionnaire de l'immeuble pour contrôle.",
        "refund_nomachine": "nous avons remboursé {amount} € ; le montant arrive sur votre moyen de paiement sous environ {eta} jours ouvrés.",
        "conflict": "les données de la machine montrent qu'une autre réservation tourne actuellement sur {machine}, c'est pourquoi la vôtre n'a pas pu démarrer.",
        "handover_why_review": "Ce remboursement nécessite une rapide vérification manuelle par un collègue.",
        "retrigger_ok": "nous venons de réactiver {machine} à distance – la porte devrait être ouverte et votre cycle tourne.",
        "refund": "nous avons remboursé {amount} € pour {machine} ; le montant arrive sur votre moyen de paiement sous environ {eta} jours ouvrés.",
        "refund_dup": "nous avons annulé le double prélèvement de {amount} € ; il apparaîtra sous environ {eta} jours ouvrés.",
        "credit": "pour compenser, nous avons crédité {amount} € sur votre compte.",
        "fault": "Nous avons aussi signalé une panne sur {machine} au gestionnaire de l'immeuble pour contrôle.",
        "running": "D'après les données de la machine, votre cycle sur {machine} tourne depuis {started}. Si ce n'est pas le cas, répondez simplement à ce message.",
        "refund_found": "votre remboursement de {amount} € a été émis le {date} et a le statut « {status} ». Selon votre banque, il peut mettre jusqu'à {eta} jours ouvrés.",
        "no_refund_found": "nous ne trouvons aucun remboursement en cours sur votre compte, et un seul prélèvement de {amount} € ces dernières 24 h. Si votre relevé montre autre chose, répondez avec une capture d'écran.",
        "single_charge": "notre système de paiement n'affiche qu'un seul prélèvement de {amount} € pour ce cycle. Une seconde ligne est en général une pré-autorisation que votre banque supprime sous quelques jours. Si elle reste, répondez avec une capture d'écran.",
        "report_thanks": "merci pour votre signalement. Nous avons ouvert un ticket de panne pour {machine} et prévenu le gestionnaire.",
        "report_which": "merci pour votre signalement. Quelle machine est concernée (numéro sur l'étiquette) ? Nous ouvrons un ticket immédiatement.",
        "faq_app": "pour les soucis de connexion ou de paiement : mettez l'application à jour, déconnectez-vous puis reconnectez-vous, et vérifiez le moyen de paiement dans « Compte ». Si ça persiste, répondez avec le message d'erreur.",
        "faq_general": "un cycle sur votre site coûte {price} € ; vous réservez la machine dans l'application et payez par carte ou PayPal. D'autres réponses sont dans « Aide ».",
        "partner": "Votre relevé pour {period} : chiffre d'affaires net {net} €, votre part {share} €. Versement {payout_status} (référence {e2e}).",
        "handover": "Votre demande a été transmise à un collègue qui vous répondra sous un jour ouvré.",
        "handover_why_damage": "Chaque dommage est examiné personnellement.",
        "handover_why_identity": "Nous devons voir votre compte – écrivez-nous depuis l'application ou indiquez l'e-mail du compte.",
        "handover_why_evidence": "Nous n'avons pas encore pu rapprocher l'incident des données de la machine.",
        "bye": "Cordialement\nVotre équipe laverie",
    },
    Language.ES: {
        "hi": "Hola {name}:",
        "fault_first": "hemos abierto un aviso de avería de {machine} al administrador del edificio para que la revisen.",
        "refund_nomachine": "hemos reembolsado {amount} €; llegará a tu método de pago en unos {eta} días laborables.",
        "conflict": "los datos de la máquina muestran que en {machine} hay otra reserva en marcha, por eso la tuya no pudo empezar.",
        "handover_why_review": "Este reembolso necesita una rápida comprobación manual de un compañero.",
        "retrigger_ok": "acabamos de reactivar {machine} a distancia: la puerta debería estar abierta y tu ciclo está en marcha.",
        "refund": "hemos reembolsado {amount} € por {machine}; llegará a tu método de pago en unos {eta} días laborables.",
        "refund_dup": "hemos anulado el cobro duplicado de {amount} €; aparecerá en unos {eta} días laborables.",
        "credit": "para compensar, hemos añadido {amount} € de saldo a tu cuenta.",
        "fault": "También hemos abierto un aviso de avería de {machine} al administrador del edificio para que la revisen.",
        "running": "Según los datos de la máquina, tu ciclo en {machine} está en marcha desde las {started}. Si no es así, responde a este mensaje.",
        "refund_found": "tu reembolso de {amount} € se emitió el {date} y está en estado «{status}». Según tu banco puede tardar hasta {eta} días laborables.",
        "no_refund_found": "no encontramos ningún reembolso pendiente en tu cuenta, y solo un cobro de {amount} € en las últimas 24 h. Si tu extracto muestra otra cosa, responde con una captura.",
        "single_charge": "nuestro sistema de pagos muestra un único cobro de {amount} € por este ciclo. Una segunda línea suele ser una preautorización que tu banco elimina en pocos días. Si se mantiene, responde con una captura.",
        "report_thanks": "gracias por avisar. Hemos registrado una avería de {machine} e informado al administrador.",
        "report_which": "gracias por avisar. ¿Qué máquina es (el número de la pegatina)? Abrimos el aviso de avería enseguida.",
        "faq_app": "para problemas de acceso o pago suele ayudar: actualizar la app, cerrar sesión y volver a entrar, y revisar el método de pago en «Cuenta». Si sigue fallando, responde con el texto del error.",
        "faq_general": "un ciclo en tu edificio cuesta {price} €; reservas la máquina en la app y pagas con tarjeta o PayPal. Más respuestas en «Ayuda».",
        "partner": "Tu liquidación de {period}: ingresos netos {net} €, tu parte {share} €. Pago {payout_status} (referencia {e2e}).",
        "handover": "Tu solicitud se ha pasado a un compañero que te responderá en un día laborable.",
        "handover_why_damage": "Revisamos cada reclamación por daños personalmente.",
        "handover_why_identity": "Necesitamos ver tu cuenta: escríbenos desde la app o indícanos el correo de tu cuenta.",
        "handover_why_evidence": "Aún no hemos podido relacionar el incidente con los datos de la máquina.",
        "bye": "Un saludo\nTu equipo de lavandería",
    },
}


def _eur(cents: int | None) -> str:
    return f"{(cents or 0) / 100:.2f}".replace(".", ",")


def render_reply(facts: dict[str, Any], language: Language) -> str:
    t = _T.get(language, _T[Language.EN])
    name = facts.get("name") or ""
    machine = facts.get("machine_label") or "the machine"
    eta = facts.get("refund_eta_days", 5)
    lines: list[str] = [t["hi"].format(name=name).replace(" ,", ",").replace(" :", ":")]
    body: list[str] = []

    for a in facts.get("actions", []):
        kind, ok = a.get("type"), a.get("success", True)
        if kind == ActionType.RETRIGGER_RELAY and ok:
            body.append(t["retrigger_ok"].format(machine=machine))
        elif kind == ActionType.REFUND and ok:
            key = ("refund_dup" if a.get("reason_code") == "ACT_REFUND_DUPLICATE"
                   else "refund" if facts.get("machine_label") else "refund_nomachine")
            body.append(t[key].format(amount=_eur(a.get("amount_cents")), machine=machine, eta=eta))
        elif kind == ActionType.GOODWILL_CREDIT and ok:
            body.append(t["credit"].format(amount=_eur(a.get("amount_cents"))))
        elif kind == ActionType.OPEN_FAULT_TICKET and ok:
            if facts.get("info_kind") == "report_thanks":
                continue
            body.append(t["fault" if body else "fault_first"].format(machine=machine))
        elif kind == ActionType.PARTNER_STATEMENT and ok:
            s = a.get("statement", {})
            body.append(t["partner"].format(period=s.get("period", ""), net=_eur(s.get("net_cents")),
                                            share=_eur(s.get("partner_share_cents")),
                                            payout_status=s.get("payout_status", "pending"), e2e=s.get("end_to_end_id", "-")))

    info = facts.get("info_kind")
    if info == "running":
        body.insert(0, t["running"].format(machine=machine, started=facts.get("cycle_started_at", "")))
    elif info == "refund_found":
        r = facts.get("refund", {})
        body.insert(0, t["refund_found"].format(amount=_eur(r.get("amount_cents")), date=r.get("date", ""),
                                                status=r.get("status", ""), eta=eta))
    elif info == "no_refund_found":
        body.insert(0, t["no_refund_found"].format(amount=_eur(facts.get("last_charge_cents"))))
    elif info == "single_charge":
        body.insert(0, t["single_charge"].format(amount=_eur(facts.get("last_charge_cents"))))
    elif info == "conflict":
        body.insert(0, t["conflict"].format(machine=machine))
    elif info == "report_thanks":
        body.insert(0, t["report_thanks"].format(machine=machine))
    elif info == "report_which":
        body.insert(0, t["report_which"])
    elif info == "faq_app":
        body.insert(0, t["faq_app"])
    elif info == "faq_general":
        body.insert(0, t["faq_general"].format(price=_eur(facts.get("price_cents", 350))))

    if facts.get("handover"):
        why = facts.get("handover_reason")
        if why in ("damage", "identity", "evidence", "review"):
            body.append(t[f"handover_why_{why}"])
        body.append(t["handover"])

    if not body:
        body.append(t["handover"])
    body = [body[0]] + [b[0].upper() + b[1:] for b in body[1:]]
    lines.append(" ".join(body))
    lines.append(t["bye"])
    return "\n\n".join(lines)
