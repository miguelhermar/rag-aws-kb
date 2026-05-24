# Acme Notes Information Security Policy

This policy applies to all Acme Notes employees, contractors, and authorized vendors with access to company systems or data. Violations may result in disciplinary action up to and including termination and referral to law enforcement.

## Authentication

### Passwords

- Minimum length: **14 characters**.
- Must include at least one uppercase letter, one lowercase letter, one digit, and one symbol.
- Rotation required every **180 days** for human accounts on Restricted-tier systems.
- Reuse of any of the **last 10 passwords** is prohibited.
- Passwords must be stored in the company-issued password manager. Recording passwords in notes, spreadsheets, or chat is prohibited.

### Multi-Factor Authentication

MFA is **mandatory** for all SSO logins, administrative consoles, and production access.

- **TOTP** (e.g., 1Password, Authy, YubiKey OTP) is the **preferred** second factor.
- **Hardware security keys** (FIDO2/WebAuthn) are required for production-tier admin roles.
- **SMS-based MFA is forbidden** on all company systems due to SIM-swap risk.

## Data Classification

All company data is classified into one of four tiers:

- **Public** — information approved for external release (marketing pages, published documentation). No handling restrictions.
- **Internal** — non-sensitive operational data intended for employees only (org charts, internal wiki pages). Must not be shared externally without approval.
- **Confidential** — business-sensitive data (financials, customer lists, source code). Must be encrypted in transit and at rest; sharing requires a documented business need.
- **Restricted** — highly sensitive data (customer content, credentials, PII, security incident details). Access is logged and audited; sharing requires explicit owner approval.

## Endpoint Security

- Full-disk encryption is **required** on all company laptops: **FileVault** on macOS, **BitLocker** on Windows.
- Endpoint Detection and Response (EDR) agents must remain enabled at all times.
- **USB drives are prohibited** for any interaction with Restricted-tier data. Use approved cloud transfer mechanisms instead.
- Screen lock must engage after no more than **5 minutes** of inactivity.

## Incident Reporting

Any suspected security incident — including phishing, lost devices, suspicious logins, or accidental data exposure — must be reported to **security@acmenotes.example** within **1 hour** of discovery.

### Severity Tiers

- **SEV1** — active compromise, customer data exposure, or service-wide outage. Page the on-call immediately via PagerDuty.
- **SEV2** — confirmed vulnerability with material impact, or suspected breach pending investigation. On-call response within 1 hour.
- **SEV3** — non-urgent security finding with limited blast radius. Response within 1 business day.
- **SEV4** — informational or housekeeping item (e.g., expired certificate with low impact). Response within 5 business days.

### On-Call Rotation

The Security team maintains a 24/7 on-call rotation. The current primary and secondary on-call engineers are listed in PagerDuty and pinned in the `#security` channel.

## Acceptable Use

Company devices and accounts are for company business. Personal use should be incidental and must not violate any other policy. Installing unapproved software on endpoints with access to Confidential or Restricted data requires prior approval from the Security team.

*Last updated: 2026-05-01* — *Owner: Information Security*
