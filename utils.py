import requests
import pandas as pd
import numpy as np
import re
import subprocess
import os
from bs4 import BeautifulSoup
from datetime import datetime




def excel_to_html(tennis_type, df, output_path, output_file):
    slams = ['澳网','法网','温网','美网']
    wta_forced = ['印第安维尔斯','迈阿密','马德里','罗马','蒙特利尔','辛辛那提','北京']
    wta_uncombined = ['多哈','迪拜','武汉']
    atp_forced = ['印第安维尔斯','迈阿密','马德里','罗马','多伦多','辛辛那提','上海','巴黎']
    atp_uncombined = ['蒙特卡洛']

    def filter_func(row):
        index_0 = row[['用户名','状态','升降','主选球员','备选球员','排名','总分']].index
        if tennis_type == 'wta':
            index_1 = row[slams].index.union(row[wta_forced].astype('int').nlargest(6).index).union(row[wta_uncombined].astype('int').nlargest(1).index)
            index_2 = row[row.index.difference(index_0.union(index_1))].astype('int').nlargest(7).index
        elif tennis_type == 'atp':
            index_1 = row[slams].index.union(row[atp_forced].astype('int').nlargest(5).index)
            index_2 = row[row.index.difference(index_0.union(index_1))].astype('int').nlargest(10).index
        index = row.index.difference(index_0.union(index_1).union(index_2))       
        return index.tolist()
            

    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if tennis_type == 'wta':
        title = 'WTA幸存者冠军排名'
    elif tennis_type == 'atp':
        title = 'ATP幸存者冠军排名'

    # === 生成主选球员 value_counts 的 HTML段 ===
    if '主选球员' in df.columns:
        player_counts = df[df['主选球员'] != '']['主选球员'].value_counts()
        total_players = len(df[df['主选球员'] != ''])  # 计算总选择人数
        choice_info_html = f'<div class="choice-info"><strong>今日选择情况：</strong> 总选择人数: {total_players} <ul>'
        for player, count in player_counts.items():
            choice_info_html += f'<li>{player}：{count}</li>'
        choice_info_html += '</ul></div>'
    else:
        choice_info_html = ''

    # 创建HTML表格
    html = df.to_html(index=False, classes='data-table', border=0)

    # 使用BeautifulSoup美化HTML
    soup = BeautifulSoup(html, 'html.parser')

    # 设置"状态"列为"存活"时所在行的背景色
    header_cells = soup.select('thead tr th')
    status_index = None
    for idx, th in enumerate(header_cells):
        if '状态' in th.text:
            status_index = idx
            break

    # 设置"升降"列箭头与颜色显示
    trend_index = None
    for idx, th in enumerate(header_cells):
        if '升降' in th.text:
            trend_index = idx
            break

    if trend_index is not None:
        for row in soup.select('tbody tr'):
            cells = row.find_all('td')
            if trend_index < len(cells):
                raw = cells[trend_index].text.strip()
                try:
                    value = float(raw)
                    arrow_html = ''
                    if value > 0:
                        arrow_html = f"""
                        <span style="display: inline-flex; align-items: center;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="green" style="margin-right: 4px;">
                                <path d="M4 12l1.41 1.41L11 7.83V20h2V7.83l5.59 5.58L20 12l-8-8-8 8z"/>
                            </svg>{int(value)}
                        </span>
                        """
                    elif value < 0:
                        arrow_html = f"""
                        <span style="display: inline-flex; align-items: center;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="red" style="margin-right: 4px;">
                                <path d="M4 12l1.41-1.41L11 16.17V4h2v12.17l5.59-5.58L20 12l-8 8-8-8z"/>
                            </svg>{int(abs(value))}
                        </span>
                        """
                    else:
                        arrow_html = ''
                    cells[trend_index].string = ''
                    cells[trend_index].append(BeautifulSoup(arrow_html, 'html.parser'))
                except ValueError:
                    pass


    column_index_map = {col: idx for idx, col in enumerate(df.columns)}
    
    for row_idx, row in enumerate(soup.select('tbody tr')):
        row_data = df.iloc[row_idx]  # 获取原始df中对应的行
        keep_columns = filter_func(row_data)  # 由你定义的函数决定保留哪些列
    
        cells = row.find_all('td')
        for col_name in df.columns:
            col_idx = column_index_map[col_name]
            if col_name in keep_columns and col_idx < len(cells):
                text = cells[col_idx].text.strip()
                cells[col_idx].string = ''
                cells[col_idx].append(BeautifulSoup(f'<del>{text}</del>', 'html.parser'))
    

    # 第 8 列列名前添加 emoji
    if len(header_cells) > 7:
        header_cells[7].string = '🎾' + header_cells[7].text.strip()

    if status_index is not None:
        for row in soup.select('tbody tr'):
            cells = row.find_all('td')
            if status_index < len(cells):
                if cells[status_index].text.strip() == '存活':
                    row['style'] = 'background-color: #e6f7ff;'

    # 添加CSS样式
    style = """
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }
        .container {
            max-width: 100%;
            margin: 0 auto;
            overflow-x: auto;
            overflow-y: auto;
            min-height: 300px;
            max-height: 100vh;
        }
        .data-table {
            width: 100%;
            border-collapse: collapse;
            margin: 25px 0;
            font-size: 0.9em;
            min-width: 800px;
        }
        .data-table thead tr {
            background-color: #2c3e50;
            color: #ffffff;
            text-align: left;
        }
        .data-table th, 
        .data-table td {
            padding: 12px 15px;
            text-align: center;
        }
        .data-table tbody tr {
            border-bottom: 1px solid #dddddd;
        }
        .data-table tbody tr:nth-of-type(even) {
            background-color: #f3f3f3;
        }
        .data-table tbody tr:last-of-type {
            border-bottom: 2px solid #2c3e50;
        }
        .data-table tbody tr:hover {
            background-color: #f1f1f1;
            font-weight: bold;
        }
        .data-table th {
            position: sticky;
            top: 0;
            background-color: #2c3e50;
            z-index: 2;
        }
        .data-table thead {
            position: sticky;
            top: 0;
            z-index: 3;
        }
        .controls-container {
            margin: 20px 0;
            text-align: center;
            display: flex;
            justify-content: center;
            gap: 20px;
            align-items: center;
            flex-wrap: wrap;
        }
        #searchInput {
            padding: 10px;
            width: 300px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        #rowsPerPage {
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        .pagination {
            display: flex;
            justify-content: center;
            margin: 20px 0;
            gap: 10px;
        }
        .pagination button {
            padding: 8px 16px;
            background-color: #2c3e50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        .pagination button:hover {
            background-color: #1a252f;
        }
        .pagination button:disabled {
            background-color: #95a5a6;
            cursor: not-allowed;
        }
        .page-info {
            display: flex;
            align-items: center;
            padding: 0 10px;
        }
        .hidden {
            display: none;
        }
        .choice-info {
            margin: 10px auto 20px;
            text-align: center;
            font-size: 0.95em;
            color: #333;
        }
        .choice-info ul {
            list-style: none;
            padding: 0;
            margin: 5px 0 0;
        }
        .choice-info li {
            display: inline-block;
            margin: 0 12px;
        }
        /* 多选筛选控件样式 */
        .filter-container {
            display: flex;
            flex-direction: column;
            margin-top: 5px;
            align-items: center;
            justify-content: center;
        }
        .filter-dropdown {
            position: relative;
            display: inline-block;
        }
        .filter-button {
            width: 100px;
            padding: 3px;
            font-size: 12px;
            border: 1px solid #ddd;
            border-radius: 3px;
            background-color: white;
            cursor: pointer;
            text-align: left;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: #333;
        }
        .filter-button::after {
            content: "▼";
            float: right;
            font-size: 10px;
        }
        .filter-options {
            display: none;
            position: absolute;
            background-color: white;
            min-width: 120px;
            max-height: 200px;
            overflow-y: auto;
            border: 1px solid #ddd;
            border-radius: 3px;
            z-index: 100;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        }
        .filter-options.show {
            display: block;
        }
        .filter-option {
            padding: 5px 10px;
            cursor: pointer;
            display: flex;
            align-items: center;
            color: #333;
            background-color: white;
        }
        .filter-option:hover {
            background-color: #f1f1f1;
        }
        .filter-option label {
            cursor: pointer;
            color: #333;
            margin-left: 5px;
            width: 100%;
        }
        .filter-option input {
            margin-right: 5px;
            cursor: pointer;
        }
        .filter-option input:checked + label {
            font-weight: bold;
            color: #2c3e50;
        }
        .filter-summary {
            font-size: 11px;
            color: #666;
            margin-top: 3px;
            text-align: center;
        }
        .clear-button {
            padding: 3px 6px;
            margin-top: 3px;
            font-size: 12px;
            border: none;
            border-radius: 3px;
            background-color: #f44336;
            color: white;
            cursor: pointer;
        }
        .clear-button:hover {
            background-color: #d32f2f;
        }
        .match-count {
            text-align: center;
            margin: 10px 0;
            font-size: 14px;
            color: #2c3e50;
            font-weight: bold;
        }
        .select-all-container {
            display: flex;
            justify-content: space-between;
            padding: 5px 10px;
            border-bottom: 1px solid #eee;
        }
        .select-all-btn, .deselect-all-btn {
            padding: 2px 5px;
            font-size: 11px;
            border: 1px solid #ddd;
            border-radius: 3px;
            background-color: #f8f8f8;
            cursor: pointer;
        }
        .select-all-btn:hover {
            background-color: #e6f7ff;
        }
        .deselect-all-btn:hover {
            background-color: #fff2f0;
        }
    </style>
    """

    # JavaScript代码
    javascript = """
        <script>
            const table = document.querySelector('.data-table');
            const allRows = Array.from(table.querySelectorAll('tbody tr'));
            let filteredRows = [...allRows];
            let currentPage = 1;
            let rowsPerPage = 10;
            let columnFilters = {};
            
            function initPagination() {
                allRows.forEach((row, index) => {
                    row.setAttribute('data-index', index);
                });
                document.getElementById('searchInput').addEventListener('input', filterTable);
                document.getElementById('rowsPerPage').addEventListener('change', changeRowsPerPage);
                document.getElementById('prevPage').addEventListener('click', prevPage);
                document.getElementById('nextPage').addEventListener('click', nextPage);
                initHeaderFilters();
                updateTable();
            }
            
            function initHeaderFilters() {
                // 全局点击事件监听，点击空白处收回复选框
                document.addEventListener('click', function(event) {
                    if (!event.target.closest('.filter-button') && 
                        !event.target.closest('.filter-options') &&
                        !event.target.closest('.select-all-btn') &&
                        !event.target.closest('.deselect-all-btn')) {
                        document.querySelectorAll('.filter-options').forEach(option => {
                            option.classList.remove('show');
                        });
                    }
                });
        
                const headers = table.querySelectorAll('thead th');
                headers.forEach((header, index) => {
                    if (index < 4) return;
                    
                    const filterContainer = document.createElement('div');
                    filterContainer.className = 'filter-container';
                    
                    const dropdown = document.createElement('div');
                    dropdown.className = 'filter-dropdown';
                    
                    const button = document.createElement('button');
                    button.className = 'filter-button';
                    button.textContent = '筛选';
                    button.dataset.columnIndex = index;
                    
                    const options = document.createElement('div');
                    options.className = 'filter-options';
                    options.dataset.columnIndex = index;
                    
                    // 添加全选/取消全选按钮
                    const selectAllContainer = document.createElement('div');
                    selectAllContainer.className = 'select-all-container';
                    
                    const selectAllBtn = document.createElement('button');
                    selectAllBtn.className = 'select-all-btn';
                    selectAllBtn.textContent = '全选';
                    selectAllBtn.dataset.columnIndex = index;
                    
                    const deselectAllBtn = document.createElement('button');
                    deselectAllBtn.className = 'deselect-all-btn';
                    deselectAllBtn.textContent = '取消';
                    deselectAllBtn.dataset.columnIndex = index;
                    
                    selectAllContainer.appendChild(selectAllBtn);
                    selectAllContainer.appendChild(deselectAllBtn);
                    options.appendChild(selectAllContainer);
                    
                    const columnValues = new Set();
                    allRows.forEach(row => {
                        const cell = row.querySelector(`td:nth-child(${index + 1})`);
                        if (cell) columnValues.add(cell.textContent.trim());
                    });
                    
                    // 从本地存储加载已选状态
                    const savedFilters = JSON.parse(localStorage.getItem('columnFilters')) || {};
                    const savedValues = savedFilters[index] || [];
                    
                    Array.from(columnValues)
                        .sort((a, b) => {
                            if (index >= 7) {
                                const numA = parseFloat(a.replace(/[^0-9.\-]/g, ''));
                                const numB = parseFloat(b.replace(/[^0-9.\-]/g, ''));
                                const isNumA = !isNaN(numA);
                                const isNumB = !isNaN(numB);
                                if (isNumA && isNumB) return numB - numA;
                            }
                            return a.localeCompare(b, 'zh');
                        })
                        .forEach(value => {
                            const option = document.createElement('div');
                            option.className = 'filter-option';
                            
                            const checkbox = document.createElement('input');
                            checkbox.type = 'checkbox';
                            checkbox.value = value;
                            checkbox.id = `filter-${index}-${value.replace(/\s+/g, '-')}`;
                            if (savedValues.includes(value)) {
                                checkbox.checked = true;
                            }
                            
                            const label = document.createElement('label');
                            label.htmlFor = checkbox.id;
                            label.textContent = value;
                            
                            option.appendChild(checkbox);
                            option.appendChild(label);
                            options.appendChild(option);
                        });
                    
                    const summary = document.createElement('div');
                    summary.className = 'filter-summary';
                    summary.textContent = savedValues.length > 0 ? `${savedValues.length}项选中` : '0项选中';
                    summary.dataset.columnIndex = index;
                    
                    const clearButton = document.createElement('button');
                    clearButton.className = 'clear-button';
                    clearButton.textContent = '清除';
                    clearButton.dataset.columnIndex = index;
                    
                    dropdown.appendChild(button);
                    dropdown.appendChild(options);
                    filterContainer.appendChild(dropdown);
                    filterContainer.appendChild(summary);
                    filterContainer.appendChild(clearButton);
                    header.appendChild(filterContainer);
                    
                    // 点击按钮显示/隐藏选项
                    button.addEventListener('click', function(e) {
                        e.stopPropagation();
                        document.querySelectorAll('.filter-options').forEach(opt => {
                            if (opt !== options) opt.classList.remove('show');
                        });
                        options.classList.toggle('show');
                    });
                    
                    // 全选功能
                    selectAllBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        const columnIdx = parseInt(this.dataset.columnIndex);
                        document.querySelectorAll(`.filter-options[data-column-index="${columnIdx}"] input[type="checkbox"]`).forEach(cb => {
                            cb.checked = true;
                        });
                        updateColumnFilter(columnIdx);
                    });
                    
                    // 取消全选功能
                    deselectAllBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        const columnIdx = parseInt(this.dataset.columnIndex);
                        document.querySelectorAll(`.filter-options[data-column-index="${columnIdx}"] input[type="checkbox"]`).forEach(cb => {
                            cb.checked = false;
                        });
                        updateColumnFilter(columnIdx);
                    });
                    
                    // 点击选项时更新筛选
                    options.addEventListener('change', function(e) {
                        e.stopPropagation();
                        if (e.target.tagName === 'INPUT') {
                            updateColumnFilter(index);
                        }
                    });
                    
                    // 清除筛选
                    clearButton.addEventListener('click', function(e) {
                        e.stopPropagation();
                        const columnIndex = parseInt(this.dataset.columnIndex);
                        document.querySelectorAll(`.filter-options[data-column-index="${columnIndex}"] input[type="checkbox"]`).forEach(checkbox => {
                            checkbox.checked = false;
                        });
                        clearColumnFilter(columnIndex);
                    });
                });
                
                // 初始化时应用保存的筛选
                const savedFilters = JSON.parse(localStorage.getItem('columnFilters')) || {};
                if (Object.keys(savedFilters).length > 0) {
                    columnFilters = savedFilters;
                    filterTable();
                }
            }
            
            function updateColumnFilter(columnIndex) {
                const checkboxes = document.querySelectorAll(`.filter-options[data-column-index="${columnIndex}"] input:checked`);
                const selectedValues = Array.from(checkboxes).map(cb => cb.value);
                const summary = document.querySelector(`.filter-summary[data-column-index="${columnIndex}"]`);
                
                if (selectedValues.length === 0) {
                    delete columnFilters[columnIndex];
                    summary.textContent = '0项选中';
                } else {
                    columnFilters[columnIndex] = selectedValues;
                    summary.textContent = `${selectedValues.length}项选中`;
                }
                
                // 保存筛选状态到本地存储
                localStorage.setItem('columnFilters', JSON.stringify(columnFilters));
                filterTable();
            }
            
            function clearColumnFilter(columnIndex) {
                delete columnFilters[columnIndex];
                document.querySelector(`.filter-summary[data-column-index="${columnIndex}"]`).textContent = '0项选中';
                localStorage.setItem('columnFilters', JSON.stringify(columnFilters));
                filterTable();
            }
            
            function updateTable() {
                const totalRows = filteredRows.length;
                const totalPages = rowsPerPage === 0 ? 1 : Math.ceil(totalRows / rowsPerPage);
                const startIndex = (currentPage - 1) * rowsPerPage;
                const endIndex = rowsPerPage === 0 ? totalRows : startIndex + rowsPerPage;
                
                allRows.forEach(row => row.classList.add('hidden'));
                filteredRows.slice(startIndex, endIndex).forEach(row => {
                    row.classList.remove('hidden');
                });
                
                document.getElementById('pageInfo').textContent = '第' + currentPage + '页/共' + totalPages + '页';
                document.getElementById('prevPage').disabled = currentPage <= 1;
                document.getElementById('nextPage').disabled = currentPage >= totalPages || rowsPerPage === 0;
                
                // 更新匹配记录数显示
                const matchCountElement = document.getElementById('matchCount');
                if (matchCountElement) {
                    matchCountElement.textContent = `共找到 ${totalRows} 条匹配记录`;
                }
            }
            
            function filterTable() {
                const searchTerm = document.getElementById('searchInput').value.toLowerCase();
                filteredRows = allRows.filter(row => {
                    const cells = row.querySelectorAll('td');
                    
                    // 全局搜索匹配
                    const globalMatch = searchTerm === '' || 
                        Array.from(cells).some(cell => 
                            cell.textContent.toLowerCase().includes(searchTerm)
                        );
                    if (!globalMatch) return false;
                    
                    // 列筛选匹配
                    for (const [columnIndex, filterValues] of Object.entries(columnFilters)) {
                        const cellIndex = parseInt(columnIndex);
                        if (cellIndex < cells.length) {
                            const cellText = cells[cellIndex].textContent.trim();
                            if (!filterValues.includes(cellText)) {
                                return false;
                            }
                        }
                    }
                    return true;
                });
                
                currentPage = 1;
                updateTable();
            }
            
            function changeRowsPerPage() {
                rowsPerPage = parseInt(document.getElementById('rowsPerPage').value);
                currentPage = 1;
                updateTable();
            }
            
            function prevPage() {
                if (currentPage > 1) {
                    currentPage--;
                    updateTable();
                }
            }
            
            function nextPage() {
                const totalRows = filteredRows.length;
                const totalPages = rowsPerPage === 0 ? 1 : Math.ceil(totalRows / rowsPerPage);
                if (currentPage < totalPages) {
                    currentPage++;
                    updateTable();
                }
            }
            
            document.addEventListener('DOMContentLoaded', initPagination);
        </script>
    """

    # 创建完整的HTML文档
    full_html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        {style}
    </head>
    <body>
        <div class="container">
            <h1>{title}</h1>
            <div style="text-align: center; color: #888; font-size: 0.9em; margin-bottom: 10px;">
                页面数据更新时间：{now}
            </div>
            {choice_info_html}
            <div class="controls-container">
                <div>
                    <input type="text" id="searchInput" placeholder="搜索用户名或分数...">
                </div>
                <div>
                    <select id="rowsPerPage">
                        <option value="10">每页10行</option>
                        <option value="25">每页25行</option>
                        <option value="50">每页50行</option>
                        <option value="100">每页100行</option>
                        <option value="0">显示全部</option>
                    </select>
                </div>
            </div>
            <div id="matchCount" class="match-count">共找到 {len(df)} 条记录</div>
            <div class="data-table-wrapper">
                {str(soup)}
            </div>
            <div class="pagination">
                <button id="prevPage" disabled>上一页</button>
                <div class="page-info" id="pageInfo">第1页</div>
                <button id="nextPage">下一页</button>
            </div>
        </div>
        {javascript}
    </body>
    </html>
    """

    # 写入HTML文件
    with open(output_path + output_file, 'w', encoding='utf-8') as f:
        f.write(full_html)

    print(f"成功生成HTML文件: {output_file}")



def push_to_github(file_paths, commit_message, repo_path, branch='main'):

    if isinstance(file_paths, str):
        file_paths = [file_paths]
    os.chdir(repo_path)

    stash_applied = False

    try:
        # 1. 若本地有未提交修改，先 stash
        unstaged = subprocess.run(['git', 'diff', '--quiet'])
        if unstaged.returncode != 0:
            print("🔄 本地有未提交变更，自动 stash...")
            subprocess.run(['git', 'stash'], check=True)
            stash_applied = True

        # # 2. 拉取远程
        # print("📥 拉取远程仓库...")
        # subprocess.run(['git', 'pull', '--rebase', 'origin', branch], check=True)

        # 3. 恢复 stash 内容
        if stash_applied:
            print("🔁 还原之前的变更...")
            subprocess.run(['git', 'stash', 'pop'], check=True)

        # 4. 添加文件
        print("📦 添加文件到暂存区...")
        subprocess.run(['git', 'add'] + file_paths, check=True)

        # 5. 判断是否有变动
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'])
        if result.returncode == 0:
            print("😴 没有变化需要提交，跳过提交和推送。")
            return

        # 6. 提交 + 推送
        print("✅ 提交变更...")
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)

        print("🚀 推送到 GitHub...")
        subprocess.run(['git', 'push', 'origin', branch], check=True)

        print("🎉 成功推送到 GitHub！")

    except subprocess.CalledProcessError as e:
        print(f"❌ Git 操作失败: {e}")