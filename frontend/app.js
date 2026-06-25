// State management
let allTenders = [];
let filteredTenders = [];
let currentFilter = 'all'; // 'all', 'lead_created', 'rules_passed', 'rejected_ai', 'rules_rejected'
let currentSort = { column: 'created_at', direction: 'desc' };

// DOM Elements
const refreshBtn = document.getElementById('refresh-btn');
const searchInput = document.getElementById('search-input');
const filterSource = document.getElementById('filter-source');
const filterDate = document.getElementById('filter-date');
const filterDateCustom = document.getElementById('filter-date-custom');
const renderEmailBtn = document.getElementById('renderemail');
const tendersTableBody = document.getElementById('tenders-table-body');
const tableLoading = document.getElementById('table-loading');
const tableEmpty = document.getElementById('table-empty');
const tabs = document.querySelectorAll('.tab-btn');
const consoleCard = document.getElementById('console-card');
const consoleLogBody = document.getElementById('console-log-body');
const consoleTitle = document.getElementById('console-title');
const consoleClose = document.getElementById('console-close');

// Stats Elements
const statTotal = document.getElementById('stat-total');
const statS1Pass = document.getElementById('stat-s1-pass');
const statS1Fail = document.getElementById('stat-s1-fail');
const statS2Leads = document.getElementById('stat-s2-leads');
const statS2Fail = document.getElementById('stat-s2-fail');

// Track previous values for animation
let prevStats = { total: 0, s1Pass: 0, s1Fail: 0, s2Leads: 0, s2Fail: 0 };

// Settings Elements
const ruleRange = document.getElementById('rule-range');
const ruleThreshold = document.getElementById('rule-threshold');
const ruleIncludeKeywords = document.getElementById('rule-include-keywords');
const ruleExcludeKeywords = document.getElementById('rule-exclude-keywords');
const ruleScope = document.getElementById('rule-scope');
const ruleDisqualifiers = document.getElementById('rule-disqualifiers');

// Drawer Elements
const drawerBackdrop = document.getElementById('drawer-backdrop');
const drawer = document.getElementById('drawer');
const drawerClose = document.getElementById('drawer-close');
const drawerTenderId = document.getElementById('drawer-tender-id');
const drawerSourceSubtitle = document.getElementById('drawer-source-subtitle');
const drawerScoreSection = document.getElementById('drawer-score-section');
const drawerScoreCircle = document.getElementById('drawer-score-circle');
const drawerActionText = document.getElementById('drawer-action-text');
const drawerTitle = document.getElementById('drawer-title');
const drawerAuthority = document.getElementById('drawer-authority');
const drawerLocation = document.getElementById('drawer-location');
const drawerValue = document.getElementById('drawer-value');
const drawerEmd = document.getElementById('drawer-emd');
const drawerDueDate = document.getElementById('drawer-due-date');
const drawerProcessedDate = document.getElementById('drawer-processed-date');
const drawerLink = document.getElementById('drawer-link');
const drawerS1Verdict = document.getElementById('drawer-s1-verdict');
const drawerS1Rationale = document.getElementById('drawer-s1-rationale');
const drawerRationaleSection = document.getElementById('drawer-rationale-section');
const drawerRationaleBody = document.getElementById('drawer-rationale-body');
const drawerExtractedScopeSection = document.getElementById('drawer-extracted-scope-section');
const drawerExtractedScope = document.getElementById('drawer-extracted-scope');
const drawerExtractedEligibility = document.getElementById('drawer-extracted-eligibility');

