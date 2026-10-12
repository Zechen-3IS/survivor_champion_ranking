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
SEED_PREFIX_RE = re.compile(r"^(\d+)\s*")
DAY_SCORE_RE = re.compile(r"当前分数：(\d+)")
FIRST_DAY_BYE_RE = re.compile(r"本站有(\d+)个首日轮空")
PICK_ROUND_RE = re.compile(r"当前轮次：(\S+)")
PICK_SELECTOR_RE = re.compile(r"Day\s+(\d+)\s+(\d{1,2})/(\d{1,2})")
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
BRACKET_OPENING_ROUND = {
    128: "R128",
    64: "R64",
    32: "R32",
    16: "R16",
    8: "QF",
}


def field_bracket_size(n: int) -> int:
    if n <= 1:
        return max(n, 0)
    return 1 << (n - 1).bit_length()


def opening_round_name(n: int) -> str:
    return BRACKET_OPENING_ROUND.get(field_bracket_size(n), "")


def opening_round_rank(n: int) -> int:
    return ROUND_ORDER.get(opening_round_name(n), 0)


def assign_draw_byes(players: dict[str, DrawPlayer] | list[DrawPlayer]) -> None:
    items = list(players.values()) if isinstance(players, dict) else list(players)
    n_byes = field_bracket_size(len(items)) - len(items)
    if n_byes <= 0:
        return
    ranked = sorted((player for player in items if player.seed), key=lambda player: player.seed or 0)
    for player in ranked[:n_byes]:
        player.bye = True


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
    seed: int | None = None
    bye: bool = False

    @property
    def round_rank(self) -> int:
        return ROUND_ORDER.get(self.round, 0)


@dataclass
class DrawStatus:
    by_id: dict[str, DrawPlayer]
    by_name: dict[str, DrawPlayer]
    round_points: dict[str, int] = field(default_factory=dict)
    day_points: list[int] = field(default_factory=list)
    tennis_bye_count: int = 0
    bracket_size: int = 0
    first_day_bye_quota: int = 0
    is_slam: bool = False
    pick_days: list[dict[str, Any]] = field(default_factory=list)

    def lookup(self, player_id: str | None, name: str | None) -> DrawPlayer | None:
        pid = str(player_id or "").strip()
        if pid and pid != "0" and pid in self.by_id:
            return self.by_id[pid]
        label = str(name or "").strip()
        if label and label in self.by_name:
            return self.by_name[label]
        return None


def _parse_seed_and_name(text: str) -> tuple[int | None, str]:
    text = " ".join(str(text or "").split())
    match = SEED_PREFIX_RE.match(text)
    if match:
        return int(match.group(1)), text[match.end() :].strip()
    return None, DRAW_NAME_PREFIX_RE.sub("", text).strip()


