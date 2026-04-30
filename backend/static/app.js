const DOM = {
    modeAudio: document.getElementById("modeAudio"),
    modeVideo: document.getElementById("modeVideo"),
    modeEdit: document.getElementById("modeEdit"),
    audioForm: document.getElementById("audioForm"),
    videoForm: document.getElementById("videoForm"),
    editForm: document.getElementById("editForm"),
    audioUploadZone: document.getElementById("audioUploadZone"),
    videoUploadZone: document.getElementById("videoUploadZone"),
    editUploadZone: document.getElementById("editUploadZone"),
    audioFileInput: document.getElementById("audioFileInput"),
    videoFileInput: document.getElementById("videoFileInput"),
    editFileInput: document.getElementById("editFileInput"),
    btnBrowseAudio: document.getElementById("btnBrowseAudio"),
    btnBrowseVideo: document.getElementById("btnBrowseVideo"),
    btnBrowseEdit: document.getElementById("btnBrowseEdit"),
    audioFileInfo: document.getElementById("audioFileInfo"),
    videoFileInfo: document.getElementById("videoFileInfo"),
    editFileInfo: document.getElementById("editFileInfo"),
    btnStartAudio: document.getElementById("btnStartAudio"),
    btnStartVideo: document.getElementById("btnStartVideo"),
    btnStartEdit: document.getElementById("btnStartEdit"),
    sourceUrl: document.getElementById("sourceUrl"),
    editSourceUrl: document.getElementById("editSourceUrl"),
    zeroState: document.getElementById("zeroState"),
    processingEngine: document.getElementById("processingEngine"),
    currentJobId: document.getElementById("currentJobId"),
    resultPlayer: document.getElementById("resultPlayer"),
    resultMessage: document.getElementById("resultMessage"),
    resultMeta: document.getElementById("resultMeta"),
    audioPlayer: document.getElementById("audioPlayer"),
    videoPlayer: document.getElementById("videoPlayer"),
    btnDownload: document.getElementById("btnDownload"),
    toast: document.getElementById("toast"),
    toastMessage: document.getElementById("toastMessage"),
    // Narrator gender radio buttons
    genderFemale: document.getElementById("genderFemale"),
    genderMale: document.getElementById("genderMale"),
};

const orderedSteps = ["parsing", "analyzing", "voice", "fx", "mixing"];
const statusPaths = {
    pending: "parsing",
    parsing: "parsing",
    analyzing: "analyzing",
    generating_voice: "voice",
    generating_fx: "fx",
    mixing: "mixing",
    done: "done",
};

let activeMode = "audio";
let selectedAudioFile = null;
let selectedVideoFile = null;
let selectedEditFile = null;
let currentJobInterval = null;

DOM.modeAudio.addEventListener("click", () => setMode("audio"));
DOM.modeVideo.addEventListener("click", () => setMode("video"));
DOM.modeEdit.addEventListener("click", () => setMode("edit"));
DOM.btnBrowseAudio.addEventListener("click", () => DOM.audioFileInput.click());
DOM.btnBrowseVideo.addEventListener("click", () => DOM.videoFileInput.click());
DOM.btnBrowseEdit.addEventListener("click", () => DOM.editFileInput.click());
DOM.audioFileInput.addEventListener("change", (event) => {
    if (event.target.files.length) {
        handleAudioFileSelect(event.target.files[0]);
    }
});
DOM.videoFileInput.addEventListener("change", (event) => {
    if (event.target.files.length) {
        handleVideoFileSelect(event.target.files[0]);
    }
});
DOM.editFileInput.addEventListener("change", (event) => {
    if (event.target.files.length) {
        handleEditFileSelect(event.target.files[0]);
    }
});
DOM.sourceUrl.addEventListener("input", () => {
    if (DOM.sourceUrl.value.trim()) {
        selectedVideoFile = null;
        DOM.videoFileInput.value = "";
        DOM.videoFileInfo.classList.add("hidden");
        DOM.videoFileInfo.textContent = "";
    }
});
DOM.editSourceUrl.addEventListener("input", () => {
    if (DOM.editSourceUrl.value.trim()) {
        selectedEditFile = null;
        DOM.editFileInput.value = "";
        DOM.editFileInfo.classList.add("hidden");
        DOM.editFileInfo.textContent = "";
    }
});
DOM.btnStartAudio.addEventListener("click", startAudioJob);
DOM.btnStartVideo.addEventListener("click", startVideoJob);
DOM.btnStartEdit.addEventListener("click", startEditJob);

