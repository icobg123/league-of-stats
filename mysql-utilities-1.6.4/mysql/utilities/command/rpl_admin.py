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
This file contains the replication administration tools for managine a
simple main-to-subordinates topology.
"""

import logging
import os
import sys
import time

from datetime import datetime, timedelta

from mysql.utilities.exception import UtilRplError
from mysql.utilities.common.gtid import gtid_set_itemize
from mysql.utilities.common.ip_parser import hostname_is_ip
from mysql.utilities.common.messages import (ERROR_SAME_MASTER,
                                             ERROR_USER_WITHOUT_PRIVILEGES,
                                             HOST_IP_WARNING,
                                             EXTERNAL_SCRIPT_DOES_NOT_EXIST,
                                             INSUFFICIENT_FILE_PERMISSIONS)
from mysql.utilities.common.tools import ping_host, execute_script
from mysql.utilities.common.format import print_list
from mysql.utilities.common.topology import Topology
from mysql.utilities.command.failover_console import FailoverConsole
from mysql.utilities.command.failover_daemon import FailoverDaemon

_VALID_COMMANDS_TEXT = """
Available Commands:

  elect       - perform best subordinate election and report best subordinate
  failover    - conduct failover from main to best subordinate
  gtid        - show status of global transaction id variables
                also displays uuids for all servers
  health      - display the replication health
  reset       - stop and reset all subordinates
  start       - start all subordinates
  stop        - stop all subordinates
  switchover  - perform subordinate promotion

  Note:
        elect, gtid and health require --main and either
        --subordinates or --discover-subordinates-login;

        failover requires --subordinates;

        switchover requires --main, --new-main and either
        --subordinates or --discover-subordinates-login;

        start, stop and reset require --subordinates (and --main is optional)

