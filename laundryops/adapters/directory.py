"""Directory of partners, sites and users (stands in for the operator's CRM / app backend)."""
from __future__ import annotations

from laundryops.domain.models import Partner, Site, User


class Directory:
    def __init__(self):
        self.partners: dict[str, Partner] = {}
        self.sites: dict[str, Site] = {}
        self.users: dict[str, User] = {}

    def add_partner(self, p: Partner) -> None:
        self.partners[p.partner_id] = p

    def add_site(self, s: Site) -> None:
        self.sites[s.site_id] = s

    def add_user(self, u: User) -> None:
        self.users[u.user_id] = u

    def get_user(self, user_id: str | None) -> User | None:
        return self.users.get(user_id) if user_id else None

    def get_partner(self, partner_id: str | None) -> Partner | None:
        return self.partners.get(partner_id) if partner_id else None

    def get_site(self, site_id: str | None) -> Site | None:
        return self.sites.get(site_id) if site_id else None

    def sites_for_partner(self, partner_id: str) -> list[Site]:
        return [s for s in self.sites.values() if s.partner_id == partner_id]


class Notifier:
    """Records outbound notifications (push/email/SMS). A real one would call the app's push service."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to: str, channel: str, subject: str, body: str, **meta) -> None:
        self.sent.append({"to": to, "channel": channel, "subject": subject, "body": body, **meta})

    def for_recipient(self, to: str) -> list[dict]:
        return [m for m in self.sent if m["to"] == to]
