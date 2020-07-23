#!/usr/bin/env python
#
# Copyright (c) 2010, 2014, Oracle and/or its affiliates. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
#

"""
This file contains the replicate utility. It is used to establish a
main/subordinate replication topology among two servers.
"""

from mysql.utilities.common.tools import check_python_version

# Check Python version compatibility
check_python_version()

import os.path
import sys

from mysql.utilities.exception import FormatError, UtilError
from mysql.utilities.command.setup_rpl import setup_replication
from mysql.utilities.common.ip_parser import parse_connection
from mysql.utilities.common.server import check_hostname_alias
from mysql.utilities.common.tools import check_connector_python
from mysql.utilities.common.options import (setup_common_options, add_rpl_user,
                                            add_verbosity, add_ssl_options,
                                            get_ssl_dict,
                                            check_password_security)
from mysql.utilities.common.messages import (PARSE_ERR_OPTS_REQ,
                                             WARN_OPT_USING_DEFAULT)


# Constants
NAME = "MySQL Utilities - mysqlreplicate "
DESCRIPTION = "mysqlreplicate - establish replication with a main"
USAGE = "%prog --main=root@localhost:3306 --subordinate=root@localhost:3310 " \
        "--rpl-user=rpl:passwd "

# Check for connector/python
if not check_connector_python():
    sys.exit(1)

if __name__ == '__main__':
    # Setup the command parser
    parser = setup_common_options(os.path.basename(sys.argv[0]),
                                  DESCRIPTION, USAGE, True, False)

    # Setup utility-specific options:

    # Connection information for the source server
    parser.add_option('--main', action="store", dest="main",
                      type="string", default=None,
                      help="connection information for main server in the "
                           "form: <user>[:<password>]@<host>[:<port>]"
                           "[:<socket>] or <login-path>[:<port>][:<socket>]"
                           " or <config-path>[<[group]>].")

    # Connection information for the destination server
    parser.add_option('--subordinate', action="store", dest="subordinate",
                      type="string", default=None,
                      help="connection information for subordinate server in the "
                           "form: <user>[:<password>]@<host>[:<port>]"
                           "[:<socket>] or <login-path>[:<port>][:<socket>]"
                           " or <config-path>[<[group]>].")

    # Replication user and password
    add_rpl_user(parser)

    # Pedantic mode for failing if storage engines differ
    parser.add_option("-p", "--pedantic", action="store_true", default=False,
                      dest="pedantic", help="fail if storage engines differ "
                      "among main and subordinate.")

    # Test replication option
    parser.add_option("--test-db", action="store", dest="test_db",
                      type="string", help="database name to use in testing "
                      "replication setup (optional)")

    # Add main log file option
    parser.add_option("--main-log-file", action="store",
                      dest="main_log_file", type="string",
                      help="use this main log file to initiate the subordinate.",
                      default=None)

    # Add main log position option
    parser.add_option("--main-log-pos", action="store",
                      dest="main_log_pos", type="int",
                      help="use this position in the main log file to "
                           "initiate the subordinate.",
                      default=-1)

    # Add start from beginning option
    parser.add_option("-b", "--start-from-beginning", action="store_true",
                      default=False, dest="from_beginning",
                      help="start replication from the first event recorded "
                           "in the binary logging of the main. Not valid "
                           "with --main-log-file or --main-log-pos.")

    # Add ssl options
    add_ssl_options(parser)

    # Add verbosity
    add_verbosity(parser)

    # Now we process the rest of the arguments.
    opt, args = parser.parse_args()

    # Check security settings
    check_password_security(opt, args)

    # option --main is required (mandatory)
    if not opt.main:
        default_val = 'root@localhost:3306'
        print(WARN_OPT_USING_DEFAULT.format(default=default_val,
                                            opt='--main'))
        # Print the WARNING to force determinism if a parser error occurs.
        sys.stdout.flush()

    # option --subordinate is required (mandatory)
    if not opt.subordinate:
        parser.error(PARSE_ERR_OPTS_REQ.format(opt='--subordinate'))

    # option --rpl-user is required (mandatory)
    if not opt.rpl_user:
        parser.error(PARSE_ERR_OPTS_REQ.format(opt="--rpl-user"))

    # Parse source connection values
    try:
        m_values = parse_connection(opt.main, None, opt)
    except FormatError:
        _, err, _ = sys.exc_info()
        parser.error("Main connection values invalid: {0}.".format(err))
    except UtilError:
        _, err, _ = sys.exc_info()
        parser.error("Main connection values invalid: {0}."
                     "".format(err.errmsg))

    # Parse source connection values
    try:
        s_values = parse_connection(opt.subordinate, None, opt)
    except FormatError:
        _, err, _ = sys.exc_info()
        parser.error("Subordinate connection values invalid: %s." % err)
    except UtilError:
        _, err, _ = sys.exc_info()
        parser.error("Subordinate connection values invalid: %s." % err.errmsg)

    # Check hostname alias
    if check_hostname_alias(m_values, s_values):
        parser.error("The main and subordinate are the same host and port.")

    # Check required --main-log-file for --main-log-pos
    if opt.main_log_pos >= 0 and opt.main_log_file is None:
        parser.error("You must specify a main log file to use the main "
                     "log file position option.")

    if ((opt.main_log_pos >= 0 or (opt.main_log_file is not None)) and
            opt.from_beginning):
        parser.error("The --start-from-beginning option is not valid in "
                     "combination with --main-log-file or --main-log-pos.")

    # Create dictionary of options
    options = {
        'verbosity': opt.verbosity,
        'pedantic': opt.pedantic,
        'quiet': False,
        'main_log_file': opt.main_log_file,
        'main_log_pos': opt.main_log_pos,
        'from_beginning': opt.from_beginning,
    }

    # Add ssl Values to options instead of connection.
    options.update(get_ssl_dict(opt))

    try:
        setup_replication(m_values, s_values, opt.rpl_user, options,
                          opt.test_db)
    except UtilError:
        _, e, _ = sys.exc_info()
        print("ERROR: {0}".format(e.errmsg))
        sys.exit(1)

    sys.exit()