// Column Headers for sorting
const headers = {
    'th-id': 'tender_id',
    'th-source': 'source',
    'th-title': 'title',
    'th-details': 'authority',
    'th-value': 'value',
    'th-arrival': 'arrival_date',
    'th-duedate': 'due_date',
    'th-status': 'status',
    'th-score': 'ai_score'
};

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadRules();
    loadTenders();

    // Event Listeners
    refreshBtn.addEventListener('click', () => {
        loadStats();
        loadTenders();
    });

    renderEmailBtn.addEventListener('click', startEmailScrape);

    // Sync KPI Card Clicks to Navigation Tabs
    const cardIntake = document.querySelector('.stat-card.intake');
    if (cardIntake) cardIntake.addEventListener('click', () => triggerTab('tab-all'));
    const cardS1Pass = document.querySelector('.stat-card.s1-pass');
    if (cardS1Pass) cardS1Pass.addEventListener('click', () => triggerTab('tab-s1passed'));
    const cardS1Fail = document.querySelector('.stat-card.s1-fail');
    if (cardS1Fail) cardS1Fail.addEventListener('click', () => triggerTab('tab-s1failed'));
    const cardS2Leads = document.querySelector('.stat-card.s2-leads');
    if (cardS2Leads) cardS2Leads.addEventListener('click', () => triggerTab('tab-promoted'));
    const cardS2Fail = document.querySelector('.stat-card.s2-fail');
    if (cardS2Fail) cardS2Fail.addEventListener('click', () => triggerTab('tab-s2failed'));

    searchInput.addEventListener('input', applyFilters);
    filterSource.addEventListener('change', applyFilters);

    // Date Filters
    filterDate.addEventListener('change', () => {
        if (filterDate.value === 'custom') {
            filterDateCustom.style.display = 'inline-block';
        } else {
            filterDateCustom.style.display = 'none';
        }
        applyFilters();
    });
    filterDateCustom.addEventListener('change', applyFilters);

    // Tab Filters
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentFilter = tab.getAttribute('data-filter');
            
            // Set default sorting based on the tab
            if (currentFilter === 'lead_created') {
                currentSort = { column: 'ai_score', direction: 'desc' };
            } else {
                currentSort = { column: 'created_at', direction: 'desc' };
            }
            updateSortHeadersUI();
            
            applyFilters();
        });
    });

    // Drawer closing
    drawerClose.addEventListener('click', closeDrawer);
    drawerBackdrop.addEventListener('click', closeDrawer);

    // Console closing
    if (consoleClose) {
        consoleClose.addEventListener('click', () => {
            if (consoleCard) consoleCard.style.display = 'none';
        });
    }

    // Table Header Sorting
    Object.keys(headers).forEach(headerId => {
        const el = document.getElementById(headerId);
        if (el) {
            el.style.cursor = 'pointer';
            el.addEventListener('click', () => handleSort(headers[headerId]));
        }
    });
});

// Load KPI Stats
async function loadStats() {
    try {
        const res = await fetch('/api/stats');
        const stats = await res.json();
        
        if (statTotal) statTotal.textContent = stats.total_intake || 0;
        if (statS1Pass) statS1Pass.textContent = stats.stage1_passed || 0;
        if (statS1Fail) statS1Fail.textContent = stats.stage1_rejected || 0;
        if (statS2Leads) statS2Leads.textContent = stats.stage2_leads || 0;
        if (statS2Fail) statS2Fail.textContent = stats.stage2_rejected || 0;
    } catch (e) {
        console.error('Failed to load stats:', e);
    }
}

// Load KBP Settings Rules
async function loadRules() {
    try {
        const res = await fetch('/api/rules');
        const rules = await res.json();
        
        ruleRange.textContent = `${rules.min_tender_value || 'N/A'} - ${rules.max_tender_value || 'N/A'}`;
        ruleThreshold.textContent = `${rules.ai_score_threshold || 70} / 100`;
        ruleScope.textContent = rules.scope_of_work || 'None';
        ruleDisqualifiers.textContent = rules.disqualifiers || 'None';

        // Render include keywords badges
        ruleIncludeKeywords.innerHTML = '';
        if (rules.stage1_include_keywords && rules.stage1_include_keywords.length) {
            rules.stage1_include_keywords.forEach(kw => {
                const badge = document.createElement('span');
                badge.className = 'keyword-badge';
                badge.textContent = kw;
                ruleIncludeKeywords.appendChild(badge);
            });
        } else {
            ruleIncludeKeywords.textContent = 'None';
        }

        // Render exclude keywords badges
        ruleExcludeKeywords.innerHTML = '';
        if (rules.stage1_exclude_keywords && rules.stage1_exclude_keywords.length) {
            rules.stage1_exclude_keywords.forEach(kw => {
                const badge = document.createElement('span');
                badge.className = 'keyword-badge exclude';
                badge.textContent = kw;
                ruleExcludeKeywords.appendChild(badge);
            });
        } else {
            ruleExcludeKeywords.textContent = 'None';
        }
    } catch (e) {
        console.error('Failed to load rules:', e);
    }
}

