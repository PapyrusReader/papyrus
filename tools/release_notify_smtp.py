#!/usr/bin/env python3
"""Send release alerts to the recipient configured for Papyrus monitoring."""

import argparse
import json
import re
import shlex
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path

from release_notifications import PAYLOAD_LIMIT, TERMINAL_STATES


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str
    sender: Address
    recipient: Address
    security: str


def read_settings(path, password_file):
    fields = {}

    for line in path.read_text().splitlines():
        key, separator, raw = line.partition("=")

        if separator and key.strip() in {
            "ALERT_EMAIL", "SMTP_HOST", "SMTP_USERNAME", "SMTP_FROM_EMAIL", "SMTP_STARTTLS_POLICY", "SMTP_SECURITY",
        }:
            values = shlex.split(raw, comments=True)

            if len(values) != 1:
                raise ValueError("Expected one value per SMTP setting")

            fields[key.strip()] = values[0]

    host, _, port = fields["SMTP_HOST"].rpartition(":")
    security = fields.get("SMTP_SECURITY", "starttls")
    port = int(port)
    password = password_file.read_text().rstrip("\r\n")

    if not host or not 1 <= port <= 65535 or security not in {"starttls", "ssl"}:
        raise ValueError("SMTP must have a host, a port and TLS")

    if security == "starttls" and fields.get("SMTP_STARTTLS_POLICY", "MandatoryStartTLS") != "MandatoryStartTLS":
        raise ValueError("SMTP must require STARTTLS")

    if not fields["SMTP_USERNAME"] or not password:
        raise ValueError("SMTP credentials are empty")

    return MailSettings(
        host, port, fields["SMTP_USERNAME"], password, Address(addr_spec=fields["SMTP_FROM_EMAIL"]),
        Address(addr_spec=fields["ALERT_EMAIL"]), security,
    )


def message_for(settings, event):
    release, state, detail = (event[key] for key in ("release", "state", "detail"))

    if not isinstance(release, str) or not re.fullmatch(r"[a-zA-Z0-9_.+-]{1,100}", release):
        raise ValueError("Invalid release ID")

    if state not in TERMINAL_STATES | {"waiting"} or not isinstance(detail, str) or len(detail) > 2000:
        raise ValueError("Invalid release notice")

    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message["Subject"] = f"[Papyrus release] {release} needs attention"
    message.set_content(f"Release {release} ({state})\n\n{detail}\n")
    return message


def deliver(settings, message):
    context = ssl.create_default_context()

    if settings.security == "ssl":
        connection = smtplib.SMTP_SSL(settings.host, settings.port, timeout=12, context=context)
    else:
        connection = smtplib.SMTP(settings.host, settings.port, timeout=12)

    with connection:
        if settings.security == "starttls":
            connection.starttls(context=context)

        connection.login(settings.username, settings.password)

        if message is not None:
            connection.send_message(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True, help="SMTP and ALERT_EMAIL settings file")
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Authenticate to SMTP without sending mail")
    options = parser.parse_args(argv)

    try:
        settings = read_settings(options.settings, options.password_file)
        message = None

        if not options.check:
            payload = sys.stdin.buffer.read(PAYLOAD_LIMIT + 1)

            if len(payload) > PAYLOAD_LIMIT:
                raise ValueError("Release notice is too large")

            message = message_for(settings, json.loads(payload))

        deliver(settings, message)
    except (KeyError, OSError, TypeError, ValueError, smtplib.SMTPException) as error:
        print(f"release mail: {type(error).__name__}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
