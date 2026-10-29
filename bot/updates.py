"""Running version and NAS update status shared through the data volume.

The NAS scripts (`automatico.sh`, `aggiorna.sh`) write plain `key=value` files in
`<data>/update`; the bot only reads them and writes installation requests.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot import messages as texts

logger = logging.getLogger(__name__)

CHECK_FILE = "check"
INSTALL_FILE = "install"
REQUEST_FILE = "request"
NOTIFIED_FILE = "notified"

_REVISION_PATTERN = re.compile(r"[0-9a-f]{7,40}")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILE_BYTES = 8192
_MAX_TEXT_LENGTH = 200
INSTALL_RESULTS = frozenset({"ok", "rolled_back", "failed", "skipped"})


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _CONTROL_CHARACTERS.sub(" ", value).strip()
    return cleaned[:_MAX_TEXT_LENGTH] or None


def _revision(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower()
    return candidate if _REVISION_PATTERN.fullmatch(candidate) else None


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def same_revision(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    return first.startswith(second) or second.startswith(first)


def parse_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            fields[key.strip()] = value.strip()
    return fields


@dataclass(frozen=True, slots=True)
class RunningVersion:
    revision: str | None
    committed_at: datetime | None
    subject: str | None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RunningVersion:
        source = os.environ if environ is None else environ
        return cls(
            revision=_revision(source.get("APP_REVISION")),
            committed_at=_timestamp(source.get("APP_COMMIT_DATE")),
            subject=_clean_text(source.get("APP_COMMIT_SUBJECT")),
        )


@dataclass(frozen=True, slots=True)
class UpdateCheck:
    checked_at: datetime
    available_revision: str | None
    available_summary: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    result: str
    finished_at: datetime
    finished_at_raw: str
    revision: str | None
    summary: str | None
    reason: str | None


class UpdateStatusStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _read(self, name: str) -> dict[str, str] | None:
        path = self.directory / name
        try:
            if not path.is_file():
                return None
            with path.open("rb") as handle:
                raw = handle.read(_MAX_FILE_BYTES)
        except OSError as error:
            logger.warning("Could not read update status file %s: %s", name, type(error).__name__)
            return None
        return parse_fields(raw.decode("utf-8", errors="replace"))

    def _write(self, name: str, fields: Mapping[str, str]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        temporary = self.directory / f".{name}.tmp"
        body = "".join(f"{key}={_clean_text(value) or ''}\n" for key, value in fields.items())
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, target)

    def read_check(self) -> UpdateCheck | None:
        fields = self._read(CHECK_FILE)
        if fields is None:
            return None
        checked_at = _timestamp(fields.get("checked_at"))
        if checked_at is None:
            return None
        return UpdateCheck(
            checked_at=checked_at,
            available_revision=_revision(fields.get("available_revision")),
            available_summary=_clean_text(fields.get("available_summary")),
            error=_clean_text(fields.get("error")),
        )

    def read_install(self) -> InstallOutcome | None:
        fields = self._read(INSTALL_FILE)
        if fields is None:
            return None
        result = fields.get("result", "")
        raw_finished = fields.get("finished_at", "")
        finished_at = _timestamp(raw_finished)
        if result not in INSTALL_RESULTS or finished_at is None:
            return None
        return InstallOutcome(
            result=result,
            finished_at=finished_at,
            finished_at_raw=raw_finished,
            revision=_revision(fields.get("revision")),
            summary=_clean_text(fields.get("summary")),
            reason=_clean_text(fields.get("reason")),
        )

    def has_request(self) -> bool:
        return (self.directory / REQUEST_FILE).is_file()

    def request_install(self, now: datetime) -> None:
        self._write(REQUEST_FILE, {"requested_at": _utc_text(now)})

    def read_notified(self) -> dict[str, str]:
        return self._read(NOTIFIED_FILE) or {}

    def write_notified(self, fields: Mapping[str, str]) -> None:
        self._write(NOTIFIED_FILE, fields)


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown timezone for update messages, using UTC")
        return timezone.utc


def _local_text(moment: datetime, zone: tzinfo) -> str:
    return moment.astimezone(zone).strftime("%d/%m/%Y %H:%M")


def running_label(running: RunningVersion, zone: tzinfo) -> str | None:
    if running.revision is None:
        return None
    label = running.revision[:7]
    if running.committed_at is not None:
        label = texts.VERSION_WITH_DATE.format(
            short=label, date=_local_text(running.committed_at, zone)
        )
    if running.subject:
        label = f"{label} – {running.subject}"
    return label


def update_available(running: RunningVersion, check: UpdateCheck | None) -> bool:
    return (
        check is not None
        and check.available_revision is not None
        and running.revision is not None
        and not same_revision(running.revision, check.available_revision)
    )


def _available_summary(check: UpdateCheck) -> str:
    if check.available_summary:
        return check.available_summary
    return (check.available_revision or "")[:7]


def reason_text(reason: str | None) -> str:
    return texts.UPDATE_REASONS.get(reason or "", texts.UPDATE_REASON_UNKNOWN)


def describe_version(
    running: RunningVersion,
    check: UpdateCheck | None,
    install: InstallOutcome | None,
    *,
    request_pending: bool,
    zone: tzinfo,
) -> str:
    label = running_label(running, zone)
    lines = [
        texts.VERSION_RUNNING.format(version=label)
        if label is not None
        else texts.VERSION_UNKNOWN
    ]
    if check is None:
        lines.append(texts.VERSION_NOT_CHECKED)
    else:
        checked = _local_text(check.checked_at, zone)
        if check.error:
            lines.append(texts.VERSION_CHECK_FAILED.format(checked=checked))
        if update_available(running, check):
            lines.append(texts.VERSION_NEW.format(summary=_available_summary(check)))
        elif running.revision is None and check.available_revision is not None:
            lines.append(
                texts.VERSION_PUBLISHED.format(
                    summary=_available_summary(check), checked=checked
                )
            )
        elif not check.error and check.available_revision is not None:
            lines.append(texts.VERSION_LATEST.format(checked=checked))
    if install is not None and install.result in {"rolled_back", "failed"}:
        lines.append(
            texts.VERSION_LAST_FAILED.format(
                summary=install.summary or texts.VERSION_UNKNOWN_SHORT,
                reason=reason_text(install.reason),
            )
        )
    if request_pending:
        lines.append(texts.VERSION_REQUEST_PENDING)
    return "\n".join(lines)


def install_notice(install: InstallOutcome) -> str:
    summary = install.summary or texts.VERSION_UNKNOWN_SHORT
    reason = reason_text(install.reason)
    if install.result == "ok":
        return texts.UPDATE_DONE.format(summary=summary)
    if install.result == "rolled_back":
        return texts.UPDATE_ROLLED_BACK.format(summary=summary, reason=reason)
    if install.result == "skipped":
        return texts.UPDATE_SKIPPED.format(reason=reason)
    return texts.UPDATE_FAILED.format(summary=summary, reason=reason)


def pending_notices(
    store: UpdateStatusStore, running: RunningVersion
) -> tuple[list[str], dict[str, str]]:
    """Return admin notices not yet sent and the marker to store after sending."""
    notified = store.read_notified()
    marker = dict(notified)
    notices: list[str] = []
    install = store.read_install()
    if install is not None and install.finished_at_raw != notified.get("install_finished_at"):
        notices.append(install_notice(install))
        marker["install_finished_at"] = install.finished_at_raw
    check = store.read_check()
    if (
        check is not None
        and update_available(running, check)
        and check.available_revision != notified.get("available_revision")
    ):
        notices.append(texts.UPDATE_AVAILABLE.format(summary=_available_summary(check)))
        marker["available_revision"] = check.available_revision or ""
    return notices, marker