// Load Tenders Data
async function loadTenders() {
    tableLoading.style.display = 'flex';
    tendersTableBody.innerHTML = '';
    tableEmpty.style.display = 'none';

    try {
        const res = await fetch('/api/tenders');
        allTenders = await res.json();
        applyFilters();
    } catch (e) {
        console.error('Failed to load tenders:', e);
        tableLoading.style.display = 'none';
        tableEmpty.style.display = 'block';
        tableEmpty.innerHTML = `<div class="empty-state-icon">⚠️</div><p>Failed to retrieve data. Make sure server is running and database is populated.</p>`;
    }
}

// Handle Sorting
function handleSort(column) {
    if (currentSort.column === column) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.column = column;
        currentSort.direction = 'desc';
    }
    updateSortHeadersUI();
    sortAndRenderTenders();
}

// Update Sort Icons in Header UI
function updateSortHeadersUI() {
    Object.keys(headers).forEach(headerId => {
        const el = document.getElementById(headerId);
        if (!el) return;
        
        // Clear existing indicators
        let text = el.textContent.replace(' ▲', '').replace(' ▼', '');
        
        if (headers[headerId] === currentSort.column) {
            text += currentSort.direction === 'asc' ? ' ▲' : ' ▼';
        }
        el.textContent = text;
    });
}

// Apply Search and Filters
function applyFilters() {
    const query = searchInput.value.toLowerCase().trim();
    const source = filterSource.value;
    const dateRange = filterDate.value;

    // 1. Filter by search, source, and date to get active scoped list
    const scopedTenders = allTenders.filter(tender => {
        // Date Range Filter
        if (dateRange !== 'all') {
            if (!tender.created_at) return false;
            const tenderDateStr = tender.created_at.split('T')[0];
            
            if (dateRange === 'today') {
                if (tenderDateStr !== getLocalDateString(0)) return false;
            } else if (dateRange === 'yesterday') {
                if (tenderDateStr !== getLocalDateString(1)) return false;
            } else if (dateRange === 'last-7') {
                const tenderDate = new Date(tenderDateStr);
                tenderDate.setHours(0,0,0,0);
                
                const cutoffDate = new Date();
                cutoffDate.setDate(cutoffDate.getDate() - 7);
                cutoffDate.setHours(0,0,0,0);
                
                const todayDate = new Date();
                todayDate.setHours(0,0,0,0);
                
                if (tenderDate < cutoffDate || tenderDate > todayDate) return false;
            } else if (dateRange === 'custom') {
                const customVal = filterDateCustom.value;
                if (!customVal || tenderDateStr !== customVal) return false;
            }
        }

        // Platform Source Filter
        if (source !== 'all' && tender.source !== source) return false;

        // Search Query Filter
        if (query) {
            const idMatch = tender.tender_id ? String(tender.tender_id).toLowerCase().includes(query) : false;
            const titleMatch = (tender.title || '').toLowerCase().includes(query);
            const authMatch = (tender.authority || '').toLowerCase().includes(query);
            const locMatch = (tender.location || '').toLowerCase().includes(query);
            return idMatch || titleMatch || authMatch || locMatch;
        }

        return true;
    });

    // 2. Update KPI Summary Stats dynamically based on this scoped list
    updateStatsCards(scopedTenders);

    // 3. Filter scoped list by the active status tab
    filteredTenders = scopedTenders.filter(tender => {
        if (currentFilter !== 'all') {
            if (currentFilter === 'lead_created' && tender.status !== 'lead_created') return false;
            if (currentFilter === 'rules_passed' && tender.status !== 'rules_passed') return false;
            if (currentFilter === 'rejected_ai' && tender.status !== 'rejected_ai') return false;
            if (currentFilter === 'rules_rejected' && tender.status !== 'rules_rejected') return false;
        }
        return true;
    });

    sortAndRenderTenders();
}

