from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from .config import TourRules
from .fetch import DrawStatus, strip_username

DETAIL_PATTERN = re.compile(r"【([^【】（）()]+)\((\d+)\)】")
PLAYER_PATTERN = re.compile(r"【([^【】]+)】")
YEAR_TAIL = re.compile(r"^(.*)((?:19|20)\d{2})$")


def base_event_name(name: str) -> str:
    match = YEAR_TAIL.fullmatch(str(name))
    return match.group(1) if match else str(name)


def _matching_columns(group: list[str], available: list[str]) -> list[str]:
    wanted = set(group)
    return [col for col in available if col in wanted or base_event_name(col) in wanted]


def _as_key(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        try:
            float(text)
            return text[:-2]
        except ValueError:
            return text
    return text


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).astype(int)


def parse_event_scores(details: str | None) -> dict[str, int]:
    if not details:
        return {}
    text = BeautifulSoup(str(details), "html.parser").get_text(" ", strip=True)
    scores = {}
    for name, score in DETAIL_PATTERN.findall(text):
        name = name.strip()
        if name and "<" not in name:
            scores[name] = int(score)
    return scores


def parse_player_list(details: str | None) -> list[str]:
    if not details:
        return []
    text = BeautifulSoup(str(details), "html.parser").get_text(" ", strip=True)
    return PLAYER_PATTERN.findall(text)


def _killer_player(status: str, players: list[str]) -> str:
    if players and status == "球员输球":
        return players[-1]
    return ""


def _event_bucket(name: str, rules: TourRules) -> str:
    base = base_event_name(name)
    if name in rules.slams or base in rules.slams:
        return "slam"
    if name in rules.finals or base in rules.finals:
        return "finals"
    if name in rules.forced or base in rules.forced:
        return "forced"
    if name in rules.uncombined or base in rules.uncombined:
        return "uncombined"
    return "other"


def _select_keep(row: pd.Series, rules: TourRules, events: list[str]) -> tuple[set[str], dict[str, set[str]]]:
    available = [col for col in events if col in row.index]
    empty: dict[str, set[str]] = {
        "slam": set(),
        "finals": set(),
        "forced": set(),
        "uncombined": set(),
        "other": set(),
    }
    if not available:
        return set(), empty
    scores = pd.to_numeric(row[available], errors="coerce").fillna(0)

    slams = set(_matching_columns(rules.slams, available))
    finals = set(_matching_columns(rules.finals, available))
    forced = _matching_columns(rules.forced, available)
    kept_forced: set[str] = set()
    if forced and rules.forced_keep:
        kept_forced = set(scores[forced].astype(int).nlargest(rules.forced_keep).index)

    uncombined = _matching_columns(rules.uncombined, available)
    kept_uncombined: set[str] = set()
    if uncombined and rules.uncombined_keep:
        kept_uncombined = set(scores[uncombined].astype(int).nlargest(rules.uncombined_keep).index)

    skip = slams | finals | kept_forced | kept_uncombined
    others = [col for col in scores.index if col not in skip]
    kept_others: set[str] = set()
    if others and rules.other_keep:
        kept_others = set(scores[others].astype(int).nlargest(rules.other_keep).index)

    groups = {
        "slam": slams,
        "finals": finals,
        "forced": kept_forced,
        "uncombined": kept_uncombined,
        "other": kept_others,
    }
    keep = slams | finals | kept_forced | kept_uncombined | kept_others
    return keep, groups


def counted_events(
    row: pd.Series,
    rules: TourRules,
    events: list[str],
    this_week: list[str] | None = None,
) -> set[str]:
    live = [name for name in (this_week or []) if name]
    past = [name for name in events if name not in live]
    keep, groups = _select_keep(row, rules, past)
    if not live:
        return keep

    scores = pd.to_numeric(row, errors="coerce").fillna(0)
    for name in live:
        if name not in row.index:
            continue
        score = int(scores.get(name, 0) or 0)
        bucket = _event_bucket(name, rules)
        if bucket in ("slam", "finals"):
            keep.add(name)
            groups[bucket].add(name)
            continue
        if bucket == "uncombined" and not rules.uncombined_keep:
            bucket = "other"
        group = groups[bucket]
        limit = {
            "forced": rules.forced_keep,
            "uncombined": rules.uncombined_keep,
            "other": rules.other_keep,
        }[bucket]
        if not limit:
            continue
        if len(group) < limit:
            keep.add(name)
            group.add(name)
            continue
        lowest = min(group, key=lambda col: (int(scores.get(col, 0) or 0), col))
        if score > int(scores.get(lowest, 0) or 0):
            keep.remove(lowest)
            group.remove(lowest)
            keep.add(name)
            group.add(name)
    return keep


