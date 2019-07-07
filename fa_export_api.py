from typing import List

import requests

from fa_submission import FASubmission, FASubmissionShort, FASubmissionFull


class PageNotFound(Exception):
    pass


class FAExportAPI:

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _api_request(self, path: str) -> requests.Response:
        path = path.lstrip("/")
        return requests.get(f"{self.base_url}/{path}")

    def get_full_submission(self, submission_id: str) -> FASubmissionFull:
        sub_resp = self._api_request(f"submission/{submission_id}.json")
        # If API returns fine
        if sub_resp.status_code == 200:
            submission = FASubmission.from_full_dict(sub_resp.json())
            return submission
        else:
            raise PageNotFound(f"Submission not found with ID: {submission_id}")

    def get_user_folder(self, user: str, folder: str, page: int = 1) -> List[FASubmissionShort]:
        if user.strip() == "":
            raise PageNotFound(f"User not found by name: {user}")
        resp = self._api_request(f"user/{user}/{folder}.json?page={page}&full=1&perpage=48")
        if resp.status_code == 200:
            data = resp.json()
            submissions = []
            for submission_data in data:
                submissions.append(FASubmission.from_short_dict(submission_data))
            return submissions
        else:
            raise PageNotFound(f"User not found by name: {user}")

    def get_search_results(self, query: str, page: int = 1) -> List[FASubmissionShort]:
        resp = self._api_request(f"search.json?full=1&perpage=48&q={query}&page={page}")
        data = resp.json()
        submissions = []
        for submission_data in data:
            submissions.append(FASubmission.from_short_dict(submission_data))
        return submissions
