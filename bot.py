import re
import uuid
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Union

import telegram
import time

from telegram import Chat, InlineQueryResultArticle, InputTextMessageContent, InlineQueryResultPhoto, InlineQueryResult
from telegram.ext import Updater, Filters, MessageHandler, CommandHandler, InlineQueryHandler
import logging
from telegram.utils.request import Request
import json

from fa_export_api import FAExportAPI, PageNotFound
from fa_submission import CantSendFileType, FASubmissionFull, FASubmissionShort


class FilterRegex(Filters.regex):

    def filter(self, message):
        text = message.text_markdown_urled or message.caption_markdown_urled
        if text:
            return bool(self.pattern.search(text))
        return False


class FASearchBot:

    def __init__(self, conf_file):
        with open(conf_file, 'r') as f:
            self.config = json.load(f)
        self.bot_key = self.config["bot_key"]
        self.api_url = self.config['api_url']
        self.api = FAExportAPI(self.config['api_url'])
        self.bot = None
        self.alive = False
        self.functionalities = []

    def start(self):
        request = Request(con_pool_size=8)
        self.bot = telegram.Bot(token=self.bot_key, request=request)
        updater = Updater(bot=self.bot)
        dispatcher = updater.dispatcher
        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

        self.functionalities = self.initialise_functionalities()
        for func in self.functionalities:
            func.register(dispatcher)

        updater.start_polling()
        self.alive = True

        while self.alive:
            print("Main thread alive")
            time.sleep(30)

    def initialise_functionalities(self):
        return [
            BeepFunctionality(),
            WelcomeFunctionality(),
            NeatenFunctionality(self.api),
            InlineFunctionality(self.api)
        ]


class BotFunctionality(ABC):

    def __init__(self, handler_cls, **kwargs):
        self.kwargs = kwargs
        self.handler_cls = handler_cls

    def register(self, dispatcher):
        args_dict = self.kwargs
        args_dict["callback"] = self.call
        handler = self.handler_cls(**args_dict)
        dispatcher.add_handler(handler)

    @abstractmethod
    def call(self, bot, update):
        pass


class BeepFunctionality(BotFunctionality):

    def __init__(self):
        super().__init__(CommandHandler, command='beep')

    def call(self, bot, update):
        bot.send_message(chat_id=update.message.chat_id, text="boop")


class WelcomeFunctionality(BotFunctionality):

    def __init__(self):
        super().__init__(CommandHandler, command='start')

    def call(self, bot, update):
        bot.send_message(
            chat_id=update.message.chat_id,
            text="Hello, I'm a new bot so I'm still learning. I can't do a whole lot yet. "
                 "If you have any suggestions, requests, or questions, direct them to @deerspangle.\n"
                 "Currently I can:\n"
                 "- Neaten up any FA submission or direct links you give me\n"
                 "- Respond to inline search queries"
        )


