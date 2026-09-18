const form = document.getElementById("loginForm");
const errorBox = document.getElementById("loginError");
const forgotLink = document.getElementById("forgotLink");
const forgotMessage = document.getElementById("forgotMessage");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("visible");
}

if (forgotLink) {
  forgotLink.addEventListener("click", (e) => {
    e.preventDefault();
    forgotMessage.textContent = "Please contact the admin to reset your password.";
    forgotMessage.classList.add("visible");
  });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.remove("visible");

  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;

  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.detail || "Sign in failed.");
      return;
    }

    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("role", data.role);
    localStorage.setItem("full_name", data.full_name);
    localStorage.setItem("approval_status", data.approval_status);

    if (data.must_reset_password) {
      window.location.href = "/reset-password";
    } else if (data.role === "admin") {
      window.location.href = "/admin";
    } else if (data.role === "setter") {
      window.location.href = "/setter";
    } else {
      window.location.href = "/exam";
    }
  } catch (err) {
    showError("Could not reach the server. Try again.");
  }
});
