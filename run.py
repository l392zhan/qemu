#!/usr/bin/env python3

import argparse
import multiprocessing
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional, Union

import utils

# path constants
PATH_REPO = Path(__file__).parent.resolve()
PATH_AGENT = os.path.join(PATH_REPO, "agent")
PATH_BUILD = os.path.join(PATH_REPO, "build")
PATH_WKS = os.path.join(PATH_REPO, "workspace")

PATH_AGENT_HOST_SRC = os.path.join(PATH_REPO, "agent", "host")
PATH_AGENT_GUEST_SRC = os.path.join(PATH_REPO, "agent", "guest", "guest.c")

PATH_WKS_ARTIFACT = os.path.join(PATH_WKS, "artifact")
PATH_WKS_ARTIFACT_BUILD = os.path.join(PATH_WKS_ARTIFACT, "build")
PATH_WKS_ARTIFACT_INSTALL = os.path.join(PATH_WKS_ARTIFACT, "install")
PATH_WKS_ARTIFACT_INSTALL_LIB = os.path.join(
    PATH_WKS_ARTIFACT_INSTALL, "lib", "x86_64-linux-gnu"
)
PATH_WKS_ARTIFACT_INSTALL_QEMU_AMD64 = os.path.join(
    PATH_WKS_ARTIFACT_INSTALL, "bin", "qemu-system-x86_64"
)
PATH_WKS_ARTIFACT_INSTALL_QEMU_IMG = os.path.join(
    PATH_WKS_ARTIFACT_INSTALL, "bin", "qemu-img"
)
PATH_WKS_ARTIFACT_INSTALL_QEMU_NBD = os.path.join(
    PATH_WKS_ARTIFACT_INSTALL, "bin", "qemu-nbd"
)

PATH_WKS_LINUX = os.path.join(PATH_WKS, "linux")
PATH_WKS_LINUX_KERNEL = os.path.join(PATH_WKS_LINUX, "kernel.img")
PATH_WKS_LINUX_INITRD = os.path.join(PATH_WKS_LINUX, "initrd.img")
PATH_WKS_LINUX_DISK = os.path.join(PATH_WKS_LINUX, "disk.qcow2")
PATH_WKS_LINUX_HARNESS = os.path.join(PATH_WKS_LINUX, "harness")
PATH_WKS_LINUX_AGENT_HOST = os.path.join(PATH_WKS_LINUX, "agent-host")
PATH_WKS_LINUX_AGENT_GUEST = os.path.join(PATH_WKS_LINUX, "agent-guest")
PATH_WKS_LINUX_ROOTFS_EXT4 = os.path.join(PATH_WKS_LINUX, "rootfs.ext4")

# qemu constants
VM_MONITOR_SOCKET = "monitor"
VM_CONSOLE_INPUT = "input"
VM_CONSOLE_OUTPUT = "output"

VM_MEM_SIZE = 2 * 1024 * 1024 * 1024
VM_DISK_SIZE = 2 * 1024 * 1024 * 1024

# docker constants
DOCKER_TAG = "qemu"
DOCKER_SRC_DIR = "/src"
DOCKER_WORKDIR_PREFIX = "/work"

# system constants
NUM_CPUS = multiprocessing.cpu_count()
GB_IN_BYTES = 1024 * 1024 * 1024


def _docker_exec(
    tag: str, volumes: List[str], ephemeral: bool, interactive: bool, cmdline: List[str]
) -> None:
    command = [
        "docker",
        "run",
        "--device=/dev/kvm",
        "--tmpfs",
        "/dev/shm:exec",
        "--privileged=true",
    ]

    if ephemeral:
        command.append("--rm")
    if interactive:
        command.extend(["--interactive", "--tty"])

    command.extend(
        [
            "--mount",
            "type=bind,source={},target={}".format(PATH_REPO, DOCKER_SRC_DIR),
        ]
    )
    for i, v in enumerate(volumes):
        command.extend(
            [
                "--mount",
                "type=bind,source={},target={}{},readonly".format(
                    os.path.abspath(v), DOCKER_WORKDIR_PREFIX, i
                ),
            ]
        )

    command.append(tag)
    command.extend(cmdline)

    subprocess.check_call(command)


