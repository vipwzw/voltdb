# This file is part of VoltDB.

# Copyright (C) 2008-2012 VoltDB Inc.
#
# This file contains original code and/or modifications of original code.
# Any modifications made by VoltDB Inc. are licensed under the following
# terms and conditions:
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

__author__ = 'scooper'

import sys
import os
import inspect

import voltdbclient
from verbs import *
from voltcli import cli
from voltcli import environment
from voltcli import utility

#===============================================================================
# Global data
#===============================================================================

# Standard CLI options.
base_cli_spec = cli.CLISpec(
    description = '''\
Specific actions are provided by verbs.  Run "%prog help VERB" to display full
usage for a verb, including its options and arguments.
''',
    usage = '%prog [OPTIONS] VERB [ARGUMENTS ...]',
    cli_options = (
        cli.BooleanOption('-d', '--debug', 'debug',
                       'display debug messages'),
        cli.BooleanOption('-n', '--dry-run', 'dryrun',
                       'display actions without executing them'),
        cli.BooleanOption('-p', '--pause', 'pause',
                       'pause before significant actions'),
        cli.BooleanOption('-v', '--verbose', 'verbose',
                       'display verbose messages, including external command lines'),
    )
)

# Internal command names that get added to the VOLT namespace of user scripts.
internal_commands = ['volt', 'voltadmin']

#===============================================================================
class JavaRunner(object):
#===============================================================================
    """
    Execute or compile Java programs.
    """

    def __init__(self, classpath):
        self.classpath = classpath

    def execute(self, java_class, java_opts_override, *args, **kwargs):
        """
        Run a Java command line with option overrides.
        """
        classpath = self.classpath
        kwargs_classpath = kwargs.get('classpath', None)
        if kwargs_classpath:
            classpath = ':'.join((kwargs_classpath, classpath))
        java_args = [environment.java]
        java_opts = utility.merge_java_options(environment.java_opts, java_opts_override)
        java_args.extend(java_opts)
        debug_port = kwargs.get('remotedebug', None)
        if debug_port:
            java_args.extend((
                '-Xdebug',
                '-Xnoagent',
                '-Djava.compiler=NONE',
                '-Xrunjdwp:transport=dt_socket,address=%d,server=y,suspend=y' % debug_port))
        java_args.append('-Dlog4j.configuration=file://%s' % os.environ['LOG4J_CONFIG_PATH'])
        java_args.append('-Djava.library.path="%s"' % os.environ['VOLTDB_VOLTDB'])
        java_args.extend(('-classpath', classpath))
        java_args.append(java_class)
        for arg in args:
            if arg is not None:
                java_args.append(arg)
        return utility.run_cmd(*java_args)

    def compile(self, outdir, *srcfiles):
        """
        Compile Java source using javac.
        """
        if not os.path.exists(outdir):
            os.makedirs(outdir)
        utility.run_cmd('javac', '-target', '1.6', '-source', '1.6',
                          '-classpath', self.classpath, '-d', outdir, *srcfiles)

