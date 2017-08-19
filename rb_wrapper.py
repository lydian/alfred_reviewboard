# We need to import workflow so that it is able to find the lib/
import os
import time
from datetime import datetime

from workflow import Workflow

Workflow(libraries=['./lib'])
from rbtools.api.client import RBClient

class RBWrapper(object):

    def __init__(self, user, password, url):
        if any(v is None for v in [url, user, password]):
            raise ValueError("Unable to login,'{}', '{}', '{}']".format(
                user, password, url))
        self.user = user
        self.url = url
        self.client = RBClient(url, username=user, password=password)

    @property
    def root(self):
        return self.client.get_root()

    def get_user_lists(self):
        """Return dict of all users, dict key is user handle, and value
        is a dict containing 'username', 'fullname' and 'avatar_url'
        """
        count = self.root.get_users(counts_only=True).count
        users = {}
        user_columns = ['username', 'fullname', 'avatar_url']
        for start in range(1, count + 1, 200):
            for user in self.root.get_users(start=start, max_results=200):
                users[user['username']] = {
                    col: getattr(user, col) for col in user_columns}
            time.sleep(1)
        return users

    def search(self, **filters):
        """search list of reviews based on the given filters

        for available filters:
        https://www.reviewboard.org/docs/manual/dev/webapi/2.0/resources/
        review-request-list/#webapi2.0-review-request-list-resource
        """
        def _parse_time(t):
            return datetime.strptime(t, '%Y-%m-%dT%H:%M:%SZ')

        def _build_request_dict(request):
            return {
                'id': request.id,
                'summary': request.summary,
                'time_added': _parse_time(request.time_added),
                'last_updated': _parse_time(request.last_updated),
                'ship_it_count': request.ship_it_count,
                'status': request.status,
                'submitter': request.links.submitter.title,
                'repo': request.links.repository.title,
                'target_people': [p.title for p in request.target_people],
                'absolute_url': request.absolute_url,
                'primary_reviewers': sorted([
                    u.strip()
                    for u in getattr(
                        request.extra_data, 'primary_reviewers', ''
                    ).split(',')
                    if u.strip() != ''],
                    key=lambda name: name != self.user)
                }
        return map(
            _build_request_dict,
            self.root.get_review_requests(**filters))

    def search_cr_from(self, username=None):
        """shortcut for search cr from specific user
        if no username is given, search "my" crs
        """
        if username is None:
            username = self.user
        return self.search(from_user=username, status='all')

    def search_cr_to(self, username=None):
        """shortcut for search cr to specific user
        if no username is given, search "my" crs
        """
        if username is None:
            username = self.user
        return self.search(to_users_directly=username, status='all')

    def get_user_cr_url(self, username=None):
        if username is None:
            username = self.user
        return os.path.join(self.url, 'users', username)

    def get_cr_url(self, cr_id):
        return os.path.join(self.url, 'r', str(cr_id))

    def get_dashboard_url(self):
        return os.path.join(self.url, 'dashboard')
