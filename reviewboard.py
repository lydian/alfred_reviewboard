# encoding: utf-8
import argparse
import os
import re
import subprocess
import sys
import time
from operator import itemgetter
from datetime import datetime

from workflow import ICON_INFO
from workflow import ICON_SETTINGS
from workflow import ICON_USER
from workflow import ICON_WEB
from workflow import MATCH_CAPITALS
from workflow import notify
from workflow import Variables
from workflow import web
from workflow import Workflow
from workflow.background import is_running
from workflow.background import run_in_background

from rb_wrapper import RBWrapper


__version__ = '1.0.0'
WF_CONFIG = {
    'github_slug': 'lydian/alfred_reviewboard',
    'version': __version__,
    'frequency': 7,
    'prereleases': '-beta' in __version__
}
LIMIT = 8


class RBFlow(object):

    def __init__(self):
        self.wf = Workflow(update_settings=WF_CONFIG, libraries=['./lib'])

    def get_login_info(self):
        login_info = self.wf.stored_data('login_info') or {}
        try:
            password = self.wf.get_password('review_board')
        except:
            password = None
        url = login_info.get('url', None)
        username = login_info.get('user', None)
        return {'user': username, 'url': url, 'password': password}

    def get_rb_wrapper(self):
        login_info = self.get_login_info()
        return RBWrapper(
            login_info['user'], login_info['password'], login_info['url'])

    def parse_argument(self):
        parser = argparse.ArgumentParser(prog='ReviewBoard')
        subparsers = parser.add_subparsers(dest='action_type')

        config_parser = subparsers.add_parser('configure')
        config_parser.add_argument('data_type', nargs='?', default=None)
        config_parser.add_argument('data_value', nargs='?', default=None)
        config_parser.add_argument('--save', action='store_true', default=False)

        subparsers.add_parser('update_users')

        search_parser = subparsers.add_parser('search', help='search help')
        search_subparsers = search_parser.add_subparsers(dest='query_type')

        to_me_parser = search_subparsers.add_parser('to_me')
        to_me_parser.add_argument('extra_filter', nargs='*', default=[])

        my_parser = search_subparsers.add_parser('my')
        my_parser.add_argument('extra_filter', nargs='*', default=[])

        user_parser = search_subparsers.add_parser('user')
        user_parser.add_argument('--username', dest='search_user', default='')
        user_parser.add_argument('extra_filter', nargs='*', default=[])

        launch_parser = subparsers.add_parser('launch')
        launch_parser.add_argument('launch_args', default='')

        return parser.parse_args(self.wf.args)

    def main(self, wf):
        args = self.parse_argument()
        if args.action_type == 'configure':
            if args.save:
                return self.store_config(args)
            else:
                return self.configure(args)

        wrapper = self.get_rb_wrapper()
        if args.action_type == 'update_users':
            return self.update_users(wrapper)

        if args.action_type == 'search':
            if args.query_type == 'user':
                return self.query_user_crs(wrapper, args)

            if args.query_type == 'my':
                return self.query_my_crs(wrapper, args)

            if args.query_type == 'to_me':
                return self.query_to_me_crs(wrapper, args)

        if args.action_type == "launch":
            return self.launch(wrapper, args)

    def update_users(self, wrapper):
        wrapper = self.get_rb_wrapper()
        user_dicts = wrapper.get_user_lists()
        self.wf.cache_data('users', user_dicts)
        self.wf.cache_data('users_list', user_dicts.keys())

    def _parse_filters(self, filter_args):
        search_term = []
        extra_filter = {}

        for filter_string in filter_args:
            parsed_filter = filter_string.split(':')
            if len(parsed_filter) == 2:
                extra_filter[parsed_filter[0]] = parsed_filter[1]
            else:
                search_term.append(parsed_filter[0])
        return [search_term, extra_filter]

    def _filter_cr(self, rows, search_terms, extra_filter):
        if extra_filter:
            rows = filter(
                lambda row: all(
                    row[key] == value
                    for key, value in extra_filter.iteritems()),
                rows)
        for search_term in search_terms:
            rows = self.wf.filter(search_term.strip(), rows, itemgetter('summary'))
        return rows

    def search_user_name(self, prefix, limit=LIMIT):
        user_search_history = self.wf.stored_data('recent_users') or []
        if prefix.strip() != '':
            matched_recent_users = self.wf.filter(prefix, user_search_history)
        else:
            matched_recent_users = user_search_history

        if not self.wf.cached_data_fresh('users_list', 86400):
            run_in_background(
                'update_users', [
                    '/usr/bin/python',
                    self.wf.workflowfile('reviewboard.py'),
                    'update_users'])

        # if is_running('update_users'):
        #    self.wf.add_item('Updating users', icon=ICON_INFO)
        user_caches = self.wf.cached_data('users', max_age=0) or dict()
        users_list = self.wf.cached_data('users_list', max_age=0) or set()

        if prefix.strip() != '':
            matched_cached_users = self.wf.filter(
                prefix,
                users_list)
        else:
            matched_cached_users = users_list

        # remove duplicated
        matched_users = matched_recent_users + [
            user
            for user in matched_cached_users
            if user not in set(matched_recent_users)]

        return [
            user_caches[user] for user in matched_users[:limit]
        ]

    def query_user_crs(self, wrapper, args):
        user_rows = self.search_user_name(args.search_user)
        if len(user_rows) == 1:
            selected = user_rows[0]
            cr_rows = self.wf.cached_data(
                '{}_requests'.format(selected['username']),
                lambda: wrapper.search_cr_from(selected['username']),
                max_age=60 * 15)
        else:
            cr_rows = []

        cr_rows = self._filter_cr(
            cr_rows,
            *self._parse_filters(args.extra_filter))

        if not cr_rows:  # Show users
            self.build_user_items(user_rows)

        else:   # List CRs
            self.build_items(cr_rows[:LIMIT])

        user_url = wrapper.get_user_cr_url(args.search_user)
        self.wf.add_item(
            title='Go to {}\'s page directly'.format(args.search_user),
            subtitle=user_url,
            arg=user_url,
            icon=ICON_WEB,
            valid=True)
        self.wf.send_feedback()

    def query_my_crs(self, wrapper, args):
        cr_rows = self.wf.cached_data(
            '{}_requests'.format(wrapper.user),
            lambda: wrapper.search_cr_from(),
            max_age=60 * 15)
        cr_rows = self._filter_cr(
            cr_rows,
            *self._parse_filters(args.extra_filter))

        self.build_items(cr_rows[:LIMIT])
        user_url = wrapper.get_user_cr_url()
        self.wf.add_item(
            title='Go to my page directly',
            subtitle=user_url,
            arg=user_url,
            icon=ICON_WEB,
            valid=True
        )
        self.wf.send_feedback()

    def query_to_me_crs(self, wrapper, args):
        wrapper = self.get_rb_wrapper()
        cr_rows = self.wf.cached_data(
            'requests_to_me',
            lambda: wrapper.search_cr_to(),
            max_age=60 * 15)
        cr_rows = self._filter_cr(
            cr_rows,
            *self._parse_filters(args.extra_filter))

        self.build_items(cr_rows[:LIMIT])
        dashboard_url = wrapper.get_dashboard_url()
        self.wf.add_item(
            title='Go to my dashboard directly',
            subtitle=dashboard_url,
            arg=dashboard_url,
            icon=ICON_WEB,
            valid=True
        )
        self.wf.send_feedback()

    def log_searched_user(self, username):
        user_search_history = self.wf.stored_data('recent_users') or []
        user_search_history = [username] + [
            user for user in user_search_history if user != username]
        self.wf.store_data('recent_users', user_search_history[:10])

    def configure(self, args):
        self.build_config_items(args.data_type, args.data_value)
        return self.wf.send_feedback()

    def store_config(self, args):
        try:
            if args.data_type == 'password':
                self.wf.save_password('review_board', args.data_value)
            else:
                login_info = self.get_login_info()
                login_info[args.data_type] = args.data_value
                self.wf.store_data('login_info', login_info)
                stored = self.wf.stored_data('login_info')
                assert stored == login_info
            return notify.notify(
                title='Success', text='{} is saved'.format(args.data_type))
        except Exception as e:
            return notify.notify(title='Error!', text=str(e))

    def build_config_items(self, data_type=None, data_value=None):
        login_info = self.get_login_info()
        def get_value(key):
            value = login_info.get(key, None)
            if key == 'password' and value:
                value = 'Configured'
            return value if value else 'Not Configured'
        if data_type is None:
            config_item = ['user', 'password', 'url']
        else:
            config_item = [data_type]
        for key in config_item:
            if key == data_type:
                subtitle_append = ', new value: {}'.format(data_value)
            else:
                subtitle_append = ''
            self.wf.add_item(
                title='configure {}'.format(key),
                subtitle='current value: {}'.format(get_value(key)) + subtitle_append,
                autocomplete=key + ' ',
                valid=data_value is not None,
                arg='{} {}'.format(data_type, data_value),
                icon=ICON_SETTINGS)

    def build_user_items(self, rows):
        for row in rows[:10]:
            self.wf.add_item(
                title=row['fullname'],
            subtitle=row['username'],
            autocomplete=row['username']+ ' ',
            valid=True,
            icon=ICON_USER)

    def build_items(self, rows):
        # Loop through the returned posts and add an item for each to
        # the list of results for Alfred
        for row in rows[:10]:
            reviewers = list(set(
                row.get('primary_reviewers', []) +  row['target_people']))
            self.wf.add_item(
                title='[{id}] {summary}'.format(
                    id=row['id'],
                    summary=row['summary']
                ),
                subtitle='{submitter} last_updated: {last_updated} reviewer: {reviewers}'.format(
                   submitter=row['submitter'],
                   last_updated=row['last_updated'].strftime('%Y-%m-%d'),
                   reviewers=','.join(reviewers[:2])
                ),
                arg='review-{}'.format(row['id']),
                valid=True,
                icon="./icon.png")


    def launch(self, wrapper, args):
        launch_args = args.launch_args

        if re.match('^user-', launch_args):
            username = launch_args.replace('user-', '')
            # TODO: log_user
            return

        url = None
        if re.match('^http[s]?://', launch_args):
            url = launch_args

        if re.match('review-', launch_args):
            cr_id = launch_args.replace('review-', '')
            # TODO: log_review
            url = wrapper.get_cr_url(cr_id)

        if url:
            subprocess.call(['open', url])


if __name__ == u"__main__":
    flow = RBFlow()
    sys.exit(flow.wf.run(flow.main))
