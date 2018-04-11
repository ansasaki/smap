#!/usr/bin/env python
from __future__ import print_function

import argparse
import logging
import os
import re
import shutil
import sys

# import warnings

VERBOSITY_MAP = {"debug": logging.DEBUG,
                 "info": logging.INFO,
                 "warning": logging.WARNING,
                 "error": logging.ERROR,
                 "quiet": logging.CRITICAL}


class Single_Logger(object):
    """
    A singleton logger for the module
    """
    __instance = None

    @classmethod
    def getLogger(cls, name, filename=None):
        """
        Get the unique instance of the logger

        :param name: The name of the module (usually just __name__)
        :returns: An instance of logging.Logger
        """
        if Single_Logger.__instance is None:
            # Get logger
            logger = logging.getLogger(name)

            if filename:
                file_handler = logging.FileHandler(filename)
                logger.addHandler(file_handler)

            # Setup a handler to print warnings and above to stderr
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.WARNING)
            console_format = "[%(levelname)s] %(message)s"
            console_formatter = logging.Formatter(console_format)
            console_handler.setFormatter(console_formatter)

            logger.addHandler(console_handler)

            Single_Logger.__instance = logger
        return Single_Logger.__instance


def get_version_from_string(version_string):
    """
    Get the version numbers from a string

    :param version_string: A string composed by numbers separated by non
    alphanumeric characters (e.g. 0_1_2 or 0.1.2)
    :returns: A list of the numbers in the string
    """
    # Get logger
    logger = Single_Logger.getLogger(__name__)

    m = re.findall(r'[a-zA-Z0-9]+', version_string)

    if m:
        if len(m) < 2:
            msg = "".join(["Provide at least a major and a minor",
                           " version digit (eg. '1.2.3' or '1_2')"])
            logger.warn(msg)
            # warnings.warn(msg)
        if len(m) > 3:
            msg = "".join(["Version has too many parts; provide 3 or less",
                           " ( e.g. '0.1.2')"])
            logger.warn(msg)
            # warnings.warn(msg)
    else:
        msg = "".join(["Could not get version parts. Provide digits separated",
                       " by non-alphanumeric characters.",
                       " (e.g. 0_1_2 or 0.1.2)"])
        logger.error(msg)
        raise Exception(msg)

    version = [int(i) for i in m]

    return version


def get_info_from_release_string(release):
    """
    Get the information from a release name

    The given string is split in a prefix (usually the name of the lib) and a
    suffix (the version part, e.g. '_1_4_7'). A list with the version info
    converted to ints is also contained in the returned list.

    :param release: A string in format 'LIBX_1_0_0' or similar
    :returns: A list in format [release, prefix, suffix, [CUR, AGE, REV]]
    """

    version = [None, None, None]
    ver_suffix = ''
    prefix = ''
    tail = ''

    # Search for the first ocurrence of a version like sequence
    m = re.search(r'_+[0-9]+', release)
    if m:
        # If found, remove the version like sequence to get the prefix
        prefix = release[:m.start()]
        tail = release[m.start():]
    else:
        # The release does not have version info, but can have trailing '_'
        m = re.search(r'_+$', release)
        if m:
            # If so, remove the trailing '_'
            prefix = release[:m.start()]
        else:
            # Otherwise the prefix is the whole release name
            prefix = release

    if tail:
        # Search and get the version information
        version = get_version_from_string(tail)
        if version:
            # for i in version:
            #    ver_suffix += "_%d" %(i)
            ver_suffix = "".join(["_" + str(i) for i in version])

    # Return the information got
    return [release, prefix, ver_suffix, version]


# TODO: Make bump strategy customizable
def bump_version(version, abi_break):
    """
    Bump a version depending if the ABI was broken or not

    If the ABI was broken, CUR is bumped; AGE and REV are set to zero.
    Otherwise, CUR is kept, AGE is bumped, and REV is set to zero.
    This also works with versions without the REV component (e.g. [1, 4, None])

    :param version:     A list in format [CUR, AGE, REV]
    :param abi_break:   A boolean indication if the ABI was broken
    :returns:           A list in format [CUR, AGE, REV]
    """

    new_version = []
    if abi_break:
        if version[0] is not None:
            new_version.append(version[0] + 1)
        if len(version) > 1:
            for i in version[1:]:
                new_version.append(0)
    else:
        if version[0] is not None:
            new_version.append(version[0])
        if version[1] is not None:
            new_version.append(version[1] + 1)
        if len(version) > 2:
            for i in version[2:]:
                new_version.append(0)
    return new_version


def clean_symbols(symbols):
    """
    Receives a list of lines read from the input and returns a list of words

    :param symbols: A list of lines containing symbols
    :returns:       A list of the obtained symbols
    """

    # Split the lines into potential symbols and remove invalid characters
    clean = []
    if symbols:
        for line in symbols:
            parts = re.split(r'\W+', line)
            if parts:
                for symbol in parts:
                    m = re.match(r'\w+', symbol)
                    if m:
                        clean.append(m.group())

    return clean


# Error classes

