import argparse
import os
import pkg_resources
import signal
import subprocess
import sys

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

from rbtools import get_version_string
from rbtools.commands import Option, RB_MAIN


GLOBAL_OPTIONS = [
    Option('-v', '--version',
           action='version',
           version='RBTools %s' % get_version_string()),
    Option('-h', '--help',
           action='store_true',
           dest='help',
           default=False),
    Option('command',
           nargs=argparse.REMAINDER,
           help='The RBTools command to execute, and any arguments. '
                '(See below)'),
]


def build_help_text(command_class):
    """Generate help text from a command class."""
    command = command_class()
    parser = command.create_parser({})

    return parser.format_help()


def help(args, parser):
    if args:
        # TODO: First check for static help text file before
        # generating it at run time.
        ep = pkg_resources.get_entry_info("rbtools", "rbtools_commands",
                                          args[0])

        if ep:
            help_text = build_help_text(ep.load())
            print help_text
            sys.exit(0)

        print "No help found for %s" % args[0]
        sys.exit(0)

    parser.print_help()

    # We cast to a set to de-dupe the list, since third-parties may
    # try to override commands by using the same name, and then cast
    # back to a list for easy sorting.
    entrypoints = pkg_resources.iter_entry_points('rbtools_commands')
    commands = list(set([entrypoint.name for entrypoint in entrypoints]))
    common_commands = ['post', 'patch', 'close', 'diff']

    print "\nThe most commonly used commands are:"
    for command in common_commands:
        print "  %s" % command

    print "\nOther commands:"
    for command in sorted(commands):
        if command not in common_commands:
            print "  %s" % command

    print ("See '%s help <command>' for more information "
           "on a specific command." % RB_MAIN)
    sys.exit(0)


def main():
    """Execute a command."""
    def exit_on_int(sig, frame):
        sys.exit(128 + sig)
    signal.signal(signal.SIGINT, exit_on_int)

    parser = argparse.ArgumentParser(
        prog=RB_MAIN,
        usage='%(prog)s [--version] <command> [options] [<args>]',
        add_help=False)

    for option in GLOBAL_OPTIONS:
        option.add_to(parser)

    opt = parser.parse_args()

    if not opt.command:
        help([], parser)

    command_name = opt.command[0]
    args = opt.command[1:]

    if command_name == "help":
        help(args, parser)
    elif opt.help or "--help" in args or '-h' in args:
        help(opt.command, parser)

    # Attempt to retrieve the command class from the entry points. We
    # first look in rbtools for the commands, and failing that, we look
    # for third-party commands.
    ep = pkg_resources.get_entry_info("rbtools", "rbtools_commands",
                                      command_name)

    if not ep:
        try:
            ep = pkg_resources.iter_entry_points('rbtools_commands',
                                                 command_name).next()
        except StopIteration:
            # There aren't any custom entry points defined.
            pass

    if ep:
        try:
            command = ep.load()()
        except ImportError:
            # TODO: It might be useful to actual have the strack
            # trace here, due to an import somewhere down the import
            # chain failing.
            sys.stderr.write("Could not load command entry point %s\n" %
                             ep.name)
            sys.exit(1)
        except Exception, e:
            sys.stderr.write("Unexpected error loading command %s: %s\n" %
                             (ep.name, e))
            sys.exit(1)

        command.run_from_argv([RB_MAIN, command_name] + args)
    else:
        # A command class could not be found, so try and execute
        # the "rb-<command>" on the system.
        args = ['%s-%s' % (RB_MAIN, command_name)] + args

        try:
            sys.exit(subprocess.call(args,
                                     stdin=sys.stdin,
                                     stdout=sys.stdout,
                                     stderr=sys.stderr,
                                     env=os.environ.copy()))
        except OSError:
            parser.error("'%s' is not a command" % command_name)


if __name__ == "__main__":
    main()
