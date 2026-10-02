<script>
        const table = document.querySelector('.data-table');
        const allRows = Array.from(table.querySelectorAll('tbody tr'));
        const tag = 4;
        const startCollapsed = true;
        let filteredRows = [...allRows];
        let currentPage = 1;
        let rowsPerPage = 10;
        let columnFilters = {};
        
        function initPagination() {
            // 清除本地存储的筛选状态
            localStorage.removeItem('columnFilters');
            columnFilters = {};
            
            allRows.forEach((row, index) => {
                row.setAttribute('data-index', index);
            });
            document.getElementById('searchInput').addEventListener('input', filterTable);
            document.getElementById('rowsPerPage').addEventListener('change', changeRowsPerPage);
            document.getElementById('prevPage').addEventListener('click', prevPage);
            document.getElementById('nextPage').addEventListener('click', nextPage);
            initHeaderFilters();
            updateTable();
            
            // 清除所有复选框的选中状态
            document.querySelectorAll('.filter-options input[type="checkbox"]').forEach(checkbox => {
                checkbox.checked = false;
            });
            document.querySelectorAll('.filter-summary').forEach(summary => {
                summary.textContent = '0项选中';
            });
            
            // 清空搜索框
            document.getElementById('searchInput').value = '';
        }
        
        function initHeaderFilters() {
            // 全局点击事件监听
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
                if (index < tag) return;
                
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
                
                // 不再从本地存储加载
                Array.from(columnValues)
                    .sort((a, b) => {
                        // 尝试将值转换为数字
                        const numA = parseFloat(a.replace(/[^0-9.\-]/g, ''));
                        const numB = parseFloat(b.replace(/[^0-9.\-]/g, ''));
                        const isNumA = !isNaN(numA);
                        const isNumB = !isNaN(numB);
                        
                        // 情况1：两个值都是数字 -> 按数值降序
                        if (isNumA && isNumB) {
                            return numB - numA; // 降序排列
                        }
                        // 情况2：只有A是数字 -> 数字排在前面（降序时大数在前）
                        else if (isNumA) {
                            return -1;
                        }
                        // 情况3：只有B是数字 -> 数字排在前面
                        else if (isNumB) {
                            return 1;
                        }
                        // 情况4：都不是数字 -> 按中文拼音升序
                        else {
                            return a.localeCompare(b, 'zh');
                        }
                    })
                    .forEach(value => {
                        const option = document.createElement('div');
                        option.className = 'filter-option';
                        
                        const checkbox = document.createElement('input');
                        checkbox.type = 'checkbox';
                        checkbox.value = value;
                        checkbox.id = `filter-${index}-${value.replace(/\s+/g, '-')}`;
                        
                        const label = document.createElement('label');
                        label.htmlFor = checkbox.id;
                        label.textContent = value;
                        
                        option.appendChild(checkbox);
                        option.appendChild(label);
                        options.appendChild(option);
                    });
                
                const summary = document.createElement('div');
                summary.className = 'filter-summary';
                summary.textContent = '0项选中';  // 初始为0项选中
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
            
            filterTable();
        }
        
        function clearColumnFilter(columnIndex) {
            delete columnFilters[columnIndex];
            document.querySelector(`.filter-summary[data-column-index="${columnIndex}"]`).textContent = '0项选中';
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
        function toggleStatsTable(tableId) {
            const table = document.getElementById(tableId);
            const isHidden = table.classList.toggle('hidden');
            const btn = table.previousElementSibling;
            const btnText = btn.querySelector('.btn-text');
            const arrowIcon = btn.querySelector('.arrow-icon');
            
            if (isHidden) {
                btnText.textContent = btnText.textContent.replace('隐藏', '显示');
                arrowIcon.textContent = '▼';
                arrowIcon.style.transform = 'rotate(0deg)';
            } else {
                btnText.textContent = btnText.textContent.replace('显示', '隐藏');
                arrowIcon.textContent = '▲';
                arrowIcon.style.transform = 'rotate(0deg)';
            }
        }
        
        // 展开/收起功能
        function initExpandToggle() {
            const expandBtn = document.getElementById('expandToggleBtn');
            if (!expandBtn) return;
            
            const tableContainer = document.querySelector('.container');
            if (startCollapsed) {
                tableContainer.classList.add('collapsed');
                expandBtn.innerHTML = '<span class="btn-text">展开列</span><span class="expand-icon">▶</span>';
            } else {
                tableContainer.classList.remove('collapsed');
                expandBtn.innerHTML = '<span class="btn-text">收起列</span><span class="expand-icon">▼</span>';
            }
            
            expandBtn.addEventListener('click', function() {
                const isCollapsed = tableContainer.classList.contains('collapsed');
                
                if (isCollapsed) {
                    // 展开
                    tableContainer.classList.remove('collapsed');
                    expandBtn.innerHTML = '<span class="btn-text">收起列</span><span class="expand-icon">▼</span>';
                } else {
                    // 收起
                    tableContainer.classList.add('collapsed');
                    expandBtn.innerHTML = '<span class="btn-text">展开列</span><span class="expand-icon">▶</span>';
                }
            });
        }
        
        // 初始化所有功能
        document.addEventListener('DOMContentLoaded', function() {
            initPagination();
            initExpandToggle();
        });
    </script>