def cmd_docker_build(force: bool) -> None:
    # TODO: check tag existence
    if force:
        subprocess.check_call(["docker", "image", "rm", DOCKER_TAG])
        subprocess.check_call(["docker", "image", "prune", "--force"])

    subprocess.check_call(["docker", "build", ".", "-t", DOCKER_TAG], cwd=PATH_REPO)


def cmd_docker_shell(volumes: List[str]) -> None:
    _docker_exec(DOCKER_TAG, volumes, True, True, ["bash"])


def _docker_exec_self(volumes: List[str], args: List[str]) -> None:
    _docker_exec(
        DOCKER_TAG,
        volumes,
        True,
        False,
        ["python3", "{}/run.py".format(DOCKER_SRC_DIR), *args],
    )


def _qemu_config(
    path_build: Union[str, Path], path_install: Union[str, Path], release: bool
) -> None:
    command = [
        os.path.join(PATH_REPO, "configure"),
        # basics
        "--prefix={}".format(path_install),
        # scopes
        "--target-list=x86_64-softmmu",
        # TODO: fine-tune a list of features
        # "--without-default-features",
        # "--without-default-devices",
        "--disable-docs",
        "--disable-user",
        # features
        "--enable-tcg",
        "--enable-kvm",
        "--enable-slirp",
        "--disable-multiprocess",
    ]

    if release:
        command.extend(
            [
                "--enable-lto",
            ]
        )
    else:
        command.extend(
            [
                "--enable-debug",
            ]
        )

    subprocess.run(command, cwd=path_build, check=True)


def cmd_init(force: bool) -> None:
    # prepare directory
    if os.path.exists(PATH_BUILD):
        if not force:
            sys.exit("Build directory not empty: {}".format(PATH_BUILD))
        shutil.rmtree(PATH_BUILD)

    # build
    _qemu_config(PATH_REPO, os.devnull, False)
    subprocess.run(["make", "-j{}".format(NUM_CPUS)], cwd=PATH_BUILD, check=True)

    # connect the compilation database
    shutil.copy2(
        os.path.join(PATH_BUILD, "compile_commands.json"),
        os.path.join(PATH_REPO, "compile_commands.json"),
    )


def cmd_build(release: bool) -> None:
    # prepare directory
    if os.path.exists(PATH_WKS_ARTIFACT):
        shutil.rmtree(PATH_WKS_ARTIFACT)

    os.makedirs(PATH_WKS_ARTIFACT, exist_ok=False)
    os.mkdir(PATH_WKS_ARTIFACT_BUILD)

    # build
    _qemu_config(PATH_WKS_ARTIFACT_BUILD, PATH_WKS_ARTIFACT_INSTALL, release)
    subprocess.run(
        ["make", "-j{}".format(NUM_CPUS)], cwd=PATH_WKS_ARTIFACT_BUILD, check=True
    )
    subprocess.run(["make", "install"], cwd=PATH_WKS_ARTIFACT_BUILD, check=True)


def __compile_agent_guest(
    harness: Optional[str], blob: Optional[str], simulate_virtme: bool
):
    command = [
        "cc",
        "-static",
        "-std=c2x",
        PATH_AGENT_GUEST_SRC,
        "-o",
        PATH_WKS_LINUX_AGENT_GUEST,
    ]
    if harness is not None:
        command.append('-DHARNESS="{}"'.format(harness))
    if blob is not None:
        command.append('-DBLOB="{}"'.format(blob))
    if simulate_virtme:
        command.append("-DVIRTME")

    # run the compilation
    subprocess.check_call(command, cwd=PATH_AGENT)


def __compile_agent_host(verbose: bool):
    agent_host_name = "qce-agent-host"

    # re-use the debug build of the host agent
    if verbose:
        shutil.copy2(
            os.path.join(PATH_AGENT_HOST_SRC, "target", "debug", agent_host_name),
            PATH_WKS_LINUX_AGENT_HOST,
        )
        return

    # build a fresh host agent (in non-verbose mode)
    with TemporaryDirectory() as tmp:
        subprocess.check_call(
            [
                "cargo",
                "build",
                "--release",
                "--target-dir",
                tmp,
            ],
            cwd=PATH_AGENT_HOST_SRC,
        )
        shutil.copy2(
            os.path.join(tmp, "release", agent_host_name),
            PATH_WKS_LINUX_AGENT_HOST,
        )