def row_total(
    row: pd.Series,
    rules: TourRules,
    events: list[str],
    this_week: list[str] | None = None,
) -> int:
    keep = counted_events(row, rules, events, this_week)
    if not keep:
        return 0
    return int(pd.to_numeric(row[list(keep)], errors="coerce").fillna(0).sum())


def _ordered_events(
    all_events: list[str],
    rules: TourRules,
    this_week: list[str],
) -> list[str]:
    ordered: list[str] = []
    for name in this_week:
        if name and name in all_events and name not in ordered:
            ordered.append(name)
    for group in (rules.slams, rules.finals, rules.forced, rules.uncombined):
        for name in _matching_columns(group, all_events):
            if name not in ordered:
                ordered.append(name)
    for name in all_events:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _race_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    parsed = []
    for item in rows:
        parsed.append(
            {
                "主键": _as_key(item.get("user_id")),
                "用户名": strip_username(item.get("username")),
                **parse_event_scores(item.get("details")),
            }
        )
    return pd.DataFrame(parsed)


def _week_score_frame(rows: list[dict[str, Any]], event_name: str) -> pd.DataFrame:
    parsed = []
    for item in rows:
        players = parse_player_list(item.get("players"))
        status = item.get("fill_status") or ""
        parsed.append(
            {
                "主键": _as_key(item.get("user_id")),
                "用户名": strip_username(item.get("username")),
                "状态": status,
                "存活天数": item.get("day"),
                event_name: int(item.get("score") or 0),
                "明细": players,
                "杀手球员": _killer_player(status, players),
            }
        )
    return pd.DataFrame(parsed)


def _week_choice_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["主键", "用户名", "主选球员", "备选球员"])
    frame = pd.DataFrame(rows)
    if "user_id" in frame.columns:
        if "day" in frame.columns:
            frame = frame.sort_values("day").drop_duplicates(subset=["user_id"], keep="last")
        else:
            frame = frame.drop_duplicates(subset=["user_id"], keep="last")
    frame["用户名"] = frame["username"].map(strip_username)
    frame = frame.rename(
        columns={
            "user_id": "主键",
            "player": "主选球员",
            "player_alt": "备选球员",
        }
    )
    if "主键" in frame.columns:
        frame["主键"] = frame["主键"].map(_as_key)
    keep = [col for col in ["主键", "用户名", "主选球员", "备选球员"] if col in frame.columns]
    return frame[keep].drop_duplicates(subset=["主键"], keep="last")


def _latest_detail_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in rows:
        uid = _as_key(item.get("user_id"))
        if not uid:
            continue
        current = latest.get(uid)
        if current is None or (item.get("day") or 0) >= (current.get("day") or 0):
            latest[uid] = item
    return list(latest.values())


def _today_detail_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest = max((item.get("day") or 0) for item in rows)
    return [item for item in rows if item.get("day") == latest]


