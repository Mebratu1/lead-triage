"""Browser-based admin dashboard shell."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard() -> str:
    """Serve a static self-contained dashboard backed by protected JSON APIs."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LeadTriage Admin Dashboard</title>
    <style>
        :root {
            color-scheme: light;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            --bg: #f1f5f9;
            --surface: #ffffff;
            --surface-muted: #f8fafc;
            --border: #dbe3ef;
            --text: #0f172a;
            --muted: #64748b;
            --blue: #2563eb;
            --blue-dark: #1d4ed8;
            --amber: #b45309;
            --rose: #be123c;
            --emerald: #047857;
            --sky: #0369a1;
        }

        * {
            box-sizing: border-box;
        }

        body {
            min-height: 100vh;
            margin: 0;
            background: var(--bg);
            color: var(--text);
            font-size: 14px;
            letter-spacing: 0;
        }

        main {
            width: min(100%, 1280px);
            margin: 0 auto;
            padding: 24px 20px;
        }

        .toolbar,
        .leads-panel,
        .metric-card {
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--surface);
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        }

        .toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 18px;
            padding: 18px;
        }

        h1,
        h2,
        p {
            margin: 0;
        }

        h1 {
            font-size: 26px;
            line-height: 1.2;
            font-weight: 700;
        }

        h2 {
            font-size: 18px;
            line-height: 1.3;
            font-weight: 700;
        }

        .subtitle,
        .status-line,
        .metric-label {
            color: var(--muted);
        }

        .subtitle {
            margin-top: 4px;
        }

        .status-line {
            margin-top: 4px;
        }

        .status-line.error {
            color: var(--rose);
        }

        .controls,
        .filters {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        input,
        select,
        button {
            height: 40px;
            border-radius: 6px;
            font: inherit;
        }

        input,
        select {
            border: 1px solid #cbd5e1;
            background: #ffffff;
            color: var(--text);
            outline: none;
            padding: 0 12px;
        }

        input:focus,
        select:focus,
        button:focus-visible {
            outline: 2px solid var(--blue);
            outline-offset: 2px;
        }

        #adminToken {
            width: min(360px, 100%);
        }

        button {
            border: 0;
            cursor: pointer;
            font-weight: 700;
            padding: 0 16px;
        }

        .button-primary {
            background: var(--blue);
            color: #ffffff;
        }

        .button-primary:hover {
            background: var(--blue-dark);
        }

        .button-secondary {
            background: #e2e8f0;
            color: var(--text);
        }

        .button-secondary:hover {
            background: #cbd5e1;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin-bottom: 18px;
        }

        .metric-card {
            padding: 18px;
        }

        .metric-label {
            font-weight: 700;
        }

        .metric-value {
            margin-top: 12px;
            font-size: 34px;
            line-height: 1;
            font-weight: 800;
        }

        .metric-value.pending {
            color: var(--blue);
        }

        .metric-value.backoff {
            color: var(--amber);
        }

        .metric-value.exhausted {
            color: var(--rose);
        }

        .metric-value.max-attempts {
            color: #334155;
        }

        .leads-panel {
            overflow: hidden;
        }

        .leads-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            border-bottom: 1px solid var(--border);
            padding: 16px 18px;
        }

        .table-wrap {
            overflow-x: auto;
        }

        table {
            width: 100%;
            min-width: 900px;
            border-collapse: collapse;
            text-align: left;
        }

        thead {
            background: var(--surface-muted);
            color: var(--muted);
            font-size: 12px;
            font-weight: 800;
            text-transform: uppercase;
        }

        th,
        td {
            padding: 13px 18px;
            vertical-align: top;
        }

        tbody tr {
            border-top: 1px solid var(--border);
        }

        tbody tr:first-child {
            border-top: 0;
        }

        tbody tr:hover {
            background: var(--surface-muted);
        }

        .customer {
            font-weight: 800;
            color: var(--text);
        }

        .meta,
        .timestamp {
            margin-top: 4px;
            color: var(--muted);
            font-size: 12px;
        }

        .summary {
            max-width: 440px;
            overflow: hidden;
            color: #475569;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .empty,
        .load-error {
            padding: 32px 18px;
            text-align: center;
            color: var(--muted);
        }

        .load-error {
            color: var(--rose);
        }

        .badge {
            display: inline-flex;
            align-items: center;
            min-height: 26px;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 800;
        }

        .badge.status-classified {
            background: #d1fae5;
            color: var(--emerald);
        }

        .badge.status-failed,
        .badge.urgency-hot {
            background: #ffe4e6;
            color: var(--rose);
        }

        .badge.status-pending,
        .badge.urgency-warm {
            background: #fef3c7;
            color: var(--amber);
        }

        .badge.urgency-cold {
            background: #e0f2fe;
            color: var(--sky);
        }

        .badge.urgency-empty {
            background: #e2e8f0;
            color: #475569;
        }

        @media (max-width: 900px) {
            .toolbar,
            .leads-header {
                align-items: stretch;
                flex-direction: column;
            }

            .controls,
            .filters {
                align-items: stretch;
                flex-direction: column;
            }

            #adminToken,
            button,
            select {
                width: 100%;
            }

            .metrics-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 560px) {
            main {
                padding: 16px 12px;
            }

            .metrics-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <main>
        <section class="toolbar">
            <div>
                <h1>LeadTriage Admin</h1>
                <p class="subtitle">Queue telemetry and classified lead review</p>
            </div>
            <div class="controls">
                <input id="adminToken" type="password" autocomplete="off" placeholder="Admin token">
                <button id="saveTokenButton" type="button" class="button-primary">Save</button>
                <button id="refreshButton" type="button" class="button-secondary">Refresh</button>
            </div>
        </section>

        <section class="metrics-grid" aria-label="Queue telemetry">
            <div class="metric-card">
                <div class="metric-label">Pending</div>
                <div id="pendingCount" class="metric-value pending">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Backoff / Retry</div>
                <div id="backoffCount" class="metric-value backoff">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Exhausted</div>
                <div id="exhaustedCount" class="metric-value exhausted">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Max Attempts</div>
                <div id="maxAttempts" class="metric-value max-attempts">-</div>
            </div>
        </section>

        <section class="leads-panel">
            <div class="leads-header">
                <div>
                    <h2>Leads</h2>
                    <p id="statusLine" class="status-line">Ready</p>
                </div>
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
        const TOKEN_STORAGE_KEY = "lead_triage_admin_token";
        const REFRESH_INTERVAL_MS = 30000;
        const tokenInput = document.getElementById("adminToken");
        const saveTokenButton = document.getElementById("saveTokenButton");
        const refreshButton = document.getElementById("refreshButton");
        const statusFilter = document.getElementById("statusFilter");
        const urgencyFilter = document.getElementById("urgencyFilter");
        const statusLine = document.getElementById("statusLine");
        const leadsTableBody = document.getElementById("leadsTableBody");

        function getToken() {
            return localStorage.getItem(TOKEN_STORAGE_KEY) || "";
        }

        function getAuthHeaders() {
            const token = getToken();
            return token ? { "X-Admin-Token": token } : {};
        }

        function setStatus(message, isError = false) {
            statusLine.textContent = message;
            statusLine.className = isError ? "status-line error" : "status-line";
        }

        function escapeHtml(value) {
            return String(value ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function statusBadgeClass(value) {
            const status = String(value || "pending").toLowerCase();
            if (status === "classified") {
                return "badge status-classified";
            }
            if (status === "failed") {
                return "badge status-failed";
            }
            return "badge status-pending";
        }

        function urgencyBadgeClass(value) {
            const urgency = String(value || "").toLowerCase();
            if (urgency === "hot") {
                return "badge urgency-hot";
            }
            if (urgency === "warm") {
                return "badge urgency-warm";
            }
            if (urgency === "cold") {
                return "badge urgency-cold";
            }
            return "badge urgency-empty";
        }

        function clearQueueMetrics() {
            document.getElementById("pendingCount").textContent = "-";
            document.getElementById("backoffCount").textContent = "-";
            document.getElementById("exhaustedCount").textContent = "-";
            document.getElementById("maxAttempts").textContent = "-";
        }

        function saveToken() {
            localStorage.setItem(TOKEN_STORAGE_KEY, tokenInput.value.trim());
            refreshData();
        }

        async function refreshData() {
            await Promise.all([loadQueueMetrics(), loadLeads()]);
        }

        async function loadQueueMetrics() {
            try {
                const response = await fetch("/health/queue", {
                    headers: getAuthHeaders()
                });
                if (!response.ok) {
                    clearQueueMetrics();
                    return;
                }

                const data = await response.json();
                document.getElementById("pendingCount").textContent = data.pending_count ?? 0;
                document.getElementById("backoffCount").textContent = data.backoff_count ?? 0;
                document.getElementById("exhaustedCount").textContent = data.exhausted_count ?? 0;
                document.getElementById("maxAttempts").textContent = data.max_attempts ?? 0;
            } catch {
                clearQueueMetrics();
            }
        }

        async function loadLeads() {
            const params = new URLSearchParams({ limit: "50", offset: "0" });
            if (statusFilter.value) {
                params.set("classification_status", statusFilter.value);
            }
            if (urgencyFilter.value) {
                params.set("urgency", urgencyFilter.value);
            }

            try {
                const response = await fetch(`/api/leads?${params.toString()}`, {
                    headers: getAuthHeaders()
                });

                if (!response.ok) {
                    leadsTableBody.innerHTML = `
                        <tr>
                            <td colspan="5" class="load-error">Unable to load leads. Status ${response.status}.</td>
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
                    const createdAt = lead.created_at
                        ? new Date(lead.created_at).toLocaleString()
                        : "";
                    return `
                        <tr>
                            <td>
                                <div class="customer">${escapeHtml(lead.customer_name || "Anonymous")}</div>
                                <div class="meta">${escapeHtml(lead.source)} &middot; ${escapeHtml(contact)}</div>
                            </td>
                            <td>
                                <span class="${statusBadgeClass(lead.classification_status)}">${escapeHtml(lead.classification_status)}</span>
                            </td>
                            <td>
                                <span class="${urgencyBadgeClass(lead.urgency)}">${escapeHtml(lead.urgency || "N/A")}</span>
                            </td>
                            <td>
                                <div class="summary">${escapeHtml(summary)}</div>
                            </td>
                            <td class="timestamp">${escapeHtml(createdAt)}</td>
                        </tr>
                    `;
                }).join("");
            } catch {
                leadsTableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="load-error">Unable to load leads.</td>
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
            refreshData();
            window.setInterval(refreshData, REFRESH_INTERVAL_MS);
        });
    </script>
</body>
</html>
"""