// Dynamically compute KPI counts from the filtered subset and animate the cards
function updateStatsCards(scopedTenders) {
    let total = scopedTenders.length;
    let s1Pass = 0;
    let s1Fail = 0;
    let s2Leads = 0;
    let s2Fail = 0;

    scopedTenders.forEach(t => {
        if (t.status === 'rules_rejected') {
            s1Fail++;
        } else {
            s1Pass++;
            if (t.status === 'lead_created') {
                s2Leads++;
            } else if (t.status === 'rejected_ai') {
                s2Fail++;
            }
        }
    });

    // Animate each stat card value change
    animateStatValue(statTotal, prevStats.total, total);
    animateStatValue(statS1Pass, prevStats.s1Pass, s1Pass);
    animateStatValue(statS1Fail, prevStats.s1Fail, s1Fail);
    animateStatValue(statS2Leads, prevStats.s2Leads, s2Leads);
    animateStatValue(statS2Fail, prevStats.s2Fail, s2Fail);

    prevStats = { total, s1Pass, s1Fail, s2Leads, s2Fail };

    // Also pulse the stat cards to indicate update
    document.querySelectorAll('.stat-card').forEach(card => {
        card.classList.remove('stat-updated');
        void card.offsetWidth; // force reflow
        card.classList.add('stat-updated');
    });
}

// Animate a number counter from `from` to `to`
function animateStatValue(el, from, to) {
    if (!el) return;
    if (from === to) return;
    const duration = 350;
    const start = performance.now();
    const diff = to - from;

    function step(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);
        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.round(from + diff * eased);
        if (progress < 1) requestAnimationFrame(step);
        else el.textContent = to;
    }
    requestAnimationFrame(step);
}

// Sort and Render Table Rows
function sortAndRenderTenders() {
    const { column, direction } = currentSort;
    
    filteredTenders.sort((a, b) => {
        let valA = a[column];
        let valB = b[column];

        // Handle nulls
        if (valA === undefined || valA === null) valA = '';
        if (valB === undefined || valB === null) valB = '';

        // Score sorting should treat empty scores as -1 so they go to bottom
        if (column === 'ai_score') {
            valA = valA !== '' ? parseFloat(valA) : -1;
            valB = valB !== '' ? parseFloat(valB) : -1;
        }

        // Sort comparison
        if (valA < valB) return direction === 'asc' ? -1 : 1;
        if (valA > valB) return direction === 'asc' ? 1 : -1;
        return 0;
    });

    renderTable();
}

