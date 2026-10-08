from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

from .config import (
    CALENDAR_URL,
    RANK_AJAX,
    RANK_PAGE,
    USER_AGENT,
    YEAR_AJAX,
    YEAR_PAGE,
    WeekEvent,
    beijing_now,
)

CSRF_RE = re.compile(
    r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)|'
    r'content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token["\']',
    re.I,
)
SCORE_AJAX_RE = re.compile(r"url:\s*\"(https://www\.live-tennis\.cn/zh/survivor/event/\d+/score)\"")
DETAIL_AJAX_RE = re.compile(r"url:\s*\"(https://www\.live-tennis\.cn/zh/survivor/event/\d+/\d+/detail)\"")
LEVEL_RE = re.compile(r"level_logo/(?:ATP|WTA)-([^./]+)", re.I)
DRAW_NAME_PREFIX_RE = re.compile(r"^(?:[WQL]|\d+)\s*")
DAY_SCORE_RE = re.compile(r"当前分数：(\d+)")
ROUND_ORDER = {
    "R128": 1,
    "R64": 2,
    "R32": 3,
    "R16": 4,
    "QF": 5,
    "SF": 6,
    "F": 7,
    "W": 8,
    "冠军": 8,
    "决赛": 7,
    "半决赛": 6,
    "四分之一决赛": 5,
}


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
class DrawPlayer:
    player_id: str
    name: str
    round: str = ""
    eliminated: bool = False
    live: bool = False

    @property
    def round_rank(self) -> int:
        return ROUND_ORDER.get(self.round, 0)


@dataclass
class DrawStatus:
    by_id: dict[str, DrawPlayer]
    by_name: dict[str, DrawPlayer]
    round_points: dict[str, int] = field(default_factory=dict)
    day_points: list[int] = field(default_factory=list)

    def lookup(self, player_id: str | None, name: str | None) -> DrawPlayer | None:
        pid = str(player_id or "").strip()
        if pid and pid != "0" and pid in self.by_id:
            return self.by_id[pid]
        label = str(name or "").strip()
        if label and label in self.by_name:
            return self.by_name[label]
        return None


def _clean_draw_name(tag) -> str:
    return DRAW_NAME_PREFIX_RE.sub("", tag.get_text(" ", strip=True)).strip()


def _draw_row_cells(tr) -> list[dict[str, Any]]:
    cells = []
    for td in tr.find_all("td", recursive=False):
        classes = td.get("class") or []
        if "cDrawSeq" in classes:
            continue
        pnames = td.select("pname")
        players = []
        for pname in pnames:
            pid = str(pname.get("data-id") or "").strip()
            if not pid or pid in {"LIVE", "0"}:
                continue
            players.append((pid, _clean_draw_name(pname)))
        text = " ".join(td.get_text(" ", strip=True).split())
        cells.append(
            {
                "players": players,
                "live": "进行中" in text or any(p.get("data-id") == "LIVE" for p in pnames),
                "text": text,
            }
        )
    return cells


def _first_player_in_rows(rows: list[list[dict[str, Any]]], col: int) -> tuple[str, str] | None:
    for cells in rows:
        if col < len(cells) and cells[col]["players"]:
            return cells[col]["players"][0]
    return None


