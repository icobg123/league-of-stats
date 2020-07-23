#!/usr/bin/env python
#
# Copyright (c) 2014, 2016, Oracle and/or its affiliates. All rights reserved.
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
This file contains the replication synchronization checker utility. It is used
to check the data consistency between main and subordinates (and synchronize the
data if requested by the user).
"""

from mysql.utilities.common.tools import check_python_version

# Check Python version compatibility
check_python_version()

import os
import sys

from mysql.utilities.command.rpl_sync_check import check_data_consistency
from mysql.utilities.common.messages import (
    ERROR_MASTER_IN_SLAVES, PARSE_ERR_OPT_REQ_OPT,
    PARSE_ERR_OPT_REQ_NON_NEGATIVE_VALUE, PARSE_ERR_OPT_REQ_GREATER_VALUE,
    PARSE_ERR_OPT_REQ_VALUE, PARSE_ERR_OPTS_EXCLD,
    PARSE_ERR_SLAVE_DISCO_REQ
)
from mysql.utilities.common.options import (add_discover_subordinates_option,
                                            add_main_option,
                                            add_subordinates_option,
                                            add_ssl_options, add_verbosity,
                                            check_server_lists,
                                            db_objects_list_to_dictionary,
                                            setup_common_options,
                                            check_password_security)
from mysql.utilities.common.server import (check_hostname_alias,
                                           connect_servers, Server)
from mysql.utilities.common.tools import check_connector_python
from mysql.utilities.common.topology import parse_topology_connections
from mysql.utilities.exception import UtilError, UtilRplError

# Check for connector/python
if not check_connector_python():
    sys.exit(1)

# Constants
NAME = "MySQL Utilities - mysqlrplsync"
DESCRIPTION = "mysqlrplsync - replication synchronization checker utility"
USAGE = ("%prog --main=user:pass@host:port --subordinates=user:pass@host:port \\\n"
         "                    [<db_name>[.<tbl_name>]]")
EXTENDED_HELP = """
Introduction
------------
The mysqlrplsync utility is designed to check if replication servers with
GTIDs enabled are synchronized. In other words, it checks the data consistency
between a main and a subordinate or between two subordinates.

The utility permits you to run the check while replication is active. The
synchronization algorithm is applied using GTID information to identify those
transactions that differ (missing, not read, etc.) between the servers. During
the process, the utility waits for the subordinate to catch up to the main to
ensure all GTIDs have been read prior to performing the data consistency
check.

Note: if replication is not running (e.g., all subordinates are stopped), the
utility can still perform the check, but the step to wait for the subordinate to
catch up to the main will be skipped. If you want to run the utility on a
stopped replication topology, you should ensure the subordinates are up to date
first.

By default, all data is included in the comparison. To check specific
databases or tables, list each element as a separated argument for the
utility using full qualified names as shown in the following examples.

  # Check the data consistency of a replication topology, explicitly
  # specifying the main and subordinates.

  $ mysqlrplsync --main=root:pass@host1:3306 \\
                 --subordinates=rpl:pass@host2:3306,rpl:pass@host3:3306

  # Check the data consistency of a replication topology, specifying the
  # main and using the subordinates discovery feature.

  $ mysqlrplsync --main=root:pass@host1:3306 \\
                 --discover-subordinates-login=rpl:pass

  # Check the data consistency only between specific subordinates (no check
  # performed on the main).

  $ mysqlrplsync --subordinates=rpl:pass@host2:3306,rpl:pass@host3:3306

  # Check the data consistency of a specific database (db1) and table
  # (db2.t1), explicitly specifying main and subordinates.

  $ mysqlrplsync --main=root:pass@host1:3306 \\
                 --subordinates=rpl:pass@host2:3306,rpl:pass@host3:3306 \\
                 db1 db2.t1

  # Check the data consistency of all data excluding a specific database
  # (db2) and table (db1.t2), specifying the main and using subordinate
  # discovery.

  $ mysqlrplsync --main=root:pass@host1:3306 \\
                 --discover-subordinates-login=rpl:pass --exclude=db2,db1.t2


Helpful Hints
-------------
  - The default timeout for performing the table checksum is 5 seconds.
    This value can be changed with the --checksum-timeout option.

  - The default timeout for waiting for subordinates to catch up is 300 seconds.
    This value can be changed with the --rpl-timeout option.

  - The default interval to periodically verify if a subordinate has read all of
    the GTIDs from the main is 3 seconds. This value can be changed
    with the --interval option.