// Render Table to DOM
function renderTable() {
    tableLoading.style.display = 'none';
    tendersTableBody.innerHTML = '';

    if (filteredTenders.length === 0) {
        tableEmpty.style.display = 'block';
        return;
    }

    tableEmpty.style.display = 'none';

    filteredTenders.forEach(tender => {
        const tr = document.createElement('tr');
        tr.dataset.id = tender.tender_id;
        tr.addEventListener('click', () => openDrawer(tender));

        // Format Source Badge
        const sourceClass = tender.source === 'Tender247' ? 't247' : 'tdetail';
        const sourceBadge = `<span class="tag-platform ${sourceClass}">${tender.source}</span>`;

        // Format Status Badge
        let statusBadge = '';
        if (tender.status === 'lead_created') {
            statusBadge = '<span class="badge badge-lead">Lead Promoted</span>';
        } else if (tender.status === 'rules_passed') {
            statusBadge = '<span class="badge badge-passed">Stage 1 Pass</span>';
        } else if (tender.status === 'rules_rejected') {
            statusBadge = '<span class="badge badge-rejected">Stage 1 Fail</span>';
        } else if (tender.status === 'rejected_ai') {
            statusBadge = '<span class="badge badge-rejected">Stage 2 Reject</span>';
        } else {
            statusBadge = `<span class="badge badge-pending">${tender.status}</span>`;
        }

        // Format AI Score Column
        let scoreDisplay = '<span class="score-value score-na">-</span>';
        if (tender.ai_score !== null && tender.ai_score !== undefined && tender.ai_score !== '') {
            const score = parseFloat(tender.ai_score);
            let scoreClass = 'score-low';
            if (score >= 70) scoreClass = 'score-high';
            else if (score >= 50) scoreClass = 'score-med';
            scoreDisplay = `<span class="score-value ${scoreClass}">${score.toFixed(1)}</span>`;
        }

        // Format Title and details with responsive CSS classes
        const titleText = tender.title;
        const detailsText = `${tender.authority || 'N/A'}<br><span style="color: var(--text-secondary); font-size: 0.8rem;">📍 ${tender.location || 'N/A'}</span>`;

        tr.innerHTML = `
            <td style="font-weight: 600; color: #fff;">#${tender.tender_id}</td>
            <td>${sourceBadge}</td>
            <td class="col-title" title="${tender.title}">${titleText}</td>
            <td class="col-details">${detailsText}</td>
            <td>${tender.value || 'N/A'}</td>
            <td>${tender.arrival_date || (tender.created_at ? tender.created_at.substring(0,10) : 'N/A')}</td>
            <td>${tender.due_date || 'N/A'}</td>
            <td>${statusBadge}</td>
            <td style="text-align: center;">${scoreDisplay}</td>
        `;
        tendersTableBody.appendChild(tr);
    });
}

