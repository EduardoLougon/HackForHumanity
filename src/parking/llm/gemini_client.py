"""Gemini API Client and Conversational Multi-Turn Loop.

Implements tool calling loop, compact tool response passing, citation guardrails,
and returns reply text alongside structured search results for the frontend map.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from src.parking.geo.geocode import geocode_place
from src.parking.llm.prompts import build_system_prompt
from src.parking.llm.tools import TOOL_DEFINITIONS, TOOL_REGISTRY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def execute_tool_call(name: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Execute a tool call by name and return (compact_result_for_llm, raw_results_if_any)."""
    func = TOOL_REGISTRY.get(name)
    if not func:
        return {"error": f"Tool '{name}' is not recognized."}, None

    try:
        res = func(**args)
        raw = res.pop("_raw_response", None) if isinstance(res, dict) else None
        return res, raw
    except Exception as e:
        logger.error(f"Error executing tool '{name}': {e}")
        return {"error": str(e)}, None


def verify_pole_citations(text: str, valid_poles: List[str]) -> str:
    """Citation guardrail: remove or redact hallucinated pole IDs not present in tool results."""
    # Pattern for pole IDs: e.g. '1-1004', '5-800W', 'BD-9'
    found_poles = re.findall(r"\b[A-Za-z0-9]+-[A-Za-z0-9]+\b", text)
    valid_set = set(valid_poles)

    for p in found_poles:
        if p not in valid_set:
            logger.warning(f"Hallucinated pole ID '{p}' detected in model output. Redacting citation.")
            text = text.replace(p, "[Pole]")

    return text


def run_chat_turn(
    messages: List[Dict[str, str]],
    user_location: Optional[Dict[str, float]] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute conversational turn with Gemini function calling."""
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    last_user_msg = messages[-1]["text"] if messages else ""
    system_prompt = build_system_prompt(user_location)

    # Fallback/Mock mode if GEMINI_API_KEY is unset or dummy
    if not api_key or api_key == "your_gemini_api_key_here":
        logger.info("GEMINI_API_KEY not configured. Running deterministic mock conversational handler.")
        return _mock_conversational_turn(last_user_msg, user_location)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # Convert tool definitions to Google GenAI Tool schema
        function_declarations = []
        for t in TOOL_DEFINITIONS:
            function_declarations.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=t["parameters"],
                )
            )

        gemini_tools = [types.Tool(function_declarations=function_declarations)]

        # Prepare chat history
        contents = []
        for m in messages[:-1]:
            role = "user" if m.get("role") == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m.get("text", ""))]))

        # Add latest user message
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=last_user_msg)]))

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=gemini_tools,
            temperature=0.2,
        )

        last_raw_results: Optional[Dict[str, Any]] = None
        executed_poles: List[str] = []

        # Tool-calling loop (max 4 rounds)
        for round_idx in range(4):
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )

            # Check if candidate has function calls
            candidate = response.candidates[0] if response.candidates else None
            if not candidate or not candidate.content or not candidate.content.parts:
                break

            function_calls = [p.function_call for p in candidate.content.parts if p.function_call]

            if not function_calls:
                # Model produced final text reply
                final_text = "".join(p.text for p in candidate.content.parts if p.text)
                final_text = verify_pole_citations(final_text, executed_poles)
                return {
                    "reply": final_text,
                    "results": last_raw_results.get("results", []) if last_raw_results else [],
                    "query": last_raw_results.get("query") if last_raw_results else None,
                    "data_as_of": last_raw_results.get("data_as_of") if last_raw_results else None,
                }

            # Append model's function call response to history
            contents.append(candidate.content)

            # Execute tool calls and prepare response parts
            tool_response_parts = []
            for fc in function_calls:
                fn_name = fc.name
                fn_args = dict(fc.args) if fc.args else {}
                logger.info(f"Gemini requested tool call: {fn_name}({fn_args})")

                tool_res, raw_data = execute_tool_call(fn_name, fn_args)
                if raw_data:
                    last_raw_results = raw_data
                    for r in raw_data.get("results", []):
                        executed_poles.append(r["pole_id"])

                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=fn_name,
                        response={"result": tool_res},
                    )
                )

            contents.append(types.Content(role="user", parts=tool_response_parts))

        # Fallback if loop hit limit
        return {
            "reply": "I retrieved parking information for your destination. See the map for available spots.",
            "results": last_raw_results.get("results", []) if last_raw_results else [],
            "query": last_raw_results.get("query") if last_raw_results else None,
            "data_as_of": last_raw_results.get("data_as_of") if last_raw_results else None,
        }

    except Exception as e:
        logger.error(f"Gemini API invocation error: {e}. Falling back to deterministic pipeline.")
        return _mock_conversational_turn(last_user_msg, user_location)


def _mock_conversational_turn(user_msg: str, user_location: Optional[Dict[str, float]]) -> Dict[str, Any]:
    """Deterministic conversational fallback for testing or when GEMINI_API_KEY is not set."""
    # Check if a destination is mentioned
    place = None
    for landmark in ["petco park", "gaslamp", "balboa park", "little italy", "hillcrest", "zoo"]:
        if landmark in user_msg.lower():
            place = landmark
            break

    lat, lon = (32.7073, -117.1566)  # Default Petco Park
    if place:
        coords = geocode_place(place)
        if coords:
            lat, lon = coords
    elif user_location and "lat" in user_location:
        lat, lon = user_location["lat"], user_location["lon"]

    # Parse duration if mentioned
    m_dur = re.search(r"(\d+)\s*(?:hour|hr)", user_msg, re.IGNORECASE)
    duration = int(m_dur.group(1)) * 60 if m_dur else None

    tool_res = TOOL_REGISTRY["find_parking"](lat=lat, lon=lon, duration_minutes=duration)
    raw = tool_res.get("_raw_response", {})
    spots = raw.get("results", [])

    if not spots:
        reply = "No street parking meters were found within walking distance of that location."
    else:
        top_spot = spots[0]
        avail = top_spot["availability"]
        band_str = avail.get("band", "unknown")
        reply = (
            f"I found metered parking nearby. The closest space at pole {top_spot['pole_id']} "
            f"is {top_spot['distance_m']}m away (~{top_spot['walk_minutes']} min walk) and is "
            f"estimated to be {band_str} open at arrival. "
            f"(Estimates come from historical payment patterns, not live sensors)."
        )

    return {
        "reply": reply,
        "results": spots,
        "query": raw.get("query"),
        "data_as_of": raw.get("data_as_of"),
    }
