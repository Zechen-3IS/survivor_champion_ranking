from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .config import ROOT
from .fetch import DrawStatus, LiveTennisClient, WeekEvent
from .home import render_index_page
from .process import build_combined, build_ranking
from .publish import publish_html
from .render import render_ranking_page
from .stats import build_player_stats


def _primary_event(events: list[WeekEvent] | None) -> WeekEvent | None:
    return events[0] if events else None


def _build_tour(
    tennis_type: str,
    event: WeekEvent | None,
    season_rows: list[dict[str, Any]],
    week_scores: list[dict[str, Any]],
    week_details: list[dict[str, Any]],
    drop_events: list[str],
    instant: bool,
    this_week_year: str | None = None,
    prior_years: dict[str, str] | None = None,
    draw_status: DrawStatus | None = None,
):
    return build_ranking(
        tennis_type,
        season_rows,
        week_event_name=event.name if event else None,
        week_score_rows=week_scores,
        week_detail_rows=week_details,
        drop_events=drop_events if instant else None,
        instant=instant,
        this_week_year=this_week_year if instant else None,
        prior_years=prior_years if instant else None,
        draw_status=draw_status,
    )


def generate(tours: list[str], output_dir: Path) -> list[Path]:
    client = LiveTennisClient()
    print("发现本周赛事...")
    current = client.discover_current_events()
    this_monday = client.week_monday()
    written: list[Path] = []
    built: dict[str, Any] = {}

    for tennis_type in tours:
        events = current.get(tennis_type) or []
        event = _primary_event(events)
        if event:
            print(f"{tennis_type.upper()} 本周：{event.name} ({event.page_id})")
        else:
            print(f"{tennis_type.upper()} 未发现本周赛事")

        print(f"抓取 {tennis_type.upper()} 冠军榜 / 52周榜...")
        race_rows = client.fetch_race_rank(tennis_type)
        year_rows, year_monday = client.fetch_year_rank(tennis_type)
        week_scores = client.fetch_week_scores(event) if event else []
        week_details = client.fetch_week_details(event) if event else []
        draw_status = client.fetch_draw_status(event) if event else None
        if draw_status and draw_status.by_id:
            live_n = sum(1 for player in draw_status.by_id.values() if player.live)
            elim_n = sum(1 for player in draw_status.by_id.values() if player.eliminated)
            print(f"  签表 {len(draw_status.by_id)} 人，淘汰 {elim_n}，进行中 {live_n}")
        week = client.tour_week(tennis_type, this_monday)
        pair = client.week_pair(this_monday, tennis_type)
        drop_events = pair.last_names
        prior_years: dict[str, str] = {}
        if event and event.name not in drop_events:
            old_year = client.prior_event_year(
                event.name, tennis_type, pair.last_monday, pair.this_monday
            )
            if old_year:
                prior_years[event.name] = old_year
        print(
            f"  赛季 {len(race_rows)}，52周 {len(year_rows)}（窗口 {year_monday}），"
            f"本周积分 {len(week_scores)}，明细 {len(week_details)}"
        )
        print(
            f"  日历周 {week.this_monday.date()}，开赛周 {week.current_start.date()}："
            f"{pair.this_names or ([event.name] if event else [])}；"
            f"上周完赛 {week.previous_events[0]['name'] if week.previous_events else '无'}；"
            f"去年同期 {pair.last_monday.date()}：{drop_events or '无'}"
            + (" → 替换" if drop_events else " → 去年同期无赛事")
            + (f"；同名保留 {prior_years}" if prior_years else "")
        )

        champ, champ_stats, champ_seeds, champ_summary = _build_tour(
            tennis_type,
            event,
            race_rows,
            week_scores,
            week_details,
            drop_events,
            False,
            draw_status=draw_status,
        )
        inst, inst_stats, inst_seeds, inst_summary = _build_tour(
            tennis_type,
            event,
            year_rows,
            week_scores,
            week_details,
            drop_events,
            True,
            this_week_year=event.year if event else None,
            prior_years=prior_years,
            draw_status=draw_status,
        )
        written.append(render_ranking_page(tennis_type, champ, champ_stats, champ_summary, output_dir))
        written.append(
            render_ranking_page(
                f"{tennis_type}_instant",
                inst,
                inst_stats,
                inst_summary,
                output_dir,
                extra_stats=inst_seeds,
            )
        )
        built[tennis_type] = champ
        built[f"{tennis_type}_instant"] = inst
        settled = champ.attrs.get("settlement") or {}
        if any(settled.values()):
            print(
                f"  实时结算 胜 {settled.get('win', 0)}，"
                f"负 {settled.get('loss', 0)}，"
                f"未完赛 {settled.get('pending', 0)}"
            )
        print(f"已生成 {tennis_type} 冠军榜和即时榜")

    if "atp" in built and "wta" in built:
        combined = build_combined(built["atp"], built["wta"], instant=False)
        combined_instant = build_combined(built["atp_instant"], built["wta_instant"], instant=True)
        written.append(render_ranking_page("combined", combined, None, None, output_dir))
        written.append(render_ranking_page("combined_instant", combined_instant, None, None, output_dir))
        print("已生成联合冠军榜和联合即时榜")
    index_path = output_dir / "index.html"
    if index_path.exists():
        print("更新首页...")
        render_index_page(client, index_path)
        written.append(index_path)
        print("已更新 index.html")
    return written


def generate_stats(tours: list[str], output_dir: Path) -> list[Path]:
    client = LiveTennisClient()
    current = client.discover_current_events()
    written: list[Path] = []
    for tennis_type in tours:
        events = current.get(tennis_type) or []
        event = _primary_event(events)
        print(f"统计 {tennis_type.upper()} 球员成绩（每周增量）...")
        show = build_player_stats(client, tennis_type, event)
        written.append(render_ranking_page(f"{tennis_type}_show", show, None, None, output_dir))
        print(f"已生成 {tennis_type} 球员统计")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="从 live-tennis 抓取幸存者数据并生成排名页")
    parser.add_argument("--tour", choices=["atp", "wta", "all"], default="all")
    parser.add_argument("--output", default=str(ROOT))
    parser.add_argument("--stats", action="store_true", help="只更新球员成绩统计（每周增量）")
    parser.add_argument("--push", action="store_true", help="只提交并推送 HTML 到 GitHub")
    args = parser.parse_args()
    tours = ["atp", "wta"] if args.tour == "all" else [args.tour]
    if args.stats:
        generate_stats(tours, Path(args.output))
    else:
        generate(tours, Path(args.output))
    if args.push:
        publish_html(Path(args.output))


if __name__ == "__main__":
    main()