// Open Details Drawer
function openDrawer(tender) {
    drawerTenderId.textContent = `Tender #${tender.tender_id}`;
    drawerSourceSubtitle.textContent = `Source: ${tender.source} | Processed on ${tender.created_at ? tender.created_at.replace('T', ' ').substring(0, 19) : 'N/A'}`;
    
    drawerTitle.textContent = tender.title;
    drawerAuthority.textContent = tender.authority || 'N/A';
    drawerLocation.textContent = tender.location || 'N/A';
    drawerValue.textContent = tender.value || 'N/A';
    drawerEmd.textContent = tender.emd || 'N/A';
    drawerDueDate.textContent = tender.due_date || 'N/A';
    // Arrival date = date the tender was first seen/scraped into the system
    const arrivalDisplay = tender.arrival_date || (tender.created_at ? tender.created_at.substring(0, 10) : 'N/A');
    drawerProcessedDate.textContent = arrivalDisplay;
    
    if (tender.link) {
        drawerLink.href = tender.link;
        drawerLink.style.display = 'inline-flex';
    } else {
        drawerLink.style.display = 'none';
    }

    // Stage 1 Status and Rationale
    if (tender.status === 'rules_rejected') {
        drawerS1Verdict.className = 'badge badge-rejected';
        drawerS1Verdict.textContent = 'Stage 1 Rejected';
        // If rejected at Stage 1, the AI Rationale column in DB holds the Stage 1 keywords/rules check details
        drawerS1Rationale.innerHTML = `<div class="rationale-alert"><strong>Keyword Filter Rationale:</strong><br>${tender.ai_rationale || 'Does not match KBP required keywords or triggered exclusion keywords.'}</div>`;
    } else {
        drawerS1Verdict.className = 'badge badge-passed';
        drawerS1Verdict.textContent = 'Stage 1 Passed';
        drawerS1Rationale.textContent = 'Tender successfully matched KBP required keywords and bypassed all exclude/disqualifier keywords in title check.';
    }

    // Stage 2 Score and Detail
    const hasStage2 = (tender.status === 'lead_created' || tender.status === 'rejected_ai');
    
    if (hasStage2 && tender.ai_score !== null && tender.ai_score !== undefined) {
        drawerScoreSection.style.display = 'flex';
        const score = parseFloat(tender.ai_score);
        drawerScoreCircle.textContent = score.toFixed(1);
        
        let scoreColor = '#ef4444';
        let actionText = 'Drop (Below 70 score threshold)';
        if (score >= 70) {
            scoreColor = '#10b981';
            actionText = 'Bid (Promoted to leads list)';
        } else if (score >= 50) {
            scoreColor = '#f59e0b';
            actionText = 'Manual Review Recommended';
        }
        
        drawerScoreCircle.style.setProperty('--score-color', scoreColor);
        drawerScoreCircle.style.borderColor = scoreColor;
        drawerActionText.innerHTML = `<strong>Action Recommendation:</strong> <span style="color: ${scoreColor}">${actionText}</span>`;
        
        // Show dimension section and parse it from rationale
        drawerRationaleSection.style.display = 'block';
        drawerRationaleBody.innerHTML = '';
        
        const parsedDimensions = parseFullRationale(tender.ai_rationale);
        
        // Display Dimension Bars
        const dimensionSection = document.getElementById('drawer-dimension-section');
        dimensionSection.style.display = 'block';
        
        // Set dimension bars widths & texts
        animateDimensionBar('bar-scope', 'score-val-scope', parsedDimensions.scope);
        animateDimensionBar('bar-location', 'score-val-location', parsedDimensions.location);
        animateDimensionBar('bar-eligibility', 'score-val-eligibility', parsedDimensions.eligibility);
        animateDimensionBar('bar-value', 'score-val-value', parsedDimensions.value);
        animateDimensionBar('bar-disq', 'score-val-disq', parsedDimensions.disq);

        // Display Detailed Text Rationale Sections
        let rationaleHtml = '';
        if (parsedDimensions.scopeRationale) {
            rationaleHtml += `<p style="margin-bottom: 12px;"><strong>🎯 Scope Match Assessment:</strong><br>${parsedDimensions.scopeRationale}</p>`;
        }
        if (parsedDimensions.locationRationale) {
            rationaleHtml += `<p style="margin-bottom: 12px;"><strong>📍 Location Suitability:</strong><br>${parsedDimensions.locationRationale}</p>`;
        }
        if (parsedDimensions.eligibilityRationale) {
            rationaleHtml += `<p style="margin-bottom: 12px;"><strong>🎓 Credentials &amp; Eligibility:</strong><br>${parsedDimensions.eligibilityRationale}</p>`;
        }
        if (parsedDimensions.valueRationale) {
            rationaleHtml += `<p style="margin-bottom: 12px;"><strong>💰 Value &amp; Budget Fit:</strong><br>${parsedDimensions.valueRationale}</p>`;
        }
        if (parsedDimensions.disqRationale) {
            rationaleHtml += `<p style="margin-bottom: 12px;"><strong>🚫 Disqualifier Scan:</strong><br>${parsedDimensions.disqRationale}</p>`;
        }
        if (parsedDimensions.risks) {
            rationaleHtml += `<p style="margin-bottom: 12px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.06);"><strong>⚠️ Key Risks / Concerns:</strong><br>${parsedDimensions.risks}</p>`;
        }

        if (rationaleHtml) {
            drawerRationaleBody.innerHTML = rationaleHtml;
        } else {
            drawerRationaleBody.textContent = tender.ai_rationale || 'No detailed rationale text stored.';
        }
    } else {
        // No stage 2 scoring yet
        drawerScoreSection.style.display = 'flex';
        drawerScoreCircle.textContent = '-';
        drawerScoreCircle.style.setProperty('--score-color', 'var(--text-muted)');
        drawerScoreCircle.style.borderColor = 'var(--text-muted)';
        
        let subText = 'Intake filtered, awaiting Stage 2 execution.';
        if (tender.status === 'rules_rejected') {
            subText = 'Rejected at Stage 1 (Scope keyword check failed)';
        }
        drawerActionText.innerHTML = `<strong>Status:</strong> ${subText}`;
        
        document.getElementById('drawer-dimension-section').style.display = 'none';
        drawerRationaleSection.style.display = 'none';
    }

    // Extracted scope and eligibility details from stage 2 (if present)
    if (tender.scope_of_work || tender.eligibility) {
        drawerExtractedScopeSection.style.display = 'block';
        drawerExtractedScope.textContent = tender.scope_of_work || 'Not extracted.';
        drawerExtractedEligibility.textContent = tender.eligibility || 'Not extracted.';
    } else {
        drawerExtractedScopeSection.style.display = 'none';
    }

    // Show Drawer
    drawerBackdrop.classList.add('active');
    drawer.classList.add('active');
}

