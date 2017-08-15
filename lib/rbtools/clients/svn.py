import logging
import os
import re
import sys
import urllib
from xml.etree import ElementTree

from rbtools.api.errors import APIError
from rbtools.clients import SCMClient, RepositoryInfo
from rbtools.clients.errors import (InvalidRevisionSpecError,
                                    TooManyRevisionsError)
from rbtools.utils.checks import check_gnu_diff, check_install
from rbtools.utils.filesystem import make_empty_files, walk_parents
from rbtools.utils.process import execute


class SVNClient(SCMClient):
    """
    A wrapper around the svn Subversion tool that fetches repository
    information and generates compatible diffs.
    """
    name = 'Subversion'

    INDEX_SEP = '=' * 67

    # Match the diff control lines generated by 'svn diff'.
    DIFF_ORIG_FILE_LINE_RE = re.compile(r'^---\s+.*\s+\(.*\)')
    DIFF_NEW_FILE_LINE_RE = re.compile(r'^\+\+\+\s+.*\s+\(.*\)')
    DIFF_COMPLETE_REMOVAL_RE = re.compile(r'^@@ -1,\d+ \+0,0 @@$')

    REVISION_WORKING_COPY = '--rbtools-working-copy'
    REVISION_CHANGELIST_PREFIX = '--rbtools-changelist:'

    def __init__(self, **kwargs):
        super(SVNClient, self).__init__(**kwargs)

    def get_repository_info(self):
        if not check_install(['svn', 'help']):
            logging.debug('Unable to execute "svn help": skipping SVN')
            return None

        # Get the SVN repository path (either via a working copy or
        # a supplied URI)
        svn_info_params = ["svn", "info"]

        if getattr(self.options, 'repository_url', None):
            svn_info_params.append(self.options.repository_url)

        # Add --non-interactive so that this command will not hang
        #  when used  on a https repository path
        svn_info_params.append("--non-interactive")

        data = execute(svn_info_params, ignore_errors=True)

        m = re.search(r'^Repository Root: (.+)$', data, re.M)
        if not m:
            return None

        path = m.group(1)

        m = re.search(r'^URL: (.+)$', data, re.M)
        if not m:
            return None

        base_path = m.group(1)[len(path):] or "/"

        m = re.search(r'^Repository UUID: (.+)$', data, re.M)
        if not m:
            return None

        # Now that we know it's SVN, make sure we have GNU diff installed,
        # and error out if we don't.
        check_gnu_diff()

        return SVNRepositoryInfo(path, base_path, m.group(1))

    def parse_revision_spec(self, revisions=[]):
        """Parses the given revision spec.

        The 'revisions' argument is a list of revisions as specified by the
        user. Items in the list do not necessarily represent a single revision,
        since the user can use SCM-native syntaxes such as "r1..r2" or "r1:r2".
        SCMTool-specific overrides of this method are expected to deal with
        such syntaxes.

        This will return a dictionary with the following keys:
            'base':        A revision to use as the base of the resulting diff.
            'tip':         A revision to use as the tip of the resulting diff.

        These will be used to generate the diffs to upload to Review Board (or
        print). The diff for review will include the changes in (base, tip].

        If a single revision is passed in, this will return the parent of that
        revision for 'base' and the passed-in revision for 'tip'.

        If zero revisions are passed in, this will return the most recently
        checked-out revision for 'base' and a special string indicating the
        working copy for 'tip'.

        The SVN SCMClient never fills in the 'parent_base' key. Users who are
        using other patch-stack tools who want to use parent diffs with SVN
        will have to generate their diffs by hand.
        """
        n_revisions = len(revisions)

        if n_revisions == 1 and ':' in revisions[0]:
            revisions = revisions[0].split(':')
            n_revisions = len(revisions)

        if n_revisions == 0:
            # Most recent checked-out revision -- working copy

            # TODO: this should warn about mixed-revision working copies that
            # affect the list of files changed (see bug 2392).
            return {
                'base': 'BASE',
                'tip': self.REVISION_WORKING_COPY,
            }
        elif n_revisions == 1:
            # Either a numeric revision (n-1:n) or a changelist
            revision = revisions[0]
            try:
                revision = self._convert_symbolic_revision(revision)
                return {
                    'base': revision - 1,
                    'tip': revision,
                }
            except ValueError:
                # It's not a revision--let's try a changelist. This only makes
                # sense if we have a working copy.
                if not self.options.repository_url:
                    status = execute(['svn', 'status', '--cl', str(revision),
                                      '--ignore-externals', '--xml'])
                    cl = ElementTree.fromstring(status).find('changelist')
                    if cl is not None:
                        # TODO: this should warn about mixed-revision working
                        # copies that affect the list of files changed (see
                        # bug 2392).
                        return {
                            'base': 'BASE',
                            'tip': self.REVISION_CHANGELIST_PREFIX + revision
                        }

                raise InvalidRevisionSpecError(
                    '"%s" does not appear to be a valid revision or '
                    'changelist name' % revision)
        elif n_revisions == 2:
            # Diff between two numeric revisions
            try:
                return {
                    'base': self._convert_symbolic_revision(revisions[0]),
                    'tip': self._convert_symbolic_revision(revisions[1]),
                }
            except ValueError:
                raise InvalidRevisionSpecError(
                    'Could not parse specified revisions: %s' % revisions)
        else:
            raise TooManyRevisionsError

    def _convert_symbolic_revision(self, revision):
        command = ['svn', 'log', '-r', str(revision), '-l', '1', '--xml']
        if getattr(self.options, 'repository_url', None):
            command.append(self.options.repository_url)
        log = execute(command, ignore_errors=True, none_on_ignored_error=True)

        if log is not None:
            root = ElementTree.fromstring(log)
            logentry = root.find('logentry')
            if logentry is not None:
                return int(logentry.attrib['revision'])

        raise ValueError

    def scan_for_server(self, repository_info):
        # Scan first for dot files, since it's faster and will cover the
        # user's $HOME/.reviewboardrc
        server_url = super(SVNClient, self).scan_for_server(repository_info)
        if server_url:
            return server_url

        return self.scan_for_server_property(repository_info)

    def scan_for_server_property(self, repository_info):
        def get_url_prop(path):
            url = execute(["svn", "propget", "reviewboard:url", path],
                          with_errors=False).strip()
            return url or None

        for path in walk_parents(os.getcwd()):
            if not os.path.exists(os.path.join(path, ".svn")):
                break

            prop = get_url_prop(path)
            if prop:
                return prop

        return get_url_prop(repository_info.path)

    def diff(self, revisions, files=[], extra_args=[]):
        """
        Performs a diff in a Subversion repository.

        If the given revision spec is empty, this will do a diff of the
        modified files in the working directory. If the spec is a changelist,
        it will do a diff of the modified files in that changelist. If the spec
        is a single revision, it will show the changes in that revision. If the
        spec is two revisions, this will do a diff between the two revisions.

        SVN repositories do not support branches of branches in a way that
        makes parent diffs possible, so we never return a parent diff.
        """
        # Keep track of information needed for handling empty files later.
        empty_files_revisions = {
            'base': None,
            'tip': None,
        }

        base = str(revisions['base'])
        tip = str(revisions['tip'])

        repository_info = self.get_repository_info()

        diff_cmd = ['svn', 'diff', '--diff-cmd=diff', '--notice-ancestry']
        changelist = None

        if tip == self.REVISION_WORKING_COPY:
            # Posting the working copy
            diff_cmd.extend(['-r', base])
        elif tip.startswith(self.REVISION_CHANGELIST_PREFIX):
            # Posting a changelist
            changelist = tip[len(self.REVISION_CHANGELIST_PREFIX):]
            diff_cmd.extend(['--changelist', changelist])
        else:
            # Diff between two separate revisions. Behavior depends on whether
            # or not there's a working copy
            if self.options.repository_url:
                # No working copy--create 'old' and 'new' URLs
                if len(files) == 1:
                    # If there's a single file or directory passed in, we use
                    # that as part of the URL instead of as a separate
                    # filename.
                    repository_info.set_base_path(files[0])
                    files = []

                new_url = (repository_info.path + repository_info.base_path +
                           '@' + tip)

                # When the source revision is '0', assume the user wants to
                # upload a diff containing all the files in 'base_path' as
                # new files. If the base path within the repository is added to
                # both the old and new URLs, `svn diff` will error out, since
                # the base_path didn't exist at revision 0. To avoid that
                # error, use the repository's root URL as the source for the
                # diff.
                if base == '0':
                    old_url = repository_info.path + '@' + base
                else:
                    old_url = (repository_info.path + repository_info.base_path +
                               '@' + base)

                diff_cmd.extend([old_url, new_url])

                empty_files_revisions['base'] = '(revision %s)' % base
                empty_files_revisions['tip'] = '(revision %s)' % tip
            else:
                # Working copy--do a normal range diff
                diff_cmd.extend(['-r', '%s:%s' % (base, tip)])

                empty_files_revisions['base'] = '(revision %s)' % base
                empty_files_revisions['tip'] = '(revision %s)' % tip

        diff_cmd.extend(files)

        if self.history_scheduled_with_commit(changelist):
            svn_show_copies_as_adds = getattr(
                self.options, 'svn_show_copies_as_adds', None)
            if svn_show_copies_as_adds is None:
                sys.stderr.write("One or more files in your changeset has "
                                 "history scheduled with commit. Please try "
                                 "again with '--svn-show-copies-as-adds=y/n"
                                 "'\n")
                sys.exit(1)
            else:
                if svn_show_copies_as_adds in 'Yy':
                    diff_cmd.append("--show-copies-as-adds")

        diff = execute(diff_cmd, split_lines=True)
        diff = self.handle_renames(diff)

        if self._supports_empty_files():
            diff = self._handle_empty_files(diff, diff_cmd,
                                            empty_files_revisions)

        diff = self.convert_to_absolute_paths(diff, repository_info)

        return {
            'diff': ''.join(diff),
        }

    def history_scheduled_with_commit(self, changelist):
        """ Method to find if any file status has '+' in 4th column"""
        status_cmd = ['svn', 'status', '--ignore-externals']

        if changelist:
            status_cmd.extend(['--changelist', changelist])

        for p in execute(status_cmd, split_lines=True):
            try:
                if p[3] == '+':
                    return True
            except IndexError:
                # This may be some other output, or just doesn't have the
                # data we're looking for. Move along.
                pass

        return False

    def find_copyfrom(self, path):
        """
        A helper function for handle_renames

        The output of 'svn info' reports the "Copied From" header when invoked
        on the exact path that was copied. If the current file was copied as a
        part of a parent or any further ancestor directory, 'svn info' will not
        report the origin. Thus it is needed to ascend from the path until
        either a copied path is found or there are no more path components to
        try.
        """
        def smart_join(p1, p2):
            if p2:
                return os.path.join(p1, p2)

            return p1

        path1 = path
        path2 = None

        while path1:
            info = self.svn_info(path1, ignore_errors=True) or {}
            url = info.get('Copied From URL', None)

            if url:
                root = info["Repository Root"]
                from_path1 = urllib.unquote(url[len(root):])
                return smart_join(from_path1, path2)

            if info.get('Schedule', None) != 'normal':
                # Not added as a part of the parent directory, bail out
                return None

            # Strip one component from path1 to path2
            path1, tmp = os.path.split(path1)

            if path1 == "" or path1 == "/":
                path1 = None
            else:
                path2 = smart_join(tmp, path2)

        return None

    def handle_renames(self, diff_content):
        """
        The output of svn diff is incorrect when the file in question came
        into being via svn mv/cp. Although the patch for these files are
        relative to its parent, the diff header doesn't reflect this.
        This function fixes the relevant section headers of the patch to
        portray this relationship.
        """

        # svn diff against a repository URL on two revisions appears to
        # handle moved files properly, so only adjust the diff file names
        # if they were created using a working copy.
        if self.options.repository_url:
            return diff_content

        result = []

        from_line = to_line = None
        for line in diff_content:
            if self.DIFF_ORIG_FILE_LINE_RE.match(line):
                from_line = line
                continue

            if self.DIFF_NEW_FILE_LINE_RE.match(line):
                to_line = line
                continue

            # This is where we decide how mangle the previous '--- '
            if from_line and to_line:
                # If the file is marked completely removed, bail out with
                # original diff. The reason for this is that 'svn diff
                # --notice-ancestry' generates two diffs for a replaced file:
                # one as a complete deletion, and one as a new addition.
                # If it was replaced with history, though, we need to preserve
                # the file name in the "deletion" part - or the patch won't
                # apply.
                if self.DIFF_COMPLETE_REMOVAL_RE.match(line):
                    result.append(from_line)
                    result.append(to_line)
                else:
                    to_file, _ = self.parse_filename_header(to_line[4:])
                    copied_from = self.find_copyfrom(to_file)
                    if copied_from is not None:
                        result.append(from_line.replace(to_file, copied_from))
                    else:
                        result.append(from_line)  # As is, no copy performed
                    result.append(to_line)
                from_line = to_line = None

            # We only mangle '---' lines. All others get added straight to
            # the output.
            result.append(line)

        return result

    def _handle_empty_files(self, diff_content, diff_cmd, revisions):
        """Handles added and deleted 0-length files in the diff output.

        Since the diff output from svn diff does not give enough context for
        0-length files, we add extra information to the patch.

        For example, the original diff output of an added 0-length file is:
        Index: foo\n
        ===================================================================\n

        The modified diff of an added 0-length file will be:
        Index: foo\t(added)\n
        ===================================================================\n
        --- foo\t(<base_revision>)\n
        +++ foo\t(<tip_revision>)\n
        """
        # Get a list of all deleted files in this diff so we can differentiate
        # between added empty files and deleted empty files.
        diff_cmd.append('--no-diff-deleted')
        diff_with_deleted = execute(diff_cmd,
                                    ignore_errors=True,
                                    none_on_ignored_error=True)

        if not diff_with_deleted:
            return diff_content

        deleted_files = re.findall(r'^Index:\s+(\S+)\s+\(deleted\)$',
                                   diff_with_deleted, re.M)

        result = []
        index_line = filename = None
        i = 0
        num_lines = len(diff_content)

        while i < num_lines:
            line = diff_content[i]

            if (line.startswith('Index: ') and
                (i + 2 == num_lines or
                 (i + 2 < num_lines and
                  diff_content[i + 2].startswith('Index: ')))):
                # An empty file. Get and add the extra diff information.
                index_line = line.strip()
                filename = index_line.split(' ', 1)[1].strip()

                if filename in deleted_files:
                    # Deleted empty file.
                    result.append('%s\t(deleted)\n' % index_line)

                    if not revisions['base'] and not revisions['tip']:
                        tip = '(working copy)'
                        info = self.svn_info(filename, ignore_errors=True)

                        if info and 'Revision' in info:
                            base = '(revision %s)' % info['Revision']
                        else:
                            continue
                else:
                    # Added empty file.
                    result.append('%s\t(added)\n' % index_line)

                    if not revisions['base'] and not revisions['tip']:
                        base = tip = '(revision 0)'

                result.append('%s\n' % self.INDEX_SEP)
                result.append('--- %s\t%s\n' % (filename, base))
                result.append('+++ %s\t%s\n' % (filename, tip))

                # Skip the next line (the index separator) since we've already
                # copied it.
                i += 2
            else:
                result.append(line)
                i += 1

        return result

    def convert_to_absolute_paths(self, diff_content, repository_info):
        """
        Converts relative paths in a diff output to absolute paths.
        This handles paths that have been svn switched to other parts of the
        repository.
        """

        result = []

        for line in diff_content:
            front = None
            orig_line = line
            if (self.DIFF_NEW_FILE_LINE_RE.match(line)
                or self.DIFF_ORIG_FILE_LINE_RE.match(line)
                or line.startswith('Index: ')):
                front, line = line.split(" ", 1)

            if front:
                if line.startswith('/'):  # Already absolute
                    line = front + " " + line
                else:
                    # Filename and rest of line (usually the revision
                    # component)
                    file, rest = self.parse_filename_header(line)

                    # If working with a diff generated outside of a working
                    # copy, then file paths are already absolute, so just
                    # add initial slash.
                    if self.options.repository_url:
                        path = urllib.unquote(
                            "%s/%s" % (repository_info.base_path, file))
                    else:
                        info = self.svn_info(file, True)
                        if info is None:
                            result.append(orig_line)
                            continue
                        url = info["URL"]
                        root = info["Repository Root"]
                        path = urllib.unquote(url[len(root):])

                    line = front + " " + path + rest

            result.append(line)

        return result

    def svn_info(self, path, ignore_errors=False):
        """Return a dict which is the result of 'svn info' at a given path."""
        svninfo = {}

        # SVN's internal path recognizers think that any file path that
        # includes an '@' character will be path@rev, and skips everything that
        # comes after the '@'. This makes it hard to do operations on files
        # which include '@' in the name (such as image@2x.png).
        if '@' in path and not path[-1] == '@':
            path += '@'

        result = execute(["svn", "info", path],
                         split_lines=True,
                         ignore_errors=ignore_errors,
                         none_on_ignored_error=True)
        if result is None:
            return None

        for info in result:
            parts = info.strip().split(": ", 1)
            if len(parts) == 2:
                key, value = parts
                svninfo[key] = value

        return svninfo

    # Adapted from server code parser.py
    def parse_filename_header(self, s):
        parts = None
        if "\t" in s:
            # There's a \t separating the filename and info. This is the
            # best case scenario, since it allows for filenames with spaces
            # without much work. The info can also contain tabs after the
            # initial one; ignore those when splitting the string.
            parts = s.split("\t", 1)

        # There's spaces being used to separate the filename and info.
        # This is technically wrong, so all we can do is assume that
        # 1) the filename won't have multiple consecutive spaces, and
        # 2) there's at least 2 spaces separating the filename and info.
        if "  " in s:
            parts = re.split(r"  +", s)

        if parts:
            parts[1] = '\t' + parts[1]
            return parts

        # strip off ending newline, and return it as the second component
        return [s.split('\n')[0], '\n']

    def _apply_patch_for_empty_files(self, patch, p_num):
        """Returns True if any empty files in the patch are applied.

        If there are no empty files in the patch or if an error occurs while
        applying the patch, we return False.
        """
        patched_empty_files = False
        added_files = re.findall(r'^Index:\s+(\S+)\t\(added\)$', patch, re.M)
        deleted_files = re.findall(r'^Index:\s+(\S+)\t\(deleted\)$', patch,
                                   re.M)

        if added_files:
            added_files = self._strip_p_num_slashes(added_files, int(p_num))
            make_empty_files(added_files)
            result = execute(['svn', 'add'] + added_files, ignore_errors=True,
                             none_on_ignored_error=True)

            if result is None:
                logging.error('Unable to execute "svn add" on: %s',
                              ', '.join(added_files))
            else:
                patched_empty_files = True

        if deleted_files:
            deleted_files = self._strip_p_num_slashes(deleted_files,
                                                      int(p_num))
            result = execute(['svn', 'delete'] + deleted_files,
                             ignore_errors=True, none_on_ignored_error=True)

            if result is None:
                logging.error('Unable to execute "svn delete" on: %s',
                              ', '.join(deleted_files))
            else:
                patched_empty_files = True

        return patched_empty_files

    def _supports_empty_files(self):
        """Checks if the RB server supports added/deleted empty files."""
        return (self.capabilities and
                self.capabilities.has_capability('scmtools', 'svn',
                                                 'empty_files'))