bindDropzone(DOM.audioUploadZone, handleAudioFileSelect);
bindDropzone(DOM.videoUploadZone, handleVideoFileSelect);
bindDropzone(DOM.editUploadZone, handleEditFileSelect);

function setMode(mode) {
    activeMode = mode;
    DOM.modeAudio.classList.toggle("active", mode === "audio");
    DOM.modeVideo.classList.toggle("active", mode === "video");
    DOM.modeEdit.classList.toggle("active", mode === "edit");
    DOM.audioForm.classList.toggle("hidden", mode !== "audio");
    DOM.videoForm.classList.toggle("hidden", mode !== "video");
    DOM.editForm.classList.toggle("hidden", mode !== "edit");
}

function bindDropzone(element, onSelect) {
    element.addEventListener("dragover", (event) => {
        event.preventDefault();
        element.classList.add("dragover");
    });
    element.addEventListener("dragleave", () => element.classList.remove("dragover"));
    element.addEventListener("drop", (event) => {
        event.preventDefault();
        element.classList.remove("dragover");
        if (event.dataTransfer.files.length) {
            onSelect(event.dataTransfer.files[0]);
        }
    });
}

function handleAudioFileSelect(file) {
    const validExts = [".pdf", ".epub", ".txt"];
    const filename = file.name.toLowerCase();
    if (!validExts.some((ext) => filename.endsWith(ext))) {
        showError("Audiobook chi ho tro file .pdf, .epub hoac .txt");
        return;
    }
    selectedAudioFile = file;
    DOM.audioFileInfo.textContent = `Da chon: ${file.name}`;
    DOM.audioFileInfo.classList.remove("hidden");
    DOM.btnStartAudio.disabled = false;
}

function handleVideoFileSelect(file) {
    const validExts = [".mp4", ".mov", ".mkv", ".webm", ".avi"];
    const filename = file.name.toLowerCase();
    if (!validExts.some((ext) => filename.endsWith(ext))) {
        showError("Video review chi ho tro .mp4, .mov, .mkv, .webm, .avi");
        return;
    }
    selectedVideoFile = file;
    DOM.sourceUrl.value = "";
    DOM.videoFileInfo.textContent = `Da chon: ${file.name}`;
    DOM.videoFileInfo.classList.remove("hidden");
}

function handleEditFileSelect(file) {
    const validExts = [".mp4", ".mov", ".mkv", ".webm", ".avi"];
    const filename = file.name.toLowerCase();
    if (!validExts.some((ext) => filename.endsWith(ext))) {
        showError("Video edit chi ho tro .mp4, .mov, .mkv, .webm, .avi");
        return;
    }
    selectedEditFile = file;
    DOM.editSourceUrl.value = "";
    DOM.editFileInfo.textContent = `Da chon: ${file.name}`;
    DOM.editFileInfo.classList.remove("hidden");
}

async function startAudioJob() {
    if (!selectedAudioFile) {
        showError("Hãy chọn file truyện trước khi tạo audiobook");
        return;
    }

    // Resolve narrator gender from radio buttons
    const narratorGender = document.querySelector('input[name="narratorGender"]:checked')?.value || "female";

    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    setBusy(DOM.btnStartAudio, true, "Đang khởi tạo audiobook...");
    const formData = new FormData();
    formData.append("file", selectedAudioFile);
    formData.append("start_chapter", document.getElementById("startChapter").value);
    formData.append("end_chapter", document.getElementById("endChapter").value);
    formData.append("narrator_gender", narratorGender);

    try {
        const response = await fetch("/api/upload", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Upload audiobook thất bại");
        }
        prepareJobView(payload.job_id);
        startPolling(payload.job_id);
    } catch (error) {
        showError(error.message);
    } finally {
        setBusy(DOM.btnStartAudio, false, "Tạo audiobook", "headphones");
    }
}

