import re
from os import path

from pcs import settings
from pcs.common import reports
from pcs.common.reports import ReportProcessor
from pcs.common.reports.item import ReportItem
from pcs.common.services.interfaces import ServiceManagerInterface
from pcs.lib.corosync.config_facade import ConfigFacade as CorosyncConfFacade
from pcs.lib.errors import LibraryError
from pcs.lib.services import is_systemd
from pcs.lib.tools import (
    dict_to_environment_file,
    environment_file_to_dict,
)

DEVICE_INITIALIZATION_OPTIONS_MAPPING = {
    "watchdog-timeout": "-1",
    "allocate-timeout": "-2",
    "loop-timeout": "-3",
    "msgwait-timeout": "-4",
}


def _even_number_of_nodes_and_no_qdevice(
    corosync_conf_facade, node_number_modifier=0
):
    """
    Returns True whenever cluster has no quorum device configured and number of
    nodes + node_number_modifier is even number, False otherwise.

    corosync_conf_facade --
    node_number_modifier -- this value will be added to current number of nodes.
        This can be useful to test whenever is ATB needed when adding/removing
        node.
    """
    return (
        not corosync_conf_facade.has_quorum_device()
        and (len(corosync_conf_facade.get_nodes()) + node_number_modifier) % 2
        == 0
    )


def is_auto_tie_breaker_needed(
    service_manager: ServiceManagerInterface,
    corosync_conf_facade: CorosyncConfFacade,
    node_number_modifier: int = 0,
) -> bool:
    """
    Returns True whenever quorum option auto tie breaker is needed to be enabled
    for proper working of SBD fencing. False if it is not needed.

    service_manager --
    corosync_conf_facade --
    node_number_modifier -- this value vill be added to current number of nodes.
        This can be useful to test whenever is ATB needed when adding/removeing
        node.
    """
    return (
        _even_number_of_nodes_and_no_qdevice(
            corosync_conf_facade, node_number_modifier
        )
        and is_sbd_installed(service_manager)
        and is_sbd_enabled(service_manager)
        and not is_device_set_local()
    )


def atb_has_to_be_enabled_pre_enable_check(corosync_conf_facade):
    """
    Returns True whenever quorum option auto_tie_breaker is needed to be enabled
    for proper working of SBD fencing. False if it is not needed. This function
    doesn't check if sbd is installed nor enabled.
    """
    # fmt: off
    return (
        not corosync_conf_facade.is_enabled_auto_tie_breaker()
        and
        _even_number_of_nodes_and_no_qdevice(corosync_conf_facade)
    )
    # fmt: on


def atb_has_to_be_enabled(
    service_manager: ServiceManagerInterface,
    corosync_conf_facade: CorosyncConfFacade,
    node_number_modifier: int = 0,
) -> bool:
    """
    Return True whenever quorum option auto tie breaker has to be enabled for
    proper working of SBD fencing. False if it's not needed or it is already
    enabled.

    service_manager --
    corosync_conf_facade --
    node_number_modifier -- this value vill be added to current number of nodes.
        This can be useful to test whenever is ATB needed when adding/removeing
        node.
    """
    return (
        not corosync_conf_facade.is_enabled_auto_tie_breaker()
        and is_auto_tie_breaker_needed(
            service_manager, corosync_conf_facade, node_number_modifier
        )
    )


def validate_new_nodes_devices(nodes_devices):
    """
    Validate if SBD devices are set for new nodes when they should be

    dict nodes_devices -- name: node name, key: list of SBD devices
    """
    if is_device_set_local():
        return validate_nodes_devices(
            nodes_devices, adding_nodes_to_sbd_enabled_cluster=True
        )
    return [
        ReportItem.error(
            reports.messages.SbdWithDevicesNotUsedCannotSetDevice(node)
        )
        for node, devices in nodes_devices.items()
        if devices
    ]


