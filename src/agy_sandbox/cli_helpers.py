import argparse
import difflib
import sys
from typing import Iterable, List, Optional, Set


def preprocess_unquoted_comma_args(argv: List[str]) -> List[str]:
    """
    Pre-processes CLI arguments to automatically detect and merge unquoted space-separated
    arguments following a comma (e.g., ['--agent', 'omp,', 'prime-agent'] -> ['--agent', 'omp,prime-agent']).
    
    Adheres to Clean Code principles by being pure, deterministic, and self-documenting.
    """
    if not argv:
        return []

    processed = []
    i = 0
    n = len(argv)

    while i < n:
        token = argv[i]
        # Check if current token ends with a comma and there is a next token that is not a flag
        if token.endswith(",") and i + 1 < n and not argv[i + 1].startswith("-"):
            merged = token
            auto_merged_parts = [token]
            i += 1
            while i < n and not argv[i].startswith("-"):
                auto_merged_parts.append(argv[i])
                merged += argv[i]
                if argv[i].endswith(","):
                    i += 1
                else:
                    i += 1
                    break
            
            print(
                f"💡 [Smart CLI] Auto-merged unquoted space-separated arguments {' '.join(auto_merged_parts)} -> '{merged}'",
                file=sys.stderr,
            )
            processed.append(merged)
        else:
            processed.append(token)
            i += 1

    return processed


def find_best_match(candidate: str, choices: Iterable[str], cutoff: float = 0.5) -> Optional[str]:
    """
    Finds the closest matching string from dynamic choices using difflib.
    Returns None if no candidate exceeds the similarity cutoff.
    """
    if not candidate or not choices:
        return None
    
    matches = difflib.get_close_matches(candidate, list(choices), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def format_smart_error(error_msg: str, hint: Optional[str] = None) -> str:
    """Formats standardized, actionable CLI error messages with distinct visual hints."""
    out = f"❌ Error: {error_msg}"
    if hint:
        out += f"\n💡 Hint: {hint}"
    return out


class SmartArgumentParser(argparse.ArgumentParser):
    """
    Custom ArgumentParser that intercepts syntax/choice errors and uses difflib
    fuzzy matching to suggest the closest valid command or flag.
    """

    def error(self, message: str) -> None:
        # Extract invalid choice / argument if present in standard argparse message
        suggestion_hint = None

        # Handle 'invalid choice: 'x' (choose from 'y', 'z')'
        if "invalid choice:" in message:
            import re
            match = re.search(r"invalid choice:\s*'([^']+)'\s*\(choose from\s*([^)]+)\)", message)
            if match:
                invalid_choice = match.group(1)
                raw_choices = match.group(2)
                valid_choices = [c.strip("' ") for c in raw_choices.split(",")]
                closest = find_best_match(invalid_choice, valid_choices)
                if closest:
                    suggestion_hint = f"Did you mean '{closest}'? Available choices: {', '.join(valid_choices)}"
                else:
                    suggestion_hint = f"Available choices: {', '.join(valid_choices)}"

        # Handle 'unrecognized arguments: x'
        elif "unrecognized arguments:" in message:
            import re
            match = re.search(r"unrecognized arguments:\s*(.+)", message)
            if match:
                unrecognized = match.group(1).strip()
                # Collect known options from parser
                known_flags: Set[str] = set()
                for action in self._actions:
                    known_flags.update(action.option_strings)
                
                first_unrecognized = unrecognized.split()[0]
                closest = find_best_match(first_unrecognized, known_flags)
                if closest:
                    suggestion_hint = f"Did you mean option '{closest}'?"
                elif "," in first_unrecognized or first_unrecognized.endswith(","):
                    suggestion_hint = "If you are passing a list of values, remove spaces after commas or wrap in quotes (e.g. --agent \"omp, prime-agent\")."

        formatted_msg = format_smart_error(message, suggestion_hint)
        self.print_usage(sys.stderr)
        print(f"{formatted_msg}\n", file=sys.stderr)
        self.exit(2)