def _clean_draw_name(tag) -> str:
    return _parse_seed_and_name(tag.get_text(" ", strip=True))[1]


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
            # 对手格里的「进行中」只说明下一轮对手未产生，不代表这一侧正在打球。
            if cell["players"] or col == 0:
                continue
            player_col = col - 1 if col > 0 else 0
            span = 2 ** (player_col + 1)
            start = (index // span) * span
            group = rows[start : start + span]
            if len(group) < span:
                continue
            half = span // 2
            left = _first_player_in_rows(group[:half], player_col)
            right = _first_player_in_rows(group[half:], player_col)
            if not left or not right:
                continue
            if left[0] in {"LIVE", "COMEUP"} or right[0] in {"LIVE", "COMEUP"}:
                continue
            pairs.append((left, right))
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
            seed, name = _parse_seed_and_name(name_el.get_text(" ", strip=True) if name_el else "")
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
                seed=seed,
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
    for part in soup.select(".cDrawPart"):
        if part.get("data-id") != gender:
            continue
        for pname in part.select("pname"):
            pid = str(pname.get("data-id") or "").strip()
            player = by_id.get(pid)
            if player is None:
                continue
            seed, name = _parse_seed_and_name(pname.get_text(" ", strip=True))
            if seed and not player.seed:
                player.seed = seed
            if name and not player.name:
                player.name = name
    assign_draw_byes(by_id)
    by_name = {player.name: player for player in by_id.values() if player.name}
    return DrawStatus(by_id=by_id, by_name=by_name, round_points=_parse_round_points(soup, gender))


def parse_pick_days(html: str, year: str | int | None = None) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    year_int = int(year) if str(year or "").isdigit() else beijing_now().year
    by_day: dict[int, dict[str, Any]] = {}
    for selector in soup.select(".cSurvivorDayPickSelector"):
        text = selector.get_text(" ", strip=True)
        match = PICK_SELECTOR_RE.search(text)
        if not match:
            continue
        day = int(match.group(1))
        date = datetime(year_int, int(match.group(2)), int(match.group(3))).date()
        by_day[day] = {"day": day, "date": date, "round": "", "open": False, "has_cards": False}
    for info in soup.select(".cSurvivorDayPickInfo"):
        cards = info.select(".cSurvivorSmallCard, .cSurvivorCard")
        card_days = [int(card.get("data-day") or 0) for card in cards if card.get("data-day")]
        card_dates = [card.get("data-date") for card in cards if card.get("data-date")]
        fillable = {str(card.get("data-fillable") or "") for card in cards}
        notice = " ".join(node.get_text(" ", strip=True) for node in info.select(".cSurvivorFillNotice"))
        round_match = PICK_ROUND_RE.search(notice)
        day = card_days[0] if card_days else int(info.get("data-id") or 0) + 1
        item = by_day.setdefault(day, {"day": day, "date": None, "round": "", "open": False, "has_cards": False})
        if card_dates and not item.get("date"):
            try:
                item["date"] = datetime.strptime(card_dates[0][:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        if round_match:
            item["round"] = round_match.group(1).strip()
        item["has_cards"] = bool(cards)
        item["open"] = item["open"] or ("0" in fillable or "1" in fillable)
    return [by_day[day] for day in sorted(by_day)]


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
        self._tour_weeks: dict[tuple[str, Any], TourWeek] = {}
        self._event_open: dict[str, bool] = {}
        self._event_live: dict[str, bool] = {}
        self._event_scores: dict[str, list[dict[str, Any]]] = {}
        self._event_details: dict[str, list[dict[str, Any]]] = {}

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
        rows = self._post_datatable(url, event.score_page)
        if rows:
            return rows
        return _scores_from_details(self.fetch_week_details(event))

    def fetch_week_details(self, event: WeekEvent) -> list[dict[str, Any]]:
        key = f"{event.page_id}|{event.year}|{event.name}"
        cached = self._event_details.get(key)
        if cached is not None:
            return cached
        if not event.ajax_id:
            self._fill_ajax_ids(event)
        url = f"https://www.live-tennis.cn/zh/survivor/event/{event.ajax_id}/{event.year}/detail"
        rows = self._post_datatable(url, event.detail_page)
        self._event_details[key] = rows
        return rows

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
        field = len(status.by_id)
        status.bracket_size = field_bracket_size(field)
        status.tennis_bye_count = max(status.bracket_size - field, 0)
        status.is_slam = event.level == "大满贯"
        try:
            my_html = self._get(event.my_page).text
        except requests.RequestException:
            my_html = ""
        status.day_points = [int(value) for value in DAY_SCORE_RE.findall(my_html)]
        notice = FIRST_DAY_BYE_RE.search(my_html)
        if notice:
            status.first_day_bye_quota = int(notice.group(1))
        status.pick_days = parse_pick_days(my_html, event.year)
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

    @staticmethod
    def _event_cache_key(info: dict[str, Any]) -> str:
        return f"{info.get('id')}|{info.get('year')}|{info.get('name')}"

    def _event_score_rows(self, info: dict[str, Any]) -> list[dict[str, Any]]:
        key = self._event_cache_key(info)
        if key not in self._event_scores:
            self._event_scores[key] = self.fetch_event_results(info)
        return self._event_scores[key]

    def _event_detail_rows(self, info: dict[str, Any]) -> list[dict[str, Any]]:
        key = self._event_cache_key(info)
        if key in self._event_details:
            return self._event_details[key]
        event = WeekEvent(
            tennis_type="atp" if str(info.get("gender") or "").upper() == "MS" else "wta",
            name=str(info.get("name") or ""),
            page_id=str(info.get("id") or ""),
            year=str(info.get("year") or ""),
            gender=str(info.get("gender") or ""),
            level=str(info.get("level") or ""),
        )
        try:
            self._fill_ajax_ids(event)
            rows = self.fetch_week_details(event)
        except (FetchError, requests.RequestException):
            rows = []
        self._event_details[key] = rows
        return rows

    def _draw_has_live(self, info: dict[str, Any]) -> bool:
        if not info.get("id"):
            return False
        event = WeekEvent(
            tennis_type="atp" if str(info.get("gender") or "").upper() == "MS" else "wta",
            name=str(info.get("name") or ""),
            page_id=str(info.get("id") or ""),
            year=str(info.get("year") or ""),
            gender=str(info.get("gender") or ""),
            level=str(info.get("level") or ""),
        )
        try:
            status = self.fetch_draw_status(event)
        except (FetchError, requests.RequestException):
            return False
        return any(player.live for player in status.by_id.values())

    def _event_still_live(self, info: dict[str, Any]) -> bool:
        key = self._event_cache_key(info)
        cached = self._event_live.get(key)
        if cached is not None:
            return cached
        rows = self._event_score_rows(info)
        live = any(item.get("fill_status") == "存活" for item in rows)
        if not live:
            live = self._draw_has_live(info)
        self._event_live[key] = live
        return live

    def _events_still_live(self, events: list[dict[str, str]]) -> bool:
        return any(self._event_still_live(info) for info in events if info.get("id"))

    def _events_already_open(self, events: list[dict[str, str]]) -> bool:
        for info in events:
            if not info.get("id"):
                continue
            key = self._event_cache_key(info)
            if key not in self._event_open:
                self._event_open[key] = bool(self._event_score_rows(info) or self._event_detail_rows(info))
            if self._event_open[key]:
                return True
        return False

    def tour_week(self, tennis_type: str, this_monday: datetime | str | None = None) -> TourWeek:
        monday = self.week_monday(this_monday)
        cache_key = (tennis_type, monday.date())
        cached = self._tour_weeks.get(cache_key)
        if cached is not None:
            return cached
        schedule = self.tour_schedule(tennis_type)
        this_events = [event for start, events in schedule if start.date() == monday.date() for event in events]
        before = [(start, events) for start, events in schedule if start.date() < monday.date()]
        after = [(start, events) for start, events in schedule if start.date() > monday.date()]
        next_start, upcoming = after[0] if after else (None, [])
        later = after[1][1] if len(after) > 1 else []
        if this_events:
            current_start, current = monday, this_events
            previous = before[-1][1] if before else []
            nxt = upcoming
        elif before:
            last_start, last_events = before[-1]
            previous_of_last = before[-2][1] if len(before) > 1 else []
            if self._events_still_live(last_events):
                current_start, current = last_start, last_events
                previous = previous_of_last
                nxt = upcoming
            elif upcoming and self._events_already_open(upcoming):
                current_start, current = next_start or monday, upcoming
                previous = last_events
                nxt = later
            else:
                current_start, current = monday, []
                previous = last_events
                nxt = upcoming
        elif upcoming and self._events_already_open(upcoming):
            current_start, current = next_start or monday, upcoming
            previous = []
            nxt = later
        else:
            current_start, current, previous, nxt = monday, [], [], upcoming
        week = TourWeek(
            this_monday=monday,
            current_start=current_start,
            current_events=current,
            previous_events=previous,
            next_events=nxt,
        )
        self._tour_weeks[cache_key] = week
        return week

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

    def fetch_event_day_points(self, info: dict[str, Any]) -> list[int]:
        event = WeekEvent(
            tennis_type="atp" if str(info.get("gender") or "").upper() == "MS" else "wta",
            name=str(info.get("name") or ""),
            page_id=str(info.get("id") or ""),
            year=str(info.get("year") or ""),
            gender=str(info.get("gender") or ""),
        )
        if not event.page_id or not event.year:
            return []
        try:
            html = self._get(event.my_page).text
        except requests.RequestException:
            return []
        return [int(value) for value in DAY_SCORE_RE.findall(html)]


def _scores_from_details(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[Any, dict[str, Any]] = {}
    for item in details:
        uid = item.get("user_id")
        if uid is None or uid in rows:
            continue
        rows[uid] = {
            "user_id": uid,
            "username": item.get("username"),
            "fill_status": "存活",
            "day": 0,
            "score": 0,
            "players": "",
        }
    return list(rows.values())


def strip_username(raw: str | None) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(str(raw), "html.parser").get_text()
    pos = text.rfind(">")
    return (text[pos + 1 :] if pos != -1 else text).strip()
