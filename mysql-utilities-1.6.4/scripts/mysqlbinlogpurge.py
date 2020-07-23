#!/usr/bin/env python
#
# Copyright (c) 2014, 2015, Oracle and/or its affiliates. All rights reserved.
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
This file contains the purge binlog utility. It is used to purge binlog on
demand or by schedule and standalone or on a establish replication topology.
"""

from mysql.utilities.common.tools import check_python_version

# Check Python version compatibility
check_python_version()

import os
import sys

from mysql.utilities.command.binlog_admin import binlog_purge
from mysql.utilities.common.messages import (ERROR_MASTER_IN_SLAVES,
                                             PARSE_ERR_OPT_REQ_OPT,
                                             PARSE_ERR_OPTS_EXCLD,
                                             PARSE_ERR_OPTS_REQ)
from mysql.utilities.common.ip_parser import parse_connection
from mysql.utilities.common.options import (add_verbosity,
                                            add_discover_subordinates_option,
                                            add_main_option,
                                            add_subordinates_option,
                                            check_server_lists, get_ssl_dict,
                                            setup_common_options)
from mysql.utilities.common.server import check_hostname_alias
from mysql.utilities.common.tools import check_connector_python
from mysql.utilities.common.topology import parse_topology_connections
from mysql.utilities.exception import UtilError, UtilRplError, FormatError

# Check for connector/python
if not check_connector_python():
    sys.exit(1)

# Constants
NAME = "MySQL Utilities - mysqlbinlogpurge "
DESCRIPTION = "mysqlbinlogpurge - purges unnecessary binary log files"
USAGE = (
    "%prog --main=user:pass@host:port "
    "--subordinates=user:pass@host:port,user:pass@host:port"
)
DATE_FORMAT = '%Y-%m-%d %H:%M:%S %p'
EXTENDED_HELP = """
Introduction
------------
The mysqlbinlogpurge utility was designed to purge binary log files in a
replication scenario operating in a safe manner by prohibiting deletion of
binary log files that are open or which are required by a subordinate (have not
been read by the subordinate). The utility verifies the latest binary log file that
has been read by all the subordinate servers to determine the binary log files that
can be deleted.

Note: In order to determine the latest binary log file that has been
replicated by all the subordinates, they must be connected to the main at the time
the utility is executed.

The following are examples of use:
  # Purge all the binary log files prior to a specified file for a standalone
  # server.
  $ mysqlbinlogpurge --server=root:pass@host1:3306 \\
                     --binlog=bin-log.001302

  # Display the latest binary log that has been replicated by all specified
  # subordinates in a replication scenario.
  $ mysqlbinlogpurge --main=root:pass@host2:3306 \\
                     --subordinates=root:pass@host3:3308,root:pass@host3:3309 \\
                     --dry-run
"""

if __name__ == '__main__':
    # Setup the command parser
    parser = setup_common_options(os.path.basename(sys.argv[0]),
                                  DESCRIPTION, USAGE, False, True,
                                  server_default=None,
                                  extended_help=EXTENDED_HELP, add_ssl=True)

    # Do not Purge binlog, instead print info
    parser.add_option("-d", "--dry-run", action="store_true", dest="dry_run",
                      help="run the utility without purge any binary log, "
                      "instead it will print the unused binary log files.")

    # Add Binlog index binlog_name
    parser.add_option("--binlog", action="store",
                      dest="binlog", default=None, type="string",
                      help="Binlog file name to keep (not to purge). All the "
                      "binary log files prior to the specified file will be "
                      "removed.")

    # Add the --discover-subordinates-login option.
    add_discover_subordinates_option(parser)

    # Add the --main option.
    add_main_option(parser)

    # Add the --subordinates option.
    add_subordinates_option(parser)

    # Add verbosity
    add_verbosity(parser, quiet=False)

    # Now we process the rest of the arguments.
    opt, args = parser.parse_args()

    server_val = None
    main_val = None
    subordinates_val = None

    if opt.server and opt.main:
        parser.error(PARSE_ERR_OPTS_EXCLD.format(opt1="--server",
                                                 opt2="--main"))

    if opt.main is None and opt.subordinates:
        parser.error(PARSE_ERR_OPT_REQ_OPT.format(opt="--subordinates",
                                                  opts="--main"))

    if opt.main is None and opt.discover:
        parser.error(
            PARSE_ERR_OPT_REQ_OPT.format(opt="--discover-subordinates-login",
                                         opts="--main")
        )

    if opt.main and opt.subordinates is None and opt.discover is None:
        err_msg = PARSE_ERR_OPT_REQ_OPT.format(
            opt="--main",
            opts="--subordinates or --discover-subordinates-login",
        )
        parser.error(err_msg)

    # Check mandatory options: --server or --main.
    if not opt.server and not opt.main:
        parser.error(PARSE_ERR_OPTS_REQ.format(
            opt="--server' or '--main"))

    # Check subordinates list (main cannot be included in subordinates list).
    if opt.main:
        check_server_lists(parser, opt.main, opt.subordinates)

        # Parse the main and subordinates connection parameters (no candidates).
        try:
            main_val, subordinates_val, _ = parse_topology_connections(
                opt, parse_candidates=False
            )
        except UtilRplError:
            _, err, _ = sys.exc_info()
            parser.error("ERROR: {0}\n".format(err.errmsg))
            sys.exit(1)

        # Check host aliases (main cannot be included in subordinates list).
        if main_val:
            for subordinate_val in subordinates_val:
                if check_hostname_alias(main_val, subordinate_val):
                    err = ERROR_MASTER_IN_SLAVES.format(
                        main_host=main_val['host'],
                        main_port=main_val['port'],
                        subordinates_candidates="subordinates",
                        subordinate_host=subordinate_val['host'],
                        subordinate_port=subordinate_val['port'],
                    )
                    parser.error(err)

    # Parse source connection values of --server
    if opt.server:
        try:
            ssl_opts = get_ssl_dict(opt)
            server_val = parse_connection(opt.server, None, ssl_opts)
        except FormatError:
            _, err, _ = sys.exc_info()
            parser.error("ERROR: {0}\n".format(err.errmsg))
        except UtilError as err:
            _, err, _ = sys.exc_info()
            parser.error("ERROR: {0}\n".format(err.errmsg))

    # Create dictionary of options
    options = {
        'discover': opt.discover,
        'verbosity': 0 if opt.verbosity is None else opt.verbosity,
        'to_binlog_name': opt.binlog,
        'dry_run': opt.dry_run,
    }

    try:
        binlog_purge(server_val, main_val, subordinates_val, options)
    except UtilError:
        _, e, _ = sys.exc_info()
        errmsg = e.errmsg.strip(" ")
        sys.stderr.write("ERROR: {0}\n".format(errmsg))
        sys.exit(1)
    sys.exit(0)
