import telegram
from telegram.ext import MessageHandler, CallbackContext

from functionalities.functionalities import BotFunctionality


class UnhandledMessageFunctionality(BotFunctionality):
    
    def __init__(self):
        super().__init__(MessageHandler)

    def call(self, update: telegram.Update, context: CallbackContext):
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Sorry, I'm not sure how to handle that message"
        )
