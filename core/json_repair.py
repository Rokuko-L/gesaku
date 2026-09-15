"""Healing JSON parser for LLM output.

LLMs return damaged JSON constantly. This module recovers it in layers:

  1. Strip ``` fences
  2. Locate the first `{`/`[` and find its matching close by brace counting
     (ignoring braces inside strings)
  3. `repair_unescaped_quotes` — escape raw `"` inside string values
  4. Insert missing commas, strip trailing commas
  5. `fix_truncated_json` — close open strings/braces for truncated output

The result is *syntactically* valid JSON. It says nothing about *shape* —
that is core/validation.py's job.

Split out of core/llm.py so the HTTP client and the repair layer can be
read and tested independently.
"""

import json
import re
import sys


def is_json_boundary(text: str, idx: int, is_key: bool) -> bool:
    """Check if the lookahead character indicates this quote is a JSON structural boundary."""
    n = len(text)
    j = idx
    while j < n and text[j].isspace():
        j += 1
    if j == n:
        return True
    
    c = text[j]
    if is_key:
        return c == ':'
    else:
        if c in ('}', ']'):
            return True
        if c == ',':
            # Check what follows the comma; it must be a valid JSON key, value, or closing brace
            k = j + 1
            while k < n and text[k].isspace():
                k += 1
            if k == n:
                return True
            next_c = text[k]
            if next_c in ('"', '{', '[', '}', ']'):
                return True
            if next_c.isdigit() or next_c == '-':
                return True
            if next_c in ('t', 'f', 'n'):
                word = text[k:k+5]
                if word.startswith('true') or word.startswith('false') or word.startswith('null'):
                    return True
            return False
        if c == '"':
            # Lookahead check for missing commas: see if this starts a new key (e.g. "key":)
            k = j + 1
            while k < n and text[k] != '"':
                k += 1
            if k < n:
                k += 1
                while k < n and text[k].isspace():
                    k += 1
                if k < n and text[k] == ':':
                    return True
            return False
        return False

def repair_unescaped_quotes(text: str) -> str:
    """Escapes unescaped double quotes inside JSON string values."""
    result = []
    in_value_string = False
    is_key = False
    i = 0
    n = len(text)
    stack = []  # Track open containers: '{' or '['
    
    while i < n:
        c = text[i]
        
        # Track containers if we are outside any string
        if not in_value_string:
            if c in ('{', '['):
                stack.append(c)
            elif c in ('}', ']'):
                if stack:
                    stack.pop()
                    
        if c == '"':
            # Check if this quote is already escaped
            is_escaped = False
            backslashes = 0
            k = i - 1
            while k >= 0 and text[k] == '\\':
                backslashes += 1
                k -= 1
            if backslashes % 2 == 1:
                is_escaped = True
                
            if is_escaped:
                result.append(c)
                i += 1
                continue
                
            if not in_value_string:
                # Entering a JSON key or string value
                # Determine if it's a key or a value
                if stack and stack[-1] == '[':
                    # Inside an array, it's always a value string
                    is_key = False
                else:
                    # Inside an object or at top-level
                    last_char = None
                    k = len(result) - 1
                    while k >= 0:
                        if not result[k].isspace():
                            last_char = result[k]
                            break
                        k -= 1
                    is_key = (last_char != ':')
                
                in_value_string = True
                result.append(c)
                i += 1
            else:
                # Inside a string. Check if this is the closing boundary quote
                if is_json_boundary(text, i + 1, is_key):
                    in_value_string = False
                    result.append(c)
                else:
                    result.append('\\"')
                i += 1
        else:
            result.append(c)
            i += 1
            
    return "".join(result)

def fix_truncated_json(text: str) -> str:
    """Heal truncated JSON strings by closing open string values and structures."""
    in_string = False
    escape = False
    stack = []
    
    for i, c in enumerate(text):
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c in ('{', '['):
            stack.append(c)
        elif c in ('}', ']'):
            if stack:
                stack.pop()
                
    if in_string:
        text += '"'
    for open_char in reversed(stack):
        if open_char == '{':
            text += '}'
        elif open_char == '[':
            text += ']'
    return text

def parse_json_response(text: str) -> dict | list:
    """Extract and heal JSON from LLM response text."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        
    start = text.find('{')
    is_obj = True
    if start == -1 or (text.find('[') != -1 and text.find('[') < start):
        start = text.find('[')
        is_obj = False
    if start == -1:
        raise ValueError("No JSON object or array found in response")
        
    # Count braces/brackets to find the matching closing character,
    # thereby stripping any trailing conversation text that causes JSON decode errors.
    brace_count = 0
    in_string = False
    escape = False
    end_idx = len(text)
    for idx in range(start, len(text)):
        c = text[idx]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == ('{' if is_obj else '['):
            brace_count += 1
        elif c == ('}' if is_obj else ']'):
            brace_count -= 1
            if brace_count == 0:
                end_idx = idx + 1
                break
                
    json_part = text[start:end_idx]
    
    # Run repairs
    json_part = repair_unescaped_quotes(json_part)
    
    # Missing commas repair (avoiding escaped quotes using negative lookbehind (?<!\\))
    json_part = re.sub(
        r'(?<!\\)("|\d|\]|\}|true|false|null)\s+(?<!\\)(\s*"([^"]+)"\s*:)',
        r'\1,\n\2',
        json_part
    )
    
    # Trailing commas repair
    json_part = re.sub(r',\s*([\}\]])', r'\1', json_part)
    
    # Heal truncated JSON
    healed = fix_truncated_json(json_part)
    if healed != json_part:
        import traceback
        caller = traceback.extract_stack(limit=4)[-2]
        print(
            f"  [WARN] parse_json_response HEALED truncated JSON from {caller.filename.split('/')[-1]}:{caller.lineno} "
            f"(appended {len(healed) - len(json_part)} closing chars — partial data will be used as-is)",
            file=sys.stderr,
        )
        json_part = healed
        # Healing can manufacture a trailing comma (e.g. "…[1," → "[1,]") — repair it.
        json_part = re.sub(r',\s*([\}\]])', r'\1', json_part)
    
    if end_idx < len(text) and text[end_idx:].strip():
        import traceback
        caller = traceback.extract_stack(limit=4)[-2]
        print(
            f"  [WARN] parse_json_response ignored {len(text[end_idx:].strip())} trailing chars after the "
            f"closing brace from {caller.filename.split('/')[-1]}:{caller.lineno}",
            file=sys.stderr,
        )
    
    return json.loads(json_part, strict=False)
