#!/usr/bin/env python3
"""
Docksmith CLI - A simplified Docker-like build and runtime system.
"""

import sys
import os
import argparse
import json
import time

# Ensure the package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docksmith.build_engine import BuildEngine
from docksmith.image_store import ImageStore
from docksmith.container_runtime import ContainerRuntime
from docksmith.state import DocksmithState


def cmd_build(args):
    """Build an image from a Docksmithfile."""
    context_dir = os.path.abspath(args.context)
    if not os.path.isdir(context_dir):
        print(f"Error: context directory '{args.context}' does not exist", file=sys.stderr)
        sys.exit(1)

    docksmithfile = os.path.join(context_dir, "Docksmithfile")
    if not os.path.isfile(docksmithfile):
        print(f"Error: no Docksmithfile found in '{args.context}'", file=sys.stderr)
        sys.exit(1)

    # Parse name:tag
    tag_str = args.tag
    if ":" in tag_str:
        name, tag = tag_str.rsplit(":", 1)
    else:
        name, tag = tag_str, "latest"

    state = DocksmithState()
    engine = BuildEngine(state, no_cache=args.no_cache)
    
    start = time.time()
    try:
        image = engine.build(docksmithfile, context_dir, name, tag)
        elapsed = time.time() - start
        short_digest = image["digest"].split(":")[1][:12]
        print(f"\nSuccessfully built sha256:{short_digest} {name}:{tag} ({elapsed:.2f}s)")
    except Exception as e:
        print(f"\nBuild failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_images(args):
    """List all images in the local store."""
    state = DocksmithState()
    store = ImageStore(state)
    images = store.list_images()

    if not images:
        print("No images found.")
        return

    # Header
    fmt = "{:<20} {:<10} {:<15} {:<25}"
    print(fmt.format("NAME", "TAG", "ID", "CREATED"))
    print("-" * 72)
    for img in images:
        short_id = img["digest"].split(":")[1][:12] if ":" in img["digest"] else img["digest"][:12]
        created = img.get("created", "unknown")
        print(fmt.format(img["name"], img["tag"], short_id, created))


def cmd_rmi(args):
    """Remove an image and its layers."""
    tag_str = args.name_tag
    if ":" in tag_str:
        name, tag = tag_str.rsplit(":", 1)
    else:
        name, tag = tag_str, "latest"

    state = DocksmithState()
    store = ImageStore(state)
    try:
        store.remove_image(name, tag)
        print(f"Removed {name}:{tag}")
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_run(args):
    """Run a container from an image."""
    tag_str = args.name_tag
    if ":" in tag_str:
        name, tag = tag_str.rsplit(":", 1)
    else:
        name, tag = tag_str, "latest"

    # Parse -e overrides
    env_overrides = {}
    for e in (args.env or []):
        if "=" in e:
            k, v = e.split("=", 1)
            env_overrides[k] = v
        else:
            env_overrides[e] = ""

    state = DocksmithState()
    store = ImageStore(state)

    try:
        image = store.get_image(name, tag)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Determine command
    cmd_override = args.cmd if args.cmd else None
    if not cmd_override and not image.get("config", {}).get("Cmd"):
        print(f"Error: no CMD defined in image '{name}:{tag}' and no command provided", file=sys.stderr)
        sys.exit(1)

    runtime = ContainerRuntime(state)
    exit_code = runtime.run(image, cmd_override, env_overrides)
    print(f"\nContainer exited with code: {exit_code}")
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(
        prog="docksmith",
        description="Docksmith - A simplified Docker-like build and runtime system"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # build
    p_build = subparsers.add_parser("build", help="Build an image from a Docksmithfile")
    p_build.add_argument("-t", "--tag", required=True, metavar="name:tag", help="Name and optionally tag for the image")
    p_build.add_argument("--no-cache", action="store_true", help="Do not use build cache")
    p_build.add_argument("context", help="Build context directory")

    # images
    p_images = subparsers.add_parser("images", help="List images")

    # rmi
    p_rmi = subparsers.add_parser("rmi", help="Remove an image")
    p_rmi.add_argument("name_tag", metavar="name:tag", help="Image name and tag")

    # run
    p_run = subparsers.add_parser("run", help="Run a container")
    p_run.add_argument("-e", dest="env", action="append", metavar="KEY=VALUE", help="Environment variable override")
    p_run.add_argument("name_tag", metavar="name:tag", help="Image name and tag")
    p_run.add_argument("cmd", nargs=argparse.REMAINDER, help="Command override")

    args = parser.parse_args()

    if args.command == "build":
        cmd_build(args)
    elif args.command == "images":
        cmd_images(args)
    elif args.command == "rmi":
        cmd_rmi(args)
    elif args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