class SVNRepositoryInfo(RepositoryInfo):
    """
    A representation of a SVN source code repository. This version knows how to
    find a matching repository on the server even if the URLs differ.
    """
    def __init__(self, path, base_path, uuid, supports_parent_diffs=False):
        RepositoryInfo.__init__(self, path, base_path,
                                supports_parent_diffs=supports_parent_diffs)
        self.uuid = uuid

    def find_server_repository_info(self, server):
        """
        The point of this function is to find a repository on the server that
        matches self, even if the paths aren't the same. (For example, if self
        uses an 'http' path, but the server uses a 'file' path for the same
        repository.) It does this by comparing repository UUIDs. If the
        repositories use the same path, you'll get back self, otherwise you'll
        get a different SVNRepositoryInfo object (with a different path).
        """
        repositories = [
            repository
            for repository in server.get_repositories()
            if repository['tool'] == 'Subversion'
        ]

        # Do two paths. The first will be to try to find a matching entry
        # by path/mirror path. If we don't find anything, then the second will
        # be to find a matching UUID.
        for repository in repositories:
            if self.path in (repository['path'],
                             repository.get('mirror_path', '')):
                return self

        # We didn't find our locally matched repository, so scan based on UUID.
        for repository in repositories:
            info = self._get_repository_info(server, repository)

            if not info or self.uuid != info['uuid']:
                continue

            repos_base_path = info['url'][len(info['root_url']):]
            relpath = self._get_relative_path(self.base_path, repos_base_path)

            if relpath:
                return SVNRepositoryInfo(info['url'], relpath, self.uuid)

        # We didn't find a matching repository on the server. We'll just return
        # self and hope for the best. In reality, we'll likely fail, but we
        # did all we could really do.
        return self

    def _get_repository_info(self, server, repository):
        try:
            return server.get_repository_info(repository['id'])
        except APIError, e:
            # If the server couldn't fetch the repository info, it will return
            # code 210. Ignore those.
            # Other more serious errors should still be raised, though.
            if e.error_code == 210:
                return None

            raise e

    def _get_relative_path(self, path, root):
        pathdirs = self._split_on_slash(path)
        rootdirs = self._split_on_slash(root)

        # root is empty, so anything relative to that is itself
        if len(rootdirs) == 0:
            return path

        # If one of the directories doesn't match, then path is not relative
        # to root.
        if rootdirs != pathdirs[:len(rootdirs)]:
            return None

        # All the directories matched, so the relative path is whatever
        # directories are left over. The base_path can't be empty, though, so
        # if the paths are the same, return '/'
        if len(pathdirs) == len(rootdirs):
            return '/'
        else:
            return '/' + '/'.join(pathdirs[len(rootdirs):])

    def _split_on_slash(self, path):
        # Split on slashes, but ignore multiple slashes and throw away any
        # trailing slashes.
        split = re.split('/*', path)
        if split[-1] == '':
            split = split[0:-1]
        return split
