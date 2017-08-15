# encoding: utf-8
import os
import argparse
import sys
import time
from operator import itemgetter
from datetime import datetime

from workflow import ICON_WEB, ICON_USER, web, notify, Workflow, MATCH_CAPITALS, ICON_INFO
from workflow.background import run_in_background, is_running


class RBClientWrapper(object):

    def __init__(self):
        self.wf = Workflow(libraries=['./lib'])
        # rbtools path will only be added after wf is initialized
        from rbtools.api.client import RBClient

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
            time.sleep(1)
        self.wf.cache_data('users', users)
        self.wf.cache_data('users_list', users.keys())

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

    def get_review_request_info(self, request_id):
        return self._build_request_dict(
            self.root.get_review_request(review_request_id=request_id))

    def parse_argument(self):
        parser = argparse.ArgumentParser(prog='PROG')
        subparsers = parser.add_subparsers(help='sub-command help')
        config_parser = subparsers.add_parser('configure', help='configure help')
        config_parser.add_argument('--action-type', default='configure')
        config_parser.add_argument('--data-type')
        config_parser.add_argument('--data-value')

        search_parser = subparsers.add_parser('search', help='search help')
        search_parser.add_argument('--action-type', default='search')
        search_parser.add_argument('query_type', choices=['to_me', 'user', 'my'], default=None)
        search_parser.add_argument('extra_filters', nargs='*', default=[])

        update_parser = subparsers.add_parser('update_users', help='search help')
        update_parser.add_argument('--action-type', default='update_users')

        return parser.parse_args(self.wf.args)


    def main(self, wf):
        args = self.parse_argument()
        if args.action_type == 'configure':
            return self.store_config(args.data_type, args.data_value)

        if args.action_type == 'update_users':
            return self.update_users()

        elif args.action_type == 'search':
            if self.root is None:
                self.wf.add_item(
                    title="Not Configured",
                    subtitle="Please User r config to configure user, password and url",
                    valid=False
                )
                return self.wf.send_feedback()

            def parse_filters(filter_args):
                return dict(
                    filter_string.split(':')
                    for filter_string in filter_args
                    if len(filter_string.split(':')) == 2
                )

            if args.query_type == 'user':
                query_user = (args.extra_filters or [''])[0].split(':')
                if len(query_user) == 1:
                    search_user_prefix, = query_user
                    return self.search_users(search_user_prefix)
                else:
                    search_user, search_term = query_user
                    self.log_searched_user(search_user)
                    if len(args.extra_filters) > 1:
                        extra_filters = parse_filters(args.extra_filters[1:])
                    else:
                        extra_filters = {}
                    filters = {'from_user': search_user}
                    alias=search_user

                    # Build Custom row
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

                    if selected_user is None:
                        self.wf.add_item(
                            title='Could Not find user',
                            icon=ICON_INFO,
                            valid=False)
                        return self.wf.send_feedback()

            if args.query_type == 'my':
                extra_filters = parse_filters(args.extra_filters)
                filters = {'from_user': self.username}
                search_term = ([
                    row for row in args.extra_filters
                    if len(row.split(':')) == 1] or [''])[0]
                alias = self.username

            if args.query_type == 'to_me':
                extra_filters = parse_filters(args.extra_filters)
                filters = {'to_users_directly': self.username}
                search_term = ([
                    row for row in args.extra_filters
                    if len(row.split(':')) == 1] or [''])[0]
                alias = 'to_me'

            filters['status'] = 'all'
            func = lambda: self._search_review_requests(**filters)
            rows = self.wf.cached_data(
                    '{}_requests'.format(alias), func, max_age=60 * 15)
            if extra_filters:
                rows = filter(
                    lambda row: all(
                        row[key] == value
                        for key, value in extra_filters.iteritems()),
                    rows)
            if search_term.strip() != '':
                rows = self.wf.filter(search_term, rows, itemgetter('summary'))
            return self.build_items(rows)


    def log_searched_user(self, username):
        user_search_history = self.wf.stored_data('recent_users') or []
        user_search_history = [username] + [
            user for user in user_search_history if user != username]
        self.wf.store_data('recent_users', user_search_history[:10])

    def search_users(self, prefix):
        user_search_history = self.wf.stored_data('recent_users') or []
        if prefix.strip() != '':
            matched_users = self.wf.filter(prefix, user_search_history)
        else:
            matched_users = user_search_history

        if not self.wf.cached_data_fresh('users_list', 86400):
            run_in_background(
                'update_users', [
                    '/usr/bin/python',
                    self.wf.workflowfile('reviewboard.py'),
                    'update_users'])
        if is_running('update_users'):
            self.wf.add_item('Updating users', icon=ICON_INFO)
        user_caches = self.wf.cached_data('users', max_age=0) or dict()

        if len(matched_users) == 0 and prefix.strip() != '':
            users_list = self.wf.cached_data('users_list', max_age=0) or set()
            matched_users = self.wf.filter(
                prefix,
                users_list)
        self.build_user_items([
            (user, user_caches[user]) for user in matched_users])

    def store_config(self, data_type, value):
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
                icon="./icon.png")

        # Send the results to Alfred as XML
        self.wf.send_feedback()


if __name__ == u"__main__":
    wrapper = RBClientWrapper()
    sys.exit(wrapper.wf.run(wrapper.main))