def apply_live_settlement(
    week_scores: pd.DataFrame,
    detail_rows: list[dict[str, Any]],
    draw: DrawStatus | None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    empty = {"win": 0, "loss": 0, "pending": 0}
    if week_scores.empty or not detail_rows or draw is None or not draw.by_id:
        return week_scores, empty

    today = _latest_detail_rows(detail_rows)
    today_by_user = {_as_key(item.get("user_id")): item for item in today}
    today_players = []
    fills_by_match: dict[Any, set[str]] = defaultdict(set)
    for item in today:
        player = draw.lookup(item.get("fill"), item.get("player"))
        if player:
            today_players.append(player)
        match_id = item.get("normal_match_id")
        fill = str(item.get("fill") or "").strip()
        if match_id and fill:
            fills_by_match[match_id].add(fill)
    live_round = min((p.round_rank for p in today_players if p.live and p.round_rank), default=0)
    max_elim_round = max((p.round_rank for p in today_players if p.eliminated and p.round_rank), default=0)
    still_ranks = [p.round_rank for p in today_players if not p.eliminated and not p.live and p.round_rank]
    min_still_round = min(still_ranks) if still_ranks else 0

    result = week_scores.copy()
    counts = dict(empty)
    for index, row in result.iterrows():
        if row.get("状态") != "存活":
            continue
        detail = today_by_user.get(_as_key(row.get("主键")))
        if not detail:
            continue
        pick = str(detail.get("player") or "").strip()
        if not pick:
            continue
        used = list(row.get("明细") or [])
        already = pick in used
        if pick == "轮空":
            outcome = "win"
            player = None
        else:
            player = draw.lookup(detail.get("fill"), pick)
            if player is None:
                counts["pending"] += 1
                continue
            if player.eliminated:
                outcome = "loss"
            elif player.live:
                outcome = None
            else:
                outcome = None
                fill = str(detail.get("fill") or "").strip()
                for opp_id in fills_by_match.get(detail.get("normal_match_id")) or set():
                    if opp_id == fill:
                        continue
                    opp = draw.lookup(opp_id, None)
                    if opp and opp.eliminated:
                        outcome = "win"
                        break
                if outcome is None and live_round and player.round_rank > live_round:
                    outcome = "win"
                elif outcome is None and (not live_round) and max_elim_round and player.round_rank > max_elim_round:
                    outcome = "win"
                elif outcome is None and min_still_round and player.round_rank > min_still_round:
                    outcome = "win"
        if outcome == "win":
            if not already:
                used.append(pick)
                result.at[index, "明细"] = used
            result.at[index, "存活天数"] = detail.get("day") or row.get("存活天数")
            result.at[index, "杀手球员"] = ""
            counts["win"] += 1
        elif outcome == "loss":
            if not already:
                used.append(pick)
                result.at[index, "明细"] = used
            result.at[index, "状态"] = "球员输球"
            result.at[index, "杀手球员"] = pick
            result.at[index, "存活天数"] = detail.get("day") or row.get("存活天数")
            counts["loss"] += 1
        else:
            counts["pending"] += 1
    return result, counts


def killer_stats(week_scores: pd.DataFrame) -> pd.DataFrame:
    if week_scores.empty or "明细" not in week_scores.columns:
        return pd.DataFrame()
    by_day: dict[Any, Counter] = defaultdict(Counter)
    for _, row in week_scores.iterrows():
        if row.get("状态") != "球员输球":
            continue
        players = row.get("明细") or []
        if not players:
            continue
        by_day[row.get("存活天数")].update([players[-1]])
    records = []
    for day in sorted(k for k in by_day if pd.notna(k)):
        top = by_day[day].most_common(2)
        record = {"天数": int(day), "杀手球员1": "", "死亡人数1": "", "杀手球员2": "", "死亡人数2": ""}
        if top:
            record["杀手球员1"], record["死亡人数1"] = top[0]
        if len(top) > 1:
            record["杀手球员2"], record["死亡人数2"] = top[1]
        records.append(record)
    return pd.DataFrame(records)


def choice_summary(choices: pd.DataFrame) -> list[tuple[str, int]]:
    if choices.empty or "主选球员" not in choices.columns:
        return []
    picked = choices["主选球员"].fillna("").astype(str)
    picked = picked[picked.str.strip() != ""]
    return list(picked.value_counts().items())


def seed_death_stats(frame: pd.DataFrame, week_scores: pd.DataFrame, seed_cutoff: int = 16) -> pd.DataFrame:
    if week_scores.empty or "上周排名" not in frame.columns:
        return pd.DataFrame()
    left = week_scores.drop(columns=["用户名"], errors="ignore").copy()
    left["主键"] = left["主键"].map(_as_key)
    ranks = frame[["主键", "用户名", "上周排名"]].copy()
    ranks["主键"] = ranks["主键"].map(_as_key)
    merged = left.merge(ranks, on="主键", how="left")
    dead = merged[(merged["状态"] != "存活") & (pd.to_numeric(merged["上周排名"], errors="coerce") <= seed_cutoff)]
    if dead.empty:
        return pd.DataFrame()
    records = []
    for day, group in dead.groupby("存活天数"):
        if pd.isna(day):
            continue
        seeds = [
            f"{row['用户名']}({int(row['上周排名'])})"
            for _, row in group.sort_values("上周排名").iterrows()
            if row.get("用户名")
        ]
        records.append({"天数": int(day), "死亡人数": len(seeds), "死亡种子": ", ".join(seeds)})
    return pd.DataFrame(records)


def _merge_week(result: pd.DataFrame, week_scores: pd.DataFrame, event_name: str) -> pd.DataFrame:
    merge_cols = ["主键", "用户名", "状态", event_name, "存活天数", "杀手球员", "明细"]
    merge_cols = [col for col in merge_cols if col in week_scores.columns]
    week_part = week_scores[merge_cols].rename(columns={"用户名": "用户名_week"})
    result = result.drop(columns=[event_name], errors="ignore")
    result = result.merge(week_part, on="主键", how="outer")
    result["用户名"] = result["用户名"].fillna(result["用户名_week"])
    return result.drop(columns=["用户名_week"], errors="ignore")


def build_ranking(
    tennis_type: str,
    race_rows: list[dict[str, Any]],
    week_event_name: str | None = None,
    week_score_rows: list[dict[str, Any]] | None = None,
    week_detail_rows: list[dict[str, Any]] | None = None,
    hide_used_picks: bool = True,
    drop_events: list[str] | None = None,
    instant: bool = False,
    this_week_year: str | None = None,
    prior_years: dict[str, str] | None = None,
    draw_status: DrawStatus | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[tuple[str, int]]]:
    rules = TourRules.for_tour(tennis_type)
    result = _race_frame(race_rows)
    if result.empty:
        empty = pd.DataFrame()
        return empty, empty, empty, []

    this_week_names = [week_event_name] if week_event_name else []
    drop_events = [name for name in (drop_events or []) if name]
    prior_years = prior_years or {}
    week_scores = (
        _week_score_frame(week_score_rows or [], week_event_name or "")
        if week_event_name and week_score_rows
        else pd.DataFrame()
    )
    settlement = {"win": 0, "loss": 0, "pending": 0}
    if not week_scores.empty:
        week_scores, settlement = apply_live_settlement(week_scores, week_detail_rows or [], draw_status)
    choices = _week_choice_frame(week_detail_rows or [])

    all_events = [col for col in result.columns if col not in ("用户名", "主键")]
    year_event_names = set(all_events)
    for name in this_week_names:
        if name not in all_events:
            all_events.append(name)
    season_events = list(all_events)
    ordered = _ordered_events(all_events, rules, this_week_names)
    result = result.reindex(columns=["用户名", "主键"] + ordered, fill_value=0)
    result[ordered] = result[ordered].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)

    past_events = [name for name in season_events if name not in this_week_names]
    result["上周总分"] = result.apply(lambda row: row_total(row, rules, past_events), axis=1).astype(int)
    result["上周排名"] = result["上周总分"].rank(ascending=False, method="min").astype(int)

    drop_present = [name for name in drop_events if name in result.columns]
    for name in drop_present:
        result[f"替换{name}"] = _to_int(result[name])
    if drop_present:
        result["替换赛事"] = result[drop_present].sum(axis=1).astype(int)
    elif instant:
        result["替换赛事"] = 0

    live_name = week_event_name
    if (
        instant
        and week_event_name
        and week_event_name in year_event_names
        and week_event_name not in drop_events
        and this_week_year
    ):
        old_year = prior_years.get(week_event_name) or (
            str(int(this_week_year) - 1) if str(this_week_year).isdigit() else None
        )
        if old_year and str(old_year) != str(this_week_year):
            old_col = f"{week_event_name}{old_year}"
            live_name = f"{week_event_name}{this_week_year}"
            result = result.rename(columns={week_event_name: old_col})
            if not week_scores.empty and week_event_name in week_scores.columns:
                week_scores = week_scores.rename(columns={week_event_name: live_name})
            this_week_names = [live_name]
            ordered = [old_col if col == week_event_name else col for col in ordered]
            if live_name not in ordered:
                ordered.insert(0, live_name)
            if old_col not in result.columns:
                result[old_col] = 0
            if live_name not in result.columns:
                result[live_name] = 0
            current_events = [
                col
                for col in result.columns
                if col not in ("用户名", "主键", "上周总分", "上周排名", "替换赛事")
                and not str(col).startswith("替换")
            ]
            ordered = _ordered_events(current_events, rules, this_week_names)

    scoring_events = [name for name in ordered if name not in drop_events or name in this_week_names]
    for name in this_week_names:
        if name not in scoring_events:
            scoring_events.insert(0, name)

    if not week_scores.empty and live_name:
        result = _merge_week(result, week_scores, live_name)
        result["主键"] = result["主键"].map(_as_key)
        result["状态"] = result["状态"].fillna("未参赛")
    elif "状态" not in result.columns:
        result["状态"] = "本周完赛"
    else:
        result["状态"] = result["状态"].fillna("本周完赛")

    if "上周总分" not in result.columns:
        result["上周总分"] = 0
    result["上周总分"] = _to_int(result["上周总分"])
    result["上周排名"] = result["上周总分"].rank(ascending=False, method="min").astype(int)
    if "替换赛事" in result.columns:
        result["替换赛事"] = _to_int(result["替换赛事"])
    for name in drop_present:
        col = f"替换{name}"
        if col in result.columns:
            result[col] = _to_int(result[col])

    if not choices.empty:
        choice_part = choices.rename(columns={"用户名": "用户名_choice"})
        result = result.merge(
            choice_part.drop(columns=["用户名_choice"], errors="ignore"),
            on="主键",
            how="left",
        )
        if "用户名_choice" in result.columns:
            result["用户名"] = result["用户名"].fillna(result["用户名_choice"])
            result = result.drop(columns=["用户名_choice"])

    for col in ["主选球员", "备选球员", "杀手球员"]:
        if col not in result.columns:
            result[col] = ""
        result[col] = result[col].fillna("")

    if hide_used_picks and "明细" in result.columns:
        def _hide(row: pd.Series, field: str) -> str:
            if row.get("状态") != "存活":
                return ""
            used = row.get("明细") or []
            value = row.get(field) or ""
            return value if value and value not in used else ""

        result["备选球员"] = result.apply(lambda row: _hide(row, "备选球员"), axis=1)
        result["主选球员"] = result.apply(lambda row: _hide(row, "主选球员"), axis=1)

    if "明细" in result.columns:
        result["选人明细"] = result["明细"].map(
            lambda value: " → ".join(str(item) for item in value if item) if isinstance(value, list) else ""
        )
        result["选人明细"] = result["选人明细"].fillna("")
    result = result.drop(columns=["明细"], errors="ignore")
    for col in scoring_events:
        if col not in result.columns:
            result[col] = 0
    result[scoring_events] = result[scoring_events].apply(_to_int)
    result["总分"] = result.apply(
        lambda row: row_total(row, rules, scoring_events, this_week_names), axis=1
    ).astype(int)
    result["排名"] = result["总分"].rank(ascending=False, method="min").astype(int)
    result["升降"] = result["上周排名"] - result["排名"]
    result["用户名"] = result["用户名"].fillna("")
    if "存活天数" in result.columns:
        result["存活天数"] = pd.to_numeric(result["存活天数"], errors="coerce")
        result["存活天数"] = result["存活天数"].apply(lambda value: "" if pd.isna(value) else int(value))
    result = result.sort_values(["排名", "用户名"]).reset_index(drop=True)

    seeds = seed_death_stats(result, week_scores) if not week_scores.empty else pd.DataFrame()
    display_events = [name for name in scoring_events if name not in drop_events or name in this_week_names]
    display_events = _ordered_events(display_events, rules, this_week_names)
    display_cols = ["排名", "用户名", "总分", "升降", "状态", "主选球员", "备选球员"]
    extra = [col for col in ["杀手球员", "存活天数", "替换赛事"] if col in result.columns]
    html_columns = display_cols + extra + display_events

    stats = killer_stats(week_scores) if not week_scores.empty else pd.DataFrame()
    summary = choice_summary(_week_choice_frame(_today_detail_rows(week_detail_rows or [])))
    result.attrs["this_week"] = this_week_names
    result.attrs["event_columns"] = display_events
    result.attrs["drop_events"] = drop_events
    result.attrs["display_columns"] = html_columns
    result.attrs["rules"] = rules
    result.attrs["instant"] = instant
    result.attrs["tennis_type"] = tennis_type
    result.attrs["uncounted_columns"] = ["替换赛事"] if instant else []
    result.attrs["settlement"] = settlement
    return result, stats, seeds, summary


