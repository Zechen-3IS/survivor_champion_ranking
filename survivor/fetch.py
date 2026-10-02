from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

from .config import (
    CALENDAR_URL,
    MENU_URL,
    RANK_AJAX,
    RANK_PAGE,
    USER_AGENT,
    YEAR_AJAX,
    YEAR_PAGE,
    WeekEvent,
)

CSRF_RE = re.compile(
    r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)|'
    r'content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token["\']',
    re.I,
)
EVENT_RE = re.compile(
    r'href="https://www\.live-tennis\.cn/zh/survivor/event/([^/]+)/(\d+)/((?:MS|WS))/my"\s*>\s*(ATP|WTA)\s+([^<]+)',
    re.I,
)
SCORE_AJAX_RE = re.compile(r"url:\s*\"(https://www\.live-tennis\.cn/zh/survivor/event/\d+/score)\"")
DETAIL_AJAX_RE = re.compile(r"url:\s*\"(https://www\.live-tennis\.cn/zh/survivor/event/\d+/\d+/detail)\"")
LEVEL_RE = re.compile(r"level_logo/(?:ATP|WTA)-([^./]+)", re.I)


def event_level_label(src: str | None) -> str:
    if not src:
        return ""
    match = LEVEL_RE.search(src)
    if not match:
        return ""
    kind = match.group(1).split("-")[0].upper()
    aliases = {"GS": "大满贯", "FINAL": "总决赛", "FINALS": "总决赛"}
    if kind in aliases:
        return aliases[kind]
    if kind.isdigit():
        return f"{kind}赛"
    return kind


class FetchError(RuntimeError):
    pass


@dataclass
class WeekPair:
    this_monday: datetime
    last_monday: datetime
    this_events: list[dict[str, str]]
    last_events: list[dict[str, str]]

    @property
    def this_names(self) -> list[str]:
        return [event["name"] for event in self.this_events if event.get("name")]

    @property
    def last_names(self) -> list[str]:
        return [event["name"] for event in self.last_events if event.get("name")]


class LiveTennisClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        self._calendars: dict[int, list[dict[str, Any]]] = {}
        self._current_events: dict[str, list[WeekEvent]] | None = None

    def _csrf(self, html: str) -> str:
        match = CSRF_RE.search(html)
        if not match:
            return ""
        return match.group(1) or match.group(2) or ""

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response

    def _post_datatable(self, url: str, referer: str, page_size: int = 100) -> list[dict[str, Any]]:
        html = self._get(referer).text
        csrf = self._csrf(html)
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.live-tennis.cn",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }
        if csrf:
            headers["X-CSRF-TOKEN"] = csrf

        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = {
                "draw": "1",
                "start": str(start),
                "length": str(page_size),
                "search[value]": "",
                "search[regex]": "false",
                "device": "0",
            }
            response = self.session.post(url, headers=headers, data=payload, timeout=30)
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise FetchError(f"非 JSON 响应: {url} {response.text[:200]}") from exc

            chunk = body.get("data") or []
            if not chunk:
                break
            rows.extend(chunk)
            total = body.get("recordsTotal")
            if total is not None and len(rows) >= int(total):
                break
            if len(chunk) < page_size:
                break
            start += page_size
        return rows

    def discover_current_events(self) -> dict[str, list[WeekEvent]]:
        if self._current_events is not None:
            return self._current_events
        html = self._get(MENU_URL).text
        parts = re.split(r"<li><a disabled", html)
        current_html = parts[2] if len(parts) > 2 else html
        found: dict[str, list[WeekEvent]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()
        for page_id, year, gender, label, name in EVENT_RE.findall(current_html):
            tennis_type = "atp" if label.upper() == "ATP" else "wta"
            key = (tennis_type, page_id)
            if key in seen:
                continue
            seen.add(key)
            found[tennis_type].append(
                WeekEvent(
                    tennis_type=tennis_type,
                    name=name.strip(),
                    page_id=page_id,
                    year=year,
                    gender=gender.upper(),
                )
            )
        for events in found.values():
            for event in events:
                self._fill_ajax_ids(event)
        self._current_events = dict(found)
        return self._current_events

    def _fill_ajax_ids(self, event: WeekEvent) -> None:
        score_html = self._get(event.score_page).text
        score_match = SCORE_AJAX_RE.search(score_html)
        if not score_match:
            raise FetchError(f"找不到本周积分接口: {event.score_page}")
        event.ajax_id = score_match.group(1).rstrip("/").split("/")[-2]
        detail_html = self._get(event.detail_page).text
        if not DETAIL_AJAX_RE.search(detail_html):
            raise FetchError(f"找不到本周明细接口: {event.detail_page}")

    def fetch_race_rank(self, tennis_type: str) -> list[dict[str, Any]]:
        return self._post_datatable(RANK_AJAX[tennis_type], RANK_PAGE[tennis_type], page_size=1000)

    def fetch_year_rank(self, tennis_type: str) -> tuple[list[dict[str, Any]], str | None]:
        html = self._get(YEAR_PAGE[tennis_type]).text
        csrf = self._csrf(html)
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.live-tennis.cn",
            "Referer": YEAR_PAGE[tennis_type],
            "X-Requested-With": "XMLHttpRequest",
        }
        if csrf:
            headers["X-CSRF-TOKEN"] = csrf
        response = self.session.post(
            YEAR_AJAX[tennis_type],
            headers=headers,
            data={"draw": "1", "start": "0", "length": "1000", "device": "0"},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        return body.get("data") or [], body.get("mondy") or body.get("monday")

    def fetch_week_scores(self, event: WeekEvent) -> list[dict[str, Any]]:
        if not event.ajax_id:
            self._fill_ajax_ids(event)
        url = f"https://www.live-tennis.cn/zh/survivor/event/{event.ajax_id}/score"
        return self._post_datatable(url, event.score_page)

    def fetch_week_details(self, event: WeekEvent) -> list[dict[str, Any]]:
        if not event.ajax_id:
            self._fill_ajax_ids(event)
        url = f"https://www.live-tennis.cn/zh/survivor/event/{event.ajax_id}/{event.year}/detail"
        return self._post_datatable(url, event.detail_page)

    def calendar_events(self, year: int) -> list[dict[str, Any]]:
        if year in self._calendars:
            return self._calendars[year]
        html = self._get(CALENDAR_URL.format(year=year)).text
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict[str, Any]] = []
        for row in soup.select(".cSurvivorCalendarRow"):
            date_el = row.select_one(".cSurvivorDateColumn")
            if not date_el:
                continue
            date = date_el.get_text(strip=True)

            def _events(selector: str) -> list[dict[str, str]]:
                cell = row.select_one(selector)
                if not cell:
                    return []
                found = []
                for link in cell.select("a.cSurvivorCellContent"):
                    name_el = link.select_one(".cSurvivorCityName")
                    href = link.get("href") or ""
                    match = re.search(r"/event/([^/]+)/(\d+)/((?:MS|WS))", href)
                    img = link.select_one("img.cSurvivorLevelImg")
                    found.append(
                        {
                            "name": name_el.get_text(strip=True) if name_el else "",
                            "id": match.group(1) if match else "",
                            "year": match.group(2) if match else "",
                            "gender": match.group(3) if match else "",
                            "level": event_level_label(img.get("src") if img else ""),
                        }
                    )
                return found

            rows.append({"date": date, "atp": _events(".cSurvivorMSColumn"), "wta": _events(".cSurvivorWSColumn")})
        self._calendars[year] = rows
        return rows

    @staticmethod
    def week_monday(value: datetime | str | None = None) -> datetime:
        if isinstance(value, str) and value:
            current = datetime.strptime(value[:10], "%Y-%m-%d")
        elif isinstance(value, datetime):
            current = value
        else:
            current = datetime.now()
        current = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return current - timedelta(days=current.weekday())

    def events_on_monday(self, monday: datetime, tennis_type: str) -> list[dict[str, str]]:
        rows = self.calendar_events(monday.year)
        target = monday.strftime("%m-%d")
        for row in rows:
            if row["date"] == target:
                return list(row.get(tennis_type) or [])
        return []

    def week_pair(self, this_monday: datetime | str | None, tennis_type: str) -> WeekPair:
        current = self.week_monday(this_monday)
        previous = current - timedelta(weeks=52)
        return WeekPair(
            this_monday=current,
            last_monday=previous,
            this_events=self.events_on_monday(current, tennis_type),
            last_events=self.events_on_monday(previous, tennis_type),
        )

    def prior_event_year(
        self,
        name: str,
        tennis_type: str,
        last_monday: datetime,
        this_monday: datetime,
    ) -> str | None:
        years: list[str] = []
        for year in range(last_monday.year, this_monday.year + 1):
            for row in self.calendar_events(year):
                try:
                    row_date = datetime.strptime(f"{year}-{row['date']}", "%Y-%m-%d")
                except (KeyError, TypeError, ValueError):
                    continue
                if not (last_monday.date() <= row_date.date() < this_monday.date()):
                    continue
                for event in row.get(tennis_type) or []:
                    if event.get("name") == name:
                        years.append(event.get("year") or str(year))
        return years[-1] if years else None

    def last_year_events(self, monday: str | None, tennis_type: str) -> list[str]:
        return self.week_pair(monday, tennis_type).last_names

    def previous_edition(
        self,
        name: str,
        tennis_type: str,
        this_monday: datetime | None = None,
    ) -> list[dict[str, str]]:
        if not name:
            return []
        current = self.week_monday(this_monday)
        target = (current.month, current.day)
        found: list[tuple[int, dict[str, str]]] = []
        for row in self.calendar_events(current.year - 1):
            try:
                month, day = [int(part) for part in row["date"].split("-")]
            except (KeyError, TypeError, ValueError):
                continue
            delta = abs((month - target[0]) * 31 + (day - target[1]))
            for event in row.get(tennis_type) or []:
                if event.get("name") == name:
                    found.append((delta, event))
        found.sort(key=lambda item: item[0])
        return [item[1] for item in found[:1]]


def strip_username(raw: str | None) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(str(raw), "html.parser").get_text()
    pos = text.rfind(">")
    return (text[pos + 1 :] if pos != -1 else text).strip()