#===============================================================================
class VerbRunner(object):
#===============================================================================

    def __init__(self, command, verbspace, internal_verbspaces, config, cli_processor, **kwargs):
        """
        VerbRunner constructor.
        """
        # Unpack the command object for use by command implementations.
        self.verb       = command.verb
        self.opts       = command.opts
        self.args       = command.args
        self.parser     = command.parser
        self.outer_opts = command.outer_opts
        # The verbspace supports running nested commands.
        self.verbspace     = verbspace
        self.config        = config
        self.cli_processor = cli_processor
        self.go_default    = None
        self.project_path  = os.path.join(os.getcwd(), 'project.xml')
        # The internal verbspaces are just used for packaging other verbspaces.
        self.internal_verbspaces = internal_verbspaces
        # Build the Java classpath using environment variable, config file,
        # verb attribute, and kwargs.
        classpath = ':'.join(environment.classpath)
        classpath_ext = config.get('volt.classpath')
        if classpath_ext:
            classpath = ':'.join((classpath, classpath_ext))
        if hasattr(self.verb, 'classpath') and self.verb.classpath:
            classpath = ':'.join((self.verb.classpath, classpath))
        if 'classpath' in kwargs:
            classpath = ':'.join((kwargs['classpath'], classpath))
        # Create a Java runner.
        self.java = JavaRunner(classpath)

    def shell(self, *args):
        """
        Run a shell command.
        """
        utility.run_cmd(*args)

    def get_catalog(self):
        """
        Get the catalog path from the configuration.
        """
        return self.config.get_required('volt.catalog')

    def mkdir(self, dir):
        """
        Create a directory recursively.
        """
        self.shell('mkdir', '-p', dir)

    def catalog_exists(self):
        """
        Test if catalog file exists.
        """
        return os.path.exists(self.get_catalog())

    def abort(self, *msgs):
        """
        Display errors (optional) and abort execution.
        """
        utility.error('Fatal error in "%s" command.' % self.verb.name, *msgs)
        self.help()
        utility.abort()

    def help(self, *args, **kwargs):
        """
        Display help for command.
        """
        # The only valid keyword argument is 'all' for now.
        context = '%s.help()' % self.__class__.__name__
        all = utility.kwargs_get(kwargs, 'all', default = False)
        if all:
            sys.stdout.write('\n===== Full Help =====\n')
            self.usage()
            for verb_name in self.verbspace.verb_names:
                if not self.verbspace.verbs[verb_name].cli_spec.baseverb:
                    sys.stdout.write('\n===== Verb: %s =====\n' % verb_name)
                    self._help_verb(verb_name)
            for verb_name in self.verbspace.verb_names:
                if self.verbspace.verbs[verb_name].cli_spec.baseverb:
                    sys.stdout.write('\n===== Common Verb: %s =====\n' % verb_name)
                    self._help_verb(verb_name)
        else:
            if args:
                for name in args:
                    for verb_name in self.verbspace.verb_names:
                        if verb_name == name:
                            self._help_verb(verb_name)
                            break
                    else:
                        utility.error('Verb "%s" was not found.' % name)
                        self.usage()
            else:
                self.usage()

    def package(self, output_dir_in, force, *args):
        """
        Create python-runnable package/zip file.
        """
        if output_dir_in is None:
            output_dir = ''
        else:
            output_dir = output_dir_in
        if args:
            # Package other verbspaces.
            for name in args:
                if name not in self.internal_verbspaces:
                    utility.abort('Unknown base command "%s" specified for packaging.' % name)
                verbspace = self.internal_verbspaces[name]
                self._create_package(output_dir, verbspace.name, verbspace.version,
                                     verbspace.description, force)
        else:
            # Package the active verbspace.
            self._create_package(output_dir, self.verbspace.name, self.verbspace.version,
                                 self.verbspace.description, force)
        # Warn for Python version < 2.6.
        if sys.version_info[0] == 2 and sys.version_info[1] < 6:
            utility.warning(
                    'Generated program packages require Python version 2.6 or greater.',
                    'The running Python version is %d.%d.%d' % sys.version_info[:3],
                    "It will crash with Python versions that can't detect and run zip packages.")

    def usage(self):
        """
        Display usage screen.
        """
        sys.stdout.write('\n')
        self.cli_processor.print_help()
        sys.stdout.write('\n')

    def set_go_default(self, go_default):
        """
        Called by Verb to set the default go action.
        """
        self.go_default = go_default

    def go(self, *args):
        """
        Default go action provided by Verb object.
        """
        if self.go_default is None:
            utility.abort('Verb "%s" (class %s) does not provide a default go action.'
                                % (self.verb.name, self.verb.__class__.__name__))
        else:
            self.go_default(self, *args)

    def execute(self):
        """
        Execute the verb function.
        """
        self.verb.execute(self)

    def call(self, *args, **kwargs):
        if not args:
            utility.abort('No arguments were passed to VerbRunner.call().')
        if args[0].find('.') == -1:
            self._run_command(self.verbspace, *args, **kwargs)
        else:
            verbspace_name, verb_name = args[0].split('.', 1)
            if verbspace_name not in self.internal_verbspaces:
                utility.abort('Unknown name passed to VerbRunner.call(): %s' % verbspace_name)
            verbspace = self.internal_verbspaces[verbspace_name]
            if verb_name not in verbspace.verb_names:
                utility.abort('Unknown verb passed to VerbRunner.call(): %s' % args[0],
                              'Available verbs in "%s":' % verbspace_name,
                              verbspace.verb_names)
            args2 = [verb_name] + list(args[1:])
            self._run_command(self.internal_verbspaces[verbspace_name], *args2, **kwargs)

    def _help_verb(self, name):
        # Internal method to display help for a verb
        verb = self.verbspace.verbs[name]
        parser = self.cli_processor.create_verb_parser(verb)
        sys.stdout.write('\n')
        parser.print_help()
        if verb.cli_spec.description2:
            sys.stdout.write('\n')
            sys.stdout.write('%s\n' % verb.cli_spec.description2.strip())

    def _create_package(self, output_dir, name, version, description, force):
        output_path = os.path.join(output_dir, '%s' % name)
        utility.info('Creating compressed executable Python program: %s' % output_path)
        zipper = utility.Zipper(excludes = ['[.]pyc$'])
        zipper.open(output_path, force = force, preamble = '#!/usr/bin/env python\n')
        try:
            # Generate the __main__.py module for automatic execution from the zip file.
            main_script = ('''\
import sys
from voltcli import runner
runner.main('%(name)s', '', '%(version)s', '%(description)s', package = True, *sys.argv[1:])'''
                    % locals())
            zipper.add_file_from_string(main_script, '__main__.py')
            # Recursively package lib/python as lib in the zip file.
            zipper.add_directory(environment.volt_python, '')
        finally:
            zipper.close(make_executable = True)

    def _run_command(self, verbspace, *args, **kwargs):
        processor = cli.VoltCLICommandProcessor(verbspace.verbs,
                                                base_cli_spec.cli_options,
                                                base_cli_spec.usage,
                                                '\n'.join((verbspace.description,
                                                           base_cli_spec.description)),
                                                '%%prog version %s' % verbspace.version)
        command = processor.parse(args)
        command.outer_opts = self.outer_opts
        runner = VerbRunner(command, verbspace, self.internal_verbspaces, self.config,
                            processor, **kwargs)
        runner.execute()