class ParserError(Exception):
    """
    Exception type raised by the map parser

    Used mostly to keep track where an error was found in the given file

    Attributes:
        filename:    The name (path) of the file being parsed
        context:     The line where the error was detected
        line:        The index of the line where the error was detected
        column:      The index of the column where the error was detected
        message:     The error message
    """

    def __str__(self):
        content = "".join(["In file ", self.filename,
                           ", line ", str(self.line + 1),
                           ", column ", str(self.column),
                           ": ", self.message,
                           "\n",
                           self.context,
                           (" " * (self.column - 1)),
                           "^"])
        return content

    def __init__(self, filename, context, line, column, message):
        """
        The constructor

        :param filename:    The name (path) of the file being parsed
        :param context:     The line where the error was detected
        :param line:        The index of the line where the error was detected
        :param column:      The index of the column where the error was detected
        :param message:     The error message
        """
        self.filename = filename
        self.context = context
        self.line = line
        self.column = column
        self.message = message


# Map class

class Map(object):
    """
    A linker map (version script) representation

    This class is an internal representation of a version script.
    It is intended to be initialized by calling the method ``read()`` and
    passing the path to a version script file.
    The parser will parse the file and check the file syntax, creating a list of
    releases (instances of the ``Release`` class), which is stored in ``releases``.

    Attributes:
        init:       Indicates if the object was initialized by calling
                    ``read()``
        logger:     The logger object; can be specified in the constructor
        filename:   Holds the name (path) of the file read
        lines:      A list containing the lines of the file
    """

    # To make printable
    def __str__(self):
        """
        Print the map in a usable form for the linker

        :returns: A string containing the whole map file as it would be written
                  in a file
        """

        content = "".join([str(release) + "\n" for release in
                           self.releases])
        return content

    # Constructor
    def __init__(self, filename=None, logger=None):
        """
        The constructor.

        :param filename: The name of the file to be read. If provided the
                         ``read()`` method is called using this name.
        :param logger:   A logger object. If not provided, the module based
                         logger will be used
        """

        # The state
        self.init = False
        self.releases = []
        # Logging
        self.logger = Single_Logger.getLogger(__name__)
        # From the raw file
        self.filename = ''
        self.lines = []
        if filename:
            self.read(filename)

    def parse(self, lines):
        """
        A simple version script parser.

        This is the main initializator of the ``releases`` list.
        This simple parser receives the lines of a given version script, check its
        syntax, and construct the list of releases.
        Some semantic aspects are checked, like the existence of the ``*`` wildcard
        in global scope and the existence of duplicated release names.

        It works by running a finite state machine:

         The parser states. Can be:
            0. name: The parser is searching for a release name or ``EOF``
            1. opening: The parser is searching for the release opening ``{``
            2. element: The parser is searching for an identifier name or ``}``
            3. element_closer: The parser is searching for ``:`` or ``;``
            4. previous: The parser is searching for previous release name
            5. previous_closer: The parser is searching for ``;``

        :param lines: The lines of a version script file
        """

        state = 0

        # The list of releases parsed
        releases = []
        last = (0, 0)

        for index, line in enumerate(lines):
            column = 0
            while column < len(line):
                try:
                    # Remove whitespaces or comments
                    m = re.match(r'\s+|\s*#.*$', line[column:])
                    if m:
                        column += m.end()
                        last = (index, column)
                        continue
                    # Searching for a release name
                    if state == 0:
                        self.logger.debug(">>Name")
                        m = re.match(r'\w+', line[column:])
                        if m is None:
                            raise ParserError(self.filename,
                                              lines[last[0]], last[0],
                                              last[1],
                                              "Invalid Release identifier")
                        else:
                            # New release found
                            name = m.group(0)
                            # Check if a release with this name is present
                            has_duplicate = [release for release in releases if
                                             release.name == name]
                            column += m.end()
                            r = Release()
                            r.name = m.group(0)
                            releases.append(r)
                            last = (index, column)
                            state += 1
                            if has_duplicate:
                                msg = "".join(["Duplicated Release identifier"
                                               " \'", name, "\'"])
                                # This is non-critical, only warning
                                self.logger.warn(ParserError(self.filename,
                                                             lines[index],
                                                             index,
                                                             column, msg))
                            continue
                    # Searching for the '{'
                    elif state == 1:
                        self.logger.debug(">>Opening")
                        m = re.match(r'\{', line[column:])
                        if m is None:
                            raise ParserError(self.filename,
                                              lines[last[0]], last[0], last[1],
                                              "Missing \'{\'")
                        else:
                            column += m.end()
                            v = None
                            last = (index, column)
                            state += 1
                            continue
                    elif state == 2:
                        self.logger.debug(">>Element")
                        m = re.match(r'\}', line[column:])
                        if m:
                            self.logger.debug(">>Closer, jump to Previous")
                            column += m.end()
                            last = (index, column)
                            state = 4
                            continue
                        m = re.match(r'\w+|\*', line[column:])
                        if m is None:
                            raise ParserError(self.filename,
                                              lines[last[0]], last[0], last[1],
                                              "Invalid identifier")
                        else:
                            # In this case the position before the
                            # identifier is stored
                            last = (index, m.start())
                            column += m.end()
                            identifier = m.group(0)
                            state += 1
                            continue
                    elif state == 3:
                        self.logger.debug(">>Element closer")
                        m = re.match(r';', line[column:])
                        if m is None:
                            # It was not Symbol. Maybe a new visibility.
                            m = re.match(r':', line[column:])
                            if m is None:
                                msg = "".join(["Missing \';\' or \':\' after",
                                               " \'", identifier, "\'"])
                                # In this case the current position is used
                                raise ParserError(self.filename,
                                                  lines[index], index, column,
                                                  msg)
                            else:
                                # New visibility found
                                v = (identifier, [])
                                r.symbols.append(v)
                                column += m.end()
                                last = (index, column)
                                state = 2
                                continue
                        else:
                            if v is None:
                                # There was no open visibility scope
                                v = ('global', [])
                                r.symbols.append(v)
                                msg = "".join(["Missing visibility scope",
                                               " before \'", identifier, "\'.",
                                               " Symbols considered in",
                                               " \'global:\'"])
                                # Non-critical, only warning
                                self.logger.warn(ParserError(self.filename,
                                                             lines[last[0]],
                                                             last[0], last[1],
                                                             msg))
                            else:
                                # Symbol found
                                v[1].append(identifier)
                                column += m.end()
                                last = (index, column)
                                # Move back the state to find elements
                                state = 2
                                continue
                    elif state == 4:
                        self.logger.debug(">>Previous")
                        m = re.match(r'^;', line[column:])
                        if m:
                            self.logger.debug(">>Empty previous")
                            column += m.end()
                            last = (index, column)
                            # Move back the state to find other releases
                            state = 0
                            continue
                        m = re.match(r'\w+', line[column:])
                        if m is None:
                            raise ParserError(self.filename,
                                              lines[last[0]], last[0], last[1],
                                              "Invalid identifier")
                        else:
                            # Found previous release identifier
                            column += m.end()
                            identifier = m.group(0)
                            last = (index, column)
                            state += 1
                            continue
                    elif state == 5:
                        self.logger.debug(">>Previous closer")
                        m = re.match(r'^;', line[column:])
                        if m is None:
                            raise ParserError(self.filename,
                                              lines[last[0]], last[0], last[1],
                                              "Missing \';\'")
                        else:
                            # Found previous closer
                            column += m.end()
                            r.previous = identifier
                            last = (index, column)
                            # Move back the state to find other releases
                            state = 0
                            continue

                except ParserError as e:
                    # Any exception raised is considered an error
                    self.logger.error(e)
                    raise e
        # Store the parsed releases
        self.releases = releases

    def read(self, filename):
        """
        Read a linker map file (version script) and store the obtained releases

        Obtain the lines of the file and calls ``parse()`` to parse the file

        :param filename:        The path to the file to be read
        :raises ParserError:    Raised when a syntax error is found in the file
        """

        with open(filename, "r") as f:
            self.filename = filename
            self.lines = f.readlines()
            self.parse(self.lines)
            # Check the map read
            self.check()
            self.init = True

    def all_global_symbols(self):
        """
        Returns all global symbols from all releases contained in the Map
        object

        :returns: A set containing all global symbols in all releases
        """

        symbols = []
        for release in self.releases:
            for scope, scope_symbols in release.symbols:
                if scope.lower() == 'global':
                    symbols.extend(scope_symbols)
        return set(symbols)

    def duplicates(self):
        """
        Find and return a list of duplicated symbols for each release

        If no duplicates are found, return an empty list

        :returns: A list of tuples [(release, [(scope, [duplicates])])]
        """

        duplicates = []
        for release in self.releases:
            rel_dup = release.duplicates()
            if rel_dup:
                duplicates.append((release.name, rel_dup))
        return duplicates

    def dependencies(self):
        """
        Construct the dependencies lists

        Contruct a list of dependency lists. Each dependency list contain the
        names of the releases in a dependency path.
        The heads of the dependencies lists are the releases not refered as a
        previous release in any release.

        :returns:   A list containing the dependencies lists
        """

        def get_dependency(releases, head):
            found = [release for release in releases if release.name == head]
            if not found:
                msg = "".join(["Release \'", head, "\' not found"])
                self.logger.error(msg)
                raise Exception(msg)
            if len(found) > 1:
                msg = "".join(["defined more than 1 release ",
                               "\'", head, "\'"])
                self.logger.error(msg)
                raise Exception(msg)
            return found[0].previous

        solved = []
        deps = []
        for release in self.releases:
            # If the dependencies of the current release were resolved, skip
            if release.name in solved:
                continue
            else:
                current = [release.name]
                dep = release.previous
                # Construct the current release dependency list
                while dep:
                    # If the found dependency was already in the list
                    if dep in current:
                        msg = "".join(["Circular dependency detected!\n",
                                       "    "] +
                                      [i + "->" for i in current] +
                                      [dep])
                        self.logger.error(msg)
                        raise Exception(msg)
                    # Append the dependency to the current list
                    current.append(dep)

                    # Remove the releases that are not heads from the list
                    if dep in solved:
                        for i in deps:
                            if i[0] == dep:
                                deps.remove(i)
                    else:
                        solved.append(dep)
                    dep = get_dependency(self.releases, dep)
                solved.append(release.name)
                deps.append(current)
        return deps

    def check(self):
        """
        Check the map structure.

        Reports errors found in the structure of the map in form of warnings.
        """

        have_wildcard = []
        seems_base = []

        # Find duplicated symbols
        d = self.duplicates()
        if d:
            for release, duplicates in d:
                msg = "".join(["Duplicates found in release \'", release,
                               "\':"])
                self.logger.warn(msg)
                # warnings.warn(msg)
                for scope, symbols in duplicates:
                    msg = ' ' * 4 + scope + ':'
                    self.logger.warn(msg)
                    # warnings.warn(msg)
                    for symbol in symbols:
                        msg = ' ' * 8 + symbol
                        self.logger.warn(msg)
                        # warnings.warn(msg)

        # Check '*' wildcard usage
        for release in self.releases:
            for scope, symbols in release.symbols:
                if scope == 'local':
                    if symbols:
                        if "*" in symbols:
                            msg = "".join([release.name,
                                           " contains the local \'*\'",
                                           " wildcard"])
                            self.logger.info(msg)
                            if release.previous:
                                # Predecessor version and local: *; are present
                                msg = "".join([release.name,
                                               " should not contain the",
                                               " local wildcard because",
                                               " it is not the base",
                                               " version (it refers to",
                                               " version ",
                                               release.previous,
                                               " as its predecessor)"])
                                self.logger.warn(msg)
                                # warnings.warn(msg)
                            else:
                                # Release seems to be base: empty predecessor
                                msg = "".join([release.name, "seems to",
                                               " be the base version"])
                                self.logger.info(msg)
                                seems_base.append(release.name)

                            # Append to the list of releases which contain the
                            # wildcard '*'
                            have_wildcard.append((release.name, scope))
                elif scope == 'global':
                    if symbols:
                        if "*" in symbols:
                            # Release contains '*' wildcard in global scope
                            msg = "".join([release.name, " contains the",
                                           " \'*\' wildcard in global",
                                           " scope. It is probably",
                                           " exporting symbols it should",
                                           " not."])
                            self.logger.warn(msg)
                            # warnings.warn(msg)
                            have_wildcard.append((release.name, scope))
                else:
                    # Release contains unknown visibility scopes (not global or
                    # local)
                    msg = "".join([release.name, "contains unknown scope named ",
                                   scope, " (different from ",
                                   " \'global\' and \'local\')"])
                    self.logger.warn(msg)
                    # warnings.warn(msg)

        if have_wildcard:
            if len(have_wildcard) > 1:
                # The '*' wildcard was found in more than one place
                msg = "".join(["The \'*\' wildcard was found in more than",
                               " one place:"])
                self.logger.warn(msg)
                # warnings.warn(msg)
                for name, scope in have_wildcard:
                    msg = "".join([" " * 4, name, ": in \'", scope, "\'"])
                    self.logger.warn(msg)
                    # warnings.warn(msg)
        else:
            msg = "The \'*\' wildcard was not found"
            self.logger.warn(msg)
            # warnings.warn(msg)

        if seems_base:
            if len(seems_base) > 1:
                # There is more than one release without predecessor and
                # containing '*' wildcard in local scope
                msg = "".join(["More than one release seems the base",
                               " version (contains the local wildcard",
                               " and does not have a predecessor"
                               " version):"])
                self.logger.warn(msg)
                # warnings.warn(msg)
                for name in seems_base:
                    msg = "".join([" " * 4, name])
                    self.logger.warn(msg)
                    # warnings.warn(msg)
        else:
            msg = "No base version release found"
            self.logger.warn(msg)
            # warnings.warn(msg)

        dependencies = self.dependencies()
        self.logger.info("Found dependencies: ")
        for release in dependencies:
            content = [" " * 4]
            content.extend((dep + "->" for dep in release))
            cur = "".join(content)
            self.logger.info(cur)

    def guess_latest_release(self):
        """
        Try to guess the latest release

        It uses the information found in the releases present in the version
        script read. It tries to find the latest release using heuristics.

        :returns:   A list [release, prefix, suffix, version[CUR, AGE, REV]]
        """

        if not self.init:
            msg = "Map not initialized, try to read a file first"
            self.logger.error(msg)
            raise Exception(msg)

        deps = self.dependencies()

        heads = (dep[0] for dep in deps)

        latest = [None, None, '_0_0_0', None]
        for release in heads:
            info = get_info_from_release_string(release)
            if info[2] > latest[2]:
                latest = info

        return latest

    def guess_name(self, abi_break=False, new_release=None, new_prefix=None,
                   new_suffix=None, new_ver=None, prev_release=None,
                   prev_prefix=None, prev_ver=None):
        """
        Use the given information to guess the name for the new release

        The two parts necessary to make the release name:
            - The new prefix: Usually the library name (e.g. LIBX)
            - The new suffix: The version information (e.g. _1_2_3)

        If the new prefix is not provided:
            1. Try previous prefix, if given
            2. Try previous release name, if given
                - This will also set the version, if not set yet
            3. Try to find a common prefix between release names
            4. Try to find latest release

        If the new suffix is not provided:
            1. Try previous version, if given
            2. Try previous release name, if given
                - This will also set the prefix, if not set yet
            4. Try to find latest release version

        :param abi_break:   Boolean, indicates if the ABI was broken
        :param new_release: String, the name of the new release. If this is
                            provided, the guessing is avoided and this will
                            be used as the release name
        :param new_prefix:  The prefix to be used (library name)
        :param new_suffix:  The suffix to be used (version, like \'_1_0_0\')
        :param new_ver:     A list of int, the components of the version (e.g.
                            [CURRENT, AGE, RELEASE]).
        :param prev_release:    The name of the previous release.
        :param prev_prefix:     The previous release prefix (library name)
        :param prev_ver:        A list of int, the components of the previous
                                version (e.g. [CURRENT, AGE, RELEASE])
        :returns: The guessed release name (new prefix + new suffix)
        """

        # If the two required parts were given, just combine and return
        if new_prefix:
            if new_suffix:
                self.logger.debug("[guess]: Two parts found, using them")
                return new_prefix.upper() + new_suffix
            elif new_ver:
                self.logger.debug("[guess]: Prefix and version found, using them")
                new_suffix = "".join(["_" + str(i) for i in new_ver])
                return new_prefix.upper() + new_suffix

        # If the new release name was given (and could not be parsed), use it
        if new_release:
            self.logger.debug("[guess]: New release found, using it")
            return new_release.upper()

        # If a previous release was given, extract info and check it
        if prev_release:
            self.logger.debug("[guess]: Previous release found")
            info = get_info_from_release_string(prev_release)
            # If the prefix was successfully extracted
            if info[1]:
                # Use it as the new prefix, if none was given
                if not new_prefix:
                    new_prefix = info[1]

            # If the version was successfully extracted
            if info[3]:
                if not prev_ver:
                    prev_ver = info[3]

        if not new_prefix:
            if prev_prefix:
                self.logger.debug("[guess]: Using previous prefix as the new")
                # Reuse the prefix from the previous release, if available
                new_prefix = prev_prefix
            else:
                self.logger.debug("[guess]: Trying to find common prefix")
                # Find a common prefix between all releases
                names = [release.name for release in self.releases]
                if names:
                    s1 = min(names)
                    s2 = max(names)
                    for i, c in enumerate(s1):
                        if c != s2[i]:
                            break
                    if s1[i] != s2[i]:
                        new_prefix = s1[:i]
                    else:
                        new_prefix = s1

                    # If a common prefix was found, use it
                    if new_prefix:
                        self.logger.debug("[guess]: Common prefix found")
                        # Search and remove any version info found as prefix
                        m = re.search(r'_+[0-9]+|_+$', new_prefix)
                        if m:
                            new_prefix = new_prefix[:m.start()]
                    else:
                        self.logger.debug("[guess]: Using prefix from latest")
                        # Try to use the latest_release prefix
                        head = self.guess_latest_release()
                        new_prefix = head[1]

        # At this point, new_prefix can still be None

        if not new_suffix:
            self.logger.debug("[guess]: Guessing new suffix")

            # If the new version was given, make the suffix from it
            if new_ver:
                self.logger.debug("[guess]: Using new version to make suffix")
                new_suffix = "".join(("_" + i for i in new_ver))

            elif not prev_ver:
                self.logger.debug("[guess]: Guessing latest release to make suffix")
                # Guess the latest release
                head = self.guess_latest_release()
                if head[3]:
                    self.logger.debug("[guess]: Got suffix from latest")
                    prev_ver = head[3]

            if not new_suffix:
                if prev_ver:
                    self.logger.debug("[guess]: Bumping release")
                    new_ver = bump_version(prev_ver, abi_break)
                    new_suffix = "".join(("_" + str(i) for i in new_ver))

        if not new_prefix or not new_suffix:
            # ERROR: could not guess the name
            msg = "".join(["Insufficient information to guess the new release",
                           " name. Releases found do not have version",
                           " information."])
            self.logger.error(msg)
            raise Exception(msg)

        # Return the combination of the prefix and version
        return new_prefix.upper() + new_suffix

    def sort_releases_nice(self, top_release):
        """
        Sort the releases contained in a map file putting the dependencies of
        ``top_release`` first. This changes the order of the list in
        ``releases``.

        :param top_release: The release whose dependencies should be prioritized
        """

        self.releases.sort(key=lambda release: release.name)
        dependencies = self.dependencies()
        top_dependency = next((dependency for dependency in dependencies if
                               dependency[0] == top_release))

        new_list = []
        index = 0

        while self.releases:
            release = self.releases.pop()
            if release.name in top_dependency:
                new_list.insert(index, release)
                index += 1
            else:
                new_list.append(release)

        self.releases = new_list


