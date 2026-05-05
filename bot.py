from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from codex_runner import CodexRunner, RunnerBusyError, resolve_codex_bin
from security import PendingTaskStore, RiskAssessment, is_authorized, parse_allowed_user_ids


SAFE_CHUNK_SIZE = 3800


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "tele_codex.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_config() -> tuple[str, set[int], Path, int, Path, Path | None]:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")

    allowed_ids = parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
    workdir = Path(os.getenv("CODEX_WORKDIR", os.getcwd())).expanduser().resolve()
    timeout_seconds = int(os.getenv("CODEX_TIMEOUT_SECONDS", "900"))
    codex_bin = resolve_codex_bin(os.getenv("CODEX_BIN"))
    codex_home_raw = os.getenv("CODEX_HOME", "").strip()
    codex_home = Path(codex_home_raw).expanduser().resolve() if codex_home_raw else None

    return token, allowed_ids, workdir, timeout_seconds, codex_bin, codex_home


async def send_long_message(update: Update, text: str) -> None:
    if not update.effective_chat:
        return

    if not text:
        text = "(no output)"

    for start in range(0, len(text), SAFE_CHUNK_SIZE):
        await update.effective_chat.send_message(text[start : start + SAFE_CHUNK_SIZE])


def user_id_from(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


async def reject_unauthorized(update: Update, allowed_ids: set[int]) -> bool:
    user_id = user_id_from(update)
    if is_authorized(user_id, allowed_ids):
        return False

    await send_long_message(
        update,
        "拒绝访问。\n"
        f"你的 Telegram user id 是: {user_id}\n"
        "把它加入 TELEGRAM_ALLOWED_USER_IDS 后重启 bot。",
    )
    logging.warning("unauthorized access user_id=%s", user_id)
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_ids: set[int] = context.application.bot_data["allowed_ids"]
    user_id = user_id_from(update)

    if await reject_unauthorized(update, allowed_ids):
        return

    await send_long_message(
        update,
        "Codex Telegram 控制台已就绪。\n\n"
        f"你的 Telegram user id: {user_id}\n\n"
        "可用命令:\n"
        "/run <任务> - 让 Codex 执行任务\n"
        "/confirm <id> - 确认执行高风险任务\n"
        "/cancel <id> - 取消待确认任务\n"
        "/status - 查看运行状态",
    )


async def run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_ids: set[int] = context.application.bot_data["allowed_ids"]
    if await reject_unauthorized(update, allowed_ids):
        return

    prompt = " ".join(context.args).strip()
    if not prompt:
        await send_long_message(update, "用法: /run <任务>")
        return

    pending_store: PendingTaskStore = context.application.bot_data["pending_store"]
    assessment = RiskAssessment.from_prompt(prompt)

    if assessment.requires_confirmation:
        task = pending_store.add(user_id_from(update), prompt, assessment.reasons)
        await send_long_message(
            update,
            "这个任务需要确认后再执行。\n\n"
            f"任务 ID: {task.task_id}\n"
            f"原因: {', '.join(task.reasons)}\n\n"
            f"确认执行: /confirm {task.task_id}\n"
            f"取消任务: /cancel {task.task_id}",
        )
        logging.info("pending task task_id=%s user_id=%s reasons=%s", task.task_id, task.user_id, task.reasons)
        return

    await execute_prompt(update, context, prompt)


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_ids: set[int] = context.application.bot_data["allowed_ids"]
    if await reject_unauthorized(update, allowed_ids):
        return

    if not context.args:
        await send_long_message(update, "用法: /confirm <id>")
        return

    pending_store: PendingTaskStore = context.application.bot_data["pending_store"]
    task = pending_store.pop(context.args[0], user_id_from(update))
    if not task:
        await send_long_message(update, "没有找到这个待确认任务，或它不属于你。")
        return

    await execute_prompt(update, context, task.prompt)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_ids: set[int] = context.application.bot_data["allowed_ids"]
    if await reject_unauthorized(update, allowed_ids):
        return

    if not context.args:
        await send_long_message(update, "用法: /cancel <id>")
        return

    pending_store: PendingTaskStore = context.application.bot_data["pending_store"]
    task = pending_store.pop(context.args[0], user_id_from(update))
    if not task:
        await send_long_message(update, "没有找到这个待确认任务，或它不属于你。")
        return

    logging.info("cancelled task task_id=%s user_id=%s", task.task_id, task.user_id)
    await send_long_message(update, f"已取消任务 {task.task_id}。")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_ids: set[int] = context.application.bot_data["allowed_ids"]
    if await reject_unauthorized(update, allowed_ids):
        return

    runner: CodexRunner = context.application.bot_data["runner"]
    pending_store: PendingTaskStore = context.application.bot_data["pending_store"]
    running = "是" if runner.is_running else "否"
    await send_long_message(
        update,
        f"Codex 正在运行: {running}\n"
        f"待确认任务数: {pending_store.count_for_user(user_id_from(update))}",
    )


async def execute_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    runner: CodexRunner = context.application.bot_data["runner"]
    await send_long_message(update, "已开始执行，完成后我会把结果发回来。")

    try:
        result = await runner.run(prompt)
    except RunnerBusyError:
        await send_long_message(update, "当前已有 Codex 任务在运行。请稍后再试，或先用 /status 查看状态。")
        return

    status_line = "成功" if result.returncode == 0 else f"失败，退出码 {result.returncode}"
    elapsed = f"{result.elapsed_seconds:.1f}s"
    logging.info(
        "codex finished status=%s elapsed=%s prompt=%r output_summary=%r",
        result.returncode,
        elapsed,
        prompt[:300],
        result.output_summary,
    )

    await send_long_message(update, f"Codex 执行{status_line}，耗时 {elapsed}。\n\n{result.display_output}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("telegram handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        await update.effective_chat.send_message("Bot 遇到错误，请查看本地 logs/tele_codex.log。")


def main() -> None:
    token, allowed_ids, workdir, timeout_seconds, codex_bin, codex_home = get_config()
    setup_logging(Path("logs"))

    runner = CodexRunner(
        workdir=workdir,
        codex_bin=codex_bin,
        timeout_seconds=timeout_seconds,
        codex_home=codex_home,
    )
    pending_store = PendingTaskStore()

    app = Application.builder().token(token).build()
    app.bot_data["allowed_ids"] = allowed_ids
    app.bot_data["runner"] = runner
    app.bot_data["pending_store"] = pending_store

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("run", run))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))
    app.add_error_handler(error_handler)

    logging.info(
        "starting bot workdir=%s codex_bin=%s codex_home=%s allowed_ids=%s",
        workdir,
        codex_bin,
        runner.codex_home,
        sorted(allowed_ids),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