#===============================================================================
class VOLT(object):
#===============================================================================
    """
    The VOLT namespace provided to dynamically loaded verb scripts.
    """
    def __init__(self, verb_decorators):
        # Add all verb_decorators methods not starting with '_' as members.
        for name, function in inspect.getmembers(verb_decorators, inspect.ismethod):
            if not name.startswith('_'):
                setattr(self, name, function)
        # For declaring options in command decorators.
        self.BooleanOption  = cli.BooleanOption
        self.StringOption   = cli.StringOption
        # Expose voltdbclient symbols for Volt client commands.
        self.VoltProcedure  = voltdbclient.VoltProcedure
        self.VoltResponse   = voltdbclient.VoltResponse
        self.VoltException  = voltdbclient.VoltException
        self.VoltTable      = voltdbclient.VoltTable
        self.VoltColumn     = voltdbclient.VoltColumn
        self.FastSerializer = voltdbclient.FastSerializer
        # As a convenience expose the utility module so that commands don't
        # need to import it.
        self.utility = utility

#===============================================================================
def load_verbspace(command_name, command_dir, config, version, description, package):
#===============================================================================
    """
    Build a verb space by searching for source files with verbs in this source
    file's directory, the calling script location (if provided), and the
    working directory.
    """
    utility.debug('Loading verbspace for "%s" from "%s"...' % (command_name, command_dir))
    scan_base_dirs = [os.path.dirname(__file__)]
    verbs_subdir = '%s.d' % command_name
    if command_dir is not None and command_dir not in scan_base_dirs:
        scan_base_dirs.append(command_dir)
    cwd = os.getcwd()
    if cwd not in scan_base_dirs:
        scan_base_dirs.append(cwd)

    # Build the VOLT namespace with the specific set of classes, functions and
    # decorators we make available to command implementations.
    verbs = {}
    verb_decorators = VerbDecorators(verbs)
    namespace_VOLT = VOLT(verb_decorators)

    # Build the verbspace by executing modules found based on the calling
    # script location and the location of this module. The executed modules
    # have decorator calls that populate the verbs dictionary.
    finder = utility.PythonSourceFinder()
    for scan_dir in scan_base_dirs:
        finder.add_path(os.path.join(scan_dir, verbs_subdir))
    # If running from a zip package add resource locations.
    if package:
        finder.add_resource('__main__', os.path.join('voltcli', verbs_subdir))
    finder.search_and_execute(VOLT = namespace_VOLT)

    # Add standard verbs if they aren't supplied.
    def default_func(runner):
        runner.go()
    for verb_name, verb_cls in (('help', HelpVerb), ('package', PackageVerb)):
        if verb_name not in verbs:
            verbs[verb_name] = verb_cls(verb_name, default_func)

    return VerbSpace(command_name, version, description, namespace_VOLT, verbs)