"""

if __name__ == '__main__':
    # Setup the command parser (with common options).
    parser = setup_common_options(os.path.basename(sys.argv[0]),
                                  DESCRIPTION, USAGE, server=False,
                                  extended_help=EXTENDED_HELP)

    # Add the --discover-subordinates-login option.
    add_discover_subordinates_option(parser)

    # Add the --main option.
    add_main_option(parser)

    # Add the --subordinates option.
    add_subordinates_option(parser)

    # Add the --ssl options
    add_ssl_options(parser)

    # Add verbosity option (no --quite option).
    add_verbosity(parser, False)

    # Add timeout options.
    parser.add_option("--rpl-timeout", action="store", dest="rpl_timeout",
                      type="int", default=300,
                      help="maximum timeout in seconds to wait for "
                           "synchronization (subordinate waiting to catch up to "
                           "main). Default = 300.")
    parser.add_option("--checksum-timeout", action="store",
                      dest="checksum_timeout", type="int", default=5,
                      help="maximum timeout in seconds to wait for CHECKSUM "
                           "query to complete. Default = 5.")

    # Add polling interval option.
    parser.add_option("--interval", "-i", action="store", dest="interval",
                      type="int", default="3", help="interval in seconds for "
                      "polling subordinates for sync status. Default = 3.")

    # Add option to exclude databases/tables check.
    parser.add_option("--exclude", action="store", dest="exclude",
                      type="string", default=None,
                      help="databases or tables to exclude. Example: "
                           "<db_name>[.<tbl_name>]. List multiple names in a "
                           "comma-separated list.")

    # Parse the options and arguments.
    opt, args = parser.parse_args()

    # Check security settings
    check_password_security(opt, args)

    # At least one of the options --discover-subordinates-login or --subordinates is
    # required.
    if not opt.discover and not opt.subordinates:
        parser.error(PARSE_ERR_SLAVE_DISCO_REQ)

    # The --discover-subordinates-login and --subordinates options cannot be used
    # simultaneously (only one).
    if opt.discover and opt.subordinates:
        parser.error(PARSE_ERR_OPTS_EXCLD.format(
            opt1='--discover-subordinates-login', opt2='--subordinates'
        ))

    if opt.discover and not opt.main:
        parser.error(PARSE_ERR_OPT_REQ_OPT.format(
            opt="--discover-subordinates-login",
            opts="--main"
        ))

    # Check timeout values, must be greater than zero.
    if opt.rpl_timeout < 0:
        parser.error(
            PARSE_ERR_OPT_REQ_NON_NEGATIVE_VALUE.format(opt='--rpl-timeout')
        )
    if opt.checksum_timeout < 0:
        parser.error(
            PARSE_ERR_OPT_REQ_NON_NEGATIVE_VALUE.format(
                opt='--checksum-timeout'
            )
        )

    # Check interval value, must be greater than zero.
    if opt.interval < 1:
        parser.error(PARSE_ERR_OPT_REQ_GREATER_VALUE.format(opt='--interval',
                                                            val='zero'))

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
        sys.stderr.write("ERROR: {0}\n".format(err.errmsg))
        sys.exit(1)

    # Check host aliases (main cannot be included in subordinates list).
    if main_val:
        for subordinate_val in subordinates_val:
            if check_hostname_alias(main_val, subordinate_val):
                main = Server({'conn_info': main_val})
                subordinate = Server({'conn_info': subordinate_val})
                parser.error(
                    ERROR_MASTER_IN_SLAVES.format(main_host=main.host,
                                                  main_port=main.port,
                                                  subordinates_candidates="subordinates",
                                                  subordinate_host=subordinate.host,
                                                  subordinate_port=subordinate.port)
                )
        # Get the sql_mode set in main
        conn_opts = {
            'quiet': True,
            'version': "5.1.30",
        }
        try:
            servers = connect_servers(main_val, None, conn_opts)
            sql_mode = servers[0].select_variable("SQL_MODE")
        except UtilError:
            sql_mode = ''
    else:
        sql_mode = ''

    # Process list of databases/tables to exclude (check format errors).
    data_to_exclude = {}
    if opt.exclude:
        exclude_list = [val for val in opt.exclude.split(',') if val]
        data_to_exclude = db_objects_list_to_dictionary(parser, exclude_list,
                                                        'the --exclude option',
                                                        sql_mode=sql_mode)
    elif opt.exclude == '':
        # Issue an error if --exclude is used with no value.
        parser.error(PARSE_ERR_OPT_REQ_VALUE.format(opt='--exclude'))

    # Process list of databases/tables to include (check format errors).
    data_to_include = {}
    if args:
        data_to_include = db_objects_list_to_dictionary(parser, args,
                                                        'the database/table '
                                                        'arguments',
                                                        sql_mode=sql_mode)

    # Create dictionary of options
    options = {
        'discover': opt.discover,
        'verbosity': 0 if opt.verbosity is None else opt.verbosity,
        'rpl_timeout': opt.rpl_timeout,
        'checksum_timeout': opt.checksum_timeout,
        'interval': opt.interval,
    }

    # Create a replication synchronizer and check the topology's consistency.
    issues_found = 0
    try:
        issues_found = check_data_consistency(main_val, subordinates_val, options,
                                              data_to_include, data_to_exclude)
    except UtilError:
        _, err, _ = sys.exc_info()
        sys.stderr.write("ERROR: {0}\n".format(err.errmsg))
        sys.exit(1)

    # Exit with the appropriate status.
    if issues_found == 0:
        sys.exit(0)
    else:
        sys.exit(1)