class Release(object):
    """
    A internal representation of a release version and its symbols

    A release is usually identified by the library name (suffix) and the release
    version (suffix). A release contains symbols, grouped by their visibility
    scope (global or local).

    In this class the symbols of a release are stored in a list of dictionaries
    mapping a visibility scope name (e.g. \"global\") to a list of the contained
    symbols:
    ::

        ([{"global": [symbols]}, {"local": [local_symbols]}])

    Attributes:
        name: The release name
        previous: The previous release to which this release is dependent
        symbols: The symbols contained in the release, grouped by the visibility
                 scope.
    """

    def __init__(self):
        self.name = ''
        self.previous = ''
        self.symbols = []

    def __str__(self):
        content = ''
        content += self.name
        content += "\n{\n"
        for visibility, symbols in self.symbols:
            symbols.sort()
            content += "    "
            content += visibility
            content += ":\n"
            for symbol in symbols:
                content += "        "
                content += symbol
                content += ";\n"
        content += "} "
        content += self.previous
        content += ";\n"
        return content

    def duplicates(self):
        duplicates = []
        for scope, symbols in self.symbols:
            seen = []
            release_dups = []
            if symbols:
                for symbol in symbols:
                    if symbol not in seen:
                        seen.append(symbol)
                    else:
                        release_dups.append(symbol)
                if release_dups:
                    duplicates.append((scope, set(release_dups)))
        return duplicates


