#
# Copyright (c) 2014, 2016 Oracle and/or its affiliates. All rights
# reserved.
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
This file contains the multi-source replication utility. It is used to setup
replication among a subordinate and multiple mains.
"""

import os
import sys
import time
import logging

from mysql.utilities.exception import FormatError, UtilError, UtilRplError
from mysql.utilities.common.daemon import Daemon
from mysql.utilities.common.format import print_list
from mysql.utilities.common.ip_parser import hostname_is_ip
from mysql.utilities.common.messages import (ERROR_USER_WITHOUT_PRIVILEGES,
                                             ERROR_MIN_SERVER_VERSIONS,
                                             HOST_IP_WARNING)
from mysql.utilities.common.options import parse_user_password
from mysql.utilities.common.server import connect_servers, get_server_state
from mysql.utilities.common.replication import Replication, Main, Subordinate
from mysql.utilities.common.topology import Topology
from mysql.utilities.common.user import User
from mysql.utilities.common.messages import USER_PASSWORD_FORMAT


_MIN_SERVER_VERSION = (5, 6, 9)
_GTID_LISTS = ["Transactions executed on the servers:",
               "Transactions purged from the servers:",
               "Transactions owned by another server:"]
_GEN_UUID_COLS = ["host", "port", "role", "uuid"]
_GEN_GTID_COLS = ["host", "port", "role", "gtid"]


class ReplicationMultiSource(Daemon):
    """Setup replication among a subordinate and multiple mains.

    This class implements a multi-source replication using a round-robin
    scheduling for setup replication among all mains and subordinate.

    This class also implements a POSIX daemon.
    """
    def __init__(self, subordinate_vals, mains_vals, options):
        """Constructor.

        subordinate_vals[in]     Subordinate server connection dictionary.
        main_vals[in]    List of main server connection dictionaries.
        options[in]        Options dictionary.
        """
        pidfile = options.get("pidfile", None)
        if pidfile is None:
            pidfile = "./rplms_daemon.pid"
        super(ReplicationMultiSource, self).__init__(pidfile)

        self.subordinate_vals = subordinate_vals
        self.mains_vals = mains_vals
        self.options = options
        self.quiet = self.options.get("quiet", False)
        self.logging = self.options.get("logging", False)
        self.rpl_user = self.options.get("rpl_user", None)
        self.verbosity = options.get("verbosity", 0)
        self.interval = options.get("interval", 15)
        self.switchover_interval = options.get("switchover_interval", 60)
        self.format = self.options.get("format", False)
        self.topology = None
        self.report_values = [
            report.lower() for report in
            self.options["report_values"].split(",")
        ]

        # A sys.stdout copy, that can be used later to turn on/off stdout
        self.stdout_copy = sys.stdout
        self.stdout_devnull = open(os.devnull, "w")

        # Disable stdout when running --daemon with start, stop or restart
        self.daemon = options.get("daemon")
        if self.daemon:
            if self.daemon in ("start", "nodetach"):
                self._report("Starting multi-source replication daemon...",
                             logging.INFO, False)
            elif self.daemon == "stop":
                self._report("Stopping multi-source replication daemon...",
                             logging.INFO, False)
            else:
                self._report("Restarting multi-source replication daemon...",
                             logging.INFO, False)

            # Disable stdout
            sys.stdout = self.stdout_devnull
        else:
            self._report("# Starting multi-source replication...",
                         logging.INFO)
            print("# Press CTRL+C to quit.")

        # Check server versions
        try:
            self._check_server_versions()
        except UtilError as err:
            raise UtilRplError(err.errmsg)

        # Check user privileges
        try:
            self._check_privileges()
        except UtilError as err:
            msg = "Error checking user privileges: {0}".format(err.errmsg)
            self._report(msg, logging.CRITICAL, False)
            raise UtilRplError(err.errmsg)

    @staticmethod
    def _reconnect_server(server, pingtime=3):
        """Tries to reconnect to the server.

        This method tries to reconnect to the server and if connection fails
        after 3 attemps, returns False.

        server[in]      Server instance.
        pingtime[in]    Interval between connection attempts.
        """
        if server and server.is_alive():
            return True
        is_connected = False
        i = 0
        while i < 3:
            try:
                server.connect()
                is_connected = True
                break
            except UtilError:
                pass
            time.sleep(pingtime)
            i += 1
        return is_connected

    def _get_subordinate(self):
        """Get the subordinate server instance.

        Returns a Server instance of the subordinate from the replication topology.
        """
        return self.topology.subordinates[0]["instance"]

    def _get_main(self):
        """Get the current main server instance.

        Returns a Server instance of the current main from the replication
        topology.
        """
        return self.topology.main

    def _check_server_versions(self):
        """Checks the server versions.
        """
        if self.verbosity > 0:
            print("# Checking server versions.\n#")

        # Connection dictionary
        conn_dict = {
            "conn_info": None,
            "quiet": True,
            "verbose": self.verbosity > 0,
        }

        # Check mains version
        for main_vals in self.mains_vals:
            conn_dict["conn_info"] = main_vals
            main = Main(conn_dict)
            main.connect()
            if not main.check_version_compat(*_MIN_SERVER_VERSION):
                raise UtilRplError(
                    ERROR_MIN_SERVER_VERSIONS.format(
                        utility="mysqlrplms",
                        min_version=".".join([str(val) for val in
                                              _MIN_SERVER_VERSION]),
                        host=main.host,
                        port=main.port
                    )
                )
            main.disconnect()

        # Check subordinate version
        conn_dict["conn_info"] = self.subordinate_vals
        subordinate = Subordinate(conn_dict)
        subordinate.connect()
        if not subordinate.check_version_compat(*_MIN_SERVER_VERSION):
            raise UtilRplError(
                ERROR_MIN_SERVER_VERSIONS.format(
                    utility="mysqlrplms",
                    min_version=".".join([str(val) for val in
                                          _MIN_SERVER_VERSION]),
                    host=subordinate.host,
                    port=subordinate.port
                )
            )
        subordinate.disconnect()

    def _check_privileges(self):
        """Check required privileges to perform the multi-source replication.

        This method check if the used users for the subordinate and mains have
        the required privileges to perform the multi-source replication.
        The following privileges are required:
            - on subordinate: SUPER, SELECT, INSERT, UPDATE, REPLICATION
                        SLAVE AND GRANT OPTION;
            - on the main: SUPER, SELECT, INSERT, UPDATE, REPLICATION SLAVE
                             AND GRANT OPTION.
        An exception is thrown if users doesn't have enough privileges.
        """
        if self.verbosity > 0:
            print("# Checking users privileges for replication.\n#")

        # Connection dictionary
        conn_dict = {
            "conn_info": None,
            "quiet": True,
            "verbose": self.verbosity > 0,
        }

        # Check privileges for main.
        main_priv = [('SUPER',), ('SELECT',), ('INSERT',), ('UPDATE',),
                       ('REPLICATION SLAVE',), ('GRANT OPTION',)]
        main_priv_str = ("SUPER, SELECT, INSERT, UPDATE, REPLICATION SLAVE "
                           "AND GRANT OPTION")

        for main_vals in self.mains_vals:
            conn_dict["conn_info"] = main_vals
            main = Main(conn_dict)
            main.connect()

            user_obj = User(main, "{0}@{1}".format(main.user, main.host))
            for any_priv_tuple in main_priv:
                has_privilege = any(
                    [user_obj.has_privilege('*', '*', priv)
                        for priv in any_priv_tuple]
                )
                if not has_privilege:
                    msg = ERROR_USER_WITHOUT_PRIVILEGES.format(
                        user=main.user, host=main.host, port=main.port,
                        operation='perform replication',
                        req_privileges=main_priv_str
                    )
                    self._report(msg, logging.CRITICAL, False)
                    raise UtilRplError(msg)
            main.disconnect()

        # Check privileges for subordinate
        subordinate_priv = [('SUPER',), ('SELECT',), ('INSERT',), ('UPDATE',),
                      ('REPLICATION SLAVE',), ('GRANT OPTION',)]
        subordinate_priv_str = ("SUPER, SELECT, INSERT, UPDATE, REPLICATION SLAVE "
                          "AND GRANT OPTION")

        conn_dict["conn_info"] = self.subordinate_vals
        subordinate = Subordinate(conn_dict)
        subordinate.connect()

        user_obj = User(subordinate, "{0}@{1}".format(subordinate.user, subordinate.host))
        for any_priv_tuple in subordinate_priv:
            has_privilege = any(
                [user_obj.has_privilege('*', '*', priv)
                    for priv in any_priv_tuple]
            )
            if not has_privilege:
                msg = ("User '{0}' on '{1}@{2}' does not have sufficient "
                       "privileges to perform replication (required: {3})."
                       "".format(subordinate.user, subordinate.host, subordinate.port,
                                 subordinate_priv_str))
                self._report(msg, logging.CRITICAL, False)
                raise UtilRplError(msg)
        subordinate.disconnect()

    def _check_host_references(self):
        """Check to see if using all host or all IP addresses.

        Returns bool - True = all references are consistent.
        """
        uses_ip = hostname_is_ip(self.topology.main.host)
        subordinate = self._get_subordinate()
        host_port = subordinate.get_main_host_port()
        host = None
        if host_port:
            host = host_port[0]
        if (not host or uses_ip != hostname_is_ip(subordinate.host) or
           uses_ip != hostname_is_ip(host)):
            return False
        return True

    def _setup_replication(self, main_vals, use_rpl_setup=True):
        """Setup replication among a main and a subordinate.

        main_vals[in]      Main server connection dictionary.
        use_rpl_setup[in]    Use Replication.setup() if True otherwise use
                             switch_main() on the subordinate. This is used to
                             control the first pass in the mains round-robin
                             scheduling.
        """
        conn_options = {
            "src_name": "main",
            "dest_name": "subordinate",
            "version": "5.0.0",
            "unique": True,
        }
        (main, subordinate,) = connect_servers(main_vals, self.subordinate_vals,
                                           conn_options)
        rpl_options = self.options.copy()
        rpl_options["verbosity"] = self.verbosity > 0

        # Start from beginning only on the first pass
        if rpl_options.get("from_beginning", False) and not use_rpl_setup:
            rpl_options["from_beginning"] = False

        # Create an instance of the replication object
        rpl = Replication(main, subordinate, rpl_options)

        if use_rpl_setup:
            # Check server ids
            errors = rpl.check_server_ids()
            for error in errors:
                self._report(error, logging.ERROR, True)

            # Check for server_id uniqueness
            errors = rpl.check_server_uuids()
            for error in errors:
                self._report(error, logging.ERROR, True)

            # Check InnoDB compatibility
            errors = rpl.check_innodb_compatibility(self.options)
            for error in errors:
                self._report(error, logging.ERROR, True)

            # Checking storage engines
            errors = rpl.check_storage_engines(self.options)
            for error in errors:
                self._report(error, logging.ERROR, True)

            # Check main for binary logging
            errors = rpl.check_main_binlog()
            if not errors == []:
                raise UtilRplError(errors[0])

            # Setup replication
            if not rpl.setup(self.rpl_user, 10):
                msg = "Cannot setup replication."
                self._report(msg, logging.CRITICAL, False)
                raise UtilRplError(msg)
        else:
            # Parse user and password (support login-paths)
            try:
                (r_user, r_pass,) = parse_user_password(self.rpl_user)
            except FormatError:
                raise UtilError (USER_PASSWORD_FORMAT.format("--rpl-user"))

            # Switch main and start subordinate
            subordinate.switch_main(main, r_user, r_pass)
            subordinate.start({'fetch': False})

        # Disconnect from servers
        main.disconnect()
        subordinate.disconnect()

    def _switch_main(self, main_vals, use_rpl_setup=True):
        """Switches replication to a new main.

        This method stops replication with the old main if exists and
        starts the replication with a new one.

        main_vals[in]      Main server connection dictionary.
        use_rpl_setup[in]    Used to control the first pass in the mains
                             round-robin scheduling.
        """
        if self.topology:
            # Stop subordinate
            main = self._get_main()
            if main.is_alive():
                main.disconnect()
            subordinate = self._get_subordinate()
            if not subordinate.is_alive() and not self._reconnect_server(subordinate):
                msg = "Failed to connect to the subordinate."
                self._report(msg, logging.CRITICAL, False)
                raise UtilRplError(msg)
            subordinate.stop()
            subordinate.disconnect()

        self._report("# Switching to main '{0}:{1}'."
                     "".format(main_vals["host"],
                               main_vals["port"]), logging.INFO, True)

        try:
            # Setup replication on the new main
            self._setup_replication(main_vals, use_rpl_setup)

            # Create a Topology object
            self.topology = Topology(main_vals, [self.subordinate_vals],
                                     self.options)
        except UtilError as err:
            msg = "Error while switching main: {0}".format(err.errmsg)
            self._report(msg, logging.CRITICAL, False)
            raise UtilRplError(err.errmsg)

        # Only works for GTID_MODE=ON
        if not self.topology.gtid_enabled():
            msg = ("Topology must support global transaction ids and have "
                   "GTID_MODE=ON.")
            self._report(msg, logging.CRITICAL, False)
            raise UtilRplError(msg)

        # Check for mixing IP and hostnames
        if not self._check_host_references():
            print("# WARNING: {0}".format(HOST_IP_WARNING))
            self._report(HOST_IP_WARNING, logging.WARN, False)

    def _report(self, message, level=logging.INFO, print_msg=True):
        """Log message if logging is on.

        This method will log the message presented if the log is turned on.
        Specifically, if options['log_file'] is not None. It will also
        print the message to stdout.

        message[in]      Message to be printed.
        level[in]        Level of message to log. Default = INFO.
        print_msg[in]    If True, print the message to stdout. Default = True.
        """
        # First, print the message.
        if print_msg and not self.quiet:
            print(message)
        # Now log message if logging turned on
        if self.logging:
            logging.log(int(level), message.strip("#").strip(" "))

    def _format_health_data(self):
        """Return health data from topology.

        Returns tuple - (columns, rows).
        """
        if self.topology:
            try:
                health_data = self.topology.get_health()
                current_main = self._get_main()

                # Get data for the remaining mains
                for main_vals in self.mains_vals:
                    # Discard the current main
                    if main_vals["host"] == current_main.host and \
                       main_vals["port"] == current_main.port:
                        continue

                    # Connect to the main
                    conn_dict = {
                        "conn_info": main_vals,
                        "quiet": True,
                        "verbose": self.verbosity > 0,
                    }
                    main = Main(conn_dict)
                    main.connect()

                    # Get main health
                    rpl_health = main.check_rpl_health()

                    main_data = [
                        main.host,
                        main.port,
                        "MASTER",
                        get_server_state(main, main.host, 3,
                                         self.verbosity > 0),
                        main.supports_gtid(),
                        "OK" if rpl_health[0] else ", ".join(rpl_health[1]),
                    ]

                    # Get main status
                    main_status = main.get_status()
                    if len(main_status):
                        main_log, main_log_pos = main_status[0][0:2]
                    else:
                        main_log = None
                        main_log_pos = 0

                    # Show additional details if verbosity is turned on
                    if self.verbosity > 0:
                        main_data.extend([main.get_version(), main_log,
                                            main_log_pos, "", "", "", "", "",
                                            "", "", "", ""])
                    health_data[1].append(main_data)
                return health_data
            except UtilError as err:
                msg = "Cannot get health data: {0}".format(err)
                self._report(msg, logging.ERROR, False)
                raise UtilRplError(msg)
        return ([], [])

    def _format_uuid_data(self):
        """Return the server's uuids.

        Returns tuple - (columns, rows).
        """
        if self.topology:
            try:
                return (_GEN_UUID_COLS, self.topology.get_server_uuids())
            except UtilError as err:
                msg = "Cannot get UUID data: {0}".format(err)
                self._report(msg, logging.ERROR, False)
                raise UtilRplError(msg)
        return ([], [])

    def _format_gtid_data(self):
        """Return the GTID information from the topology.

        Returns tuple - (columns, rows).
        """
        if self.topology:
            try:
                return (_GEN_GTID_COLS, self.topology.get_gtid_data())
            except UtilError as err:
                msg = "Cannot get GTID data: {0}".format(err)
                self._report(msg, logging.ERROR, False)
                raise UtilRplError(msg)
        return ([], [])

    def _log_data(self, title, labels, data, print_format=True):
        """Helper method to log data.

        title[in]     Title to log.
        labels[in]    List of labels.
        data[in]      List of data rows.
        """
        self._report("# {0}".format(title), logging.INFO)
        for row in data:
            msg = ", ".join(
                ["{0}: {1}".format(*col) for col in zip(labels, row)]
            )
            self._report("# {0}".format(msg), logging.INFO, False)
        if print_format:
            print_list(sys.stdout, self.format, labels, data)

    def _log_main_status(self, main):
        """Logs the main information.

        main[in]    Main server instance.

        This method logs the main information from SHOW MASTER STATUS.
        """
        # If no main present, don't print anything.
        if main is None:
            return

        print("#")
        self._report("# {0}:".format("Current Main Information"),
                     logging.INFO)

        try:
            status = main.get_status()[0]
        except UtilError:
            msg = "Cannot get main status"
            self._report(msg, logging.ERROR, False)
            raise UtilRplError(msg)

        cols = ("Binary Log File", "Position", "Binlog_Do_DB",
                "Binlog_Ignore_DB")
        rows = (status[0] or "N/A", status[1] or "N/A", status[2] or "N/A",
                status[3] or "N/A")

        print_list(sys.stdout, self.format, cols, [rows])

        self._report("# {0}".format(
            ", ".join(["{0}: {1}".format(*item) for item in zip(cols, rows)]),
        ), logging.INFO, False)

        # Display gtid executed set
        main_gtids = []
        for gtid in status[4].split("\n"):
            if gtid:
                # Add each GTID to a tuple to match the required format to
                # print the full GRID list correctly.
                main_gtids.append((gtid.strip(","),))

        try:
            if len(main_gtids) > 1:
                gtid_executed = "{0}[...]".format(main_gtids[0][0])
            else:
                gtid_executed = main_gtids[0][0]
        except IndexError:
            gtid_executed = "None"

        self._report("# GTID Executed Set: {0}".format(gtid_executed),
                     logging.INFO)

    def stop_replication(self):
        """Stops multi-source replication.

        Stop the subordinate if topology is available.
        """
        if self.topology:
            # Get the subordinate instance
            subordinate = self._get_subordinate()
            # If subordinate is not connected, try to reconnect and stop replication
            if self._reconnect_server(subordinate):
                subordinate.stop()
                subordinate.disconnect()
        if self.daemon:
            self._report("Multi-source replication daemon stopped.",
                         logging.INFO, False)
        else:
            print("")
            self._report("# Multi-source replication stopped.",
                         logging.INFO, True)

    def stop(self):
        """Stops the daemon.

        Stop subordinate if topology is available and then stop the daemon.
        """
        self.stop_replication()
        super(ReplicationMultiSource, self).stop()

    def run(self):
        """Run the multi-source replication using the round-robin scheduling.

        This method implements the multi-source replication by using time
        slices for each main.
        """
        num_mains = len(self.mains_vals)
        use_rpl_setup = True

        while True:
            # Round-robin scheduling on the mains
            for idx in range(num_mains):
                # Get the new main values and switch for the next one
                try:
                    main_vals = self.mains_vals[idx]
                    self._switch_main(main_vals, use_rpl_setup)
                except UtilError as err:
                    msg = ("Error while switching main: {0}"
                           "".format(err.errmsg))
                    self._report(msg, logging.CRITICAL, False)
                    raise UtilRplError(msg)

                # Get the new main and subordinate instances
                main = self._get_main()
                subordinate = self._get_subordinate()

                switchover_timeout = time.time() + self.switchover_interval

                while switchover_timeout > time.time():
                    # If servers not connected, try to reconnect
                    if not self._reconnect_server(main):
                        msg = ("Failed to connect to the main '{0}:{1}'."
                               "".format(main_vals["host"],
                                         main_vals["port"]))
                        self._report(msg, logging.CRITICAL, False)
                        raise UtilRplError(msg)

                    if not self._reconnect_server(subordinate):
                        msg = "Failed to connect to the subordinate."
                        self._report(msg, logging.CRITICAL, False)
                        raise UtilRplError(msg)

                    # Report
                    self._log_main_status(main)
                    if "health" in self.report_values:
                        (health_labels, health_data,) = \
                            self._format_health_data()
                        if health_data:
                            print("#")
                            self._log_data("Health Status:", health_labels,
                                           health_data)
                    if "gtid" in self.report_values:
                        (gtid_labels, gtid_data,) = self._format_gtid_data()
                        for i, row in enumerate(gtid_data):
                            if row:
                                print("#")
                                self._log_data("GTID Status - {0}"
                                               "".format(_GTID_LISTS[i]),
                                               gtid_labels, row)

                    if "uuid" in self.report_values:
                        (uuid_labels, uuid_data,) = self._format_uuid_data()
                        if uuid_data:
                            print("#")
                            self._log_data("UUID Status:", uuid_labels,
                                           uuid_data)

                    # Disconnect servers
                    main.disconnect()
                    subordinate.disconnect()

                    # Wait for reporting interval
                    time.sleep(self.interval)

            # Use Replication.setup() only for the first round
            use_rpl_setup = False
