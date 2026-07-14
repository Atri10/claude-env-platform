"""
claude-env :: Domain - Content Security Detectors - shared pattern banks
"""
from __future__ import annotations

INJECTION_PATTERNS = [
    (r"(?i)\bignore (all |the )?(previous|prior|above) (instructions|prompts?)\b", 0.9),
    (r"(?i)\bdisregard (the )?(system|previous) (prompt|message|instructions)\b", 0.9),
    (r"(?i)\byou are now\b.*\b(dan|developer mode|unrestricted)\b", 0.8),
    (r"(?i)\b(reveal|print|exfiltrate|leak|send).{0,30}\b(system prompt|api[_ ]?key|secret|\.env)\b", 0.95),
    (r"(?i)\bnew (instructions?|task)\b\s*[:\-]", 0.5),
    (r"(?i)<\s*/?\s*(system|assistant)\s*>", 0.7),
    (r"(?i)\bact as\b.*\b(no restrictions|without limitations)\b", 0.7),
    (r"(?i)curl\s+.*\|\s*(sh|bash)", 0.85),
    (r"(?i)\b(base64 -d|eval\()", 0.5),
]

SECRET_PATTERNS = [
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("aws_secret", r"(?i)aws_secret_access_key\s*=\s*[A-Za-z0-9/+]{40}"),
    ("private_key", r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("gcp_key", r'"type":\s*"service_account"'),
    ("slack_token", r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    ("github_pat", r"ghp_[0-9A-Za-z]{36}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("generic_secret", r'(?i)(secret|password|passwd|api[_-]?key|token)["\']?\s*[:=]\s*["\'][^"\']{12,}["\']'),
]
