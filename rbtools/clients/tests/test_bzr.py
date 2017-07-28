"""Unit tests for BazaarClient."""

from __future__ import unicode_literals

import os
from hashlib import md5
from tempfile import mktemp

from nose import SkipTest

from rbtools.clients import RepositoryInfo
from rbtools.clients.bazaar import BazaarClient
from rbtools.clients.errors import TooManyRevisionsError
from rbtools.clients.tests import FOO, FOO1, FOO2, FOO3, SCMClientTests
from rbtools.utils.process import execute


class BazaarClientTests(SCMClientTests):
    """Unit tests for BazaarClient."""

    def setUp(self):
        super(BazaarClientTests, self).setUp()

        if not self.is_exe_in_path("bzr"):
            raise SkipTest("bzr not found in path")

        self.set_user_home(
            os.path.join(self.testdata_dir, 'homedir'))

        self.orig_dir = os.getcwd()

        self.original_branch = self.chdir_tmp()
        self._run_bzr(["init", "."])
        self._bzr_add_file_commit("foo.txt", FOO, "initial commit")

        self.child_branch = mktemp()
        self._run_bzr(["branch", self.original_branch, self.child_branch])
        self.client = BazaarClient(options=self.options)
        os.chdir(self.orig_dir)

        self.options.parent_branch = None

    def _run_bzr(self, command, *args, **kwargs):
        return execute(['bzr'] + command, *args, **kwargs)

    def _bzr_add_file_commit(self, file, data, msg):
        """Add a file to a Bazaar repository with the content of data
        and commit with msg."""
        with open(file, 'w') as foo:
            foo.write(data)
        self._run_bzr(["add", file])
        self._run_bzr(["commit", "-m", msg, '--author', 'Test User'])

    def _compare_diffs(self, filename, full_diff, expected_diff_digest,
                       change_type='modified'):
        """Testing that the full_diff for ``filename`` matches the
        ``expected_diff``."""
        diff_lines = full_diff.splitlines()

        self.assertEqual(('=== %s file \'%s\''
                          % (change_type, filename)).encode('utf-8'),
                         diff_lines[0])
        self.assertTrue(diff_lines[1].startswith(
            ('--- %s\t' % filename).encode('utf-8')))
        self.assertTrue(diff_lines[2].startswith(
            ('+++ %s\t' % filename).encode('utf-8')))

        diff_body = b'\n'.join(diff_lines[3:])
        self.assertEqual(md5(diff_body).hexdigest(), expected_diff_digest)

    def _count_files_in_diff(self, diff):
        return len([
            line
            for line in diff.split(b'\n')
            if line.startswith(b'===')
        ])

    def test_get_repository_info_original_branch(self):
        """Testing BazaarClient get_repository_info with original branch"""
        os.chdir(self.original_branch)
        ri = self.client.get_repository_info()

        self.assertTrue(isinstance(ri, RepositoryInfo))
        self.assertEqual(os.path.realpath(ri.path),
                         os.path.realpath(self.original_branch))
        self.assertTrue(ri.supports_parent_diffs)

        self.assertEqual(ri.base_path, '/')
        self.assertFalse(ri.supports_changesets)

    def test_get_repository_info_child_branch(self):
        """Testing BazaarClient get_repository_info with child branch"""
        os.chdir(self.child_branch)
        ri = self.client.get_repository_info()

        self.assertTrue(isinstance(ri, RepositoryInfo))
        self.assertEqual(os.path.realpath(ri.path),
                         os.path.realpath(self.child_branch))
        self.assertTrue(ri.supports_parent_diffs)

        self.assertEqual(ri.base_path, "/")
        self.assertFalse(ri.supports_changesets)

    def test_get_repository_info_no_branch(self):
        """Testing BazaarClient get_repository_info, no branch"""
        self.chdir_tmp()
        ri = self.client.get_repository_info()
        self.assertEqual(ri, None)

    def test_too_many_revisions(self):
        """Testing BazaarClient parse_revision_spec with too many revisions"""
        self.assertRaises(TooManyRevisionsError,
                          self.client.parse_revision_spec,
                          [1, 2, 3])

    def test_diff_simple(self):
        """Testing BazaarClient simple diff case"""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "delete and modify stuff")

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs('foo.txt', result['diff'],
                            'a6326b53933f8b255a4b840485d8e210')

    def test_diff_exclude(self):
        """Testing BazaarClient diff with file exclusion."""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        self._bzr_add_file_commit("exclude.txt", FOO2, "commit 2")

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions, exclude_patterns=['exclude.txt'])
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs('foo.txt', result['diff'],
                            'a6326b53933f8b255a4b840485d8e210')

        self.assertEqual(self._count_files_in_diff(result['diff']), 1)

    def test_diff_exclude_in_subdir(self):
        """Testing BazaarClient diff with file exclusion in a subdirectory."""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit('foo.txt', FOO1, 'commit 1')

        os.mkdir('subdir')
        os.chdir('subdir')

        self._bzr_add_file_commit('exclude.txt', FOO2, 'commit 2')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions,
                                  exclude_patterns=['exclude.txt', '.'])
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs('foo.txt', result['diff'],
                            'a6326b53933f8b255a4b840485d8e210')

        self.assertEqual(self._count_files_in_diff(result['diff']), 1)

    def test_diff_exclude_root_pattern_in_subdir(self):
        """Testing BazaarClient diff with file exclusion in the repo root."""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit('exclude.txt', FOO2, 'commit 1')

        os.mkdir('subdir')
        os.chdir('subdir')

        self._bzr_add_file_commit('foo.txt', FOO1, 'commit 2')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(
            revisions,
            exclude_patterns=[os.path.sep + 'exclude.txt',
                              os.path.sep + 'subdir'])

        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs(os.path.join('subdir', 'foo.txt'), result['diff'],
                            '4deffcb296180fa166eddff2512bd0e4',
                            change_type='added')

    def test_diff_specific_files(self):
        """Testing BazaarClient diff with specific files"""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "delete and modify stuff")
        self._bzr_add_file_commit("bar.txt", "baz", "added bar")

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions, ['foo.txt'])
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs('foo.txt', result['diff'],
                            'a6326b53933f8b255a4b840485d8e210')

    def test_diff_simple_multiple(self):
        """Testing BazaarClient simple diff with multiple commits case"""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        self._bzr_add_file_commit("foo.txt", FOO3, "commit 3")

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs('foo.txt', result['diff'],
                            '4109cc082dce22288c2f1baca9b107b6')

    def test_diff_parent(self):
        """Testing BazaarClient diff with changes only in the parent branch"""
        os.chdir(self.child_branch)
        self._bzr_add_file_commit("foo.txt", FOO1, "delete and modify stuff")

        grand_child_branch = mktemp()
        self._run_bzr(["branch", self.child_branch, grand_child_branch])
        os.chdir(grand_child_branch)

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self.assertEqual(result['diff'], None)

    def test_diff_grand_parent(self):
        """Testing BazaarClient diff with changes between a 2nd level
        descendant"""
        os.chdir(self.child_branch)
        self._bzr_add_file_commit("foo.txt", FOO1, "delete and modify stuff")

        grand_child_branch = mktemp()
        self._run_bzr(["branch", self.child_branch, grand_child_branch])
        os.chdir(grand_child_branch)

        # Requesting the diff between the grand child branch and its grand
        # parent:
        self.options.parent_branch = self.original_branch

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)
        self.assertTrue(isinstance(result, dict))
        self.assertTrue('diff' in result)

        self._compare_diffs("foo.txt", result['diff'],
                            'a6326b53933f8b255a4b840485d8e210')

    def test_guessed_summary_and_description(self):
        """Testing BazaarClient guessing summary and description"""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        self._bzr_add_file_commit("foo.txt", FOO3, "commit 3")

        self.options.guess_summary = True
        self.options.guess_description = True
        revisions = self.client.parse_revision_spec([])
        commit_message = self.client.get_commit_message(revisions)

        self.assertEqual("commit 3", commit_message['summary'])

        description = commit_message['description']
        self.assertTrue("commit 1" in description)
        self.assertTrue("commit 2" in description)
        self.assertFalse("commit 3" in description)

    def test_guessed_summary_and_description_in_grand_parent_branch(self):
        """Testing BazaarClient guessing summary and description for grand
        parent branch."""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        self._bzr_add_file_commit("foo.txt", FOO3, "commit 3")

        self.options.guess_summary = True
        self.options.guess_description = True

        grand_child_branch = mktemp()
        self._run_bzr(["branch", self.child_branch, grand_child_branch])
        os.chdir(grand_child_branch)

        # Requesting the diff between the grand child branch and its grand
        # parent:
        self.options.parent_branch = self.original_branch

        revisions = self.client.parse_revision_spec([])
        commit_message = self.client.get_commit_message(revisions)

        self.assertEqual("commit 3", commit_message['summary'])

        description = commit_message['description']
        self.assertTrue("commit 1" in description)
        self.assertTrue("commit 2" in description)
        self.assertFalse("commit 3" in description)

    def test_guessed_summary_and_description_with_revision_range(self):
        """Testing BazaarClient guessing summary and description with a
        revision range."""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        self._bzr_add_file_commit("foo.txt", FOO3, "commit 3")

        self.options.guess_summary = True
        self.options.guess_description = True
        revisions = self.client.parse_revision_spec(['2..3'])
        commit_message = self.client.get_commit_message(revisions)

        self.assertEqual("commit 2", commit_message['summary'])
        self.assertEqual("commit 2", commit_message['description'])

    def test_parse_revision_spec_no_args(self):
        """Testing BazaarClient.parse_revision_spec with no specified
        revisions"""
        os.chdir(self.child_branch)

        base_commit_id = self.client._get_revno()
        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        tip_commit_id = self.client._get_revno()

        revisions = self.client.parse_revision_spec()
        self.assertTrue(isinstance(revisions, dict))
        self.assertTrue('base' in revisions)
        self.assertTrue('tip' in revisions)
        self.assertTrue('parent_base' not in revisions)
        self.assertEqual(revisions['base'], base_commit_id)
        self.assertEqual(revisions['tip'], tip_commit_id)

    def test_parse_revision_spec_one_arg(self):
        """Testing BazaarClient.parse_revision_spec with one specified
        revision"""
        os.chdir(self.child_branch)

        base_commit_id = self.client._get_revno()
        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        tip_commit_id = self.client._get_revno()

        revisions = self.client.parse_revision_spec([tip_commit_id])
        self.assertTrue(isinstance(revisions, dict))
        self.assertTrue('base' in revisions)
        self.assertTrue('tip' in revisions)
        self.assertTrue('parent_base' not in revisions)
        self.assertEqual(revisions['base'], base_commit_id)
        self.assertEqual(revisions['tip'], tip_commit_id)

    def test_parse_revision_spec_one_arg_parent(self):
        """Testing BazaarClient.parse_revision_spec with one specified
        revision and a parent diff"""
        os.chdir(self.original_branch)
        parent_base_commit_id = self.client._get_revno()

        grand_child_branch = mktemp()
        self._run_bzr(["branch", self.child_branch, grand_child_branch])
        os.chdir(grand_child_branch)

        base_commit_id = self.client._get_revno()
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        tip_commit_id = self.client._get_revno()

        self.options.parent_branch = self.child_branch

        revisions = self.client.parse_revision_spec([tip_commit_id])
        self.assertTrue(isinstance(revisions, dict))
        self.assertTrue('parent_base' in revisions)
        self.assertTrue('base' in revisions)
        self.assertTrue('tip' in revisions)
        self.assertEqual(revisions['parent_base'], parent_base_commit_id)
        self.assertEqual(revisions['base'], base_commit_id)
        self.assertEqual(revisions['tip'], tip_commit_id)

    def test_parse_revision_spec_one_arg_split(self):
        """Testing BazaarClient.parse_revision_spec with R1..R2 syntax"""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        base_commit_id = self.client._get_revno()
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        tip_commit_id = self.client._get_revno()

        revisions = self.client.parse_revision_spec(
            ['%s..%s' % (base_commit_id, tip_commit_id)])
        self.assertTrue(isinstance(revisions, dict))
        self.assertTrue('parent_base' not in revisions)
        self.assertTrue('base' in revisions)
        self.assertTrue('tip' in revisions)
        self.assertEqual(revisions['base'], base_commit_id)
        self.assertEqual(revisions['tip'], tip_commit_id)

    def test_parse_revision_spec_two_args(self):
        """Testing BazaarClient.parse_revision_spec with two revisions"""
        os.chdir(self.child_branch)

        self._bzr_add_file_commit("foo.txt", FOO1, "commit 1")
        base_commit_id = self.client._get_revno()
        self._bzr_add_file_commit("foo.txt", FOO2, "commit 2")
        tip_commit_id = self.client._get_revno()

        revisions = self.client.parse_revision_spec(
            [base_commit_id, tip_commit_id])
        self.assertTrue(isinstance(revisions, dict))
        self.assertTrue('parent_base' not in revisions)
        self.assertTrue('base' in revisions)
        self.assertTrue('tip' in revisions)
        self.assertEqual(revisions['base'], base_commit_id)
        self.assertEqual(revisions['tip'], tip_commit_id)
