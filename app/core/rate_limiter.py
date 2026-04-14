"""Per-account rate limiter + anti-ban state machine.

Adapted from tg-harvester/rate_limiter.py with fixes:
- No fire-and-forget asyncio.create_task() without storage
- Single unified state (no _is_resting vs _banned_until conflict)
- Work-duration computed once per cycle via _compute_work_duration()
- FloodWait > threshold → mark banned without sleeping (redistribute work)
"""
import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from loguru import logger

from app.settings import RateLimitsCfg


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AccountRuntime:
    account_id: int
    premium_multiplier: float = 1.0

    groups_today: int = 0
    members_today: int = 0
    messages_today: int = 0
    profiles_today: int = 0
    searches_today: int = 0
    groups_hour: int = 0
    hour_slot: int = 0

    floods_hour: int = 0
    last_flood_at: datetime | None = None
    backoff_multiplier: float = 1.0

    work_started_at: datetime | None = None
    work_duration_sec: int = 0
    rest_until: datetime | None = None

    banned_until: datetime | None = None
    ban_reason: str = ""

    last_action_at: datetime | None = None

    day_date: datetime | None = None


class RateLimiter:
    """One instance per account."""

    OP_DELAY_KEYS = {
        "group": "delay_between_groups",
        "messages_batch": "delay_between_messages_batch",
        "profile": "delay_between_profiles",
        "search": "delay_between_searches",
    }

    def __init__(
        self,
        cfg: RateLimitsCfg,
        runtime: AccountRuntime,
        persist: Callable[[dict], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.rt = runtime
        self._persist = persist
        self._lock = asyncio.Lock()
        self._compute_work_duration()

    # ------------ state ------------

    def _compute_work_duration(self) -> None:
        lo, hi = self.cfg.account_work_minutes
        self.rt.work_duration_sec = random.randint(int(lo), int(hi)) * 60

    def _in_night_pause(self) -> bool:
        lo, hi = self.cfg.night_pause_utc
        now_h = _utcnow().hour
        if lo <= hi:
            return lo <= now_h < hi
        return now_h >= lo or now_h < hi

    def _roll_day_counters(self) -> None:
        today = _utcnow().date()
        if self.rt.day_date != today:
            self.rt.day_date = today  # type: ignore[assignment]
            self.rt.groups_today = 0
            self.rt.members_today = 0
            self.rt.messages_today = 0
            self.rt.profiles_today = 0
            self.rt.searches_today = 0

    def _roll_hour_counters(self) -> None:
        now = _utcnow()
        slot = int(now.timestamp() // 3600)
        if self.rt.hour_slot != slot:
            self.rt.hour_slot = slot
            self.rt.groups_hour = 0
            if (
                self.rt.last_flood_at is not None
                and (now - self.rt.last_flood_at).total_seconds()
                > self.cfg.flood_reset_hours * 3600
            ):
                self.rt.floods_hour = 0
                self.rt.backoff_multiplier = 1.0

    def is_banned(self) -> bool:
        if self.rt.banned_until and _utcnow() < self.rt.banned_until:
            return True
        if self.rt.banned_until and _utcnow() >= self.rt.banned_until:
            self.rt.banned_until = None
            self.rt.ban_reason = ""
        return False

    def is_resting(self) -> bool:
        if self.rt.rest_until is None:
            return False
        if _utcnow() >= self.rt.rest_until:
            self.rt.rest_until = None
            self.rt.work_started_at = None
            self._compute_work_duration()
            return False
        return True

    def should_rest(self) -> bool:
        if self.rt.work_started_at is None:
            return False
        elapsed = (_utcnow() - self.rt.work_started_at).total_seconds()
        return elapsed >= self.rt.work_duration_sec

    def start_rest(self) -> None:
        lo, hi = self.cfg.account_rest_minutes
        rest = random.randint(int(lo), int(hi))
        self.rt.rest_until = _utcnow() + timedelta(minutes=rest)
        self.rt.work_started_at = None

    def can_continue(self) -> tuple[bool, str]:
        self._roll_day_counters()
        self._roll_hour_counters()
        if self.is_banned():
            return False, f"banned until {self.rt.banned_until}"
        if self.is_resting():
            return False, f"resting until {self.rt.rest_until}"
        if self._in_night_pause():
            return False, "night pause"
        cap = int(self.cfg.max_groups_per_day * self.rt.premium_multiplier)
        if self.rt.groups_today >= cap:
            return False, f"daily cap {cap}"
        cap_h = int(self.cfg.max_groups_per_hour * self.rt.premium_multiplier)
        if self.rt.groups_hour >= cap_h:
            return False, f"hourly cap {cap_h}"
        if self.should_rest():
            self.start_rest()
            return False, "work/rest cycle"
        return True, ""

    # ------------ throttle ------------

    async def throttle(self, operation: str) -> None:
        async with self._lock:
            if self.rt.work_started_at is None:
                self.rt.work_started_at = _utcnow()

            delay = self._delay_for(operation)
            delay *= self.rt.backoff_multiplier
            delay = min(delay, 120.0)

            if self.rt.last_action_at is not None:
                elapsed = (_utcnow() - self.rt.last_action_at).total_seconds()
                if elapsed < delay:
                    await asyncio.sleep(delay - elapsed)
            else:
                if delay > 0:
                    await asyncio.sleep(random.uniform(0, min(1.0, delay)))

            self.rt.last_action_at = _utcnow()

    def _delay_for(self, operation: str) -> float:
        key = self.OP_DELAY_KEYS.get(operation)
        if key is None:
            return 0.0
        val = getattr(self.cfg, key)
        if isinstance(val, (tuple, list)):
            return random.uniform(float(val[0]), float(val[1]))
        return float(val)

    # ------------ counters ------------

    def bump(self, field_name: str, amount: int = 1) -> None:
        cur = getattr(self.rt, field_name, 0)
        setattr(self.rt, field_name, cur + amount)
        if field_name == "groups_today":
            self.rt.groups_hour += amount

    # ------------ flood ------------

    async def handle_flood(self, seconds: int) -> bool:
        """Return True if we just slept; False if account was marked long-banned."""
        self._roll_hour_counters()
        self.rt.floods_hour += 1
        self.rt.last_flood_at = _utcnow()

        if seconds >= self.cfg.flood_long_threshold_seconds:
            self.rt.banned_until = _utcnow() + timedelta(seconds=seconds)
            self.rt.ban_reason = f"FloodWait {seconds}s"
            logger.warning(
                "account {} hit long FloodWait {}s → marked banned until {}",
                self.rt.account_id, seconds, self.rt.banned_until,
            )
            return False

        if self.rt.floods_hour >= self.cfg.max_flood_waits_before_pause:
            self.rt.rest_until = _utcnow() + timedelta(minutes=60)
            logger.warning(
                "account {} hit {} floods this hour → 60min pause",
                self.rt.account_id, self.rt.floods_hour,
            )

        self.rt.backoff_multiplier *= self.cfg.flood_wait_multiplier
        sleep_for = min(float(seconds) + random.uniform(1, 3), 120.0)
        logger.info("account {} sleeping {:.1f}s on FloodWait", self.rt.account_id, sleep_for)
        await asyncio.sleep(sleep_for)
        return True

    def mark_long_ban(self, seconds: int, reason: str) -> None:
        self.rt.banned_until = _utcnow() + timedelta(seconds=seconds)
        self.rt.ban_reason = reason