// Close Details Drawer
function closeDrawer() {
    drawerBackdrop.classList.remove('active');
    drawer.classList.remove('active');
}

// Animate a dimension bar width and score
function animateDimensionBar(barId, valId, score) {
    const bar = document.getElementById(barId);
    const textVal = document.getElementById(valId);
    
    if (bar && textVal) {
        textVal.textContent = `${score}/10`;
        // Delay slightly for transition animation
        setTimeout(() => {
            bar.style.width = `${score * 10}%`;
        }, 100);
    }
}

// Parse AI rationale string into distinct segments & estimate raw scores
function parseFullRationale(rationaleText) {
    const data = {
        scope: 0,
        location: 0,
        eligibility: 0,
        value: 0,
        disq: 0,
        scopeRationale: '',
        locationRationale: '',
        eligibilityRationale: '',
        valueRationale: '',
        disqRationale: '',
        risks: ''
    };

    if (!rationaleText) return data;

    // Split text by lines
    const lines = rationaleText.split('\n');
    
    lines.forEach(line => {
        const lower = line.toLowerCase().trim();
        if (lower.startsWith('scope match:') || lower.startsWith('scope:')) {
            data.scopeRationale = line.substring(line.indexOf(':') + 1).trim();
            data.scope = extractScoreFromText(data.scopeRationale, 8); // Default fallback score
        } else if (lower.startsWith('location:')) {
            data.locationRationale = line.substring(line.indexOf(':') + 1).trim();
            data.location = extractScoreFromText(data.locationRationale, 10);
        } else if (lower.startsWith('eligibility:')) {
            data.eligibilityRationale = line.substring(line.indexOf(':') + 1).trim();
            data.eligibility = extractScoreFromText(data.eligibilityRationale, 7);
        } else if (lower.startsWith('value:') || lower.startsWith('value fit:')) {
            data.valueRationale = line.substring(line.indexOf(':') + 1).trim();
            data.value = extractScoreFromText(data.valueRationale, 8);
        } else if (lower.startsWith('disqualifiers:') || lower.startsWith('disqualifier:')) {
            data.disqRationale = line.substring(line.indexOf(':') + 1).trim();
            data.disq = extractScoreFromText(data.disqRationale, 10);
        } else if (lower.startsWith('risks:') || lower.startsWith('key risks:')) {
            data.risks = line.substring(line.indexOf(':') + 1).trim();
        }
    });

    return data;
}

// Helper to look for scoring hints or return a realistic baseline
function extractScoreFromText(text, fallback) {
    // Check if text mentions a score like "9/10" or "8 out of 10" or similar
    const scoreRegex = /(\d+)\s*\/\s*10/;
    const match = text.match(scoreRegex);
    if (match) {
        return parseInt(match[1]);
    }
    
    // Fallback based on sentiment
    const lower = text.toLowerCase();
    if (lower.includes('perfect') || lower.includes('closely aligns') || lower.includes('no disqualifiers')) {
        return 10;
    } else if (lower.includes('high') || lower.includes('fits well') || lower.includes('within range')) {
        return 9;
    } else if (lower.includes('moderate') || lower.includes('some risk') || lower.includes('unclear')) {
        return 7;
    } else if (lower.includes('not align') || lower.includes('mismatch') || lower.includes('disqualified')) {
        return 3;
    }

    return fallback;
}// Helper to format date offset in local timezone YYYY-MM-DD
function getLocalDateString(offsetDays = 0) {
    const d = new Date();
    d.setDate(d.getDate() - offsetDays);
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
}

