# Telegram Bot Remote Control for Codex

This project runs a local Telegram bot on Windows. Authorized Telegram users can send a task with `/run`, and the bot executes it through the local Codex CLI with `codex exec`.

## Safety Model

- Only Telegram user IDs listed in `TELEGRAM_ALLOWED_USER_IDS` can use the bot.
- Codex runs with `codex exec --sandbox workspace-write`.
- The bot never uses `danger-full-access` or `--dangerously-bypass-approvals-and-sandbox`.
- Risky prompts are held until you confirm them with `/confirm <id>`.
- Logs are written to `logs/tele_codex.log`.

## Setup

1. Create a bot with [BotFather](https://t.me/BotFather) and copy the token.
2. Copy `.env.example` to `.env`.
3. Fill in:

   ```powershell
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_ALLOWED_USER_IDS=your_numeric_telegram_user_id
   CODEX_WORKDIR=D:\codeing\Tele_bot
   CODEX_BIN=
   CODEX_HOME=D:\codeing\Tele_bot\.codex-home
   CODEX_TIMEOUT_SECONDS=900
   ```

   `CODEX_BIN` can be left empty. If auto-detection fails, set it to the full path shown by:

   ```powershell
   (Get-Command codex).Source
   ```

   `CODEX_HOME` should point to a local writable directory. This avoids Windows permission errors while Codex creates helper files.

4. If you do not know your Telegram user ID yet, temporarily set your expected ID after checking it with a user ID lookup bot, or start this bot and read the rejected `/start` response from logs/console.

## Run Locally

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python bot.py
```

## Local Checks

```powershell
python -m py_compile bot.py codex_runner.py security.py
python -m unittest discover -s tests
```

## Telegram Commands

- `/start` shows available commands and your user ID.
- `/run <task>` asks Codex to execute a task.
- `/confirm <id>` confirms a risky pending task.
- `/cancel <id>` cancels a pending task.
- `/status` shows whether Codex is running and how many pending tasks you have.

## Windows Autostart Option

Use Windows Task Scheduler:

1. Open Task Scheduler.
2. Create a basic task that starts when you log in.
3. Program/script:

   ```text
   .\.venv\Scripts\python.exe
   ```

4. Arguments:

   ```text
   bot.py
   ```

5. Start in:

   ```text
   .\Tele_bot
   ```

You can also use NSSM if you prefer running it as a Windows service.