def validate_nodes_devices(
    node_device_dict, adding_nodes_to_sbd_enabled_cluster=False
):
    """
    Validates device list for all nodes. If node is present, it checks if there
    is at least one device and at max settings.sbd_max_device_num. Also devices
    have to be specified with absolute path.
    Returns list of ReportItem

    dict node_device_dict -- name: node name, key: list of SBD devices
    bool adding_nodes_to_sbd_enabled_cluster -- provides context to reports
    """
    report_item_list = []
    for node_label, device_list in node_device_dict.items():
        if not device_list:
            report_item_list.append(
                ReportItem.error(
                    reports.messages.SbdNoDeviceForNode(
                        node_label,
                        sbd_enabled_in_cluster=(
                            adding_nodes_to_sbd_enabled_cluster
                        ),
                    )
                )
            )
        elif len(device_list) > settings.sbd_max_device_num:
            report_item_list.append(
                ReportItem.error(
                    reports.messages.SbdTooManyDevicesForNode(
                        node_label, device_list, settings.sbd_max_device_num
                    )
                )
            )
        for device in device_list:
            if not device or not path.isabs(device):
                report_item_list.append(
                    ReportItem.error(
                        reports.messages.SbdDevicePathNotAbsolute(
                            device, node_label
                        )
                    )
                )
    return report_item_list


def create_sbd_config(base_config, node_label, watchdog, device_list=None):
    # TODO: figure out which name/ring has to be in SBD_OPTS
    config = dict(base_config)
    config["SBD_OPTS"] = '"-n {node_name}"'.format(node_name=node_label)
    if watchdog:
        config["SBD_WATCHDOG_DEV"] = watchdog
    if device_list:
        config["SBD_DEVICE"] = '"{0}"'.format(";".join(device_list))
    return dict_to_environment_file(config)


def get_default_sbd_config():
    """
    Returns default SBD configuration as dictionary.
    """
    return {
        "SBD_DELAY_START": "no",
        "SBD_PACEMAKER": "yes",
        "SBD_STARTMODE": "always",
        "SBD_WATCHDOG_DEV": settings.sbd_watchdog_default,
        "SBD_WATCHDOG_TIMEOUT": "5",
    }


def get_local_sbd_config():
    """
    Get local SBD configuration.
    Returns SBD configuration file as string.
    Raises LibraryError on any failure.
    """
    try:
        with open(settings.sbd_config, "r") as sbd_cfg:
            return sbd_cfg.read()
    except EnvironmentError as e:
        raise LibraryError(
            ReportItem.error(
                reports.messages.UnableToGetSbdConfig("local node", str(e))
            )
        ) from e


def get_sbd_service_name(service_manager: ServiceManagerInterface) -> str:
    return "sbd" if is_systemd(service_manager) else "sbd_helper"


def is_sbd_enabled(service_manager: ServiceManagerInterface) -> bool:
    """
    Check if SBD service is enabled in local system.
    Return True if SBD service is enabled, False otherwise.
    """
    return service_manager.is_enabled(get_sbd_service_name(service_manager))


def is_sbd_installed(service_manager: ServiceManagerInterface) -> bool:
    """
    Check if SBD service is installed in local system.
    Reurns True id SBD service is installed. False otherwise.
    """
    return service_manager.is_installed(get_sbd_service_name(service_manager))


def initialize_block_devices(
    report_processor: ReportProcessor, cmd_runner, device_list, option_dict
):
    """
    Initialize devices with specified options in option_dict.
    Raise LibraryError on failure.

    report_processor -- report processor
    cmd_runner -- CommandRunner
    device_list -- list of strings
    option_dict -- dictionary of options and their values
    """
    report_processor.report(
        ReportItem.info(
            reports.messages.SbdDeviceInitializationStarted(device_list)
        )
    )

    cmd = [settings.sbd_binary]
    for device in device_list:
        cmd += ["-d", device]

    for option, value in sorted(option_dict.items()):
        cmd += [DEVICE_INITIALIZATION_OPTIONS_MAPPING[option], str(value)]

    cmd.append("create")
    _, std_err, ret_val = cmd_runner.run(cmd)
    if ret_val != 0:
        raise LibraryError(
            ReportItem.error(
                reports.messages.SbdDeviceInitializationError(
                    device_list, std_err
                )
            )
        )
    report_processor.report(
        ReportItem.info(
            reports.messages.SbdDeviceInitializationSuccess(device_list)
        )
    )