// Shortcut to trigger tab programmatically
function triggerTab(tabId) {
    const tab = document.getElementById(tabId);
    if (tab) {
        tab.click();
    }
}

// Background scraper triggers
async function startEmailScrape() {
    try {
        // Disable buttons during scrape
        renderEmailBtn.disabled = true;
        refreshBtn.disabled = true;
        renderEmailBtn.classList.add('loading');
        
        const btnText = renderEmailBtn.querySelector('span');
        const originalText = btnText.textContent;
        btnText.textContent = 'Syncing Tenders...';
        
        // Show terminal console card
        if (consoleCard) {
            consoleCard.style.display = 'block';
            consoleLogBody.textContent = 'Initializing scraper pipeline...\n';
            consoleTitle.textContent = 'Live Scraper Terminal Output - Running...';
            const spinnerNode = consoleCard.querySelector('.spinner');
            if (spinnerNode) spinnerNode.style.display = 'inline-block';
        }
        
        // Show status in table loading area
        tableLoading.querySelector('p').textContent = "Running pipeline scraper & scoring tenders (takes about 30 seconds)...";
        tableLoading.style.display = 'flex';
        tendersTableBody.innerHTML = '';
        tableEmpty.style.display = 'none';

        // Trigger pipeline run
        const res = await fetch('/api/trigger-scrape');
        const data = await res.json();
        
        if (data.status === 'started') {
            let logPollInterval;
            
            // Poll scrape status every 1.5 seconds
            const interval = setInterval(async () => {
                const statusRes = await fetch('/api/scrape-status');
                const statusData = await statusRes.json();
                
                if (!statusData.running) {
                    clearInterval(interval);
                    clearInterval(logPollInterval);
                    
                    // Fetch final log
                    const finalLogRes = await fetch('/api/scrape-log');
                    const finalLogData = await finalLogRes.json();
                    if (consoleLogBody) {
                        consoleLogBody.textContent = finalLogData.log || 'No logs captured.';
                        consoleLogBody.scrollTop = consoleLogBody.scrollHeight;
                    }
                    
                    // Re-enable and reset buttons
                    renderEmailBtn.disabled = false;
                    refreshBtn.disabled = false;
                    renderEmailBtn.classList.remove('loading');
                    btnText.textContent = originalText;
                    
                    // Reset loading status text
                    tableLoading.querySelector('p').textContent = "Loading database records...";
                    
                    if (consoleCard) {
                        consoleTitle.textContent = 'Live Scraper Terminal Output - Completed';
                        const spinnerNode = consoleCard.querySelector('.spinner');
                        if (spinnerNode) spinnerNode.style.display = 'none';
                    }
                    
                    // Refresh data
                    loadStats();
                    loadTenders();
                    alert('Scraping and processing pipeline completed successfully!');
                }
            }, 1500);

            // Poll scrape log content every 1 second
            logPollInterval = setInterval(async () => {
                const logRes = await fetch('/api/scrape-log');
                const logData = await logRes.json();
                if (consoleLogBody && logData.log) {
                    consoleLogBody.textContent = logData.log;
                    // Auto-scroll to the bottom
                    consoleLogBody.scrollTop = consoleLogBody.scrollHeight;
                }
            }, 1000);
            
        } else {
            alert('Scraper is already running or failed to start: ' + data.message);
            renderEmailBtn.disabled = false;
            refreshBtn.disabled = false;
            renderEmailBtn.classList.remove('loading');
            btnText.textContent = originalText;
            tableLoading.style.display = 'none';
            if (consoleCard) consoleCard.style.display = 'none';
        }
    } catch (e) {
        console.error('Error starting scrape:', e);
        alert('Failed to connect to the scraper backend.');
        renderEmailBtn.disabled = false;
        refreshBtn.disabled = false;
        renderEmailBtn.classList.remove('loading');
        tableLoading.style.display = 'none';
        if (consoleCard) consoleCard.style.display = 'none';
    }
}


