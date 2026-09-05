// --- Global State ---
let activeSection = 'section-overview';
let activeCaseId = null;
let comparisonChart = null;
let failuresChart = null;

// --- Helper: Format currency to INR ---
function formatINR(paisa) {
    const rupees = (paisa / 100).toFixed(2);
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR'
    }).format(rupees);
}

// --- Helper: Show toast notification ---
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';

    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// --- Init & Tab Navigation ---
document.addEventListener('DOMContentLoaded', () => {
    // Nav Button listeners
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.content-section').forEach(s => s.classList.remove('active'));

            btn.classList.add('active');
            const target = btn.getAttribute('data-target');
            document.getElementById(target).classList.add('active');
            activeSection = target;

            // Update breadcrumb
            const sectionName = btn.innerText.trim();
            document.getElementById('breadcrumb-current').innerText = sectionName;

            // Load relevant section data
            if (target === 'section-overview') loadOverviewData();
            if (target === 'section-risk') loadRiskSection();
            if (target === 'section-analytics') loadAnalyticsSection();
            if (target === 'section-cases') loadCasesRegistry();
            if (target === 'section-actions') loadActionsSection();
            if (target === 'section-outcomes') loadOutcomesSection();
            if (target === 'section-ai-decisions') loadAIDecisionsSection();
            if (target === 'section-evaluation') loadEvaluationExplorer();
            if (target === 'section-audit') loadAuditLogsSection();
            if (target === 'section-policy') loadPolicySection();
            if (target === 'section-reliability') loadReliabilitySection();
        });
    });

    // Close Case Sidebar
    document.getElementById('btn-close-sidebar').addEventListener('click', closeCaseSidebar);

    // Sidebar Action Triggers
    document.getElementById('btn-trigger-ai').addEventListener('click', triggerAIStep);
    document.getElementById('btn-execute-action').addEventListener('click', triggerExecuteAction);
    document.getElementById('btn-verify-outcome').addEventListener('click', triggerVerifyOutcome);

    // Refresh Cases list
    document.getElementById('btn-refresh-cases').addEventListener('click', loadCasesRegistry);

    // Filters change triggers
    document.getElementById('filter-state').addEventListener('change', loadCasesRegistry);
    document.getElementById('filter-strategy').addEventListener('change', loadCasesRegistry);

    // Evaluation Form Submit
    document.getElementById('eval-run-form').addEventListener('submit', runNewEvaluation);

    // Initial Load
    loadOverviewData();
});

// --- SECTION 1: OVERVIEW METRICS & CHARTS ---
async function loadOverviewData() {
    try {
        const res = await fetch('/api/v1/dashboard/metrics?include_synthetic=true');
        if (!res.ok) throw new Error("Failed to load dashboard metrics");
        const data = await res.json();

        // Populate metrics card text values
        document.getElementById('kpi-rev-at-risk').innerText = formatINR(data.total_revenue_at_risk);
        document.getElementById('kpi-rev-recovered').innerText = formatINR(data.total_recovered_revenue);
        document.getElementById('kpi-recovery-rate').innerText = `${(data.recovery_rate * 100).toFixed(2)}%`;
        document.getElementById('kpi-active-cases').innerText = data.active_cases;
        document.getElementById('kpi-cases-at-risk').innerText = `${data.active_cases} active cases at risk`;
        document.getElementById('kpi-resolved-cases').innerText = `${data.recovered_cases} cases resolved successfully`;
        document.getElementById('kpi-uplift-rate').innerText = `Uplift vs Baseline: ${(data.uplift * 100).toFixed(1)}%`;

        // Load all cases to build failure reasons doughnut dynamically (No fabrication)
        const casesRes = await fetch('/api/v1/cases/?limit=200');
        let casesData = [];
        if (casesRes.ok) {
            casesData = await casesRes.json();
        }

        // Render charts
        renderOverviewCharts(data, casesData);
    } catch (err) {
        showToast(err.message, 'error');
    }
}

function renderOverviewCharts(data, casesData) {
    const ctxComp = document.getElementById('chart-comparison').getContext('2d');
    const ctxFail = document.getElementById('chart-failures').getContext('2d');

    // 1. AI vs Baseline rates comparison chart
    if (comparisonChart) comparisonChart.destroy();
    comparisonChart = new Chart(ctxComp, {
        type: 'bar',
        data: {
            labels: ['BASELINE Strategy', 'AI Strategy'],
            datasets: [{
                label: 'Recovery Rate (%)',
                data: [data.baseline_group.recovery_rate * 100, data.ai_group.recovery_rate * 100],
                backgroundColor: ['#64748b', '#1b59f8'],
                borderColor: ['rgba(100, 116, 139, 0.2)', 'rgba(27, 89, 248, 0.2)'],
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(226, 232, 240, 0.6)' }
                },
                y: {
                    min: 0,
                    max: 100,
                    ticks: { callback: v => `${v}%` },
                    grid: { color: 'rgba(226, 232, 240, 0.6)' }
                }
            }
        }
    });

    // 2. Dynamic payment failure distribution breakdown
    const reasonsMap = {};
    casesData.forEach(c => {
        const reason = c.failure_reason || 'unknown';
        reasonsMap[reason] = (reasonsMap[reason] || 0) + 1;
    });

    const labels = Object.keys(reasonsMap);
    const chartData = Object.values(reasonsMap);

    if (failuresChart) failuresChart.destroy();
    failuresChart = new Chart(ctxFail, {
        type: 'doughnut',
        data: {
            labels: labels.length > 0 ? labels : ['No Data'],
            datasets: [{
                data: chartData.length > 0 ? chartData : [1],
                backgroundColor: ['#1b59f8', '#ef4444', '#f59e0b', '#10b981', '#64748b'],
                borderWidth: 2,
                borderColor: '#ffffff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: {
                        color: '#334155',
                        font: { family: 'Inter', size: 11 }
                    }
                }
            }
        }
    });
}

