from __future__ import print_function, unicode_literals

import os

from rbtools.api.errors import APIError
from rbtools.commands import Command, CommandError, Option
from rbtools.utils.commands import get_review_request


class Attach(Command):
    """Attach a file to a review request."""
    name = 'attach'
    author = 'The Review Board Project'
    args = '<review-request-id> <file>'
    option_list = [
        Option('--filename',
               dest='filename',
               default=None,
               help='Custom filename for the file attachment.'),
        Option('--caption',
               dest='caption',
               default=None,
               help='Caption for the file attachment.'),
        Command.server_options,
        Command.repository_options,
    ]

    def main(self, request_id, path_to_file):
        self.repository_info, self.tool = self.initialize_scm_tool(
            client_name=self.options.repository_type)
        server_url = self.get_server_url(self.repository_info, self.tool)
        api_client, api_root = self.get_api(server_url)

        request = get_review_request(request_id, api_root)

        try:
            with open(path_to_file, 'rb') as f:
                content = f.read()
        except IOError:
            raise CommandError('%s is not a valid file.' % path_to_file)

        # Check if the user specified a custom filename, otherwise
        # use the original filename.
        filename = self.options.filename or os.path.basename(path_to_file)

        try:
            request.get_file_attachments().upload_attachment(
                filename, content, self.options.caption)
        except APIError as e:
            raise CommandError('Error uploading file: %s' % e)

        print('Uploaded %s to review request %s.' %
              (path_to_file, request_id))
