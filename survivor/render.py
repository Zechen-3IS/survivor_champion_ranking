from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .config import META_COLS, OUTPUT_FILES, TEMPLATE_DIR, TITLES, TourRules, beijing_now
from .process import counted_events

UP_ARROW = """
<span style="display: inline-flex; align-items: center;">
    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="green" style="margin-right: 4px;">
        <path d="M4 12l1.41 1.41L11 7.83V20h2V7.83l5.59 5.58L20 12l-8-8-8 8z"/>
    </svg>{value}
</span>
"""
DOWN_ARROW = """
<span style="display: inline-flex; align-items: center;">
    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="red" style="margin-right: 4px;">
        <path d="M4 12l1.41-1.41L11 16.17V4h2v12.17l5.59-5.58L20 12l-8 8-8-8z"/>
    </svg>{value}
</span>
"""


def _read_asset(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _trend_html(value: object) -> str:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return ""
    if number > 0:
        return UP_ARROW.format(value=number)
    if number < 0:
        return DOWN_ARROW.format(value=abs(number))
    return ""


def _choice_info_html(summary: list[tuple[str, int]]) -> str:
    total = sum(count for _, count in summary)
    items = "".join(f"<li>{escape(str(name))}：{count}</li>" for name, count in summary)
    return (
        f'<div class="choice-info"><strong>今日选择情况：</strong> 总选择人数：{total} '
        f"<ul>{items}</ul></div>"
    )


def _stats_html(stats: pd.DataFrame, table_id: str, label: str) -> str:
    if stats is None or stats.empty:
        return ""
    table = stats.to_html(index=False, classes="dataframe stats-table", border=0)
    return f"""
            <div class="stats-container">
                <button class="toggle-stats-btn" onclick="toggleStatsTable('{table_id}')">
                    <span class="btn-text">显示{label}</span>
                    <span class="arrow-icon">▼</span>
                </button>
                <div id="{table_id}" class="hidden">
                    {table}
                </div>
            </div>
    """


def _as_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _decorate_table(
    frame: pd.DataFrame,
    rules: TourRules | None,
    event_columns: list[str],
    this_week: list[str],
    uncounted_columns: list[str] | None = None,
) -> str:
    html_columns = [col for col in (frame.attrs.get("display_columns") or list(frame.columns)) if col in frame.columns]
    view = frame[html_columns]
    html = view.to_html(index=False, classes="dataframe data-table", border=0, escape=True)
    soup = BeautifulSoup(html, "html.parser")
    headers = soup.select("thead th")
    column_names = list(view.columns)
    this_week = _as_list(this_week)
    uncounted = set(uncounted_columns or [])
    uncounted.update(name for name in column_names if str(name).startswith("替换"))

    for idx, name in enumerate(column_names):
        if idx >= len(headers):
            break
        if name in event_columns and name not in this_week:
            headers[idx]["class"] = headers[idx].get("class", []) + ["expandable-column"]
        if name in this_week and rules is not None:
            headers[idx].string = "🎾" + str(name)

    status_index = column_names.index("状态") if "状态" in column_names else None
    trend_index = column_names.index("升降") if "升降" in column_names else None

    for row_idx, tr in enumerate(soup.select("tbody tr")):
        cells = tr.find_all("td")
        row = view.iloc[row_idx]
        if rules is not None:
            keep = counted_events(row, rules, event_columns, this_week)
        else:
            keep = {name for name in event_columns if name not in uncounted}
        for col_idx, name in enumerate(column_names):
            if col_idx >= len(cells):
                break
            if name in event_columns and name not in this_week:
                cells[col_idx]["class"] = cells[col_idx].get("class", []) + ["expandable-column"]
            strike = name in uncounted or (
                rules is not None and name in event_columns and name not in keep
            )
            if strike:
                text = cells[col_idx].get_text(strip=True)
                cells[col_idx].clear()
                cells[col_idx].append(BeautifulSoup(f"<del>{text}</del>", "html.parser"))
        if trend_index is not None and trend_index < len(cells):
            cells[trend_index].clear()
            cells[trend_index].append(BeautifulSoup(_trend_html(row.get("升降")), "html.parser"))
        if status_index is not None and status_index < len(cells):
            if cells[status_index].get_text(strip=True) == "存活":
                tr["style"] = "background-color: #e6f7ff;"
    return str(soup)


def render_ranking_page(
    board: str,
    frame: pd.DataFrame,
    stats: pd.DataFrame | None,
    summary: list[tuple[str, int]] | None,
    output_dir: Path,
    extra_stats: pd.DataFrame | None = None,
) -> Path:
    title = TITLES[board]
    this_week = _as_list(frame.attrs.get("this_week"))
    event_columns = frame.attrs.get("event_columns")
    if event_columns is None:
        event_columns = [col for col in frame.columns if col not in META_COLS]
    rules = frame.attrs.get("rules")
    uncounted = frame.attrs.get("uncounted_columns") or []
    table_html = _decorate_table(frame, rules, event_columns, this_week, uncounted)
    now = beijing_now().strftime("%Y-%m-%d %H:%M:%S")
    choice_block = _choice_info_html(summary or []) if summary is not None else ""
    stats_block = _stats_html(stats if stats is not None else pd.DataFrame(), "statsTable1", "每日杀手统计")
    extra_block = _stats_html(
        extra_stats if extra_stats is not None else pd.DataFrame(), "statsTable2", "死亡种子统计"
    )
    filter_skip = int(frame.attrs.get("filter_skip") or 4)
    start_collapsed = frame.attrs.get("start_collapsed")
    if start_collapsed is None:
        start_collapsed = True
    script = _read_asset("ranking.js").replace("const tag = 4;", f"const tag = {filter_skip};", 1)
    script = script.replace(
        "const startCollapsed = true;",
        f"const startCollapsed = {str(bool(start_collapsed)).lower()};",
        1,
    )
    page = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        {_read_asset("ranking.css")}
    </head>
    <body>
        <div class="container">
            <h1>{title}</h1>
            <div style="text-align: center; color: #888; font-size: 0.9em; margin-bottom: 10px;">
                页面数据更新时间：{now}
            </div>
            {choice_block}
            {stats_block}
            {extra_block}
            <div class="controls-wrapper">
                <div class="controls-container">
                    <div>
                        <input type="text" id="searchInput" placeholder="搜索用户名或分数..." style="width: 250px;">
                    </div>
                    <div>
                        <select id="rowsPerPage" style="width: 120px;">
                            <option value="10">每页10行</option>
                            <option value="25">每页25行</option>
                            <option value="50">每页50行</option>
                            <option value="100">每页100行</option>
                            <option value="0">显示全部</option>
                        </select>
                    </div>
                    <button id="expandToggleBtn" class="expand-toggle-btn">
                        <span class="btn-text">展开列</span>
                        <span class="expand-icon">▶</span>
                    </button>
                </div>
            </div>
            <div id="matchCount" class="match-count">共找到 {len(frame)} 条记录</div>
            <div class="data-table-wrapper">
                {table_html}
            </div>
            <div class="pagination">
                <button id="prevPage" disabled>上一页</button>
                <div class="page-info" id="pageInfo">第1页</div>
                <button id="nextPage">下一页</button>
            </div>
        </div>
        {script}
    </body>
    </html>
    """
    output_path = Path(output_dir) / OUTPUT_FILES[board]
    output_path.write_text(page, encoding="utf-8")
    return output_path