def check_files(out_arg, out_name, in_arg, in_name, dry):
    """
    Check if output and input are the same file. Create a backup if so.

    :param out_arg:  The name of the option used to receive output file name
    :param out_name: The received string as output file path
    :param in_arg:   The name of the option used to receive input file name
    :param in_name:  The received string as input file path
    """

    # Get logger
    logger = Single_Logger.getLogger(__name__)

    # Check if the first file exists
    if os.path.isfile(out_name):
        # Check if given input file is the same as output
        if os.path.isfile(in_name):
            if os.path.samefile(out_name, in_name):
                msg = "".join(["Given paths in \'",
                               str(out_arg), "\' and \'",
                               str(in_arg), "\' are the same."])
                logger.warn(msg)
                # warnings.warn(msg)

                # Avoid changing the files if this is a dry run
                if dry:
                    return

                msg = "".join(["Moving \'",
                               str(in_name), "\' to \'",
                               str(in_name), ".old\'."])
                logger.warn(msg)
                # warnings.warn(msg)
                try:
                    # If it is the case, copy to another file to
                    # preserve the content
                    shutil.copy2(str(in_name), str(in_name) + ".old")
                except Exception as e:
                    msg = "".join(["Could no copy \'",
                                   str(in_name), "\' to \'",
                                   str(in_name), ".old\'. Aborting."])
                    logger.error(msg)
                    raise e


