#!/usr/bin/env python3
"""
run_agent.py — Interactive CLI for testing the payment agent.

Usage:
    python run_agent.py              # Interactive mode
    python run_agent.py --demo       # Run a pre-scripted demo
    python run_agent.py --scenario happy_path_clean  # Run a specific eval scenario
"""

import os
import sys
import argparse

from dotenv import load_dotenv
load_dotenv()

from agent import Agent


def run_interactive():
    """Interactive mode — type messages and see agent responses."""
    print("\n" + "="*60)
    print("  Payment Collection AI Agent — Interactive Mode")
    print("  Type 'quit' or 'exit' to end | 'reset' for new session")
    print("="*60 + "\n")

    agent = Agent()

    # Start the conversation
    result = agent.next("Hello")
    print(f"Agent: {result['message']}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        if user_input.lower() == "reset":
            agent = Agent()
            result = agent.next("Hello")
            print(f"\nAgent: {result['message']}\n")
            continue

        result = agent.next(user_input)
        print(f"\nAgent: {result['message']}\n")


def run_demo():
    """
    Run a pre-scripted happy path demo to show the agent working end-to-end.
    Uses real messy inputs as described in the spec.
    """
    script = [
        "Hi there",
        "yeah my account number is ACC1001 I think",
        "it's Nithin, Nithin Jain",
        "last four of my Aadhaar is 4321",
        "just clear 500 for now",
        "the card number is 4532 0151 1283 0366",
        "CVV is one two three",
        "expires December 2027",
        "Nithin Jain",
    ]

    print("\n" + "="*60)
    print("  Payment Agent — Demo (Messy Input Happy Path)")
    print("="*60 + "\n")

    agent = Agent()

    for user_msg in script:
        print(f"User:  {user_msg}")
        result = agent.next(user_msg)
        print(f"Agent: {result['message']}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the payment collection agent")
    parser.add_argument("--demo", action="store_true", help="Run pre-scripted demo")
    parser.add_argument("--scenario", type=str, help="Run a specific eval scenario interactively")
    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.scenario:
        # Import here to avoid circular
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from eval.evaluator import SCENARIOS, run_scenario
        matching = [s for s in SCENARIOS if s.name == args.scenario]
        if not matching:
            print(f"Scenario '{args.scenario}' not found.")
            sys.exit(1)
        run_scenario(matching[0], verbose=True)
    else:
        run_interactive()
