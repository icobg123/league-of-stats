#
# Copyright (c) 2010, 2016, Oracle and/or its affiliates. All rights reserved.
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
This module contains abstractions of MySQL replication functionality.
"""

import os
import time
import StringIO
import socket

from mysql.utilities.exception import UtilError, UtilRplWarn, UtilRplError
from mysql.utilities.exception import FormatError
from mysql.utilities.common.options import parse_user_password
from mysql.utilities.common.server import Server
from mysql.utilities.common.user import User
from mysql.utilities.common.ip_parser import clean_IPv6, format_IPv6
from mysql.utilities.common.format import format_tabular_list
from mysql.utilities.common.messages import USER_PASSWORD_FORMAT


_MASTER_INFO_COL = [
    'Main_Log_File', 'Read_Main_Log_Pos', 'Main_Host', 'Main_User',
    'Main_Password', 'Main_Port', 'Connect_Retry', 'Main_SSL_Allowed',
    'Main_SSL_CA_File', 'Main_SSL_CA_Path', 'Main_SSL_Cert',
    'Main_SSL_Cipher', 'Main_SSL_Key', 'Main_SSL_Verify_Server_Cert',
    'Heartbeat', 'Bind', 'Ignored_server_ids', 'Uuid', 'Retry_count',
    'SSL_CRL', 'SSL_CRL_Path', 'Enabled_auto_position', 'Channel_Name',
]

_SLAVE_IO_STATE, _SLAVE_MASTER_HOST, _SLAVE_MASTER_USER, _SLAVE_MASTER_PORT, \
    _SLAVE_MASTER_LOG_FILE, _SLAVE_MASTER_LOG_FILE_POS, _SLAVE_IO_RUNNING, \
    _SLAVE_SQL_RUNNING, _SLAVE_DO_DB, _SLAVE_IGNORE_DB, _SLAVE_DO_TABLE, \
    _SLAVE_IGNORE_TABLE, _SLAVE_WILD_DO_TABLE, _SLAVE_WILD_IGNORE_TABLE, \
    _SLAVE_DELAY, _SLAVE_REMAINING_DELAY, _SLAVE_IO_ERRORNO, _SLAVE_IO_ERROR, \
    _SLAVE_SQL_ERRORNO, _SLAVE_SQL_ERROR, _MASTER_UUID, _RETRIEVED_GTID_SET, \
    _EXECUTED_GTID_SET = \
    0, 1, 2, 3, 5, 6, 10, 11, 12, 13, 14, 15, 16, 17, 32, 33, 34, 35, 36, 37,\
    40, 51, 52

_PRINT_WIDTH = 75

_MASTER_DO_DB, _MASTER_IGNORE_DB = 2, 3

_RPL_USER_QUERY = """
    SELECT user, host, password = '' as has_password
    FROM mysql.user
    WHERE repl_subordinate_priv = 'Y'
"""
# Query for server versions >= 5.7.6.
_RPL_USER_QUERY_5_7_6 = """
    SELECT user, host, authentication_string = '' as has_password
    FROM mysql.user
    WHERE repl_subordinate_priv = 'Y'
