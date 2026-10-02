from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .config import TourRules
from .fetch import LiveTennisClient, WeekEvent, strip_username
from .process import _as_key, row_total

PLAYED_STATUS = {"存活", "球员输球", "自杀(退赛)", "自杀(重复)", "自杀(主备冲突)"}
EXPAND_COLS = ["夺冠明细", "夺亚明细", "连胜细节", "连败细节"]
DISPLAY_COLS = [
    "排名",
    "用户名",
    "周期参赛数",
    "周期冠军数",
    "历史冠军数",
    "夺冠明细",
    "历史亚军数",
    "夺亚明细",
    "历史最佳排名",
    "历史最长连胜天数",
    "连胜细节",
    "历史最长连败天数",
    "连败细节",
    "世界第一周数",
    "历史最高得分",
    "赛季存活率",
    "历史存活率",
]


def _day(row: dict[str, Any]) -> int:
    try:
        return int(row.get("day") or 0)
    except (TypeError, ValueError):
        return 0


def _score(row: dict[str, Any]) -> int:
    try:
        return int(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _played(row: dict[str, Any]) -> bool:
    status = str(row.get("fill_status") or "")
    return status in PLAYED_STATUS and (_day(row) > 0 or status == "存活")


def _longest_streak(flags: list[tuple[bool, str, int]], with_days: bool) -> tuple[int, str]:
    best: list[tuple[str, int]] = []
    current: list[tuple[str, int]] = []
    best_days = -1
    for matched, label, days in flags:
        if matched:
            current.append((label, days))
            continue
        total = sum(item[1] for item in current)
        if current and total > best_days:
            best = current
            best_days = total
        current = []
    total = sum(item[1] for item in current)
    if current and total > best_days:
        best = current
        best_days = total
    if not best:
        return 0, ""
    if with_days:
        text = ", ".join(f"{name}({days})" for name, days in best)
    else:
        text = ", ".join(name for name, _ in best)
    return max(best_days, 0), text


def _unique_event_name(name: str, year: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    labeled = f"{name}{year}"
    used.add(labeled)
    return labeled


def build_player_stats(
    client: LiveTennisClient,
    tennis_type: str,
    current_event: WeekEvent | None = None,
) -> pd.DataFrame:
    rules = TourRules.for_tour(tennis_type)
    this_monday = client.week_monday()
    season_year = this_monday.year
    window_start = this_monday - timedelta(weeks=52)
    live_key = (
        (current_event.name, str(current_event.year))
        if current_event and current_event.name
        else None
    )

    catalog = client.list_tour_events(tennis_type)
    records: list[dict[str, Any]] = []
    for index, info in enumerate(catalog, start=1):
        print(f"  球员统计 {tennis_type.upper()} {index}/{len(catalog)} {info.get('year')}-{info.get('name')}")
        rows = client.fetch_event_results(info)
        if not rows:
            continue
        played_rows = [row for row in rows if _played(row)]
        if not played_rows:
            continue
        max_day = max(_day(row) for row in played_rows)
        in_progress = live_key == (info.get("name"), str(info.get("year") or ""))
        champions = {
            _as_key(row.get("user_id"))
            for row in played_rows
            if row.get("fill_status") == "存活" and not in_progress
        }
        dead_days = [_day(row) for row in played_rows if _as_key(row.get("user_id")) not in champions]
        runner_day = max(dead_days) if dead_days and champions else -1
        label = f"{info.get('year')}-{info.get('name')}"
        for row in played_rows:
            uid = _as_key(row.get("user_id"))
            if not uid:
                continue
            day = _day(row)
            records.append(
                {
                    "主键": uid,
                    "用户名": strip_username(row.get("username")),
                    "name": info.get("name") or "",
                    "year": str(info.get("year") or ""),
                    "monday": info["monday"],
                    "season_year": int(info.get("season_year") or info["monday"].year),
                    "label": label,
                    "day": day,
                    "score": _score(row),
                    "max_day": max_day,
                    "champion": uid in champions,
                    "runner": bool(champions) and uid not in champions and day == runner_day,
                    "completed": not in_progress,
                }
            )

    if not records:
        empty = pd.DataFrame(columns=DISPLAY_COLS)
        empty.attrs.update(
            {
                "this_week": [],
                "event_columns": EXPAND_COLS,
                "display_columns": DISPLAY_COLS,
                "rules": None,
                "uncounted_columns": [],
            }
        )
        return empty

    history = pd.DataFrame(records)
    history = history.sort_values(["monday", "name"]).reset_index(drop=True)
    users = (
        history.groupby("主键", as_index=False)
        .agg(用户名=("用户名", "last"))
        .set_index("主键")
    )

    completed = history[history["completed"]]
    weeks = sorted({value for value in completed["monday"].tolist() if isinstance(value, datetime)})
    best_rank: dict[str, int] = {}
    best_score: dict[str, int] = {}
    weeks_at_one: dict[str, int] = defaultdict(int)
    for monday in weeks:
        window = completed[(completed["monday"] > monday - timedelta(weeks=52)) & (completed["monday"] <= monday)]
        if window.empty:
            continue
        used_names: set[str] = set()
        rename: dict[tuple[str, str], str] = {}
        for _, event in window[["name", "year"]].drop_duplicates().iterrows():
            rename[(event["name"], event["year"])] = _unique_event_name(event["name"], event["year"], used_names)
        window = window.copy()
        window["col"] = window.apply(lambda row: rename[(row["name"], row["year"])], axis=1)
        scores = window.pivot_table(index="主键", columns="col", values="score", aggfunc="max").fillna(0)
        events = list(scores.columns)
        totals = scores.apply(lambda row: row_total(row, rules, events), axis=1)
        ranks = totals.rank(ascending=False, method="min")
        for uid, total in totals.items():
            total_int = int(total)
            rank_int = int(ranks.loc[uid])
            if uid not in best_score or total_int > best_score[uid]:
                best_score[uid] = total_int
            if uid not in best_rank or rank_int < best_rank[uid]:
                best_rank[uid] = rank_int
            if rank_int == 1:
                weeks_at_one[uid] += 1

    rows_out: list[dict[str, Any]] = []
    for uid, meta in users.iterrows():
        person = history[history["主键"] == uid].sort_values("monday")
        cycle = person[person["monday"] > window_start]
        season = person[person["season_year"] == season_year]
        titles = person[person["champion"]]
        runners = person[person["runner"]]
        win_flags = [
            (bool(row.champion), row.label, int(row.day))
            for row in person.itertuples()
        ]
        lose_flags = [
            ((not row.champion) and int(row.day) <= 1, row.label, int(row.day))
            for row in person.itertuples()
        ]
        win_days, win_detail = _longest_streak(win_flags, True)
        lose_days, lose_detail = _longest_streak(lose_flags, False)
        season_possible = int(season["max_day"].sum()) if not season.empty else 0
        all_possible = int(person["max_day"].sum()) if not person.empty else 0
        season_rate = round(float(season["day"].sum()) / season_possible, 6) if season_possible else 0.0
        all_rate = round(float(person["day"].sum()) / all_possible, 6) if all_possible else 0.0
        rows_out.append(
            {
                "主键": uid,
                "用户名": meta["用户名"],
                "周期参赛数": int(len(cycle)),
                "周期冠军数": int(cycle["champion"].sum()),
                "历史冠军数": int(titles.shape[0]),
                "夺冠明细": ", ".join(titles["label"].tolist()),
                "历史亚军数": int(runners.shape[0]),
                "夺亚明细": ", ".join(runners["label"].tolist()),
                "历史最佳排名": int(best_rank.get(uid, 0)),
                "历史最长连胜天数": int(win_days),
                "连胜细节": win_detail,
                "历史最长连败天数": int(lose_days),
                "连败细节": lose_detail,
                "世界第一周数": int(weeks_at_one.get(uid, 0)),
                "历史最高得分": int(best_score.get(uid, 0)),
                "赛季存活率": season_rate,
                "历史存活率": all_rate,
            }
        )

    frame = pd.DataFrame(rows_out)
    frame["_资格"] = (frame["周期参赛数"] < 8).astype(int)
    frame = frame.sort_values(
        ["_资格", "历史最高得分", "历史冠军数", "赛季存活率", "周期冠军数"],
        ascending=[True, False, False, False, False],
    ).reset_index(drop=True)
    frame = frame.drop(columns=["_资格"])
    frame["排名"] = range(1, len(frame) + 1)
    view = frame[DISPLAY_COLS].copy()
    view.attrs.update(
        {
            "this_week": [],
            "event_columns": EXPAND_COLS,
            "display_columns": DISPLAY_COLS,
            "rules": None,
            "uncounted_columns": [],
            "filter_skip": 2,
        }
    )
    return view