def _prepare_linux(
    kernel: str,
    harness: Optional[str],
    blob: Optional[str],
    simulate_virtme: bool,
    verbose: bool,
) -> None:
    # sanity check
    if harness is None and blob is not None:
        sys.exit("cannot specify blob without harness")

    # clear previous states
    if os.path.exists(PATH_WKS_LINUX):
        shutil.rmtree(PATH_WKS_LINUX)
    os.mkdir(PATH_WKS_LINUX)

    # search for kernel image
    if not os.path.exists(kernel):
        sys.exit("kernel image does not exist: {}".format(kernel))
    shutil.copy2(kernel, PATH_WKS_LINUX_KERNEL)

    # compile the agents
    __compile_agent_host(verbose)
    __compile_agent_guest(harness, blob, simulate_virtme)

    # prepare harness
    if harness is not None:
        if not os.path.exists(harness):
            sys.exit("harness source code does not exist at {}".format(harness))
        subprocess.check_call(["cc", "-static", harness, "-o", PATH_WKS_LINUX_HARNESS])

    if blob is not None:
        sys.exit("blob data file does not exist at {}".format(blob))

    # prepare the rootfs image
    utils.mk_rootfs(
        PATH_WKS_ARTIFACT_INSTALL_QEMU_IMG,
        PATH_WKS_ARTIFACT_INSTALL_QEMU_NBD,
        PATH_WKS_LINUX_DISK,
        "{}G".format(VM_DISK_SIZE // GB_IN_BYTES),
        PATH_WKS_LINUX_AGENT_GUEST,
        None if harness is None else PATH_WKS_LINUX_HARNESS,
        blob,
        use_host_rootfs=simulate_virtme,
    )

    # prepare the init ramdisk image
    utils.mk_initramfs(PATH_WKS_LINUX_INITRD)


def _execute_linux(
    tmp: str,
    kvm: bool,
    verbose: bool,
) -> None:
    path_in = os.path.join(tmp, VM_CONSOLE_INPUT)
    Path(path_in).touch()
    path_out = os.path.join(tmp, VM_CONSOLE_OUTPUT)

    # command holder
    command = [PATH_WKS_ARTIFACT_INSTALL_QEMU_AMD64]
    kernel_args = []

    # basics
    command.extend(["-m", "{}G".format(VM_MEM_SIZE // VM_MEM_SIZE)])
    if kvm:
        command.extend(["-machine", "accel=kvm:tcg"])

    # no display
    command.extend(["-vga", "none"])
    command.extend(["-display", "none"])

    # IO
    command.extend(["-parallel", "none"])
    command.extend(["-serial", "stdio"])
    if verbose:
        kernel_args.append("console=ttyS0")

    # kernel
    command.extend(["-kernel", PATH_WKS_LINUX_KERNEL])
    command.extend(["-initrd", PATH_WKS_LINUX_INITRD])

    # disk
    command.extend(
        [
            "-drive",
            ",".join(
                [
                    "file={}".format(PATH_WKS_LINUX_DISK),
                    "node-name=disk0",
                    "if=virtio",
                    "media=disk",
                    "index=0",
                    "snapshot=on",
                ]
            ),
        ]
    )
    # kernel_args.extend(["root=/dev/vda", "rw"])  # /dev/vda maps to virtio-disk index 0
    # kernel_args.append("init=/root/agent")

    # networking
    command.extend(["-net", "none"])
    command.extend(["-device", "virtio-net-pci,netdev=n0"])
    command.extend(["-netdev", "user,id=n0"])
    kernel_args.extend(
        [
            # prevent annoying interface renaming
            "net.ifnames=0",
            "biosdevname=0",
        ]
    )

    # behaviors
    command.extend(["-no-reboot"])
    kernel_args.append("panic=-1")

    # monitor
    command.extend(
        [
            "-chardev",
            "socket,id=qmp,path={},server=on,wait=off".format(
                os.path.join(tmp, VM_MONITOR_SOCKET)
            ),
            "-mon",
            "chardev=qmp,mode=control",
        ]
    )

    # interaction
    command.extend(
        [
            "-chardev",
            "file,id=vmio,path={},input-path={}".format(path_out, path_in),
            "-serial",
            "chardev:vmio",
        ]
    )

    # append kernel arguments
    if len(kernel_args) != 0:
        command.extend(["-append", " ".join(kernel_args)])

    # execute
    subprocess.check_call(
        command, env={"LD_LIBRARY_PATH": PATH_WKS_ARTIFACT_INSTALL_LIB}
    )

    # dump output if in verbose mode
    if verbose:
        with open(path_out, "r") as f:
            print(f.read())


def cmd_linux(
    kernel: str,
    kvm: bool,
    harness: Optional[str],
    blob: Optional[str],
    simulate_virtme: bool,
    verbose: bool,
) -> None:
    _prepare_linux(kernel, harness, blob, simulate_virtme, verbose)
    with TemporaryDirectory() as tmp:
        # start the host
        host = subprocess.Popen([PATH_WKS_LINUX_AGENT_HOST, tmp])
        # start the guest
        _execute_linux(tmp, kvm, verbose)
        # wait for host termination
        host.wait()


def _dev_fresh() -> None:
    _docker_exec_self([], ["build"])


def cmd_dev_sample(volumes: List[str], kvm: bool, virtme: bool, solution: bool) -> None:
    if len(volumes) != 1:
        sys.exit("Expect one and only one volume to attach")

    passthrough_args = ["linux"]
    if kvm:
        passthrough_args.append("--kvm")
    if virtme:
        passthrough_args.append("--virtme")

    passthrough_args.append("--verbose")
    passthrough_args.extend(
        [
            "--kernel",
            "/{}0/src/arch/x86_64/boot/bzImage".format(DOCKER_WORKDIR_PREFIX),
        ]
    )
    passthrough_args.extend(
        [
            "--harness",
            "/{}0/src/test_harnesses/linux_test_harness.c".format(
                DOCKER_WORKDIR_PREFIX
            ),
        ]
    )
    if solution:
        passthrough_args.extend(
            [
                "--blob",
                "/{}0/exemplar_only/blobs/sample_solve.bin".format(
                    DOCKER_WORKDIR_PREFIX
                ),
            ]
        )

    _docker_exec_self(volumes, passthrough_args)


def main() -> None:
    # args
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    #
    # docker group
    #

    parser_docker = subparsers.add_parser("docker")
    sub_docker = parser_docker.add_subparsers(dest="cmd_docker")

    parser_docker_build = sub_docker.add_parser("build")
    parser_docker_build.add_argument("--force", action="store_true")

    parser_docker_shell = sub_docker.add_parser("shell")
    parser_docker_shell.add_argument("-v", "--volume", action="append", default=[])

    #
    # dev group
    #

    parser_dev = subparsers.add_parser("dev")
    parser_dev.add_argument("--fresh", action="store_true")
    parser_dev.add_argument("-v", "--volume", action="append", default=[])
    sub_dev = parser_dev.add_subparsers(dest="cmd_dev")

    parser_dev_sample = sub_dev.add_parser("sample")
    parser_dev_sample.add_argument("--kvm", action="store_true")
    parser_dev_sample.add_argument("--virtme", action="store_true")
    parser_dev_sample.add_argument("--solution", action="store_true")

    #
    # other commands
    #

    parser_init = subparsers.add_parser("init")
    parser_init.add_argument("--force", action="store_true")

    parser_build = subparsers.add_parser("build")
    parser_build.add_argument("--release", action="store_true")

    parser_linux = subparsers.add_parser("linux")
    parser_linux.add_argument("--kernel", required=True)
    parser_linux.add_argument("--kvm", action="store_true")
    parser_linux.add_argument("--harness")
    parser_linux.add_argument("--blob")
    parser_linux.add_argument("--virtme", action="store_true")
    parser_linux.add_argument("--verbose", action="store_true")

    # actions
    args = parser.parse_args()
    if args.command == "docker":
        if args.cmd_docker == "build":
            cmd_docker_build(args.force)
        elif args.cmd_docker == "shell":
            cmd_docker_shell(args.volume)
        else:
            parser_docker.print_help()

    elif args.command == "dev":
        if args.fresh:
            _dev_fresh()
        if args.cmd_dev == "sample":
            cmd_dev_sample(args.volume, args.kvm, args.virtme, args.solution)
        else:
            parser_dev.print_help()

    elif args.command == "init":
        cmd_init(args.force)
    elif args.command == "build":
        cmd_build(args.release)
    elif args.command == "linux":
        cmd_linux(
            args.kernel, args.kvm, args.harness, args.blob, args.virtme, args.verbose
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()