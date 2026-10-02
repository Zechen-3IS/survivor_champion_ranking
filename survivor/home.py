from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape

from .config import WeekEvent
from .fetch import LiveTennisClient, strip_username

TOUR_LABEL = {"wta": "WTA", "atp": "ATP"}
SECTION_RE = re.compile(
    r'(<h2>(?:上周赛事结果|本周赛事|下周赛事预告)</h2>\s*<div class="card-container">)(.*?)(</div>)(?=\s*(?:<!--|<h2>))',
    re.S,
)


def _week_event(tennis_type: str, info: dict) -> WeekEvent:
    return WeekEvent(
        tennis_type=tennis_type,
        name=info.get("name") or "",
        page_id=info.get("id") or "",
        year=info.get("year") or "",
        gender=info.get("gender") or "",
        level=info.get("level") or "",
    )


def _match_info(name: str, level: str) -> str:
    level_html = f'<span class="tournament-level">{escape(level)}</span>' if level else ""
    return f"""
            <div class="match-info">
                <span class="tournament-name">{escape(name)}</span>
                {level_html}
            </div>"""


def _summarize(client: LiveTennisClient, event: WeekEvent) -> dict:
    rows = client.fetch_week_scores(event) if event.page_id else []
    alive = [strip_username(item.get("username")) for item in rows if item.get("fill_status") == "存活"]
    alive = [name for name in alive if name]
    scores = [int(item.get("score") or 0) for item in rows]
    top = max(scores) if scores else 0
    top_names = [
        strip_username(item.get("username"))
        for item in rows
        if int(item.get("score") or 0) == top
    ]
    top_names = [name for name in top_names if name]
    return {
        "event": event,
        "count": len(rows),
        "alive": alive,
        "top": top,
        "top_names": top_names,
    }


def _names(names: list[str]) -> str:
    return ", ".join(escape(name) for name in names)


def _result_card(label: str, info: dict) -> str:
    event = info["event"]
    match = _match_info(event.name, event.level)
    if info["alive"]:
        body = f'{match}\n            <div class="champion-info">恭喜{_names(info["alive"])}夺冠</div>'
        return f"""
            <div class="card champion-card ">
                <div class="card-icon">🏆</div>
                <div class="card-content">
                    <h3>{label}上周赛事</h3>
                    <div class="card-content-wrapper">{body}</div>
                </div>
                <div class="trophy-badge">🏆</div>
            </div>"""
    extra = ""
    if info["top_names"] and info["top"]:
        extra = (
            f'<div class="champion-info">恭喜{_names(info["top_names"])}'
            f'获得赛事最高分：{info["top"]}</div>'
        )
    body = f'{match}\n            <div class="no-champion">本站无人生还</div>{extra}'
    return f"""
            <div class="card  ">
                <div class="card-icon">🎾</div>
                <div class="card-content">
                    <h3>{label}上周赛事</h3>
                    <div class="card-content-wrapper">{body}</div>
                </div>
            </div>"""


def _rest_card(label: str) -> str:
    return f"""
            <div class="card rest-card ">
                <div class="card-icon">⏸️</div>
                <div class="card-content">
                    <h3>{label}下周赛事</h3>
                    <div class="card-content-wrapper"><div class="defending-no-champion">休赛期</div></div>
                </div>
            </div>"""


def _preview_card(label: str, event: WeekEvent) -> str:
    return f"""
            <div class="card  ">
                <div class="card-icon">📅</div>
                <div class="card-content">
                    <h3>{label}下周赛事</h3>
                    <div class="card-content-wrapper">{_match_info(event.name, event.level)}</div>
                </div>
            </div>"""


def _defending_block(info: dict) -> str:
    event = info["event"]
    match = _match_info(event.name, event.level)
    if info["alive"]:
        result = f'<div class="defending-champion">卫冕冠军：{_names(info["alive"])}</div>'
    elif info["top_names"] and info["top"]:
        result = (
            '<div class="defending-no-champion">卫冕赛事无人生还</div>'
            f'<div class="defending-champion">{_names(info["top_names"])}获得赛事最高分：{info["top"]}</div>'
        )
    else:
        result = '<div class="defending-no-champion">卫冕赛事无人生还</div>'
    return f"""
                <div class="defending-match-block">
                    {match}
            {result}
                </div>"""


