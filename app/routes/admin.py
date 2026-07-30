"""Browser-based admin dashboard shell."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard() -> str:
    """Serve a static admin dashboard backed by protected JSON APIs."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LeadTriage Admin</title>
    <style>
        :root {
            color-scheme: light;
            --bg: #f4f6f8;
            --panel: #ffffff;
            --border: #d9e0e7;
            --text: #17202a;
            --muted: #667085;
            --accent: #2563eb;
            --accent-dark: #1d4ed8;
            --danger: #b42318;
            --warning: #b54708;
            --success: #067647;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            background: var(--bg);
            color: var(--text);
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
                "Segoe UI", sans-serif;
        }
        button, input, select { font: inherit; }
        .shell {
            width: min(1180px, calc(100% - 32px));
            margin: 0 auto;
            padding: 28px 0 40px;
        }
        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            padding: 18px;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
        }
        h1 {
            margin: 0;
            font-size: 1.45rem;
            line-height: 1.2;
        }
        .subtitle {
            margin: 6px 0 0;
            color: var(--muted);
            font-size: 0.92rem;
        }
        .controls {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
        .token-input {
            width: min(320px, 100%);
            height: 40px;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 0 12px;
            background: #fff;
        }
        .button {
            height: 40px;
            border: 0;
            border-radius: 6px;
            padding: 0 14px;
            cursor: pointer;
            font-weight: 650;
        }
        .button.primary {
            color: #fff;
            background: var(--accent);
        }
        .button.primary:hover { background: var(--accent-dark); }
        .button.secondary {
            color: var(--text);
            background: #e7edf3;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin: 18px 0;
        }
        .metric {
            min-height: 104px;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }
        .metric-label {
            color: var(--muted);
            font-size: 0.86rem;
            font-weight: 650;
        }
        .metric-value {
            margin-top: 10px;
            font-size: 2rem;
            font-weight: 800;
        }
        .metric-value.pending { color: var(--accent); }
        .metric-value.backoff { color: var(--warning); }
        .metric-value.exhausted { color: var(--danger); }
        .metric-value.neutral { color: #344054; }
        .panel {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
        }
        .panel-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding: 16px 18px;
            border-bottom: 1px solid var(--border);
        }
        .panel-title {
            margin: 0;
            font-size: 1.05rem;
        }
        .filters {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
        .filters select {
            height: 38px;
            min-width: 150px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: #fff;
            padding: 0 10px;
        }
        .status-line {
            min-height: 28px;
            padding: 10px 18px 0;
            color: var(--muted);
            font-size: 0.9rem;
        }
        .status-line.error { color: var(--danger); }
        .table-wrap { overflow-x: auto; }
        table {
            width: 100%;
            min-width: 820px;
            border-collapse: collapse;
        }
        th, td {
            padding: 12px 18px;
            text-align: left;
            vertical-align: top;
            border-bottom: 1px solid var(--border);
        }
        th {
            background: #f8fafc;
            color: var(--muted);
            font-size: 0.75rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        td { font-size: 0.92rem; }
        .customer { font-weight: 750; }
        .contact {
            color: var(--muted);
            font-size: 0.82rem;
            margin-top: 4px;
        }
        .badge {
            display: inline-flex;
            align-items: center;
            min-height: 24px;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 750;
            background: #eef2f6;
            color: #344054;
        }
        .badge.classified {
            background: #dcfae6;
            color: var(--success);
        }
        .badge.failed, .badge.hot {
            background: #fee4e2;
            color: var(--danger);
        }
        .badge.pending, .badge.warm {
            background: #fef0c7;
            color: var(--warning);
        }
        .badge.cold {
            background: #dbeafe;
            color: #1e40af;
        }
        .summary {
            max-width: 360px;
            color: #475467;
            line-height: 1.42;
        }
        .empty {
            padding: 28px 18px;
            text-align: center;
            color: var(--muted);
        }
        @media (max-width: 860px) {
            .topbar, .panel-header {
                align-items: stretch;
                flex-direction: column;
            }
            .controls, .filters { justify-content: flex-start; }
            .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 560px) {
            .shell {
                width: min(100% - 20px, 1180px);
                padding-top: 12px;
            }
            .grid { grid-template-columns: 1fr; }
            .token-input { width: 100%; }
            .button { flex: 1 1 120px; }
        }
    </style>
</head>
<body>
    <main class="shell">
        <section class="topbar">
            <div>
                <h1>LeadTriage Admin</h1>
                <p class="subtitle">Queue health and classified lead review.</p>
            </div>
            <div class="controls">
                <input
                    class="token-input"
                    type="password"
                    id="adminToken"
                    autocomplete="off"
                    placeholder="QUEUE_METRICS_TOKEN"
                >
                <button class="button primary" type="button" id="saveTokenButton">Save</button>
                <button class="button secondary" type="button" id="refreshButton">Refresh</button>
            </div>
        </section>

        <section class="grid" aria-label="Queue metrics">
            <div class="metric">
                <div class="metric-label">Pending Leads</div>
                <div id="pendingCount" class="metric-value pending">-</div>
            </div>
            <div class="metric">
                <div class="metric-label">Backoff / Retry</div>
                <div id="backoffCount" class="metric-value backoff">-</div>
            </div>
            <div class="metric">
                <div class="metric-label">Exhausted Leads</div>
                <div id="exhaustedCount" class="metric-value exhausted">-</div>
            </div>
            <div class="metric">
                <div class="metric-label">Max Attempts</div>
                <div id="maxAttempts" class="metric-value neutral">-</div>
            </div>
        </section>

        <section class="panel">
            <div class="panel-header">
                <h2 class="panel-title">Leads</h2>
                <div class="filters">
                    <select id="statusFilter" aria-label="Filter by classification status">
                        <option value="">All statuses</option>
                        <option value="classified">Classified</option>
                        <option value="pending">Pending</option>
                        <option value="failed">Failed</option>
                    </select>
                    <select id="urgencyFilter" aria-label="Filter by urgency">
                        <option value="">All urgencies</option>
                        <option value="hot">Hot</option>
                        <option value="warm">Warm</option>
                        <option value="cold">Cold</option>
                    </select>
                </div>
            </div>
            <div id="statusLine" class="status-line">Enter the admin token, then refresh.</div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Source / Customer</th>
                            <th>Status</th>
                            <th>Urgency</th>
                            <th>Summary</th>
                            <th>Created</th>
                        </tr>
                    </thead>
                    <tbody id="leadsTableBody">
                        <tr>
                            <td colspan="5" class="empty">No data loaded.</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>
    </main>

    <script>
        const tokenInput = document.getElementById("adminToken");
        const saveTokenButton = document.getElementById("saveTokenButton");
        const refreshButton = document.getElementById("refreshButton");
        const statusFilter = document.getElementById("statusFilter");
        const urgencyFilter = document.getElementById("urgencyFilter");
        const statusLine = document.getElementById("statusLine");
        const leadsTableBody = document.getElementById("leadsTableBody");

        function getToken() {
            return localStorage.getItem("lead_triage_admin_token") || "";
        }

        function setStatus(message, isError = false) {
            statusLine.textContent = message;
            statusLine.classList.toggle("error", isError);
        }

        function escapeHtml(value) {
            return String(value ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function badgeClass(value) {
            const normalized = String(value || "").toLowerCase();
            return `badge ${escapeHtml(normalized)}`;
        }

        function saveToken() {
            localStorage.setItem("lead_triage_admin_token", tokenInput.value.trim());
            refreshData();
        }

        async function refreshData() {
            await Promise.all([loadQueueMetrics(), loadLeads()]);
        }

        async function loadQueueMetrics() {
            const token = getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};

            try {
                const response = await fetch("/health/queue", { headers });
                if (!response.ok) {
                    document.getElementById("pendingCount").textContent = "-";
                    document.getElementById("backoffCount").textContent = "-";
                    document.getElementById("exhaustedCount").textContent = "-";
                    document.getElementById("maxAttempts").textContent = "-";
                    return;
                }

                const data = await response.json();
                document.getElementById("pendingCount").textContent = data.pending_count ?? 0;
                document.getElementById("backoffCount").textContent = data.backoff_count ?? 0;
                document.getElementById("exhaustedCount").textContent = data.exhausted_count ?? 0;
                document.getElementById("maxAttempts").textContent = data.max_attempts ?? 0;
            } catch {
                document.getElementById("pendingCount").textContent = "-";
                document.getElementById("backoffCount").textContent = "-";
                document.getElementById("exhaustedCount").textContent = "-";
                document.getElementById("maxAttempts").textContent = "-";
            }
        }

        async function loadLeads() {
            const token = getToken();
            const params = new URLSearchParams({ limit: "50", offset: "0" });
            if (statusFilter.value) {
                params.set("classification_status", statusFilter.value);
            }
            if (urgencyFilter.value) {
                params.set("urgency", urgencyFilter.value);
            }

            try {
                const response = await fetch(`/api/leads?${params.toString()}`, {
                    headers: token ? { "X-Admin-Token": token } : {}
                });

                if (!response.ok) {
                    leadsTableBody.innerHTML = `
                        <tr>
                            <td colspan="5" class="empty">Unable to load leads. Status ${response.status}.</td>
                        </tr>
                    `;
                    setStatus("Lead load failed.", true);
                    return;
                }

                const data = await response.json();
                const items = Array.isArray(data.items) ? data.items : [];
                setStatus(`${data.total ?? items.length} matching lead(s).`);

                if (items.length === 0) {
                    leadsTableBody.innerHTML = `
                        <tr>
                            <td colspan="5" class="empty">No leads found.</td>
                        </tr>
                    `;
                    return;
                }

                leadsTableBody.innerHTML = items.map((lead) => {
                    const contact = lead.customer_email || lead.customer_phone || "No contact";
                    const summary = lead.summary || lead.message || "";
                    return `
                        <tr>
                            <td>
                                <div class="customer">${escapeHtml(lead.customer_name || "Anonymous")}</div>
                                <div class="contact">${escapeHtml(lead.source)} &middot; ${escapeHtml(contact)}</div>
                            </td>
                            <td><span class="${badgeClass(lead.classification_status)}">${escapeHtml(lead.classification_status)}</span></td>
                            <td><span class="${badgeClass(lead.urgency)}">${escapeHtml(lead.urgency || "N/A")}</span></td>
                            <td><div class="summary">${escapeHtml(summary)}</div></td>
                            <td>${escapeHtml(new Date(lead.created_at).toLocaleString())}</td>
                        </tr>
                    `;
                }).join("");
            } catch {
                leadsTableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="empty">Unable to load leads.</td>
                    </tr>
                `;
                setStatus("Lead load failed.", true);
            }
        }

        saveTokenButton.addEventListener("click", saveToken);
        refreshButton.addEventListener("click", refreshData);
        statusFilter.addEventListener("change", loadLeads);
        urgencyFilter.addEventListener("change", loadLeads);

        window.addEventListener("load", () => {
            tokenInput.value = getToken();
            if (tokenInput.value) {
                refreshData();
            }
        });
    </script>
</body>
</html>
"""