async function startVideoJob() {
    const sourceUrl = DOM.sourceUrl.value.trim();
    if (!selectedVideoFile && !sourceUrl) {
        showError("Can chon file video hoac dan link nguon");
        return;
    }

    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    setBusy(DOM.btnStartVideo, true, "Dang tao video review...");
    const formData = new FormData();
    if (selectedVideoFile) {
        formData.append("file", selectedVideoFile);
    }
    if (sourceUrl) {
        formData.append("source_url", sourceUrl);
    }
    formData.append("orientation", document.getElementById("videoOrientation").value);
    formData.append("max_duration_sec", document.getElementById("maxDuration").value);
    formData.append("style", "review_short");

    try {
        const response = await fetch("/api/video/review", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Tao video review that bai");
        }
        prepareJobView(payload.job_id);
        startPolling(payload.job_id);
    } catch (error) {
        showError(error.message);
    } finally {
        setBusy(DOM.btnStartVideo, false, "Tao video review", "film");
    }
}

async function startEditJob() {
    const sourceUrl = DOM.editSourceUrl.value.trim();
    if (!selectedEditFile && !sourceUrl) {
        showError("Can chon file video raw hoac dan link nguon");
        return;
    }

    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    setBusy(DOM.btnStartEdit, true, "Dang hau ky video...");
    const formData = new FormData();
    if (selectedEditFile) {
        formData.append("file", selectedEditFile);
    }
    if (sourceUrl) {
        formData.append("source_url", sourceUrl);
    }
    formData.append("orientation", document.getElementById("editOrientation").value);
    formData.append("style", document.getElementById("editStyle").value);
    formData.append("keep_full_video", "true");

    try {
        const response = await fetch("/api/video/edit", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Tao video edit that bai");
        }
        prepareJobView(payload.job_id);
        startPolling(payload.job_id);
    } catch (error) {
        showError(error.message);
    } finally {
        setBusy(DOM.btnStartEdit, false, "Tao video edit", "sparkles");
    }
}

function prepareJobView(jobId) {
    DOM.zeroState.classList.add("hidden");
    DOM.processingEngine.classList.remove("hidden");
    DOM.resultPlayer.classList.add("hidden");
    DOM.currentJobId.textContent = jobId;
    DOM.resultMeta.classList.add("hidden");
    DOM.resultMeta.innerHTML = "";
    DOM.audioPlayer.classList.add("hidden");
    DOM.videoPlayer.classList.add("hidden");
    DOM.audioPlayer.removeAttribute("src");
    DOM.videoPlayer.removeAttribute("src");
    orderedSteps.forEach((step) => {
        document.getElementById(`step-${step}`).className = "step";
        document.getElementById(`bar-${step}`).style.width = "0%";
    });
}