class NeatenFunctionality(BotFunctionality):
    FA_SUB_LINK = re.compile(r"furaffinity\.net/view/([0-9]+)", re.I)
    FA_DIRECT_LINK = re.compile(r"d\.facdn\.net/art/([^/]+)/(?:|stories/|poetry/|music/)([0-9]+)/", re.I)
    FA_THUMB_LINK = re.compile(r"t\.facdn\.net/([0-9]+)@[0-9]+-[0-9]+\.jpg")
    FA_LINKS = re.compile(f"{FA_SUB_LINK.pattern}|{FA_DIRECT_LINK.pattern}|{FA_THUMB_LINK.pattern}")

    def __init__(self, api):
        super().__init__(MessageHandler, filters=FilterRegex(self.FA_LINKS))
        self.api = api

    def call(self, bot, update):
        message = update.message.text_markdown_urled or update.message.caption_markdown_urled
        submission_ids = []
        for match in self.FA_LINKS.finditer(message):
            submission_id = self._get_submission_id_from_link(bot, update, match.group(0))
            if submission_id:
                submission_ids.append(submission_id)
        # Remove duplicates, preserving order
        submission_ids = list(dict.fromkeys(submission_ids))
        # Handle each submission
        for submission_id in submission_ids:
            self._handle_fa_submission_link(bot, update, submission_id)

    def _get_submission_id_from_link(self, bot, update, link: str) -> Optional[int]:
        # Handle submission page link matches
        sub_match = self.FA_SUB_LINK.match(link)
        if sub_match:
            return int(sub_match.group(1))
        # Handle thumbnail link matches
        thumb_match = self.FA_THUMB_LINK.match(link)
        if thumb_match:
            return int(thumb_match.group(1))
        # Handle direct file link matches
        direct_match = self.FA_DIRECT_LINK.match(link)
        username = direct_match.group(1)
        image_id = int(direct_match.group(2))
        submission_id = self._find_submission(username, image_id)
        if not submission_id:
            self._return_error_in_privmsg(
                bot, update,
                f"Could not locate the image by {username} with image id {image_id}."
            )
        return submission_id

    def _handle_fa_submission_link(self, bot, update, submission_id: int):
        print("Found a link, ID:{}".format(submission_id))
        try:
            submission = self.api.get_full_submission(str(submission_id))
            self._send_neat_fa_response(bot, update, submission)
        except PageNotFound:
            self._return_error_in_privmsg(bot, update, "This doesn't seem to be a valid FA submission: "
                                                       "https://www.furaffinity.net/view/{}/".format(submission_id))

    def _send_neat_fa_response(self, bot, update, submission: FASubmissionFull):
        try:
            submission.send_message(bot, update.message.chat_id, update.message.message_id)
        except CantSendFileType as e:
            self._return_error_in_privmsg(bot, update, str(e))

    def _return_error_in_privmsg(self, bot, update, error_message: str):
        # Only send an error message in private message
        if update.message.chat.type == Chat.PRIVATE:
            bot.send_message(
                chat_id=update.message.chat_id,
                text=error_message,
                reply_to_message_id=update.message.message_id
            )

    def _find_submission(self, username: str, image_id: int) -> Optional[int]:
        folders = ["gallery", "scraps"]
        for folder in folders:
            submission_id = self._find_submission_in_folder(username, image_id, folder)
            if submission_id:
                return submission_id
        return None

    def _find_submission_in_folder(self, username: str, image_id: int, folder: str) -> Optional[int]:
        page_listing = self._find_correct_page(username, image_id, folder)
        if not page_listing:
            # No page is valid.
            return None
        return self._find_submission_on_page(image_id, page_listing)

    def _find_submission_on_page(self, image_id: int, page_listing: List[FASubmissionShort]) -> Optional[int]:
        for submission in page_listing:
            test_image_id = self._get_image_id_from_submission(submission)
            if image_id == test_image_id:
                return int(submission.submission_id)
            if test_image_id < image_id:
                return None
        return None

    def _find_correct_page(self, username: str, image_id: int, folder: str) -> Optional[List[FASubmissionShort]]:
        page = 1
        while True:
            listing = self.api.get_user_folder(username, folder, page)
            if len(listing) == 0:
                return None
            last_submission = listing[-1]
            if self._get_image_id_from_submission(last_submission) <= image_id:
                return listing
            page += 1

    def _get_image_id_from_submission(self, submission: FASubmissionShort) -> int:
        image_id = re.split(r"[-.]", submission.thumbnail_url)[-2]
        return int(image_id)