#===============================================================================
class VoltConfig(utility.PersistentConfig):
#===============================================================================
    """
    Volt-specific persistent configuration provides customized error messages.
    """
    def __init__(self, permanent_path, local_path):
        utility.PersistentConfig.__init__(self, 'INI', permanent_path, local_path)

    def get_required(self, key):
        value = self.get(key)
        if value is None:
            utility.abort('Configuration parameter "%s" was not found.' % key,
                            'Set parameters using the "config" command, for example:',
                            ['%s config %s=VALUE' % (environment.command_name, key)])
        return value

#===============================================================================
def run_command(verbspace, internal_verbspaces, config, *args, **kwargs):
#===============================================================================
    """
    Run a command after parsing the command line arguments provided.
    """
    # Parse the command line.
    processor = cli.VoltCLICommandProcessor(verbspace.verbs,
                                            base_cli_spec.cli_options,
                                            base_cli_spec.usage,
                                            '\n'.join((verbspace.description,
                                                       base_cli_spec.description)),
                                            '%%prog version %s' % verbspace.version)
    command = processor.parse(args)

    # Initialize utility function options according to parsed options.
    utility.set_verbose(command.outer_opts.verbose)
    utility.set_debug(  command.outer_opts.debug)
    utility.set_dryrun( command.outer_opts.dryrun)

    # Run the command. Pass along kwargs. This allows verbs calling other verbs
    # to add keyword arguments like "classpath".
    runner = VerbRunner(command, verbspace, internal_verbspaces, config, processor, **kwargs)
    runner.execute()

#===============================================================================
def main(command_name, command_dir, version, description, *args, **kwargs):
#===============================================================================
    """
    Called by running script to execute command with command line arguments.
    """
    # For now "package" is the only valid keyword to flag when running from a
    # package zip __main__.py.
    package = utility.kwargs_get(kwargs, 'package', default = False)
    try:
        # Pre-scan for verbose, debug, and dry-run options so that early code
        # can display verbose and debug messages, and obey dry-run.
        preproc = cli.VoltCLICommandPreprocessor(base_cli_spec.cli_options)
        preproc.preprocess(args)
        utility.set_verbose(preproc.get_option('-v', '--verbose') == True)
        utility.set_debug(  preproc.get_option('-d', '--debug'  ) == True)
        utility.set_dryrun( preproc.get_option('-n', '--dry-run') == True)

        # Load the configuration and state
        permanent_path = os.path.join(os.getcwd(), 'volt.cfg')
        local_path     = os.path.join(os.getcwd(), 'volt_local.cfg')
        config = VoltConfig(permanent_path, local_path)

        # Initialize the environment
        environment.initialize(command_name, command_dir, version)

        # Search for modules based on both this file's and the calling script's location.
        verbspace = load_verbspace(command_name, command_dir, config, version,
                                   description, package)

        # Make internal commands available to user commands via runner.verbspace().
        internal_verbspaces = {}
        if command_name not in internal_commands:
            for internal_command in internal_commands:
                internal_verbspace = load_verbspace(internal_command, None, config, version,
                                                    'Internal "%s" command' % internal_command,
                                                    package)
                internal_verbspaces[internal_command] = internal_verbspace

        # Run the command
        run_command(verbspace, internal_verbspaces, config, *args)

    except KeyboardInterrupt:
        sys.stderr.write('\n')
        utility.abort('break')