def _live_pairs_from_table(table) -> list[tuple[tuple[str, str] | None, tuple[str, str] | None]]:
    body = table.find("tbody") or table
    rows = [_draw_row_cells(tr) for tr in body.find_all("tr", recursive=False)]
    if not rows:
        rows = [_draw_row_cells(tr) for tr in table.select("tr")]
    pairs = []
    for index, cells in enumerate(rows):
        for col, cell in enumerate(cells):
            if not cell["live"]:
                continue
            player_col = col - 1 if col > 0 else 0
            span = 2 ** (player_col + 1)
            start = (index // span) * span
            group = rows[start : start + span]
            if len(group) < span:
                continue
            half = span // 2
            pairs.append(
                (
                    _first_player_in_rows(group[:half], player_col),
                    _first_player_in_rows(group[half:], player_col),
                )
            )
    return pairs


def parse_draw_status(html: str, gender: str) -> DrawStatus:
    soup = BeautifulSoup(html, "html.parser")
    part_id = f"{gender}_ENTRY"
    entry = soup.find("div", class_="cDrawPart", attrs={"data-id": part_id})
    by_id: dict[str, DrawPlayer] = {}
    if entry:
        for item in entry.select(".cDrawEntry"):
            player_id = ""
            img = item.select_one(".cDrawEntryPortraitImg")
            if img and img.get("data-original"):
                player_id = str(img["data-original"]).rstrip("/").split("/")[-1]
            name_el = item.select_one(".cDrawEntryPlayer")
            name = name_el.get_text(" ", strip=True) if name_el else ""
            name = DRAW_NAME_PREFIX_RE.sub("", name).strip()
            round_el = item.select_one(".cDrawEntryRank")
            round_name = round_el.get_text(strip=True) if round_el else ""
            eliminated = bool(item.select_one(".cDrawEntryEliminated"))
            if not player_id and not name:
                continue
            player = DrawPlayer(
                player_id=player_id,
                name=name,
                round=round_name,
                eliminated=eliminated,
            )
            if player_id:
                by_id[player_id] = player
    live_ids: set[str] = set()
    live_names: set[str] = set()
    for part in soup.select(".cDrawPart"):
        if part.get("data-id") != gender:
            continue
        for table in part.select("table.cDrawBlock"):
            for left, right in _live_pairs_from_table(table):
                for item in (left, right):
                    if not item:
                        continue
                    live_ids.add(item[0])
                    if item[1]:
                        live_names.add(item[1])
    for player in by_id.values():
        if player.player_id in live_ids or player.name in live_names:
            player.live = True
    by_name = {player.name: player for player in by_id.values() if player.name}
    return DrawStatus(by_id=by_id, by_name=by_name, round_points=_parse_round_points(soup, gender))


def _parse_round_points(soup: BeautifulSoup, gender: str) -> dict[str, int]:
    pap = soup.find("div", class_="cDrawPart", attrs={"data-id": "PAP"})
    if not pap:
        return {}
    table = pap.find("table", class_="cDrawPointAndPrizeTable", attrs={"data-id": gender})
    if not table:
        return {}
    points: dict[str, int] = {}
    for row in table.select("tbody tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
        if len(cells) < 2 or not cells[0]:
            continue
        try:
            points[cells[0]] = int(cells[1].replace(",", ""))
        except ValueError:
            continue
    return points


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


@dataclass
class TourWeek:
    this_monday: datetime
    current_start: datetime
    current_events: list[dict[str, str]]
    previous_events: list[dict[str, str]]
    next_events: list[dict[str, str]]

    @property
    def current_names(self) -> list[str]:
        return [event["name"] for event in self.current_events if event.get("name")]

    @property
    def drop_monday(self) -> datetime:
        return self.current_start - timedelta(weeks=52)


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
        found: dict[str, list[WeekEvent]] = {}
        for tennis_type in ("atp", "wta"):
            events: list[WeekEvent] = []
            for info in self.tour_week(tennis_type).current_events:
                if not info.get("id"):
                    continue
                event = WeekEvent(
                    tennis_type=tennis_type,
                    name=info.get("name") or "",
                    page_id=info["id"],
                    year=info.get("year") or "",
                    gender=info.get("gender") or "",
                    level=info.get("level") or "",
                )
                self._fill_ajax_ids(event)
                events.append(event)
            found[tennis_type] = events
        self._current_events = found
        return self._current_events

    def _fill_ajax_ids(self, event: WeekEvent, fill_details: bool = True) -> None:
        score_html = self._get(event.score_page).text
        score_match = SCORE_AJAX_RE.search(score_html)
        if not score_match:
            raise FetchError(f"找不到本周积分接口: {event.score_page}")
        event.ajax_id = score_match.group(1).rstrip("/").split("/")[-2]
        if not fill_details:
            return
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

    def fetch_draw_status(self, event: WeekEvent) -> DrawStatus:
        url = f"https://www.live-tennis.cn/zh/draw/ajax/{event.page_id}/{event.year}/device/0/horizontal/true"
        try:
            html = self.session.get(
                url,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://www.live-tennis.cn/zh/draw/{event.page_id}/{event.year}",
                },
                timeout=30,
            ).text
        except requests.RequestException:
            return DrawStatus(by_id={}, by_name={})
        status = parse_draw_status(html, event.gender)
        try:
            my_html = self._get(event.my_page).text
        except requests.RequestException:
            my_html = ""
        status.day_points = [int(value) for value in DAY_SCORE_RE.findall(my_html)]
        return status

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
            current = beijing_now()
        current = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return current - timedelta(days=current.weekday())

    def events_on_monday(self, monday: datetime, tennis_type: str) -> list[dict[str, str]]:
        rows = self.calendar_events(monday.year)
        target = monday.strftime("%m-%d")
        for row in rows:
            if row["date"] == target:
                return list(row.get(tennis_type) or [])
        return []

    def tour_schedule(self, tennis_type: str) -> list[tuple[datetime, list[dict[str, str]]]]:
        grouped: dict[datetime, list[dict[str, str]]] = {}
        year = beijing_now().year
        for item in self.list_tour_events(tennis_type, years=[year - 1, year]):
            monday = item.get("monday")
            if not isinstance(monday, datetime):
                continue
            key = monday.replace(hour=0, minute=0, second=0, microsecond=0)
            grouped.setdefault(key, []).append(
                {
                    "name": item.get("name") or "",
                    "id": item.get("id") or "",
                    "year": item.get("year") or "",
                    "gender": item.get("gender") or "",
                    "level": item.get("level") or "",
                }
            )
        return sorted(grouped.items(), key=lambda item: item[0])

    def tour_week(self, tennis_type: str, this_monday: datetime | str | None = None) -> TourWeek:
        monday = self.week_monday(this_monday)
        schedule = self.tour_schedule(tennis_type)
        this_events = [event for start, events in schedule if start.date() == monday.date() for event in events]
        before = [(start, events) for start, events in schedule if start.date() < monday.date()]
        after = [(start, events) for start, events in schedule if start.date() > monday.date()]
        if this_events:
            current_start, current = monday, this_events
            previous = before[-1][1] if before else []
        elif before:
            current_start, current = before[-1]
            previous = before[-2][1] if len(before) > 1 else []
        else:
            current_start, current, previous = monday, [], []
        return TourWeek(
            this_monday=monday,
            current_start=current_start,
            current_events=current,
            previous_events=previous,
            next_events=after[0][1] if after else [],
        )

    def week_pair(self, this_monday: datetime | str | None, tennis_type: str) -> WeekPair:
        week = self.tour_week(tennis_type, this_monday)
        last_monday = week.drop_monday
        return WeekPair(
            this_monday=week.current_start,
            last_monday=last_monday,
            this_events=week.current_events,
            last_events=self.events_on_monday(last_monday, tennis_type),
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

    def list_tour_events(self, tennis_type: str, years: list[int] | None = None) -> list[dict[str, Any]]:
        current_year = beijing_now().year
        years = years or list(range(current_year - 2, current_year + 1))
        events: list[dict[str, Any]] = []
        for year in years:
            rows = self.calendar_events(year)
            for index, row in enumerate(rows):
                mmdd = row.get("date") or ""
                try:
                    month = int(str(mmdd).split("-")[0])
                    actual_year = year - 1 if month >= 11 and index < 10 else year
                    monday = datetime.strptime(f"{actual_year}-{mmdd}", "%Y-%m-%d")
                except (TypeError, ValueError):
                    continue
                for info in row.get(tennis_type) or []:
                    if not info.get("name"):
                        continue
                    events.append({**info, "monday": monday, "season_year": year})
        events.sort(key=lambda item: (item["monday"], item.get("name") or ""))
        return events

    def fetch_event_results(self, info: dict[str, Any]) -> list[dict[str, Any]]:
        event = WeekEvent(
            tennis_type="atp" if str(info.get("gender") or "").upper() == "MS" else "wta",
            name=str(info.get("name") or ""),
            page_id=str(info.get("id") or ""),
            year=str(info.get("year") or ""),
            gender=str(info.get("gender") or ""),
        )
        try:
            self._fill_ajax_ids(event, fill_details=False)
        except (FetchError, requests.RequestException):
            return []
        if not event.ajax_id:
            return []
        url = f"https://www.live-tennis.cn/zh/survivor/event/{event.ajax_id}/score"
        try:
            return self._post_datatable(url, event.score_page, page_size=1000)
        except (FetchError, requests.RequestException):
            return []


def strip_username(raw: str | None) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(str(raw), "html.parser").get_text()
    pos = text.rfind(">")
    return (text[pos + 1 :] if pos != -1 else text).strip()
