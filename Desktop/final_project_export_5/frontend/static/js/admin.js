(() => {
  const token = localStorage.getItem("access_token");
  const role = localStorage.getItem("role");
  if (!token || role !== "admin") {
    window.location.href = "/login";
    return;
  }

  const authHeaders = { Authorization: `Bearer ${token}` };
  const jsonHeaders = { ...authHeaders, "Content-Type": "application/json" };

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    if (res.status === 401 || res.status === 403) {
      if (res.status === 401) {
        window.location.href = "/login";
        throw new Error("unauthorized");
      }
    }
    let data = null;
    if (res.status !== 204) {
      try { data = await res.json(); } catch (_) { data = null; }
    }
    if (!res.ok) {
      throw new Error((data && data.detail) || `Request failed (${res.status})`);
    }
    return data;
  }

  function flash(el, msg, ok = false) {
    el.textContent = msg;
    el.style.borderColor = ok ? "var(--success)" : "var(--danger)";
    el.style.color = ok ? "#a4e0bf" : "#e6a49c";
    el.style.background = ok ? "var(--success-soft)" : "var(--danger-soft)";
    el.classList.add("visible");
    setTimeout(() => el.classList.remove("visible"), 5000);
  }

  document.getElementById("adminShell").style.display = "block";

  // ============================================================
  // Tabs
  // ============================================================
  const tabButtons = document.querySelectorAll(".tab-btn");
  const panels = {
    monitor: document.getElementById("panel-monitor"),
    users: document.getElementById("panel-users"),
    questions: document.getElementById("panel-questions"),
  };
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => b.classList.remove("active"));
      Object.values(panels).forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      panels[btn.dataset.tab].classList.add("active");
      if (btn.dataset.tab === "users") loadUsers();
      if (btn.dataset.tab === "questions") loadQuestions();
    });
  });

  // ============================================================
  // Credential reveal modal
  // ============================================================
  const credModal = document.getElementById("credModal");
  const credModalTitle = document.getElementById("credModalTitle");
  const credEmail = document.getElementById("credEmail");
  const credPassword = document.getElementById("credPassword");
  document.getElementById("credModalClose").addEventListener("click", () => {
    credModal.classList.remove("visible");
  });
  function showCredModal(title, email, password) {
    credModalTitle.textContent = title;
    credEmail.textContent = email;
    credPassword.textContent = password;
    credModal.classList.add("visible");
  }

  // ============================================================
  // Live monitor (unchanged behaviour, existing feature)
  // ============================================================
  const connDot = document.getElementById("connDot");
  const connLabel = document.getElementById("connLabel");
  const tbody = document.getElementById("candidateTableBody");
  const tileInProgress = document.getElementById("tileInProgress");
  const tileCompleted = document.getElementById("tileCompleted");
  const tileDisqualified = document.getElementById("tileDisqualified");
  const tileTotal = document.getElementById("tileTotal");

  const candidateSessions = new Map();

  async function fetchSnapshot() {
    try {
      const rows = await api("/admin/sessions", { headers: authHeaders });
      candidateSessions.clear();
      rows.forEach((r) => candidateSessions.set(r.session_id, r));
      renderMonitor();
    } catch (err) { /* transient — next poll retries */ }
  }

  function applyWsUpdate(msg) {
    const existing = candidateSessions.get(msg.session_id) || {};
    candidateSessions.set(msg.session_id, {
      ...existing,
      session_id: msg.session_id,
      user_id: msg.user_id,
      full_name: msg.full_name,
      email: msg.email,
      status: msg.status,
      strikes: msg.strikes,
      max_strikes: msg.max_strikes,
      answered_count: msg.answered_count,
      total_questions: msg.total_questions,
      time_remaining_seconds: msg.time_remaining_seconds,
      last_activity_seconds_ago: 0,
      score: msg.score,
    });
    renderMonitor();
  }

  function formatTime(seconds) {
    if (seconds == null) return "—";
    const m = Math.floor(seconds / 60).toString().padStart(2, "0");
    const s = (seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }

  function renderMonitor() {
    const rows = Array.from(candidateSessions.values()).sort((a, b) => a.full_name.localeCompare(b.full_name));

    let inProgress = 0, completed = 0, disqualified = 0;
    rows.forEach((r) => {
      if (r.status === "in_progress") inProgress++;
      else if (r.status === "completed" || r.status === "expired") completed++;
      else if (r.status === "disqualified") disqualified++;
    });
    tileInProgress.textContent = inProgress;
    tileCompleted.textContent = completed;
    tileDisqualified.textContent = disqualified;
    tileTotal.textContent = rows.length;

    tbody.innerHTML = "";
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      const stale = (r.last_activity_seconds_ago || 0) > 20 && r.status === "in_progress";
      tr.className = stale ? "stale-row" : "";

      const dots = Array.from({ length: r.max_strikes || 3 }, (_, i) =>
        `<span class="dot ${i < r.strikes ? "on" : ""}"></span>`
      ).join("");

      tr.innerHTML = `
        <td>
          <div>${escapeHtml(r.full_name)}</div>
          <div style="color:var(--text-faint); font-size:12px;">${escapeHtml(r.email)}</div>
        </td>
        <td><span class="status-pill ${r.status}">${r.status.replace("_", " ")}</span></td>
        <td><div class="strike-cluster">${dots}</div></td>
        <td class="progress-cell">${r.answered_count} / ${r.total_questions}</td>
        <td class="score-cell">${r.score !== null && r.score !== undefined ? r.score : '—'}</td>
        <td class="time-cell">${formatTime(r.time_remaining_seconds)}</td>
        <td>
          ${r.status === "in_progress" ? `<button class="btn btn-sm btn-danger disqualify-btn" data-session-id="${r.session_id}" style="padding:4px 8px; font-size:12px;">Disqualify</button>` : `<span style="color:var(--text-faint); font-size:12px;">—</span>`}
        </td>
      `;
      tbody.appendChild(tr);
    });

    renderVideoGrid(rows);
  }

  tbody.addEventListener("click", async (e) => {
    const btn = e.target.closest(".disqualify-btn");
    if (!btn) return;
    const sessionId = btn.dataset.sessionId;
    if (!sessionId) return;
    if (!confirm("Are you sure you want to DISQUALIFY this candidate? Their session will be terminated and access revoked immediately.")) return;

    btn.disabled = true;
    try {
      await api(`/admin/sessions/${sessionId}/disqualify`, {
        method: "POST",
        headers: authHeaders,
      });
      await fetchSnapshot();
    } catch (err) {
      alert("Failed to disqualify candidate: " + err.message);
      btn.disabled = false;
    }
  });

  // --- Proctor Snapshot Video Grid (JPEG Feed) ---
  const toggleGridBtn = document.getElementById("toggleGridBtn");
  const tableView = document.getElementById("tableView");
  const gridView = document.getElementById("gridView");
  const videoGrid = document.getElementById("videoGrid");
  let gridActive = false;

  toggleGridBtn.addEventListener("click", () => {
    gridActive = !gridActive;
    if (gridActive) {
      tableView.style.display = "none";
      gridView.style.display = "block";
      toggleGridBtn.textContent = "View Table";
    } else {
      tableView.style.display = "block";
      gridView.style.display = "none";
      toggleGridBtn.textContent = "View Video Grid";
    }
  });

  const frameTimestamps = {}; // target_user_id -> timestamp

  function renderVideoGrid(rows) {
    rows.forEach((r) => {
      let tile = document.getElementById(`video-tile-${r.user_id}`);
      if (!tile && r.status === "in_progress") {
        tile = document.createElement("div");
        tile.id = `video-tile-${r.user_id}`;
        tile.className = "video-tile";
        tile.style.border = "2px solid var(--success)";
        tile.style.position = "relative";
        tile.style.backgroundColor = "#111";
        tile.style.minHeight = "180px";
        tile.style.contentVisibility = "auto";
        tile.style.containIntrinsicSize = "180px 140px";
        tile.style.display = "flex";
        tile.style.flexDirection = "column";
        tile.innerHTML = `
          <div style="flex: 1; position: relative; display: flex; align-items: center; justify-content: center; overflow: hidden;">
            <img id="video-img-${r.user_id}" style="width: 100%; height: auto; display: none; object-fit: contain;" />
            <div id="video-fallback-${r.user_id}" style="position:absolute; top:0; left:0; right:0; bottom:0; display:flex; flex-direction:column; align-items:center; justify-content:center; color:#fff;">
              <div style="font-size: 24px; font-weight: bold; opacity: 0.5;">${r.full_name.split(' ').map(n=>n[0]).join('')}</div>
              <div style="margin-top: 10px; font-size: 11px; background: rgba(220,53,69,0.8); padding: 4px 8px; border-radius: 4px;">No Video Feed / Camera Blocked</div>
            </div>
          </div>
          <div class="video-label" style="text-align:center; padding: 4px; font-weight: bold; background: var(--bg-panel);">${escapeHtml(r.full_name)}</div>
        `;
        videoGrid.appendChild(tile);
      }
      if (tile) {
        if (r.status !== "in_progress") {
          tile.remove();
          delete frameTimestamps[r.user_id];
        } else {
          tile.style.border = r.strikes > 0 ? "2px solid var(--danger)" : "2px solid var(--success)";
        }
      }
    });
  }

  let wsRef = null;

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/admin?token=${encodeURIComponent(token)}`);

    ws.onopen = () => {
      connDot.classList.add("live");
      connLabel.textContent = "Live";
    };
    ws.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "candidate_update") applyWsUpdate(msg);
        else if (msg.type === "proctor_frame") {
          const img = document.getElementById(`video-img-${msg.user_id}`);
          if (img) {
            img.src = msg.frame;
            img.style.display = "block";
            const fallback = document.getElementById(`video-fallback-${msg.user_id}`);
            if (fallback) fallback.style.display = "none";
            frameTimestamps[msg.user_id] = Date.now();
          }
        }
      } catch (_) { /* ignore malformed frame */ }
    };
    ws.onclose = () => {
      connDot.classList.remove("live");
      connLabel.textContent = "Reconnecting…";
      setTimeout(connectWebSocket, 2000);
    };
    ws.onerror = () => ws.close();
    wsRef = ws;
  }

  fetchSnapshot();
  setInterval(fetchSnapshot, 5000);
  setInterval(() => {
    let changed = false;
    candidateSessions.forEach((r) => {
      if (r.status === "in_progress") {
        if (r.time_remaining_seconds > 0) {
          r.time_remaining_seconds--;
          r.last_activity_seconds_ago = (r.last_activity_seconds_ago || 0) + 1;
          changed = true;
        }
        
        // Fallback check
        const lastTime = frameTimestamps[r.user_id] || 0;
        if (Date.now() - lastTime > 3000) {
          const img = document.getElementById(`video-img-${r.user_id}`);
          const fallback = document.getElementById(`video-fallback-${r.user_id}`);
          if (img && fallback && img.style.display !== "none") {
            img.style.display = "none";
            fallback.style.display = "flex";
          }
        }
      }
    });
    if (changed) renderMonitor();
  }, 1000);
  connectWebSocket();

  // ============================================================
  // Manage users — setters & candidates
  // ============================================================
  const userMsg = document.getElementById("userActionMessage");
  const setterTableBody = document.getElementById("setterTableBody");
  const candidateUsersTableBody = document.getElementById("candidateUsersTableBody");

  async function loadUsers() {
    try {
      const [setters, candidates] = await Promise.all([
        api("/admin/users?role=setter", { headers: authHeaders }),
        api("/admin/users?role=candidate", { headers: authHeaders }),
      ]);
      renderSetters(setters);
      renderCandidates(candidates);
    } catch (err) {
      flash(userMsg, err.message);
    }
  }

  function renderSetters(rows) {
    if (!rows.length) {
      setterTableBody.innerHTML = `<tr><td colspan="4"><div class="empty-state">No question setters yet.</div></td></tr>`;
      return;
    }
    setterTableBody.innerHTML = rows.map((u) => `
      <tr data-id="${u.id}">
        <td>${escapeHtml(u.full_name)}</td>
        <td>${escapeHtml(u.email)}</td>
        <td><span class="status-pill ${u.is_active ? "active" : "inactive"}">${u.is_active ? "active" : "disabled"}</span></td>
        <td class="action-cell">
          <button class="btn btn-sm" data-action="reset" data-id="${u.id}">Reset password</button>
          <button class="btn btn-sm" data-action="toggle" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? "Disable" : "Enable"}</button>
        </td>
      </tr>
    `).join("");
  }

  function renderCandidates(rows) {
    if (!rows.length) {
      candidateUsersTableBody.innerHTML = `<tr><td colspan="5"><div class="empty-state">No candidates yet.</div></td></tr>`;
      return;
    }
    candidateUsersTableBody.innerHTML = rows.map((u) => {
      const attempt = u.latest_session_status || "no_attempt";
      const canReactivate = ["disqualified", "completed", "expired"].includes(attempt);
      return `
      <tr data-id="${u.id}">
        <td>${escapeHtml(u.full_name)}</td>
        <td>${escapeHtml(u.email)}</td>
        <td><span class="status-pill ${u.is_active ? "active" : "inactive"}">${u.is_active ? "active" : "disabled"}</span></td>
        <td><span class="status-pill ${attempt}">${attempt.replace("_", " ")}</span></td>
        <td>${u.latest_session_score !== null && u.latest_session_score !== undefined ? u.latest_session_score : '—'}</td>
        <td class="action-cell">
          <button class="btn btn-sm" data-action="reset" data-id="${u.id}">Reset password</button>
          <button class="btn btn-sm" data-action="toggle" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? "Disable" : "Enable"}</button>
          ${canReactivate ? `<button class="btn btn-primary btn-sm" data-action="reactivate" data-id="${u.id}">Allow retake</button>` : ""}
        </td>
      </tr>
    `;
    }).join("");
  }

  // --- Bulk User Import ---
  const bulkUserImportForm = document.getElementById("bulkUserImportForm");
  const showBulkUserImportBtn = document.getElementById("showBulkUserImportBtn");
  if (showBulkUserImportBtn) {
    showBulkUserImportBtn.addEventListener("click", () => bulkUserImportForm.classList.add("visible"));
    bulkUserImportForm.querySelector('[data-cancel]').addEventListener("click", () => bulkUserImportForm.classList.remove("visible"));
    
    document.getElementById("submitBulkUserImportBtn").addEventListener("click", async () => {
      const fileInput = document.getElementById("csvUserFile");
      if (!fileInput.files[0]) {
        flash(userMsg, "Please select a CSV file first.");
        return;
      }
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      try {
        const res = await api("/admin/users/upload", { method: "POST", headers: authHeaders, body: formData });
        flash(userMsg, `Imported ${res.inserted} user(s) successfully. Default password is DefaultPass@2026`, true);
        bulkUserImportForm.classList.remove("visible");
        fileInput.value = "";
        loadUsers();
      } catch (err) {
        flash(userMsg, err.message);
      }
    });
  }

  document.getElementById("panel-users").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;

    try {
      if (action === "reset") {
        if (!confirm("Generate a new temporary password for this user? Their current password will stop working immediately.")) return;
        const res = await api(`/admin/users/${id}/reset-password`, { method: "POST", headers: authHeaders });
        showCredModal("Password reset", res.email, res.temporary_password);
        loadUsers();
      } else if (action === "toggle") {
        const currentlyActive = btn.dataset.active === "true";
        await api(`/admin/users/${id}`, {
          method: "PATCH", headers: jsonHeaders,
          body: JSON.stringify({ is_active: !currentlyActive }),
        });
        flash(userMsg, currentlyActive ? "Account disabled." : "Account re-enabled.", true);
        loadUsers();
      } else if (action === "reactivate") {
        if (!confirm("Clear this candidate's previous exam attempt so they can sign in and take the exam again?")) return;
        const res = await api(`/admin/users/${id}/reactivate`, { method: "POST", headers: authHeaders });
        flash(userMsg, res.message, true);
        loadUsers();
      }
    } catch (err) {
      flash(userMsg, err.message);
    }
  });

  function wireCreateForm({ addBtnId, formId, nameId, emailId, submitBtnId, role, title }) {
    const form = document.getElementById(formId);
    document.getElementById(addBtnId).addEventListener("click", () => {
      form.classList.add("visible");
    });
    form.querySelector('[data-cancel]').addEventListener("click", () => {
      form.classList.remove("visible");
      document.getElementById(nameId).value = "";
      document.getElementById(emailId).value = "";
    });
    document.getElementById(submitBtnId).addEventListener("click", async () => {
      const full_name = document.getElementById(nameId).value.trim();
      const email = document.getElementById(emailId).value.trim();
      if (!full_name || !email) {
        flash(userMsg, "Name and email are required.");
        return;
      }
      try {
        const res = await api("/admin/users", {
          method: "POST", headers: jsonHeaders,
          body: JSON.stringify({ full_name, email, role }),
        });
        form.classList.remove("visible");
        document.getElementById(nameId).value = "";
        document.getElementById(emailId).value = "";
        showCredModal(title, res.user.email, res.temporary_password);
        loadUsers();
      } catch (err) {
        flash(userMsg, err.message);
      }
    });
  }

  wireCreateForm({
    addBtnId: "addSetterBtn", formId: "createSetterForm",
    nameId: "newSetterName", emailId: "newSetterEmail",
    submitBtnId: "submitSetterBtn", role: "setter",
    title: "Question setter account created",
  });
  wireCreateForm({
    addBtnId: "addCandidateBtn", formId: "createCandidateForm",
    nameId: "newCandidateName", emailId: "newCandidateEmail",
    submitBtnId: "submitCandidateBtn", role: "candidate",
    title: "Candidate account created",
  });

  // ============================================================
  // Question bank
  // ============================================================
  const questionMsg = document.getElementById("questionActionMessage");
  const questionsTableBody = document.getElementById("questionsTableBody");
  const questionForm = document.getElementById("questionForm");
  const qOptionsList = document.getElementById("qOptionsList");
  const qOptionsWrap = document.getElementById("qOptionsWrap");
  const qType = document.getElementById("qType");

  function addOptionRow(text = "", checked = false) {
    const row = document.createElement("div");
    row.className = "option-input-row";
    row.innerHTML = `
      <input type="radio" name="correctOption">
      <input type="text" placeholder="Option text" value="${escapeHtml(text)}">
      <button type="button" class="btn btn-sm" data-remove-option>✕</button>
    `;
    row.querySelector('input[type="radio"]').checked = checked;
    row.querySelector('[data-remove-option]').addEventListener("click", () => row.remove());
    qOptionsList.appendChild(row);
  }

  document.getElementById("addOptionBtn").addEventListener("click", () => addOptionRow());

  qType.addEventListener("change", () => {
    qOptionsWrap.style.display = qType.value === "mcq" ? "block" : "none";
  });

  let allDomains = [];
  let allQuestionsCache = [];

  async function loadMeta() {
    try {
      const meta = await api("/questions/meta", { headers: authHeaders });
      allDomains = meta.domains;
      const optionsHtml = allDomains.map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join("");
      qDomain.innerHTML = optionsHtml;
      domainFilter.innerHTML = `<option value="">All domains</option>` + optionsHtml;
    } catch (err) {
      flash(questionMsg, err.message);
    }
  }

  function resetQuestionForm() {
    document.getElementById("questionId").value = "";
    document.getElementById("qOrderNo").value = "";
    document.getElementById("qText").value = "";
    if (allDomains.length) qDomain.value = allDomains[0];
    qType.value = "mcq";
    qOptionsWrap.style.display = "block";
    qOptionsList.innerHTML = "";
    addOptionRow();
    addOptionRow();
  }

  document.getElementById("addQuestionBtn").addEventListener("click", () => {
    resetQuestionForm();
    questionForm.classList.add("visible");
    bulkImportForm.classList.remove("visible");
  });
  questionForm.querySelector('[data-cancel]').addEventListener("click", () => {
    questionForm.classList.remove("visible");
  });

  document.getElementById("submitQuestionBtn").addEventListener("click", async () => {
    const id = document.getElementById("questionId").value;
    const order_no = parseInt(document.getElementById("qOrderNo").value, 10);
    const domain = qDomain.value;
    const question_text = document.getElementById("qText").value.trim();
    const question_type = qType.value;

    if (!order_no || !question_text || !domain) {
      flash(questionMsg, "Order number, domain, and question text are required.");
      return;
    }

    let options = null;
    let correct_option = null;
    if (question_type === "mcq") {
      const rows = Array.from(qOptionsList.querySelectorAll(".option-input-row"));
      options = rows.map((r) => r.querySelector('input[type="text"]').value.trim()).filter(Boolean);
      const checkedIdx = rows.findIndex((r) => r.querySelector('input[type="radio"]').checked);
      if (options.length < 2) {
        flash(questionMsg, "Add at least 2 options for a multiple-choice question.");
        return;
      }
      if (checkedIdx === -1) {
        flash(questionMsg, "Select which option is correct.");
        return;
      }
      correct_option = checkedIdx;
    }

    const payload = { order_no, domain, question_type, question_text, options, correct_option };

    try {
      if (id) {
        await api(`/questions/${id}`, { method: "PUT", headers: jsonHeaders, body: JSON.stringify(payload) });
        flash(questionMsg, "Question updated.", true);
      } else {
        await api("/questions", { method: "POST", headers: jsonHeaders, body: JSON.stringify(payload) });
        flash(questionMsg, "Question added.", true);
      }
      questionForm.classList.remove("visible");
      loadQuestions();
    } catch (err) {
      flash(questionMsg, err.message);
    }
  });

  async function loadQuestions() {
    try {
      allQuestionsCache = await api("/questions", { headers: authHeaders });
      applyFilters();
    } catch (err) {
      flash(questionMsg, err.message);
    }
  }

  function applyFilters() {
    const domain = domainFilter.value;
    const search = questionSearch.value.trim().toLowerCase();
    let filtered = allQuestionsCache;
    if (domain) filtered = filtered.filter((q) => q.domain === domain);
    if (search) filtered = filtered.filter((q) => q.question_text.toLowerCase().includes(search));
    renderQuestions(filtered);
  }

  domainFilter.addEventListener("change", applyFilters);
  let searchDebounce;
  questionSearch.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(applyFilters, 200);
  });

  function renderQuestions(questions) {
    if (!questions.length) {
      questionsTableBody.innerHTML = `<tr><td colspan="5"><div class="empty-state">No questions match.</div></td></tr>`;
      return;
    }
    questionsTableBody.innerHTML = questions.map((q) => `
      <tr>
        <td class="progress-cell">${q.order_no}</td>
        <td>${escapeHtml(q.domain)}</td>
        <td><span class="status-pill in_progress">${q.question_type}</span></td>
        <td>${escapeHtml(q.question_text)}</td>
        <td class="action-cell">
          <button class="btn btn-sm" data-edit="${q.id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-delete="${q.id}">Delete</button>
        </td>
      </tr>
    `).join("");

    questions.forEach((q) => {
      questionsTableBody.querySelector(`[data-edit="${q.id}"]`).addEventListener("click", () => {
        document.getElementById("questionId").value = q.id;
        document.getElementById("qOrderNo").value = q.order_no;
        qDomain.value = q.domain;
        document.getElementById("qText").value = q.question_text;
        qType.value = q.question_type;
        qOptionsWrap.style.display = q.question_type === "mcq" ? "block" : "none";
        qOptionsList.innerHTML = "";
        (q.options || []).forEach((opt, i) => addOptionRow(opt, i === q.correct_option));
        if (q.question_type === "mcq" && (!q.options || q.options.length === 0)) {
          addOptionRow(); addOptionRow();
        }
        questionForm.classList.add("visible");
        bulkImportForm.classList.remove("visible");
        questionForm.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      questionsTableBody.querySelector(`[data-delete="${q.id}"]`).addEventListener("click", async () => {
        if (!confirm("Delete this question? This can't be undone.")) return;
        try {
          await api(`/questions/${q.id}`, { method: "DELETE", headers: authHeaders });
          flash(questionMsg, "Question deleted.", true);
          loadQuestions();
        } catch (err) {
          flash(questionMsg, err.message);
        }
      });
    });
  }

  // ---------------- Bulk import (CSV) ----------------
  document.getElementById("showBulkImportBtn").addEventListener("click", () => {
    bulkImportForm.classList.add("visible");
    questionForm.classList.remove("visible");
  });
  bulkImportForm.querySelector('[data-cancel]').addEventListener("click", () => {
    bulkImportForm.classList.remove("visible");
  });

  document.getElementById("downloadTemplateBtn").addEventListener("click", async () => {
    try {
      const res = await fetch("/questions/bulk-import/template", { headers: authHeaders });
      if (!res.ok) throw new Error("Could not download template");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "question_bank_template.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      flash(questionMsg, err.message);
    }
  });

  document.getElementById("submitBulkImportBtn").addEventListener("click", async () => {
    const bulkFile = document.getElementById("bulkFile");
    const domainInput = document.getElementById("bulkImportDomain").value.trim();
    const phaseInput = document.getElementById("bulkImportPhase").value;
    
    const file = bulkFile.files[0];
    if (!file || !domainInput) {
      flash(questionMsg, "Choose a file and provide domain.");
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("domain", domainInput);
    formData.append("phase", phaseInput);
    
    try {
      const result = await api("/admin/questions/upload", { method: "POST", headers: authHeaders, body: formData });
      let msg = `Imported ${result.inserted} question(s) successfully.`;
      flash(questionMsg, msg, true);
      bulkImportForm.classList.remove("visible");
      bulkFile.value = "";
      loadQuestions();
    } catch (err) {
      flash(questionMsg, err.message);
    }
  });

  loadMeta().then(loadQuestions);
})();
