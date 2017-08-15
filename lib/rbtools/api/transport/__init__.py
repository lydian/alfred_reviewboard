class Transport(object):
    """Base class for API Transport layers.

    An API Transport layer acts as an intermediary between the API
    user and the Resource objects. All access to a resource's data,
    and all communication with the Review Board server are handled by
    the Transport. This allows for Transport implementations with
    unique interfaces which operate on the same underlying resource
    classes. Specifically, this allows for both a synchronous, and an
    asynchronous implementation of the transport.
    """
    def __init__(self, url, *args, **kwargs):
        self.url = url

    def get_root(self, *args, **kwargs):
        """Retrieve the root api resource."""
        raise NotImplementedError

    def get_path(self, path, *args, **kwargs):
        """Retrieve the api resource at the provided path."""
        raise NotImplementedError

    def get_url(self, url, *args, **kwargs):
        """Retrieve the resource at the provided URL.

        The URL is not guaranteed to be part of the configured Review
        Board domain.
        """
        raise NotImplementedError

    def login(self, username, password, *args, **kwargs):
        """Reset login information to be populated on next request.

        The transport should override this method and provide a way
        to reset the username and password which will be populated
        in the next request.
        """
        raise NotImplementedError

    def execute_request_method(self, method, *args, **kwargs):
        """Execute a method and carry out the returned HttpRequest."""
        return method(*args, **kwargs)