function startPolling(jobId) {
    if (currentJobInterval) {
        clearInterval(currentJobInterval);
    }
    currentJobInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/jobs/${jobId}`);
            const payload = await response.json();
            updateTimeline(payload.status, payload.progress);

            if (payload.status === "done") {
                clearInterval(currentJobInterval);
                showResult(payload);
            } else if (payload.status === "failed") {
                clearInterval(currentJobInterval);
                markStepError();
                showErrorModal(payload);
            }
        } catch (error) {
            console.error("Polling error:", error);
        }
    }, 2000);
}

function updateTimeline(status, progressMap) {
    const activeStepId = statusPaths[status];
    let isPast = true;

    orderedSteps.forEach((step) => {
        const stepElement = document.getElementById(`step-${step}`);
        const barElement = document.getElementById(`bar-${step}`);
        // lucide replaces <i> with <svg>
        const iconElement = stepElement.querySelector('.step-icon svg') || stepElement.querySelector('.step-icon i');

        // Reset icon animation
        if (iconElement) iconElement.classList.remove("spin");

        if (status === "done") {
            stepElement.className = "step done";
            barElement.style.width = "100%";
            return;
        }

        if (step === activeStepId) {
            isPast = false;
            stepElement.className = "step active";
            const percent = progressMap[step] || 0;
            // set width with min 5% so it's visible, and show actual % text on title if available
            barElement.style.width = `${Math.max(5, percent)}%`;
            if (iconElement) iconElement.classList.add("spin");
            
            const titleElement = stepElement.querySelector('h4');
            const originalText = titleElement.getAttribute('data-orig') || titleElement.innerText;
            if (!titleElement.getAttribute('data-orig')) {
                titleElement.setAttribute('data-orig', originalText);
            }
            if (percent > 0) {
                titleElement.innerText = `${originalText} (${percent}%)`;
            } else {
                titleElement.innerText = `${originalText} (Đang xử lý...)`;
            }
        } else if (isPast) {
            stepElement.className = "step done";
            barElement.style.width = "100%";
            const titleElement = stepElement.querySelector('h4');
            if (titleElement && titleElement.getAttribute('data-orig')) {
                titleElement.innerText = titleElement.getAttribute('data-orig') + " (Xong)";
            }
        } else {
            stepElement.className = "step";
            barElement.style.width = "0%";
            const titleElement = stepElement.querySelector('h4');
            if (titleElement && titleElement.getAttribute('data-orig')) {
                titleElement.innerText = titleElement.getAttribute('data-orig');
            }
        }
    });
}

function notifyUser(title, body) {
    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") {
        new Notification(title, { body, icon: "/favicon.ico" });
    } else if (Notification.permission !== "denied") {
        Notification.requestPermission().then(permission => {
            if (permission === "granted") {
                new Notification(title, { body, icon: "/favicon.ico" });
            }
        });
    }
}

function showResult(payload) {
    DOM.processingEngine.classList.add("hidden");
    DOM.resultPlayer.classList.remove("hidden");
    DOM.btnDownload.href = payload.download_url;

    let messageTitle = "Tiến trình hoàn tất!";
    let messageBody = "Output đã sẵn sàng.";

    if (payload.media_kind === "video") {
        const isEdit = payload.meta?.video_task === "edit";
        messageBody = isEdit
            ? "Video edit da duoc hau ky va san sang tai ve."
            : "Video review đã được lưu và sẵn sàng tải về.";
        DOM.resultMessage.textContent = isEdit ? "Video edit da render xong." : "Video review đã render xong.";
        DOM.videoPlayer.src = payload.download_url;
        DOM.videoPlayer.classList.remove("hidden");
    } else {
        messageBody = "Audiobook đã được lưu và sẵn sàng tải về.";
        DOM.resultMessage.textContent = "Audiobook đã sẵn sàng.";
        DOM.audioPlayer.src = payload.download_url;
        DOM.audioPlayer.classList.remove("hidden");
    }

    notifyUser(messageTitle, messageBody);

    const notes = [];
    if (payload.meta?.review_title) {
        notes.push(`<strong>Tiêu đề review:</strong> ${escapeHtml(payload.meta.review_title)}`);
    }
    if (payload.meta?.narrator_gender) {
        const genderLabel = payload.meta.narrator_gender === "male" ? "Nam (NamMinh)" : "Nữ (HoaiMy)";
        notes.push(`<strong>Giọng narrator:</strong> ${escapeHtml(genderLabel)}`);
    }
    if (payload.meta?.orientation) {
        const orientLabel = payload.meta.orientation === "vertical" ? "Dọc 9:16" : "Ngang 16:9";
        notes.push(`<strong>Khung hình:</strong> ${escapeHtml(orientLabel)}`);
    }
    if (payload.meta?.video_task === "edit") {
        notes.push(`<strong>Loai video:</strong> Video edit hau ky tu dong`);
    }
    if (payload.meta?.renderer) {
        notes.push(`<strong>Renderer:</strong> ${escapeHtml(payload.meta.renderer)}`);
    }
    if (payload.meta?.edit_plan_summary) {
        const s = payload.meta.edit_plan_summary;
        notes.push(`<strong>Edit cues:</strong> captions ${s.captions || 0}, text ${s.text_popups || 0}, icons ${s.icons || 0}, sfx ${s.sfx || 0}`);
    }
    // Highlight exact local path so the user knows exactly where it's saved
    if (payload.output_path) {
        notes.push(`<strong>Đã lưu tại máy tính:</strong> <br><code style="background:#222;padding:2px 4px;border-radius:4px;">${escapeHtml(payload.output_path)}</code>`);
    } else if (payload.meta?.source_path) {
        notes.push(`<strong>Nguồn:</strong> ${escapeHtml(payload.meta.source_path)}`);
    }

    if (notes.length) {
        DOM.resultMeta.innerHTML = notes.join("<br>");
        DOM.resultMeta.classList.remove("hidden");
    }
}

function markStepError() {
    document.querySelectorAll(".step.active .step-icon").forEach((element) => {
        element.style.borderColor = "var(--error)";
        element.style.color = "var(--error)";
        element.style.background = "rgba(255, 255, 255, 0.04)";
        element.style.boxShadow = "0 0 12px rgba(239, 68, 68, 0.45)";
    });
}

function setBusy(button, busy, label, icon = "loader") {
    button.disabled = busy;
    button.innerHTML = busy
        ? `<i data-lucide="${icon}" class="spin"></i>${label}`
        : `<i data-lucide="${icon}"></i>${label}`;
    lucide.createIcons();
}

function showError(message) {
    DOM.toastMessage.textContent = message;
    DOM.toast.classList.remove("hidden");
    DOM.toast.classList.add("show");
    setTimeout(() => DOM.toast.classList.remove("show"), 5000);
}

function showErrorModal(payload) {
    const errorDetail = payload.meta?.error_detail;
    const errorText = payload.error || "Unknown error";

    // Build modal HTML
    let modalHTML = `
        <div class="error-modal-overlay" id="errorModalOverlay">
            <div class="error-modal">
                <div class="error-modal-header">
                    <i data-lucide="alert-triangle" style="color:var(--error);width:28px;height:28px"></i>
                    <h3>Job thất bại</h3>
                    <button class="error-modal-close" onclick="closeErrorModal()">&times;</button>
                </div>
                <div class="error-modal-body">
                    <div class="error-main-message">${escapeHtml(errorText.split('\n')[0])}</div>`;

    if (errorDetail) {
        // Show specific QA errors
        if (errorDetail.errors && errorDetail.errors.length) {
            modalHTML += `<div class="error-section"><h4>🔍 Chi tiết lỗi</h4><ul>`;
            errorDetail.errors.forEach(err => {
                modalHTML += `<li class="error-item">${escapeHtml(err)}</li>`;
            });
            modalHTML += `</ul></div>`;
        }

        // Show quality report
        const qr = errorDetail.quality_report;
        if (qr && (qr.errors?.length || qr.warnings?.length)) {
            modalHTML += `<div class="error-section"><h4>📋 Quality Report</h4>`;
            if (qr.errors?.length) {
                modalHTML += `<p class="qr-label">❌ Lỗi (${qr.errors.length}):</p><ul>`;
                qr.errors.forEach(e => { modalHTML += `<li class="error-item">${escapeHtml(e)}</li>`; });
                modalHTML += `</ul>`;
            }
            if (qr.warnings?.length) {
                modalHTML += `<p class="qr-label">⚠️ Cảnh báo (${qr.warnings.length}):</p><ul>`;
                qr.warnings.forEach(w => { modalHTML += `<li class="warning-item">${escapeHtml(w)}</li>`; });
                modalHTML += `</ul>`;
            }
            if (qr.retry_count > 0) {
                modalHTML += `<p class="qr-retry">Đã thử lại ${qr.retry_count} lần trước khi thất bại.</p>`;
            }
            modalHTML += `</div>`;
        }

        // Show last agent steps
        if (errorDetail.agent_log && errorDetail.agent_log.length) {
            modalHTML += `<div class="error-section"><h4>🤖 Agent Log (gần nhất)</h4><ul class="agent-log">`;
            errorDetail.agent_log.forEach(entry => {
                const icon = entry.status === 'ok' ? '✅' : '❌';
                modalHTML += `<li>${icon} <strong>${escapeHtml(entry.agent)}</strong>: ${escapeHtml(entry.message)}</li>`;
            });
            modalHTML += `</ul></div>`;
        }
    }

    modalHTML += `
                </div>
                <div class="error-modal-footer">
                    <button class="btn-close-modal" onclick="closeErrorModal()">Đóng</button>
                </div>
            </div>
        </div>`;

    // Remove existing modal
    const existing = document.getElementById('errorModalOverlay');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHTML);
    lucide.createIcons();

    // Also show short toast
    showError('Job thất bại — xem chi tiết trong popup');
}

function closeErrorModal() {
    const modal = document.getElementById('errorModalOverlay');
    if (modal) modal.remove();
}

function escapeHtml(value) {
    return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
}
