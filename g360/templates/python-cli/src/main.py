#!/usr/bin/env python3
"""
G360 Python CLI Application
Main entry point with argparse-based CLI structure
"""

import argparse
import sys
import os
from pathlib import Path

VERSION = "1.0.0"
APP_NAME = "G360 CLI"
AUTHOR = "Carlos Cusi"


def setup_logging(verbose=False):
    """Configure logging based on verbose flag"""
    import logging
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s - %(levelname)s - %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")
    return logging.getLogger(__name__)


def cmd_hello(args):
    """Hello world command example"""
    print(f"\n{APP_NAME} v{VERSION}")
    print(f"Author: {AUTHOR}")
    print(f"Working directory: {os.getcwd()}")
    if args.name:
        print(f"\nHello, {args.name}!")
    print()


def cmd_process(args):
    """Process data command"""
    logger = setup_logging(args.verbose)
    logger.info(f"Processing: {args.input}")
    
    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        return 1
    
    output_dir = args.output or os.path.dirname(args.input) or "."
    logger.info(f"Output directory: {output_dir}")
    
    print(f"✓ Processed {args.input}")
    return 0


def cmd_config(args):
    """Show or edit configuration"""
    config_path = Path("g360") / "config.json"
    
    if args.show:
        if config_path.exists():
            import json
            with open(config_path) as f:
                print(json.dumps(json.load(f), indent=2))
        else:
            print("No config file found. Run: g360 init")
        return
    
    if args.set:
        key, value = args.set.split("=")
        print(f"Setting {key} = {value}")


def main():
    parser = argparse.ArgumentParser(
        prog="g360",
        description=f"{APP_NAME} - CLI tool for G360 ecosystem",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  g360 hello
  g360 hello --name "World"
  g360 process data.csv -o output/
  g360 config --show
        """
    )
    
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--verbose", "-V", action="store_true", help="Enable verbose output")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    hello_parser = subparsers.add_parser("hello", help="Say hello")
    hello_parser.add_argument("--name", "-n", help="Name to greet")
    
    process_parser = subparsers.add_parser("process", help="Process input data")
    process_parser.add_argument("input", help="Input file or directory")
    process_parser.add_argument("-o", "--output", help="Output directory")
    
    config_parser = subparsers.add_parser("config", help="Manage configuration")
    config_parser.add_argument("--show", action="store_true", help="Show current config")
    config_parser.add_argument("--set", metavar="KEY=VALUE", help="Set config value")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    if args.command == "hello":
        return cmd_hello(args)
    elif args.command == "process":
        return cmd_process(args)
    elif args.command == "config":
        return cmd_config(args)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())