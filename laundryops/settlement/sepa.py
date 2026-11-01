"""SEPA credit-transfer initiation (ISO 20022 pain.001.001.09) and IBAN checks.

Scope is deliberately narrow: one batch of partner payouts per run, EUR only, SLEV charges,
BIC optional (SEPA 'IBAN-only'). Returned-payment handling lives in ``reconcile.py``.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from laundryops.domain.models import Partner, PayoutInstruction

NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_E2E_RE = re.compile(r"^[A-Za-z0-9+?/\-:().,' ]{1,35}$")


def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not _IBAN_RE.match(s):
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def end_to_end_id(partner_id: str, period: str) -> str:
    e2e = f"LOPS-{partner_id}-{period}"[:35]
    assert _E2E_RE.match(e2e), e2e
    return e2e


def _cents_to_amt(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def build_pain001(payouts: list[PayoutInstruction], partners: dict[str, Partner], *, debtor_name: str, debtor_iban: str,
                  debtor_bic: str | None, execution_date: date, msg_id: str | None = None, created_at: datetime | None = None) -> str:
    if not payouts:
        raise ValueError("no payouts")
    for p in payouts:
        partner = partners[p.partner_id]
        if not iban_valid(partner.iban):
            raise ValueError(f"invalid creditor IBAN for partner {p.partner_id}")
        if p.amount_cents <= 0:
            raise ValueError(f"non-positive amount for payout {p.payout_id}")
    if not iban_valid(debtor_iban):
        raise ValueError("invalid debtor IBAN")

    msg_id = msg_id or f"LOPS{uuid.uuid4().hex[:12].upper()}"
    created_at = created_at or datetime.now(timezone.utc)
    ctrl = sum(p.amount_cents for p in payouts)

    doc = Element("Document", xmlns=NS)
    root = SubElement(doc, "CstmrCdtTrfInitn")
    hdr = SubElement(root, "GrpHdr")
    SubElement(hdr, "MsgId").text = msg_id
    SubElement(hdr, "CreDtTm").text = created_at.strftime("%Y-%m-%dT%H:%M:%S")
    SubElement(hdr, "NbOfTxs").text = str(len(payouts))
    SubElement(hdr, "CtrlSum").text = _cents_to_amt(ctrl)
    SubElement(SubElement(hdr, "InitgPty"), "Nm").text = debtor_name

    pmt = SubElement(root, "PmtInf")
    SubElement(pmt, "PmtInfId").text = f"{msg_id}-P1"
    SubElement(pmt, "PmtMtd").text = "TRF"
    SubElement(pmt, "BtchBookg").text = "true"
    SubElement(pmt, "NbOfTxs").text = str(len(payouts))
    SubElement(pmt, "CtrlSum").text = _cents_to_amt(ctrl)
    SubElement(SubElement(SubElement(pmt, "PmtTpInf"), "SvcLvl"), "Cd").text = "SEPA"
    SubElement(SubElement(pmt, "ReqdExctnDt"), "Dt").text = execution_date.isoformat()
    SubElement(SubElement(pmt, "Dbtr"), "Nm").text = debtor_name
    SubElement(SubElement(SubElement(pmt, "DbtrAcct"), "Id"), "IBAN").text = debtor_iban
    agt = SubElement(SubElement(pmt, "DbtrAgt"), "FinInstnId")
    if debtor_bic:
        SubElement(agt, "BICFI").text = debtor_bic
    else:
        SubElement(SubElement(agt, "Othr"), "Id").text = "NOTPROVIDED"
    SubElement(pmt, "ChrgBr").text = "SLEV"

    for p in payouts:
        partner = partners[p.partner_id]
        tx = SubElement(pmt, "CdtTrfTxInf")
        pid = SubElement(tx, "PmtId")
        SubElement(pid, "InstrId").text = p.payout_id[:35]
        SubElement(pid, "EndToEndId").text = p.end_to_end_id
        SubElement(SubElement(tx, "Amt"), "InstdAmt", Ccy="EUR").text = _cents_to_amt(p.amount_cents)
        if partner.bic:
            SubElement(SubElement(SubElement(tx, "CdtrAgt"), "FinInstnId"), "BICFI").text = partner.bic
        SubElement(SubElement(tx, "Cdtr"), "Nm").text = partner.name[:140]
        SubElement(SubElement(SubElement(tx, "CdtrAcct"), "Id"), "IBAN").text = partner.iban.replace(" ", "")
        SubElement(SubElement(tx, "RmtInf"), "Ustrd").text = f"Laundry revenue share {p.statement_id}"[:140]

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(doc, encoding="unicode")