def _this_week_card(label: str, current: dict, defending: list[dict]) -> str:
    event = current["event"]
    stats = f"""
            <div class="survivor-stats">
                <span class="participant-count">
                    <span class="stat-icon">👥</span>
                    <span class="stat-label">参赛: </span>
                    <span class="stat-value">{current["count"]}</span>
                </span>
                <span class="survivor-count">
                    <span class="stat-icon">✅</span>
                    <span class="stat-label">幸存: </span>
                    <span class="stat-value">{len(current["alive"])}</span>
                </span>
            </div>"""
    defend = ""
    if defending:
        blocks = []
        for idx, item in enumerate(defending):
            if idx:
                blocks.append('<hr class="defending-divider">')
            blocks.append(_defending_block(item))
        defend = f"""
            <div class="defending-section">
                <div class="defending-title">卫冕赛事：</div>
                {"".join(blocks)}
            </div>"""
    return f"""
            <a href="{escape(event.my_page)}" class="card-link">
                <div class="card  current-week-card">
                    <div class="card-icon">🎾</div>
                    <div class="card-content">
                        <h3>{label}本周赛事</h3>
                        <div class="card-content-wrapper">
            {_match_info(event.name, event.level)}
            {stats}
            {defend}
            </div>
                    </div>
                </div>
            </a>"""


def _empty_week_card(label: str, kind: str) -> str:
    title = f"{label}上周赛事" if kind == "last" else f"{label}本周赛事"
    return f"""
            <div class="card rest-card ">
                <div class="card-icon">⏸️</div>
                <div class="card-content">
                    <h3>{title}</h3>
                    <div class="card-content-wrapper"><div class="defending-no-champion">无赛事</div></div>
                </div>
            </div>"""


def build_home_sections(client: LiveTennisClient) -> dict[str, str]:
    this_monday = client.week_monday()
    prev_monday = this_monday - timedelta(weeks=1)
    next_monday = this_monday + timedelta(weeks=1)
    last_week: list[str] = []
    this_week: list[str] = []
    next_week: list[str] = []
    current_live = client.discover_current_events()

    for tennis_type in ("wta", "atp"):
        label = TOUR_LABEL[tennis_type]
        prev_events = client.events_on_monday(prev_monday, tennis_type)
        if prev_events:
            last_week.append(_result_card(label, _summarize(client, _week_event(tennis_type, prev_events[0]))))
        else:
            last_week.append(_empty_week_card(label, "last"))

        live = (current_live.get(tennis_type) or [None])[0]
        calendar_now = client.events_on_monday(this_monday, tennis_type)
        current = live or (_week_event(tennis_type, calendar_now[0]) if calendar_now else None)
        if current:
            if calendar_now and not current.level:
                current.level = calendar_now[0].get("level") or ""
            defending_events = client.previous_edition(current.name, tennis_type, this_monday)
            defending = [_summarize(client, _week_event(tennis_type, item)) for item in defending_events]
            this_week.append(_this_week_card(label, _summarize(client, current), defending))
        else:
            this_week.append(_empty_week_card(label, "this"))

        upcoming = client.events_on_monday(next_monday, tennis_type)
        if upcoming:
            next_week.append(_preview_card(label, _week_event(tennis_type, upcoming[0])))
        else:
            next_week.append(_rest_card(label))

    return {
        "上周赛事结果": "\n".join(last_week) + "\n            ",
        "本周赛事": "\n".join(this_week) + "\n            ",
        "下周赛事预告": "\n".join(next_week) + "\n            ",
    }


def render_index_page(client: LiveTennisClient, output_path) -> None:
    html = output_path.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = re.sub(r"页面数据更新时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", f"页面数据更新时间：{now}", html, count=1)
    sections = build_home_sections(client)
    heading = {"上周赛事结果", "本周赛事", "下周赛事预告"}

    def _replace(match: re.Match[str]) -> str:
        title = re.search(r"<h2>(.*?)</h2>", match.group(1))
        name = title.group(1) if title else ""
        if name not in heading:
            return match.group(0)
        return match.group(1) + sections[name] + match.group(3)

    html = SECTION_RE.sub(_replace, html)
    output_path.write_text(html, encoding="utf-8")
