# pylint: disable=invalid-name
# pylint: disable=missing-docstring
# pylint: disable=too-many-branches

import os.path
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(CURRENT_DIR, "{}.d".format(sys.argv[0]))


def write_file_to_stdout(file_name):
    with open(file_name) as file:
        sys.stdout.write(file.read())


def write_local_file_to_stdout(file_name):
    write_file_to_stdout(os.path.join(DATA_DIR, file_name))


def main():
    argv = sys.argv[1:]
    if not argv:
        raise AssertionError()

    arg = argv.pop(0)

    option_file_map = {
        "--list-standards": "list_standards",
        "--list-ocf-providers": "list_ocf_providers",
    }

    if arg == "--show-metadata":
        if len(argv) != 1:
            raise AssertionError()
        arg = argv[0]
        known_agents = (
            "lsb:network",
            "ocf:heartbeat:Dummy",
            "ocf:heartbeat:IPaddr2",
            "ocf:heartbeat:Filesystem",
            "ocf:pacemaker:Dummy",
            "ocf:pacemaker:HealthCPU",
            "ocf:pacemaker:remote",
            "ocf:pacemaker:Stateful",
            "ocf:pacemaker:SystemHealth",
            "stonith:fence_apc",
            "stonith:fence_ilo",
            "stonith:fence_scsi",
            "stonith:fence_xvm",
            "systemd:test@a:b",
        )
        # known_agents_map = {item.lower()}
        if arg in known_agents:
            write_local_file_to_stdout(
                "{}_metadata.xml".format(arg.replace(":", "__"))
            )
        elif arg == "ocf:pacemaker:nonexistent":
            sys.stderr.write(
                "Metadata query for ocf:pacemaker:nonexistent failed: "
                "Input/output error\n"
            )
            raise SystemExit(5)
        elif arg == "stonith:fence_noexist":
            sys.stderr.write(
                "Agent fence_noexist not found or does not support meta-data: "
                "Invalid argument (22)\nMetadata query for "
                "stonith:fence_noexist failed: Input/output error\n"
            )
            raise SystemExit(5)
        else:
            raise AssertionError()
    elif arg in option_file_map:
        if argv:
            raise AssertionError()
        write_local_file_to_stdout(option_file_map[arg])
    elif arg == "--list-agents":
        if len(argv) != 1:
            raise AssertionError()
        arg = argv[0]
        known_providers = ("ocf:heartbeat", "ocf:pacemaker", "stonith")
        if arg in known_providers:
            write_local_file_to_stdout(
                "list_agents_{}".format(arg.replace(":", "__"))
            )
        else:
            raise AssertionError()
    else:
        raise AssertionError()


if __name__ == "__main__":
    main()
