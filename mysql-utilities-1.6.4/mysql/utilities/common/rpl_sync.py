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
This file contains features to check the data consistency in a replication
topology (i.e., between the main and its subordinates, or only subordinates), providing
synchronization features to perform the check over the (supposed) same data of
a system with replication active (running).
"""
import re
import sys

from multiprocessing.pool import ThreadPool

from mysql.utilities.command.dbcompare import diff_objects, get_common_objects
from mysql.utilities.common.database import Database
from mysql.utilities.common.gtid import (get_last_server_gtid,
                                         gtid_set_cardinality,
                                         gtid_set_union)
from mysql.utilities.common.messages import (ERROR_USER_WITHOUT_PRIVILEGES,
                                             ERROR_ANSI_QUOTES_MIX_SQL_MODE)
from mysql.utilities.common.pattern_matching import convertSQL_LIKE2REGEXP
from mysql.utilities.common.sql_transform import quote_with_backticks
from mysql.utilities.common.topology import Topology
from mysql.utilities.common.user import User
from mysql.utilities.exception import UtilError

# Regular expression to handle the server version format.
_RE_VERSION_FORMAT = r'^(\d+\.\d+(\.\d+)*).*$'


class RPLSynchronizer(object):
    """Class to manage the features of the replication synchronization checker.

    The RPLSynchronizer class is used to manage synchronization check between
    servers of a replication topology, namely between the main and its
    subordinates or only between subordinates. It provides functions to determine the
    subordinates missing transactions (i.e., missing GTIDs) and check data
    consistency.
    """

    def __init__(self, main_cnx_dic, subordinates_cnx_dic_lst, options):
        """Constructor.

        options[in]       dictionary of options (e.g., discover, timeouts,
                          verbosity).
        """
        self._verbosity = options.get('verbosity')
        self._rpl_timeout = options.get('rpl_timeout')
        self._checksum_timeout = options.get('checksum_timeout')
        self._interval = options.get('interval')

        self._rpl_topology = Topology(main_cnx_dic, subordinates_cnx_dic_lst,
                                      options)
        self._subordinates = self._rpl_topology.get_subordinates_dict()

        # Verify all the servers in the topology has or does not sql_mode set 
        # to 'ANSI_QUOTES'.
        match_group, unmatch_group = \
            self._rpl_topology.get_servers_with_different_sql_mode(
                'ANSI_QUOTES'
            )
        # List and Raise an error if just some of the server has sql_mode set 
        # to 'ANSI_QUOTES' instead of all or none.
        if match_group and unmatch_group:
            sql_mode = match_group[0].select_variable("SQL_MODE")
            if sql_mode == '':
                sql_mode = '""'
            sql_mode = sql_mode.replace(',', ', ')
            print("# The SQL mode in the following servers is set to "
                  "ANSI_QUOTES: {0}".format(sql_mode))
            for server in match_group:
                sql_mode = server.select_variable("SQL_MODE")
                if sql_mode == '':
                    sql_mode = '""'
                sql_mode = sql_mode.replace(',', ', ')
                print("# {0}:{1} sql_mode={2}"
                      "".format(server.host, server.port, sql_mode))
            print("# The SQL mode in the following servers is not set to "
                  "ANSI_QUOTES:")
            for server in unmatch_group:
                sql_mode = server.select_variable("SQL_MODE")
                if sql_mode == '':
                    sql_mode = '""'
                print("# {0}:{1} sql_mode={2}"
                      "".format(server.host, server.port, sql_mode))

            raise UtilError(ERROR_ANSI_QUOTES_MIX_SQL_MODE.format(
                utility='mysqlrplsync'
            ))

        # Set base server used as reference for comparisons.
        self._base_server = None
        self._base_server_key = None
        self._set_base_server()

        # Check user permissions to perform the consistency check.
        self._check_privileges()

        # Check usage of replication filters.
        self._main_rpl_filters = {}
        self._subordinates_rpl_filters = {}
        self._check_rpl_filters()

    def _set_base_server(self):
        """Set the base server used for comparison in the internal state.

        Set the main if used or the first subordinate from the topology as the
        base server. The base server is the one used as a reference for
        comparison with the others. This method sets two instance variables:
        _base_server with the Server instance, and _base_server_key with the
        string identifying the server (format: 'host@port').

        Note: base server might need to be changed (set again) if it is
        removed from the topology for some reason (e.g. GTID disabled).
        """
        main = self._get_main()
        self._base_server = main if main \
            else self._rpl_topology.subordinates[0]['instance']
        self._base_server_key = "{0}@{1}".format(self._base_server.host,
                                                 self._base_server.port)

    def _get_subordinate(self, subordinate_key):
        """Get the subordinate server instance for the specified key 'host@port'.

        This function retrieves the Server instance of for a subordinate from the
        internal state by specifying the key that uniquely identifies it,
        i.e. 'host@port'.

        subordinate_key[in]   String with the format 'host@port' that uniquely
                        identifies a server.

        Returns a Server instance of the subordinate with the specified key value
        (i.e., 'host@port').
        """
        subordinate_dict = self._subordinates[subordinate_key]
        return subordinate_dict['instance']

    def _get_main(self):
        """Get the main server instance.

        This function retrieves the Server instance of the main (in the
        replication topology).

        Returns a Server instance of the main.
        """
        return self._rpl_topology.main

    def _check_privileges(self):
        """Check required privileges to perform the synchronization check.

        This method check if the used users for the main and subordinates possess
        the required privileges to perform the synchronization check. More
        specifically, the following privileges are required:
            - on the main: SUPER or REPLICATION CLIENT, LOCK TABLES and
                             SELECT;
            - on subordinates: SUPER and SELECT.
        An exception is thrown if users doesn't have enough privileges.
        """
        if self._verbosity:
            print("# Checking users permission to perform consistency check.\n"
                  "#")

        # Check privileges for main.
        main_priv = [('SUPER', 'REPLICATION CLIENT'), ('LOCK TABLES',),
                       ('SELECT',)]
        main_priv_str = "SUPER or REPLICATION CLIENT, LOCK TABLES and SELECT"
        if self._get_main():
            server = self._get_main()
            user_obj = User(server, "{0}@{1}".format(server.user, server.host))
            for any_priv_tuple in main_priv:
                has_privilege = any(
                    [user_obj.has_privilege('*', '*', priv)
                        for priv in any_priv_tuple]
                )
                if not has_privilege:
                    raise UtilError(ERROR_USER_WITHOUT_PRIVILEGES.format(
                        user=server.user, host=server.host, port=server.port,
                        operation='perform the synchronization check',
                        req_privileges=main_priv_str
                    ))

        # Check privileges for subordinates.
        subordinate_priv = [('SUPER',), ('SELECT',)]
        subordinate_priv_str = "SUPER and SELECT"
        for subordinate_key in self._subordinates:
            server = self._get_subordinate(subordinate_key)
            user_obj = User(server, "{0}@{1}".format(server.user, server.host))
            for any_priv_tuple in subordinate_priv:
                has_privilege = any(
                    [user_obj.has_privilege('*', '*', priv)
                        for priv in any_priv_tuple]
                )
                if not has_privilege:
                    raise UtilError(
                        "User '{0}' on '{1}@{2}' does not have sufficient "
                        "privileges to perform the synchronization check "
                        "(required: {3}).".format(server.user, server.host,
                                                  server.port, subordinate_priv_str)
                    )

    def _check_rpl_filters(self):
        """Check usage of replication filters.

        Check the usage of replication filtering option on the main (if
        defined) and subordinates, and set the internal state with the found options
        (to check later).
        """
        # Get binlog filtering option for the main.
        if self._get_main():
            m_filters = self._get_main().get_binlog_exceptions()
            if m_filters:
                # Set filtering option for main.
                self._main_rpl_filters['binlog_do_db'] = \
                    m_filters[0][1].split(',') if m_filters[0][1] else None
                self._main_rpl_filters['binlog_ignore_db'] = \
                    m_filters[0][2].split(',') if m_filters[0][2] else None

        # Get replication filtering options for each subordinate.
        for subordinate_key in self._subordinates:
            subordinate = self._get_subordinate(subordinate_key)
            s_filters = subordinate.get_subordinate_rpl_filters()
            if s_filters:
                # Handle known server issues with some replication filters,
                # leading to inconsistent GTID sets. Sync not supported for
                # server with those issues.
                issues = [(0, 'replicate_do_db'), (1, 'replicate_ignore_db'),
                          (4, 'replicate_wild_do_table')]
                for index, rpl_opt in issues:
                    if s_filters[index]:
                        raise UtilError(
                            "Use of {0} option is not supported. There is a "
                            "known issue with the use this replication filter "
                            "and GTID for some server versions. Issue "
                            "detected for '{1}'.".format(rpl_opt, subordinate_key))
                # Set map (dictionary) with the subordinate filtering options.
                filters_map = {
                    'replicate_do_db':
                    s_filters[0].split(',') if s_filters[0] else None,
                    'replicate_ignore_db':
                    s_filters[1].split(',') if s_filters[1] else None,
                    'replicate_do_table':
                    s_filters[2].split(',') if s_filters[2] else None,
                    'replicate_ignore_table':
                    s_filters[3].split(',') if s_filters[3] else None,
                }
                # Handle wild-*-table filters differently to create
                # corresponding regexp.
                if s_filters[4]:
                    wild_list = s_filters[4].split(',')
                    filters_map['replicate_wild_do_table'] = wild_list
                    # Create auxiliary list with compiled regexp to match.
                    regexp_list = []
                    for wild in wild_list:
                        regexp = re.compile(convertSQL_LIKE2REGEXP(wild))
                        regexp_list.append(regexp)
                    filters_map['regexp_do_table'] = regexp_list
                else:
                    filters_map['replicate_wild_do_table'] = None
                    filters_map['regexp_do_table'] = None
                if s_filters[5]:
                    wild_list = s_filters[5].split(',')
                    filters_map['replicate_wild_ignore_table'] = wild_list
                    # Create auxiliary list with compiled regexp to match.
                    regexp_list = []
                    for wild in wild_list:
                        regexp = re.compile(convertSQL_LIKE2REGEXP(wild))
                        regexp_list.append(regexp)
                    filters_map['regexp_ignore_table'] = regexp_list
                else:
                    filters_map['replicate_wild_ignore_table'] = None
                    filters_map['regexp_ignore_table'] = None
                # Set filtering options for the subordinate.
                self._subordinates_rpl_filters[subordinate_key] = filters_map

        # Print warning if filters are found.
        if self._main_rpl_filters or self._subordinates_rpl_filters:
            print("# WARNING: Replication filters found on checked "
                  "servers. This can lead data consistency issues "
                  "depending on how statements are evaluated.\n"
                  "# More information: "
                  "http://dev.mysql.com/doc/en/replication-rules.html")
            if self._verbosity:
                # Print filter options in verbose mode.
                if self._main_rpl_filters:
                    print("# Main '{0}@{1}':".format(
                        self._get_main().host, self._get_main().port
                    ))
                    for rpl_filter in self._main_rpl_filters:
                        if self._main_rpl_filters[rpl_filter]:
                            print("#   - {0}: {1}".format(
                                rpl_filter,
                                ', '.join(
                                    self._main_rpl_filters[rpl_filter]
                                )
                            ))
                if self._subordinates_rpl_filters:
                    for subordinate_key in self._subordinates_rpl_filters:
                        print("# Subordinate '{0}':".format(subordinate_key))
                        filters_map = self._subordinates_rpl_filters[subordinate_key]
                        for rpl_filter in filters_map:
                            if (rpl_filter.startswith('replicate')
                                    and filters_map[rpl_filter]):
                                print("#   - {0}: {1}".format(
                                    rpl_filter,
                                    ', '.join(filters_map[rpl_filter])
                                ))

    def _is_rpl_filtered(self, db_name, tbl_name=None, subordinate=None):
        """ Check if the given object is to be filtered by replication.

        This method checks if the given database or table name is
        supposed to be filtered by replication (i.e., not replicated),
        according to the defined replication filters for the main or
        the specified subordinate.

        db_name[in]     Name of the database to check (not backtick quoted) or
                        associated to the table to check..
        tbl_name[in]    Name of the table to check (not backtick quoted).
                        Table level filtering rules are only checked if this
                        value is not None. By default None, meaning that only
                        the database level rules are checked.
        subordinate[in]       Identification of the subordinate in the format 'host@port'
                        to check, determining which filtering rules will be
                        checked. If None only the main filtering rules are
                        checked, otherwise the rule of the specified subordinates
                        are used. By default: None.

        Returns a boolean value indicating if the given database or table is
        supposed to be filtered by the replication  or not. More precisely,
        if True then updates associated to the object are (supposedly) not
        replicated, otherwise they are replicated.
        """
        def match_regexp(name, regex_list):
            """ Check if 'name' matches one of the regex in the given list.
            """
            for regex in regex_list:
                if regex.match(name):
                    return True
            return False

        # Determine object to check and set full qualified name.
        is_db = tbl_name is None
        obj_name = db_name if is_db else '{0}.{1}'.format(db_name, tbl_name)

        # Match replication filter for Main.
        if not subordinate and is_db and self._main_rpl_filters:
            if self._main_rpl_filters['binlog_do_db']:
                if obj_name in self._main_rpl_filters['binlog_do_db']:
                    return False
                else:
                    return True
            elif self._main_rpl_filters['binlog_ignore_db']:
                if obj_name in self._main_rpl_filters['binlog_ignore_db']:
                    return True

        # Match replication filters for the specified subordinate.
        if subordinate and subordinate in self._subordinates_rpl_filters:
            rpl_filter = self._subordinates_rpl_filters[subordinate]
            if is_db:
                if rpl_filter['replicate_do_db']:
                    if obj_name in rpl_filter['replicate_do_db']:
                        return False
                    else:
                        return True
                elif (rpl_filter['replicate_ignore_db']
                        and obj_name in rpl_filter['replicate_ignore_db']):
                    return True
            else:
                if (rpl_filter['replicate_do_table']
                        and obj_name in rpl_filter['replicate_do_table']):
                    return False
                if (rpl_filter['replicate_ignore_table']
                        and obj_name in rpl_filter['replicate_ignore_table']):
                    return True
                if (rpl_filter['replicate_wild_do_table']
                        and match_regexp(obj_name,
                                         rpl_filter['regexp_do_table'])):
                    return False
                if (rpl_filter['replicate_wild_ignore_table']
                        and match_regexp(obj_name,
                                         rpl_filter['regexp_ignore_table'])):
                    return True
                if (rpl_filter['replicate_do_table']
                        or rpl_filter['replicate_wild_do_table']):
                    return True

        # Do not filter replication for object (if no filter rule matched).
        return False

    def _apply_for_all_subordinates(self, subordinates, function, args=(), kwargs=None,
                              multithreading=False):
        """Apply specified function to all given subordinates.

        This function allow the execution (concurrently or not) of the
        specified function with the given arguments on all the specified
        subordinates.

        subordinates[in]          List of subordinates to apply the function. It is assumed
                            that the list is composed by strings with the
                            format 'host@port', identifying each subordinate.
        function[in]        Name of the function (string) to apply on all
                            subordinates.
        args[in]            Tuple with all the function arguments (except
                            keyword arguments).
        kwargs[in]          Dictionary with all the function keyword arguments.
        multithreading[in]  Boolean value indicating if the function will be
                            applied concurrently on all subordinates. By default
                            False, no concurrency.

        Return a list of tuples composed by two elements: a string identifying
        the subordinate ('host@port') and the result of the execution of the target
        function for the corresponding subordinate.
        """
        if kwargs is None:
            kwargs = {}
        if multithreading:
            # Create a pool of threads to execute the method for each subordinate.
            pool = ThreadPool(processes=len(subordinates))
            thread_res_lst = []
            for subordinate_key in subordinates:
                subordinate = self._get_subordinate(subordinate_key)
                thread_res = pool.apply_async(getattr(subordinate, function), args,
                                              kwargs)
                thread_res_lst.append((subordinate_key, thread_res))
            pool.close()
            # Wait for all threads to finish here to avoid RuntimeErrors when
            # waiting for the result of a thread that is already dead.
            pool.join()
            # Get the result from each subordinate and return the results.
            res = []
            for subordinate_key, thread_res in thread_res_lst:
                res.append((subordinate_key, thread_res.get()))
            return res
        else:
            res = []
            for subordinate_key in subordinates:
                subordinate = self._get_subordinate(subordinate_key)
                subordinate_res = getattr(subordinate, function)(*args, **kwargs)
                res.append((subordinate_key, subordinate_res))
            return res

    def check_server_versions(self):
        """Check server versions.

        Check all server versions and report version differences.
        """
        srv_versions = {}
        # Get the server version of the main if used.
        main = self._get_main()
        if main:
            main_version = main.get_version()
            match = re.match(_RE_VERSION_FORMAT, main_version.strip())
            if match:
                # Add .0 as release version if not provided.
                if not match.group(2):
                    main_version = "{0}.0".format(match.group(1))
                else:
                    main_version = match.group(1)
            main_id = '{0}@{1}'.format(main.host, main.port)
            # Store the main version.
            srv_versions[main_version] = [main_id]

        # Get the server version for all subordinates.
        for subordinate_key in self._subordinates:
            subordinate = self._get_subordinate(subordinate_key)
            version = subordinate.get_version()
            match = re.match(_RE_VERSION_FORMAT, version.strip())
            if match:
                # Add .0 as release version if not provided.
                if not match.group(2):
                    version = "{0}.0".format(match.group(1))
                else:
                    version = match.group(1)
            # Store the subordinate version.
            if version in srv_versions:
                srv_versions[version].append(subordinate_key)
            else:
                srv_versions[version] = [subordinate_key]

        # Check the servers versions and issue a warning if different.
        if len(srv_versions) > 1:
            print("# WARNING: Servers using different versions:")
            for version in srv_versions:
                servers_str = ",".join(srv_versions[version])
                print("# - {0} for {1}.".format(version, servers_str))
            print("#")

    def check_gtid_sync(self):
        """Check GTIDs synchronization.

        Perform several GTID checks (enabled and errant transactions). If the
        main is available (was specified) then it also checks if GTIDs are
        in sync between main and its subordinates and report the amount of
        transaction (i.e., GTIDs) behind the main for each subordinate.

        GTID differences might be an indicator of the existence of data
        consistency issues.

        Note: The main may not be specified, its use is not mandatory.
        """
        # Check if GTIDs are enabled on the topology.
        if self._get_main():  # Use of Main is not mandatory.
            # GTIDs must be enabled on the main.
            if self._get_main().supports_gtid().upper() != 'ON':
                raise UtilError(
                    "Main must support GTIDs and have GTID_MODE=ON."
                )
        # Skip subordinates without GTID enabled and warn user.
        reset_base_srv = False
        for subordinate_key, subordinate_dict in self._subordinates.items():
            subordinate = subordinate_dict['instance']
            support_gtid = subordinate.supports_gtid().upper()
            if support_gtid != 'ON':
                reason = "GTID_MODE=OFF" if support_gtid == 'OFF' \
                    else "not support GTIDs"
                print("# WARNING: Subordinate '{0}' will be skipped - "
                      "{1}.".format(subordinate_key, reason))
                print("#")
                del self._subordinates[subordinate_key]
                self._rpl_topology.remove_subordinate(subordinate_dict)
                if subordinate_key == self._base_server_key:
                    reset_base_srv = True
        # At least on subordinate must have GTIDs enabled.
        if len(self._subordinates) == 0:
            raise UtilError("No subordinates found with GTID support and "
                            "GTID_MODE=ON.")
        # Reset base server if needed (it must have GTID_MODE=ON).
        if reset_base_srv:
            self._set_base_server()

        # Check the set of executed GTIDs and report differences, only if the
        # main is specified.
        if self._get_main():
            main_gtids = self._get_main().get_gtid_executed()
            subordinates_gtids_data = \
                self._rpl_topology.subordinates_gtid_subtract_executed(
                    main_gtids, multithreading=True
                )
            print("#\n# GTID differences between Main and Subordinates:")
            for host, port, gtids_missing in subordinates_gtids_data:
                subordinate_key = '{0}@{1}'.format(host, port)
                gtid_size = gtid_set_cardinality(gtids_missing)
                if gtid_size:
                    plural = 's' if gtid_size > 1 else ''
                    print("# - Subordinate '{0}' is {1} transaction{2} behind "
                          "Main.".format(subordinate_key, gtid_size, plural))
                    if self._verbosity:
                        print("#       Missing GTIDs: "
                              "{0}".format(gtids_missing))
                else:
                    print("# - Subordinate '{0}' is up-to-date.".format(subordinate_key))

        print("#")

    @staticmethod
    def _exist_in_obj_list(obj_name, obj_type, obj_list):
        """Check if object (name and type) exists in the given list.

        This function checks if the database object for the specified name and
        type exists in the specified list of database objects.

        obj_name[in]    Name of the object to check.
        obj_type[in]    Type of the object to check.
        obj_list[in]    List of objects to check. It is assumed that the list
                        has the format of the ones returned by the function
                        mysql.utilities.command.dbcompare.get_common_objects().
                        More precisely with the format:
                        [(obj_type1, (obj_name1,))..(obj_typeN, (obj_nameN,))]

        Returns a boolean value indicating if object with the specified name
        and type exists in the specified list of objects.
        """
        for obj_row in obj_list:
            if obj_row[0] == obj_type and obj_row[1][0] == obj_name:
                return True
        return False

    def _split_active_subordinates(self, subordinates):
        """Get the list of subordinates with replication running and not.

        This method separates the list of given subordinates into active (with the
        IO and SQL thread running) and non active subordinates (with one of the
        threads stopped).

        subordinates[in]      List of target subordinates to separate.

        Returns a tuple with two elements, first with the list of active subordinates
        and the second with the list of not active ones.
        """
        # Get subordinates status.
        subordinates_state = self._apply_for_all_subordinates(subordinates, 'get_subordinates_errors',
                                                  multithreading=True)

        # Store IO and SQL thread status.
        active_subordinates = []
        not_active_subordinates = []
        for subordinate_key, state in subordinates_state:
            # Locally store IO and SQL threads status.
            io_running = state[3].upper() == 'YES'
            self._subordinates[subordinate_key]['IO_Running'] = io_running
            sql_running = state[4].upper() == 'YES'
            self._subordinates[subordinate_key]['SQL_Running'] = sql_running
            if io_running and sql_running:
                active_subordinates.append(subordinate_key)
            else:
                not_active_subordinates.append(subordinate_key)
                print("#   WARNING: Subordinate not active '{0}' - "
                      "Sync skipped.".format(subordinate_key))
                if self._verbosity:
                    # Print warning if subordinate is stopped due to an error.
                    if not io_running and state[2]:
                        print("#    - IO thread stopped: ERROR {0} - "
                              "{1}".format(state[1], state[2]))
                    if not sql_running and state[6]:
                        print("#    - SQL thread stopped: ERROR {0} - "
                              "{1}".format(state[5], state[6]))

        # Return separated list of active and non active replication subordinates.
        return active_subordinates, not_active_subordinates

    def _compute_sync_point(self, active_subordinates=None, main_uuid=None):
        """Compute the GTID synchronization point.

        This method computes the GTID synchronization point based based on the
        GTID_EXECUTED set. If a main is available for synchronization the
        last GTID from the GTID_EXECUTED set is used as sync point  If no
        main is available the union of the GTID_EXECUTED sets among all
        active subordinates is used as the sync point.

        active_subordinates[in]   List of active subordinates to consider. Only required
                            if the main is not available. It is assumed
                            that the list is composed by strings with the
                            format 'host@port', identifying each subordinate.
        main_uuid[in]     UUID of the main server used to compute its last
                            GTID (sync point). If not provided it is
                            determined, but can lead to issues for servers
                            >= 5.7.6 if specific tables are locked previously.

        Return a GTID set representing to synchronization point (to wait for
        subordinates to catch up and stop).
        """
        if self._get_main():
            gtid_set = self._get_main().get_gtid_executed()
            main_uuid = main_uuid if main_uuid \
                else self._get_main().get_server_uuid()
            return get_last_server_gtid(gtid_set, main_uuid)
        else:
            # Get GTID_EXECUTED on all subordinates.
            all_gtid_executed = self._apply_for_all_subordinates(
                active_subordinates, 'get_gtid_executed', multithreading=True
            )

            # Compute the union of all GTID sets for each UUID among subordinates.
            gtid_sets_by_uuid = {}
            for _, gtid_executed in all_gtid_executed:
                gtids_list = gtid_executed.split("\n")
                for gtid in gtids_list:
                    gtid_set = gtid.rstrip(', ')
                    uuid = gtid_set.split(':')[0]
                    if uuid not in gtid_sets_by_uuid:
                        gtid_sets_by_uuid[uuid] = gtid_set
                    else:
                        union_set = gtid_set_union(gtid_sets_by_uuid[uuid],
                                                   gtid_set)
                        gtid_sets_by_uuid[uuid] = union_set

            # Return union of all know executed GTID.
            return ",".join(gtid_sets_by_uuid.itervalues())

    def _sync_subordinates(self, subordinates, gtid):
        """Set synchronization point (specified GTID set) for the given subordinates.

        The method set the synchronization point for the given subordinates by
        (concurrently) stopping and immediately executing START SLAVE UNTIL
        on all given subordinates in order to stop upon reaching the given GTID set
        (i.e., committing all corresponding transactions for the given GTID
        sync point).

        subordinates[in]      List of target subordinates to synchronize (i.e., instruct
                        to stop upon reaching the synchronization point).
        gtid[in]        GTID set used as the synchronization point.
        """
        # Make running subordinates stop until sync point (GTID) is reached.
        if self._verbosity:
            print("#   Setting data synchronization point for subordinates.")
        # STOP subordinate (only SQL thread).
        self._apply_for_all_subordinates(subordinates, 'stop_sql_thread',
                                   multithreading=True)
        # START subordinate UNTIL sync point is reached.
        # Note: Only the SQL thread is stopped when the condition is reached.
        until_ops = {'until_gtid_set': gtid, 'sql_after_gtid': True,
                     'only_sql_thread': True}
        self._apply_for_all_subordinates(subordinates, 'start', (), until_ops,
                                   multithreading=True)

    def _checksum_and_resume_rpl(self, not_sync_subordinates, sync_subordinate, table):
        """Checksum table and resume replication on subordinates.

        This method computes (concurrently) the table checksum of the given
        subordinates lists (those synced and not synced). For the list of not synced
        subordinates the table checksum is immediately computed. For the list of
        synced subordinates, first it waits for them to catch up and the sync point
        and only then compute the table checksum and resume replication.

        not_sync_subordinates[in] List of not synced subordinates.
        sync_subordinate[in]      List of (previously) synced subordinates.
        table[in]           Target table to compute the checksum.

        Returns a list of tuples, each tuple containing the identification of
        the server and the corresponding checksum result.
        """
        if self._verbosity:
            print("#   Compute checksum on subordinates (wait to catch up and resume"
                  " replication).")
            sys.stdout.flush()
        not_sync_checksum = []
        if not_sync_subordinates:
            not_sync_checksum = self._apply_for_all_subordinates(
                not_sync_subordinates, 'checksum_table', (table,),
                {'exec_timeout': self._checksum_timeout},
                multithreading=True
            )
        sync_checksum = []
        if sync_subordinate:
            sync_checksum = self._apply_for_all_subordinates(
                sync_subordinate, 'wait_checksum_and_start', (table,),
                {'wait_timeout': self._rpl_timeout,
                 'wait_interval': self._interval,
                 'checksum_timeout': self._checksum_timeout},
                multithreading=True
            )
        return not_sync_checksum + sync_checksum

    def _check_table_data_sync(self, table, subordinates):
        """Check table data synchronization for specified subordinates.

        This method check the data consistency for the specified table between
        the base server (main or subordinate) and the specified salves. This
        operation requires the definition of a "synchronization point" in order
        to ensure that the "supposed" same data is compared between servers.
        This coordination process is based on GTIDs (checking that all data
        until a given GTID has been processed on the subordinates). A different
        algorithm is used to set the "synchronization point" depending if the
        main is used or not. The data consistency is checked relying on the
        CHECKSUM TABLE query.

        If an error occur during this process, any locked table must be
        unlocked and both main and subordinates should resume their previous
        activity.

        Important note: this method assumes that the table exists on the base
        server and all specified subordinates, therefore checking the existence of
        the table as well as other integrity checks (server versions, GTID
        definitions, etc.) need to be performed outside the scope of this
        method.

        table[in]       Qualified name of the table to check (quoted with
                        backticks).
        subordinates[in]      List of subordinates to check. Each element of the list must
                        be a string with the format 'host@port'.

        Returns the number of data consistency found.
        """
        success = False
        checksum_issues = 0
        # If no main used then add base server (subordinate) to subordinates to sync.
        if not self._get_main():
            subordinates = subordinates + [self._base_server_key]

        # Separate active from non active subordinates.
        active_subordinates, not_active_subordinates = self._split_active_subordinates(subordinates)

        if self._get_main():
            # Get uuid of the main server
            main_uuid = self._get_main().get_server_uuid()

            # Lock the table on the main to get GTID synchronization point
            # and perform the table checksum.
            try:
                self._get_main().exec_query(
                    "LOCK TABLES {0} READ".format(table)
                )

                last_exec_gtid = self._compute_sync_point(
                        main_uuid=main_uuid)
                if self._verbosity > 2:
                    print("#   Sync point GTID: {0}".format(last_exec_gtid))

                # Immediately instruct active subordinates to stop on sync point.
                if active_subordinates:
                    self._sync_subordinates(active_subordinates, last_exec_gtid)

                # Perform table checksum on main.
                base_server_checksum = self._get_main().checksum_table(
                    table, self._checksum_timeout
                )
                if base_server_checksum[0]:
                    success = True  # Successful checksum for base server.
                    if self._verbosity > 2:
                        print("#   Checksum on base server (Main): "
                              "{0}".format(base_server_checksum[0][1]))
                else:
                    print("#   [SKIP] {0} checksum on base server (Main) - "
                          "{1}".format(table, base_server_checksum[1]))
            finally:
                # Unlock table.
                self._get_main().exec_query("UNLOCK TABLES")
        elif active_subordinates:
            # Perform sync without main, only based on active subordinate (if any).
            try:
                # Stop all active subordinates to get the GTID synchronization point.
                self._apply_for_all_subordinates(
                    active_subordinates, 'stop_sql_thread', multithreading=True
                )

                sync_gtids = self._compute_sync_point(active_subordinates)
                if self._verbosity > 2:
                    print("#   Sync point GTID: {0}".format(sync_gtids))

                # Instruct active subordinates to stop on sync point.
                self._sync_subordinates(active_subordinates, sync_gtids)

            except UtilError:
                # Try to restart the subordinates in case an error occurs.
                self._apply_for_all_subordinates(
                    active_subordinates, 'star_sql_thread', multithreading=True
                )

        # Compute checksum on all subordinates and return to previous state.
        subordinates_checksum = self._checksum_and_resume_rpl(not_active_subordinates,
                                                        active_subordinates, table)

        # Check if checksum for base server was successfully computed.
        if not self._get_main():
            for subordinate_key, checksum in subordinates_checksum:
                if subordinate_key == self._base_server_key:
                    if checksum[0]:
                        success = True  # Successful checksum for base server.
                        base_server_checksum = checksum
                        subordinates_checksum.remove((subordinate_key, checksum))
                        if self._verbosity > 2:
                            print("#   Checksum on base server: "
                                  "{0}".format(base_server_checksum[0][1]))
                    else:
                        print("#   [SKIP] {0} checksum on base server - "
                              "{1}".format(table, checksum[1]))
                    break

        # Compare checksum and report results.
        if success and subordinates_checksum:
            for subordinate_key, checksum_res in subordinates_checksum:
                if checksum_res[0] is None:
                    print("#   [SKIP] {0} checksum for Subordinate '{1}' - "
                          "{2}.".format(table, subordinate_key, checksum_res[1]))
                else:
                    if self._verbosity > 2:
                        checksum_val = ': {0}'.format(checksum_res[0][1])
                    else:
                        checksum_val = ''
                    if checksum_res[0] != base_server_checksum[0]:
                        print("#   [DIFF] {0} checksum for server '{1}'"
                              "{2}.".format(table, subordinate_key, checksum_val))
                        checksum_issues += 1
                    else:
                        print("#   [OK] {0} checksum for server '{1}'"
                              "{2}.".format(table, subordinate_key, checksum_val))

        return checksum_issues

    def check_data_sync(self, options, data_to_include, data_to_exclude):
        """Check data synchronization.

        Check if the data (in all tables) is in sync between the checked
        servers (main and its subordinates, or only subordinates). It reports structure
        difference database/tables missing or with a different definition and
        data differences between a base server and the others.

        Note: A different algorithm is applied to perform the synchronization,
        depending if the main is specified (available) or not.

        options[in]         Dictionary of options.
        data_to_include[in] Dictionary of data (set of tables) by database to
                            check.
        data_to_exclude[in] Dictionary of data (set of tables) by database to
                            exclude from check.

        Returns the number of consistency issues found (comparing database
        definitions and data).
        """
        issues_count = 0

        # Skip all database objects, except tables.
        options['skip_views'] = True
        options['skip_triggers'] = True
        options['skip_procs'] = True
        options['skip_funcs'] = True
        options['skip_events'] = True
        options['skip_grants'] = True

        diff_options = {}
        diff_options.update(options)
        diff_options['quiet'] = True  # Do not print messages.
        diff_options['suppress_sql'] = True  # Do not print SQL statements.
        diff_options['skip_table_opts'] = True  # Ignore AUTO_INCREMENT diffs.

        # Check the server version requirement to support sync features.
        # Subordinate servers of version >= 5.6.14 are required due to a known issue
        # for START SLAVE UNTIL with the SQL_AFTER_GTIDS option. More info:
        # https://dev.mysql.com/doc/refman/5.6/en/start-slave.html
        for subordinate_key in self._subordinates:
            if not self._get_subordinate(subordinate_key).check_version_compat(5, 6, 14):
                raise UtilError(
                    "Server '{0}' version must be 5.6.14 or greater. Sync is "
                    "not supported for versions prior to 5.6.14 due to a "
                    "known issue with START SLAVE UNTIL and the "
                    "SQL_AFTER_GTIDS option.".format(subordinate_key))

        print("# Checking data consistency.\n#")
        base_srv_type = 'Main' if self._get_main() else 'Subordinate'
        print("# Using {0} '{1}' as base server for comparison."
              "".format(base_srv_type, self._base_server_key))

        # Get all databases from the base server.
        db_rows = self._base_server.get_all_databases()
        base_server_dbs = set([row[0] for row in db_rows])

        # Process databases to include/exclude from check.
        db_to_include = set()
        if data_to_include:
            db_to_include = set([db for db in data_to_include])
            base_server_dbs = base_server_dbs & db_to_include
            not_exist_db = db_to_include - base_server_dbs
            if not_exist_db:
                plurals = ('s', '') if len(not_exist_db) > 1 else ('', 'es')
                print('# WARNING: specified database{0} to check do{1} not '
                      'exist on base server and will be skipped: '
                      '{2}.'.format(plurals[0], plurals[1],
                                    ", ".join(not_exist_db)))
        db_to_exclude = set()
        if data_to_exclude:
            db_to_exclude = set(
                [db for db in data_to_exclude if not data_to_exclude[db]]
            )
            base_server_dbs = base_server_dbs - db_to_exclude

        # Check databases on subordinates (except the base server).
        subordinates_except_base = [key for key in self._subordinates
                              if key != self._base_server_key]
        for subordinate_key in subordinates_except_base:
            subordinate = self._get_subordinate(subordinate_key)
            db_rows = subordinate.get_all_databases()
            subordinate_dbs = set([row[0] for row in db_rows])
            # Process databases to include/exclude.
            if db_to_include:
                subordinate_dbs = subordinate_dbs & db_to_include
            if db_to_exclude:
                subordinate_dbs = subordinate_dbs - db_to_exclude
            # Add subordinate databases set to internal state.
            self._subordinates[subordinate_key]['databases'] = subordinate_dbs
            # Report databases not on base server and filtered by replication.
            dbs_not_in_base_srv = subordinate_dbs - base_server_dbs
            filtered_dbs = set(
                [db for db in dbs_not_in_base_srv
                    if self._is_rpl_filtered(db, subordinate=self._base_server_key)]
            )
            dbs_not_in_base_srv -= filtered_dbs
            for db in filtered_dbs:
                print("# [SKIP] Database '{0}' - filtered by replication "
                      "rule on base server.".format(db))
            if dbs_not_in_base_srv:
                issues_count += len(dbs_not_in_base_srv)
                plural = 's' if len(dbs_not_in_base_srv) > 1 else ''
                print("# [DIFF] Database{0} NOT on base server but found on "
                      "'{1}': {2}".format(plural, subordinate_key,
                                          ",".join(dbs_not_in_base_srv)))

        # Determine server to check base replication filtering options.
        filter_srv = None if self._get_main() else self._base_server_key

        # Check data consistency for each table on the base server.
        for db_name in base_server_dbs:
            # Skip database if filtered by defined replication rules.
            if self._is_rpl_filtered(db_name, subordinate=filter_srv):
                print("# [SKIP] Database '{0}' check - filtered by "
                      "replication rule.".format(db_name))
                continue
            print("# Checking '{0}' database...".format(db_name))
            subordinates_to_check = {}
            # Check if database exists on subordinates (except the base server).
            for subordinate_key in subordinates_except_base:
                # Skip database if filtered by defined replication rules.
                if self._is_rpl_filtered(db_name, subordinate=subordinate_key):
                    print("# [SKIP] Database '{0}' check for '{1}' - filtered "
                          "by replication rule.".format(db_name, subordinate_key))
                    continue
                if db_name in self._subordinates[subordinate_key]['databases']:
                    # Store subordinate database instance and common objects.
                    subordinate_db = Database(self._get_subordinate(subordinate_key), db_name,
                                        options)
                    subordinate_db.init()
                    subordinate_dic = {'db': subordinate_db}
                    in_both, in_basesrv, not_in_basesrv = get_common_objects(
                        self._base_server, self._get_subordinate(subordinate_key),
                        db_name, db_name, False, options)
                    # Process tables to include/exclude from check (on subordinates).
                    if (data_to_include and db_name in data_to_include
                            and data_to_include[db_name]):
                        in_both = [
                            obj_row for obj_row in in_both
                            if obj_row[1][0] in data_to_include[db_name]
                        ]
                        in_basesrv = [
                            obj_row for obj_row in in_basesrv
                            if obj_row[1][0] in data_to_include[db_name]
                        ]
                        not_in_basesrv = [
                            obj_row for obj_row in not_in_basesrv
                            if obj_row[1][0] in data_to_include[db_name]
                        ]
                    if (data_to_exclude and db_name in data_to_exclude
                            and data_to_exclude[db_name]):
                        in_both = [
                            obj_row for obj_row in in_both
                            if obj_row[1][0] not in data_to_exclude[db_name]
                        ]
                        in_basesrv = [
                            obj_row for obj_row in in_basesrv
                            if obj_row[1][0] not in data_to_exclude[db_name]
                        ]
                        not_in_basesrv = [
                            obj_row for obj_row in not_in_basesrv
                            if obj_row[1][0] not in data_to_exclude[db_name]
                        ]
                    subordinate_dic['in_both'] = in_both
                    subordinate_dic['in_basesrv'] = in_basesrv
                    subordinates_to_check[subordinate_key] = subordinate_dic
                    # Report tables not on base server and filtered by
                    # replication.
                    tbls_not_in = set(
                        [obj_row[1][0] for obj_row in not_in_basesrv
                         if obj_row[0] == 'TABLE']
                    )
                    filtered_tbls = set(
                        [tbl for tbl in tbls_not_in if self._is_rpl_filtered(
                            db_name, tbl_name=tbl, subordinate=self._base_server_key
                        )]
                    )
                    tbls_not_in -= filtered_tbls
                    for tbl in filtered_tbls:
                        print("# [SKIP] Table '{0}' - filtered by replication "
                              "rule on base server.".format(tbl))
                    if tbls_not_in:
                        plural = 's' if len(tbls_not_in) > 1 else ''
                        print("#   [DIFF] Table{0} NOT on base server but "
                              "found on '{1}': "
                              "{2}".format(plural, subordinate_key,
                                           ", ".join(tbls_not_in)))
                        issues_count += len(tbls_not_in)
                else:
                    print("#   [DIFF] Database '{0}' NOT on server "
                          "'{1}'.".format(db_name, subordinate_key))
                    issues_count += 1
            # Only check database if at least one subordinate has it.
            if subordinates_to_check:
                db = Database(self._base_server, db_name, options)
                db.init()
                for db_obj in db.get_next_object():
                    obj_type = db_obj[0]
                    obj_name = db_obj[1][0]
                    # Process tables to include/exclude from check (on base
                    # server).
                    if (data_to_include and data_to_include[db_name]
                            and obj_name not in data_to_include[db_name]):
                        # Skip to the next object if not in data to include.
                        continue
                    if (data_to_exclude and data_to_exclude[db_name]
                            and obj_name in data_to_exclude[db_name]):
                        # Skip to the next object if in data to exclude.
                        continue
                    checksum_task = []
                    # Check object data on all valid subordinates.
                    for subordinate_key in subordinates_to_check:
                        # Skip table if filtered by defined replication rules.
                        if (obj_type == 'TABLE'
                            and self._is_rpl_filtered(db_name, obj_name,
                                                      subordinate=subordinate_key)):
                            print("# [SKIP] Table '{0}' check for '{1}' - "
                                  "filtered by replication rule."
                                  "".format(obj_name, subordinate_key))
                            continue
                        subordinate_dic = subordinates_to_check[subordinate_key]
                        # Check if object does not exist on Subordinate.
                        if self._exist_in_obj_list(obj_name, obj_type,
                                                   subordinate_dic['in_basesrv']):
                            print("#   [DIFF] {0} '{1}.{2}' NOT on server "
                                  "'{3}'.".format(obj_type.capitalize(),
                                                  db_name, obj_name,
                                                  subordinate_key))
                            issues_count += 1
                            continue

                        # Quote object name with backticks.
                        q_obj = '{0}.{1}'.format(
                            quote_with_backticks(db_name, db.sql_mode),
                            quote_with_backticks(obj_name, db.sql_mode)
                        )

                        # Check object definition.
                        def_diff = diff_objects(
                            self._base_server, self._get_subordinate(subordinate_key),
                            q_obj, q_obj, diff_options, obj_type
                        )
                        if def_diff:
                            print("#   [DIFF] {0} {1} definition is "
                                  "different on '{2}'."
                                  "".format(obj_type.capitalize(), q_obj,
                                            subordinate_key))
                            issues_count += 1
                            if self._verbosity:
                                for diff in def_diff[3:]:
                                    print("#       {0}".format(diff))
                            continue

                        # Add subordinate to table checksum task.
                        checksum_task.append(subordinate_key)

                    # Perform table checksum on valid subordinates.
                    if checksum_task and obj_type == 'TABLE':
                        print("# - Checking '{0}' table data..."
                              "".format(obj_name))
                        num_issues = self._check_table_data_sync(q_obj,
                                                                 checksum_task)
                        issues_count += num_issues

        print("#\n#...done.\n#")
        str_issues_count = 'No' if issues_count == 0 else str(issues_count)
        plural = 's' if issues_count > 1 else ''
        print("# SUMMARY: {0} data consistency issue{1} found.\n"
              "#".format(str_issues_count, plural))
        return issues_count