"""

_WARNING = "# WARNING: %s"
_MASTER_BINLOG = "Server '%s' does not have binary logging turned on."
_NO_RPL_USER = "No --rpl-user specified and multiple users found with " + \
               "replication privileges."
_RPL_USER_PASS = "No --rpl-user specified and the user found with " + \
                 "replication privileges requires a password."

_GTID_EXECUTED = "SELECT @@GLOBAL.GTID_EXECUTED"
_GTID_WAIT = "SELECT WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS('%s', %s)"


def _get_list(rows, cols):
    """Return a list of information in GRID format to stdout.

    rows[in]          rows of data
    cols[in]          column headings

    Returns list of strings
    """
    ostream = StringIO.StringIO()
    format_tabular_list(ostream, cols, rows)
    return ostream.getvalue().splitlines()


def negotiate_rpl_connection(server, is_main=True, strict=True,
                             options=None):
    """Determine replication connection

    This method attempts to determine if it is possible to build a CHANGE
    MASTER command based on the server passed. If it is possible, the method
    will return a CHANGE MASTER command. If there are errors and the strict
    option is turned on, it will throw errors if there is something missing.
    Otherwise, it will return the CHANGE MASTER command with warnings.

    If the server is a main, the following error checks will be performed.

      - if binary log is turned OFF, and strict = False, a warning message
        is added to the strings returned else an error is thrown

      - if the rpl_user option is missing, the method attempts to find a
        replication user. If more than one user is found or none are found, and
        strict = False, a warning message is added to the strings returned else
        an error is thrown

      - if a replication user is found but the user requires a password,
        the MASTER_USER and MASTER_PASSWORD options are commented out

    Note: the CHANGE MASTER command is formatted whereby each option is
          separated by a newline and indented two spaces

    Note: the make_change_main method does not support SSL connections

    server[in]        a Server class instance
    is_main[in]     if True, the server is acting as a main
                      Default = True
    strict[in]        if True, raise exception on errors
                      Default = True
    options[in]       replication options including rpl_user, quiet, multiline

    Returns list - strings containing the CHANGE MASTER command
    """
    if options is None:
        options = {}

    rpl_mode = options.get("rpl_mode", "main")
    rpl_user = options.get("rpl_user", None)
    quiet = options.get("quiet", False)

    # Copy options and add connected server
    new_opts = options.copy()
    new_opts["conn_info"] = server

    uname = None
    main_values = {}
    change_main = []

    # If server is a main, perform error checking
    if is_main:
        main = Main(new_opts)
        main.connect()

        # Check main for binlog
        if not main.binlog_enabled():
            raise UtilError("Main must have binary logging turned on.")
        else:
            # Check rpl user
            if rpl_user is None:
                # Try to find the replication user
                res = main.get_rpl_users()
                if len(res) > 1:
                    uname = ""
                    passwd = ""
                    # Throw error if strict but not for rpl_mode = both
                    if strict and not rpl_mode == 'both':
                        raise UtilRplError(_NO_RPL_USER)
                    else:
                        change_main.append(_WARNING % _NO_RPL_USER)
                else:
                    uname = res[0][0]
                    if res[0][2]:
                        # Throw error if strict but not for rpl_mode = both
                        if strict and not rpl_mode == 'both':
                            raise UtilRplError(_RPL_USER_PASS)
                        else:
                            change_main.append(_WARNING % _RPL_USER_PASS)
                    passwd = res[0][1]
            else:
                # Parse username and password (supports login-paths)
                try:
                    uname, passwd = parse_user_password(rpl_user, options=options)
                except FormatError as fmt_err:
                    raise UtilError (USER_PASSWORD_FORMAT.format("--rpl-user"))
                if not passwd:
                    passwd = ''

                # Check replication user privileges
                errors = main.check_rpl_user(uname, main.host)
                if errors != []:
                    raise UtilError(errors[0])

            res = main.get_status()
            if not res:
                raise UtilError("Cannot retrieve main status.")

            # Need to get the main values for the make_change_main command
            main_values = {
                'Main_Host': main.host,
                'Main_Port': main.port,
                'Main_User': uname,
                'Main_Password': passwd,
                'Main_Log_File': res[0][0],
                'Read_Main_Log_Pos': res[0][1],
            }

            if main.has_ssl:
                main_values['Main_SSL_Allowed'] = 1
                if main.ssl_ca:
                    main_values['Main_SSL_CA_File'] = main.ssl_ca
                if main.ssl_cert:
                    main_values['Main_SSL_Cert'] = main.ssl_cert
                if main.ssl_key:
                    main_values['Main_SSL_Key'] = main.ssl_key

    # Use subordinate class to get change main command
    subordinate = Subordinate(new_opts)
    subordinate.connect()
    cm_cmd = subordinate.make_change_main(False, main_values)

    if rpl_user is None and uname == "" and not quiet:
        cm_cmd = cm_cmd.replace("MASTER_PORT", "# MASTER_USER = '', "
                                "# MASTER_PASSWORD = '', MASTER_PORT")

    if options.get("multiline", False):
        cm_cmd = cm_cmd.replace(", ", ", \n  ") + ";"
        change_main.extend(cm_cmd.split("\n"))
    else:
        change_main.append(cm_cmd + ";")

    return change_main


class Replication(object):
    """
    The Replication class can be used to establish a replication connection
    between a main and a subordinate with the following utilities:

        - Create the replication user
        - Setup replication
        - Test prerequisites for replication
        - Conduct validation checks:
            - binlog
            - server ids
            - storage engine compatibility
            - innodb version compatibility
            - main binlog
            - lower case table name compatibility
            - subordinate connection to main
            - subordinate delay

    Replication prerequisite tests shall be constructed so that they return
    None if the check passes (no errors) or a list of strings containing the
    errors or warnings. They shall accept a dictionary of options set to
    options={}. This will allow for reduced code needed to call multiple tests.
    """

    def __init__(self, main, subordinate, options):
        """Constructor

        main[in]         Main Server object
        subordinate[in]          Subordinate Server object
        options[in]        Options for class
          verbose          print extra data during operations (optional)
                           default value = False
          main_log_file  main log file
                           default value = None
          main_log_pos   position in log file
                           default = -1 (no position specified)
          from_beginning   if True, start from beginning of logged events
                           default = False
        """
        self.verbosity = options.get("verbosity", 0)
        self.main_log_file = options.get("main_log_file", None)
        self.main_log_pos = options.get("main_log_pos", 0)
        self.from_beginning = options.get("from_beginning", False)
        self.ssl_ca = options.get("ssl_ca", None)
        self.ssl_cert = options.get("ssl_cert", None)
        self.ssl_key = options.get("ssl_key", None)
        self.ssl_opt = options.get("ssl", None)
        self.ssl = False
        if self.ssl_ca or self.ssl_cert or self.ssl_key or self.ssl_opt:
            self.ssl = True
        self.main = main
        self.subordinate = subordinate
        self.replicating = False
        self.query_options = {
            'fetch': False
        }

    def check_server_ids(self):
        """Check server ids on main and subordinate

        This method will check the server ids on the main and subordinate. It will
        raise exceptions for error conditions.

        Returns [] if compatible, list of errors if not compatible
        """
        main_server_id = self.main.get_server_id()
        subordinate_server_id = self.subordinate.get_server_id()
        if main_server_id == 0:
            raise UtilRplError("Main server_id is set to 0.")

        if subordinate_server_id == 0:
            raise UtilRplError("Subordinate server_id is set to 0.")

        # Check for server_id uniqueness
        if main_server_id == subordinate_server_id:
            raise UtilRplError("The subordinate's server_id is the same as the "
                               "main.")

        return []

    def check_server_uuids(self):
        """Check UUIDs on main and subordinate

        This method will check the UUIDs on the main and subordinate. It will
        raise exceptions for error conditions.

        Returns [] if compatible or no UUIDs used, list of errors if not
        """
        main_uuid = self.main.get_uuid()
        subordinate_uuid = self.subordinate.get_uuid()

        # Check for both not supporting UUIDs.
        if main_uuid is None and subordinate_uuid is None:
            return []

        # Check for unbalanced servers - one with UUID, one without
        if main_uuid is None or subordinate_uuid is None:
            raise UtilRplError("%s does not support UUIDs." %
                               "Main" if main_uuid is None else "Subordinate")

        # Check for uuid uniqueness
        if main_uuid == subordinate_uuid:
            raise UtilRplError("The subordinate's UUID is the same as the "
                               "main.")

        return []

    def check_innodb_compatibility(self, options):
        """Check InnoDB compatibility

        This method checks the main and subordinate to ensure they have compatible
        installations of InnoDB. It will print the InnoDB settings on the
        main and subordinate if quiet is not set. If pedantic is set, method
        will raise an error.

        options[in]   dictionary of options (verbose, pedantic)

        Returns [] if compatible, list of errors if not compatible
        """

        pedantic = options.get("pedantic", False)
        verbose = options.get("verbosity", 0) > 0

        errors = []

        main_innodb_stats = self.main.get_innodb_stats()
        subordinate_innodb_stats = self.subordinate.get_innodb_stats()

        if main_innodb_stats != subordinate_innodb_stats:
            if not pedantic:
                errors.append("WARNING: Innodb settings differ between main "
                              "and subordinate.")
            if verbose or pedantic:
                cols = ['type', 'plugin_version', 'plugin_type_version',
                        'have_innodb']
                rows = []
                rows.append(main_innodb_stats)
                errors.append("# Main's InnoDB Stats:")
                errors.extend(_get_list(rows, cols))
                rows = []
                rows.append(subordinate_innodb_stats)
                errors.append("# Subordinate's InnoDB Stats:")
                errors.extend(_get_list(rows, cols))
            if pedantic:
                for line in errors:
                    print line
                raise UtilRplError("Innodb settings differ between main "
                                   "and subordinate.")

        return errors

    def check_storage_engines(self, options):
        """Check compatibility of storage engines on main and subordinate

        This method checks that the main and subordinate have compatible storage
        engines. It will print the InnoDB settings on the main and subordinate if
        quiet is not set. If pedantic is set, method will raise an error.

        options[in]   dictionary of options (verbose, pedantic)

        Returns [] if compatible, list of errors if not compatible
        """

        pedantic = options.get("pedantic", False)
        verbose = options.get("verbosity", 0) > 0

        errors = []
        subordinate_engines = self.subordinate.get_storage_engines()
        results = self.main.check_storage_engines(subordinate_engines)
        if results[0] is not None or results[1] is not None:
            if not pedantic:
                errors.append("WARNING: The main and subordinate have differing "
                              "storage engine configurations!")
            if verbose or pedantic:
                cols = ['engine', 'support']
                if results[0] is not None:
                    errors.append("# Storage engine configuration on Main:")
                    errors.extend(_get_list(results[0], cols))
                if results[1] is not None:
                    errors.append("# Storage engine configuration on Subordinate:")
                    errors.extend(_get_list(results[1], cols))
            if pedantic:
                for line in errors:
                    print line
                raise UtilRplError("The main and subordinate have differing "
                                   "storage engine configurations!")

        return errors

    def check_main_binlog(self):
        """Check prerequisites for main for replication

        Returns [] if main ok, list of errors if binary logging turned off.
        """
        errors = []
        if not self.main.binlog_enabled():
            errors.append("Main must have binary logging turned on.")
        return errors

    def check_lctn(self):
        """Check lower_case_table_name setting

        Returns [] - no exceptions, list if exceptions found
        """
        errors = []
        subordinate_lctn = self.subordinate.get_lctn()
        main_lctn = self.main.get_lctn()
        if subordinate_lctn != main_lctn:
            return (main_lctn, subordinate_lctn)
        if subordinate_lctn == 1:
            msg = "WARNING: identifiers can have inconsistent case " + \
                  "when lower_case_table_names = 1 on the subordinate and " + \
                  "the main has a different value."
            errors.append(msg)

        return errors

    def get_binlog_exceptions(self):
        """Get any binary logging exceptions

        This method queries the main and subordinate status for the *-do-db and
        *-ignore-db settings. It returns the values of either of these for
        the main and subordinate.

        Returns [] - no exceptions, list if exceptions found
        """
        binlog_ex = []
        rows = []
        rows.extend(self.main.get_binlog_exceptions())
        rows.extend(self.subordinate.get_binlog_exceptions())
        if len(rows) > 0:
            cols = ['server', 'do_db', 'ignore_db']
            binlog_ex = _get_list(rows, cols)

        return binlog_ex

    def check_subordinate_connection(self):
        """Check to see if subordinate is connected to main

        This method will check the subordinate specified at instantiation to see if
        it is connected to the main specified. If the subordinate is connected
        to a different main, an error is returned. It will also raise an
        exception if the subordinate is stopped or if the server is not setup as a
        subordinate.

        Returns bool - True = subordinate connected to main
        """
        state = self.subordinate.get_io_running()
        if not state:
            raise UtilRplError("Subordinate is stopped.")
        if not self.subordinate.is_configured_for_main(self.main) or \
           state.upper() != "YES":
            return False
        return True

    def check_subordinate_delay(self):
        """Check to see if subordinate is behind main.

        This method checks subordinate_behind_main returning None if 0 or a
        message containing the value if non-zero. Also includes the subordinate's
        position as related to the main.

        Returns [] - no exceptions, list if exceptions found
        """
        m_log_file = None
        m_log_pos = 0
        errors = []
        res = self.main.get_status()
        if res != []:
            m_log_file = res[0][0]       # main's binlog file
            m_log_pos = res[0][1]        # main's binlog position
        else:
            raise UtilRplError("Cannot read main status.")
        delay_info = self.subordinate.get_delay()
        if delay_info is None:
            raise UtilRplError("The server specified as the subordinate is "
                               "not configured as a replication subordinate.")

        state, sec_behind, delay_remaining, \
            read_log_file, read_log_pos = delay_info

        if not state:
            raise UtilRplError("Subordinate is stopped.")
        if delay_remaining is None:  # if unknown, return the error
            errors.append("Cannot determine subordinate delay. Status: UNKNOWN.")
            return errors

        if sec_behind == 0:
            if m_log_file is not None and \
               (read_log_file != m_log_file or read_log_pos != m_log_pos):
                errors.append("Subordinate is behind main.")
                errors.append("Main binary log file = %s" % m_log_file)
                errors.append("Main binary log position = %s" % m_log_pos)
                errors.append("Subordinate is reading main binary log "
                              "file = %s" % read_log_file)
                errors.append("Subordinate is reading main binary log "
                              "position = %s" % read_log_pos)
            else:
                return errors
        else:
            errors.append("Subordinate is % seconds behind main." %
                          sec_behind)

        return errors

    def create_rpl_user(self, r_user, r_pass=None):
        """Create the replication user and grant privileges

        If the user exists, check privileges and add privileges as needed.
        Calls Main class method to execute.

        r_user[in]     user to create
        r_pass[in]     password for user to create (optional)

        Returns bool - True = success, False = errors
        """
        ssl = False
        if self.ssl:
            ssl = True
        return self.main.create_rpl_user(self.subordinate.host, self.subordinate.port,
                                           r_user, r_pass, self.verbosity, ssl)

    def setup(self, rpl_user, num_tries):
        """Setup replication among a subordinate and main.

        Note: Must have connected to a main and subordinate before calling this
        method.

        rpl_user[in]       Replication user in form user:passwd
        num_tries[in]      Number of attempts to wait for subordinate synch

        Returns True if success, False if error
        """
        if self.main is None or self.subordinate is None:
            print "ERROR: Must connect to main and subordinate before " \
                  "calling replicate()"
            return False

        result = True

        # Parse user and password (support login-paths)
        try:
            r_user, r_pass = parse_user_password(rpl_user)
        except FormatError as fmt_err:
            raise UtilError (USER_PASSWORD_FORMAT.format("--rpl-user"))

        # Check to see if rpl_user is present, else create her
        if not self.create_rpl_user(r_user, r_pass)[0]:
            return False

        # Read main log file information
        res = self.main.get_status()
        if not res:
            print "ERROR: Cannot retrieve main status."
            return False

        # If main log file, pos not specified, read main log file info
        read_main_info = False
        if self.main_log_file is None:
            res = self.main.get_status()
            if not res:
                print "ERROR: Cannot retrieve main status."
                return False

            read_main_info = True
            self.main_log_file = res[0][0]
            self.main_log_pos = res[0][1]
        else:
            # Check to make sure file is accessible and valid
            found = False
            res = self.main.get_binary_logs(self.query_options)
            for row in res:
                if row[0] == self.main_log_file:
                    found = True
                    break
            if not found:
                raise UtilError("Main binary log file not listed as a "
                                "valid binary log file on the main.")

        if self.main_log_file is None:
            raise UtilError("No main log file specified.")

        # Stop subordinate first
        res = self.subordinate.get_thread_status()
        if res is not None:
            if res[1] == "Yes" or res[2] == "Yes":
                res = self.subordinate.stop(self.query_options)

        # Connect subordinate to main
        if self.verbosity > 0:
            print "# Connecting subordinate to main..."
        main_values = {
            'Main_Host': self.main.host,
            'Main_Port': self.main.port,
            'Main_User': r_user,
            'Main_Password': r_pass,
            'Main_Log_File': self.main_log_file,
            'Read_Main_Log_Pos': self.main_log_pos,
        }

        # Use the options SSL certificates if defined,
        # else use the main SSL certificates if defined.
        if self.ssl:
            main_values['Main_SSL_Allowed'] = 1
            if self.ssl_ca:
                main_values['Main_SSL_CA_File'] = self.ssl_ca
            if self.ssl_cert:
                main_values['Main_SSL_Cert'] = self.ssl_cert
            if self.ssl_key:
                main_values['Main_SSL_Key'] = self.ssl_key

        elif self.main.has_ssl:
            main_values['Main_SSL_Allowed'] = 1
            main_values['Main_SSL_CA_File'] = self.main.ssl_ca
            main_values['Main_SSL_Cert'] = self.main.ssl_cert
            main_values['Main_SSL_Key'] = self.main.ssl_key

        change_main = self.subordinate.make_change_main(self.from_beginning,
                                                      main_values)
        res = self.subordinate.exec_query(change_main, self.query_options)
        if self.verbosity > 0:
            print "# %s" % change_main

        # Start subordinate
        if self.verbosity > 0:
            if not self.from_beginning:
                if read_main_info:
                    print "# Starting subordinate from main's last position..."
                else:
                    msg = "# Starting subordinate from main log file '%s'" % \
                          self.main_log_file
                    if self.main_log_pos >= 0:
                        msg += " using position %s" % self.main_log_pos
                    msg += "..."
                    print msg
            else:
                print "# Starting subordinate from the beginning..."
        res = self.subordinate.start(self.query_options)

        # Add commit because C/Py are auto_commit=0 by default
        self.subordinate.exec_query("COMMIT")

        # Check subordinate status
        i = 0
        while i < num_tries:
            time.sleep(1)
            res = self.subordinate.get_subordinates_errors()
            status = res[0]
            sql_running = res[4]
            if self.verbosity > 0:
                io_errorno = res[1]
                io_error = res[2]
                io_running = res[3]
                sql_errorno = res[5]
                sql_error = res[6]
                print "# IO status: %s" % status
                print "# IO thread running: %s" % io_running
                # if io_errorno = 0 and error = '' -> no error
                if not io_errorno and not io_error:
                    print "# IO error: None"
                else:
                    print "# IO error: %s:%s" % (io_errorno, io_error)
                # if io_errorno = 0 and error = '' -> no error
                print "# SQL thread running: %s" % sql_running
                if not sql_errorno and not sql_error:
                    print "# SQL error: None"
                else:
                    print "# SQL error: %s:%s" % (io_errorno, io_error)
            if status == "Waiting for main to send event" and sql_running:
                break
            elif not sql_running:
                if self.verbosity > 0:
                    print "# Retry to start the subordinate SQL thread..."
                # SQL thread is not running, retry to start it
                res = self.subordinate.start_sql_thread(self.query_options)
            if self.verbosity > 0:
                print "# Waiting for subordinate to synchronize with main"
            i += 1
        if i == num_tries:
            print "ERROR: failed to sync subordinate with main."
            result = False

        if result is True:
            self.replicating = True

        return result

    def test(self, db, num_tries):
        """Test the replication setup.

        Requires a database name which is created on the main then
        verified it appears on the subordinate.

        db[in]             Name of a database to use in test
        num_tries[in]      Number of attempts to wait for subordinate synch
        """

        if not self.replicating:
            print "ERROR: Replication is not running among main and subordinate."
        print "# Testing replication setup..."
        if self.verbosity > 0:
            print "# Creating a test database on main named %s..." % db
        res = self.main.exec_query("CREATE DATABASE %s" % db,
                                     self.query_options)
        i = 0
        while i < num_tries:
            time.sleep(1)
            res = self.subordinate.exec_query("SHOW DATABASES")
            for row in res:
                if row[0] == db:
                    res = self.main.exec_query("DROP DATABASE %s" % db,
                                                 self.query_options)
                    print "# Success! Replication is running."
                    i = num_tries
                    break
            i += 1
            if i < num_tries and self.verbosity > 0:
                print "# Waiting for subordinate to synchronize with main"
        if i == num_tries:
            print "ERROR: Unable to complete testing."


class Main(Server):
    """The Subordinate class is a subclass of the Server class. It represents a
    MySQL server performing the role of a subordinate in a replication topology.
    The following utilities are provide in addition to the Server utilities:

        - check to see if replication user is defined and has privileges
        - get binary log exceptions
        - get main status
        - reset main

    """

    def __init__(self, options=None):
        """Constructor

        The method accepts one of the following types for options['conn_info']:

            - dictionary containing connection information including:
              (user, passwd, host, port, socket)
            - connection string in the form: user:pass@host:port:socket
            - an instance of the Server class

        options[in]        options for controlling behavior:
            conn_info      a dictionary containing connection information
                           (user, passwd, host, port, socket)
            role           Name or role of server (e.g., server, main)
            verbose        print extra data during operations (optional)
                           default value = False
            charset        Default character set for the connection.
                           (default latin1)
        """
        if options is None:
            options = {}

        assert not options.get("conn_info") is None

        self.options = options
        Server.__init__(self, options)

    def get_status(self):
        """Return the main status

        Returns result set
        """
        return self.exec_query("SHOW MASTER STATUS")

    def get_binlog_exceptions(self):
        """Get any binary logging exceptions

        This method queries the server status for the *-do-db and
        *-ignore-db settings.

        Returns [] - no exceptions, list if exceptions found
        """
        rows = []
        res = self.get_status()
        if res != []:
            do_db = res[0][_MASTER_DO_DB]
            ignore_db = res[0][_MASTER_IGNORE_DB]
            if len(do_db) > 0 or len(ignore_db) > 0:
                rows.append(('main', do_db, ignore_db))

        return rows

    def get_binlog_info(self):
        """Return the main's binary log information (file name and position).

        Returns a tuple with the binary log filename and position, or None if
        the server is not acting as a main.
        """
        res = self.get_status()
        if res:
            # Return binlog_file and binlog_pos.
            return res[0][0], res[0][1]
        else:
            # Status data is empty, server is not acting as a main.
            return None

    def get_rpl_users(self, options=None):
        """Attempts to find the users who have the REPLICATION SLAVE privilege

        options[in]    query options

        Returns tuple list - (string, string, bool) = (user, host,
                                                       has_password)
        """
        if options is None:
            options = {}
        # Use the correct query for server (changed for 5.7.6).
        if self.check_version_compat(5, 7, 6):
            query = _RPL_USER_QUERY_5_7_6
        else:
            query = _RPL_USER_QUERY
        return self.exec_query(query, options)

    def create_rpl_user(self, host, port, r_user, r_pass=None, verbosity=0,
                        ssl=False):
        """Create the replication user and grant privileges

        If the user exists, check privileges and add privileges as needed.

        host[in]       host of the subordinate
        port[in]       port of the subordinate
        r_user[in]     user to create
        r_pass[in]     password for user to create (optional)
        verbosity[in]  verbosity of output
                       Default = 0
        ssl[in]        If True the grant will include 'REQUIRE SSL'
                       (Default False).

        Returns tuple (bool, str) - (True, None) = success,
                                    (False, <error>) = error
        """

        grants_enabled = self.grant_tables_enabled()
        if not grants_enabled:
            return (True, None)

        if "]" in host:
            host = clean_IPv6(host)

        # Create user class instance
        user = User(self, "%s:%s@%s:%s" % (r_user, r_pass, host, port))
        if not user.exists():
            user.create()

        if not user.has_privilege("*", "*", "REPLICATION SLAVE"):
            if verbosity > 0:
                print "# Granting replication access to replication user..."
            query_str = "GRANT REPLICATION SLAVE ON *.* TO '%s'@'%s' " % \
                        (r_user, host)
            if r_pass:
                query_str += "IDENTIFIED BY '%s'" % r_pass

            if ssl:
                query_str = "{0} {1}".format(query_str, " REQUIRE SSL")
            try:
                self.exec_query(query_str)
            except UtilError:
                return (False, "ERROR: Cannot grant replication subordinate to "
                        "replication user.")

        return (True, None)

    def reset(self, options=None):
        """Reset the main

        options[in]    query options
        """
        if options is None:
            options = {}
        return self.exec_query("RESET MASTER", options)

    def check_rpl_health(self):
        """Check replication health of the main.

        This method checks to see if the main is setup correctly to
        operate in a replication environment. It returns a tuple with a
        bool to indicate if health is Ok (True), and a list to contain any
        errors encountered during the checks.

        Returns tuple (bool, []) - (True, []) = Ok,
                                   (False, error_list) = not setup correctly
        """
        errors = []
        rpl_ok = True

        if not self.is_alive():
            return (False, ["Cannot connect to server"])

        gtid_enabled = self.supports_gtid() == "ON"

        # Check for binlogging
        if not gtid_enabled and not self.binlog_enabled():
            errors.append("No binlog on main.")
            rpl_ok = False

        # See if there is at least one user with rpl privileges
        res = self.get_rpl_users()
        if len(res) == 0:
            errors.append("There are no users with replication privileges.")
            rpl_ok = False

        return (rpl_ok, errors)

    def _check_discovered_subordinate(self, conn_dict):
        """ Check discovered subordinate is configured to this main

        This method attempts to determine if the subordinate specified is
        configured to connect to this main.

        conn_dict[in]  dictionary of connection information

        Returns True if configured with this main otherwise an error is
        raised.
        """
        subordinate_conn = Subordinate(conn_dict)
        try:
            subordinate_conn.connect()
            # Skip discovered subordinates that are not configured
            # to connect to the main
            return subordinate_conn.is_configured_for_main(self,
                                                       verify_state=False,
                                                       raise_error=True)
        finally:
            subordinate_conn.disconnect()

    def get_subordinates(self, user, password):
        """Return the subordinates registered for this main.

        This method returns a list of subordinates (host, port) if this server is
        a main in a replication topology and has subordinates registered.

        user[in]       user login
        password[in]   user password

        Returns list - [host:port, ...]
        """
        def _get_subordinate_info(host, port):
            """Return the subordinate info
            """
            if len(host) > 0:
                if ":" in host:
                    host = format_IPv6(host)
                subordinate_info = host
            else:
                subordinate_info = "unknown host"
            subordinate_info += ":%s" % port
            return subordinate_info

        subordinates = []
        no_host_subordinates = []
        connect_error_subordinates = []
        res = self.exec_query("SHOW SLAVE HOSTS")
        verbose = self.options.get("verbose", False)
        if not res == []:
            # Sort for conformity
            res.sort()  # pylint: disable=E1103

            for row in res:
                info = _get_subordinate_info(row[1], row[2])
                conn_dict = {
                    'conn_info': {'user': user, 'passwd': password,
                                  'host': row[1], 'port': row[2],
                                  'socket': None, 'ssl_ca': self.ssl_ca,
                                  'ssl_cert': self.ssl_cert,
                                  'ssl_key': self.ssl_key,
                                  'ssl': self.ssl},
                    'role': 'subordinate',
                    'verbose': verbose,
                }
                if not row[1]:
                    no_host_subordinates.append(" - {0}".format(info))
                    break
                # Verify subordinate connection and configuration.
                try:
                    self._check_discovered_subordinate(conn_dict)
                    # Subordinate correctly configured.
                    subordinates.append(info)
                except UtilError as err:
                    # Connection or configuration errors found.
                    connect_error_subordinates.append(
                        " - {0}: {1}".format(info, err.errmsg)
                    )

        # Warn if subordinates were found with configuration/connection issues.
        hint = ":" if verbose else " (--verbose for more details)."
        if no_host_subordinates:
            print("WARNING: There are subordinates that have not been registered"
                  " with --report-host or --report-port{0}".format(hint))
            if verbose:
                for row in no_host_subordinates:
                    print(row)
        if connect_error_subordinates:
            print("\nWARNING: Cannot connect to some subordinates{0}".format(hint))
            if verbose:
                for row in connect_error_subordinates:
                    print(row)

        return subordinates

    def get_gtid_purged_statement(self):
        """General the SET @@GTID_PURGED statement for backup

        Returns string - statement for subordinate if GTID=ON, else None
        """
        if self.supports_gtid == "ON":
            gtid_executed = self.exec_query("SELECT @@GLOBAL.GTID_EXECUTED")[0]
            return "SET @@GLOBAL.GTID_PURGED = '{0}'".format(gtid_executed)
        else:
            return None


class MainInfo(object):
    """The MainInfo is an abstraction of the mechanism for storing the
    main information for subordinate servers. It is designed to return an
    implementation neutral representation of the information for use in
    newer servers that use a table to store the information as well as
    older servers that use a file to store the information.
    """

    def __init__(self, subordinate, options):
        """Constructor

        The method accepts one of the following types for options['conn_info']:

            - dictionary containing connection information including:
              (user, passwd, host, port, socket)
            - connection string in the form: user:pass@host:port:socket
            - an instance of the Server class

        options[in]        options for controlling behavior:
          filename         filename for main info file - valid only for
                           servers with main-info-repository=FILE or
                           versions prior to 5.6.5.
          verbosity        determines level of output. Default = 0.
          quiet            turns off all messages except errors.
                           Default is False.
        """

        assert subordinate is not None, "MainInfo requires an instance of Subordinate."
        self.subordinate = subordinate
        self.filename = options.get("main_info", "main.info")
        self.quiet = options.get("quiet", False)
        self.verbosity = options.get("verbosity", 0)
        self.values = {}      # internal dictionary of the values
        self.repo = "FILE"
        if self.subordinate is not None:
            res = self.subordinate.show_server_variable("main_info_repository")
            if res is not None and res != [] and \
               res[0][1].upper() == "TABLE":
                self.repo = "TABLE"

    def read(self):
        """Read the main information

        This method reads the main information either from a file or a
        table depending on the availability of and setting for
        main-info-repository. If missing (server version < 5.6.5), it
        defaults to reading from a file.

        Returns bool - True = success
        """
        if self.verbosity > 2:
            print "# Reading main information from a %s." % self.repo.lower()
        if self.repo == "FILE":
            # Check host name of this host. If not the same, issue error.
            if self.subordinate.is_alias(socket.gethostname()):
                return self._read_main_info_file()
            else:
                raise UtilRplWarn("Cannot read main information file "
                                  "from a remote machine.")
        else:
            return self._read_main_info_table()

    def _check_read(self, refresh=False):
        """Check if main information has been read

        refresh[in]    if True, re-read the main information.
                       Default is False.

        If the main information has not been read, read it and populate
        the dictionary.
        """
        # Read the values if not already read or user says to refresh them.
        if self.values is None or self.values == {} or refresh:
            self.read()

    def _build_dictionary(self, rows):
        """Build the internal dictionary of values.

        rows[in]       Rows as read from the file or table
        """
        for i in range(0, len(rows)):
            self.values[_MASTER_INFO_COL[i]] = rows[i]

    def _read_main_info_file(self):
        """Read the contents of the main.info file.

        This method will raise an error if the file is missing or cannot be
        read by the user.

        Returns bool - success = True
        """
        contents = []
        res = self.subordinate.show_server_variable('datadir')
        if res is None or res == []:
            raise UtilRplError("Cannot get datadir.")
        datadir = res[0][1]
        if self.filename == 'main.info':
            self.filename = os.path.join(datadir, self.filename)

        if not os.path.exists(self.filename):
            raise UtilRplError("Cannot find main information file: "
                               "%s." % self.filename)
        try:
            mfile = open(self.filename, 'r')
            num = int(mfile.readline())
            # Protect overrun of array if main_info file length is
            # changed (more values added).
            if num > len(_MASTER_INFO_COL):
                num = len(_MASTER_INFO_COL)
        except:
            raise UtilRplError("Cannot read main information file: "
                               "%s.\nUser needs to have read access to "
                               "the file." % self.filename)
        # Build the dictionary
        i = 1
        while i < num:
            contents.append(mfile.readline().strip('\n'))
            i += 1
        self._build_dictionary(contents)
        mfile.close()

        return True

    def _read_main_info_table(self):
        """Read the contents of the subordinate_main_info table.

        This method will raise an error if the file is missing or cannot be
        read by the user.

        Returns bool - success = True
        """
        res = None
        try:
            res = self.subordinate.exec_query("SELECT * FROM "
                                        "mysql.subordinate_main_info")
        except UtilError, e:
            raise UtilRplError("Unable to read the subordinate_main_info table. "
                               "Error: %s" % e.errmsg)
        if res is None or res == []:
            return False

        # Protect overrun of array if the main_info table size has changed
        # (more rows than expected).
        num = len(res[0][1:])
        if num > len(_MASTER_INFO_COL):
            num = len(_MASTER_INFO_COL)
        # Build dictionary for the information with column information
        rows = []
        for i in range(0, num):
            rows.append(res[0][i + 1])
        self._build_dictionary(rows)

        return True

    def show_main_info(self, refresh=False):
        """Display the contents of the main information.

        refresh[in]    if True, re-read the main information.
                       Default is False.
        """
        # Check to see if we need to read the information
        self._check_read(refresh)
        stop = len(self.values)
        for i in range(0, stop):
            print "{0:>30} : {1}".format(_MASTER_INFO_COL[i],
                                         self.values[_MASTER_INFO_COL[i]])

    def check_main_info(self, refresh=False):
        """Check to see if main info file matches subordinate status

        This method will return a list of discrepancies if the main.info
        file does not match subordinate status. It will also raise errors if there
        are problem accessing the main.info file.

        refresh[in]    if True, re-read the main information.
                       Default is False.

        Returns [] - no exceptions, list if exceptions found
        """
        # Check to see if we need to read the information
        self._check_read(refresh)
        errors = []
        res = self.subordinate.get_status()
        if res != []:
            state = res[0][_SLAVE_IO_STATE]
            if not state:
                raise UtilRplError("Subordinate is stopped.")
            m_host = res[0][_SLAVE_MASTER_HOST]
            m_port = res[0][_SLAVE_MASTER_PORT]
            rpl_user = res[0][_SLAVE_MASTER_USER]
            if m_host != self.values['Main_Host'] or \
               int(m_port) != int(self.values['Main_Port']) or \
               rpl_user != self.values['Main_User']:
                errors.append("Subordinate is connected to main differently "
                              "than what is recorded in the main "
                              "information file. Main information file "
                              "= user=%s, host=%s, port=%s." %
                              (self.values['Main_User'],
                               self.values['Main_Host'],
                               self.values['Main_Port']))

        return errors

    def get_value(self, key, refresh=False):
        """Returns the value found for the key or None if key not found.

        refresh[in]    if True, re-read the main information.
                       Default is False.

        Returns value - Value found for the key or None if key missing
        """
        # Check to see if we need to read the information
        self._check_read(refresh)
        try:
            return self.values[key]
        except:
            return None

    def get_main_info(self, refresh=False):
        """Returns the main information dictionary.

        refresh[in]    if True, re-read the main information.
                       Default is False.

        Returns dict - main information
        """
        # Check to see if we need to read the information
        self._check_read(refresh)
        return self.values


class Subordinate(Server):
    """The Subordinate class is a subclass of the Server class. It represents a
    MySQL server performing the role of a subordinate in a replication topology.
    The following utilities are provide in addition to the Server utilities:

        - get methods to return status, binary log exceptions, subordinate delay,
          thread status, io error, and main information
        - form the change main command with either known main or user-
          supplied values
        - check to see if subordinate is connected to a main
        - display subordinate status
        - show main information
        - verify main information matches currently connected main
        - start, stop, and reset subordinate

    """

    def __init__(self, options=None):
        """Constructor

        The method accepts one of the following types for options['conn_info']:

            - dictionary containing connection information including:
              (user, passwd, host, port, socket)
            - connection string in the form: user:pass@host:port:socket
            - an instance of the Server class

        options[in]        options for controlling behavior:
            conn_info      a dictionary containing connection information
                           (user, passwd, host, port, socket)
            role           Name or role of server (e.g., server, main)
            verbose        print extra data during operations (optional)
                           default value = False
            charset        Default character set for the connection.
                           (default latin1)
        """
        if options is None:
            options = {}

        assert not options.get("conn_info") is None

        self.options = options
        Server.__init__(self, options)
        self.main_info = None

    def get_status(self, col_options=None):
        """Return the subordinate status

        col_options[in]    options for displaying columns (optional)

        Returns result set
        """
        if not col_options:
            col_options = {}
        return self.exec_query("SHOW SLAVE STATUS", col_options)

    def get_retrieved_gtid_set(self):
        """Get any events (gtids) read but not executed

        Returns a string with the list of gtids in Executed_Gtid_Set.

        Note: an empty string is returned if the server is not acting as a
              subordinate.
        """
        res = self.get_status()
        if res != []:
            return res[0][_RETRIEVED_GTID_SET]
        return ''

    def get_executed_gtid_set(self):
        """Get any events (gtids) executed

        Returns a string with the list of gtids in Executed_Gtid_Set.

        Note: an empty string is returned if the server is not acting as a
              subordinate.
        """
        res = self.get_status()
        if res:
            return res[0][_EXECUTED_GTID_SET]

        return ''

    def get_binlog_exceptions(self):
        """Get any binary logging exceptions

        This method queries the server status for the *-do-db and
        *-ignore-db settings.

        Returns [] - no exceptions, list if exceptions found
        """
        rows = []
        res = self.get_status()
        if res != []:
            do_db = res[0][_SLAVE_DO_DB]
            ignore_db = res[0][_SLAVE_IGNORE_DB]
            if len(do_db) > 0 or len(ignore_db) > 0:
                rows.append(('subordinate', do_db, ignore_db))

        return rows

    def get_main_host_port(self):
        """Get the subordinate's connected main host and port

        Returns tuple - (main host, main port) or
                        None if not acting as subordinate
        """
        res = self.get_status()
        if res == []:
            return None
        m_host = res[0][_SLAVE_MASTER_HOST]
        m_port = res[0][_SLAVE_MASTER_PORT]

        return (m_host, m_port)

    def is_connected(self):
        """Check to see if subordinate is connected to main

        This method will check the subordinate to see if it is connected to a main
        by checking if his I/O Thread is running.

        Returns bool - True = subordinate is connected
        """
        res = self.get_status()
        if res == []:
            return False
        return res[0][_SLAVE_IO_RUNNING].upper() == "YES"

    def get_rpl_main_user(self):
        """Get the rpl main user from the subordinate status

        Returns the subordinate_main_user as string or False if there is
        no subordinate status.
        """
        res = self.get_status()
        if not res:
            return False
        return res[0][_SLAVE_MASTER_USER]

    def get_main_uuid(self):
        """Get the main_uuid from the subordinate status.

        Return the main UUID or None if not an acting subordinate.
        """
        res = self.get_status()
        if not res:
            return None
        return res[0][_MASTER_UUID]

    def get_state(self):
        """Get the subordinate's connection state

        Returns state or None if not acting as subordinate
        """
        res = self.get_status()
        if res == []:
            return None
        state = res[0][_SLAVE_IO_STATE]

        return state

    def get_io_running(self):
        """Get the subordinate's IO thread status

        Returns IO_THREAD state or None if not acting as subordinate
        """
        res = self.get_status()
        if res == []:
            return None
        return res[0][_SLAVE_IO_RUNNING]

    def get_sql_running(self):
        """Get the subordinate's SQL thread status

        Returns SQL_THREAD state or None if not acting as subordinate
        """
        res = self.get_status()
        if res == []:
            return None
        return res[0][_SLAVE_SQL_RUNNING]

    def get_delay(self):
        """Return subordinate delay values

        This method retrieves the subordinate's delay parameters.

        Returns tuple - subordinate delay values or None if not connected
        """
        res = self.get_status()
        if res == []:
            return None

        # subordinate IO state
        state = res[0][_SLAVE_IO_STATE]
        # seconds behind main
        if res[0][_SLAVE_DELAY] is None:
            sec_behind = 0
        else:
            sec_behind = int(res[0][_SLAVE_DELAY])
        # remaining delay
        delay_remaining = res[0][_SLAVE_REMAINING_DELAY]
        # main's log file read
        read_log_file = res[0][_SLAVE_MASTER_LOG_FILE]
        # position in main's binlog
        read_log_pos = res[0][_SLAVE_MASTER_LOG_FILE_POS]

        return (state, sec_behind, delay_remaining,
                read_log_file, read_log_pos)

    def get_thread_status(self):
        """Return the subordinate threads status

        Returns tuple - (subordinate_io_state, subordinate_io_running, subordinate_sql_running)
                        or None if not connected
        """
        res = self.get_status()
        if res == []:
            return None

        # subordinate IO state
        state = res[0][_SLAVE_IO_STATE]
        # subordinate_io_running
        io_running = res[0][_SLAVE_IO_RUNNING]
        # subordinate_sql_running
        sql_running = res[0][_SLAVE_SQL_RUNNING]

        return (state, io_running, sql_running)

    def get_io_error(self):
        """Return the subordinate subordinate io error status

        Returns tuple - (subordinate_io_state, io_errorno, io_error)
                        or None if not connected
        """
        res = self.get_status()
        if res == []:
            return None

        state = res[0][_SLAVE_IO_STATE]
        io_errorno = int(res[0][_SLAVE_IO_ERRORNO])
        io_error = res[0][_SLAVE_IO_ERROR]

        return (state, io_errorno, io_error)

    def get_sql_error(self):
        """Return the subordinate subordinate sql error status

        Returns tuple - (sql_running, sql_errorno, sql_error)
                        or None if not connected
        """
        res = self.get_status()
        if not res:
            return None

        sql_running = res[0][_SLAVE_SQL_RUNNING]
        sql_errorno = int(res[0][_SLAVE_SQL_ERRORNO])
        sql_error = res[0][_SLAVE_SQL_ERROR]

        return (sql_running, sql_errorno, sql_error)

    def get_subordinates_errors(self):
        """Return the subordinate subordinate io and sql error status

        Returns tuple - (subordinate_io_state, io_errorno, io_error, io_running,
                         sql_running, sql_errorno, sql_error)
                        or None if not connected
        """
        res = self.get_status()
        if not res:
            return None

        state = res[0][_SLAVE_IO_STATE]
        io_errorno = int(res[0][_SLAVE_IO_ERRORNO])
        io_error = res[0][_SLAVE_IO_ERROR]
        io_running = res[0][_SLAVE_IO_RUNNING]
        sql_running = res[0][_SLAVE_SQL_RUNNING]
        sql_errorno = int(res[0][_SLAVE_SQL_ERRORNO])
        sql_error = res[0][_SLAVE_SQL_ERROR]

        return (state, io_errorno, io_error, io_running, sql_running,
                sql_errorno, sql_error)

    def get_subordinate_rpl_filters(self):
        """Get the replication filter options for the subordinate.

        Get the replication filter information from the subordinate status.

        Returns a tuple with the replication filter options (Replicate_Do_DB,
        Replicate_Ignore_DB, Replicate_Do_Table, Replicate_Ignore_Table,
        Replicate_Wild_Do_Table, Replicate_Wild_Ignore_Table). An empty tuple
        () is returned if no filter is defined and None if the subordinate status is
        not available.
        """
        res = self.get_status()
        if not res:
            return None

        rpl_do_db = res[0][_SLAVE_DO_DB]
        rpl_ignore_db = res[0][_SLAVE_IGNORE_DB]
        rpl_do_table = res[0][_SLAVE_DO_TABLE]
        rpl_ignore_table = res[0][_SLAVE_IGNORE_TABLE]
        rpl_wild_do_table = res[0][_SLAVE_WILD_DO_TABLE]
        rpl_wild_ignore_table = res[0][_SLAVE_WILD_IGNORE_TABLE]

        if (rpl_do_db or rpl_ignore_db or rpl_do_table or rpl_ignore_table
                or rpl_wild_do_table or rpl_wild_ignore_table):
            return (rpl_do_db, rpl_ignore_db, rpl_do_table, rpl_ignore_table,
                    rpl_wild_do_table, rpl_wild_ignore_table)
        else:
            return ()

    def show_status(self):
        """Display the subordinate status from the subordinate server
        """
        col_options = {
            'columns': True
        }
        res = self.get_status(col_options)
        if res != [] and res[1] != []:
            stop = len(res[0])
            cols = res[0]
            rows = res[1]
            for i in range(0, stop):
                print "{0:>30} : {1}".format(cols[i], rows[0][i])
        else:
            raise UtilRplError("Cannot get subordinate status or subordinate is "
                               "not configured as a subordinate or not "
                               "started.")

    def get_rpl_user(self):
        """Return the main user from the main info record.

        Returns - tuple = (user, password) or (None, None) if errors
        """
        self.main_info = MainInfo(self, self.options)
        m_host = self.main_info.get_value("Main_User")
        m_passwd = self.main_info.get_value("Main_Password")
        if m_host is not None:
            return (m_host, m_passwd)
        return (None, None)

    def start(self, options=None, autocommit_fix=True, until_gtid_set=None,
              sql_after_gtid=True, only_sql_thread=False):
        """Start the subordinate.

        Execute the START SLAVE statement (to start the IO and/or SQL threads),
        according to the used parameters.

        options[in]         query options
        autocommit_fix[in]  If True, turn off AUTOCOMMIT before start command.
                            True by default to always apply the fix.
        until_gtid_set[in]  GTID set to use to execute START SLAVE UNTIL. By
                            default None, until option is not applied.
        sql_after_gtid[in]  Indicates if the until option SQL_AFTER_GTIDS is
                            used or in alternative SQL_BEFORE_GTIDS. Only
                            applied if until_gtid_set is specified. By default
                            True, SQL_AFTER_GTIDS is used.
        only_sql_thread[in] If True only the SQL thread is started, otherwise
                            both (by default).
        """
        if options is None:
            options = {}

        # Temporary workaround for BUG#16533802 - remove when fixed (part 1/2).
        if autocommit_fix:
            autocommit_value = self.autocommit_set()
            # If disabled, turn it on.
            if not autocommit_value:
                self.toggle_autocommit(True)

        query = "START SLAVE"
        if only_sql_thread:
            query = "{0} SQL_THREAD".format(query)
        if until_gtid_set:
            # Use until option.
            until_type = (
                'SQL_AFTER_GTIDS' if sql_after_gtid else 'SQL_BEFORE_GTIDS'
            )
            query = "{0} UNTIL {1} = '{2}'".format(query, until_type,
                                                   until_gtid_set)
        res = self.exec_query(query, options)

        # Temporary workaround for BUG#16533802 - remove when fixed (part 2/2).
        if autocommit_fix:
            # If disabled originally, turn it off.
            if not autocommit_value:
                self.toggle_autocommit(False)

        return res

    def start_sql_thread(self, options=None):
        """Start the subordinate SQL thread

        options[in]    query options
        """
        if options is None:
            options = {}
        return self.exec_query("START SLAVE SQL_THREAD", options)

    def stop(self, options=None):
        """Stop the subordinate

        options[in]    query options
        """
        if options is None:
            options = {}
        return self.exec_query("STOP SLAVE", options)

    def stop_sql_thread(self, options=None):
        """Stop the subordinate SQL thread.

        options[in]    query options
        """
        if options is None:
            options = {}
        return self.exec_query("STOP SLAVE SQL_THREAD", options)

    def reset(self, options=None):
        """Reset the subordinate

        options[in]    query options
        """
        if options is None:
            options = {}
        return self.exec_query("RESET SLAVE", options)

    def reset_all(self, options=None):
        """Reset all information on this subordinate.

        options[in]    query options
        """
        if options is None:
            options = {}
        # Must be sure to do stop first
        self.stop()
        # RESET SLAVE ALL was implemented in version 5.5.16 and later
        if not self.check_version_compat(5, 5, 16):
            return self.reset()
        return self.exec_query("RESET SLAVE ALL", options)

    def wait_checksum_and_start(self, tbl_name, wait_timeout=30,
                                wait_interval=3, checksum_timeout=0,
                                options=None):
        """Checksum specified table and start subordinate.

        tbl_name[in]        Name of the table to perform the checksum.
        wait_timeout[in]    Timeout value to wait for the subordinate to stop SQL
                            thread (automatically stopped after catching up
                            with main). By default 30 seconds.
        wait_interval[in]   Wait interval to perform the next polling (check
                            if SQL thread is stopped) By default 3 seconds..
        options[in]     Query options.

        Returns the result of the table checksum,more precisely a tuple with
        the checksum and an error description. If the checksum is computed it
        returns (checksum, None), otherwise (None, <skip error description>)
        where <skip error description> is a brief description of the motive why
        the checksum was not computed.
        """
        # Wait for subordinate to stop (if timeout > 0).
        tick = 0
        checksum = None
        skip_checksum = True if wait_timeout > 0 else False
        while tick < wait_timeout:
            status = self.get_subordinates_errors()
            io_running = status[3].upper() == 'YES'
            sql_running = status[4].upper() == 'YES'
            # Only check if SQl thread is running since START SLAVE UNTIL does
            # not stop the IO thread.
            if sql_running:
                time.sleep(wait_interval)
                tick += wait_interval
            else:
                skip_checksum = False
                # Report if replication was stopped due to an error.
                if not io_running and status[2]:
                    print("# IO thread ERROR found for {0}:{1}: {2} - "
                          "{3}".format(self.host, self.port, status[1],
                                       status[2]))
                if not sql_running and status[6]:
                    print("# SQL thread ERROR found for {0}:{1}: {2} - "
                          "{3}".format(self.host, self.port, status[5],
                                       status[6]))
                break

        if skip_checksum:
            # Checksum skipped.
            skip_error = "timeout catching up with main"
            self.stop_sql_thread(options)
        else:
            # Compute checksum.
            checksum, skip_error = self.checksum_table(
                tbl_name, exec_timeout=checksum_timeout
            )

        # Resume replication, start subordinate.
        self.start_sql_thread(options)

        return checksum, skip_error

    def num_gtid_behind(self, main_gtids):
        """Get the number of transactions the subordinate is behind the main.

        main_gtids[in]  the main's GTID_EXECUTED list

        Returns int - number of trans behind main
        """
        subordinate_gtids = self.exec_query(_GTID_EXECUTED)[0][0]
        gtids = self.exec_query("SELECT GTID_SUBTRACT('%s','%s')" %
                                (main_gtids[0][0], subordinate_gtids))[0]
        # Init gtid_behind count (if no GTIDs behind then 0 is returned)
        gtid_behind = 0
        # Check if there are GTIDs behind
        # (i.e. string with GTIDs set is not equal to '')
        if gtids[0]:
            gtids_list = gtids[0].split("\n")
            # Extract the interval for each GTID and compute its length
            for gtid_item in gtids_list:
                interval_list = gtid_item.rstrip(', ')
                interval_list = interval_list.split(':')[1:]
                for interval_str in interval_list:
                    interval = interval_str.split('-')
                    if len(interval) == 1:
                        # Interval has only one element
                        gtid_behind += 1
                    else:
                        # Compute interval size and sum to total GTIDs behind.
                        num_gtids = int(interval[1]) - int(interval[0]) + 1
                        gtid_behind += num_gtids
        return gtid_behind

    def wait_for_subordinate(self, binlog_file, binlog_pos, timeout=300):
        """Wait for the subordinate to read the main's binlog to specified position

        binlog_file[in]  main's binlog file
        binlog_pos[in]   main's binlog file position
        timeout[in]      maximum number of seconds to wait for event to occur

        Returns bool - True = subordinate has read to the file and pos,
                       False = subordinate is behind.
        """
        # Wait for subordinate to read the main log file
        _MASTER_POS_WAIT = "SELECT MASTER_POS_WAIT('%s', %s, %s)"
        res = self.exec_query(_MASTER_POS_WAIT % (binlog_file,
                                                  binlog_pos, timeout))
        if res is None or (res[0][0] is not None and int(res[0][0]) < 0):
            return False
        return True

    def wait_for_subordinate_gtid(self, main_gtid, timeout=300, verbose=False):
        """Wait for the subordinate to read the main's GTIDs.

        This method requires that the server supports GTIDs.

        main_gtid[in]  the list of gtids from the main
                         obtained via SELECT @@GLOBAL.GTID_EXECUTED on main
        timeout[in]      timeout for waiting for subordinate to catch up
                         Note: per GTID call. Default is 300 seconds (5 min.).
        verbose[in]      if True, print query used.
                         Default is False

        Returns bool - True = subordinate has read all GTIDs
                       False = subordinate is behind
        """
        main_gtids = main_gtid[0][0].split('\n')
        subordinate_wait_ok = True
        for gtid in main_gtids:
            try:
                if verbose:
                    print "# Subordinate %s:%s:" % (self.host, self.port)
                    print "# QUERY =", _GTID_WAIT % (gtid.strip(','), timeout)
                res = self.exec_query(_GTID_WAIT % (gtid.strip(','), timeout))
                if verbose:
                    print "# Return Code =", res[0][0]
                if res is None or res[0] is None or res[0][0] is None or \
                   int(res[0][0]) < 0:
                    subordinate_wait_ok = False
            except UtilRplError, e:
                raise UtilRplError("Error executing %s: %s" %
                                   ((_GTID_WAIT % (gtid.strip(','), timeout)),
                                    e.errmsg))
        return subordinate_wait_ok

    def make_change_main(self, from_beginning=False, main_values=None):
        """Make the CHANGE MASTER command.

        This method forms the CHANGE MASTER command based on the current
        settings of the subordinate. If the user supplies a dictionary of options,
        the method will use those values provided by the user if present
        otherwise it will use current settings.

        Note: the keys used in the dictionary are defined in the
              _MASTER_INFO_COL list defined above.

        from_beginning[in] if True, omit specification of main's binlog info
        main_values[in] if provided, use values in the dictionary

        Returns string - CHANGE MASTER command
        """
        if not main_values:
            main_values = {}
        if main_values == {} and not self.is_connected():
            raise UtilRplError("Cannot generate CHANGE MASTER command. The "
                               "subordinate is not connected to a main and no "
                               "main information was provided.")
        elif self.is_connected():
            m_info = MainInfo(self, self.options)
            main_info = m_info.get_main_info()
            if main_info is None and main_values == {}:
                raise UtilRplError("Cannot create CHANGE MASTER command.")
        else:
            main_info = None

        # Form values for command.
        # If we cannot get the main info information, try the values passed
        if main_info is None:
            main_host = main_values['Main_Host']
            if "]" in main_host:
                main_host = clean_IPv6(main_host)
            main_port = main_values['Main_Port']
            main_user = main_values['Main_User']
            main_passwd = main_values['Main_Password']
            main_log_file = main_values['Main_Log_File']
            main_log_pos = main_values['Read_Main_Log_Pos']
            main_ssl = main_values.get('Main_SSL_Allowed', None)
            main_ssl_ca = main_values.get('Main_SSL_CA_File', None)
            main_ssl_cert = main_values.get('Main_SSL_Cert', None)
            main_ssl_key = main_values.get('Main_SSL_Key', None)
            if main_ssl and main_ssl_ca is None:
                main_ssl_ca = ''
        else:
            main_host = main_values.get('Main_Host',
                                            main_info['Main_Host'])
            main_port = main_values.get('Main_Port',
                                            main_info['Main_Port'])
            main_user = main_values.get('Main_User',
                                            main_info['Main_User'])
            main_passwd = main_values.get('Main_Password',
                                              main_info['Main_Password'])
            main_log_file = main_values.get('Main_Log_File',
                                                main_info['Main_Log_File'])
            main_log_pos = main_values.get(
                'Read_Main_Log_Pos',
                main_info['Read_Main_Log_Pos']
            )
            main_ssl = main_values.get(
                'Main_SSL_Allowed',
                main_info['Main_SSL_Allowed']
            )
            main_ssl_ca = main_values.get(
                'Main_SSL_CA_File',
                main_info['Main_SSL_CA_File']
            )
            main_ssl_cert = main_values.get(
                'Main_SSL_Cert',
                main_info['Main_SSL_Cert']
            )
            main_ssl_key = main_values.get(
                'Main_SSL_Key',
                main_info['Main_SSL_Key']
            )

        change_main = "CHANGE MASTER TO MASTER_HOST = '%s', " % main_host
        if main_user:
            change_main += "MASTER_USER = '%s', " % main_user
        # To rewrite a current password with blank password, not check against
        # empty string.
        if main_passwd is not None:
            change_main += "MASTER_PASSWORD = '%s', " % main_passwd
        change_main += "MASTER_PORT = %s" % main_port
        if main_ssl and main_ssl not in ('0', 'OFF'):
            change_main = "{0}, MASTER_SSL = {1}".format(change_main, 1)
        if main_ssl_ca is not None:
            change_main = ("{0}, MASTER_SSL_CA = '{1}'"
                             ).format(change_main, main_ssl_ca)
        if main_ssl_cert:
            change_main = ("{0}, MASTER_SSL_CERT = '{1}'"
                             ).format(change_main, main_ssl_cert)
        if main_ssl_key:
            change_main = ("{0}, MASTER_SSL_KEY = '{1}'"
                             ).format(change_main, main_ssl_key)
        if self.supports_gtid() == "ON":
            change_main += ", MASTER_AUTO_POSITION=1"
        elif not from_beginning:
            change_main += ", MASTER_LOG_FILE = '%s'" % main_log_file
            if main_log_pos >= 0:
                change_main += ", MASTER_LOG_POS = %s" % main_log_pos

        return change_main

    def is_configured_for_main(self, main, verify_state=False,
                                 raise_error=False):
        """Check that subordinate is connected to the main at host, port.

        main[in]          Instance of the main.
        verify_state[in]    Flag to verify the state of the subordinate.
                            By default False, state verification ignored.
        raise_error[in]     Indicate if an Error is raised instead of
                            returning false (not configured for main).
                            By default False, return a boolean value.

        Returns bool - True = is connected
        """
        res = self.get_status()
        if res == [] or not res[0]:
            if raise_error:
                raise UtilRplError("Server '{0}:{1}' is not acting as a subordinate "
                                   "(subordinate status is empty)"
                                   ".".format(self.host, self.port))
            return False
        # We must not assume there is one and only one main for a subordinate.
        # Starting with 5.7.6, multi-main means a subordinate could have many
        # mains, each connected via a replication channel. Thus, we must
        # loop through the rows in the SHOW SLAVE STATUS and check every
        # main listed. If no matches to this main is found, we can
        # declare the subordinate not connected to the main otherwise, we can
        # stop the loop when the main is found.
        m_host = ""
        m_port = None
        main_found = False
        for row in res:
            # pylint: disable=W0633
            m_host = row[_SLAVE_MASTER_HOST]  # get main host
            m_port = row[_SLAVE_MASTER_PORT]  # get main port
            # Suppose the state is True for "Waiting for main to send event"
            # so we can ignore it if verify_state is not given as True.
            if verify_state:
                state = (row[_SLAVE_IO_STATE] ==
                         "Waiting for main to send event")
                if not state:
                    if raise_error:
                        raise UtilRplError("Subordinate '{0}:{1}' is not waiting"
                                           " for events from main."
                                           "".format(self.host, self.port))
                    return False
            # If we find a match, stop.
            if main.is_alias(m_host) and int(m_port) == int(main.port):
                main_found = True
                break
        # If no main found, report what we did find or in the case of
        # multi-main (more than one row in SHOW SLAVE STATUS), state this
        # main is not among the mains listed for the subordinate.
        if not main_found:
            if raise_error:
                if len(res) > 1:
                    raise UtilRplError("The list of mains for subordinate "
                                       "'{0}:{1}' does not include main"
                                       " '{2}:{3}'"
                                       ".".format(self.host, self.port,
                                                  main.host, main.port))
                else:
                    raise UtilRplError("Subordinate '{0}:{1}' is configured for "
                                       "main '{2}:{3}' and not '{4}:{5}'"
                                       ".".format(self.host, self.port,
                                                  m_host, m_port,
                                                  main.host, main.port))
            return False
        return True

    def check_rpl_health(self, main, main_log, main_log_pos,
                         max_delay, max_pos, verbosity):
        """Check replication health of the subordinate.

        This method checks to see if the subordinate is setup correctly to
        operate in a replication environment. It returns a tuple with a
        bool to indicate if health is Ok (True), and a list to contain any
        errors encountered during the checks.

        main[in]         Main class instance
        main_log[in]     main's log file
        main_log_pos[in] main's log file position
        max_delay[in]      if the subordinate delay (in seconds) is greater than this
                           value, the subordinate health is not Ok
        max_pos[in]        maximum position difference from main to subordinate to
                           determine if subordinate health is not Ok
        verbosity[in]      if > 1, return detailed errors else return only
                           short phrases

        Returns tuple (bool, []) - (True, []) = Ok,
                                   (False, error_list) = not setup correctly
        """
        errors = []
        rpl_ok = True

        if not self.is_alive():
            return (False, ["Cannot connect to server"])

        res = self.get_status()
        if res != [] and res[0] != []:
            res = res[0]
            self.get_main_host_port()
            m_log = res[_SLAVE_MASTER_LOG_FILE]
            m_log_pos = res[_SLAVE_MASTER_LOG_FILE_POS]
            io_running = res[_SLAVE_IO_RUNNING]
            sql_running = res[_SLAVE_SQL_RUNNING]
            s_delay = res[_SLAVE_DELAY]
            delay = s_delay if s_delay is not None else 0
            remaining_delay = res[_SLAVE_REMAINING_DELAY]
            io_error_num = res[_SLAVE_IO_ERRORNO]
            io_error_text = res[_SLAVE_IO_ERROR]

            # Check to see that subordinate is connected to the right main
            if not self.is_configured_for_main(main):
                return (False, ["Not connected to correct main."])

            # Check subordinate status for errors, threads activity
            if io_running.upper() != "YES":
                errors.append("IO thread is not running.")
                rpl_ok = False
            if sql_running.upper() != "YES":
                errors.append("SQL thread is not running.")
                rpl_ok = False
            if int(io_error_num) > 0:
                errors.append(io_error_text)
                rpl_ok = False

            # Check subordinate delay with threshhold of SBM, and main's log pos
            if int(delay) > int(max_delay):
                errors.append("Subordinate delay is %s seconds behind main." %
                              delay)
                if len(remaining_delay):
                    errors.append(remaining_delay)
                rpl_ok = False

            # Check main position
            if self.supports_gtid() != "ON":
                if m_log != main_log:
                    errors.append("Wrong main log file.")
                    rpl_ok = False
                elif (int(m_log_pos) + int(max_pos)) < int(main_log_pos):
                    errors.append("Subordinate's main position exceeds maximum.")
                    rpl_ok = False

            # Check GTID trans behind.
            elif self.supports_gtid() == "ON":
                main_gtids = main.exec_query(_GTID_EXECUTED)
                num_gtids_behind = self.num_gtid_behind(main_gtids)
                if num_gtids_behind > 0:
                    errors.append("Subordinate has %s transactions behind main." %
                                  num_gtids_behind)
                    rpl_ok = False

        else:
            errors.append("Not connected")
            rpl_ok = False

        if len(errors) > 1:
            errors = [", ".join(errors)]

        return (rpl_ok, errors)

    def get_rpl_details(self):
        """Return subordinate status variables for health reporting

        This method retrieves the subordinate's parameters for checking relationship
        with main.

        Returns tuple - subordinate values or None if not connected
        """
        res = self.get_status()
        if res == []:
            return None

        res = res[0]
        read_log_file = res[_SLAVE_MASTER_LOG_FILE]
        read_log_pos = res[_SLAVE_MASTER_LOG_FILE_POS]
        io_thread = res[_SLAVE_IO_RUNNING]
        sql_thread = res[_SLAVE_SQL_RUNNING]

        # seconds behind main
        if res[_SLAVE_DELAY] is None:
            sec_behind = 0
        else:
            sec_behind = int(res[_SLAVE_DELAY])
        delay_remaining = res[_SLAVE_REMAINING_DELAY]

        io_error_num = res[_SLAVE_IO_ERRORNO]
        io_error_text = res[_SLAVE_IO_ERROR]
        sql_error_num = res[_SLAVE_SQL_ERRORNO]
        sql_error_text = res[_SLAVE_SQL_ERROR]

        return (read_log_file, read_log_pos, io_thread, sql_thread, sec_behind,
                delay_remaining, io_error_num, io_error_text, sql_error_num,
                sql_error_text)

    def switch_main(self, main, user, passwd="", from_beginning=False,
                      main_log_file=None, main_log_pos=None,
                      show_command=False):
        """Switch subordinate to a new main

        This method stops the subordinate and issues a new change main command
        to the main specified then starts the subordinate. No prerequisites are
        checked and it does not wait to see if subordinate catches up to the main.

        main[in]           Main class instance
        user[in]             replication user
        passwd[in]           replication user password
        from_beginning[in]   if True, start from beginning of logged events
                             Default = False
        main_log_file[in]  main's log file (not needed for GTID)
        main_log_pos[in]   main's log file position (not needed for GTID)
        show_command[in]     if True, display the change main command
                             Default = False

        returns bool - True = success
        """
        hostport = "%s:%s" % (self.host, self.port)

        main_values = {
            'Main_Host': main.host,
            'Main_Port': main.port,
            'Main_User': user,
            'Main_Password': passwd,
            'Main_Log_File': main_log_file,
            'Read_Main_Log_Pos': main_log_pos,
        }
        if main.has_ssl:
            main_values['Main_SSL_Allowed'] = 1
            if main.ssl_ca:
                main_values['Main_SSL_CA_File'] = main.ssl_ca
            if main.ssl_cert:
                main_values['Main_SSL_Cert'] = main.ssl_cert
            if main.ssl_key:
                main_values['Main_SSL_Key'] = main.ssl_key
        change_main = self.make_change_main(from_beginning, main_values)
        if show_command:
            print "# Change main command for %s:%s" % (self.host, self.port)
            print "#", change_main
        try:
            self.exec_query(change_main)
        except UtilError as err:
            raise UtilRplError("Subordinate {0} change main failed. "
                               "{1}".format(hostport, err.errmsg))
        return True
