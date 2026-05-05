from __future__ import annotations

import asyncio
import os
import subprocess
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


MAX_DISPLAY_CHARS = 12000
SUMMARY_CHARS = 500
CODEX_HOME_SYNC_FILES = ("auth.json", "config.toml", "cap_sid")


def resolve_codex_bin(raw_value: str | None = None) -> Path:
    configured = (raw_value or "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.exists():
            return path

    discovered = shutil.which("codex")
    if discovered:
        return Path(discovered).resolve()

    for candidate in candidate_codex_paths():
        if candidate.exists():
            return candidate.resolve()

    configured_hint = f" Configured CODEX_BIN was not found: {configured}." if configured else ""
    raise RuntimeError(
        "Cannot find codex executable."
        f"{configured_hint} Set CODEX_BIN in .env to the full codex.exe path, or make codex available on PATH."
    )


def candidate_codex_paths() -> list[Path]:
    candidates: list[Path] = []

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "OpenAI" / "Codex" / "bin" / "codex.exe")

    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.exe")

    for home in (Path.home(),):
        candidates.append(home / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.exe")

    for root in (Path("C:/Users"), Path("D:/WpSystem")):
        if not root.exists():
            continue
        try:
            candidates.extend(root.glob("*/AppData/Local/OpenAI/Codex/bin/codex.exe"))
            candidates.extend(root.glob("*/AppData/Local/Packages/OpenAI.Codex_*/LocalCache/Local/OpenAI/Codex/bin/codex.exe"))
        except OSError:
            continue

    return list(dict.fromkeys(candidates))


class RunnerBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexResult:
    returncode: int
    elapsed_seconds: float
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def combined_output(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append("[stderr]\n" + self.stderr.strip())
        if self.timed_out:
            parts.append("[timeout]\nCodex reached the configured timeout.")
        return "\n\n".join(parts).strip()

    @property
    def display_output(self) -> str:
        output = self.combined_output or "(no output)"
        if len(output) <= MAX_DISPLAY_CHARS:
            return output

        tail = output[-MAX_DISPLAY_CHARS:]
        return f"[output truncated to last {MAX_DISPLAY_CHARS} chars]\n{tail}"

    @property
    def output_summary(self) -> str:
        output = self.combined_output.replace("\n", " ")
        if len(output) <= SUMMARY_CHARS:
            return output
        return output[:SUMMARY_CHARS] + "..."


class CodexRunner:
    def __init__(
        self,
        workdir: Path,
        codex_bin: Path,
        timeout_seconds: int = 900,
        codex_home: Path | None = None,
    ) -> None:
        self.workdir = workdir
        self.codex_bin = codex_bin
        self.codex_home = codex_home or workdir / ".codex-home"
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    async def run(self, prompt: str) -> CodexResult:
        if self._lock.locked():
            raise RunnerBusyError("Codex is already running")

        async with self._lock:
            start = time.monotonic()
            self.codex_home.mkdir(parents=True, exist_ok=True)
            sync_codex_home_credentials(self.codex_home)
            env = os.environ.copy()
            env["CODEX_HOME"] = str(self.codex_home)

            process = await asyncio.create_subprocess_exec(
                str(self.codex_bin),
                "exec",
                "--cd",
                str(self.workdir),
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--color",
                "never",
                "-",
                cwd=self.workdir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=self.timeout_seconds,
                )
                timed_out = False
            except asyncio.TimeoutError:
                process.kill()
                stdout_bytes, stderr_bytes = await process.communicate()
                timed_out = True

            elapsed = time.monotonic() - start
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            returncode = process.returncode if process.returncode is not None else -1

            if timed_out and returncode == 0:
                returncode = -1

            return CodexResult(
                returncode=returncode,
                elapsed_seconds=elapsed,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
            )


def default_codex_home() -> Path | None:
    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        candidate = Path(user_profile) / ".codex"
        if candidate.exists():
            return candidate

    candidate = Path.home() / ".codex"
    if candidate.exists():
        return candidate

    return None


def sync_codex_home_credentials(target_home: Path, source_home: Path | None = None) -> list[Path]:
    source = source_home or default_codex_home()
    if not source or source.resolve() == target_home.resolve():
        return []

    copied: list[Path] = []
    target_home.mkdir(parents=True, exist_ok=True)

    for name in CODEX_HOME_SYNC_FILES:
        source_file = source / name
        target_file = target_home / name
        if not source_file.exists() or source_file.is_dir():
            continue

        if target_file.exists() and target_file.stat().st_mtime >= source_file.stat().st_mtime:
            continue

        shutil.copy2(source_file, target_file)
        copied.append(target_file)

    return copied
