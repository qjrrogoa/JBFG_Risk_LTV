import sys

file_path = "frontend/src/App_v2.jsx"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: Add trimmedUsername in handleLogin
old_check = 'if (!username.trim() || !pw.trim()) return alert("아이디와 비밀번호를 입력해주세요.");'
new_check = 'const trimmedUsername = username.trim();\n        if (!trimmedUsername || !pw.trim()) return alert("아이디와 비밀번호를 입력해주세요.");'
content = content.replace(old_check, new_check)

# Fix 2: Remove isChecking from AuthPage login button
# Note: There are two instances of disabled={isLoading || isChecking}. 
# One in AuthPage (line ~570) and one in SignupModal (line ~803).
# We only want to fix the first one.
content = content.replace('disabled={isLoading || isChecking}', 'disabled={isLoading}', 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Fix applied successfully.")