def update(args):
    """
    Given the new list of symbols, update the map

    The new map will be generated by the following rules:
        - If new symbols are added, a new release is created containing the new
          symbols. This is a compatible update.
        - If a previous existing symbol is removed, then all releases are
          unified in a new release. This is an incompatible change, the SONAME
          of the library should be bumped

    :param args: Arguments given in command line parsed by argparse
    """

    # Get logger
    logger = Single_Logger.getLogger(__name__, filename=args.logfile)

    logger.info("Command: update")
    logger.debug("Arguments provided: ")
    logger.debug(str(args))

    # Set the verbosity if provided
    if args.verbosity:
        logger.setLevel(VERBOSITY_MAP[args.verbosity])

    # If output would be overwritten, print a warning
    if args.out:
        if os.path.isfile(args.out):
            msg = "".join(["Overwriting existing file \'", args.out, "\'"])
            logger.warn(msg)
            # warnings.warn(msg)

    # If both output and input files were given, check if are the same
    if args.out and args.input:
        check_files('--out', args.out, '--in', args.input, args.dry)

    # If output is given, check with the file to be updated
    if args.out and args.file:
        check_files('--out', args.out, 'file', args.file, args.dry)

    # Read the current map file
    cur_map = Map(filename=args.file, logger=logger)

    # Get all global symbols
    all_symbols = list(cur_map.all_global_symbols())

    # Generate the list of the new symbols
    new_symbols = []
    if args.input:
        with open(args.input, "r") as symbols_fp:
            lines = symbols_fp.readlines()
            for line in lines:
                new_symbols.extend(line.split())
    else:
        # Read from stdin
        lines = sys.stdin.readlines()
        for line in lines:
            new_symbols.extend(line.split())

    # Clean the input removing invalid symbols
    new_symbols = clean_symbols(new_symbols)

    # All symbols read
    new_set = set(new_symbols)

    added = []
    removed = []

    # If the list of all symbols are being compared
    if args.symbols:
        for symbol in new_set:
            if symbol not in all_symbols:
                added.append(symbol)

        for symbol in all_symbols:
            if symbol not in new_set:
                removed.append(symbol)
    # If the list of symbols are being added
    elif args.add:
        # Check the symbols and print a warning if already present
        for symbol in new_symbols:
            if symbol in all_symbols:
                msg = "".join(["The symbol \'", symbol, "\' is already",
                               " present in a previous version. Keep the",
                               " previous implementation to not break ABI."])
                logger.warn(msg)
                # warnings.warn(msg)

        added.extend(new_symbols)
    # If the list of symbols are being removed
    elif args.remove:
        # Remove the symbols to be removed
        for symbol in new_symbols:
            if symbol in all_symbols:
                removed.append(symbol)
            else:
                msg = "".join(["Requested to remove \'", symbol, "\', but",
                               " not found."])
                logger.warn(msg)
                # warnings.warn(msg)
    else:
        # Execution should never reach this point
        raise Exception("No strategy was provided (add/delete/symbols)")

    # Remove duplicates
    added = list(set(added))
    removed = list(set(removed))

    # Print the modifications
    if added:
        added.sort()
        content = ["Added:\n"]
        content.extend(["    " + symbol + "\n" for symbol in added])
        msg = "".join(content)
        print(msg)

    if removed:
        removed.sort()
        content = ["Removed:\n"]
        content.extend(["    " + symbol + "\n" for symbol in removed])
        msg = "".join(content)
        print(msg)

    # Guess the latest release
    latest = cur_map.guess_latest_release()

    if not added and not removed:
        print("No symbols added or removed. Nothing done.")
        return

    if added:
        r = Release()
        # Guess the name for the new release
        r.name = cur_map.guess_name()
        r.name.upper()

        # Add the symbols added to global scope
        r.symbols.append(("global", added))

        if not removed:
            # Add the name for the previous release
            r.previous = latest[0]

            # Put the release on the map
            cur_map.releases.append(r)

    if removed:
        if args.care:
            msg = "ABI break detected: symbols would be removed"
            logger.error(msg)
            raise Exception(msg)

        msg = "ABI break detected: symbols were removed."
        logger.warn(msg)
        # warnings.warn(msg)
        print("Merging all symbols in a single new release")
        new_map = Map()
        r = Release()

        # Guess the name of the new release
        r.name = cur_map.guess_name(abi_break=True)
        r.name.upper()

        # Add the symbols added to global scope
        all_symbols.extend(added)

        # Remove duplicates
        all_symbols = list(set(all_symbols))

        # Remove the symbols to be removed
        for symbol in removed:
            all_symbols.remove(symbol)

        # Remove the '*' wildcard, if present
        if '*' in all_symbols:
            msg = "".join(["Wildcard \'*\' found in global. Removed to avoid",
                           " exporting unexpected symbols."])
            logger.warn(msg)
            # warnings.warn(msg)
            all_symbols.remove('*')

        r.symbols.append(('global', all_symbols))

        # Add the wildcard to the local symbols
        r.symbols.append(('local', ['*']))

        # Put the release on the map
        new_map.releases.append(r)

        # Substitute the map
        cur_map = new_map

    # Do a structural check
    cur_map.check()

    # Sort the releases putting the new release and dependencies first
    cur_map.sort_releases_nice(r.name)

    if args.dry:
        print("This is a dry run, the files were not modified.")
        return

    # Write out to the output
    if args.out:
        with open(args.out, "w") as outfile:
            outfile.write("# This map file was automatically updated\n\n")
            outfile.write(str(cur_map))
    else:
        # Print to stdout
        sys.stdout.write("# This map file was automatically updated\n\n")
        sys.stdout.write(str(cur_map))