"""

_VALID_COMMANDS = ["elect", "failover", "gtid", "health", "reset", "start",
                   "stop", "switchover"]
_SLAVE_COMMANDS = ["reset", "start", "stop"]
_MASTER_COLS = ["Host", "Port", "Binary Log File", "Position"]
_SLAVE_COLS = ["Host", "Port", "Main Log File", "Position", "Seconds Behind"]
_GTID_COLS = ["host", "port", "role", "gtid"]

_FAILOVER_ERROR = "%sCheck server for errors and run the mysqlrpladmin " + \
                  "utility to perform manual failover."
_FAILOVER_ERRNO = 911

_DATE_FORMAT = '%Y-%m-%d %H:%M:%S %p'
_DATE_LEN = 22

_ERRANT_TNX_ERROR = "Errant transaction(s) found on subordinate(s)."

_GTID_ON_REQ = "{action} requires GTID_MODE=ON for all servers."

WARNING_SLEEP_TIME = 10


def get_valid_rpl_command_text():
    """Provide list of valid command descriptions to caller.
    """
    return _VALID_COMMANDS_TEXT


def get_valid_rpl_commands():
    """Provide list of valid commands to caller.
    """
    return _VALID_COMMANDS


def purge_log(filename, age):
    """Purge old log entries

    This method deletes rows from the log file older than the age specified
    in days.

    filename[in]       filename of log fil
    age[in]            age in days

    Returns bool - True = success, Fail = error reading/writing log file
    """
    if not os.path.exists(filename):
        print "NOTE: Log file '%s' does not exist. Will be created." % filename
        return True

    # Read a row, check age. If > today + age, delete row.
    # Ignore user markups and other miscellaneous entries.
    try:
        log = open(filename, "r")
        log_entries = log.readlines()
        log.close()
        threshold = datetime.now() - timedelta(days=age)
        start = 0
        for row in log_entries:
            # Check age here
            try:
                row_time = time.strptime(row[0:_DATE_LEN], _DATE_FORMAT)
                row_age = datetime(*row_time[:6])
                if row_age < threshold:
                    start += 1
                elif start == 0:
                    return True
                else:
                    break
            except:
                start += 1    # Remove invalid formatted lines
        log = open(filename, "w")
        log.writelines(log_entries[start:])
        log.close()
    except:
        return False
    return True


def skip_subordinates_trx(gtid_set, subordinates_cnx_val, options):
    """Skip transactions on subordinates.

    This method skips the given transactions (GTID set) on all the specified
    subordinates. That is, an empty transaction is injected for each GTID in
    the given set for one of each subordinates. In case a subordinate already has an
    executed transaction for a given GTID then that GTID is ignored for this
    subordinate.

    gtid_set[in]            String representing the set of GTIDs to skip.
    subordinates_cnx_val[in]      List of the dictionaries with the connection
                            values for each target subordinate.
    options[in]             Dictionary of options (dry_run, verbosity).

    Throws an UtilError exception if an error occurs during the execution.
    """
    verbosity = options.get('verbosity')
    dryrun = options.get('dry_run')

    # Connect to subordinates.
    rpl_topology = Topology(None, subordinates_cnx_val, options)

    # Check required privileges.
    errors = rpl_topology.check_privileges(skip_main=True)
    if errors:
        err_details = ''
        for err in errors:
            err_msg = ERROR_USER_WITHOUT_PRIVILEGES.format(
                user=err[0], host=err[1], port=err[2],
                operation='inject empty transactions', req_privileges=err[3])
            err_details = '{0}{1}\n'.format(err_details, err_msg)
        err_details.strip()
        raise UtilRplError("Not enough privileges.\n{0}".format(err_details))

    # GTID must be enabled on all servers.
    srv_list = rpl_topology.get_servers_with_gtid_not_on()
    if srv_list:
        if verbosity:
            print("# Subordinates with GTID not enabled:")
            for srv in srv_list:
                msg = "#  - GTID_MODE={0} on {1}:{2}".format(srv[2], srv[0],
                                                             srv[1])
                print(msg)
        raise UtilRplError(_GTID_ON_REQ.format(action='Transaction skip'))

    if dryrun:
        print("#")
        print("# WARNING: Executing utility in dry run mode (read only).")

    # Get GTID set that can be skipped, i.e., not in GTID_EXECUTED.
    gtids_by_subordinate = rpl_topology.subordinates_gtid_subtract_executed(gtid_set)

    # Output GTID set that will be skipped.
    print("#")
    print("# GTID set to be skipped for each server:")
    has_gtid_to_skip = False
    for host, port, gtids_to_skip in gtids_by_subordinate:
        if not gtids_to_skip:
            gtids_to_skip = 'None'
        else:
            # Set flag to indicate that there is at least one GTID to skip.
            has_gtid_to_skip = True
        print("# - {0}@{1}: {2}".format(host, port, gtids_to_skip))

    # Create dictionary to directly access the subordinates instances.
    subordinates_dict = rpl_topology.get_subordinates_dict()

    # Skip transactions for the given list of subordinates.
    print("#")
    if has_gtid_to_skip:
        for host, port, gtids_to_skip in gtids_by_subordinate:
            if gtids_to_skip:
                # Decompose GTID set into a list of single transactions.
                gtid_items = gtid_set_itemize(gtids_to_skip)
                dryrun_mark = '(dry run) ' if dryrun else ''
                print("# {0}Injecting empty transactions for '{1}:{2}'"
                      "...".format(dryrun_mark, host, port))
                subordinate_key = '{0}@{1}'.format(host, port)
                subordinate_srv = subordinates_dict[subordinate_key]['instance']
                for uuid, trx_list in gtid_items:
                    for trx_num in trx_list:
                        trx_to_skip = '{0}:{1}'.format(uuid, trx_num)
                        if verbosity:
                            print("# - {0}".format(trx_to_skip))
                        if not dryrun:
                            # Inject empty transaction.
                            subordinate_srv.inject_empty_trx(
                                trx_to_skip, gtid_next_automatic=False)
                if not dryrun:
                    subordinate_srv.set_gtid_next_automatic()
    else:
        print("# No transaction to skip.")
    print("#\n#...done.\n#")


class RplCommands(object):
    """Replication commands.

    This class supports the following replication commands.

    elect       - perform best subordinate election and report best subordinate
    failover    - conduct failover from main to best subordinate as specified
                  by the user. This option performs best subordinate election.
    gtid        - show status of global transaction id variables
    health      - display the replication health
    reset       - stop and reset all subordinates
    start       - start all subordinates
    stop        - stop all subordinates
    switchover  - perform subordinate promotion as specified by the user to a
                  specific subordinate. Requires --main and the --candidate
                  options.
    """

    def __init__(self, main_vals, subordinate_vals, options,
                 skip_conn_err=True):
        """Constructor

        main_vals[in]    main server connection dictionary
        subordinate_vals[in]     list of subordinate server connection dictionaries
        options[in]        options dictionary
        skip_conn_err[in]  if True, do not fail on connection failure
                           Default = True
        """
        # A sys.stdout copy, that can be used later to turn on/off stdout
        self.stdout_copy = sys.stdout
        self.stdout_devnull = open(os.devnull, "w")

        # Disable stdout when running --daemon with start, stop or restart
        daemon = options.get("daemon")
        if daemon:
            if daemon in ("start", "nodetach"):
                print("Starting failover daemon...")
            elif daemon == "stop":
                print("Stopping failover daemon...")
            else:
                print("Restarting failover daemon...")
            # Disable stdout if daemon not nodetach
            if daemon != "nodetach":
                sys.stdout = self.stdout_devnull

        self.main = None
        self.main_vals = main_vals
        self.options = options
        self.quiet = self.options.get("quiet", False)
        self.logging = self.options.get("logging", False)
        self.candidates = self.options.get("candidates", None)
        self.verbose = self.options.get("verbose", None)
        self.rpl_user = self.options.get("rpl_user", None)
        self.ssl_ca = options.get("ssl_ca", None)
        self.ssl_cert = options.get("ssl_cert", None)
        self.ssl_key = options.get("ssl_key", None)
        if self.ssl_ca or self.ssl_cert or self.ssl_key:
            self.ssl = True

        try:
            self.topology = Topology(main_vals, subordinate_vals, self.options,
                                     skip_conn_err)
        except Exception as err:
            if daemon and daemon != "nodetach":
                # Turn on sys.stdout
                sys.stdout = self.stdout_copy
            raise UtilRplError(str(err))

    def _report(self, message, level=logging.INFO, print_msg=True):
        """Log message if logging is on

        This method will log the message presented if the log is turned on.
        Specifically, if options['log_file'] is not None. It will also
        print the message to stdout.

        message[in]    message to be printed
        level[in]      level of message to log. Default = INFO
        print_msg[in]  if True, print the message to stdout. Default = True
        """
        # First, print the message.
        if print_msg and not self.quiet:
            print message
        # Now log message if logging turned on
        if self.logging:
            logging.log(int(level), message.strip("#").strip(' '))

    def _show_health(self):
        """Run a command on a list of subordinates.

        This method will display the replication health of the topology. This
        includes the following for each server.

          - host       : host name
          - port       : connection port
          - role       : "MASTER" or "SLAVE"
          - state      : UP = connected, WARN = cannot connect but can ping,
                         DOWN = cannot connect nor ping
          - gtid       : ON = gtid supported and turned on, OFF = supported
                         but not enabled, NO = not supported
          - rpl_health : (main) binlog enabled,
                         (subordinate) IO tread is running, SQL thread is running,
                         no errors, subordinate delay < max_delay,
                         read log pos + max_position < main's log position
                         Note: Will show 'ERROR' if there are multiple
                         errors encountered otherwise will display the
                         health check that failed.

        If verbosity is set, it will show the following additional information.

          (main)
            - server version, binary log file, position

          (subordinates)
            - server version, main's binary log file, main's log position,
              IO_Thread, SQL_Thread, Secs_Behind, Remaining_Delay,
              IO_Error_Num, IO_Error
        """
        fmt = self.options.get("format", "grid")
        quiet = self.options.get("quiet", False)

        cols, rows = self.topology.get_health()

        if not quiet:
            print "#"
            print "# Replication Topology Health:"

        # Print health report
        print_list(sys.stdout, fmt, cols, rows)

        return

    def _show_gtid_data(self):
        """Display the GTID lists from the servers.

        This method displays the three GTID lists for all of the servers. Each
        server is listed with its entries in each list. If a list has no
        entries, that list is not printed.
        """
        if not self.topology.gtid_enabled():
            self._report("# WARNING: GTIDs are not supported on this "
                         "topology.", logging.WARN)
            return

        fmt = self.options.get("format", "grid")

        # Get UUIDs
        uuids = self.topology.get_server_uuids()
        if len(uuids):
            print "#"
            print "# UUIDS for all servers:"
            print_list(sys.stdout, fmt, ['host', 'port', 'role', 'uuid'],
                       uuids)

        # Get GTID lists
        executed, purged, owned = self.topology.get_gtid_data()
        if len(executed):
            print "#"
            print "# Transactions executed on the server:"
            print_list(sys.stdout, fmt, _GTID_COLS, executed)
        if len(purged):
            print "#"
            print "# Transactions purged from the server:"
            print_list(sys.stdout, fmt, _GTID_COLS, purged)
        if len(owned):
            print "#"
            print "# Transactions owned by another server:"
            print_list(sys.stdout, fmt, _GTID_COLS, owned)

    def _check_host_references(self):
        """Check to see if using all host or all IP addresses

        Returns bool - True = all references are consistent
        """

        uses_ip = hostname_is_ip(self.topology.main.host)
        for subordinate_dict in self.topology.subordinates:
            subordinate = subordinate_dict['instance']
            if subordinate is not None:
                host_port = subordinate.get_main_host_port()
                host = None
                if host_port:
                    host = host_port[0]
                if (not host or uses_ip != hostname_is_ip(subordinate.host) or
                   uses_ip != hostname_is_ip(host)):
                    return False
        return True

    def _switchover(self):
        """Perform switchover from main to candidate subordinate

        This method switches the role of main to a candidate subordinate. The
        candidate is specified via the --candidate option.

        Returns bool - True = no errors, False = errors reported.
        """
        # Check new main is not actual main - need valid candidate
        candidate = self.options.get("new_main", None)
        if (self.topology.main.is_alias(candidate['host']) and
           self.main_vals['port'] == candidate['port']):
            err_msg = ERROR_SAME_MASTER.format(candidate['host'],
                                               candidate['port'],
                                               self.main_vals['host'],
                                               self.main_vals['port'])
            self._report(err_msg, logging.WARN)
            self._report(err_msg, logging.CRITICAL)
            raise UtilRplError(err_msg)

        # Check for --main-info-repository=TABLE if rpl_user is None
        if not self._check_main_info_type():
            return False

        # Check for mixing IP and hostnames
        if not self._check_host_references():
            print("# WARNING: {0}".format(HOST_IP_WARNING))
            self._report(HOST_IP_WARNING, logging.WARN, False)

        # Check prerequisites
        if candidate is None:
            msg = "No candidate specified."
            self._report(msg, logging.CRITICAL)
            raise UtilRplError(msg)

        # Can only check errant transactions if GTIDs are enabled.
        if self.topology.gtid_enabled():
            # Check existence of errant transactions on subordinates
            errant_tnx = self.topology.find_errant_transactions()
            if errant_tnx:
                force = self.options.get('force')
                print("# ERROR: {0}".format(_ERRANT_TNX_ERROR))
                self._report(_ERRANT_TNX_ERROR, logging.ERROR, False)
                for host, port, tnx_set in errant_tnx:
                    errant_msg = (" - For subordinate '{0}@{1}': "
                                  "{2}".format(host, port, ", ".join(tnx_set)))
                    print("# {0}".format(errant_msg))
                    self._report(errant_msg, logging.ERROR, False)
                # Raise an exception (to stop) if tolerant mode is OFF
                if not force:
                    raise UtilRplError("{0} Note: If you want to ignore this "
                                       "issue, although not advised, please "
                                       "use the utility with the --force "
                                       "option.".format(_ERRANT_TNX_ERROR))
        else:
            warn_msg = ("Errant transactions check skipped (GTID not enabled "
                        "for the whole topology).")
            print("# WARNING: {0}".format(warn_msg))
            self._report(warn_msg, logging.WARN, False)

        self._report(" ".join(["# Performing switchover from main at",
                     "%s:%s" % (self.main_vals['host'],
                                self.main_vals['port']),
                               "to subordinate at %s:%s." %
                               (candidate['host'], candidate['port'])]))
        if not self.topology.switchover(candidate):
            self._report("# Errors found. Switchover aborted.", logging.ERROR)
            return False

        return True

    def _elect_subordinate(self):
        """Perform best subordinate election

        This method determines which subordinate is the best candidate for
        GTID-enabled failover. If called for a non-GTID topology, a warning
        is issued.
        """
        if not self.topology.gtid_enabled():
            warn_msg = _GTID_ON_REQ.format(action='Subordinate election')
            print("# WARNING: {0}".format(warn_msg))
            self._report(warn_msg, logging.WARN, False)
            return

        # Check for mixing IP and hostnames
        if not self._check_host_references():
            print("# WARNING: {0}".format(HOST_IP_WARNING))
            self._report(HOST_IP_WARNING, logging.WARN, False)

        candidates = self.options.get("candidates", None)
        if candidates is None or len(candidates) == 0:
            self._report("# Electing candidate subordinate from known subordinates.")
        else:
            self._report("# Electing candidate subordinate from candidate list "
                         "then subordinates list.")
        best_subordinate = self.topology.find_best_subordinate(candidates)
        if best_subordinate is None:
            self._report("ERROR: No subordinate found that meets eligilibility "
                         "requirements.", logging.ERROR)
            return

        self._report("# Best subordinate found is located on %s:%s." %
                     (best_subordinate['host'], best_subordinate['port']))

    def _failover(self, strict=False, options=None):
        """Perform failover

        This method executes GTID-enabled failover. If called for a non-GTID
        topology, a warning is issued.

        strict[in]     if True, use only the candidate list for subordinate
                       election and fail if no candidates are viable.
                       Default = False
        options[in]    options dictionary.

        Returns bool - True = failover succeeded, False = errors found
        """
        if options is None:
            options = {}
        srv_list = self.topology.get_servers_with_gtid_not_on()
        if srv_list:
            err_msg = _GTID_ON_REQ.format(action='Subordinate election')
            print("# ERROR: {0}".format(err_msg))
            self._report(err_msg, logging.ERROR, False)
            for srv in srv_list:
                msg = "#  - GTID_MODE={0} on {1}:{2}".format(srv[2], srv[0],
                                                             srv[1])
                self._report(msg, logging.ERROR)

            self._report(err_msg, logging.CRITICAL, False)
            raise UtilRplError(err_msg)

        # Check for --main-info-repository=TABLE if rpl_user is None
        if not self._check_main_info_type():
            return False

        # Check existence of errant transactions on subordinates
        errant_tnx = self.topology.find_errant_transactions()
        if errant_tnx:
            force = options.get('force')
            print("# ERROR: {0}".format(_ERRANT_TNX_ERROR))
            self._report(_ERRANT_TNX_ERROR, logging.ERROR, False)
            for host, port, tnx_set in errant_tnx:
                errant_msg = (" - For subordinate '{0}@{1}': "
                              "{2}".format(host, port, ", ".join(tnx_set)))
                print("# {0}".format(errant_msg))
                self._report(errant_msg, logging.ERROR, False)
            # Raise an exception (to stop) if tolerant mode is OFF
            if not force:
                raise UtilRplError("{0} Note: If you want to ignore this "
                                   "issue, although not advised, please use "
                                   "the utility with the --force option."
                                   "".format(_ERRANT_TNX_ERROR))

        self._report("# Performing failover.")
        if not self.topology.failover(self.candidates, strict,
                                      stop_on_error=True):
            self._report("# Errors found.", logging.ERROR)
            return False
        return True

    def _check_main_info_type(self, halt=True):
        """Check for main information set to TABLE if rpl_user not provided

        halt[in]       if True, raise error on failure. Default is True

        Returns bool - True if rpl_user is specified or False if rpl_user not
                       specified and at least one subordinate does not have
                       --main-info-repository=TABLE.
        """
        error = "You must specify either the --rpl-user or set all subordinates " + \
                "to use --main-info-repository=TABLE."
        # Check for --main-info-repository=TABLE if rpl_user is None
        if self.rpl_user is None:
            if not self.topology.check_main_info_type("TABLE"):
                if halt:
                    raise UtilRplError(error)
                self._report(error, logging.ERROR)
                return False
        return True

    def check_host_references(self):
        """Public method to access self.check_host_references()
        """
        return self._check_host_references()

    def execute_command(self, command, options=None):
        """Execute a replication admin command

        This method executes one of the valid replication administration
        commands as described above.

        command[in]        command to execute
        options[in]        options dictionary.

        Returns bool - True = success, raise error on failure
        """
        if options is None:
            options = {}
        # Raise error if command is not valid
        if command not in _VALID_COMMANDS:
            msg = "'%s' is not a valid command." % command
            self._report(msg, logging.CRITICAL)
            raise UtilRplError(msg)

        # Check privileges
        self._report("# Checking privileges.")
        full_check = command in ['failover', 'elect', 'switchover']
        errors = self.topology.check_privileges(full_check)
        if len(errors):
            msg = "User %s on %s does not have sufficient privileges to " + \
                  "execute the %s command."
            for error in errors:
                self._report(msg % (error[0], error[1], command),
                             logging.CRITICAL)
            raise UtilRplError("Not enough privileges to execute command.")

        self._report("Executing %s command..." % command, logging.INFO, False)

        # Execute the command
        if command in _SLAVE_COMMANDS:
            if command == 'reset':
                self.topology.run_cmd_on_subordinates('stop')
            self.topology.run_cmd_on_subordinates(command)
        elif command in 'gtid':
            self._show_gtid_data()
        elif command == 'health':
            self._show_health()
        elif command == 'switchover':
            self._switchover()
        elif command == 'elect':
            self._elect_subordinate()
        elif command == 'failover':
            self._failover(options=options)
        else:
            msg = "Command '%s' is not implemented." % command
            self._report(msg, logging.CRITICAL)
            raise UtilRplError(msg)

        if command in ['switchover', 'failover'] and \
           not self.options.get("no_health", False):
            self._show_health()

        self._report("# ...done.")

        return True

    def auto_failover(self, interval):
        """Automatic failover

        Wrapper class for running automatic failover. See
        run_automatic_failover for details on implementation.

        This method ensures the registration/deregistration occurs
        regardless of exception or errors.

        interval[in]   time in seconds to wait to check status of servers

        Returns bool - True = success, raises exception on error
        """
        failover_mode = self.options.get("failover_mode", "auto")
        force = self.options.get("force", False)

        # Initialize a console
        console = FailoverConsole(self.topology.main,
                                  self.topology.get_health,
                                  self.topology.get_gtid_data,
                                  self.topology.get_server_uuids,
                                  self.options)

        # Check privileges
        self._report("# Checking privileges.")
        errors = self.topology.check_privileges(failover_mode != 'fail')
        if len(errors):
            for error in errors:
                msg = ("User {0} on {1}@{2} does not have sufficient "
                       "privileges to execute the {3} command "
                       "(required: {4}).").format(error[0], error[1], error[2],
                                                  'failover', error[3])
                print("# ERROR: {0}".format(msg))
                self._report(msg, logging.CRITICAL, False)
            raise UtilRplError("Not enough privileges to execute command.")

        # Unregister existing instances from subordinates
        self._report("Unregistering existing instances from subordinates.",
                     logging.INFO, False)
        console.unregister_subordinates(self.topology)

        # Register instance
        self._report("Registering instance on main.", logging.INFO, False)
        old_mode = failover_mode
        failover_mode = console.register_instance(force)
        if failover_mode != old_mode:
            self._report("Multiple instances of failover console found for "
                         "main %s:%s." % (self.topology.main.host,
                                            self.topology.main.port),
                         logging.WARN)
            print "If this is an error, restart the console with --force. "
            print "Failover mode changed to 'FAIL' for this instance. "
            print "Console will start in 10 seconds.",
            sys.stdout.flush()
            i = 0
            while i < 9:
                time.sleep(1)
                sys.stdout.write('.')
                sys.stdout.flush()
                i += 1
            print "starting Console."
            time.sleep(1)

        try:
            res = self.run_auto_failover(console, failover_mode)
        except:
            raise
        finally:
            try:
                # Unregister instance
                self._report("Unregistering instance on main.", logging.INFO,
                             False)
                console.register_instance(True, False)
                self._report("Failover console stopped.", logging.INFO, False)
            except:
                pass

        return res

    def auto_failover_as_daemon(self):
        """Automatic failover

        Wrapper class for running automatic failover as daemon.

        This method ensures the registration/deregistration occurs
        regardless of exception or errors.

        Returns bool - True = success, raises exception on error
        """
        # Initialize failover daemon
        failover_daemon = FailoverDaemon(self)
        res = None

        try:
            action = self.options.get("daemon")
            if action == "start":
                res = failover_daemon.start()
            elif action == "stop":
                res = failover_daemon.stop()
            elif action == "restart":
                res = failover_daemon.restart()
            else:
                # Start failover deamon in foreground
                res = failover_daemon.start(detach_process=False)
        except:
            try:
                # Unregister instance
                self._report("Unregistering instance on main.", logging.INFO,
                             False)
                failover_daemon.register_instance(True, False)
                self._report("Failover daemon stopped.", logging.INFO, False)
            except:
                pass

        return res

    def run_auto_failover(self, console, failover_mode="auto"):
        """Run automatic failover

        This method implements the automatic failover facility. It uses the
        FailoverConsole class from the failover_console.py to implement all
        user interface commands and uses the existing failover() method of
        this class to conduct failover.

        When the main goes down, the method can perform one of three actions:

        1) failover to list of candidates first then subordinates
        2) failover to list of candidates only
        3) fail

        console[in]    instance of the failover console class.

        Returns bool - True = success, raises exception on error
        """
        pingtime = self.options.get("pingtime", 3)
        exec_fail = self.options.get("exec_fail", None)
        post_fail = self.options.get("post_fail", None)
        pedantic = self.options.get('pedantic', False)
        fail_retry = self.options.get('fail_retry', None)

        # Only works for GTID_MODE=ON
        if not self.topology.gtid_enabled():
            msg = "Topology must support global transaction ids " + \
                  "and have GTID_MODE=ON."
            self._report(msg, logging.CRITICAL)
            raise UtilRplError(msg)

        # Require --main-info-repository=TABLE for all subordinates
        if not self.topology.check_main_info_type("TABLE"):
            msg = "Failover requires --main-info-repository=TABLE for " + \
                  "all subordinates."
            self._report(msg, logging.ERROR, False)
            raise UtilRplError(msg)

        # Check for mixing IP and hostnames
        if not self._check_host_references():
            print("# WARNING: {0}".format(HOST_IP_WARNING))
            self._report(HOST_IP_WARNING, logging.WARN, False)
            print("#\n# Failover console will start in {0} seconds.".format(
                WARNING_SLEEP_TIME))
            time.sleep(WARNING_SLEEP_TIME)

        # Check existence of errant transactions on subordinates
        errant_tnx = self.topology.find_errant_transactions()
        if errant_tnx:
            print("# WARNING: {0}".format(_ERRANT_TNX_ERROR))
            self._report(_ERRANT_TNX_ERROR, logging.WARN, False)
            for host, port, tnx_set in errant_tnx:
                errant_msg = (" - For subordinate '{0}@{1}': "
                              "{2}".format(host, port, ", ".join(tnx_set)))
                print("# {0}".format(errant_msg))
                self._report(errant_msg, logging.WARN, False)
            # Raise an exception (to stop) if pedantic mode is ON
            if pedantic:
                raise UtilRplError("{0} Note: If you want to ignore this "
                                   "issue, please do not use the --pedantic "
                                   "option.".format(_ERRANT_TNX_ERROR))

        self._report("Failover console started.", logging.INFO, False)
        self._report("Failover mode = %s." % failover_mode, logging.INFO,
                     False)

        # Main loop - loop and fire on interval.
        done = False
        first_pass = True
        failover = False
        while not done:
            # Use try block in case main class has gone away.
            try:
                old_host = self.main.host
                old_port = self.main.port
            except:
                old_host = "UNKNOWN"
                old_port = "UNKNOWN"

            # If a failover script is provided, check it else check main
            # using connectivity checks.
            if exec_fail is not None:
                # Execute failover check script
                if not os.path.isfile(exec_fail):
                    message = EXTERNAL_SCRIPT_DOES_NOT_EXIST.format(
                        path=exec_fail)
                    self._report(message, logging.CRITICAL, False)
                    raise UtilRplError(message)
                elif not os.access(exec_fail, os.X_OK):
                    message = INSUFFICIENT_FILE_PERMISSIONS.format(
                        path=exec_fail, permissions='execute')
                    self._report(message, logging.CRITICAL, False)
                    raise UtilRplError(message)
                else:
                    self._report("# Spawning external script for failover "
                                 "checking.")
                    res = execute_script(exec_fail, None,
                                         [old_host, old_port], self.verbose)
                    if res == 0:
                        self._report("# Failover check script completed Ok. "
                                     "Failover averted.")
                    else:
                        self._report("# Failover check script failed. "
                                     "Failover initiated", logging.WARN)
                        failover = True
            else:
                # Check the main. If not alive, wait for pingtime seconds
                # and try again.
                if self.topology.main is not None and \
                   not self.topology.main.is_alive():
                    msg = "Main may be down. Waiting for %s seconds." % \
                          pingtime
                    self._report(msg, logging.INFO, False)
                    time.sleep(pingtime)
                    try:
                        self.topology.main.connect()
                    except:
                        pass

                # If user specified a main fail retry, wait for the
                # predetermined time and attempt to check the main again.
                if fail_retry is not None and \
                   not self.topology.main.is_alive():
                    msg = "Main is still not reachable. Waiting for %s " \
                          "seconds to retry detection." % fail_retry
                    self._report(msg, logging.INFO, False)
                    time.sleep(fail_retry)
                    try:
                        self.topology.main.connect()
                    except:
                        pass

                # Check the main again. If no connection or lost connection,
                # try ping. This performs the timeout threshold for detecting
                # a down main. If still not alive, try to reconnect and if
                # connection fails after 3 attempts, failover.
                if self.topology.main is None or \
                   not ping_host(self.topology.main.host, pingtime) or \
                   not self.topology.main.is_alive():
                    failover = True
                    i = 0
                    while i < 3:
                        try:
                            self.topology.main.connect()
                            failover = False  # Main is now connected again
                            break
                        except:
                            pass
                        time.sleep(pingtime)
                        i += 1

                    if failover:
                        self._report("Failed to reconnect to the main after "
                                     "3 attemps.", logging.INFO)
                    else:
                        self._report("Main is Ok. Resuming watch.",
                                     logging.INFO)

            if failover:
                self._report("Main is confirmed to be down or unreachable.",
                             logging.CRITICAL, False)
                try:
                    self.topology.main.disconnect()
                except:
                    pass
                console.clear()
                if failover_mode == 'auto':
                    self._report("Failover starting in 'auto' mode...")
                    res = self.topology.failover(self.candidates, False)
                elif failover_mode == 'elect':
                    self._report("Failover starting in 'elect' mode...")
                    res = self.topology.failover(self.candidates, True)
                else:
                    msg = _FAILOVER_ERROR % ("Main has failed and automatic "
                                             "failover is not enabled. ")
                    self._report(msg, logging.CRITICAL, False)
                    # Execute post failover script
                    self.topology.run_script(post_fail, False,
                                             [old_host, old_port])
                    raise UtilRplError(msg, _FAILOVER_ERRNO)
                if not res:
                    msg = _FAILOVER_ERROR % ("An error was encountered "
                                             "during failover. ")
                    self._report(msg, logging.CRITICAL, False)
                    # Execute post failover script
                    self.topology.run_script(post_fail, False,
                                             [old_host, old_port])
                    raise UtilRplError(msg)
                self.main = self.topology.main
                console.main = self.main
                self.topology.remove_discovered_subordinates()
                self.topology.discover_subordinates()
                console.list_data = None
                print "\nFailover console will restart in 5 seconds."
                time.sleep(5)
                console.clear()
                failover = False
                # Execute post failover script
                self.topology.run_script(post_fail, False,
                                         [old_host, old_port,
                                          self.main.host, self.main.port])

                # Unregister existing instances from subordinates
                self._report("Unregistering existing instances from subordinates.",
                             logging.INFO, False)
                console.unregister_subordinates(self.topology)

                # Register instance on the new main
                self._report("Registering instance on main.", logging.INFO,
                             False)
                failover_mode = console.register_instance()

            # discover subordinates if option was specified at startup
            elif (self.options.get("discover", None) is not None
                  and not first_pass):
                # Force refresh of health list if new subordinates found
                if self.topology.discover_subordinates():
                    console.list_data = None

            # Check existence of errant transactions on subordinates
            errant_tnx = self.topology.find_errant_transactions()
            if errant_tnx:
                if pedantic:
                    print("# WARNING: {0}".format(_ERRANT_TNX_ERROR))
                    self._report(_ERRANT_TNX_ERROR, logging.WARN, False)
                    for host, port, tnx_set in errant_tnx:
                        errant_msg = (" - For subordinate '{0}@{1}': "
                                      "{2}".format(host, port,
                                                   ", ".join(tnx_set)))
                        print("# {0}".format(errant_msg))
                        self._report(errant_msg, logging.WARN, False)

                    # Raise an exception (to stop) if pedantic mode is ON
                    raise UtilRplError("{0} Note: If you want to ignore this "
                                       "issue, please do not use the "
                                       "--pedantic "
                                       "option.".format(_ERRANT_TNX_ERROR))
                else:
                    if self.logging:
                        warn_msg = ("{0} Check log for more "
                                    "details.".format(_ERRANT_TNX_ERROR))
                    else:
                        warn_msg = _ERRANT_TNX_ERROR
                    console.add_warning('errant_tnx', warn_msg)
                    self._report(_ERRANT_TNX_ERROR, logging.WARN, False)
                    for host, port, tnx_set in errant_tnx:
                        errant_msg = (" - For subordinate '{0}@{1}': "
                                      "{2}".format(host, port,
                                                   ", ".join(tnx_set)))
                        self._report(errant_msg, logging.WARN, False)
            else:
                console.del_warning('errant_tnx')

            res = console.display_console()
            if res is not None:    # None = normal timeout, keep going
                if not res:
                    return False   # Errors detected
                done = True        # User has quit
            first_pass = False

        return True
