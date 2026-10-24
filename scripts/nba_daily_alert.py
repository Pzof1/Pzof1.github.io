import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo("Europe/Moscow")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"


def api_call(method, payload):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except Exception as exc:
        raise RuntimeError("Telegram API request failed; secret values were not logged.") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram API rejected the request; its response was not logged.")
    return result.get("result")


def fetch_month(month_key):
    query = urlencode({"dates": month_key, "limit": "500"})
    request = Request(
        f"{ESPN_SCOREBOARD}?{query}",
        headers={"Accept": "application/json", "User-Agent": "NBA-Moscow-Schedule/1.0"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception:
        raise RuntimeError("Could not load the NBA schedule from ESPN.") from None
    if not isinstance(payload.get("events"), list):
        raise RuntimeError("ESPN returned an unexpected schedule response.")
    return payload["events"]


def scheduled_datetime(event):
    raw = event.get("date")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(MOSCOW)


def events_for_dates(today, tomorrow):
    keys = {
        (today - timedelta(days=1)).strftime("%Y%m"),
        today.strftime("%Y%m"),
        tomorrow.strftime("%Y%m"),
    }
    events_by_id = {}
    for key in sorted(keys):
        for event in fetch_month(key):
            event_id = event.get("id") or f"{event.get('date')}:{event.get('name')}"
            events_by_id[event_id] = event
    return events_by_id.values()


def send_message(text):
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is not configured in repository Actions secrets.")
    api_call("sendMessage", {"chat_id": chat_id, "text": text})


def run():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in repository Actions secrets.")

    mode = os.environ.get("NBA_ALERT_MODE", "auto").strip()
    if mode == "test":
        send_message("Дисциплина")
        print("Test notification sent.")
        return

    now = datetime.now(MOSCOW)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    if mode == "auto" and not os.environ.get("TELEGRAM_CHAT_ID", "").strip():
        print("TELEGRAM_CHAT_ID is not configured yet; no message was sent.")
        return
    if mode != "auto":
        raise RuntimeError("Unsupported alert mode.")

    tomorrow_midnight = datetime.combine(tomorrow, time.min, tzinfo=MOSCOW)
    has_today_game = False
    has_morning_game = False
    for event in events_for_dates(today, tomorrow):
        start = scheduled_datetime(event)
        if start is None:
            continue
        if now < start <= tomorrow_midnight:
            has_today_game = True
        if start.date() == tomorrow and time(6, 0) <= start.time().replace(tzinfo=None) <= time(12, 0):
            has_morning_game = True

    slot = os.environ.get("NBA_ALERT_SLOT", "").strip()
    if slot == "" or slot == "manual":
        slot = "evening" if now.hour == 21 else "day"
    if slot not in {"day", "evening"}:
        raise RuntimeError("Unsupported alert schedule slot.")

    if slot == "day":
        message = "Дисциплина" if has_today_game else None
    elif has_today_game and has_morning_game:
        message = "Дисциплина вечером и утром"
    elif has_morning_game:
        message = "Дисциплина утром"
    elif has_today_game:
        message = "Дисциплина"
    else:
        message = None

    if message is None:
        print("No matching games in the notification windows; no message was sent.")
        return

    send_message(message)
    print("NBA schedule notification sent.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Deliberately omit exception details because some network errors can contain request URLs.
        message = str(exc)
        if message.startswith("TELEGRAM_") or message.startswith("No ") or message.startswith("Unsupported "):
            print(message, file=sys.stderr)
        else:
            print("Notification failed; sensitive values were not written to the logs.", file=sys.stderr)
        sys.exit(1)