def build_combined(
    atp_frame: pd.DataFrame,
    wta_frame: pd.DataFrame,
    instant: bool = False,
) -> pd.DataFrame:
    def _slice(frame: pd.DataFrame, prefix: str, overlap: set[str]) -> pd.DataFrame:
        this_week = frame.attrs.get("this_week") or []
        drop_events = frame.attrs.get("drop_events") or []
        cols = ["主键", "用户名", "总分", "状态", "上周总分"]
        rename = {"总分": f"{prefix}总分", "状态": f"{prefix}状态", "上周总分": f"{prefix}上周总分"}
        for name in this_week:
            if name in frame.columns:
                cols.append(name)
                if name in overlap:
                    rename[name] = f"{prefix}{name}"
        part = frame[[col for col in cols if col in frame.columns]].copy()
        if instant:
            for name in drop_events:
                label = f"替换{prefix}{name}" if name in overlap else f"替换{name}"
                source = f"替换{name}" if f"替换{name}" in frame.columns else None
                if source:
                    part[label] = _to_int(frame[source])
                elif "替换赛事" in frame.columns:
                    part[label] = _to_int(frame["替换赛事"])
        return part.rename(columns=rename)

    overlap = set(atp_frame.attrs.get("this_week") or []) & set(wta_frame.attrs.get("this_week") or [])
    overlap |= set(atp_frame.attrs.get("drop_events") or []) & set(wta_frame.attrs.get("drop_events") or [])
    atp_part = _slice(atp_frame, "ATP", overlap)
    wta_part = _slice(wta_frame, "WTA", overlap)
    combined = pd.merge(wta_part, atp_part, on="主键", how="outer", suffixes=("_wta", "_atp"))
    if "用户名_wta" in combined.columns or "用户名_atp" in combined.columns:
        combined["用户名"] = combined.get("用户名_wta", pd.Series(dtype=str)).fillna(
            combined.get("用户名_atp", pd.Series(dtype=str))
        )
        combined = combined.drop(columns=[col for col in ["用户名_wta", "用户名_atp"] if col in combined.columns])
    elif "用户名" not in combined.columns:
        combined["用户名"] = ""

    for col in ["ATP总分", "WTA总分", "ATP上周总分", "WTA上周总分"]:
        if col not in combined.columns:
            combined[col] = 0
        combined[col] = _to_int(combined[col])
    for col in ["ATP状态", "WTA状态"]:
        if col not in combined.columns:
            combined[col] = ""
        combined[col] = combined[col].fillna("")

    combined["总分"] = (combined["ATP总分"] + combined["WTA总分"]).astype(int)
    combined["上周总分"] = (combined["ATP上周总分"] + combined["WTA上周总分"]).astype(int)
    combined["上周排名"] = combined["上周总分"].rank(ascending=False, method="min").astype(int)
    combined["排名"] = combined["总分"].rank(ascending=False, method="min").astype(int)
    combined["升降"] = combined["上周排名"] - combined["排名"]
    combined["用户名"] = combined["用户名"].fillna("")
    combined = combined.sort_values(["排名", "用户名"]).reset_index(drop=True)

    week_cols = []
    for prefix, frame in (("WTA", wta_frame), ("ATP", atp_frame)):
        for name in frame.attrs.get("this_week") or []:
            labeled = f"{prefix}{name}" if name in overlap else name
            if labeled in combined.columns and labeled not in week_cols:
                week_cols.append(labeled)
    replace_cols = [col for col in combined.columns if str(col).startswith("替换")]
    for col in week_cols + replace_cols:
        combined[col] = _to_int(combined[col])
    display = ["排名", "用户名", "总分", "升降", "WTA总分", "ATP总分", "WTA状态", "ATP状态"]
    html_columns = display + week_cols + replace_cols
    combined.attrs["this_week"] = week_cols
    combined.attrs["event_columns"] = week_cols
    combined.attrs["display_columns"] = html_columns
    combined.attrs["uncounted_columns"] = replace_cols
    combined.attrs["instant"] = instant
    combined.attrs["rules"] = None
    out = combined[html_columns].copy()
    out.attrs.update(combined.attrs)
    return out