def get_local_sbd_device_list():
    """
    Returns list of devices specified in local SBD config
    """
    if not path.exists(settings.sbd_config):
        return []

    cfg = environment_file_to_dict(get_local_sbd_config())
    if "SBD_DEVICE" not in cfg:
        return []
    devices = cfg["SBD_DEVICE"]
    if devices.startswith('"') and devices.endswith('"'):
        devices = devices[1:-1]
    return [device.strip() for device in devices.split(";") if device.strip()]


def is_device_set_local():
    """
    Returns True if there is at least one device specified in local SBD config,
    False otherwise.
    """
    return len(get_local_sbd_device_list()) > 0


def get_device_messages_info(cmd_runner, device):
    """
    Returns info about messages (string) stored on specified SBD device.

    cmd_runner -- CommandRunner
    device -- string
    """
    std_out, dummy_std_err, ret_val = cmd_runner.run(
        [settings.sbd_binary, "-d", device, "list"]
    )
    if ret_val != 0:
        # sbd writes error message into std_out
        raise LibraryError(
            ReportItem.error(
                reports.messages.SbdDeviceListError(device, std_out)
            )
        )
    return std_out


def get_device_sbd_header_dump(cmd_runner, device):
    """
    Returns header dump (string) of specified SBD device.

    cmd_runner -- CommandRunner
    device -- string
    """
    std_out, dummy_std_err, ret_val = cmd_runner.run(
        [settings.sbd_binary, "-d", device, "dump"]
    )
    if ret_val != 0:
        # sbd writes error message into std_out
        raise LibraryError(
            ReportItem.error(
                reports.messages.SbdDeviceDumpError(device, std_out)
            )
        )
    return std_out


def set_message(cmd_runner, device, node_name, message):
    """
    Set message of specified type 'message' on SBD device for node.

    cmd_runner -- CommandRunner
    device -- string, device path
    node_name -- string, nae of node for which message should be set
    message -- string, message type
    """
    dummy_std_out, std_err, ret_val = cmd_runner.run(
        [settings.sbd_binary, "-d", device, "message", node_name, message]
    )
    if ret_val != 0:
        raise LibraryError(
            ReportItem.error(
                reports.messages.SbdDeviceMessageError(
                    device, node_name, message, std_err
                )
            )
        )


def get_available_watchdogs(cmd_runner):
    regex = (
        r"\[\d+\] (?P<watchdog>.+)$\n"
        r"Identity: (?P<identity>.+)$\n"
        r"Driver: (?P<driver>.+)$"
        r"(\nCAUTION: (?P<caution>.+)$)?"
    )
    std_out, std_err, ret_val = cmd_runner.run(
        [settings.sbd_binary, "query-watchdog"]
    )
    if ret_val != 0:
        raise LibraryError(
            ReportItem.error(reports.messages.SbdListWatchdogError(std_err))
        )
    return {
        match.group("watchdog"): {
            key: match.group(key) for key in ["identity", "driver", "caution"]
        }
        for match in re.finditer(regex, std_out, re.MULTILINE)
    }


def test_watchdog(cmd_runner, watchdog=None):
    cmd = [settings.sbd_binary, "test-watchdog"]
    if watchdog:
        cmd.extend(["-w", watchdog])
    std_out, dummy_std_err, ret_val = cmd_runner.run(cmd)
    if ret_val:
        if "Multiple watchdog devices discovered" in std_out:
            raise LibraryError(
                ReportItem.error(
                    reports.messages.SbdWatchdogTestMultipleDevices()
                )
            )
        raise LibraryError(
            ReportItem.error(reports.messages.SbdWatchdogTestError(std_out))
        )
    raise LibraryError(
        ReportItem.error(reports.messages.SbdWatchdogTestFailed())
    )
