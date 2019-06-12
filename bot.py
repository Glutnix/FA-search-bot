import re

import requests
import telegram
import time

from telegram import Chat
from telegram.ext import Updater, Filters, MessageHandler, CommandHandler
import logging
from telegram.utils.request import Request
import json


class FilterRegex(Filters.regex):

    def filter(self, message):
        text = message.text or message.caption
        if text:
            return bool(self.pattern.search(text))
        return False


class FASearchBot:
    FA_LINK = re.compile(r"furaffinity.net/view/([0-9]+)", re.I)

    def __init__(self, conf_file):
        with open(conf_file, 'r') as f:
            self.config = json.load(f)
        self.bot_key = self.config["bot_key"]
        self.bot = None
        self.alive = False

    def start(self):
        request = Request(con_pool_size=8)
        self.bot = telegram.Bot(token=self.bot_key, request=request)
        updater = Updater(bot=self.bot)
        dispatcher = updater.dispatcher
        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

        beep_handler = CommandHandler('beep', self.beep)
        dispatcher.add_handler(beep_handler)

        start_handler = CommandHandler('start', self.welcome_message)
        dispatcher.add_handler(start_handler)

        neaten_handler = MessageHandler(FilterRegex(self.FA_LINK), self.neaten_image)
        dispatcher.add_handler(neaten_handler)

        updater.start_polling()
        self.alive = True

        while self.alive:
            print("Main thread alive")
            time.sleep(30)

    def welcome_message(self, bot, update):
        bot.send_message(
            chat_id=update.message.chat_id,
            text="Hello, I'm a new bot so I'm still learning. I can't do a whole lot yet. "
                 "If you have any suggestions, requests, or questions, direct them to @deerspangle.\n"
                 "For now, all I can do is neaten up any FA links you send me."
        )

    def beep(self, bot, update):
        bot.send_message(chat_id=update.message.chat_id, text="boop")

    def neaten_image(self, bot, update):
        message = update.message.text or update.message.caption
        for match in self.FA_LINK.finditer(message):
            self._handle_fa_submission_link(bot, update, match.group(1))

    def _handle_fa_submission_link(self, bot, update, link):
        print("Found a link, ID:{}".format(link))
        sub_resp = requests.get("{}/submission/{}.json".format(self.config['api_url'], link))
        # If API returns fine
        if sub_resp.status_code == 200:
            sub_data = sub_resp.json()
            self._send_neat_fa_response(bot, update, sub_data)
        else:
            # Only send an error message in private message
            if update.message.chat.type == Chat.PRIVATE:
                bot.send_message(
                    chat_id=update.message.chat_id,
                    text="This doesn't seem to be a valid FA submission: {}".format(link),
                    reply_to_message_id=update.message.message_id
                )

    def _send_neat_fa_response(self, bot, update, submission_data):
        # Handle gifs
        ext = submission_data['download'].split(".")[-1]
        if ext == "gif":
            bot.send_document(
                chat_id=update.message.chat_id,
                document=submission_data['download'],
                caption=submission_data['link'],
                reply_to_message_id=update.message.message_id
            )
            return
        # Handle photos
        bot.send_photo(
            chat_id=update.message.chat_id,
            photo=submission_data['download'],
            caption=submission_data['link'],
            reply_to_message_id=update.message.message_id
        )
        return
