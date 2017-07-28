# encoding: utf-8
from datetime import datetime
import argparse
import sys
import time
from operator import itemgetter

from workflow import Workflow, ICON_WEB, web, notify
from rbtools.api.client import RBClient


class RBClientWrapper(object):

    def __init__(self, url, username, password):
        self.username = username
        client = RBClient(url, username=username, password=password)
        self.root = client.get_root()

    def search_users(self, prefix):
        users = self.root.get_users(q=prefix)
        user_columns = ['username', 'fullname', 'avatar_url']
        return [
            {col: getattr(user, col) for col in user_columns}
            for user in users
        ]

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



def parse_argument():
    parser = argparse.ArgumentParser()
    parser.add_argument('action_type', default=False)
    parser.add_argument('--data-type')
    parser.add_argument('--data-value')

    parser.add_argument('--search-user', dest='search_user', default=None)
    parser.add_argument('--query-user', dest='query_user', nargs=1, default=None)
    parser.add_argument('--query-my', dest='query_my', action='store_true', default=False)
    parser.add_argument('--query-to-me', dest='query_to_me', action='store_true', default=False)
    parser.add_argument('--query-number', dest='query_number', nargs=1, default=None)
    parser.add_argument('--to-log', dest='to_log', nargs=1, default=None)
    return parser.parse_args(wf.args)


def main(wf):
    args = parse_argument()
    data = maybe_get_data(wf)
    if args.action_type == 'configure':
        return store_data(wf, args.data_type, args.data_value)

    if args.action_type == 'log':
        return log_recent_selections(wf, args.to_log, data)

    elif args.action_type == 'search':
        try:
            rb_wrapper = get_wrapper(wf)
        except Exception as e:
            print str(e)
            return build_login_config_items(wf)

        if args.search_user:
            func = lambda: rb_wrapper.search_users(args.search_user)
            rows = wf.cached_data('users', func, max_age=60 * 15)
            return build_user_items(wf, rows)

        if args.query_user:
            func = lambda: rb_wrapper.search_user_requests(args.query_user)
            rows = wf.cached_data(
                '{}_requests'.format(args.query_user), func, max_age=60 * 15)
            return build_items(wf, rows)
        elif args.query_my:
            func = lambda: rb_wrapper.search_my_open_requests()
            rows = wf.cached_data('my_requests', func, max_age=60 * 15)
            return build_items(wf, rows)
        elif args.query_to_me:
            func = lambda: rb_wrapper.search_requests_to_me()
            rows = wf.cached_data('request_to_me', func, max_age=0)
            return build_items(wf, rows)
        else:
            return build_default_items()


def store_data(wf, data_type, value):
    try:
        if data_type == 'password':
            wf.save_password('review_board', value)
        else:
            login_info = wf.stored_data('login_info') or {}
            login_info[data_type] = value
            wf.store_data('login_info', login_info)
            stored = wf.stored_data('login_info')
            assert stored == login_info
        return notify.notify(title='Success', text='Your data is saved')

    except Exception as e:
        return notify.notify(title='Error!', text=str(e))


def get_wrapper(wf):
    login_info = wf.stored_data('login_info') or {}
    user = login_info.get('user', None)
    url = login_info.get('url', None)
    try:
        password = wf.get_password('review_board')
    except:
        password = None

    if user is None or url is None or password is None:
        raise ValueError('Please Configure login info first')
    return RBClientWrapper(url, user, password)


def maybe_get_data(wf):
    # Retrieve posts from cache if available and no more than 600
    # seconds old
    data = wf.stored_data('data')
    if data is None:
        data = {}
    if time.time() - data.get('queried_time', 0) >= 60 * 10:
        rows = get_rows()
        data['rows'] = rows
        data['queried_time'] = time.time()
    wf.store_data('data', data)
    return data


def get_rows():
    url = 'https://y.yelpcorp.com/api/list'
    r = web.get(url)

    # throw an error if request failed
    # Workflow will catch this and show it to the user
    r.raise_for_status()

    # Parse the JSON returned by pinboard and extract the posts
    result = r.json()
    return result['data']



def build_items(wf, rows):
    # Loop through the returned posts and add an item for each to
    # the list of results for Alfred
    for row in rows[:10]:
        wf.add_item(
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
            icon=ICON_WEB)

    # Send the results to Alfred as XML
    wf.send_feedback()


def log_recent_selections(wf, selected_url, data):
    selected = filter(lambda (name, url): url == selected_url, data['rows'])
    if len(selected) > 0:
        data['recent'] = [selected[0]] + data.get('recent', [])
    data['recent'] = data['recent'][:10]
    wf.store_data('data', data)





if __name__ == u"__main__":
    wf = Workflow()
    sys.exit(wf.run(main))
