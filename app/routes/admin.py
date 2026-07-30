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

        button:disabled {
            cursor: not-allowed;
            opacity: 0.6;
        }

        .button-small {
            height: 34px;
            padding: 0 12px;
            font-size: 12px;
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

        .filters {
            flex-wrap: wrap;
            justify-content: flex-end;
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

        th:last-child,
        td:last-child {
            text-align: right;
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

        .badge.integration-synced {
            background: #d1fae5;
            color: var(--emerald);
        }

        .badge.integration-failed {
            background: #ffe4e6;
            color: var(--rose);
        }

        .badge.integration-pending {
            background: #e2e8f0;
            color: #475569;
        }

        .detail-dialog {
            width: min(760px, calc(100% - 32px));
            max-height: calc(100vh - 48px);
            border: 1px solid var(--border);
            border-radius: 10px;
            background: var(--surface);
            color: var(--text);
            box-shadow: 0 24px 64px rgba(15, 23, 42, 0.24);
            padding: 0;
        }

        .detail-dialog::backdrop {
            background: rgba(15, 23, 42, 0.55);
        }

        .detail-header,
        .detail-actions {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 16px 18px;
        }

        .detail-header {
            border-bottom: 1px solid var(--border);
        }

        .detail-actions {
            justify-content: flex-end;
            border-top: 1px solid var(--border);
        }

        .detail-body {
            max-height: calc(100vh - 210px);
            overflow-y: auto;
            padding: 18px;
        }

        .detail-status {
            min-height: 20px;
            margin-bottom: 14px;
            color: var(--muted);
        }

        .detail-status.error {
            color: var(--rose);
        }

        .detail-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }

        .detail-field,
        .detail-copy {
            border: 1px solid var(--border);
            border-radius: 7px;
            background: var(--surface-muted);
            padding: 12px;
        }

        .detail-copy {
            margin-top: 12px;
        }

        .detail-label {
            margin-bottom: 6px;
            color: var(--muted);
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        .detail-value {
            overflow-wrap: anywhere;
            line-height: 1.5;
        }

        .detail-message {
            white-space: pre-wrap;
        }

        .detail-loading {
            padding: 28px 0;
            color: var(--muted);
            text-align: center;
        }

        .icon-button {
            width: 36px;
            padding: 0;
            background: #e2e8f0;
            color: var(--text);
            font-size: 20px;
            line-height: 1;
        }

        .sr-only {
            position: absolute;
            width: 1px;
            height: 1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            clip-path: inset(50%);
            white-space: nowrap;
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

            .button-small,
            .icon-button {
                width: auto;
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

            .detail-grid {
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
                <input id="adminToken" type="password" autocomplete="off" aria-label="Admin token" placeholder="Admin token">
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
                    <p id="statusLine" class="status-line" role="status" aria-live="polite">Ready</p>
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
                    <button id="exportCsvButton" type="button" class="button-secondary">Export CSV</button>
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
                            <th><span class="sr-only">Actions</span></th>
                        </tr>
                    </thead>
                    <tbody id="leadsTableBody">
                        <tr>
                            <td colspan="6" class="empty">No data loaded.</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>

        <dialog id="leadDetailDialog" class="detail-dialog" aria-labelledby="leadDetailTitle">
            <div class="detail-header">
                <h2 id="leadDetailTitle">Lead details</h2>
                <button id="closeDetailButton" type="button" class="icon-button" aria-label="Close lead details">&times;</button>
            </div>
            <div class="detail-body">
                <p id="detailStatus" class="detail-status" role="status" aria-live="polite"></p>
                <div id="leadDetailContent">
                    <p class="detail-loading">Select a lead to view details.</p>
                </div>
            </div>
            <div class="detail-actions">
                <button id="syncLeadButton" type="button" class="button-primary" disabled>Sync to CRM</button>
                <button id="dismissDetailButton" type="button" class="button-secondary">Close</button>
            </div>
        </dialog>
    </main>

    <script>
        const TOKEN_STORAGE_KEY = "lead_triage_admin_token";
        const REFRESH_INTERVAL_MS = 30000;
        const tokenInput = document.getElementById("adminToken");
        const saveTokenButton = document.getElementById("saveTokenButton");
        const refreshButton = document.getElementById("refreshButton");
        const exportCsvButton = document.getElementById("exportCsvButton");
        const statusFilter = document.getElementById("statusFilter");
        const urgencyFilter = document.getElementById("urgencyFilter");
        const statusLine = document.getElementById("statusLine");
        const leadsTableBody = document.getElementById("leadsTableBody");
        const leadDetailDialog = document.getElementById("leadDetailDialog");
        const leadDetailTitle = document.getElementById("leadDetailTitle");
        const leadDetailContent = document.getElementById("leadDetailContent");
        const detailStatus = document.getElementById("detailStatus");
        const closeDetailButton = document.getElementById("closeDetailButton");
        const dismissDetailButton = document.getElementById("dismissDetailButton");
        const syncLeadButton = document.getElementById("syncLeadButton");
        let queueAbortController = null;
        let leadListAbortController = null;
        let detailAbortController = null;
        let syncAbortController = null;
        let exportAbortController = null;
        let detailRequestId = 0;
        let refreshInFlight = null;
        let selectedLead = null;

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

        function setDetailStatus(message, isError = false) {
            detailStatus.textContent = message;
            detailStatus.className = isError ? "detail-status error" : "detail-status";
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

        function integrationBadgeClass(value) {
            const integrationStatus = String(value || "pending").toLowerCase();
            if (integrationStatus === "synced") {
                return "badge integration-synced";
            }
            if (integrationStatus === "failed") {
                return "badge integration-failed";
            }
            return "badge integration-pending";
        }

        function formatDate(value) {
            if (!value) {
                return "N/A";
            }
            const date = new Date(value);
            return Number.isNaN(date.getTime()) ? "N/A" : date.toLocaleString();
        }

        function buildLeadQueryParams(limit) {
            const params = new URLSearchParams({ limit: String(limit), offset: "0" });
            if (statusFilter.value) {
                params.set("classification_status", statusFilter.value);
            }
            if (urgencyFilter.value) {
                params.set("urgency", urgencyFilter.value);
            }
            return params;
        }

        async function readResponseBody(response) {
            try {
                return await response.json();
            } catch {
                return null;
            }
        }

        function responseMessage(body, fallback) {
            return body && typeof body.detail === "string" ? body.detail : fallback;
        }

        function requireToken(reportError) {
            if (getToken()) {
                return true;
            }
            reportError("Save an admin token before using this action.", true);
            tokenInput.focus();
            return false;
        }

        function setButtonBusy(button, isBusy, busyLabel) {
            if (isBusy) {
                if (!button.dataset.idleLabel) {
                    button.dataset.idleLabel = button.textContent;
                }
                button.textContent = busyLabel;
                button.disabled = true;
                return;
            }
            button.textContent = button.dataset.idleLabel || button.textContent;
            delete button.dataset.idleLabel;
            button.disabled = false;
        }

        function clearQueueMetrics() {
            document.getElementById("pendingCount").textContent = "-";
            document.getElementById("backoffCount").textContent = "-";
            document.getElementById("exhaustedCount").textContent = "-";
            document.getElementById("maxAttempts").textContent = "-";
        }

        function cancelProtectedRequests() {
            if (queueAbortController) {
                queueAbortController.abort();
                queueAbortController = null;
            }
            if (leadListAbortController) {
                leadListAbortController.abort();
                leadListAbortController = null;
            }
            if (detailAbortController) {
                detailAbortController.abort();
                detailAbortController = null;
            }
            if (syncAbortController) {
                syncAbortController.abort();
                syncAbortController = null;
            }
            if (exportAbortController) {
                exportAbortController.abort();
                exportAbortController = null;
            }
            leadsTableBody.removeAttribute("aria-busy");
            setButtonBusy(exportCsvButton, false, "Exporting...");
        }

        async function saveToken() {
            const token = tokenInput.value.trim();
            tokenInput.value = token;
            if (!token) {
                cancelProtectedRequests();
                localStorage.removeItem(TOKEN_STORAGE_KEY);
                clearQueueMetrics();
                leadsTableBody.innerHTML = `
                    <tr>
                        <td colspan="6" class="empty">Save an admin token to load leads.</td>
                    </tr>
                `;
                setStatus("Admin token cleared.");
                closeLeadDetail();
                return;
            }

            localStorage.setItem(TOKEN_STORAGE_KEY, token);
            setStatus("Admin token saved. Refreshing data...");
            await refreshData(true);
        }

        async function refreshData(force = false) {
            if (refreshInFlight && !force) {
                return refreshInFlight;
            }

            setButtonBusy(refreshButton, true, "Refreshing...");
            const refreshOperation = Promise.all([loadQueueMetrics(), loadLeads()]);
            refreshInFlight = refreshOperation;
            try {
                await refreshOperation;
            } finally {
                if (refreshInFlight === refreshOperation) {
                    refreshInFlight = null;
                    setButtonBusy(refreshButton, false, "Refreshing...");
                }
            }
        }

        async function loadQueueMetrics() {
            if (queueAbortController) {
                queueAbortController.abort();
            }
            const requestController = new AbortController();
            queueAbortController = requestController;
            try {
                const response = await fetch("/health/queue", {
                    headers: getAuthHeaders(),
                    cache: "no-store",
                    signal: requestController.signal
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
            } catch (error) {
                if (error.name === "AbortError") {
                    return;
                }
                clearQueueMetrics();
            } finally {
                if (queueAbortController === requestController) {
                    queueAbortController = null;
                }
            }
        }

        async function loadLeads() {
            if (leadListAbortController) {
                leadListAbortController.abort();
            }
            const requestController = new AbortController();
            leadListAbortController = requestController;
            const params = buildLeadQueryParams(50);
            leadsTableBody.setAttribute("aria-busy", "true");
            setStatus("Loading leads...");
            try {
                const response = await fetch(`/api/leads?${params.toString()}`, {
                    headers: getAuthHeaders(),
                    cache: "no-store",
                    signal: requestController.signal
                });

                if (!response.ok) {
                    const body = await readResponseBody(response);
                    leadsTableBody.innerHTML = `
                        <tr>
                            <td colspan="6" class="load-error">Unable to load leads. Status ${response.status}.</td>
                        </tr>
                    `;
                    setStatus(responseMessage(body, "Lead load failed."), true);
                    return;
                }

                const data = await response.json();
                const items = Array.isArray(data.items) ? data.items : [];
                setStatus(`${data.total ?? items.length} matching lead(s); showing ${items.length}.`);

                if (items.length === 0) {
                    leadsTableBody.innerHTML = `
                        <tr>
                            <td colspan="6" class="empty">No leads found.</td>
                        </tr>
                    `;
                    return;
                }

                leadsTableBody.innerHTML = items.map((lead) => {
                    const contact = lead.customer_email || lead.customer_phone || "No contact";
                    const summary = lead.summary || lead.message || "";
                    const createdAt = formatDate(lead.created_at);
                    return `
                        <tr data-lead-id="${escapeHtml(lead.id)}">
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
                            <td>
                                <button
                                    type="button"
                                    class="button-secondary button-small view-lead-button"
                                    data-lead-id="${escapeHtml(lead.id)}"
                                    aria-label="View lead ${escapeHtml(lead.customer_name || lead.id)}"
                                >View</button>
                            </td>
                        </tr>
                    `;
                }).join("");
            } catch (error) {
                if (error.name === "AbortError") {
                    return;
                }
                leadsTableBody.innerHTML = `
                    <tr>
                        <td colspan="6" class="load-error">Unable to load leads.</td>
                    </tr>
                `;
                setStatus("Lead load failed.", true);
            } finally {
                if (leadListAbortController === requestController) {
                    leadListAbortController = null;
                    leadsTableBody.removeAttribute("aria-busy");
                }
            }
        }

        function renderLeadDetail(lead) {
            const classificationStatus = lead.classification_status || "pending";
            const integrationStatus = lead.integration_status || "pending";
            leadDetailTitle.textContent = lead.customer_name || "Anonymous lead";
            leadDetailContent.innerHTML = `
                <div class="detail-grid">
                    <div class="detail-field">
                        <div class="detail-label">Classification</div>
                        <div class="detail-value">
                            <span class="${statusBadgeClass(classificationStatus)}">${escapeHtml(classificationStatus)}</span>
                            <span class="${urgencyBadgeClass(lead.urgency)}">${escapeHtml(lead.urgency || "N/A")}</span>
                        </div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">CRM integration</div>
                        <div class="detail-value">
                            <span class="${integrationBadgeClass(integrationStatus)}">${escapeHtml(integrationStatus)}</span>
                        </div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Source</div>
                        <div class="detail-value">${escapeHtml(lead.source || "N/A")}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Classification attempts</div>
                        <div class="detail-value">${escapeHtml(lead.classification_attempt_count ?? 0)}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Email</div>
                        <div class="detail-value">${escapeHtml(lead.customer_email || "N/A")}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Phone</div>
                        <div class="detail-value">${escapeHtml(lead.customer_phone || "N/A")}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Created</div>
                        <div class="detail-value">${escapeHtml(formatDate(lead.created_at))}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Updated</div>
                        <div class="detail-value">${escapeHtml(formatDate(lead.updated_at))}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Last CRM sync</div>
                        <div class="detail-value">${escapeHtml(formatDate(lead.integration_last_synced_at))}</div>
                    </div>
                    <div class="detail-field">
                        <div class="detail-label">Lead ID</div>
                        <div class="detail-value">${escapeHtml(lead.id)}</div>
                    </div>
                </div>
                <div class="detail-copy">
                    <div class="detail-label">AI summary</div>
                    <div class="detail-value detail-message">${escapeHtml(lead.summary || "No summary available.")}</div>
                </div>
                <div class="detail-copy">
                    <div class="detail-label">Original message</div>
                    <div class="detail-value detail-message">${escapeHtml(lead.message || "No message available.")}</div>
                </div>
            `;
            leadDetailContent.removeAttribute("aria-busy");

            const canSync = classificationStatus === "classified";
            syncLeadButton.textContent = integrationStatus === "synced" ? "Sync again" : "Sync to CRM";
            syncLeadButton.disabled = !canSync;
            if (canSync) {
                setDetailStatus("Lead detail loaded.");
            } else {
                setDetailStatus("CRM sync is available after the lead is classified.");
            }
        }

        async function loadLeadDetail(leadId) {
            if (!requireToken(setDetailStatus)) {
                return;
            }
            if (detailAbortController) {
                detailAbortController.abort();
            }

            const requestController = new AbortController();
            const requestId = ++detailRequestId;
            detailAbortController = requestController;
            selectedLead = null;
            leadDetailTitle.textContent = "Lead details";
            leadDetailContent.setAttribute("aria-busy", "true");
            leadDetailContent.innerHTML = '<p class="detail-loading">Loading lead details...</p>';
            syncLeadButton.disabled = true;
            setDetailStatus("Loading...");
            if (!leadDetailDialog.open) {
                leadDetailDialog.showModal();
            }

            try {
                const response = await fetch(`/api/leads/${encodeURIComponent(leadId)}`, {
                    headers: getAuthHeaders(),
                    cache: "no-store",
                    signal: requestController.signal
                });
                const body = await readResponseBody(response);
                if (!response.ok) {
                    throw new Error(responseMessage(body, "Lead detail load failed."));
                }
                if (!body || String(body.id) !== String(leadId)) {
                    throw new Error("Lead detail response did not match the selected lead.");
                }
                if (requestId !== detailRequestId) {
                    return;
                }

                selectedLead = body;
                renderLeadDetail(body);
            } catch (error) {
                if (error.name === "AbortError" || requestId !== detailRequestId) {
                    return;
                }
                leadDetailContent.removeAttribute("aria-busy");
                leadDetailContent.innerHTML = '<p class="load-error">Unable to load lead details.</p>';
                setDetailStatus(error.message || "Lead detail load failed.", true);
            } finally {
                if (detailAbortController === requestController) {
                    detailAbortController = null;
                }
            }
        }

        function resetLeadDetailState() {
            detailRequestId += 1;
            if (detailAbortController) {
                detailAbortController.abort();
                detailAbortController = null;
            }
            selectedLead = null;
            syncLeadButton.disabled = true;
            syncLeadButton.textContent = "Sync to CRM";
            delete syncLeadButton.dataset.idleLabel;
        }

        function closeLeadDetail() {
            resetLeadDetailState();
            if (leadDetailDialog.open) {
                leadDetailDialog.close();
            }
        }

        async function syncSelectedLead() {
            if (!selectedLead || selectedLead.classification_status !== "classified") {
                setDetailStatus("Only classified leads can be synced.", true);
                return;
            }
            if (!requireToken(setDetailStatus)) {
                return;
            }

            const leadId = selectedLead.id;
            const confirmed = window.confirm(
                "Send this classified lead to the configured CRM integration now?"
            );
            if (!confirmed) {
                return;
            }

            setButtonBusy(syncLeadButton, true, "Syncing...");
            setDetailStatus("Syncing lead...");
            if (syncAbortController) {
                syncAbortController.abort();
            }
            const requestController = new AbortController();
            syncAbortController = requestController;
            let outcome = null;
            let outcomeIsError = false;
            try {
                const response = await fetch(
                    `/api/leads/${encodeURIComponent(leadId)}/sync`,
                    {
                        method: "POST",
                        headers: getAuthHeaders(),
                        signal: requestController.signal
                    }
                );
                const body = await readResponseBody(response);
                if (
                    body
                    && body.integration_status
                    && String(body.id) === String(leadId)
                ) {
                    outcome = body;
                    outcomeIsError = !response.ok;
                } else if (!response.ok) {
                    throw new Error(responseMessage(body, "Lead sync failed."));
                } else {
                    throw new Error("Lead sync returned an unexpected response.");
                }
            } catch (error) {
                if (error.name === "AbortError") {
                    return;
                }
                if (selectedLead && String(selectedLead.id) === String(leadId)) {
                    setDetailStatus(error.message || "Lead sync failed.", true);
                }
                return;
            } finally {
                if (syncAbortController === requestController) {
                    syncAbortController = null;
                }
                if (selectedLead && String(selectedLead.id) === String(leadId)) {
                    setButtonBusy(syncLeadButton, false, "Syncing...");
                }
            }

            if (!outcome || !selectedLead || String(selectedLead.id) !== String(leadId)) {
                return;
            }
            selectedLead = {
                ...selectedLead,
                integration_status: outcome.integration_status,
                integration_last_synced_at: outcome.integration_last_synced_at
            };
            renderLeadDetail(selectedLead);
            setDetailStatus(
                outcome.detail || (outcomeIsError ? "Lead sync failed." : "Lead synced."),
                outcomeIsError
            );
            await loadLeads();
        }

        async function exportCsv() {
            if (!requireToken(setStatus)) {
                return;
            }

            const params = buildLeadQueryParams(1000);
            if (exportAbortController) {
                exportAbortController.abort();
            }
            const requestController = new AbortController();
            exportAbortController = requestController;
            setButtonBusy(exportCsvButton, true, "Exporting...");
            setStatus("Preparing CSV export...");
            try {
                const response = await fetch(`/api/leads/export/csv?${params.toString()}`, {
                    headers: getAuthHeaders(),
                    cache: "no-store",
                    signal: requestController.signal
                });
                if (!response.ok) {
                    const body = await readResponseBody(response);
                    throw new Error(responseMessage(body, "CSV export failed."));
                }

                const blob = await response.blob();
                const downloadUrl = URL.createObjectURL(blob);
                const downloadLink = document.createElement("a");
                downloadLink.href = downloadUrl;
                downloadLink.download = "classified_leads_export.csv";
                document.body.appendChild(downloadLink);
                downloadLink.click();
                downloadLink.remove();
                window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 0);
                setStatus("CSV export downloaded.");
            } catch (error) {
                if (error.name === "AbortError") {
                    return;
                }
                setStatus(error.message || "CSV export failed.", true);
            } finally {
                if (exportAbortController === requestController) {
                    exportAbortController = null;
                    setButtonBusy(exportCsvButton, false, "Exporting...");
                }
            }
        }

        leadsTableBody.addEventListener("click", (event) => {
            const target = event.target instanceof Element ? event.target : null;
            const button = target ? target.closest(".view-lead-button") : null;
            if (button && button.dataset.leadId) {
                loadLeadDetail(button.dataset.leadId);
            }
        });
        saveTokenButton.addEventListener("click", saveToken);
        refreshButton.addEventListener("click", () => refreshData());
        exportCsvButton.addEventListener("click", exportCsv);
        statusFilter.addEventListener("change", loadLeads);
        urgencyFilter.addEventListener("change", loadLeads);
        closeDetailButton.addEventListener("click", closeLeadDetail);
        dismissDetailButton.addEventListener("click", closeLeadDetail);
        syncLeadButton.addEventListener("click", syncSelectedLead);
        leadDetailDialog.addEventListener("close", resetLeadDetailState);

        window.addEventListener("load", () => {
            tokenInput.value = getToken();
            refreshData();
            window.setInterval(refreshData, REFRESH_INTERVAL_MS);
        });
    </script>
</body>
</html>
"""
