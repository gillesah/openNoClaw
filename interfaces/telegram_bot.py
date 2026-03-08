"""
Telegram bot interface for openNoClaw.
Uses python-telegram-bot v21+ async API.
"""

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

if TYPE_CHECKING:
    from core.memory import Memory
    from core.skills import SkillsManager

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(
        self,
        token: str,
        backend,
        memory,
        skills_manager,
        allowed_chat_ids: list[int] = None,
        **kwargs,
    ):
        self.backend = backend
        self.memory = memory
        self.skills_manager = skills_manager
        self.allowed_chat_ids = set(allowed_chat_ids or [])

        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("clear", self.cmd_clear))
        self.app.add_handler(CommandHandler("skills", self.cmd_skills))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

    def _allowed(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True  # No restriction
        return chat_id in self.allowed_chat_ids

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self._allowed(chat_id):
            await update.message.reply_text("Unauthorized.")
            return
        await update.message.reply_text(
            "openNoClaw ready. Send me a message."
        )

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self._allowed(chat_id):
            return
        user_id = f"telegram:{chat_id}"
        await self.memory.clear(user_id)
        await update.message.reply_text("Conversation cleared.")

    async def cmd_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self._allowed(chat_id):
            return
        skills = self.skills_manager.list_skills()
        text = "Loaded skills:\n" + "\n".join(f"• {s}" for s in skills) if skills else "No skills loaded."
        await update.message.reply_text(text)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self._allowed(chat_id):
            await update.message.reply_text("Unauthorized.")
            return

        user_id = f"telegram:{chat_id}"
        user_text = update.message.text.strip()

        await self.memory.add_message(user_id, "user", user_text)

        messages = self.memory.get_history(user_id)
        system_prompt = self.skills_manager.build_system_prompt()

        # Send typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            response, _usage = await self.backend.chat(messages, system_prompt)
            await self.memory.add_message(user_id, "assistant", response)

            # Telegram max message length is 4096 chars
            if len(response) > 4000:
                for i in range(0, len(response), 4000):
                    await update.message.reply_text(response[i : i + 4000])
            else:
                await update.message.reply_text(response)

        except Exception as e:
            logger.error(f"Error processing Telegram message: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def start(self):
        """Start the bot (non-blocking, runs in background)."""
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started")

    async def stop(self):
        """Gracefully stop the bot."""
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Telegram bot stopped")