def new(args):
    """
    \'new\' subcommand implementation

    Create a new version script file containing the provided symbols.

    :param args: Arguments given in command line parsed by argparse
    """

    # Get logger
    logger = Single_Logger.getLogger(__name__, filename=args.logfile)

    logger.info("Command: new")
    logger.debug("Arguments provided: ")
    logger.debug(str(args))

    # Set the verbosity if provided
    if args.verbosity:
        logger.setLevel(VERBOSITY_MAP[args.verbosity])

    # If output would be overwritten, print a warning
    if args.out:
        if os.path.isfile(args.out):
            msg = "".join(["Overwriting existing file \'", args.out, "\'"])
            logger.warn(msg)
            # warnings.warn(msg)

    # If both output and input files were given, check if are the same
    if args.out and args.input:
        check_files('--out', args.out, '--in', args.input, args.dry)

    release_info = None
    if args.release:
        # Parse the release name string to get info
        release_info = get_info_from_release_string(args.release)
    elif args.name and args.version:
        # Parse the given version string to get the version information
        version = get_version_from_string(args.version)
        # Construct the release info list
        release_info = [None, args.name, None, version]
    else:
        msg = "".join(["It is necessary to provide either release name or",
                       " name and version"])
        logger.error(msg)
        raise Exception(msg)

    if not release_info:
        msg = "Could not retrieve release information."
        logger.error(msg)
        raise Exception(msg)

    logger.debug("Release information:")
    logger.debug(str(release_info))

    # Generate the list of the new symbols
    new_symbols = []
    if args.input:
        with open(args.input, "r") as symbols_fp:
            lines = symbols_fp.readlines()
            for line in lines:
                new_symbols.extend(line.split())
    else:
        # Read from stdin
        lines = sys.stdin.readlines()
        for line in lines:
            new_symbols.extend(line.split())

    # Clean the input removing invalid symbols
    new_symbols = clean_symbols(new_symbols)

    if new_symbols:
        new_map = Map()
        r = Release()

        name = new_map.guess_name(new_release=release_info[0],
                                  new_prefix=release_info[1],
                                  new_ver=release_info[3])

        debug_msg = "".join(["Generated name: \'", name, "\'"])
        logger.debug(debug_msg)

        # Set the name of the new release
        r.name = name.upper()

        # Add the symbols to global scope
        r.symbols.append(('global', new_symbols))

        # Add the wildcard to the local symbols
        r.symbols.append(('local', ['*']))

        # Put the release on the map
        new_map.releases.append(r)

        # Do a structural check
        new_map.check()

        # Sort the releases putting the new release and dependencies first
        new_map.sort_releases_nice(r.name)

        if args.dry:
            print("This is a dry run, the files were not modified.")
            return

        # Write out to the output
        if args.out:
            with open(args.out, "w") as outfile:
                outfile.write("# This map file was created with"
                              " symbol_version.py\n\n")
                outfile.write(str(new_map))
        else:
            # Print to stdout
            sys.stdout.write("# This map file was created with"
                             " symbol_version.py\n\n")
            sys.stdout.write(str(new_map))
    else:
        msg = "No valid symbols provided. Nothing done."
        logger.warn(msg)
        # warnings.warn(msg)