// --- SECTION 2: ACTIVE CASES REGISTRY ---
async function loadCasesRegistry() {
    const stateFilter = document.getElementById('filter-state').value;
    const strategyFilter = document.getElementById('filter-strategy').value;

    const tbody = document.getElementById('cases-list-body');
    tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">Refreshing cases registry...</td></tr>`;

    try {
        let url = `/api/v1/cases/?limit=100`;
        if (stateFilter) url += `&state=${stateFilter}`;
        if (strategyFilter) url += `&strategy_group=${strategyFilter}`;

        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to load cases");
        const cases = await res.json();

        if (cases.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">No cases matching active filters were found.</td></tr>`;
            return;
        }

        tbody.innerHTML = '';
        cases.forEach(c => {
            const tr = document.createElement('tr');
            
            const badgeClass = c.recovery_strategy_group === 'AI' ? 'badge-blue' : 'badge-gray';
            
            let stateBadgeClass = 'badge-gray';
            if (c.current_state === 'RECOVERED') {
                stateBadgeClass = 'badge-green';
            } else if (c.current_state === 'STOPPED') {
                stateBadgeClass = 'badge-red';
            } else if (c.current_state === 'ACTION_PROPOSED') {
                stateBadgeClass = 'badge-blue';
            } else if (c.current_state === 'ACTION_EXECUTED') {
                stateBadgeClass = 'badge-amber';
            }

            tr.innerHTML = `
                <td><strong>#${c.id}</strong></td>
                <td><span class="badge ${badgeClass}">${c.recovery_strategy_group}</span></td>
                <td>${formatINR(c.amount_at_risk)}</td>
                <td><code style="font-size:12px; color: var(--text-secondary);">${c.failure_reason || 'unknown'}</code></td>
                <td><span class="badge ${stateBadgeClass}">${c.current_state}</span></td>
                <td>${c.recovery_attempts} / ${c.max_attempts}</td>
                <td>${c.prioritization_score.toFixed(1)}</td>
                <td>
                    <button class="btn btn-secondary btn-sm btn-view-case" data-id="${c.id}">View Journal</button>
                </td>
            `;

            tr.querySelector('.btn-view-case').addEventListener('click', (e) => {
                e.stopPropagation();
                openCaseSidebar(c.id);
            });
            tr.addEventListener('click', () => openCaseSidebar(c.id));
            tbody.appendChild(tr);
        });

    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 3: CASE LIFECYCLE JOURNAL OVERLAY ---
async function openCaseSidebar(caseId) {
    activeCaseId = caseId;
    document.getElementById('sidebar-case-id').innerText = `Case #${caseId}`;
    document.getElementById('case-sidebar').classList.add('active');

    await fetchAndPopulateCaseDetails();
}

function closeCaseSidebar() {
    document.getElementById('case-sidebar').classList.remove('active');
    activeCaseId = null;
}

async function fetchAndPopulateCaseDetails() {
    if (!activeCaseId) return;

    try {
        const res = await fetch(`/api/v1/cases/${activeCaseId}`);
        if (!res.ok) throw new Error("Failed to load case details");
        const data = await res.json();

        // Header strategy + state
        const stratBadge = document.getElementById('sidebar-strategy-badge');
        stratBadge.innerText = data.recovery_strategy_group;
        stratBadge.className = `badge ${data.recovery_strategy_group === 'AI' ? 'badge-blue' : 'badge-gray'}`;

        const stateBadge = document.getElementById('sidebar-state-badge');
        stateBadge.innerText = data.current_state;
        
        let stateClass = 'badge-gray';
        if (data.current_state === 'RECOVERED') {
            stateClass = 'badge-green';
        } else if (data.current_state === 'STOPPED') {
            stateClass = 'badge-red';
        } else if (data.current_state === 'ACTION_PROPOSED') {
            stateClass = 'badge-blue';
        } else if (data.current_state === 'ACTION_EXECUTED') {
            stateClass = 'badge-amber';
        }
        stateBadge.className = `badge ${stateClass}`;

        // Specs demographics
        document.getElementById('spec-amount').innerText = formatINR(data.amount_at_risk);
        
        // Display Payment ID with link to Razorpay dashboard
        const paymentIdEl = document.getElementById('spec-payment-id');
        if (paymentIdEl && data.payment_id) {
            paymentIdEl.innerHTML = `<a href="https://dashboard.razorpay.com/app/payments/${data.payment_id}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-primary); text-decoration:none; font-family:monospace; font-size:11px;">${data.payment_id} ↗</a>`;
        }
        
        document.getElementById('spec-method').innerText = data.payment?.method || 'unknown';
        document.getElementById('spec-reason').innerText = data.failure_reason || 'unknown';
        document.getElementById('spec-risk').innerText = data.loss_risk_score.toFixed(2);
        document.getElementById('spec-priority').innerText = data.prioritization_score.toFixed(1);

        const hasConsent = data.customer?.is_subscribed === true;
        document.getElementById('spec-consent').innerHTML = hasConsent 
            ? '<span class="badge badge-green" style="font-size:11px; padding:2px 6px;">YES (Active Autopay)</span>'
            : '<span class="badge badge-gray" style="font-size:11px; padding:2px 6px;">NO (Not Subscribed)</span>';

        // Buttons state management
        // PENDING = AI/baseline proposed, awaiting execute
        // SCHEDULED = baseline PAYMENT_LINK/REMINDER, also awaiting user-triggered execute
        const hasPending = data.recovery_actions.some(a => a.status === 'PENDING' || a.status === 'SCHEDULED');
        const hasExecuted = data.recovery_actions.some(a => a.status === 'EXECUTED');
        const isTerminal = ['RECOVERED', 'STOPPED', 'NOT_RECOVERED'].includes(data.current_state);

        const triggerBtn = document.getElementById('btn-trigger-ai');
        if (data.recovery_strategy_group === 'BASELINE') {
            triggerBtn.innerText = "Trigger Baseline Step";
            triggerBtn.className = "btn btn-secondary btn-sm";
        } else {
            triggerBtn.innerText = "Trigger AI Step";
            triggerBtn.className = "btn btn-primary btn-sm";
        }

        triggerBtn.disabled = isTerminal || hasPending;
        document.getElementById('btn-execute-action').disabled = isTerminal || !hasPending;
        document.getElementById('btn-verify-outcome').disabled = isTerminal || !hasExecuted;

        // Render AI Decisions panel
        const aiBox = document.getElementById('sidebar-ai-box');
        if (data.ai_decisions && data.ai_decisions.length > 0) {
            const dec = data.ai_decisions[data.ai_decisions.length - 1];
            const metadata = dec.raw_decision_output?.metadata_json || {};
            const providerName = (metadata.provider || 'mock').toUpperCase();
            
            let sourceBadgeHTML = '';
            if (providerName === 'GEMINI') {
                sourceBadgeHTML = `<span class="badge badge-blue" style="font-size:10px; font-weight:600; padding:2px 6px;">🧠 LIVE GEMINI</span>`;
            } else if (providerName === 'FALLBACK') {
                sourceBadgeHTML = `<span class="badge badge-amber" style="font-size:10px; font-weight:600; padding:2px 6px;">⚠️ BASELINE FALLBACK</span>`;
            } else {
                sourceBadgeHTML = `<span class="badge badge-gray" style="font-size:10px; font-weight:600; padding:2px 6px;">🤖 MOCK PROVIDER</span>`;
            }

            aiBox.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <span style="font-size:11px; font-weight:bold; color:var(--text-secondary); text-transform:uppercase;">Decision Source</span>
                    ${sourceBadgeHTML}
                </div>
                <div class="kv-list">
                    <div class="kv-item"><span class="kv-label">Action Proposed:</span><span class="kv-val">${dec.recommended_action}</span></div>
                    <div class="kv-item"><span class="kv-label">AI Confidence:</span><span class="kv-val">${(dec.confidence * 100).toFixed(0)}%</span></div>
                    <div class="kv-item"><span class="kv-label">Expected Prob:</span><span class="kv-val">${(dec.expected_recovery_probability * 100).toFixed(0)}%</span></div>
                </div>
                <div class="ai-explanation" style="margin-top:10px; font-size:12px;">
                    <strong>Explanation:</strong>
                    <p style="margin-top:4px; color: var(--text-secondary);">${dec.reason || 'No explanation provided.'}</p>
                </div>
                <div style="font-size:10px; color:var(--text-muted); margin-top:8px;">ID: #${dec.id} | Prompt version: ${metadata.prompt_version || '1.0'} | fallback: ${providerName === 'FALLBACK'}</div>
            `;
        } else {
            aiBox.innerHTML = `<span class="text-muted" style="font-size:12px;">No AI decision proposed yet. Click Trigger AI Step.</span>`;
        }

        // Render Policy & Execution panel
        const policyBox = document.getElementById('sidebar-policy-box');
        const lastAction = data.recovery_actions.length > 0 ? data.recovery_actions[data.recovery_actions.length - 1] : null;
        if (lastAction) {
            let policyResultText = "PENDING";
            let policyBadgeClass = "badge-gray";

            if (lastAction.status === 'EXECUTED') {
                policyResultText = "APPROVED";
                policyBadgeClass = "badge-green";
            } else if (lastAction.status === 'SCHEDULED') {
                policyResultText = "READY TO EXECUTE";
                policyBadgeClass = "badge-blue";
            } else if (lastAction.status === 'BLOCKED') {
                policyResultText = "BLOCKED";
                policyBadgeClass = "badge-red";
            } else if (lastAction.status === 'FAILED') {
                policyResultText = "FAILED";
                policyBadgeClass = "badge-red";
            }

            // Build reason/info block for BLOCKED or FAILED actions
            let reasonHTML = '';
            if (lastAction.parameters) {
                const blockReason = lastAction.parameters.block_reason || lastAction.parameters.failure_reason;
                if (blockReason) {
                    const isConsentBlock = blockReason === 'RETRIES_NOT_SUPPORTED_WITHOUT_RECURRING_CONSENT';
                    reasonHTML = `
                        <div style="margin-top:10px; padding:10px; background:rgba(239,68,68,0.05); border:1px solid rgba(239,68,68,0.2); border-radius:6px;">
                            <div style="font-size:10px; font-weight:700; color:var(--accent-red); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;">
                                ${lastAction.status === 'BLOCKED' ? '🚫 Policy Block' : '⛔ Execution Failed'}
                            </div>
                            <div style="font-size:11px; color:var(--text-secondary);">${blockReason}</div>
                            ${isConsentBlock ? `<div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Direct retries require customer recurring autopay consent (subscription). Send a payment link instead.</div>` : ''}
                        </div>
                    `;
                }
            }

            // Build Razorpay Payment Link section
            let paymentLinkHTML = '';
            if (lastAction.action_type === 'PAYMENT_LINK') {
                const params = lastAction.parameters || {};
                if (params.payment_link_url) {
                    paymentLinkHTML = `
                        <div style="margin-top:14px; padding:12px; background:rgba(0,82,204,0.04); border:1px solid rgba(0,82,204,0.2); border-radius:6px;">
                            <div style="font-size:10px; font-weight:700; color:var(--accent-primary); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:10px;">💳 Razorpay Payment</div>
                            <div class="kv-list" style="margin-bottom:10px;">
                                <div class="kv-item"><span class="kv-label">Provider:</span><span class="kv-val">Razorpay</span></div>
                                <div class="kv-item"><span class="kv-label">Mode:</span><span class="badge badge-amber" style="font-size:10px;">TEST</span></div>
                                <div class="kv-item"><span class="kv-label">Link ID:</span><span class="kv-val" style="font-family:monospace; font-size:11px;">${params.payment_link_id || 'n/a'}</span></div>
                                <div class="kv-item"><span class="kv-label">Link Status:</span><span class="kv-val">${params.status || 'created'}</span></div>
                            </div>
                            <a href="${params.payment_link_url}" target="_blank" rel="noopener noreferrer"
                               style="display:inline-flex; align-items:center; gap:6px; background:var(--accent-primary); color:#fff;
                                      text-decoration:none; padding:7px 14px; border-radius:5px; font-size:12px; font-weight:600;">
                                🔗 Open Test Payment Page
                            </a>
                        </div>
                    `;
                } else if (lastAction.status === 'FAILED') {
                    // PAYMENT_LINK type but no URL — execution failed
                    const failReason = (lastAction.parameters || {}).failure_reason || 'UNKNOWN_ERROR';
                    paymentLinkHTML = `
                        <div style="margin-top:14px; padding:10px; background:rgba(239,68,68,0.05); border:1px solid rgba(239,68,68,0.2); border-radius:6px;">
                            <div style="font-size:10px; font-weight:700; color:var(--accent-red); text-transform:uppercase; margin-bottom:4px;">⛔ Payment link not generated</div>
                            <div style="font-size:11px; color:var(--text-secondary);">Reason: ${failReason}</div>
                        </div>
                    `;
                } else {
                    // Pending or unknown — no URL yet
                    paymentLinkHTML = `
                        <div style="margin-top:14px; padding:10px; background:var(--bg-app); border:1px dashed var(--border-color); border-radius:6px;">
                            <div style="font-size:11px; color:var(--text-muted);">⏳ Payment link pending — click Execute Action to generate it.</div>
                        </div>
                    `;
                }
            }

            policyBox.innerHTML = `
                <div class="kv-list">
                    <div class="kv-item"><span class="kv-label">Action Type:</span><span class="kv-val">${lastAction.action_type}</span></div>
                    <div class="kv-item"><span class="kv-label">Status:</span><span class="kv-val">${lastAction.status}</span></div>
                    <div class="kv-item"><span class="kv-label">Policy Result:</span><span class="badge ${policyBadgeClass}">${policyResultText}</span></div>
                </div>
                ${reasonHTML}
                ${paymentLinkHTML}
            `;
        } else {
            policyBox.innerHTML = `<span class="text-muted" style="font-size:12px;">No actions validated/executed yet.</span>`;
        }

        // Render Outcomes
        const outcomeBox = document.getElementById('sidebar-outcome-box');
        if (data.outcomes && data.outcomes.length > 0) {
            const out = data.outcomes[data.outcomes.length - 1];
            
            let sourceBadge = 'badge-gray';
            if (out.verification_source === 'WEBHOOK' || out.verification_source === 'API_CHECK') {
                sourceBadge = 'badge-blue';
            } else if (out.verification_source === 'OFFLINE_SIMULATION') {
                sourceBadge = 'badge-amber';
            }

            let evidenceHTML = '';
            if (out.is_recovered && (out.verification_source === 'WEBHOOK' || out.verification_source === 'API_CHECK')) {
                const raw = out.raw_verification_data || {};
                evidenceHTML = `
                    <div class="evidence-checklist">
                        <div style="font-size: 11px; font-weight: bold; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px;">Razorpay Verification Evidence</div>
                        <div class="kv-list" style="margin-bottom: 8px;">
                            <div class="kv-item"><span class="kv-label">Provider:</span><span class="kv-val">Razorpay</span></div>
                            <div class="kv-item"><span class="kv-label">Verification Source:</span><span class="badge ${sourceBadge}">${out.verification_source}</span></div>
                            <div class="kv-item"><span class="kv-label">Payment Link ID:</span><span class="kv-val" style="font-family: monospace; font-size: 11px;">${raw.payment_link_id || 'n/a'}</span></div>
                            <div class="kv-item"><span class="kv-label">Payment ID:</span><span class="kv-val" style="font-family: monospace; font-size: 11px;">${raw.payment_id || 'n/a'}</span></div>
                            <div class="kv-item"><span class="kv-label">Amount:</span><span class="kv-val">${formatINR(out.recovered_amount)}</span></div>
                            <div class="kv-item"><span class="kv-label">Currency:</span><span class="kv-val">${raw.currency || 'INR'}</span></div>
                            <div class="kv-item"><span class="kv-label">Status:</span><span class="badge badge-green">RECOVERED</span></div>
                        </div>
                        <div class="evidence-item success">✓ Amount matched</div>
                        <div class="evidence-item success">✓ Currency matched</div>
                        <div class="evidence-item success">✓ Razorpay evidence verified</div>
                    </div>
                `;
            }

            outcomeBox.innerHTML = `
                <div class="kv-list">
                    <div class="kv-item"><span class="kv-label">Outcome:</span><span class="badge ${out.is_recovered ? 'badge-green' : 'badge-red'}">${out.is_recovered ? 'RECOVERED' : 'FAILED'}</span></div>
                    <div class="kv-item"><span class="kv-label">Verification:</span><span class="badge ${sourceBadge}">${out.verification_source}</span></div>
                    <div class="kv-item"><span class="kv-label">Recovered Amount:</span><span class="kv-val">${formatINR(out.recovered_amount)}</span></div>
                </div>
                ${evidenceHTML}
            `;
        } else {
            outcomeBox.innerHTML = `<span class="text-muted" style="font-size:12px;">No outcomes resolved. Click Verify Outcome.</span>`;
        }

        // Timeline Audit Logs
        const timeline = document.getElementById('sidebar-timeline');
        timeline.innerHTML = '';
        data.audit_logs.forEach(log => {
            const li = document.createElement('li');
            li.className = 'timeline-event completed';
            li.innerHTML = `
                <span class="timeline-time">${new Date(log.timestamp).toLocaleTimeString()}</span>
                <span class="timeline-title">${log.event_name}</span>
                <span class="timeline-desc">${log.description}</span>
            `;
            timeline.appendChild(li);
        });

    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- Interactive Case Operations ---
async function triggerAIStep() {
    if (!activeCaseId) return;
    try {
        const strat = document.getElementById('sidebar-strategy-badge').innerText;
        const endpoint = strat === 'BASELINE' ? 'baseline-step' : 'ai-step';
        const res = await fetch(`/api/v1/cases/${activeCaseId}/${endpoint}`, { method: 'POST' });
        if (!res.ok) throw new Error(`Failed to trigger ${strat.toLowerCase()} step`);
        const resData = await res.json();
        const proposedAction = resData.action_proposed || resData.action_executed || 'NONE';
        showToast(`${strat} step executed: proposed ${proposedAction}`, "success");
        await fetchAndPopulateCaseDetails();
        loadCasesRegistry();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function triggerExecuteAction() {
    if (!activeCaseId) return;
    try {
        const res = await fetch(`/api/v1/cases/${activeCaseId}/execute-pending`, { method: 'POST' });
        if (!res.ok) throw new Error("Failed to execute pending action");
        const resData = await res.json();
        
        if (resData.status === 'success') {
            showToast(`Policy approved. Action executed: ${resData.action_executed}`, "success");
        } else if (resData.status === 'fallback_success') {
            showToast(`Policy BLOCKED. Falling back to: ${resData.action_executed}`, "success");
        } else {
            showToast(`Policy BLOCKED. Action stopped: ${resData.original_block_reason || 'Unknown'}`, "error");
        }
        
        await fetchAndPopulateCaseDetails();
        loadCasesRegistry();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function triggerVerifyOutcome() {
    if (!activeCaseId) return;
    try {
        const res = await fetch(`/api/v1/cases/${activeCaseId}/verify-outcome`, { method: 'POST' });
        if (!res.ok) throw new Error("Failed to verify outcome");
        const resData = await res.json();
        
        if (resData.is_resolved && resData.is_recovered) {
            showToast(`Outcome verified: ${formatINR(resData.recovered_amount)} recovered`, "success");
        } else if (resData.is_resolved) {
            showToast("Case outcome verification completed: Recovery failed.", "error");
        } else {
            showToast("Payment verification is still pending or status is non-terminal.", "info");
        }
        
        await fetchAndPopulateCaseDetails();
        loadCasesRegistry();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 4: EVALUATION EXPLORER PANEL ---
async function loadEvaluationExplorer() {
    await loadEvaluationRunsList();
}

async function loadEvaluationRunsList() {
    const container = document.getElementById('eval-runs-container');
    container.innerHTML = `<span class="text-muted" style="font-size:12px;">Loading runs registry...</span>`;

    try {
        const res = await fetch('/api/v1/evaluation');
        if (!res.ok) throw new Error("Failed to fetch evaluation runs");
        const runs = await res.json();

        if (runs.length === 0) {
            container.innerHTML = `<span class="text-muted" style="font-size:12px;">No offline evaluation runs found. Submit the form above to run one.</span>`;
            return;
        }

        container.innerHTML = '';
        runs.forEach(r => {
            const div = document.createElement('div');
            div.className = 'run-list-item';
            div.innerHTML = `
                <h5>${r.name}</h5>
                <p>Seed: ${r.random_seed} | Date: ${new Date(r.created_at).toLocaleDateString()}</p>
            `;
            div.addEventListener('click', () => {
                document.querySelectorAll('.run-list-item').forEach(el => el.classList.remove('active'));
                div.classList.add('active');
                viewEvaluationDetails(r.id);
            });
            container.appendChild(div);
        });

    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function viewEvaluationDetails(runId) {
    try {
        const res = await fetch(`/api/v1/evaluation/${runId}`);
        if (!res.ok) throw new Error("Failed to load run details");
        const data = await res.json();

        // Display results block
        document.getElementById('eval-results-container').querySelector('.card-empty-state').classList.add('hidden');
        document.getElementById('eval-results-content').classList.remove('hidden');

        // Map metrics
        const aiMetrics = data.metrics.find(m => m.strategy_group === 'AI');
        const baseMetrics = data.metrics.find(m => m.strategy_group === 'BASELINE');

        document.getElementById('eval-ai-rate').innerText = `${(aiMetrics.recovery_rate * 100).toFixed(1)}%`;
        document.getElementById('eval-base-rate').innerText = `${(baseMetrics.recovery_rate * 100).toFixed(1)}%`;

        const diff = (aiMetrics.recovery_rate - baseMetrics.recovery_rate) * 100;
        document.getElementById('eval-diff-rate').innerText = `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}%`;

        const paired = aiMetrics.paired_metrics || {};
        document.getElementById('eval-p-value').innerText = paired.p_value != null ? paired.p_value.toFixed(4) : '--';

        // Map paired matrix
        document.getElementById('matrix-both').innerText = paired.both_recovered || 0;
        document.getElementById('matrix-ai-only').innerText = paired.ai_only_recovered || 0;
        document.getElementById('matrix-base-only').innerText = paired.baseline_only_recovered || 0;
        document.getElementById('matrix-neither').innerText = paired.neither_recovered || 0;

        // Interpret significance
        const interp = document.getElementById('eval-interpretation');
        const isSig = paired.p_value < 0.05;
        interp.innerHTML = `
            <strong>Statistical Summary:</strong><br>
            Comparison shows an incremental recovered revenue of <strong>${formatINR(aiMetrics.total_recovered_revenue - baseMetrics.total_recovered_revenue)}</strong>.
            The paired difference p-value is <strong>${paired.p_value != null ? paired.p_value.toFixed(4) : '--'}</strong>.
            ${isSig ? 'The recovery difference is <strong style="color:var(--accent-green)">statistically significant</strong> (p < 0.05).' : 'The recovery rate difference is <strong style="color:var(--accent-amber)">not statistically significant</strong>.'}
            ${paired.statistical_warning ? '<br><strong style="color:var(--accent-amber)">⚠️ Statistical Warning:</strong> Mismatched pair count is small. Fisher exact / Exact Binomial fell back to guard against McNemar approximation errors.' : ''}
        `;

    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function runNewEvaluation(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-run-eval');
    btn.disabled = true;
    btn.innerText = 'Simulating run...';

    const payload = {
        name: document.getElementById('eval-name').value,
        random_seed: parseInt(document.getElementById('eval-seed').value),
        sample_size: parseInt(document.getElementById('eval-size').value)
    };

    try {
        const res = await fetch('/api/v1/evaluation/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error("Simulation execution failed");
        
        const data = await res.json();
        showToast("Offline matched-pair simulation completed successfully", "success");
        await loadEvaluationRunsList();
        
        // Auto select newly created run
        if (data.evaluation_id) {
            viewEvaluationDetails(data.evaluation_id);
        }
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = 'Execute Simulation';
    }
}

// --- SECTION 4: POLICY & SAFETY ---
function loadPolicySection() {
    // Static content section - no dynamic loading needed
    // All policy and safety information is already rendered in HTML
}

// --- SECTION 5: RELIABILITY ---
function loadReliabilitySection() {
    // Static content section - no dynamic loading needed
    // All reliability information is already rendered in HTML
}

// --- SECTION 1B: REVENUE RISK ---
async function loadRiskSection() {
    try {
        const res = await fetch('/api/v1/cases/?limit=200');
        if (!res.ok) throw new Error("Failed to load risk data");
        const cases = await res.json();
        
        // Load high-risk cases (risk score > 0.7)
        const highRiskCases = cases.filter(c => c.loss_risk_score > 0.7).slice(0, 10);
        const tbody = document.getElementById('risk-cases-body');
        
        if (highRiskCases.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No high-risk cases found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        highRiskCases.forEach(c => {
            const daysSince = Math.floor((Date.now() - new Date(c.created_at).getTime()) / (1000 * 60 * 60 * 24));
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>#${c.id}</strong></td>
                <td>${formatINR(c.amount_at_risk)}</td>
                <td><span class="badge badge-red">${c.loss_risk_score.toFixed(2)}</span></td>
                <td><code style="font-size:12px;">${c.failure_reason || 'unknown'}</code></td>
                <td>${daysSince} days</td>
                <td><button class="btn btn-primary btn-sm btn-view-case" data-id="${c.id}">Review</button></td>
            `;
            tr.querySelector('.btn-view-case').addEventListener('click', () => openCaseSidebar(c.id));
            tbody.appendChild(tr);
        });
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 1C: ANALYTICS ---
async function loadAnalyticsSection() {
    try {
        const res = await fetch('/api/v1/dashboard/metrics?include_synthetic=true');
        if (!res.ok) throw new Error("Failed to load analytics");
        const data = await res.json();
        
        document.getElementById('analytics-ai-rate').innerText = `${(data.ai_group.recovery_rate * 100).toFixed(1)}%`;
        document.getElementById('analytics-ai-cases').innerText = `${data.ai_group.total_cases} cases`;
        document.getElementById('analytics-base-rate').innerText = `${(data.baseline_group.recovery_rate * 100).toFixed(1)}%`;
        document.getElementById('analytics-base-cases').innerText = `${data.baseline_group.total_cases} cases`;
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 2B: RECOVERY ACTIONS ---
async function loadActionsSection() {
    try {
        // Load all cases to aggregate actions
        const res = await fetch('/api/v1/cases/?limit=200');
        if (!res.ok) throw new Error("Failed to load actions");
        const cases = await res.json();
        
        let allActions = [];
        cases.forEach(c => {
            if (c.recovery_actions) {
                c.recovery_actions.forEach(a => {
                    allActions.push({ ...a, case_id: c.id });
                });
            }
        });
        
        // Update KPIs
        document.getElementById('actions-total').innerText = allActions.length;
        document.getElementById('actions-executed').innerText = allActions.filter(a => a.status === 'EXECUTED').length;
        document.getElementById('actions-blocked').innerText = allActions.filter(a => a.status === 'BLOCKED').length;
        
        // Populate table (show last 50)
        const tbody = document.getElementById('actions-list-body');
        if (allActions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No actions found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        allActions.slice(0, 50).forEach(a => {
            const tr = document.createElement('tr');
            let statusBadge = 'badge-gray';
            if (a.status === 'EXECUTED') statusBadge = 'badge-green';
            if (a.status === 'BLOCKED') statusBadge = 'badge-red';
            if (a.status === 'FAILED') statusBadge = 'badge-red';
            if (a.status === 'PENDING') statusBadge = 'badge-blue';
            
            tr.innerHTML = `
                <td><strong>#${a.id}</strong></td>
                <td><a href="#" class="link-case" data-id="${a.case_id}">#${a.case_id}</a></td>
                <td>${a.action_type}</td>
                <td><span class="badge ${statusBadge}">${a.status}</span></td>
                <td>${a.proposed_by}</td>
                <td>${a.executed_at ? new Date(a.executed_at).toLocaleString() : '--'}</td>
                <td><button class="btn btn-secondary btn-sm" onclick="openCaseSidebar(${a.case_id})">View</button></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 2C: RECOVERY OUTCOMES ---
async function loadOutcomesSection() {
    try {
        const res = await fetch('/api/v1/cases/?limit=200');
        if (!res.ok) throw new Error("Failed to load outcomes");
        const cases = await res.json();
        
        let allOutcomes = [];
        cases.forEach(c => {
            if (c.outcomes) {
                c.outcomes.forEach(o => {
                    allOutcomes.push({ ...o, case_id: c.id });
                });
            }
        });
        
        // Update KPIs
        document.getElementById('outcomes-total').innerText = allOutcomes.length;
        document.getElementById('outcomes-recovered').innerText = allOutcomes.filter(o => o.is_recovered).length;
        document.getElementById('outcomes-failed').innerText = allOutcomes.filter(o => !o.is_recovered).length;
        
        // Populate table
        const tbody = document.getElementById('outcomes-list-body');
        if (allOutcomes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No outcomes found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        allOutcomes.slice(0, 50).forEach(o => {
            const tr = document.createElement('tr');
            const resultBadge = o.is_recovered ? 'badge-green' : 'badge-red';
            let sourceBadge = 'badge-gray';
            if (o.verification_source === 'WEBHOOK' || o.verification_source === 'API_CHECK') {
                sourceBadge = 'badge-blue';
            } else if (o.verification_source === 'OFFLINE_SIMULATION') {
                sourceBadge = 'badge-amber';
            }
            
            tr.innerHTML = `
                <td><strong>#${o.id}</strong></td>
                <td><a href="#" class="link-case" data-id="${o.case_id}">#${o.case_id}</a></td>
                <td><span class="badge ${resultBadge}">${o.is_recovered ? 'RECOVERED' : 'FAILED'}</span></td>
                <td>${formatINR(o.recovered_amount)}</td>
                <td><span class="badge ${sourceBadge}">${o.verification_source}</span></td>
                <td>${new Date(o.verified_at).toLocaleString()}</td>
                <td><button class="btn btn-secondary btn-sm" onclick="openCaseSidebar(${o.case_id})">View</button></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 3A: AI DECISIONS ---
async function loadAIDecisionsSection() {
    try {
        const res = await fetch('/api/v1/cases/?limit=200');
        if (!res.ok) throw new Error("Failed to load AI decisions");
        const cases = await res.json();
        
        let allDecisions = [];
        cases.forEach(c => {
            if (c.ai_decisions) {
                c.ai_decisions.forEach(d => {
                    allDecisions.push({ ...d, case_id: c.id });
                });
            }
        });
        
        // Update KPIs
        document.getElementById('ai-decisions-total').innerText = allDecisions.length;
        
        const geminiCount = allDecisions.filter(d => {
            const provider = d.raw_decision_output?.metadata_json?.provider || 'mock';
            return provider.toLowerCase() === 'gemini';
        }).length;
        document.getElementById('ai-decisions-gemini').innerText = geminiCount;
        
        const fallbackCount = allDecisions.filter(d => {
            const provider = d.raw_decision_output?.metadata_json?.provider || 'mock';
            return provider.toLowerCase() === 'fallback';
        }).length;
        document.getElementById('ai-decisions-fallback').innerText = fallbackCount;
        
        const avgConfidence = allDecisions.length > 0 
            ? (allDecisions.reduce((sum, d) => sum + d.confidence, 0) / allDecisions.length * 100).toFixed(0)
            : 0;
        document.getElementById('ai-decisions-confidence').innerText = `${avgConfidence}%`;
        
        // Populate table
        const tbody = document.getElementById('ai-decisions-list-body');
        if (allDecisions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No AI decisions found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        allDecisions.slice(0, 50).forEach(d => {
            const tr = document.createElement('tr');
            const provider = d.raw_decision_output?.metadata_json?.provider || 'mock';
            let providerBadge = 'badge-gray';
            let providerText = provider.toUpperCase();
            if (provider.toLowerCase() === 'gemini') {
                providerBadge = 'badge-blue';
                providerText = '🧠 GEMINI';
            } else if (provider.toLowerCase() === 'fallback') {
                providerBadge = 'badge-amber';
                providerText = '⚠️ FALLBACK';
            }
            
            tr.innerHTML = `
                <td><strong>#${d.id}</strong></td>
                <td><a href="#" class="link-case" data-id="${d.case_id}">#${d.case_id}</a></td>
                <td><span class="badge ${providerBadge}">${providerText}</span></td>
                <td>${d.recommended_action}</td>
                <td>${(d.confidence * 100).toFixed(0)}%</td>
                <td>${(d.expected_recovery_probability * 100).toFixed(0)}%</td>
                <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${d.reason || '--'}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- SECTION 4A: AUDIT LOGS ---
async function loadAuditLogsSection() {
    try {
        const res = await fetch('/api/v1/cases/?limit=100');
        if (!res.ok) throw new Error("Failed to load audit logs");
        const cases = await res.json();
        
        let allLogs = [];
        cases.forEach(c => {
            if (c.audit_logs) {
                c.audit_logs.forEach(log => {
                    allLogs.push({ ...log, case_id: c.id });
                });
            }
        });
        
        // Sort by timestamp desc
        allLogs.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        
        // Populate table
        const tbody = document.getElementById('audit-logs-body');
        if (allLogs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No audit logs found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        allLogs.slice(0, 100).forEach(log => {
            const tr = document.createElement('tr');
            let statusBadge = 'badge-gray';
            if (log.status === 'success') statusBadge = 'badge-green';
            if (log.status === 'failed') statusBadge = 'badge-red';
            
            tr.innerHTML = `
                <td>${new Date(log.timestamp).toLocaleString()}</td>
                <td><a href="#" class="link-case" data-id="${log.case_id}">#${log.case_id}</a></td>
                <td>${log.event_name}</td>
                <td><code style="font-size:11px;">${log.component}</code></td>
                <td><span class="badge ${statusBadge}">${log.status || 'info'}</span></td>
                <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${log.description}</td>
            `;
            tbody.appendChild(tr);
        });
        
        // Add refresh handler
        document.getElementById('btn-refresh-audit').addEventListener('click', loadAuditLogsSection);
    } catch (err) {
        showToast(err.message, 'error');
    }
}
