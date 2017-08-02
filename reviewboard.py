# encoding: utf-8
import os
import argparse
import sys
import time
from operator import itemgetter
from datetime import datetime

from workflow import ICON_WEB, ICON_USER, web, notify, Workflow, MATCH_CAPITALS
from workflow.background import run_in_background, is_running
from rbtools.api.client import RBClient


class RBClientWrapper(object):

    def __init__(self):
        self.wf = Workflow()
        login_info = self.wf.stored_data('login_info') or {}
        self.username = login_info.get('user', None)
        self.url = login_info.get('url', None)
        try:
            password = self.wf.get_password('review_board')
        except:
            password = None

        if self.username is not None and self.url is not None and password is not None:
            client = RBClient(self.url, username=self.username, password=password)
            self.root = client.get_root()
        else:
            self.root = None

    def update_users(self):
        count = self.root.get_users(counts_only=True).count
        users = {}
        user_columns = ['username', 'fullname', 'avatar_url']
        for start in range(1, count + 1, 200):
            for user in self.root.get_users(start=start, max_results=200):
                users[user['username']] = {
                    col: getattr(user, col) for col in user_columns}
            print start, len(users)
        self.wf.cache_data('users', users)
        time.sleep(1)

    def _build_request_dict(self, request):
        return {
            'id': request.id,
            'summary': request.summary,
            'time_added': datetime.strptime(request.time_added, '%Y-%m-%dT%H:%M:%SZ'),
            'last_updated': datetime.strptime(request.last_updated, '%Y-%m-%dT%H:%M:%SZ'),
            'ship_it_count': request.ship_it_count,
            'status': request.status,
            'submitter': request.links.submitter.title,
            'repo': request.links.repository.title,
            'target_people': [p.title for p in request.target_people],
            'absolute_url': request.absolute_url,
            'primary_reviewers': sorted(
                [u.strip()
                 for u in getattr(request.extra_data, 'primary_reviewers', '').split(',')],
                key=lambda name: name != self.username)
        }

    def _search_review_requests(self, **kwargs):
        """
        https://www.reviewboard.org/docs/manual/dev/webapi/2.0/resources/
        review-request-list/#webapi2.0-review-request-list-resource
        """
        return map(self._build_request_dict, self.root.get_review_requests(**kwargs))

    def search_requests_to_me(self):
        notify.notify(title='search_to_user', text='username: %s' % self.username)
        requests = self._search_review_requests(
                to_users_directly=self.username, status='pending')
        def _sort_key(r):
            return [
                # First check if I am the primary reviewer,
                (self.username not in r['primary_reviewers']),
                # Check if I am on the reviewer list
                (self.username not in r['target_people']),
                # No one show the love yet,
                r['ship_it_count'] != 0,
                # look for latest_updated
                datetime.now() - r['last_updated'],
            ]
        return sorted(requests, key=_sort_key)

    def search_my_open_requests(self):
        requests = self._search_review_requests(
            from_user=self.username, status='pending')
        def _sort_key(r):
            return [
                # No one show the love yet,
                r['ship_it_count'] == 0,
                # look for latest_updated
                datetime.now() - r['last_updated'],
                datetime.now() - r['time_added']
            ]

        return sorted(requests, key=_sort_key)[:10]

    def search_user_requests(self, username):
        requests = self._search_review_requests(
            from_user=username, status='all')

        def _sort_key(r):
            return [
                # look for latest_updated
                datetime.now() - r['last_updated'],
            ]
        return sorted(requests, key=_sort_key)[:10]

    def get_review_request_info(self, request_id):
        return self._build_request_dict(
            self.root.get_review_request(review_request_id=request_id))

    def parse_argument(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('action_type', default=False)
        parser.add_argument('--data-type')
        parser.add_argument('--data-value')

        parser.add_argument('--query-user', dest='query_user', nargs=1, default=None)
        parser.add_argument('--query-my', dest='query_my', action='store_true', default=False)
        parser.add_argument('--query-to-me', dest='query_to_me', action='store_true', default=False)
        parser.add_argument('--query-number', dest='query_number', nargs=1, default=None)
        parser.add_argument('--to-log', dest='to_log', nargs=1, default=None)
        return parser.parse_args(self.wf.args)

    def main(self, wf):
        args = self.parse_argument()
        if args.action_type == 'configure':
            return store_data(args.data_type, args.data_value)

        if args.action_type == 'update_users':
            return self.update_users()

        elif args.action_type == 'search':
            if self.root is None:
                print str(e)
                return build_login_config_items()

            if args.query_user:
                query_user = args.query_user[0].split(':')
                if len(query_user) == 1:
                    search_user_prefix, = query_user
                    return self.search_users(search_user_prefix)
                else:
                    search_user, search = query_user
                    selected_user = (
                        self.wf.cached_data('users', max_age=0) or {}
                    ).get(search_user, {})
                    self.wf.add_item(
                        title=selected_user['fullname'],
                        subtitle="Go to %s page directly" % selected_user['username'],
                        arg=os.path.join(self.url, 'users', selected_user['username']),
                        icon=ICON_WEB,
                        valid=True)
                    rows = []
                    if selected_user is not None:
                        func = lambda: self.search_user_requests(search_user)
                        rows = self.wf.cached_data(
                            '{}_requests'.format(search_user), func, max_age=60 * 15)
                        if search.strip() != '':
                            rows = self.wf.filter(search, rows, itemgetter('summary'))
                    return self.build_items(rows)

            elif args.query_my:
                func = lambda: self.search_my_open_requests()
                rows = self.wf.cached_data('my_requests', func, max_age=60 * 15)
                return self.build_items(rows)
            elif args.query_to_me:
                func = lambda: self.search_requests_to_me()
                rows = self.wf.cached_data('request_to_me', func, max_age=0)
                return self.build_items(rows)
            else:
                return self.build_default_items()

    def search_users(self, prefix):
        if not self.wf.cached_data_fresh('users', 86400):
            run_in_background(
                'update_users', [
                    '/usr/bin/python',
                    self.wf.workflowfile('reviewboard.py'),
                    'update_users'])

        if is_running('update_users'):
            self.wf.add_item('Updating users', icon=ICON_INFO)

        user_caches = self.wf.cached_data('users', max_age=0) or dict()
        matched_users = self.wf.filter(
            prefix,
            user_caches.items(),
            key=itemgetter(0))
        self.build_user_items(matched_users)

    def store_data(self, data_type, value):
        try:
            if data_type == 'password':
                self.wf.save_password('review_board', value)
            else:
                login_info = self.wf.stored_data('login_info') or {}
                login_info[data_type] = value
                self.wf.store_data('login_info', login_info)
                stored = self.wf.stored_data('login_info')
                assert stored == login_info
            return notify.notify(title='Success', text='Your data is saved')
        except Exception as e:
            return notify.notify(title='Error!', text=str(e))

    def build_user_items(self, rows):
        for username, row in rows[:10]:
            self.wf.add_item(
                title=row['fullname'],
                subtitle=row['username'],
                arg=row['username'],
                valid=True,
                icon=ICON_USER)
        self.wf.send_feedback()

    def build_items(self, rows):
        # Loop through the returned posts and add an item for each to
        # the list of results for Alfred
        for row in rows[:10]:
            self.wf.add_item(
                title='[{id}] {summary}'.format(
                    id=row['id'],
                    summary=row['summary']
                ),
                subtitle='{submitter} last_updated: {last_updated} primary: {primary_reviewers}'.format(
                   submitter=row['submitter'],
                   last_updated=row['last_updated'].strftime('%Y-%m-%d'),
                   primary_reviewers=','.join(row['primary_reviewers'][:2])
                ),
                arg=row['absolute_url'],
                valid=True,
                icon='./icon.png')

        # Send the results to Alfred as XML
        self.wf.send_feedback()


if __name__ == u"__main__":
    wrapper = RBClientWrapper()
    sys.exit(wrapper.wf.run(wrapper.main))