def get_arg_parser():
    """
    Get a parser for the command line arguments

    The parser is capable of checking requirements for the arguments and
    possible incompatible arguments.

    :returns: A parser for command line arguments. (argparse.ArgumentParser)
    """
    # Common file arguments
    file_args = argparse.ArgumentParser(add_help=False)
    file_args.add_argument('-o', '--out',
                           help='Output file (defaults to stdout)')
    file_args.add_argument('-i', '--in',
                           help='Read from this file instead of stdio',
                           dest='input')
    file_args.add_argument('-d', '--dry',
                           help='Do everything, but do not modify the files',
                           action='store_true')
    file_args.add_argument('-l', '--logfile',
                           help='Log to this file')

    # Common verbosity arguments
    verb_args = argparse.ArgumentParser(add_help=False)
    group_verb = verb_args.add_mutually_exclusive_group()
    group_verb.add_argument('--verbosity', help='Set the program verbosity',
                            choices=['quiet', 'error', 'warning', 'info',
                                     'debug'],
                            default='warning')
    group_verb.add_argument('--quiet', help='Makes the program quiet',
                            dest='verbosity', action='store_const',
                            const='quiet')
    group_verb.add_argument('--debug', help='Makes the program print debug info',
                            dest='verbosity', action='store_const', const='debug')

    # Main arguments parser
    parser = argparse.ArgumentParser(description="Helper tools for linker"
                                     " version script maintenance",
                                     epilog="Call a subcommand passing \'-h\'"
                                     " to see its specific options")

    # Subcommands parser
    subparsers = parser.add_subparsers(title="Subcommands", description="Valid"
                                       " subcommands:",
                                       help="These subcommands have their own"
                                       "set of options")

    # Update subcommand parser
    parser_up = subparsers.add_parser("update", help="Update the map file",
                                      parents=[file_args, verb_args],
                                      epilog="A list of symbols is expected as"
                                      " the input.\nIf a file is provided with"
                                      " \'-i\', the symbols are read"
                                      " from the given file. Otherwise the"
                                      " symbols are read from stdin.")
    parser_up.add_argument("-c", "--care",
                           help="Do not continue if the ABI would be broken",
                           action='store_true')
    group = parser_up.add_mutually_exclusive_group(required=True)
    group.add_argument("-a", "--add", help="Adds the symbols to the map file.",
                       action='store_true')
    group.add_argument("-r", "--remove", help="Remove the symbols from the map"
                       " file. This breaks the ABI.", action="store_true")
    group.add_argument("-s", "--symbols",
                       help="Compare the given symbol list with the current"
                       " map file and update accordingly. May break the ABI.",
                       action='store_true')
    parser_up.add_argument('file', help='The map file being updated')
    parser_up.set_defaults(func=update)

    # New subcommand parser
    parser_new = subparsers.add_parser("new",
                                       help="Create a new map file",
                                       parents=[file_args, verb_args],
                                       epilog="A list of symbols is expected"
                                       "as the input.\nIf a file is provided"
                                       " with \'-i\', the symbols are read"
                                       " from the given file. Otherwise the"
                                       " symbols are read from stdin.")
    parser_new.add_argument("-n", "--name",
                            help="The name of the library (e.g. libx)")
    parser_new.add_argument("-v", "--version",
                            help="The release version (e.g. 1_0_0)")
    parser_new.add_argument("-r", "--release",
                            help="The full name of the release to be used"
                            " (e.g. LIBX_1_0_0)")
    parser_new.set_defaults(func=new)

    return parser


# User interface
if __name__ == "__main__":

    class C(object):
        """
        Empty class used as a namespace
        """
        pass

    ns = C()

    # Get the arguments parser
    parser = get_arg_parser()

    # Parse arguments
    args = parser.parse_args(sys.argv[1:], namespace=ns)

    # Run command
    ns.func(args)
