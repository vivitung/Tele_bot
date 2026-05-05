from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


PENDING_TTL = timedelta(hours=1)


RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(rm|del|erase|remove-item|rmdir|rd)\b", "删除文件或目录"),
    (r"\bformat\b|\bmkfs\b", "格式化或重建文件系统"),
    (r"\bshutdown\b|\brestart-computer\b|\breboot\b", "关机或重启系统"),
    (r"\bgit\s+(push|commit|reset|checkout|clean|rebase)\b", "修改 Git 历史或发布代码"),
    (r"\b(pip|npm|pnpm|yarn|cargo|uv)\s+(install|add|remove|uninstall)\b", "安装或移除依赖"),
    (r"\b(curl|wget|Invoke-WebRequest|iwr|irm|Invoke-RestMethod)\b", "访问外部网络"),
    (r"\b(http://|https://|web\s*search|browse|download)\b", "访问外部网络"),
    (r"\bset-executionpolicy\b|\breg\s+(add|delete)\b", "修改系统策略或注册表"),
    (r"--dangerously-bypass-approvals-and-sandbox|danger-full-access", "绕过 Codex 安全限制"),
    (r"删除|清空|卸载|安装依赖|提交代码|推送|重置|格式化|关机|重启|注册表|外部网络|联网|下载|访问网页|搜索网络", "中文高风险操作描述"),
)


def parse_allowed_user_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError as exc:
            raise ValueError(f"Invalid Telegram user id: {item}") from exc
    return ids


def is_authorized(user_id: int | None, allowed_ids: set[int]) -> bool:
    return user_id is not None and user_id in allowed_ids


@dataclass(frozen=True)
class RiskAssessment:
    requires_confirmation: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def from_prompt(cls, prompt: str) -> "RiskAssessment":
        reasons: list[str] = []
        for pattern, reason in RISK_PATTERNS:
            if re.search(pattern, prompt, flags=re.IGNORECASE):
                reasons.append(reason)

        unique_reasons = tuple(dict.fromkeys(reasons))
        return cls(bool(unique_reasons), unique_reasons)


@dataclass(frozen=True)
class PendingTask:
    task_id: str
    user_id: int | None
    prompt: str
    reasons: tuple[str, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now - self.created_at > PENDING_TTL


class PendingTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, PendingTask] = {}

    def add(self, user_id: int | None, prompt: str, reasons: tuple[str, ...]) -> PendingTask:
        self._purge_expired()
        task_id = secrets.token_hex(3)
        while task_id in self._tasks:
            task_id = secrets.token_hex(3)

        task = PendingTask(task_id=task_id, user_id=user_id, prompt=prompt, reasons=reasons)
        self._tasks[task_id] = task
        return task

    def pop(self, task_id: str, user_id: int | None) -> PendingTask | None:
        self._purge_expired()
        task = self._tasks.get(task_id)
        if not task or task.user_id != user_id:
            return None
        return self._tasks.pop(task_id)

    def count_for_user(self, user_id: int | None) -> int:
        self._purge_expired()
        return sum(1 for task in self._tasks.values() if task.user_id == user_id)

    def _purge_expired(self) -> None:
        expired = [task_id for task_id, task in self._tasks.items() if task.is_expired()]
        for task_id in expired:
            self._tasks.pop(task_id, None)
