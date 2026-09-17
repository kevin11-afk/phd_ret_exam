(() => {
  const token = localStorage.getItem("access_token");
  const role = localStorage.getItem("role");
  if (!token || role !== "candidate") {
    window.location.href = "/login";
    return;
  }

  // ---------- DOM refs ----------
  const gateScreen = document.getElementById("gateScreen");
  const examScreen = document.getElementById("examScreen");
  const terminalScreen = document.getElementById("terminalScreen");
  const terminalCard = document.getElementById("terminalCard");
  const terminalTitle = document.getElementById("terminalTitle");
  const terminalBody = document.getElementById("terminalBody");
  const beginExamBtn = document.getElementById("beginExamBtn");
  const gateError = document.getElementById("gateError");
  const candidateName = document.getElementById("candidateName");

  const timerDisplay = document.getElementById("timerDisplay");
  const strikeDotEls = document.querySelectorAll(".strike-dot");
  const questionMeta = document.getElementById("questionMeta");
  const partBanner = document.getElementById("partBanner");
  const questionText = document.getElementById("questionText");
  const mcqOptions = document.getElementById("mcqOptions");
  const writtenWrap = document.getElementById("writtenAnswerWrap");
  const writtenInput = document.getElementById("writtenAnswerInput");
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");
  const reviewBtn = document.getElementById("reviewBtn");
  const submitExamBtn = document.getElementById("submitExamBtn");
  const navGrid = document.getElementById("navGrid");

  const webcamPip = document.getElementById("webcamPip");
  const webcamVideo = document.getElementById("webcamVideo");
  const webcamStatus = document.getElementById("webcamStatus");

  const strikeModal = document.getElementById("strikeModal");
  const strikeModalCount = document.getElementById("strikeModalCount");
  const strikeModalTitle = document.getElementById("strikeModalTitle");
  const strikeModalBody = document.getElementById("strikeModalBody");
  const strikeModalContinue = document.getElementById("strikeModalContinue");

  const intermissionScreen = document.getElementById("intermissionScreen");
  const intermissionTimerDisplay = document.getElementById("intermissionTimer");
  const domainSelectionWrap = document.getElementById("domainSelectionWrap");
  const domainSelect = document.getElementById("domainSelect");
  const submitDomainBtn = document.getElementById("submitDomainBtn");
  const skipRestBtn = document.getElementById("skipRestBtn");

  candidateName.textContent = localStorage.getItem("full_name") || "";

  // ---------- API helper ----------
  const urlParams = new URLSearchParams(window.location.search);
  const isDevMode = urlParams.get('dev') === 'true';

  if (isDevMode && skipRestBtn) {
    skipRestBtn.style.display = "block";
    skipRestBtn.addEventListener("click", () => {
      remainingSeconds = 1;
    });
  }

  // ---------- State ----------
  let current = null;       // current question payload from server
  let navState = [];        // nav grid array from /exam/state
  let maxStrikes = 3;
  let remainingSeconds = 0;
  let currentPart = 1;
  let examActive = false;
  let tickHandle = null;
  let syncHandle = null;
  let writtenSaveTimer = null;
  let lastViolationAt = 0;

  const VIOLATION_LABELS = {
    tab_switch: "You switched away from the exam tab.",
    fullscreen_exit: "You left full-screen mode.",
    window_resize: "You resized the browser window or exited full-screen.",
    copy_attempt: "Copying exam content isn't permitted.",
    right_click: "The right-click menu is disabled during the exam.",
    face_not_detected: "Your face wasn't visible to the camera.",
    multiple_faces: "More than one person was visible in frame.",
    looking_away: "You looked away from the screen for an extended period.",
  };

  async function api(path, method = "GET", body = null) {
    let finalPath = path;
    if (isDevMode && ["/exam/start", "/exam/submit-part1", "/exam/select-domain"].includes(path)) {
      finalPath += "?dev=true";
    }
    const res = await fetch(finalPath, {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* no body */ }
    return { ok: res.ok, status: res.status, data };
  }

  // ---------- Gate / start ----------
  beginExamBtn.addEventListener("click", async () => {
    gateError.classList.remove("visible");
    try {
      await document.documentElement.requestFullscreen();
    } catch (err) {
      gateError.textContent = "Full-screen permission is required to begin the exam.";
      gateError.classList.add("visible");
      return;
    }
    await startExam();
  });

  async function startExam() {
    const res = await api("/exam/start", "POST");
    if (!res.ok) {
      gateError.textContent = res.data?.detail || "Could not start the exam.";
      gateError.classList.add("visible");
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      return;
    }

    gateScreen.style.display = "none";
    examScreen.style.display = "flex";
    webcamPip.style.display = "block";
    examActive = true;

    renderQuestion(res.data);
    await refreshState();

    await Proctor.init(webcamVideo, webcamStatus, reportViolation);
    await Proctor.start();

    startTimer();
    attachViolationListeners();
    window.addEventListener("beforeunload", beforeUnloadHandler);
  }

  function beforeUnloadHandler(e) {
    if (examActive) {
      e.preventDefault();
      e.returnValue = "";
    }
  }

  // ---------- Rendering ----------
  function renderQuestion(q) {
    current = q;
    partBanner.textContent = q.part_label;
    partBanner.className = "part-banner part-" + q.part;
    questionMeta.textContent = `Question ${q.index + 1} of ${q.total}`;
    questionText.textContent = q.question_text;

    prevBtn.disabled = q.index === 0;
    nextBtn.disabled = q.index === q.total - 1;
    reviewBtn.textContent = q.marked_for_review ? "Unmark review" : "Mark for review";
    
    if (q.index === q.total - 1) {
      submitExamBtn.style.display = "inline-block";
    } else {
      submitExamBtn.style.display = "none";
    }

    if (q.question_type === "mcq") {
      writtenWrap.style.display = "none";
      mcqOptions.style.display = "block";
      mcqOptions.innerHTML = "";
      q.options.forEach((opt, idx) => {
        const row = document.createElement("label");
        row.className = "option-row" + (q.selected_display_index === idx ? " selected" : "");
        row.innerHTML = `
          <input type="radio" name="mcq_option" ${q.selected_display_index === idx ? "checked" : ""}>
          <span class="option-text"></span>
        `;
        row.querySelector(".option-text").textContent = opt;
        row.querySelector("input").addEventListener("change", () => selectOption(idx));
        mcqOptions.appendChild(row);
      });
    } else {
      mcqOptions.style.display = "none";
      writtenWrap.style.display = "block";
      writtenInput.value = q.written_text || "";
    }

    highlightNavCurrent(q.index);
  }

  function highlightNavCurrent(index) {
    document.querySelectorAll(".nav-cell").forEach((cell) => {
      cell.classList.toggle("current", Number(cell.dataset.index) === index);
    });
  }

  function buildNavGrid() {
    navGrid.innerHTML = "";
    let lastPart = null;
    navState.forEach((n) => {
      if (lastPart !== null && n.part !== lastPart) {
        const divider = document.createElement("div");
        divider.className = "nav-part-divider";
        navGrid.appendChild(divider);
      }
      lastPart = n.part;
      const cell = document.createElement("div");
      cell.className = "nav-cell";
      cell.dataset.index = n.index;
      cell.textContent = n.index + 1;
      if (n.answered) cell.classList.add("answered");
      if (n.marked_for_review) cell.classList.add("review");
      if (current && current.index === n.index) cell.classList.add("current");
      cell.addEventListener("click", () => jumpTo(n.index));
      navGrid.appendChild(cell);
    });
  }

  // ---------- Answers ----------
  async function selectOption(displayIndex) {
    document.querySelectorAll(".option-row").forEach((r, i) => r.classList.toggle("selected", i === displayIndex));
    await api("/exam/answer", "POST", { index: current.index, selected_display_index: displayIndex });
    await refreshState();
  }

  writtenInput.addEventListener("input", () => {
    clearTimeout(writtenSaveTimer);
    writtenSaveTimer = setTimeout(async () => {
      await api("/exam/answer", "POST", { index: current.index, written_text: writtenInput.value });
      await refreshState();
    }, 800);
  });

  reviewBtn.addEventListener("click", async () => {
    const newVal = !current.marked_for_review;
    current.marked_for_review = newVal;
    reviewBtn.textContent = newVal ? "Unmark review" : "Mark for review";
    await api("/exam/answer", "POST", { index: current.index, marked_for_review: newVal });
    await refreshState();
  });

  // ---------- Navigation ----------
  async function navigate(direction) {
    const res = await api("/exam/navigate", "POST", { direction });
    if (res.ok) renderQuestion(res.data);
    await refreshState();
  }
  async function jumpTo(index) {
    if (current && index === current.index) return;
    const res = await api("/exam/navigate", "POST", { jump_to: index });
    if (res.ok) renderQuestion(res.data);
    await refreshState();
  }
  prevBtn.addEventListener("click", () => navigate("prev"));
  nextBtn.addEventListener("click", () => navigate("next"));

  submitExamBtn.addEventListener("click", async () => {
    if (!confirm("Submit this part? You cannot return to these questions.")) return;
    if (currentPart === 1) {
      const res = await api("/exam/submit-part1", "POST");
      if (res.ok) await refreshState();
    } else {
      const res = await api("/exam/submit", "POST");
      if (res.ok) endExam("completed");
    }
  });

  // ---------- Domain selection ----------
  async function autoSelectDomain(domain) {
    if (!domain) {
      if (domainSelect.options.length > 1) {
        domain = domainSelect.options[1].value;
      } else {
        domain = "Computer Science & Engineering";
      }
    }
    domainSelect.value = domain;
    domainSelect.disabled = true;
    submitDomainBtn.disabled = true;

    const res = await api("/exam/select-domain", "POST", { domain });
    if (!res.ok || (res.data && res.data.ok === false)) {
      console.warn("Auto domain selection failed:", res.data?.error || res.data?.detail);
      domainSelect.disabled = false;
      submitDomainBtn.disabled = false;
    } else {
      await refreshState();
    }
  }

  submitDomainBtn.addEventListener("click", async () => {
    const domain = domainSelect.value;
    if (!domain) {
      alert("Please select a domain.");
      return;
    }
    if (!confirm(`WARNING: You are about to select the domain '${domain}'. Once submitted, this selection is final and CANNOT be changed under any circumstances. Are you sure you want to proceed?`)) return;
    
    // Lock the dropdown while submitting
    domainSelect.disabled = true;
    submitDomainBtn.disabled = true;

    const res = await api("/exam/select-domain", "POST", { domain });
    if (!res.ok || (res.data && res.data.ok === false)) {
      alert(res.data?.error || res.data?.detail || "Failed to select domain.");
      domainSelect.disabled = false;
      submitDomainBtn.disabled = false;
      return;
    }
    await refreshState();
  });

  // ---------- State sync / timer ----------
  async function refreshState() {
    const res = await api("/exam/state");
    if (!res.ok) return;
    const s = res.data;
    navState = s.nav;
    maxStrikes = s.max_strikes;
    remainingSeconds = s.time_remaining_seconds;
    currentPart = s.current_part;
    updateStrikeDots(s.strikes);
    buildNavGrid();
    updateTimerDisplay();

    if (currentPart === 2) {
      examScreen.style.pointerEvents = "none";
      intermissionScreen.style.display = "flex";
    } else {
      examScreen.style.pointerEvents = "auto";
      intermissionScreen.style.display = "none";
      if (currentPart === 3) {
        submitExamBtn.textContent = "Submit Exam";
        if (current && current.part === 1) {
          jumpTo(s.current_index);
        }
      } else {
        submitExamBtn.textContent = "Submit Phase 1";
      }
    }

    if (s.status !== "in_progress") {
      if (s.status === "expired") endExam("expired");
      else if (s.status === "disqualified") endExam("disqualified");
      else if (s.status === "completed") endExam("completed");
    }
  }

  function updateStrikeDots(count) {
    strikeDotEls.forEach((dot, i) => dot.classList.toggle("filled", i < count));
  }

  function updateTimerDisplay() {
    const m = Math.floor(remainingSeconds / 60).toString().padStart(2, "0");
    const s = (remainingSeconds % 60).toString().padStart(2, "0");
    const text = `${m}:${s}`;
    
    timerDisplay.textContent = text;
    timerDisplay.classList.toggle("low", remainingSeconds <= 300 && remainingSeconds > 60);
    timerDisplay.classList.toggle("critical", remainingSeconds <= 60);

    if (currentPart === 2) {
      intermissionTimerDisplay.textContent = text;
    }
  }

  function startTimer() {
    if (tickHandle) clearInterval(tickHandle);
    if (syncHandle) clearInterval(syncHandle);

    tickHandle = setInterval(async () => {
      if (remainingSeconds > 0) {
        remainingSeconds -= 1;
        updateTimerDisplay();
      }
      if (remainingSeconds === 0 && examActive) {
        if (currentPart === 1) {
          // Task 4: Move smoothly into intermission instead of endExam("expired")
          await api("/exam/submit-part1", "POST");
          await refreshState();
        } else if (currentPart === 2) {
          // Task 3: Auto-select domain skipping confirm() modal
          let chosen = domainSelect.value;
          if (!chosen && domainSelect.options.length > 1) {
            chosen = domainSelect.options[1].value;
          }
          await autoSelectDomain(chosen || "Computer Science & Engineering");
        } else if (currentPart === 3) {
          // Task 5: Submit exam before marking expired
          await api("/exam/submit", "POST");
          endExam("expired");
        } else {
          endExam("expired");
        }
      }
    }, 1000);
    syncHandle = setInterval(refreshState, 5000);
  }

  // ---------- Violations ----------
  function attachViolationListeners() {
    document.addEventListener("visibilitychange", onVisibilityChange);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("copy", onCopyAttempt);
    document.addEventListener("contextmenu", onContextMenu);
    window.addEventListener("resize", onWindowResize);
  }

  function detachViolationListeners() {
    document.removeEventListener("visibilitychange", onVisibilityChange);
    document.removeEventListener("fullscreenchange", onFullscreenChange);
    document.removeEventListener("copy", onCopyAttempt);
    document.removeEventListener("contextmenu", onContextMenu);
    window.removeEventListener("resize", onWindowResize);
    window.removeEventListener("beforeunload", beforeUnloadHandler);
  }

  function onVisibilityChange() {
    if (document.hidden && examActive) reportViolation("tab_switch");
  }

  function onFullscreenChange() {
    if (!document.fullscreenElement && examActive) reportViolation("fullscreen_exit");
  }

  function onWindowResize() {
    if (!examActive) return;
    if (!document.fullscreenElement) {
      reportViolation("fullscreen_exit");
    } else if (window.outerWidth < screen.availWidth * 0.85 || window.outerHeight < screen.availHeight * 0.85) {
      reportViolation("window_resize");
    }
  }

  function onCopyAttempt(e) {
    if (examActive) { e.preventDefault(); reportViolation("copy_attempt"); }
  }

  function onContextMenu(e) {
    if (examActive) { e.preventDefault(); reportViolation("right_click"); }
  }

  async function reportViolation(type) {
    if (!examActive) return;
    const now = Date.now();
    if (now - lastViolationAt < 2000) return;
    lastViolationAt = now;

    const res = await api("/exam/violation", "POST", { violation_type: type });
    if (!res.ok) return;
    const { strikes, action } = res.data;
    updateStrikeDots(strikes);

    if (action === "warn") {
      showStrikeModal(strikes, type);
    } else if (action === "disqualify") {
      endExam("disqualified");
    }
  }

  function showStrikeModal(strikeCount, type) {
    strikeModalCount.textContent = `Warning: Malpractice Detected`;
    strikeModalTitle.textContent = "Infraction recorded";
    strikeModalBody.textContent = (VIOLATION_LABELS[type] || "An exam infraction was recorded.") + " Event logged to Admin.";
    strikeModal.classList.add("visible");
  }

  strikeModalContinue.addEventListener("click", async () => {
    strikeModal.classList.remove("visible");
    if (!document.fullscreenElement) {
      try { await document.documentElement.requestFullscreen(); } catch (_) { /* best effort */ }
    }
  });

  // ---------- Terminal states ----------
  function endExam(reason) {
    if (!examActive && terminalScreen.style.display === "flex") return;
    examActive = false;

    clearInterval(tickHandle);
    clearInterval(syncHandle);
    Proctor.stop();
    detachViolationListeners();
    strikeModal.classList.remove("visible");
    webcamPip.style.display = "none";
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});

    examScreen.style.display = "none";
    terminalScreen.style.display = "flex";

    const copy = {
      completed: {
        title: "Exam submitted",
        body: "Your responses have been recorded. You may close this window.",
        cls: "completed",
      },
      expired: {
        title: "Time's up",
        body: "The exam duration has ended and your responses were submitted automatically.",
        cls: "",
      },
      disqualified: {
        title: "Attempt ended",
        body: "Your exam was ended after repeated infractions. This decision is final and your access has been revoked.",
        cls: "",
      },
    }[reason] || { title: "Exam ended", body: "", cls: "" };

    terminalTitle.textContent = copy.title;
    terminalBody.textContent = copy.body;
    terminalCard.className = "terminal-card" + (copy.cls ? " " + copy.cls : "");

    localStorage.removeItem("access_token");
  }

  // ---------- Resume session on load (Task 9) ----------
  async function checkActiveSession() {
    try {
      const res = await api("/exam/state");
      if (res.ok && res.data && res.data.status === "in_progress") {
        const s = res.data;
        gateScreen.style.display = "none";
        examScreen.style.display = "flex";
        webcamPip.style.display = "block";
        examActive = true;

        const qRes = await api(`/exam/question/${s.current_index}`);
        if (qRes.ok && qRes.data) {
          renderQuestion(qRes.data);
        }

        await refreshState();

        await Proctor.init(webcamVideo, webcamStatus, reportViolation);
        await Proctor.start();

        startTimer();
        attachViolationListeners();
        window.addEventListener("beforeunload", beforeUnloadHandler);
      }
    } catch (err) {
      console.warn("Could not check/resume active session:", err);
    }
  }

  checkActiveSession();
})();