class InlineFunctionality(BotFunctionality):

    def __init__(self, api: FAExportAPI):
        super().__init__(InlineQueryHandler)
        self.api = api

    def call(self, bot, update):
        query = update.inline_query.query
        query_clean = query.strip().lower()
        offset = update.inline_query.offset
        print(f"Got an inline query: {query}, page={offset}")
        if query_clean == "":
            bot.answer_inline_query(update.inline_query.id, [])
            return
        # Get results and next offset
        if any(query_clean.startswith(x) for x in ["favourites:", "favs:", "favorites:"]):
            _, username = query_clean.split(":", 1)
            results, next_offset = self._favs_query_results(username, offset)
        else:
            gallery_query = self._parse_folder_and_username(query_clean)
            if gallery_query:
                folder, username = gallery_query
                results, next_offset = self._gallery_query_results(folder, username, offset)
            else:
                results, next_offset = self._search_query_results(query, offset)
        # Send results
        bot.answer_inline_query(update.inline_query.id, results, next_offset=next_offset)

    def _favs_query_results(self, username: str, offset: str) -> Tuple[List[InlineQueryResult], Union[int, str]]:
        if offset == "":
            offset = None
        try:
            submissions = self.api.get_user_favs(username, offset)[:48]
        except PageNotFound:
            return self._user_not_found(username), ""
        # If no results, send error
        if len(submissions) > 0:
            next_offset = submissions[-1].fav_id
            if next_offset == offset:
                submissions = []
                next_offset = ""
        else:
            next_offset = ""
            if offset is None:
                return self._empty_user_favs(username), ""
        results = [x.to_inline_query_result() for x in submissions]
        return results, next_offset

    def _gallery_query_results(self, folder: str, username: str, offset: str) \
            -> Tuple[List[InlineQueryResult], Union[int, str]]:
        # Parse offset to page and skip
        if offset == "":
            page, skip = 1, None
        elif ":" in offset:
            page, skip = (int(x) for x in offset.split(":", 1))
        else:
            page, skip = int(offset), None
        # Default next offset
        next_offset = page + 1
        # Try and get results
        try:
            results = self._create_user_folder_results(username, folder, page)
        except PageNotFound:
            return self._user_not_found(username), ""
        # If no results, send error
        if len(results) == 0:
            next_offset = ""
            if page == 1:
                return self._empty_user_folder(username, folder), ""
        # Handle paging of big result lists
        if skip:
            results = results[skip:]
        if len(results) > 48:
            results = results[:48]
            if skip:
                skip += 48
            else:
                skip = 48
            next_offset = f"{page}:{skip}"
        return results, next_offset

    def _search_query_results(self, query: str, offset: str) -> Tuple[List[InlineQueryResult], Union[int, str]]:
        page = self._page_from_offset(offset)
        query_clean = query.strip().lower()
        next_offset = page + 1
        results = self._create_inline_search_results(query_clean, page)
        if len(results) == 0:
            next_offset = ""
            if page == 1:
                results = self._no_search_results_found(query)
        return results, next_offset

    def _page_from_offset(self, offset: str) -> int:
        if offset == "":
            offset = 1
        return int(offset)

    def _create_user_folder_results(self, username: str, folder: str, page: int) -> List[InlineQueryResultPhoto]:
        return [
            x.to_inline_query_result()
            for x
            in self.api.get_user_folder(username, folder, page)
        ]

    def _create_inline_search_results(self, query_clean: str, page: int) -> List[InlineQueryResultPhoto]:
        return [
            x.to_inline_query_result()
            for x
            in self.api.get_search_results(query_clean, page)
        ]

    def _parse_folder_and_username(self, query_clean: str) -> Optional[Tuple[str, str]]:
        if query_clean.startswith("gallery:") or query_clean.startswith("scraps:"):
            folder, username = query_clean.split(":", 1)
            return folder, username
        else:
            return None

    def _empty_user_folder(self, username: str, folder: str) -> List[InlineQueryResultArticle]:
        return [
            InlineQueryResultArticle(
                id=uuid.uuid4(),
                title=f"Nothing in {folder}.",
                input_message_content=InputTextMessageContent(
                    message_text=f"There are no submissions in {folder} for user \"{username}\"."
                )
            )
        ]

    def _empty_user_favs(self, username: str) -> List[InlineQueryResultArticle]:
        return [
            InlineQueryResultArticle(
                id=uuid.uuid4(),
                title=f"Nothing in favourites.",
                input_message_content=InputTextMessageContent(
                    message_text=f"There are no favourites for user \"{username}\"."
                )
            )
        ]

    def _no_search_results_found(self, query: str) -> List[InlineQueryResultArticle]:
        return [
            InlineQueryResultArticle(
                id=uuid.uuid4(),
                title="No results found.",
                input_message_content=InputTextMessageContent(
                    message_text=f"No results for search \"{query}\"."
                )
            )
        ]

    def _user_not_found(self, username: str) -> List[InlineQueryResultArticle]:
        return [
            InlineQueryResultArticle(
                id=uuid.uuid4(),
                title="User does not exist.",
                input_message_content=InputTextMessageContent(
                    message_text=f"FurAffinity user does not exist by the name: \"{username}\"."
                )
            )
        ]
